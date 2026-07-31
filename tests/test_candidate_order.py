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
    A single refusal must not discard hard-won knowledge - it only rotates
    that node to the back of the proven set."""
    coordinator = _coordinator(hass)
    coordinator._known_good.add(ANSWERS)
    coordinator._recent_failures[ANSWERS] = 1_000_000.0
    coordinator._last_used[ANSWERS] = 1_000_000.0
    assert ANSWERS in coordinator._known_good
    assert coordinator._candidate_macs()[0] == ANSWERS
