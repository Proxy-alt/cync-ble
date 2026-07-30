"""Turning the cloud's device MAC field into a real Bluetooth address.

The Cync cloud does not hand back addresses in the form anything else
wants them. Two separate problems, both confirmed against a live account
and a live BLE scan on the same mesh:

**No separators.** Every entry is bare hex - `F4BCDA32A971`, not
`F4:BC:DA:32:A9:71`. Home Assistant's `async_ble_device_from_address` and
`cync_lan.ble_mesh.mac_to_address` (which splits on ":") both need the
separated form, so every lookup and every session key silently fails
without this.

**Some are byte-reversed.** On the account this was found on, 44 of 46
devices were stored most-significant-byte first and 2 were stored
reversed. Reversing those two produced addresses that were genuinely
advertising at that moment, with the same `F4:BC:DA` OUI as their
neighbours - so this is a real quirk in the vendor's data, not a decoding
mistake here.

The reversed ones happened to be exactly the lowercase ones, which is
tempting as a rule and is deliberately **not** used as one: it is a
sample of two from a single account, and getting it wrong is not a
visible failure - a wrongly-oriented address produces a wrong session key
and therefore a session that authenticates against nothing. Instead
`candidates()` offers both orientations and the caller resolves it against
the Bluetooth stack, letting what is actually on the air decide.
"""

from __future__ import annotations


def _hex_only(mac: str) -> str:
    return mac.replace(":", "").replace("-", "").strip()


def to_colon_form(mac: str) -> str:
    """`f4bcda32a971` or `F4:BC:DA:32:A9:71` to `F4:BC:DA:32:A9:71`.

    Returned uppercase, which is the form Home Assistant's Bluetooth
    stack normalises to and compares against.
    """
    raw = _hex_only(mac).upper()
    return ":".join(raw[i : i + 2] for i in range(0, len(raw), 2))


def byte_reversed(mac: str) -> str:
    """The same address with its six bytes in the opposite order."""
    raw = _hex_only(mac).upper()
    octets = [raw[i : i + 2] for i in range(0, len(raw), 2)]
    return ":".join(reversed(octets))


def candidates(mac: str) -> list[str]:
    """Both plausible readings of one stored MAC, as-stored first.

    Order matters: as-stored is right for the large majority, so trying it
    first keeps the common path to a single lookup. Returns one entry when
    the address is a palindrome or malformed, rather than a duplicate.
    """
    forward = to_colon_form(mac)
    backward = byte_reversed(mac)
    if backward == forward:
        return [forward]
    return [forward, backward]
