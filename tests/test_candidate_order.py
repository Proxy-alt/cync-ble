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


async def test_a_node_that_sweeps_becomes_proven(hass):
    """Delivering status records is what earns a place at the head of the
    list - the whole point of separating this from authentication."""
    coordinator = _coordinator(hass)
    coordinator._record_sweep(ANSWERS, collected=38)
    assert ANSWERS in coordinator._known_good
    assert coordinator._candidate_macs(for_harvest=True)[0] == ANSWERS


async def test_a_node_that_authenticates_but_sweeps_nothing_is_not_proven(hass):
    """The bug this fixes. `78:6D:EB` nodes authenticate exactly like working
    ones and then refuse the subscribe outright, so a handshake must not be
    enough to make one the preferred harvest relay."""
    coordinator = _coordinator(hass)
    coordinator._record_sweep(REFUSING[0], collected=0)
    assert REFUSING[0] not in coordinator._known_good
    order = coordinator._candidate_macs(for_harvest=True)
    assert order[0] != REFUSING[0]


async def test_a_barren_node_sinks_below_untried_ones_for_harvests(hass):
    """A never-tried node might turn out to sweep; a barren one is known not
    to. Spending a harvest attempt on the latter is a guaranteed waste."""
    coordinator = _coordinator(hass)
    coordinator._record_sweep(REFUSING[0], collected=0)
    order = coordinator._candidate_macs(for_harvest=True)
    assert order.index(REFUSING[0]) > order.index(REFUSING[1])


async def test_a_barren_node_still_leads_for_commands(hass):
    """Barren is a statement about harvesting only. The node connects and
    authenticates fine, and any authenticated node reaches the whole mesh, so
    for a command it beats one nothing is known about."""
    coordinator = _coordinator(hass)
    coordinator._record_sweep(REFUSING[2], collected=0)
    order = coordinator._candidate_macs()
    assert order[0] == REFUSING[2]


async def test_a_seeded_node_is_demoted_once_it_harvests_nothing(hass):
    """Entries persisted under the old "authenticated" meaning may name nodes
    that can never sweep - two of the three on the development account did.
    They must not stay at the head of the list forever."""
    coordinator = _coordinator(hass)
    coordinator._known_good.add(REFUSING[0])
    assert coordinator._candidate_macs(for_harvest=True)[0] == REFUSING[0]

    coordinator._record_sweep(REFUSING[0], collected=0)

    assert REFUSING[0] not in coordinator._known_good
    assert coordinator._candidate_macs(for_harvest=True)[0] != REFUSING[0]


async def test_authenticating_alone_does_not_make_a_node_proven(hass):
    """Guards the exact line the bug lived on. `_authenticate` used to promote
    into `_known_good`, which is how two nodes that can never sweep came to
    lead the candidate list on the development account."""
    from unittest.mock import AsyncMock, MagicMock, patch

    coordinator = _coordinator(hass)
    session = MagicMock()
    session.authenticate = AsyncMock(return_value=True)

    with patch(
        "custom_components.cync_ble.coordinator.BleMeshSession",
        return_value=session,
    ):
        result = await coordinator._authenticate(MagicMock(), ANSWERS, ANSWERS)

    assert result is session, "authentication itself must still succeed"
    assert ANSWERS in coordinator._connectable, "and must record connectability"
    assert ANSWERS not in coordinator._known_good, (
        "but must NOT mark the node as a proven harvest relay"
    )


async def test_the_persisted_cap_keeps_the_best_sweepers(hass):
    """Sorting by mac was an active bug, not an arbitrary tiebreak: 786DEB
    sorts before F4BCDA, and those are exactly the families that cannot and
    can harvest, so the cap evicted the working nodes."""
    from custom_components.cync_ble.const import CONF_KNOWN_GOOD, MAX_KNOWN_GOOD

    macs = [f"78:6D:EB:00:00:{i:02X}" for i in range(MAX_KNOWN_GOOD)]
    best = "F4:BC:DA:00:00:99"
    coordinator = _coordinator(hass, [*macs, best])
    for mac in macs:
        coordinator._record_sweep(mac, collected=1)
    coordinator._record_sweep(best, collected=38)

    kept = coordinator.entry.options[CONF_KNOWN_GOOD]
    assert len(kept) == MAX_KNOWN_GOOD
    assert best in kept, "the node with the largest sweep must survive the cap"


