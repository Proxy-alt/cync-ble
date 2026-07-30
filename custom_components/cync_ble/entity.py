"""Shared base entity for Cync Bluetooth platforms."""

from __future__ import annotations

from typing import Any

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
        """Whether the last harvest worked - not whether a link is open
        right now.

        This transport is connect-on-demand by design (the vendor's own
        client disconnects on idle too), and the harvest deliberately closes
        its link, so "no connection at this instant" is the normal resting
        state and says nothing about reachability.
        """
        return super().available

    @property
    def reported_brightness(self) -> int | None:
        """What the mesh last said about this device (0-100), or a command
        issued since then, or None if nothing is known yet.

        None is genuinely "no information" - a device that has never appeared
        in a harvest and has never been commanded. Subclasses fall back to
        their restored state there rather than inventing one.
        """
        return self.coordinator.reported_brightness(self.target)

    @property
    def assumed_state(self) -> bool:
        """False once the mesh has actually reported this device.

        Harvested state can be up to a refresh interval old, which is what
        polling means - but it is a real report rather than an assumption,
        and Home Assistant's `assumed_state` asks which of the two it is.
        """
        return self.coordinator.device_states.get(self.target) is None
