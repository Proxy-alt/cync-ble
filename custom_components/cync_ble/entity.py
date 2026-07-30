"""Shared base entity for Cync Bluetooth platforms."""

from __future__ import annotations

from typing import Any

from cync_lan.ble_mesh import DeviceStatus
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import classify
from .address import to_colon_form
from .const import DOMAIN, MANUFACTURER
from .coordinator import CyncBleCoordinator


class CyncBleEntity(CoordinatorEntity[CyncBleCoordinator]):
    """Common plumbing for every Cync Bluetooth entity.

    State is assumed by default: nothing on this transport can normally be
    read back (see coordinator.py's module docstring), so an entity reports
    the last state it commanded rather than anything confirmed by the
    device. The exception is while the coordinator's opportunistic
    subscribe has actually taken - see `_pushed_status` below - in which
    case this specific entity's state is no longer assumed for as long as
    that session lasts. Availability reflects whether the mesh session
    itself is reachable (`coordinator.last_update_success`), the one thing
    this integration can always confirm regardless of push.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

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
            # A real, punctuated address - the cloud's bare hex was being
            # registered verbatim, which matches nothing else in the device
            # registry. Byte order is left as stored: unlike the connection
            # path there is nothing to resolve it against here, and a device
            # identity that flips depending on what the radio saw at setup
            # time would be worse than one that is merely occasionally
            # reversed.
            connections.add((dr.CONNECTION_BLUETOOTH, to_colon_form(mac)))
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unique_id)},
            connections=connections,
            manufacturer=MANUFACTURER,
            name=device["name"],
            model=classify.model_name(self.dev_type) or "Unknown",
            via_device=(DOMAIN, coordinator.entry.entry_id),
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.mesh_available

    @property
    def _pushed_status(self) -> DeviceStatus | None:
        """This device's most recent report from a live subscription, or
        None if no subscription is currently active (the ordinary case) or
        one is active but hasn't reported this specific device yet.

        `device_states` is deliberately left in place after a push session
        ends rather than cleared - see coordinator.py - but this only reads
        it while `push_active` is True, so a stale entry from a since-ended
        session doesn't get reported as current.
        """
        if not self.coordinator.push_active:
            return None
        return self.coordinator.device_states.get(self.target)

    @property
    def assumed_state(self) -> bool:
        return self._pushed_status is None
