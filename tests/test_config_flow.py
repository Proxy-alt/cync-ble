"""Tests for the Cync Bluetooth config flow.

config-flow-test-coverage: exercises every step and outcome the flow can
reach - immediate success (cached token, single home), OTP-required
success, the multi-home picker, invalid credentials, invalid OTP, and the
no-usable-devices abort.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cync_ble.const import (
    CONF_DEVICES,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    DOMAIN,
)


async def _start_user_step(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _submit_credentials(hass, flow_id):
    return await hass.config_entries.flow.async_configure(
        flow_id,
        {"account_username": "user@example.com", "account_password": "hunter2"},
    )


async def test_immediate_success_single_home(hass, mock_cloud_api, mock_single_home):
    """check_token() True (a cached, still-valid session) skips OTP, and a
    single usable home skips the home-picker step too."""
    mock_cloud_api.check_token = AsyncMock(return_value=True)

    result = await _start_user_step(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await _submit_credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["device_count"] == "1"
    assert result["description_placeholders"]["home_name"] == "My Home"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "My Home"
    assert result["data"][CONF_MESH_NAME] == "meshname1"
    assert result["data"][CONF_MESH_PASSWORD] == "meshpass1"
    assert len(result["data"][CONF_DEVICES]) == 1
    assert result["data"][CONF_DEVICES][0]["name"] == "Kitchen Switch"


async def test_otp_required_success(hass, mock_cloud_api, mock_single_home):
    result = await _start_user_step(hass)
    result = await _submit_credentials(hass, result["flow_id"])
    assert result["step_id"] == "otp"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"otp_code": "123456"}
    )
    assert result["step_id"] == "confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_multiple_homes_shows_select_home_step(
    hass, mock_cloud_api, mock_multiple_homes
):
    mock_cloud_api.check_token = AsyncMock(return_value=True)

    result = await _start_user_step(hass)
    result = await _submit_credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_home"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"home_name": "Home B"}
    )
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["home_name"] == "Home B"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MESH_NAME] == "meshnameB"


async def test_invalid_auth(hass, mock_cloud_api):
    mock_cloud_api.request_otp = AsyncMock(return_value=False)

    result = await _start_user_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"account_username": "user@example.com", "account_password": "wrong"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_invalid_otp(hass, mock_cloud_api):
    mock_cloud_api.send_otp = AsyncMock(return_value=False)

    result = await _start_user_step(hass)
    result = await _submit_credentials(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"otp_code": "000000"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_otp"}


async def test_no_usable_devices_found(hass, mock_cloud_api, mock_no_usable_devices):
    """A home whose only device is e.g. a sensor (neither light nor switch)
    must be treated the same as an empty account - not offered as a choice
    with zero controllable entities behind it."""
    mock_cloud_api.check_token = AsyncMock(return_value=True)

    result = await _start_user_step(hass)
    result = await _submit_credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_devices"}


async def test_export_failure_shows_no_devices_error(hass, mock_cloud_api):
    mock_cloud_api.check_token = AsyncMock(return_value=True)
    mock_cloud_api.export_config_file = AsyncMock(return_value=False)

    result = await _start_user_step(hass)
    result = await _submit_credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_devices"}


async def test_duplicate_mesh_aborts(hass, mock_cloud_api, mock_single_home):
    """unique-config-entry: the mesh's own identity (the home's `mac`) is
    the unique id, so re-adding the same mesh aborts rather than creating a
    second config entry for it."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    MockConfigEntry(
        domain=DOMAIN,
        unique_id="meshname1",
        data={CONF_MESH_NAME: "meshname1", CONF_MESH_PASSWORD: "x", CONF_DEVICES: []},
    ).add_to_hass(hass)

    mock_cloud_api.check_token = AsyncMock(return_value=True)
    result = await _start_user_step(hass)
    result = await _submit_credentials(hass, result["flow_id"])
    assert result["step_id"] == "confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_step_cannot_connect(hass, mock_cloud_api):
    mock_cloud_api.check_token = AsyncMock(side_effect=RuntimeError("boom"))

    result = await _start_user_step(hass)
    result = await _submit_credentials(hass, result["flow_id"])
    assert result["errors"] == {"base": "cannot_connect"}


async def test_otp_step_non_numeric_code_is_invalid_otp(hass, mock_cloud_api):
    """int(otp_code) failing is treated the same as the cloud API rejecting
    it - both are "the code was wrong", not a connection problem."""
    result = await _start_user_step(hass)
    result = await _submit_credentials(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"otp_code": "not-a-number"}
    )
    assert result["errors"] == {"base": "invalid_otp"}


async def test_otp_step_cannot_connect(hass, mock_cloud_api):
    """A genuinely numeric code that still fails to submit (network error,
    not a rejection) must hit the generic handler, not invalid_otp - unlike
    the case above, send_otp() actually gets called here since int()
    succeeds first."""
    result = await _start_user_step(hass)
    result = await _submit_credentials(hass, result["flow_id"])
    mock_cloud_api.send_otp = AsyncMock(side_effect=RuntimeError("boom"))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"otp_code": "123456"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_malformed_devices_are_skipped(hass, mock_cloud_api):
    """A device missing `type` or `mac` in the export must be skipped
    rather than crashing the flow - real accounts have been observed with
    incomplete entries (see cync_lan.cloud_api's own tolerance for this)."""
    homes = {
        "My Home": {
            "mac": "meshname1",
            "access_key": "meshpass1",
            "id": "home-1",
            "devices": {
                1: {"name": "Kitchen Switch", "type": 1, "mac": "AA:BB:CC:DD:EE:01"},
                2: {"name": "No Mac", "type": 1},
                3: {"name": "No Type", "mac": "AA:BB:CC:DD:EE:03"},
            },
        }
    }
    mock_cloud_api.check_token = AsyncMock(return_value=True)
    with (
        patch(
            "custom_components.cync_ble.config_flow.read_exported_homes",
            new=AsyncMock(return_value=homes),
        ),
        patch("custom_components.cync_ble.config_flow.is_light", return_value=False),
        patch("custom_components.cync_ble.config_flow.is_switch", return_value=True),
    ):
        result = await _start_user_step(hass)
        result = await _submit_credentials(hass, result["flow_id"])
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["device_count"] == "1"
