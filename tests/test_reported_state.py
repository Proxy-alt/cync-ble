"""How a command and a harvest combine into the state an entity shows.

The ordering here is the whole point. A harvest taken immediately after a
command has been observed still reporting the *previous* value - state
propagates through the mesh at its own pace - so a naive "harvest always
wins" would make every toggle visibly snap back.
"""

from __future__ import annotations

from cync_lan.ble_mesh import DeviceStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cync_ble.const import (
    CONF_DEVICES,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    DOMAIN,
)
from custom_components.cync_ble.coordinator import CyncBleCoordinator

TARGET = 16


def _coordinator(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="meshname",
        data={
            CONF_MESH_NAME: "meshname",
            CONF_MESH_PASSWORD: "meshpass",
            CONF_DEVICES: [
                {"id": TARGET, "name": "Stairs", "type": 48, "mac": "F4BCDA32A971"}
            ],
        },
    )
    entry.add_to_hass(hass)
    return CyncBleCoordinator(hass, entry)


def _harvested(coordinator, brightness: int, at: float) -> None:
    coordinator.device_states[TARGET] = DeviceStatus(
        device_id=TARGET, brightness=brightness, is_rgb=False
    )
    coordinator._last_harvest = at


async def test_nothing_known_yet_reports_nothing(hass):
    """A device never harvested and never commanded has no state to report -
    the entity falls back to whatever it restored, rather than being told a
    made-up value."""
    coordinator = _coordinator(hass)
    assert coordinator.reported_brightness(TARGET) is None


async def test_harvested_state_is_reported(hass):
    coordinator = _coordinator(hass)
    _harvested(coordinator, 60, at=100.0)
    assert coordinator.reported_brightness(TARGET) == 60


async def test_a_command_newer_than_the_harvest_wins(hass):
    """The anti-snap-back rule. Without this the entity shows the freshly
    commanded value, then reverts to the stale harvest the moment the
    coordinator notifies listeners."""
    coordinator = _coordinator(hass)
    _harvested(coordinator, 60, at=100.0)
    coordinator._record_optimistic(TARGET, 25)
    assert coordinator.reported_brightness(TARGET) == 25


async def test_a_harvest_newer_than_the_command_takes_over(hass):
    """Once the mesh has reported since the command, its account is the
    better one - including when the command did not actually take effect."""
    coordinator = _coordinator(hass)
    coordinator._record_optimistic(TARGET, 25)
    _harvested(coordinator, 60, at=coordinator.optimistic[TARGET][0] + 1)
    assert coordinator.reported_brightness(TARGET) == 60


async def test_a_superseded_command_is_forgotten(hass):
    """Stale optimistic entries must not accumulate for the lifetime of the
    entry - one per device per command, on a mesh of 46."""
    coordinator = _coordinator(hass)
    coordinator._record_optimistic(TARGET, 25)
    _harvested(coordinator, 60, at=coordinator.optimistic[TARGET][0] + 1)
    coordinator.reported_brightness(TARGET)
    assert TARGET not in coordinator.optimistic


async def test_off_is_reported_as_zero_not_as_unknown(hass):
    """0 is a real reading, not an absence. Getting this wrong would make
    every switched-off device fall back to its restored state forever."""
    coordinator = _coordinator(hass)
    _harvested(coordinator, 0, at=100.0)
    assert coordinator.reported_brightness(TARGET) == 0


async def test_assumed_state_only_until_the_mesh_reports(hass):
    coordinator = _coordinator(hass)
    assert coordinator.device_states.get(TARGET) is None
    _harvested(coordinator, 60, at=100.0)
    assert coordinator.device_states.get(TARGET) is not None
