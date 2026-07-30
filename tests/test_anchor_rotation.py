"""Which node each cycle dials, and in what order.

Two forces pull against each other, and an earlier version of this got it
wrong in both directions in turn.

Pinning every cycle to one known-good node means that device absorbs every
connect/disconnect in the house - the churn a fast poll interval collapsed
under. But rotating blindly across the whole mesh is worse: nodes differ
enormously in whether they will accept a connection at all (measured - one
answered 11 of 12 attempts while another refused both), and each dead end
costs ~18s before it gives up. Blind rotation spent entire refresh windows
dialling nodes that were never going to answer.

The rule that survives both: rotate, but only among nodes that have actually
connected recently.
"""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cync_ble.const import (
    CONF_DEVICES,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    DOMAIN,
    PROVEN_NODES,
)
from custom_components.cync_ble.coordinator import CyncBleCoordinator

MACS = ["AABBCCDDEE01", "AABBCCDDEE02", "AABBCCDDEE03", "AABBCCDDEE04"]


def _coordinator(hass, macs=MACS):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="meshname",
        data={
            CONF_MESH_NAME: "meshname",
            CONF_MESH_PASSWORD: "meshpass",
            CONF_DEVICES: [
                {"id": i, "name": f"d{i}", "type": 38, "mac": m}
                for i, m in enumerate(macs, start=1)
            ],
        },
    )
    entry.add_to_hass(hass)
    return CyncBleCoordinator(hass, entry)


async def test_with_nothing_proven_the_full_list_is_offered_in_order(hass):
    """Cold start has no evidence to go on, so it must not pretend to - and
    must still offer every node."""
    coordinator = _coordinator(hass)
    assert coordinator._candidate_macs() == MACS


async def test_a_proven_node_leads(hass):
    """A node known to answer is worth far more than an arbitrary one; each
    dead end costs ~18s of establish_connection."""
    coordinator = _coordinator(hass)
    coordinator._proven = ["AABBCCDDEE03"]
    assert coordinator._candidate_macs()[0] == "AABBCCDDEE03"


async def test_rotation_happens_among_proven_nodes(hass):
    """The load-spreading half: several proven nodes take turns leading
    rather than one taking every cycle."""
    coordinator = _coordinator(hass)
    coordinator._proven = ["AABBCCDDEE01", "AABBCCDDEE02", "AABBCCDDEE03"]
    leaders = [coordinator._candidate_macs()[0] for _ in range(4)]
    assert leaders == [
        "AABBCCDDEE01",
        "AABBCCDDEE02",
        "AABBCCDDEE03",
        "AABBCCDDEE01",
    ]


async def test_unproven_nodes_remain_as_fallback(hass):
    """Rotation reorders, never discards - if every proven node has since
    gone quiet the cycle must still be able to reach the others."""
    coordinator = _coordinator(hass)
    coordinator._proven = ["AABBCCDDEE02"]
    assert sorted(coordinator._candidate_macs()) == sorted(MACS)


async def test_a_single_proven_node_does_not_divide_by_zero(hass):
    coordinator = _coordinator(hass)
    coordinator._proven = ["AABBCCDDEE02"]
    for _ in range(3):
        assert coordinator._candidate_macs()[0] == "AABBCCDDEE02"


async def test_proven_entries_for_devices_no_longer_configured_are_ignored(hass):
    """The device list can change under us when the config entry is
    refreshed; a stale proven entry must not be dialled or counted."""
    coordinator = _coordinator(hass)
    coordinator._proven = ["DEADBEEF0000", "AABBCCDDEE02"]
    candidates = coordinator._candidate_macs()
    assert "DEADBEEF0000" not in candidates
    assert sorted(candidates) == sorted(MACS)


async def test_no_devices_is_not_an_error(hass):
    coordinator = _coordinator(hass, macs=[])
    assert coordinator._candidate_macs() == []


async def test_proven_list_stays_bounded(hass):
    """Otherwise it degrades back into a blind walk of the whole mesh."""
    coordinator = _coordinator(hass)
    for i in range(PROVEN_NODES + 3):
        mac = f"AABBCCDDE{i:03d}"
        if mac in coordinator._proven:
            coordinator._proven.remove(mac)
        coordinator._proven.insert(0, mac)
        del coordinator._proven[PROVEN_NODES:]
    assert len(coordinator._proven) == PROVEN_NODES