async def test_proven_nodes_survive_a_new_coordinator(hass):
    """Proven nodes are seeded from the config entry, not just learned.

    Learning them in memory alone was not enough: the count was seen
    resetting to zero between cycles without the entry reloading, and the
    cause was never established. Since a cycle that starts from a proven node
    finishes in ~8s and one that rediscovers takes 40-70s or fails outright,
    the knowledge is persisted so it survives whatever resets it.
    """
    from custom_components.cync_ble.const import CONF_KNOWN_GOOD

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="meshname",
        data={
            CONF_MESH_NAME: "meshname",
            CONF_MESH_PASSWORD: "meshpass",
            CONF_DEVICES: [
                {"id": 1, "name": "d", "type": 48, "mac": m}
                for m in [*REFUSING, ANSWERS]
            ],
        },
        options={CONF_KNOWN_GOOD: [ANSWERS]},
    )
    entry.add_to_hass(hass)
    coordinator = CyncBleCoordinator(hass, entry)

    assert ANSWERS in coordinator._known_good
    assert coordinator._candidate_macs()[0] == ANSWERS


async def test_a_barren_node_does_not_consume_the_whole_harvest_cycle(hass):
    """Connecting is not the goal - collecting is.

    Observed on the first real run after proven-node selection changed: the
    cycle reached a node of the refusing family, correctly demoted it, and then
    stopped with 44 candidates untried, because the loop broke on a successful
    *connection*. Such a node connects and authenticates perfectly and hands
    back nothing, so it has to be walked past within the same cycle.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    coordinator = _coordinator(hass)
    swept: list[str] = []

    async def _connect(mac):
        session = MagicMock()
        session._mac = mac
        coordinator._client = MagicMock()
        return session

    async def _sweep(session):
        swept.append(session._mac)
        # Only the last node in the list gives anything.
        return 38 if session._mac == ANSWERS else 0

    coordinator._connect_to = _connect
    coordinator._sweep_through = _sweep
    coordinator._async_close_link = AsyncMock()
    coordinator._resolve = lambda mac: True
    coordinator._known_good.add(REFUSING[0])  # seeded barren, tried first

    # Exactly MAX_CONNECT_ATTEMPTS candidates, so this tests the walk rather
    # than the budget - a barren node still costs a full connect plus a
    # window, so the budget is spent either way.
    with patch.object(
        coordinator, "_candidate_macs", return_value=[*REFUSING[:2], ANSWERS]
    ):
        await coordinator._async_harvest()

    assert len(swept) == 3, "a barren node must not end the cycle"
    assert swept[-1] == ANSWERS, "the walk must reach a node that actually sweeps"
    assert ANSWERS in coordinator._known_good
    assert REFUSING[0] not in coordinator._known_good, "and demote the barren one"


# ---------------------------------------------------------------------------
# Connection slots. max_attempts does NOT bound this failure - see
# _has_free_connection_slot.
# ---------------------------------------------------------------------------


async def test_no_attempt_is_made_when_every_slot_is_taken(hass):
    """The 36-second trap. Out-of-slots is a *transient* error in
    bleak_retry_connector, bounded by a hardcoded MAX_TRANSIENT_ERRORS of 9
    with a 4s backoff - not by max_attempts. Three nodes of that is 108s
    against a 45s deadline, so the attempt has to be skipped, not shortened.
    """
    from unittest.mock import MagicMock, patch

    coordinator = _coordinator(hass)
    coordinator._resolve = lambda mac: (MagicMock(), mac)

    full = [MagicMock(free=0), MagicMock(free=0)]
    with (
        patch("habluetooth.get_manager") as manager,
        patch("custom_components.cync_ble.coordinator.establish_connection") as connect,
    ):
        manager.return_value.async_current_allocations.return_value = full
        result = await coordinator._connect_to(ANSWERS)

    assert result is None
    connect.assert_not_called(), "must not spend 36s finding out"
    assert ANSWERS in coordinator._recent_failures


async def test_an_attempt_is_made_when_a_slot_is_free(hass):
    from unittest.mock import MagicMock, patch

    coordinator = _coordinator(hass)
    coordinator._resolve = lambda mac: (MagicMock(), mac)

    with (
        patch("habluetooth.get_manager") as manager,
        patch("custom_components.cync_ble.coordinator.establish_connection") as connect,
        patch.object(coordinator, "_authenticate", return_value=None),
    ):
        manager.return_value.async_current_allocations.return_value = [
            MagicMock(free=0),
            MagicMock(free=2),  # an ESPHome proxy with room
        ]
        await coordinator._connect_to(ANSWERS)

    connect.assert_called_once()


async def test_slot_check_fails_open(hass):
    """If habluetooth changes shape or raises, connect anyway. A diagnostic
    optimisation must never be why the integration stops working."""
    from unittest.mock import MagicMock, patch

    coordinator = _coordinator(hass)
    coordinator._resolve = lambda mac: (MagicMock(), mac)

    with (
        patch("habluetooth.get_manager", side_effect=RuntimeError("gone")),
        patch("custom_components.cync_ble.coordinator.establish_connection") as connect,
        patch.object(coordinator, "_authenticate", return_value=None),
    ):
        await coordinator._connect_to(ANSWERS)

    connect.assert_called_once()
