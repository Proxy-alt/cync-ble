"""How a command and a harvest combine into the state an entity shows.

The ordering here is the whole point. A harvest taken immediately after a
command has been observed still reporting the *previous* value - state
propagates through the mesh at its own pace - so a naive "harvest always
wins" would make every toggle visibly snap back.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from cync_lan.ble_mesh import DeviceStatus
from homeassistant.helpers.update_coordinator import UpdateFailed
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


async def test_state_polling_pauses_after_repeated_failure(hass):
    """Falling back to command-only is a deliberate degradation. It must not
    mark the refresh failed - a paused integration still commands devices
    fine, and failing would make every entity unavailable for a capability it
    never lost."""
    from custom_components.cync_ble.const import HARVEST_FAILURE_LIMIT

    coordinator = _coordinator(hass)

    async def _harvest_nothing() -> None:
        return None

    coordinator._async_harvest = _harvest_nothing

    for _ in range(HARVEST_FAILURE_LIMIT - 1):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    # The one that trips the limit reports success rather than failure.
    assert await coordinator._async_update_data() == {}
    assert coordinator.state_polling_active is False


async def test_a_paused_integration_stops_harvesting_but_keeps_its_link(hass):
    """Pausing state polling must not mean going idle.

    Establishing a connection is the unreliable step on this transport, so
    dropping the link between commands would put the fragile operation in
    front of every user action. The paused mode holds one link open and
    health-checks it - which is what this coordinator did before harvesting
    existed - and only stops the sacrificial harvest.
    """
    coordinator = _coordinator(hass)
    coordinator._harvest_paused_until = time.monotonic() + 3600
    harvested = False
    ensured = False

    async def _should_not_run() -> None:
        nonlocal harvested
        harvested = True

    async def _ensure():
        nonlocal ensured
        ensured = True

    coordinator._async_harvest = _should_not_run
    coordinator._async_ensure_connected = _ensure

    await coordinator._async_update_data()

    assert harvested is False, "a paused coordinator must not harvest"
    assert ensured is True, "but it must still keep a link alive"


async def test_a_missing_bumble_backend_degrades_instead_of_bricking(hass):
    """Configuring a dedicated adapter without the optional backend
    installed must not disable the integration.

    Observed on a real install: every cycle failed in 0.005s with no useful
    error, because DirectClientUnavailable propagated instead of degrading.
    The integration works perfectly well on Home Assistant's own stack, so
    that is where it should land.
    """
    from custom_components.cync_ble.const import CONF_DIRECT_ADAPTER
    from custom_components.cync_ble.direct_client import DirectClientUnavailable

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="meshname",
        data={
            CONF_MESH_NAME: "meshname",
            CONF_MESH_PASSWORD: "meshpass",
            CONF_DEVICES: [
                {"id": TARGET, "name": "d", "type": 48, "mac": "F4BCDA32A971"}
            ],
        },
        options={CONF_DIRECT_ADAPTER: "hci1"},
    )
    entry.add_to_hass(hass)
    coordinator = CyncBleCoordinator(hass, entry)
    assert coordinator.direct_mode is True

    with (
        patch(
            "custom_components.cync_ble.coordinator.build_direct_client",
            side_effect=DirectClientUnavailable("not installed"),
        ),
        patch(
            "custom_components.cync_ble.coordinator.bluetooth."
            "async_ble_device_from_address",
            return_value=None,
        ),
    ):
        # Falls through to the Home Assistant path, which finds nothing here -
        # reaching it at all is the point.
        assert await coordinator._connect_to("F4BCDA32A971") is None

    assert coordinator.direct_mode is False, "direct mode must switch itself off"


async def test_an_offline_reading_fills_a_gap_but_never_overwrites(hass):
    """A device the mesh cannot reach still reports the last level it knew.

    That is worth having when nothing else is known, and worth ignoring when
    something fresher is - the alternative is a device that was switched on an
    hour ago overwriting a reading taken seconds ago.
    """
    coordinator = _coordinator(hass)

    offline = DeviceStatus(device_id=TARGET, brightness=100, is_rgb=False, online=False)
    coordinator._on_mesh_status([offline])
    assert coordinator.device_states[TARGET].brightness == 100, (
        "with nothing known, an offline reading is better than none"
    )

    fresh = DeviceStatus(device_id=TARGET, brightness=25, is_rgb=False, online=True)
    coordinator._on_mesh_status([fresh])
    assert coordinator.device_states[TARGET].brightness == 25

    coordinator._on_mesh_status([offline])
    assert coordinator.device_states[TARGET].brightness == 25, (
        "a stale offline reading must not displace a fresh one"
    )
