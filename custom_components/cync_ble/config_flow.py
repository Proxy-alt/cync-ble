"""Config flow for Cync Bluetooth.

Mirrors cync-lan's own config flow for the account-login/OTP steps (see its
module docstring for why cync_lan.cloud_api reads credentials from process
env vars rather than call arguments, and why that limits this integration to
one Cync account per Home Assistant instance too - unique_id enforcement
below exists for the same reason).

Diverges from it after login: cync-lan's export covers every home on the
account at once, because its TCP daemon can juggle every device regardless
of which physical mesh it belongs to. A single BleMeshSession can only ever
be authenticated against one mesh's name/password, so this flow has an extra
step cync-lan's doesn't need - picking which home's mesh to control, when the
account has more than one.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.exceptions import HomeAssistantError

from .classify import is_light, is_switch
from .const import (
    CONF_ACCOUNT_PASSWORD,
    CONF_ACCOUNT_USERNAME,
    CONF_DEVICES,
    CONF_HOME_NAME,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    DOMAIN,
)
from .util import configure_environment, get_cloud_api, read_exported_homes

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCOUNT_USERNAME): str,
        vol.Required(CONF_ACCOUNT_PASSWORD): str,
    }
)
STEP_OTP_SCHEMA = vol.Schema({vol.Required("otp_code"): str})


class InvalidAuth(HomeAssistantError):
    """Username/password rejected."""


class InvalidOtp(HomeAssistantError):
    """OTP code rejected."""


def _usable_devices(raw_devices: dict) -> list[dict[str, Any]]:
    """Flatten a home's exported device dict to the light/switch subset this
    integration can represent, in the shape the coordinator/platforms expect.

    Devices with no BLE mac (impossible for a real export, since every entry
    cloud_api._parse_raw_export writes has one) or that classify as neither a
    light nor a switch (fan controllers, sensors, thermostats - not yet
    built, see ARCHITECTURE.md's build order) are skipped rather than
    guessed at.
    """
    usable: list[dict[str, Any]] = []
    for dev_id, dev in raw_devices.items():
        dev_type = dev.get("type")
        mac = dev.get("mac")
        if dev_type is None or not mac:
            continue
        if not (is_light(dev_type) or is_switch(dev_type)):
            _LOGGER.debug(
                "Skipping device %r (type %s): not yet supported by cync_ble "
                "(switch/light only)",
                dev.get("name"),
                dev_type,
            )
            continue
        usable.append(
            {
                "id": int(dev_id),
                "name": dev.get("name") or f"device_{dev_id}",
                "type": dev_type,
                "mac": mac,
            }
        )
    return usable


class CyncBleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Cync Bluetooth."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._homes: dict[str, dict] = {}
        self._chosen_home_name: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_ACCOUNT_USERNAME]
            self._password = user_input[CONF_ACCOUNT_PASSWORD]

            await configure_environment(self.hass, self._username, self._password)
            try:
                api = get_cloud_api(self.hass)
                have_token = await api.check_token()
                if have_token:
                    return await self._finish_export()
                requested = await api.request_otp()
                if not requested:
                    raise InvalidAuth
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error talking to the Cync cloud API")
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_otp()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                api = get_cloud_api(self.hass)
                ok = await api.send_otp(int(user_input["otp_code"]))
                if not ok:
                    raise InvalidOtp
            except (InvalidOtp, ValueError):
                errors["base"] = "invalid_otp"
            except Exception:
                _LOGGER.exception("Unexpected error submitting OTP to the Cync cloud API")  # noqa: E501
                errors["base"] = "cannot_connect"
            else:
                return await self._finish_export()

        return self.async_show_form(
            step_id="otp", data_schema=STEP_OTP_SCHEMA, errors=errors
        )

    async def _finish_export(self) -> config_entries.ConfigFlowResult:
        """Pull the account's homes, so a bad account/empty export fails
        here rather than producing zero entities after setup finishes."""
        api = get_cloud_api(self.hass)
        exported = await api.export_config_file()
        if not exported:
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_SCHEMA,
                errors={"base": "no_devices"},
            )

        config_dir = self.hass.config.path("cync_ble")
        homes = await read_exported_homes(config_dir)
        # Only homes with at least one light/switch this integration can
        # actually control are worth offering - an empty mesh, or one that's
        # entirely sensors/thermostats today, isn't a usable choice.
        self._homes = {
            name: home
            for name, home in homes.items()
            if _usable_devices(home.get("devices", {}))
        }
        if not self._homes:
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_SCHEMA,
                errors={"base": "no_devices"},
            )

        if len(self._homes) == 1:
            self._chosen_home_name = next(iter(self._homes))
            return await self.async_step_confirm()
        return await self.async_step_select_home()

    async def async_step_select_home(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._chosen_home_name = user_input[CONF_HOME_NAME]
            return await self.async_step_confirm()

        return self.async_show_form(
            step_id="select_home",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOME_NAME): vol.In(sorted(self._homes))}
            ),
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        assert self._chosen_home_name is not None
        home = self._homes[self._chosen_home_name]
        devices = _usable_devices(home.get("devices", {}))

        if user_input is not None:
            # unique-config-entry: one mesh, one config entry. The home's own
            # mac (the mesh name - see cync_lan.ble_mesh.mesh_credentials_from_home)
            # is the natural unique id, stable across re-runs of this flow.
            await self.async_set_unique_id(str(home["mac"]))
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._chosen_home_name,
                data={
                    CONF_MESH_NAME: str(home["mac"]),
                    CONF_MESH_PASSWORD: str(home["access_key"]),
                    CONF_DEVICES: devices,
                },
            )

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "home_name": self._chosen_home_name,
                "device_count": str(len(devices)),
            },
        )
