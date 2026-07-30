"""The Cync Bluetooth integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import CyncBleCoordinator

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.LIGHT]

type CyncBleConfigEntry = ConfigEntry[CyncBleCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: CyncBleConfigEntry) -> bool:
    coordinator = CyncBleCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CyncBleConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown_session()
    return unloaded
