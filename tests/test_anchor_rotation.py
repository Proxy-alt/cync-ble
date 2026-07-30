"""Each cycle starts from a different node.

Pinning every connect to one "known good" node is the obvious design and the
wrong one here: mesh relay means any node reaches everything, so a fixed
anchor just means one device absorbs every connect/disconnect in the house.
That is the churn a fast poll interval collapsed under.
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

MACS = ["AABBCCDDEE01", "AABBCCDDEE02", "AABBCCDDEE03"]


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


async def test_each_cycle_leads_with_a_different_node(hass):
    coordinator = _coordinator(hass)
    leaders = [coordinator._candidate_macs()[0] for _ in range(3)]
    assert leaders == MACS


async def test_rotation_wraps(hass):
    coordinator = _coordinator(hass)
    leaders = [coordinator._candidate_macs()[0] for _ in range(7)]
    assert leaders == MACS + MACS + MACS[:1]


async def test_every_node_stays_available_as_a_fallback(hass):
    """Rotation changes the order, never the set - a cycle whose lead node is
    unreachable must still be able to fall through to the others."""
    coordinator = _coordinator(hass)
    for _ in range(4):
        assert sorted(coordinator._candidate_macs()) == sorted(MACS)


async def test_no_devices_is_not_an_error(hass):
    coordinator = _coordinator(hass, macs=[])
    assert coordinator._candidate_macs() == []


async def test_single_device_mesh_does_not_divide_by_zero(hass):
    coordinator = _coordinator(hass, macs=["AABBCCDDEE01"])
    assert coordinator._candidate_macs() == ["AABBCCDDEE01"]
    assert coordinator._candidate_macs() == ["AABBCCDDEE01"]
