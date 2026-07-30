"""Connection-lifecycle coordinator for one Cync Bluetooth mesh.

Holds exactly one `cync_lan.ble_mesh.BleMeshSession` for the whole mesh - see
ARCHITECTURE.md's "one session, not one per device": mesh relay means any
single authenticated connection reaches every device, so this integration
never opens more than one link at a time.

There is nothing to poll here in the usual DataUpdateCoordinator sense. The
only inbound status opcode (0xDC) arrives exclusively through the
notification path, and subscribing to it usually kills the connection on a
local BlueZ adapter (confirmed on hardware, see ARCHITECTURE.md). So this
coordinator's periodic cycle is primarily a connection-health check, not a
state fetch: absent a working subscription, entities report the last state
they themselves commanded rather than anything read back from the device
(`assumed_state` in HA terms).

That said, "usually" is doing real work in that sentence. Static analysis of
the real iOS app's binary found it calls the same standards-compliant
subscribe API and ships dedicated retry-counter/retry-timer machinery for it
- strong evidence the call is merely unreliable, not categorically refused
for every client. So every fresh connection opportunistically tries it too
(rate-limited - see SUBSCRIBE_RETRY_INTERVAL_SECONDS in const.py, since a
refusal takes the whole connection down and retrying too eagerly would just
add connection churn across the whole mesh). If it ever holds, this session
switches from assumed to genuinely pushed state until the link drops.

The BLE client is never constructed directly - always obtained through Home
Assistant's own Bluetooth stack via `bleak_retry_connector`, which is what
lets an ESPHome Bluetooth proxy stand in for a local adapter transparently.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from cync_lan.ble_mesh import BleMeshError, BleMeshSession, DeviceStatus
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_DEVICES,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    DOMAIN,
    SUBSCRIBE_RETRY_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class CyncBleCoordinator(DataUpdateCoordinator[dict[int, DeviceStatus]]):
    """Owns the one BLE session for a mesh, and everyone's commands go
    through it."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} ({entry.title})",
            update_interval=timedelta(seconds=DEFAULT_REFRESH_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.mesh_name: str = entry.data[CONF_MESH_NAME]
        self.mesh_password: str = entry.data[CONF_MESH_PASSWORD]
        self.devices: list[dict[str, Any]] = entry.data[CONF_DEVICES]

        self._client: BleakClientWithServiceCache | None = None
        self.session: BleMeshSession | None = None
        # Tried first on every (re)connect, since it's the anchor most
        # recently known to work - avoids scanning all ~40 mesh nodes on
        # every reconnect when the same one keeps working fine.
        self._last_good_mac: str | None = None

        # Opportunistic push - see module docstring. push_active is True only
        # while a subscription is actually live on the current connection;
        # device_states holds the most recent report per device id while it
        # is, and is left in place (stale but not wrong) after it drops, for
        # entities to fall back on until state goes assumed again.
        self.push_active: bool = False
        self.device_states: dict[int, DeviceStatus] = {}
        self._next_subscribe_attempt: float = 0.0

    @property
    def mesh_available(self) -> bool:
        return self.session is not None and self.session.authenticated

    def _candidate_macs(self) -> list[str]:
        macs = [d["mac"] for d in self.devices if d.get("mac")]
        if self._last_good_mac and self._last_good_mac in macs:
            macs.remove(self._last_good_mac)
            macs.insert(0, self._last_good_mac)
        return macs

    def _on_disconnect(self, _client: BleakClientWithServiceCache) -> None:
        _LOGGER.debug("%s: mesh link disconnected", self.mesh_name)
        self._client = None
        self.session = None
        # Deliberately does NOT touch _next_subscribe_attempt: an ordinary
        # disconnect (out of range, idle timeout, HA restart) after a session
        # that WAS pushing state is worth retrying immediately on reconnect,
        # not backed off - only an explicit refusal inside
        # _maybe_try_subscribe earns the backoff.
        self.push_active = False

    def _on_mesh_status(self, statuses: list[DeviceStatus]) -> None:
        for status in statuses:
            self.device_states[status.device_id] = status
        self.async_set_updated_data(self.device_states)

    async def _maybe_try_subscribe(self, session: BleMeshSession) -> BleMeshSession:
        """Opportunistically try upgrading a freshly (re)connected session
        to live push updates - see module docstring for why this is
        attempted at all, and why it's rate-limited.

        A refused subscribe takes the WHOLE connection down, not just the
        notification path (confirmed on hardware - see
        `cync_lan.ble_mesh.BleMeshSession.subscribe`'s own docstring). So a
        failure here means reconnecting again, send-only, before returning
        anything usable to the caller.
        """
        if self.push_active or time.monotonic() < self._next_subscribe_attempt:
            return session
        try:
            await session.subscribe(self._on_mesh_status)
        except BleMeshError as exc:
            _LOGGER.debug(
                "%s: opportunistic subscribe was refused (%s); staying on "
                "send-only polling, retrying in %ds",
                self.mesh_name,
                exc,
                SUBSCRIBE_RETRY_INTERVAL_SECONDS,
            )
            self._next_subscribe_attempt = (
                time.monotonic() + SUBSCRIBE_RETRY_INTERVAL_SECONDS
            )
            self.session = None
            self._client = None
            return await self._async_ensure_connected()
        else:
            self.push_active = True
            _LOGGER.info(
                "%s: mesh accepted a live status subscription - switching to "
                "pushed state for this session",
                self.mesh_name,
            )
            return session

    async def _connect_to(self, mac: str) -> BleMeshSession | None:
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, mac, connectable=True
        )
        if ble_device is None:
            return None
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                f"{DOMAIN}-{mac}",
                disconnected_callback=self._on_disconnect,
            )
        except Exception as exc:
            _LOGGER.debug("%s: could not connect to %s: %s", self.mesh_name, mac, exc)
            return None

        session = BleMeshSession(client, mac, self.mesh_name, self.mesh_password)
        try:
            verified = await session.authenticate()
        except BleMeshError as exc:
            _LOGGER.warning(
                "%s: pairing handshake failed via %s: %s", self.mesh_name, mac, exc
            )
            await client.disconnect()
            return None
        if not verified:
            _LOGGER.warning(
                "%s: mesh mutual auth failed via %s - check the account's mesh "
                "credentials",
                self.mesh_name,
                mac,
            )
            await client.disconnect()
            return None

        self._client = client
        self._last_good_mac = mac
        return session

    async def _async_ensure_connected(self) -> BleMeshSession:
        """Return a live, authenticated session, (re)connecting if needed.

        Tries the last-known-good node first, then falls through the rest
        of the mesh's own device list - any one of them reaches the whole
        mesh once authenticated, so the first one Home Assistant can
        currently see is as good as any other.
        """
        if self.session is not None and self.session.authenticated:
            return self.session

        for mac in self._candidate_macs():
            session = await self._connect_to(mac)
            if session is not None:
                self.session = session
                return await self._maybe_try_subscribe(session)

        raise UpdateFailed(
            f"Could not reach any device on mesh {self.mesh_name!r} - none of "
            f"its {len(self.devices)} known nodes are currently visible to "
            "Home Assistant's Bluetooth stack"
        )

    async def _async_update_data(self) -> dict[int, DeviceStatus]:
        """Connection-health check, not a state fetch - see module
        docstring. Raising UpdateFailed here is what makes
        `coordinator.last_update_success` (and therefore every entity's
        availability) reflect real mesh reachability.

        Returns the existing device_states dict unchanged (rather than
        None) so a periodic tick with nothing new to report doesn't wipe
        out whatever an active push subscription has already delivered.
        """
        await self._async_ensure_connected()
        return self.device_states

    async def _with_retry(self, call: Any) -> None:
        """Run one `session -> coroutine` command, reconnecting first if
        the link isn't up, and once more if it dies mid-call.

        Commands are user-initiated (a switch/light entity call) and
        shouldn't wait for the next scheduled refresh cycle to get a
        connection - this reconnects immediately on demand instead.
        """
        session = await self._async_ensure_connected()
        try:
            await call(session)
        except Exception as exc:
            # bleak-level failure from a link that died between the health
            # check above and this write. One retry against a fresh
            # connection; a second failure is a real problem, not a
            # transient race, and should surface.
            _LOGGER.debug(
                "%s: command failed (%s), reconnecting and retrying once",
                self.mesh_name,
                exc,
            )
            self.session = None
            session = await self._async_ensure_connected()
            await call(session)

        # A command just round-tripped over a live connection - that's a
        # stronger, more current reachability signal than the periodic
        # health check, and entities' `available` reads
        # `last_update_success`. Without this, a switch that just
        # successfully reconnected and sent a command could still report
        # unavailable until the next scheduled refresh, up to
        # DEFAULT_REFRESH_INTERVAL_SECONDS later. Passing the existing dict
        # (not None) so this doesn't wipe out any pushed state already
        # collected on this session.
        self.async_set_updated_data(self.device_states)

    async def async_send(self, target: int, opcode: int, data: bytes) -> None:
        await self._with_retry(lambda session: session.send(target, opcode, data))

    async def async_set_power(self, target: int, on: bool) -> None:
        await self._with_retry(lambda session: session.set_power(target, on))

    async def async_set_brightness(
        self, target: int, brightness: int, *, is_sol_lamp: bool = False
    ) -> None:
        await self._with_retry(
            lambda session: session.set_brightness(
                target, brightness, is_sol_lamp=is_sol_lamp
            )
        )

    async def async_shutdown_session(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                _LOGGER.debug(
                    "%s: error disconnecting on unload", self.mesh_name, exc_info=True
                )
        self._client = None
        self.session = None
