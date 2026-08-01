"""Driving a dedicated adapter through bumble instead of BlueZ.

This exists to demonstrate something specific, not merely to work around a
bug: **the same integration, the same protocol code, and only the GATT
client swapped** is enough to make this device class work. Everything above
this module is unchanged between the two backends.

Why it is needed at all: this firmware refuses the CCCD write BlueZ performs
when subscribing (captured on the wire - sometimes `WRITE_NOT_PERMITTED`,
sometimes no ATT response at all), and BlueZ then destroys the connection
after its own timeout. Notifications were arriving throughout. Android never
writes the descriptor, because `setCharacteristicNotification` is local-only;
BlueZ has no equivalent and no way to skip it.

`bleak-bumble` supplies a `BleakClient`-compatible backend over Google's
bumble stack, so the application becomes the ATT client and no descriptor
write is ever required. Because it is BleakClient-shaped, it satisfies the
same `cync_lan.ble_mesh.GattClient` protocol the bleak path does, and
`BleMeshSession` cannot tell the difference.

**Deliberately not a manifest requirement.** It is not on PyPI, and a git
URL there is not installable by Home Assistant - declaring it stopped the
whole integration loading, for everyone, including the vast majority who
never turn this on. It is imported lazily instead and its absence is a clear
error on the one code path that needs it. Install it yourself to use direct
mode:

    pip install git+https://github.com/ekspla/bleak-bumble_dev_host_mode

(the `ekspla` fork, which carries host-mode fixes over the original
`vChavezB/bleak-bumble`).

**Operational cost, stated plainly.** `HCI_SOCKET` binds `HCI_CHANNEL_USER`,
which requires the adapter to be *down* and takes it away from BlueZ
entirely for as long as it is held. This is only sane on an adapter nothing
else is using - see adapters.py, which makes choosing otherwise deliberate.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class DirectClientUnavailable(Exception):
    """bleak-bumble is not installed, or the adapter cannot be driven."""


def _adapter_index(adapter: str) -> str:
    """`hci1` -> `1`, which is what the HCI_SOCKET transport expects."""
    digits = "".join(ch for ch in adapter if ch.isdigit())
    if not digits:
        raise DirectClientUnavailable(
            f"cannot work out an HCI index from adapter name {adapter!r}"
        )
    return digits


def build_direct_client(address: str, adapter: str, **kwargs: Any) -> Any:
    """A BleakClient driving `adapter` directly, bypassing BlueZ.

    Returned as a plain `BleakClient` so every caller above stays identical -
    that interchangeability is the whole point of the exercise.

    Imported lazily and deliberately: `bleak-bumble` is an optional extra,
    and an integration that only ever uses Home Assistant's own Bluetooth
    stack should not fail to load because it is absent.
    """
    try:
        from bleak import BleakClient
        from bleak_bumble import BumbleTransportCfg, TransportScheme
        from bleak_bumble.client import BleakClientBumble
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise DirectClientUnavailable(
            "bleak-bumble is not installed, so a dedicated adapter cannot be "
            "driven directly. Install it with 'pip install git+https://"
            "github.com/ekspla/bleak-bumble_dev_host_mode', or set the "
            "adapter back to the default to use Home Assistant's Bluetooth."
        ) from exc

    cfg = BumbleTransportCfg(TransportScheme.HCI_SOCKET, _adapter_index(adapter))
    _LOGGER.debug(
        "Building a bumble-backed client for %s on %s (HCI_CHANNEL_USER)",
        address,
        adapter,
    )
    return BleakClient(address, backend=BleakClientBumble, cfg=cfg, **kwargs)
