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


@pytest.fixture
def mock_cloud_api():
    """Patch cync_lan.cloud_api.CyncCloudAPI for config flow tests, so no
    real network calls happen."""
    with patch("cync_lan.cloud_api.CyncCloudAPI") as mock_cls:
        instance = mock_cls.return_value
        instance.check_token = AsyncMock(return_value=False)
        instance.request_otp = AsyncMock(return_value=True)
        instance.send_otp = AsyncMock(return_value=True)
        instance.export_config_file = AsyncMock(return_value=True)
        yield instance


# A single usable (switch-classified) device, in the shape
# cloud_api._parse_raw_export writes into `exported_homes.<home>.devices`.
ONE_SWITCH_DEVICE = {
    1: {"name": "Kitchen Switch", "type": 1, "mac": "AA:BB:CC:DD:EE:01"}
}


@pytest.fixture
def mock_single_home():
    """One home, one usable device - the common case."""
    homes = {
        "My Home": {
            "mac": "meshname1",
            "access_key": "meshpass1",
            "id": "home-1",
            "devices": ONE_SWITCH_DEVICE,
        }
    }
    with patch(
        "custom_components.cync_ble.config_flow.read_exported_homes",
        new=AsyncMock(return_value=homes),
    ), patch("custom_components.cync_ble.config_flow.is_light", return_value=False), patch(
        "custom_components.cync_ble.config_flow.is_switch", return_value=True
    ):
        yield homes


@pytest.fixture
def mock_multiple_homes():
    """Two homes, each with one usable device."""
    homes = {
        "Home A": {
            "mac": "meshnameA",
            "access_key": "meshpassA",
            "id": "home-a",
            "devices": {1: {"name": "A Switch", "type": 1, "mac": "AA:BB:CC:DD:EE:01"}},
        },
        "Home B": {
            "mac": "meshnameB",
            "access_key": "meshpassB",
            "id": "home-b",
            "devices": {2: {"name": "B Switch", "type": 1, "mac": "AA:BB:CC:DD:EE:02"}},
        },
    }
    with patch(
        "custom_components.cync_ble.config_flow.read_exported_homes",
        new=AsyncMock(return_value=homes),
    ), patch("custom_components.cync_ble.config_flow.is_light", return_value=False), patch(
        "custom_components.cync_ble.config_flow.is_switch", return_value=True
    ):
        yield homes


@pytest.fixture
def mock_no_usable_devices():
    """One home, but its only device doesn't classify as light or switch -
    e.g. a sensor - so it must be treated the same as an empty account."""
    homes = {
        "Empty Home": {
            "mac": "meshname1",
            "access_key": "meshpass1",
            "id": "home-1",
            "devices": {1: {"name": "Motion Sensor", "type": 96, "mac": "AA:BB:CC:DD:EE:01"}},
        }
    }
    with patch(
        "custom_components.cync_ble.config_flow.read_exported_homes",
        new=AsyncMock(return_value=homes),
    ), patch("custom_components.cync_ble.config_flow.is_light", return_value=False), patch(
        "custom_components.cync_ble.config_flow.is_switch", return_value=False
    ):
        yield homes
