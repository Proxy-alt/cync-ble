"""The Cync Bluetooth integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, MANUFACTURER
from .coordinator import CyncBleCoordinator

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.LIGHT]

type CyncBleConfigEntry = ConfigEntry[CyncBleCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: CyncBleConfigEntry) -> bool:
    coordinator = CyncBleCoordinator(hass, entry)
    # Deliberately async_refresh, not async_config_entry_first_refresh.
    #
    # The device list comes from the config entry, not from the mesh, so
    # entities are useful whether or not state can be read - commands connect
    # on demand and work regardless. Failing setup because the first harvest
    # did not land would leave a working integration with no entities at all,
    # which is strictly worse than entities that report the last state
    # commanded. See the coordinator's fallback handling.
    await coordinator.async_refresh()
    entry.runtime_data = coordinator

    # The mesh itself, as a device every light and switch hangs off. Every
    # entity already names this as its `via_device`, and Home Assistant warns
    # (and from 2025.12 will refuse) when that points at a device nothing ever
    # registered - which is exactly what happened on the first real install.
    #
    # It is also the honest shape: there is one BLE session for the whole mesh
    # rather than a connection per device, so a single parent is what the
    # device tree actually looks like.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        name=entry.title,
        model="Cync Bluetooth mesh",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CyncBleConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown_session()
    return unloaded
