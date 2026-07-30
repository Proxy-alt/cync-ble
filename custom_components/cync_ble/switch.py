"""Switch platform for Cync Bluetooth.

Covers binary toggle switches and plugs/outlets, per `classify.is_switch`.
Dimmable switches and fan controllers are deliberately excluded - see
classify.py's module docstring: dimmable ones belong on light.py, and fan
controllers aren't represented by any platform yet (ARCHITECTURE.md's build
order covers switch and light only, to begin with).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import classify
from .entity import CyncBleEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities = [
        CyncBleSwitch(coordinator, device)
        for device in coordinator.devices
        if classify.is_switch(device["type"])
    ]
    async_add_entities(entities)


class CyncBleSwitch(CyncBleEntity, SwitchEntity, RestoreEntity):
    """A plain on/off Cync Bluetooth switch or plug.

    See entity.py: state is assumed unless a live subscription is actively
    reporting this device, in which case `is_on` reports that instead.
    `_attr_is_on` is the assumed fallback - restored from this entity's own
    last known HA state on startup, then only ever changed by a command
    this entity itself sends.
    """

    _attr_name = None

    def __init__(self, coordinator: Any, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device)
        if classify.is_plug(self.dev_type):
            self._attr_device_class = SwitchDeviceClass.OUTLET
        self._attr_is_on: bool | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == "on"

    @property
    def is_on(self) -> bool | None:
        status = self._pushed_status
        if status is not None:
            # The mesh status report has no separate on/off field - a
            # binary switch reporting through the same 0xDC slot format as
            # dimmable devices reports its power state via brightness
            # (0 = off), matching cync_lan's own TCP-side convention.
            return status.brightness > 0
        return self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_power(self.target, True)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_power(self.target, False)
        self._attr_is_on = False
        self.async_write_ha_state()
