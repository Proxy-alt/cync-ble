"""Which node the coordinator reaches for, and in what order.

This is load-bearing rather than cosmetic. On the mesh this was developed
against, most nodes never accept a connection at all - they answer "the
adapter is out of connection slots" - and the ones that do sit at the end of
the cloud's own device ordering. Walking that order meant grinding through
refusals and reaching a working node only by luck.
"""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cync_ble.const import (
    CONF_DEVICES,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    DOMAIN,
)
from custom_components.cync_ble.coordinator import CyncBleCoordinator

# Deliberately shaped like the real account: the refusing family first, the
# node that actually answers last.
REFUSING = ["F4:BC:DA:00:00:01", "F4:BC:DA:00:00:02", "F4:BC:DA:00:00:03"]
ANSWERS = "30:C0:1B:00:00:09"


def _coordinator(hass, macs=None):
    macs = macs if macs is not None else [*REFUSING, ANSWERS]
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="meshname",
        data={
            CONF_MESH_NAME: "meshname",
            CONF_MESH_PASSWORD: "meshpass",
            CONF_DEVICES: [
                {"id": i, "name": f"d{i}", "type": 48, "mac": m}
                for i, m in enumerate(macs, start=1)
            ],
        },
    )
    entry.add_to_hass(hass)
    return CyncBleCoordinator(hass, entry)


async def test_untouched_mesh_keeps_the_stored_order(hass):
    """Nothing learned yet, so nothing to prefer - the stored order stands."""
    coordinator = _coordinator(hass)
    assert coordinator._candidate_macs() == [*REFUSING, ANSWERS]


async def test_a_proven_node_leads_regardless_of_stored_order(hass):
    """The whole point: once a node has completed a handshake it goes first,
    even though the cloud listed it last."""
    coordinator = _coordinator(hass)
    coordinator._known_good.add(ANSWERS)
    assert coordinator._candidate_macs()[0] == ANSWERS


async def test_refusals_sink_below_untried_nodes(hass):
    coordinator = _coordinator(hass)
    coordinator._recent_failures[REFUSING[0]] = 1_000_000.0
    order = coordinator._candidate_macs()
    assert order.index(REFUSING[0]) > order.index(REFUSING[1])


async def test_proven_nodes_rotate_least_recently_used_first(hass):
    """A harvest ends by having its link killed, so the node that just served
    one is the worst candidate for the next. Rotating among proven nodes
    avoids that without wandering back into the refusing majority."""
    second = "30:C0:1B:00:00:10"
    coordinator = _coordinator(hass, [*REFUSING, ANSWERS, second])
    coordinator._known_good.update({ANSWERS, second})
    coordinator._last_used[ANSWERS] = 500.0  # used most recently
    coordinator._last_used[second] = 100.0
    assert coordinator._candidate_macs()[:2] == [second, ANSWERS]


async def test_a_proven_node_stays_proven_after_one_refusal(hass):
    """ "Out of connection slots" is usually about the adapter, not the node.
    A single refusal must not discard hard-won knowledge - the node stays in
    the proven set and still outranks known refusals."""
    coordinator = _coordinator(hass)
    coordinator._known_good.add(ANSWERS)
    coordinator._recent_failures[ANSWERS] = 1_000_000.0
    assert ANSWERS in coordinator._known_good
    order = coordinator._candidate_macs()
    assert order.index(ANSWERS) < order.index(REFUSING[0])


async def test_a_node_rests_after_serving_a_harvest(hass):
    """The harvest ends by killing its own link, and that node then refuses
    the next connection. Resting it is what pushes the walk into never-tried
    nodes, which is the only way a second proven node is ever found."""
    import time as _time

    coordinator = _coordinator(hass)
    coordinator._known_good.add(ANSWERS)
    coordinator._last_used[ANSWERS] = _time.monotonic()  # just used
    order = coordinator._candidate_macs()
    assert order[0] != ANSWERS
    assert order[0] in REFUSING  # an untried node gets the turn instead


async def test_a_rested_node_still_beats_known_refusals(hass):
    """Resting demotes a proven node below untried ones, not below nodes
    already known to refuse - it is still the best thing we know."""
    coordinator = _coordinator(hass, [*REFUSING, ANSWERS])
    coordinator._known_good.add(ANSWERS)
    coordinator._last_used[ANSWERS] = __import__("time").monotonic()
    for mac in REFUSING:
        coordinator._recent_failures[mac] = 1.0
    order = coordinator._candidate_macs()
    assert order[0] == ANSWERS


async def test_untried_nodes_are_ordered_by_signal(hass):
    """The fix for a real failure: `light.turn_on` spent 90 seconds refusing
    to connect to three nodes Home Assistant listed as connectable but had no
    signal from (RSSI -127), while a node with real signal sat further down
    the list and connects on every attempt."""
    coordinator = _coordinator(hass, [*REFUSING, ANSWERS])
    signals = {ANSWERS: -55, REFUSING[0]: -127, REFUSING[1]: -127, REFUSING[2]: -90}
    coordinator._signal = lambda mac: signals[mac]

    order = coordinator._candidate_macs()

    assert order[0] == ANSWERS, "strongest signal must be tried first"
    assert order[-1] in (REFUSING[0], REFUSING[1]), "no-signal nodes go last"


async def test_signal_lookup_failure_does_not_break_ordering(hass):
    """Ordering is an optimisation. If the Bluetooth stack cannot answer,
    the walk must still happen rather than the whole cycle failing."""
    coordinator = _coordinator(hass)
    assert coordinator._candidate_macs()  # _signal hits a real, absent manager
