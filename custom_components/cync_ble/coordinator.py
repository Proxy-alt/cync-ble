"""Connection-lifecycle coordinator for one Cync Bluetooth mesh.

Holds exactly one `cync_lan.ble_mesh.BleMeshSession` for the whole mesh - see
ARCHITECTURE.md's "one session, not one per device": mesh relay means any
single authenticated connection reaches every device, so this integration
never opens more than one link at a time.

Each refresh takes a **harvest**: one deliberately sacrificial connection
that subscribes, collects the status sweep the mesh emits in response, and
loses the link ~30s later when the firmware refuses the CCCD write. See
HARVEST_WINDOW_SECONDS in const.py for why that refusal is a price worth
paying rather than a bug to route around.

So this really is `local_polling`: entities report state the mesh itself
reported, not merely what was last commanded. Commands are optimistic in the
gap between sending and the next harvest, and the harvest is what settles it -
which also means a physically-operated switch is picked up, eventually.

Commands and harvests never share a link. A connection that has subscribed is
a dead link walking, so the command session is kept strictly separate and is
never the one that harvests.

The BLE client is never constructed directly - always obtained through Home
Assistant's own Bluetooth stack via `bleak_retry_connector`, which is what
lets an ESPHome Bluetooth proxy stand in for a local adapter transparently.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import timedelta
from typing import Any

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from cync_lan.ble_mesh import (
    NOTIFICATION_CHAR,
    BleMeshError,
    BleMeshSession,
    DeviceStatus,
    decrypt_packet,
    mac_to_address,
    parse_status,
)
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import address
from .const import (
    CONF_DEVICES,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    DOMAIN,
    HARVEST_DEADLINE_SECONDS,
    HARVEST_FAILURE_LIMIT,
    HARVEST_RETRY_AFTER_SECONDS,
    HARVEST_WINDOW_SECONDS,
    MAX_CONNECT_ATTEMPTS,
    NODE_REST_SECONDS,
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
        # Nodes that refused a connection recently, by mac. A node that just
        # failed is the worst thing to try next: the adapter has a small,
        # shared pool of connection slots, and the common failure here is
        # "out of connection slots" rather than anything about the node.
        self._recent_failures: dict[str, float] = {}
        # Nodes that have completed a mesh handshake at least once. Worth
        # remembering separately from "did not fail recently": on this mesh
        # most nodes never accept a connection at all, so having proven one
        # is a much stronger signal than not having tried it.
        self._known_good: set[str] = set()
        self._last_used: dict[str, float] = {}

        # What the mesh last told us about itself, by device id. Populated by
        # _async_harvest and deliberately kept across reconnects: a stale
        # reading is still better than none, and the alternative is entities
        # blanking every time the link cycles.
        self.device_states: dict[int, DeviceStatus] = {}
        self._last_harvest: float = 0.0
        # Consecutive failed harvests, and when to try again after giving up.
        # See HARVEST_FAILURE_LIMIT: falling back to command-only is a
        # deliberate, reversible degradation, not an error state.
        self._harvest_failures: int = 0
        self._harvest_paused_until: float = 0.0
        # Commands write their intent here so an entity does not visibly snap
        # back to the pre-command value while waiting for the next harvest.
        # Cleared per device once a harvest newer than the command lands.
        self.optimistic: dict[int, tuple[float, int]] = {}

    @property
    def mesh_available(self) -> bool:
        return self.session is not None and self.session.authenticated

    def _candidate_macs(self) -> list[str]:
        """Nodes to try, best first.

        Ordering matters far more here than it looks. The stored device list
        arrives in the cloud's own order, which on a real account is dominated
        by one hardware family (`F4:BC:DA`) whose members consistently refuse
        connections with "the adapter is out of connection slots", while the
        handful of nodes that reliably answer sit near the end. Walking the
        list in order meant grinding through refusals six at a time and
        reaching a working node only by luck - three failed cycles before the
        first success, and a repeat of that grind whenever the good node was
        briefly unavailable.

        So proven nodes lead, **least recently used first**. That rotation is
        deliberate: the harvest ends by having its link killed, and the node
        that just happened to is the least likely to accept another connection
        immediately. Rotating only among nodes already known to work is what
        makes this safe - an earlier attempt that rotated across the whole
        list simply spread the failures around and exhausted the adapter.
        """
        now = time.monotonic()
        known_good, resting, untried, failed = [], [], [], []
        for device in self.devices:
            mac = device.get("mac")
            if not mac:
                continue
            used_at = self._last_used.get(mac, 0.0)
            if mac in self._known_good:
                # A node that served the last harvest had its link killed by
                # it, and reliably refuses the next one - observed as a steady
                # 72s cycle where the single proven node failed first every
                # time. Rest it, and let the walk find a second proven node so
                # there is something to rotate between.
                if now - used_at < NODE_REST_SECONDS:
                    resting.append(mac)
                else:
                    known_good.append(mac)
            elif mac in self._recent_failures:
                failed.append(mac)
            else:
                untried.append(mac)
        # Proven and rested first, then nodes never tried (which is how a
        # second proven node gets discovered), then rested-but-proven, then
        # known refusals oldest-first.
        known_good.sort(key=lambda mac: self._last_used.get(mac, 0.0))
        # Strongest signal first among nodes we know nothing about. This is
        # what stops a walk burning its budget on nodes Home Assistant lists
        # as connectable but has no signal from (RSSI -127), which is exactly
        # what made `light.turn_on` fail after 90s while a perfectly
        # reachable node sat further down the list.
        untried.sort(key=self._signal, reverse=True)
        failed.sort(key=lambda mac: self._recent_failures.get(mac, 0.0))
        return known_good + untried + resting + failed

    def _signal(self, mac: str) -> int:
        """Signal strength for a node, or a floor if there is none.

        Home Assistant reports -127 for a device it has in connectable
        history but has no real signal from, and those are precisely the
        nodes that refuse connections here - the ones that broke
        `light.turn_on` were all -127 while the node a direct client
        connects to every time had real signal. Sorting by this is what
        keeps a cycle from spending its whole budget on unreachable nodes.
        """
        best = -127
        for candidate in address.candidates(mac):
            try:
                info = bluetooth.async_last_service_info(
                    self.hass, candidate, connectable=True
                )
            except Exception:  # never let ordering fail the whole cycle
                continue
            if info is not None and info.rssi is not None:
                best = max(best, info.rssi)
        return best

    def _resolve(self, mac: str) -> tuple[Any, str] | None:
        """Find the real Bluetooth address behind one stored MAC.

        The cloud stores addresses without separators and, for a minority
        of devices, byte-reversed - see address.py. Which orientation is
        correct is decided here by asking Home Assistant's Bluetooth stack
        which one it can actually see, rather than by guessing from the
        stored form.

        The resolved address is returned alongside the device because it
        is also what the session key is derived from
        (`ble_mesh.mac_to_address`). Connecting with one orientation and
        encrypting with the other would authenticate against nothing, and
        would do it silently.
        """
        for candidate in address.candidates(mac):
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, candidate, connectable=True
            )
            if ble_device is not None:
                return ble_device, candidate
        return None

    def _on_disconnect(self, _client: BleakClientWithServiceCache) -> None:
        _LOGGER.debug("%s: mesh link disconnected", self.mesh_name)
        self._client = None
        self.session = None

    def _on_mesh_status(self, statuses: list[DeviceStatus]) -> None:
        """Record one notification's worth of the sweep.

        Deliberately does nothing but store. This runs inside a bleak
        notification callback during a refresh that is already in flight, and
        bleak swallows whatever a callback raises - so anything more ambitious
        here (notifying listeners mid-refresh, say) fails silently and takes
        the rest of the sweep with it. Confirmed the hard way: a harvest that
        called async_set_updated_data from here collected zero states while a
        bare probe against the same mesh collected 38. The refresh returns
        device_states when it finishes, which is what updates entities.
        """
        try:
            for status in statuses:
                self.device_states[status.device_id] = status
        except Exception:  # a bad packet must not end the sweep
            _LOGGER.debug("%s: bad status record", self.mesh_name, exc_info=True)

    async def _async_harvest(self) -> None:
        """One sacrificial connection that collects the mesh's own state.

        Subscribing is what kills the link - measured as invariant on this
        firmware (14 attempts, always GATT UNLIKELY_ERROR, never accepted).
        This does it deliberately anyway, because the attempt is what makes
        the mesh report: a full sweep of 34-38 devices arrives in the seconds
        before the rejection lands.

        Deliberately its own connection, torn down afterwards. A link that has
        subscribed is a dead link walking, so the command session must never
        be the one that harvests.
        """
        await self._async_close_link()

        candidates = [m for m in self._candidate_macs() if self._resolve(m)]
        _LOGGER.debug(
            "%s: %d node(s) visible, %d proven; trying up to %d",
            self.mesh_name,
            len(candidates),
            len(self._known_good),
            MAX_CONNECT_ATTEMPTS,
        )
        # A deadline checked BETWEEN attempts, never a timeout wrapped around
        # one. Cancelling an in-flight establish_connection leaks the
        # connection slot it reserved, and a leaked pool never recovers - see
        # _connect_to. So a cycle stops starting new attempts once it is out
        # of time, and always lets the one it started finish.
        deadline = time.monotonic() + HARVEST_DEADLINE_SECONDS
        session = None
        for mac in candidates[:MAX_CONNECT_ATTEMPTS]:
            if time.monotonic() > deadline:
                _LOGGER.debug(
                    "%s: out of time for this cycle, will resume next refresh",
                    self.mesh_name,
                )
                break
            session = await self._connect_to(mac)
            if session is not None:
                break
        if session is None:
            _LOGGER.debug("%s: no node reachable for a harvest", self.mesh_name)
            return

        before = len(self.device_states)
        # Deliberately NOT BleMeshSession.subscribe(). That helper returns
        # False (rather than raising) if the vendor enable-write fails, which
        # is indistinguishable here from "subscribed and heard nothing" - and
        # a harvest that silently collects zero is exactly the failure this
        # had. Doing the two steps directly means each one's outcome is
        # visible, and it matches the probe that reliably collects 34-38
        # devices against this mesh.
        address = mac_to_address(session._mac)
        key = session._session_key

        def _on_notification(_sender: Any, data: bytearray) -> None:
            try:
                clear = decrypt_packet(key, address, bytearray(data))
                self._on_mesh_status(parse_status(bytes(clear)))
            except Exception:  # one undecodable packet must not end the sweep
                _LOGGER.debug(
                    "%s: undecodable notification", self.mesh_name, exc_info=True
                )

        try:
            client = self._client
            # The vendor's own "start reporting" command - a plain value
            # write, nothing to do with the CCCD.
            await client.write_gatt_char(
                NOTIFICATION_CHAR, bytes([0x01]), response=True
            )
            # This is the call that will be refused ~30s from now, taking the
            # link with it. Bounded rather than awaited: the sweep arrives
            # long before the refusal, and waiting for it only holds a radio
            # that other integrations need.
            # Timing out here is expected, and is the point: the callback
            # was registered before the write that will be refused, so the
            # sweep has been arriving throughout.
            #
            # Cancelling IS safe here, unlike around establish_connection: the
            # connection already exists and the `finally` below closes it, so
            # there is no half-allocated slot to strand. The rule is about
            # cancelling connection setup, not cancelling anything at all.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    client.start_notify(NOTIFICATION_CHAR, _on_notification),
                    timeout=HARVEST_WINDOW_SECONDS,
                )
        except Exception as exc:  # a harvest must never be fatal
            _LOGGER.debug("%s: harvest ended early: %s", self.mesh_name, exc)
        finally:
            await self._async_close_link()

        self._last_harvest = time.monotonic()
        _LOGGER.debug(
            "%s: harvest collected %d device states (%d new)",
            self.mesh_name,
            len(self.device_states),
            len(self.device_states) - before,
        )

    async def _async_close_link(self) -> None:
        """Drop whatever link is currently open. One at a time - this radio is
        shared with every other Bluetooth integration on the box."""
        client, self._client, self.session = self._client, None, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                _LOGGER.debug("%s: error closing link", self.mesh_name, exc_info=True)

    async def _connect_to(self, mac: str) -> BleMeshSession | None:
        resolved = self._resolve(mac)
        if resolved is None:
            return None
        ble_device, addr = resolved
        try:
            # NOT wrapped in a cancelling timeout, however tempting.
            #
            # A cancelled establish_connection does not release the
            # connection slot it reserved from Home Assistant's Bluetooth
            # manager. Ten cancelled attempts in one cycle exhausted the pool
            # outright: BlueZ showed zero open connections while every
            # subsequent attempt was refused with "the adapter is out of
            # connection slots", and a raw BleakClient - which bypasses that
            # accounting - connected to the same mesh instantly throughout.
            # It never recovered on its own, and survived restarts because
            # the first cycle after boot re-leaked the pool immediately.
            #
            # So each attempt is allowed to finish. max_attempts keeps it to
            # one connection try; the slow part when no slot is free is the
            # waiting, and cancelling that wait is precisely what breaks it.
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                f"{DOMAIN}-{addr}",
                disconnected_callback=self._on_disconnect,
                max_attempts=1,
            )
        except Exception as exc:
            self._recent_failures[mac] = time.monotonic()
            self._last_used[mac] = time.monotonic()
            _LOGGER.debug("%s: could not connect to %s: %s", self.mesh_name, addr, exc)
            return None

        # `addr`, not `mac` - the session key is derived from the address, so
        # it has to be the orientation the device actually answers on.
        session = BleMeshSession(client, addr, self.mesh_name, self.mesh_password)
        try:
            verified = await session.authenticate()
        except BleMeshError as exc:
            _LOGGER.warning(
                "%s: pairing handshake failed via %s: %s", self.mesh_name, addr, exc
            )
            await client.disconnect()
            return None
        if not verified:
            _LOGGER.warning(
                "%s: mesh mutual auth failed via %s - check the account's mesh "
                "credentials",
                self.mesh_name,
                addr,
            )
            await client.disconnect()
            return None

        self._client = client
        self._known_good.add(mac)
        self._last_used[mac] = time.monotonic()
        self._recent_failures.pop(mac, None)
        return session

    async def _async_ensure_connected(self) -> BleMeshSession:
        """Return a live, authenticated session, (re)connecting if needed.

        Tries the last-known-good node first, then other nodes Home
        Assistant can currently see - any one of them reaches the whole
        mesh once authenticated, so which one hardly matters.

        Two deliberate bounds, both learned from a real install where this
        blocked Home Assistant's startup for four and a half minutes and
        starved a lock integration sharing the adapter:

        - only nodes the Bluetooth stack can actually see are attempted.
          Resolving is an in-memory lookup; connecting is not, and on a
          46-node mesh most of the list is asleep or out of range at any
          moment.
        - at most `MAX_CONNECT_ATTEMPTS` connections per pass. Failing
          quickly is better than eventually succeeding, because a failure
          here is retried on the coordinator's own schedule (and, at
          startup, as a normal `ConfigEntryNotReady` retry) instead of
          holding a shared radio.
        """
        if self.session is not None and self.session.authenticated:
            return self.session

        visible = [mac for mac in self._candidate_macs() if self._resolve(mac)]
        for mac in visible[:MAX_CONNECT_ATTEMPTS]:
            session = await self._connect_to(mac)
            if session is not None:
                self.session = session
                return session

        if not visible:
            raise UpdateFailed(
                f"Could not reach any device on mesh {self.mesh_name!r} - none "
                f"of its {len(self.devices)} known nodes are currently visible "
                "to Home Assistant's Bluetooth stack"
            )
        raise UpdateFailed(
            f"Could not connect to mesh {self.mesh_name!r} - {len(visible)} of "
            f"its {len(self.devices)} nodes are visible but the first "
            f"{min(len(visible), MAX_CONNECT_ATTEMPTS)} would not accept a "
            "connection"
        )

    @property
    def state_polling_active(self) -> bool:
        """Whether we are still trying to read state from the mesh."""
        return time.monotonic() >= self._harvest_paused_until

    async def _async_update_data(self) -> dict[int, DeviceStatus]:
        """Harvest the mesh's state, or stay out of the way if that has
        proven not to work here.

        Deliberately does NOT raise when harvesting is paused. A paused
        integration is still fully usable - commands connect on demand and
        work - so failing the refresh would mark every entity unavailable
        for a capability they never lose.
        """
        if not self.state_polling_active:
            # Paused, but not idle. Hold a link open and check it is still
            # alive, which is exactly what this coordinator did before
            # harvesting existed.
            #
            # This is deliberately NOT "connect only when a command arrives".
            # Establishing a connection is the unreliable operation on this
            # transport - the sending itself has never failed - so a
            # reconnect per command would put the fragile step in front of
            # every user action. One persistent link that never subscribes
            # stays healthy indefinitely (confirmed: "never subscribe ->
            # sending works"), and costs one of the adapter's five slots.
            await self._async_ensure_connected()
            return self.device_states

        await self._async_harvest()

        if self.device_states:
            if self._harvest_failures:
                _LOGGER.info(
                    "%s: state polling recovered after %d failed attempt(s)",
                    self.mesh_name,
                    self._harvest_failures,
                )
            self._harvest_failures = 0
            return self.device_states

        self._harvest_failures += 1
        if self._harvest_failures < HARVEST_FAILURE_LIMIT:
            raise UpdateFailed(
                f"Mesh {self.mesh_name!r} reported nothing - none of its "
                f"{len(self.devices)} nodes could be reached"
            )

        # Give up on state, keep the integration working.
        self._harvest_paused_until = time.monotonic() + HARVEST_RETRY_AFTER_SECONDS
        _LOGGER.warning(
            "%s: could not read state from the mesh %d times in a row, so "
            "state polling is pausing for %d minutes. Switches and lights keep "
            "working - they will report the last state commanded rather than "
            "the mesh's own. This usually means the Bluetooth adapter cannot "
            "sustain connections to the mesh; an ESPHome Bluetooth proxy is "
            "the usual fix.",
            self.mesh_name,
            self._harvest_failures,
            HARVEST_RETRY_AFTER_SECONDS // 60,
        )
        return self.device_states

    def _record_optimistic(self, target: int, brightness: int) -> None:
        """Remember what a command intended, so the entity does not visibly
        snap back to the old value while waiting for the next harvest."""
        self.optimistic[target] = (time.monotonic(), brightness)

    def reported_brightness(self, target: int) -> int | None:
        """Best current belief about one device, or None if nothing is known.

        A command issued since the last harvest wins - it is newer evidence
        than the sweep, and the mesh takes a few seconds to catch up (a
        harvest taken immediately after a command has been observed still
        reporting the previous value). Once a harvest lands after the
        command, the mesh's own account takes over again.
        """
        pending = self.optimistic.get(target)
        if pending is not None:
            issued_at, brightness = pending
            if issued_at > self._last_harvest:
                return brightness
            del self.optimistic[target]
        status = self.device_states.get(target)
        return None if status is None else status.brightness

    async def _with_retry(self, call: Any) -> None:
        """Run one `session -> coroutine` command, reconnecting first if
        the link isn't up, and once more if it dies mid-call.

        Commands are user-initiated and must not wait for the next scheduled
        harvest to get a connection, so this connects on demand.
        """
        session = await self._async_ensure_connected()
        try:
            await call(session)
        except Exception as exc:
            # A link that died between the check above and this write. One
            # retry against a fresh connection; a second failure is a real
            # problem, not a transient race, and should surface.
            _LOGGER.debug(
                "%s: command failed (%s), reconnecting and retrying once",
                self.mesh_name,
                exc,
            )
            self.session = None
            session = await self._async_ensure_connected()
            await call(session)

        # A command just round-tripped over a live link, which is fresher
        # evidence of reachability than the last harvest. Without this an
        # entity that just successfully sent a command could still read
        # unavailable until the next scheduled refresh.
        self.async_set_updated_data(self.device_states)

    async def async_send(self, target: int, opcode: int, data: bytes) -> None:
        await self._with_retry(lambda session: session.send(target, opcode, data))

    async def async_set_power(self, target: int, on: bool) -> None:
        await self._with_retry(lambda session: session.set_power(target, on))
        # An "on" with no level of its own restores whatever the device was
        # last at, which we may know from a harvest. Falling back to full
        # brightness is a guess, but a visible light is the right guess when
        # the alternative is showing it as off.
        if on:
            known = self.reported_brightness(target) or 0
            self._record_optimistic(target, known if known > 0 else 100)
        else:
            self._record_optimistic(target, 0)

    async def async_set_brightness(
        self, target: int, brightness: int, *, is_sol_lamp: bool = False
    ) -> None:
        await self._with_retry(
            lambda session: session.set_brightness(
                target, brightness, is_sol_lamp=is_sol_lamp
            )
        )
        self._record_optimistic(target, brightness)

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
