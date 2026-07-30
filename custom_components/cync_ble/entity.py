"""Shared base entity for Cync Bluetooth platforms."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import classify
from .const import DOMAIN, MANUFACTURER
from .coordinator import CyncBleCoordinator


class CyncBleEntity(CoordinatorEntity[CyncBleCoordinator]):
    """Common plumbing for every Cync Bluetooth entity.

    `_attr_assumed_state = True` throughout this integration: nothing on
    this transport can be read back on demand (see coordinator.py's module
    docstring), so every entity reports the last state it commanded rather
    than anything confirmed by the device. Availability instead reflects
    whether the mesh session itself is reachable
    (`coordinator.last_update_success`), which is the one thing this
    integration can actually confirm.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_assumed_state = True

    def __init__(self, coordinator: CyncBleCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._device = device
        self.target: int = device["id"]
        self.dev_type: int = device["type"]
        unique_id = f"{coordinator.entry.entry_id}_{self.target}"
        self._attr_unique_id = unique_id
        connections = set()
        mac = device.get("mac")
        if mac:
            connections.add(("bluetooth", mac.casefold()))
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unique_id)},
            connections=connections,
            manufacturer=MANUFACTURER,
            name=device["name"],
            model=classify.model_name(self.dev_type) or "Unknown",
            via_device=(DOMAIN, coordinator.entry.entry_id),
        )
