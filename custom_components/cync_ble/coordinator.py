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
from .adapters import ADAPTER_NONE
from .const import (
    CONF_DEVICES,
    CONF_DIRECT_ADAPTER,
    CONF_KNOWN_GOOD,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    DOMAIN,
    HARVEST_DEADLINE_SECONDS,
    HARVEST_FAILURE_LIMIT,
    HARVEST_RETRY_AFTER_SECONDS,
    HARVEST_WINDOW_SECONDS,
    MAX_CONNECT_ATTEMPTS,
    MAX_KNOWN_GOOD,
    NODE_REST_SECONDS,
)
from .direct_client import DirectClientUnavailable, build_direct_client

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
        # When set, this integration drives that adapter itself through
        # bumble instead of going through Home Assistant's Bluetooth stack.
        # See direct_client.py - the point is that only the GATT client
        # changes; everything above it is identical.
        self.direct_adapter: str = entry.options.get(CONF_DIRECT_ADAPTER, ADAPTER_NONE)
        # Set once if direct mode is configured but cannot actually run, so
        # the integration falls back to Home Assistant's stack instead of
        # failing every cycle. A missing optional dependency must not brick
        # an integration that works perfectly well without it.
        self._direct_disabled = False

        self._client: BleakClientWithServiceCache | None = None
        self.session: BleMeshSession | None = None
        # Nodes that refused a connection recently, by mac. A node that just
        # failed is the worst thing to try next: the adapter has a small,
        # shared pool of connection slots, and the common failure here is
        # "out of connection slots" rather than anything about the node.
        self._recent_failures: dict[str, float] = {}
        # Nodes that have completed a mesh handshake at least once. Good
        # enough to send commands through - any authenticated node reaches the
        # whole mesh - but NOT sufficient to harvest from; see _known_good.
        self._connectable: set[str] = set(entry.options.get(CONF_KNOWN_GOOD, []))
        # Nodes that have actually delivered a non-empty status sweep.
        #
        # This is a stricter thing than "authenticated", and the distinction is
        # load-bearing. Measured across all 46 nodes of a real mesh, the
        # firmware answers the subscribe two different ways depending on which
        # OUI it carries, and only one of them can ever produce a sweep:
        #
        #   F4:BC:DA / 30:C0:1B  the CCCD write gets no ATT response at all,
        #                        bleak keeps waiting, the callback stays
        #                        registered, and 22 notifications covering 38
        #                        of 46 devices arrive during the window.
        #   78:6D:EB             the write is refused instantly with
        #                        WRITE_NOT_PERMITTED, bleak raises, and the
        #                        callback is discarded before the sweep starts.
        #
        # 10/10 and 3/3, at overlapping signal strengths, across four device
        # types on the refusing OUI - so it tracks the silicon, not the
        # product or the radio conditions. Both kinds authenticate happily,
        # which is exactly why authentication is the wrong thing to remember.
        #
        # Seeded from the config entry under the OLD meaning ("authenticated"),
        # which may include nodes that can never harvest. That self-corrects:
        # the first harvest through such a node collects nothing and demotes it
        # to _barren below.
        self._known_good: set[str] = set(entry.options.get(CONF_KNOWN_GOOD, []))
        # Nodes that authenticated, were harvested through, and produced
        # nothing. Still fine to command through - only useless to harvest
        # from, so this demotes them for harvests alone.
        self._barren: dict[str, float] = {}
        # Best sweep size seen per node, used to decide which proven nodes are
        # worth keeping when the persisted set is capped.
        self._sweep_size: dict[str, int] = {}
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

    def _candidate_macs(self, *, for_harvest: bool = False) -> list[str]:
        """Nodes to try, best first - and "best" differs by errand.

        Ordering matters far more here than it looks, because the two things
        this integration does have different requirements of a node.

        A **command** needs any node that authenticates; every one of them
        reaches the whole mesh, so connecting fast is the only virtue.

        A **harvest** additionally needs a node whose firmware leaves the
        subscribe hanging rather than refusing it outright, because only the
        hanging kind ever delivers a sweep (see `_known_good`). Nodes of the
        refusing kind connect and authenticate perfectly well and then hand
        back nothing, so for a harvest they belong below never-tried nodes -
        while for a command they are among the best things available.

        Proven nodes lead in both cases, **least recently used first**. That
        rotation is deliberate: the harvest ends by having its link killed, and
        the node that just served one is the least likely to accept another
        connection immediately. Rotating only among nodes already known to work
        is what makes this safe - an earlier attempt that rotated across the
        whole list spread the failures around and exhausted the adapter.
        """
        now = time.monotonic()
        proven, barren, resting, untried, failed = [], [], [], [], []
        for device in self.devices:
            mac = device.get("mac")
            if not mac:
                continue
            used_at = self._last_used.get(mac, 0.0)
            # A node that served the last harvest had its link killed by it,
            # and reliably refuses the next one - observed as a steady 72s
            # cycle where the single proven node failed first every time. Rest
            # it, and let the walk find a second proven node so there is
            # something to rotate between.
            is_resting = now - used_at < NODE_REST_SECONDS
            if mac in self._known_good:
                (resting if is_resting else proven).append(mac)
            elif mac in self._barren or mac in self._connectable:
                barren.append(mac)
            elif mac in self._recent_failures:
                failed.append(mac)
            else:
                untried.append(mac)
        proven.sort(key=lambda mac: self._last_used.get(mac, 0.0))
        barren.sort(key=lambda mac: self._last_used.get(mac, 0.0))
        # Strongest signal first among nodes we know nothing about. This is
        # what stops a walk burning its budget on nodes Home Assistant lists
        # as connectable but has no signal from (RSSI -127), which is exactly
        # what made `light.turn_on` fail after 90s while a perfectly
        # reachable node sat further down the list.
        untried.sort(key=self._signal, reverse=True)
        failed.sort(key=lambda mac: self._recent_failures.get(mac, 0.0))
        if for_harvest:
            # Never-tried nodes outrank known-barren ones: trying one might
            # find a second node that actually sweeps, whereas a barren node
            # is guaranteed to waste the attempt.
            return proven + untried + resting + barren + failed
        # For commands, a node known to authenticate beats an unknown one.
        return proven + barren + untried + resting + failed

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
                # An offline device still reports the last level the mesh knew,
                # which is real information but not a fresh reading - so it
                # fills a gap rather than overwriting something we already
                # have. getattr because the field is newer than the version
                # floor in the manifest; older releases only ever produced
                # readings that were online by construction.
                if not getattr(status, "online", True):
                    self.device_states.setdefault(status.device_id, status)
                    continue
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

        # Home Assistant only knows what is reachable on the adapter IT is
        # scanning with, so its view is meaningless for a dedicated one.
        if self.direct_mode:
            candidates = self._candidate_macs(for_harvest=True)
        else:
            candidates = [
                m for m in self._candidate_macs(for_harvest=True) if self._resolve(m)
            ]
        _LOGGER.debug(
            "%s: %d candidate node(s), %d proven; trying up to %d (%s)",
            self.mesh_name,
            len(candidates),
            len(self._known_good),
            MAX_CONNECT_ATTEMPTS,
            f"direct via {self.direct_adapter}" if self.direct_mode else "via HA stack",
        )
        # A deadline checked BETWEEN attempts, never a timeout wrapped around
        # one. Cancelling an in-flight establish_connection leaks the
        # connection slot it reserved, and a leaked pool never recovers - see
        # _connect_to. So a cycle stops starting new attempts once it is out
        # of time, and always lets the one it started finish.
        deadline = time.monotonic() + HARVEST_DEADLINE_SECONDS
        session = None
        harvest_mac = None
        for mac in candidates[:MAX_CONNECT_ATTEMPTS]:
            if time.monotonic() > deadline:
                _LOGGER.debug(
                    "%s: out of time for this cycle, will resume next refresh",
                    self.mesh_name,
                )
                break
            session = await self._connect_to(mac)
            if session is not None:
                harvest_mac = mac
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

        collected = 0

        def _on_notification(_sender: Any, data: bytearray) -> None:
            nonlocal collected
            try:
                clear = decrypt_packet(key, address, bytearray(data))
                statuses = parse_status(bytes(clear))
                collected += len(statuses or ())
                self._on_mesh_status(statuses)
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
        if harvest_mac is not None:
            self._record_sweep(harvest_mac, collected)
        _LOGGER.debug(
            "%s: harvest via %s collected %d status record(s), %d device states "
            "known (%d new)",
            self.mesh_name,
            harvest_mac,
            collected,
            len(self.device_states),
            len(self.device_states) - before,
        )

    def _record_sweep(self, mac: str, collected: int) -> None:
        """Learn whether this node can actually harvest, from what it just did.

        The whole point of separating this from `_authenticate`: a node of the
        refusing OUI family authenticates exactly like a working one and then
        returns nothing, so only an attempted sweep distinguishes them. One
        empty sweep is enough to demote - the behaviour is a property of the
        firmware, not a transient, and it was unanimous within each family
        across every node of a 46-node mesh.
        """
        if collected:
            self._barren.pop(mac, None)
            self._sweep_size[mac] = max(self._sweep_size.get(mac, 0), collected)
            if mac not in self._known_good:
                self._known_good.add(mac)
                self._persist_known_good()
            return

        self._barren[mac] = time.monotonic()
        if mac in self._known_good:
            # Seeded from the old "authenticated" meaning, or a node that has
            # stopped delivering. Either way it is not a harvest relay.
            self._known_good.discard(mac)
            self._sweep_size.pop(mac, None)
            _LOGGER.debug(
                "%s: %s authenticates but delivers no sweep, so it is no longer "
                "preferred for harvests (it is still fine for commands)",
                self.mesh_name,
                mac,
            )
            self._persist_known_good()

    def _persist_known_good(self) -> None:
        """Write newly-proven nodes back to the config entry.

        Only on a genuine change, never on every connect - this rewrites
        stored options, and doing that on a 120s cycle for no change would be
        pointless churn. Capped, because a mesh where everything answers does
        not need a list of everything.

        Kept by **best sweep size**, not alphabetically. Sorting by mac was an
        active bug rather than an arbitrary choice: `78:6D:EB` sorts before
        `F4:BC:DA`, and those are precisely the two families that cannot and
        can harvest, so the cap systematically evicted the working nodes and
        kept the useless ones.
        """
        keep = sorted(
            self._known_good,
            key=lambda mac: (self._sweep_size.get(mac, 0), mac),
            reverse=True,
        )[:MAX_KNOWN_GOOD]
        try:
            self.hass.config_entries.async_update_entry(
                self.entry,
                options={**self.entry.options, CONF_KNOWN_GOOD: keep},
            )
        except Exception:
            _LOGGER.debug("Could not persist known-good nodes", exc_info=True)

    async def _async_close_link(self) -> None:
        """Drop whatever link is currently open. One at a time - this radio is
        shared with every other Bluetooth integration on the box."""
        client, self._client, self.session = self._client, None, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                _LOGGER.debug("%s: error closing link", self.mesh_name, exc_info=True)

    @property
    def direct_mode(self) -> bool:
        return self.direct_adapter != ADAPTER_NONE and not self._direct_disabled

    async def _connect_to(self, mac: str) -> BleMeshSession | None:
        if self.direct_mode:
            return await self._connect_direct(mac)
        resolved = self._resolve(mac)
        if resolved is None:
            return None
        ble_device, addr = resolved
        try:
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

        return await self._authenticate(client, mac, addr)

    async def _connect_direct(self, mac: str) -> BleMeshSession | None:
        """Connect over the dedicated adapter, without Home Assistant's stack.

        Address resolution is skipped entirely: Home Assistant is not
        scanning on this adapter, so it has no view of what is reachable
        there. bumble connects by address directly, which also sidesteps the
        connection-slot accounting that refused perfectly reachable nodes on
        the shared adapter.

        Byte order still has to be right, because the session key is derived
        from the address - so both orientations are tried, as elsewhere.
        """
        for addr in address.candidates(mac):
            try:
                client = build_direct_client(
                    addr, self.direct_adapter, disconnected_callback=self._on_disconnect
                )
                await client.connect()
            except DirectClientUnavailable as exc:
                # Configured for direct mode, but the optional backend is not
                # installed. Degrade to Home Assistant's stack rather than
                # failing forever - observed failing every cycle in 0.005s,
                # which is a bricked integration with no useful error.
                self._direct_disabled = True
                _LOGGER.error(
                    "%s: a dedicated adapter (%s) is configured but cannot be "
                    "used, so falling back to Home Assistant's Bluetooth. %s",
                    self.mesh_name,
                    self.direct_adapter,
                    exc,
                )
                return await self._connect_to(mac)
            except Exception as exc:
                _LOGGER.debug(
                    "%s: direct connect to %s on %s failed: %s",
                    self.mesh_name,
                    addr,
                    self.direct_adapter,
                    exc,
                )
                continue
            session = await self._authenticate(client, mac, addr)
            if session is not None:
                return session
        self._recent_failures[mac] = time.monotonic()
        self._last_used[mac] = time.monotonic()
        return None

    async def _authenticate(
        self, client: Any, mac: str, addr: str
    ) -> BleMeshSession | None:
        """Shared mesh handshake, whichever client got us here.

        Both backends land in the same place on purpose - it is the evidence
        that nothing above the GATT client needs to know which one is in use.
        """
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
        self._last_used[mac] = time.monotonic()
        self._recent_failures.pop(mac, None)
        # Authenticating proves the node can carry commands, and nothing more.
        # Whether it can carry a harvest is decided by _record_sweep, after one
        # has actually been attempted through it.
        self._connectable.add(mac)
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

        if self.direct_mode:
            visible = self._candidate_macs()
        else:
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
