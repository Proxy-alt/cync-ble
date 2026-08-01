"""Config flow for Cync Bluetooth.

Account login → one-time code → pick a home → done. The account is used
once, here, to fetch that home's Telink mesh credentials and device list;
everything after setup is local.

Talks to `.cloud`, this integration's own small client, rather than
`cync_lan.cloud_api` - see that module's docstring for why sharing
cync-lan's env-var-configured singleton silently breaks when both
integrations are installed together.

Unlike cync-lan, which exports every home at once, this picks exactly one:
a `BleMeshSession` authenticates against a single mesh's name and password,
so one config entry is one home. Add the integration again for another.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .adapters import (
    ADAPTER_NONE,
    async_list_adapters,
    is_selectable,
    selection_options,
)
from .classify import is_light, is_switch
from .cloud import CyncAuthError, CyncCloud, CyncCloudError
from .const import (
    CONF_ACCOUNT_PASSWORD,
    CONF_ACCOUNT_USERNAME,
    CONF_DEVICES,
    CONF_DIRECT_ADAPTER,
    CONF_HOME_NAME,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCOUNT_USERNAME): str,
        vol.Required(CONF_ACCOUNT_PASSWORD): str,
    }
)
STEP_OTP_SCHEMA = vol.Schema({vol.Required("otp_code"): str})


def _controllable(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The subset of a home's devices this integration can represent.

    Anything that is neither a light nor a switch - fan controllers,
    sensors, thermostats - is dropped rather than guessed at, per
    ARCHITECTURE.md's build order.
    """
    keep = []
    for device in devices:
        if is_light(device["type"]) or is_switch(device["type"]):
            keep.append(device)
        else:
            _LOGGER.debug(
                "Skipping %r (device type %s): cync_ble covers switches and "
                "lights so far",
                device["name"],
                device["type"],
            )
    return keep


class CyncBleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Cync Bluetooth."""

    VERSION = 1

    def __init__(self) -> None:
        self._cloud: CyncCloud | None = None
        self._username: str | None = None
        self._password: str | None = None
        self._homes: dict[str, dict[str, Any]] = {}
        self._chosen: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_ACCOUNT_USERNAME]
            self._password = user_input[CONF_ACCOUNT_PASSWORD]
            self._cloud = CyncCloud(async_get_clientsession(self.hass))
            try:
                await self._cloud.request_otp(self._username)
            except CyncAuthError as err:
                _LOGGER.debug("Cync rejected the account %s: %s", self._username, err)
                errors["base"] = "invalid_auth"
            except CyncCloudError:
                _LOGGER.exception("Could not reach the Cync cloud API")
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
            assert self._cloud is not None
            assert self._username is not None
            assert self._password is not None
            try:
                code = int(user_input["otp_code"])
            except ValueError:
                errors["base"] = "invalid_otp"
            else:
                try:
                    await self._cloud.login(self._username, self._password, code)
                except CyncAuthError as err:
                    _LOGGER.debug("Cync rejected the one-time code: %s", err)
                    errors["base"] = "invalid_otp"
                except CyncCloudError:
                    _LOGGER.exception("Could not reach the Cync cloud API")
                    errors["base"] = "cannot_connect"
                else:
                    return await self._async_load_homes()

        return self.async_show_form(
            step_id="otp", data_schema=STEP_OTP_SCHEMA, errors=errors
        )

    async def _async_load_homes(self) -> config_entries.ConfigFlowResult:
        """Fetch the account's homes, so an account with nothing this
        integration can drive fails here rather than after setup finishes
        with zero entities."""
        assert self._cloud is not None
        try:
            homes = await self._cloud.async_get_homes()
        except CyncCloudError:
            _LOGGER.exception("Could not read the device list from the Cync cloud API")
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_SCHEMA,
                errors={"base": "cannot_connect"},
            )

        self._homes = {}
        for home in homes:
            controllable = _controllable(home["devices"])
            if controllable:
                self._homes[home["name"]] = {**home, "devices": controllable}

        if not self._homes:
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_SCHEMA,
                errors={"base": "no_devices"},
            )
        if len(self._homes) == 1:
            self._chosen = next(iter(self._homes))
            return await self.async_step_confirm()
        return await self.async_step_select_home()

    async def async_step_select_home(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._chosen = user_input[CONF_HOME_NAME]
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
        assert self._chosen is not None
        home = self._homes[self._chosen]

        if user_input is not None:
            # unique-config-entry: one mesh, one entry. The mesh name is the
            # home's own identity and is stable across re-runs of this flow.
            await self.async_set_unique_id(home["mesh_name"])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._chosen,
                data={
                    CONF_MESH_NAME: home["mesh_name"],
                    CONF_MESH_PASSWORD: home["mesh_password"],
                    CONF_DEVICES: home["devices"],
                },
            )

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "home_name": self._chosen,
                "device_count": str(len(home["devices"])),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> CyncBleOptionsFlow:
        return CyncBleOptionsFlow()


class CyncBleOptionsFlow(config_entries.OptionsFlow):
    """Pick a Bluetooth adapter to drive directly, or none.

    Separate from setup on purpose: the account login is a one-off, while
    which radio to use is something people change when they add a dongle -
    and it is the option most likely to need undoing.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        choices = await async_list_adapters(self.hass)
        errors: dict[str, str] = {}

        if user_input is not None:
            chosen = user_input.get(CONF_DIRECT_ADAPTER, ADAPTER_NONE)
            if is_selectable(choices, chosen):
                return self.async_create_entry(data={CONF_DIRECT_ADAPTER: chosen})
            # Taking this adapter would pull the radio out from under Home
            # Assistant's own scanner and every other integration using it.
            errors["base"] = "adapter_in_use"

        current = self.config_entry.options.get(CONF_DIRECT_ADAPTER, ADAPTER_NONE)
        options = selection_options(choices)
        if current not in options:
            current = ADAPTER_NONE

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DIRECT_ADAPTER, default=current): vol.In(options),
                }
            ),
            errors=errors,
            description_placeholders={
                "free": str(sum(1 for c in choices if c.selectable)),
                "total": str(len(choices)),
            },
        )
