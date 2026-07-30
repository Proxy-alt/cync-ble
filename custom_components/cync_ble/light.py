"""Light platform for Cync Bluetooth.

Brightness only, for now. Colour temperature and RGB are NOT confirmed over
this transport (see ARCHITECTURE.md's protocol status table) - they ride the
same opcode family as brightness, which is a reasonable basis for confidence,
but nobody has moved either over BLE. Declaring support for them here would
be exactly the "plausible-looking wrong opcode" this project's whole
discipline exists to avoid, so `supported_color_modes` only ever advertises
BRIGHTNESS or ONOFF until that's tested and confirmed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
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
        CyncBleLight(coordinator, device)
        for device in coordinator.devices
        if classify.is_light(device["type"])
    ]
    async_add_entities(entities)


class CyncBleLight(CyncBleEntity, LightEntity, RestoreEntity):
    """A Cync Bluetooth light - on/off, and brightness where the device
    supports dimming.

    State comes from the mesh's own periodic report where there is one (see
    coordinator.py's harvest). `_attr_is_on`/`_attr_brightness` are only the
    fallback for a device that has never been harvested - restored from this
    entity's last known Home Assistant state on startup.
    """

    _attr_name = None

    def __init__(self, coordinator: Any, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device)
        self._is_sol_lamp = classify.is_sol_lamp(self.dev_type)
        if classify.is_dimmable(self.dev_type):
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF
        self._attr_is_on: bool | None = None
        self._attr_brightness: int | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == "on"
            restored_brightness = last_state.attributes.get("brightness")
            if isinstance(restored_brightness, (int, float)):
                self._attr_brightness = int(restored_brightness)

    @property
    def is_on(self) -> bool | None:
        brightness = self.reported_brightness
        if brightness is not None:
            return brightness > 0
        return self._attr_is_on

    @property
    def brightness(self) -> int | None:
        brightness = self.reported_brightness
        if brightness is not None:
            # Device's 0-100 percentage to HA's 0-255 scale.
            return round(brightness / 100 * 255)
        return self._attr_brightness

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_power(self.target, True)
        self._attr_is_on = True
        brightness = kwargs.get("brightness")
        dimmable = ColorMode.BRIGHTNESS in self.supported_color_modes
        if brightness is not None and dimmable:
            # HA's 0-255 scale to the device's 0-100 percentage.
            device_brightness = round(brightness / 255 * 100)
            await self.coordinator.async_set_brightness(
                self.target, device_brightness, is_sol_lamp=self._is_sol_lamp
            )
            self._attr_brightness = brightness
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_power(self.target, False)
        self._attr_is_on = False
        self.async_write_ha_state()
