"""Tests for the Cync Bluetooth config flow.

config-flow-test-coverage: exercises every step and outcome the flow can
reach - the single-home happy path, the multi-home picker, a rejected
account, a rejected one-time code, a non-numeric code, an unreachable
cloud at each of the three places it is contacted, a home with nothing
controllable, and the duplicate-mesh abort.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cync_ble.cloud import CyncAuthError, CyncCloudError
from custom_components.cync_ble.const import (
    CONF_DEVICES,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    DOMAIN,
)


async def _start(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _credentials(hass, flow_id):
    return await hass.config_entries.flow.async_configure(
        flow_id,
        {"account_username": "user@example.com", "account_password": "hunter2"},
    )


async def _otp(hass, flow_id, code="123456"):
    return await hass.config_entries.flow.async_configure(flow_id, {"otp_code": code})


async def test_single_home_happy_path(hass, mock_cloud):
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await _credentials(hass, result["flow_id"])
    assert result["step_id"] == "otp"

    result = await _otp(hass, result["flow_id"])
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["device_count"] == "1"
    assert result["description_placeholders"]["home_name"] == "My Home"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "My Home"
    assert result["data"][CONF_MESH_NAME] == "meshname1"
    assert result["data"][CONF_MESH_PASSWORD] == "meshname1-password"
    assert len(result["data"][CONF_DEVICES]) == 1
    assert result["data"][CONF_DEVICES][0]["name"] == "Kitchen Switch"


async def test_credentials_the_user_typed_are_the_ones_used(hass, mock_cloud):
    """Regression guard. The previous implementation routed through
    cync_lan's env-var-configured singleton, which - when cync-lan was also
    installed - silently ignored what was typed here and authenticated as
    the other integration's account instead."""
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    await _otp(hass, result["flow_id"])

    mock_cloud.request_otp.assert_awaited_once_with("user@example.com")
    mock_cloud.login.assert_awaited_once_with("user@example.com", "hunter2", 123456)


async def test_multiple_homes_shows_picker(hass, mock_cloud, multiple_homes):
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    result = await _otp(hass, result["flow_id"])
    assert result["step_id"] == "select_home"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"home_name": "Home B"}
    )
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["home_name"] == "Home B"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MESH_NAME] == "meshnameB"


async def test_unknown_account_is_invalid_auth(hass, mock_cloud):
    mock_cloud.request_otp = AsyncMock(side_effect=CyncAuthError("user not exists"))
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    assert result["errors"] == {"base": "invalid_auth"}


async def test_unreachable_cloud_at_login_is_cannot_connect(hass, mock_cloud):
    mock_cloud.request_otp = AsyncMock(side_effect=CyncCloudError("dns failure"))
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    assert result["errors"] == {"base": "cannot_connect"}


async def test_rejected_otp(hass, mock_cloud):
    mock_cloud.login = AsyncMock(side_effect=CyncAuthError("bad code"))
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    result = await _otp(hass, result["flow_id"])
    assert result["errors"] == {"base": "invalid_otp"}


async def test_non_numeric_otp_is_invalid_otp_not_a_crash(hass, mock_cloud):
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    result = await _otp(hass, result["flow_id"], code="not-a-number")
    assert result["errors"] == {"base": "invalid_otp"}
    mock_cloud.login.assert_not_awaited()


async def test_unreachable_cloud_at_otp_is_cannot_connect(hass, mock_cloud):
    mock_cloud.login = AsyncMock(side_effect=CyncCloudError("timeout"))
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    result = await _otp(hass, result["flow_id"])
    assert result["errors"] == {"base": "cannot_connect"}


async def test_unreachable_cloud_at_device_list_is_cannot_connect(hass, mock_cloud):
    mock_cloud.async_get_homes = AsyncMock(side_effect=CyncCloudError("timeout"))
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    result = await _otp(hass, result["flow_id"])
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_account_with_no_controllable_devices(
    hass, mock_cloud, no_controllable_devices
):
    """A home whose only device is a sensor must be reported as having no
    devices, not offered as a choice with zero entities behind it."""
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    result = await _otp(hass, result["flow_id"])
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_devices"}


async def test_sensors_are_dropped_but_switches_and_lights_kept(
    hass, mock_cloud, mixed_devices
):
    """Classification is real logic, not mocked: a home carrying a switch, a
    light and a motion sensor yields two entities, not three."""
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    result = await _otp(hass, result["flow_id"])
    assert result["description_placeholders"]["device_count"] == "2"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert sorted(d["name"] for d in result["data"][CONF_DEVICES]) == [
        "Kitchen Switch",
        "Lamp",
    ]


async def test_empty_account(hass, mock_cloud):
    mock_cloud.async_get_homes = AsyncMock(return_value=[])
    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    result = await _otp(hass, result["flow_id"])
    assert result["errors"] == {"base": "no_devices"}


async def test_duplicate_mesh_aborts(hass, mock_cloud):
    """unique-config-entry: the mesh's own name is the unique id, so
    re-adding the same mesh aborts rather than creating a second entry."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    MockConfigEntry(
        domain=DOMAIN,
        unique_id="meshname1",
        data={CONF_MESH_NAME: "meshname1", CONF_MESH_PASSWORD: "x", CONF_DEVICES: []},
    ).add_to_hass(hass)

    result = await _start(hass)
    result = await _credentials(hass, result["flow_id"])
    result = await _otp(hass, result["flow_id"])
    assert result["step_id"] == "confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
