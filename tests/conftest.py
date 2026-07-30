"""Fixtures for Cync Bluetooth tests.

Requires the `pytest-homeassistant-custom-component` package, a dev/test-only
dependency not part of this repo's own runtime requirements:

    pip install pytest-homeassistant-custom-component
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Required for pytest-homeassistant-custom-component to load
    custom_components/ instead of only core integrations."""
    yield


@pytest.fixture(autouse=True)
def auto_stub_bluetooth_dependency(hass):
    """cync_ble's manifest depends on `bluetooth_adapters`, so Home
    Assistant insists on fully setting up the real `bluetooth` component (and
    its own BlueZ/dbus adapter discovery) before this domain's config flow
    can even run - which crashes in this sandboxed test environment (no real
    Bluetooth adapter, and on macOS `bluetooth_adapters`' own D-Bus backend
    is entirely absent, not just empty).

    None of that is exercised by the config flow tests in this file
    (config_flow.py never touches Bluetooth APIs - only coordinator.py does,
    which isn't under test here), so real setup isn't needed at all.
    `async_setup_component` short-circuits to True for any domain already
    listed in `hass.config.components` - the same sanctioned bypass Home
    Assistant's own test suite uses for a dependency that's beside the point
    of what's actually being tested.
    """
    hass.config.components.add("bluetooth")
    hass.config.components.add("bluetooth_adapters")


# Real device types from cync_lan's own model map, deliberately not mocked:
# 38 is a plain wired switch, 5 a light, 96 a motion sensor. Classification
# is real logic worth exercising - an earlier revision of these fixtures
# patched is_light/is_switch away and so never noticed it was feeding in a
# type the map classifies as "unknown".
SWITCH = {"id": 1, "name": "Kitchen Switch", "type": 38, "mac": "AA:BB:CC:DD:EE:01"}
LIGHT = {"id": 2, "name": "Lamp", "type": 5, "mac": "AA:BB:CC:DD:EE:02"}
MOTION_SENSOR = {
    "id": 3,
    "name": "Motion Sensor",
    "type": 96,
    "mac": "AA:BB:CC:DD:EE:03",
}


def _home(name: str, mesh: str, devices: list[dict]) -> dict:
    return {
        "name": name,
        "mesh_name": mesh,
        "mesh_password": f"{mesh}-password",
        "devices": devices,
    }


@pytest.fixture
def mock_cloud():
    """Patch the CyncCloud class the config flow constructs, so no real
    network calls happen. Yields the instance the flow will receive."""
    with patch(
        "custom_components.cync_ble.config_flow.CyncCloud", autospec=True
    ) as cls:
        cloud = cls.return_value
        cloud.request_otp = AsyncMock(return_value=None)
        cloud.login = AsyncMock(return_value=None)
        cloud.async_get_homes = AsyncMock(
            return_value=[_home("My Home", "meshname1", [SWITCH])]
        )
        yield cloud


@pytest.fixture
def multiple_homes(mock_cloud):
    """Two homes, each with one controllable device."""
    homes = [
        _home("Home A", "meshnameA", [{**SWITCH, "name": "A Switch"}]),
        _home("Home B", "meshnameB", [{**SWITCH, "id": 2, "name": "B Switch"}]),
    ]
    mock_cloud.async_get_homes = AsyncMock(return_value=homes)
    return homes


@pytest.fixture
def mixed_devices(mock_cloud):
    """One home with a switch, a light, and a motion sensor - the sensor
    must be dropped and the other two kept."""
    home = _home("Mixed", "meshname1", [SWITCH, LIGHT, MOTION_SENSOR])
    mock_cloud.async_get_homes = AsyncMock(return_value=[home])
    return home


@pytest.fixture
def no_controllable_devices(mock_cloud):
    """One home whose only device is a motion sensor - neither a light nor a
    switch, so the home must be treated as unusable."""
    home = _home("Sensors Only", "meshname1", [MOTION_SENSOR])
    mock_cloud.async_get_homes = AsyncMock(return_value=[home])
    return home
