"""A small, self-contained Cync cloud client for setup.

Used once, during the config flow, to turn an account login into mesh
credentials and a device list. Nothing here runs after setup.

**Why this exists rather than reusing `cync_lan.cloud_api.CyncCloudAPI`.**
That class is a process-wide singleton (`__new__` caches `_instance`)
configured entirely through environment variables that `cync_lan.const`
reads *once, at import time*. Both properties are fine for the standalone
add-on it was written for - one account, one process - and cync-lan's own
integration documents the resulting single-account limitation openly.

They are not survivable here, because cync_ble is a *second* integration
that can be installed alongside cync-lan in the same Home Assistant
process. Confirmed by reproducing it directly: when cync-lan sets up first,
`cync_lan.const` freezes its paths, and afterwards

- `CyncCloudAPI()` hands back cync-lan's live instance, token cache and
  all. On the install this was found on, that meant `check_token()` picked
  up cync-lan's *expired* token and tried to refresh it - a path a fresh
  install has no way to reach, and one that was itself broken (fixed
  separately in cync-lan). The resulting exception was reported to the user
  as "could not reach the Cync cloud API", which is how a bug in neither
  the cloud nor this integration ended up presenting as a network fault
  here;
- setting `CYNC_CONFIG_DIR` has no effect, so cync_ble's writes land in
  cync-lan's directory while its reads look in cync_ble's;
- `CYNC_ACCOUNT_USERNAME`/`_PASSWORD` also have no effect, so credentials
  typed into cync_ble's wizard are silently ignored and the *other*
  account's are used instead - the worst of the three, because it looks
  like it worked.

So this talks to the four endpoints setup actually needs, taking every
input as an argument. It holds no module state, writes nothing to disk, and
returns plain dicts for the config entry to store - which also removes the
YAML-file round trip, the on-disk token cache, and the Fernet secret those
required.
"""

from __future__ import annotations

import random
import string
from typing import Any

import aiohttp

from .address import to_colon_form

API_BASE = "https://api.gelighting.com/v2/"
# The vendor's own app identifier, same value cync_lan uses.
CORP_ID = "1007d2ad150c4000"

TIMEOUT = aiohttp.ClientTimeout(total=15)


class CyncCloudError(Exception):
    """The cloud could not be reached, or answered something unusable."""


class CyncAuthError(CyncCloudError):
    """Credentials or the one-time code were rejected."""


class CyncCloud:
    """One setup conversation with the Cync cloud."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._access_token: str | None = None
        self._user_id: str | int | None = None

    async def _json(self, method: str, url: str, **kwargs: Any) -> Any:
        """One request, with transport failures and API-level errors kept
        distinct - the caller needs to tell "the cloud is unreachable" from
        "the cloud says no", and conflating them is what made the original
        bug so hard to read."""
        try:
            resp = await self._session.request(method, url, timeout=TIMEOUT, **kwargs)
            body = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise CyncCloudError(f"could not reach {url}: {exc}") from exc
        except (TimeoutError, ValueError) as exc:
            raise CyncCloudError(f"bad or absent response from {url}: {exc}") from exc

        # The API reports failures as a 4xx *and* an {"error": ...} body -
        # e.g. 404 {"error": {"msg": "user not exists", "code": 4041011}} for
        # an unknown account, which is an auth answer, not a missing page.
        if isinstance(body, dict) and "error" in body:
            err = body["error"] or {}
            raise CyncAuthError(str(err.get("msg") or err))
        if resp.status >= 400:
            raise CyncCloudError(f"{url} returned HTTP {resp.status}")
        return body

    async def request_otp(self, email: str) -> None:
        """Ask the cloud to email a one-time code. Raises on rejection."""
        await self._json(
            "POST",
            f"{API_BASE}two_factor/email/verifycode",
            json={"corp_id": CORP_ID, "email": email, "local_lang": "en-us"},
        )

    async def login(self, email: str, password: str, otp_code: int) -> None:
        """Exchange the emailed code for an access token."""
        body = await self._json(
            "POST",
            f"{API_BASE}user_auth/two_factor",
            json={
                "corp_id": CORP_ID,
                "email": email,
                "password": password,
                "two_factor": otp_code,
                "resource": "".join(random.choices(string.ascii_lowercase, k=16)),
            },
        )
        try:
            self._access_token = body["access_token"]
            self._user_id = body["user_id"]
        except (KeyError, TypeError) as exc:
            raise CyncCloudError(f"login response missing {exc}") from exc

    @property
    def _auth_headers(self) -> dict[str, str]:
        if not self._access_token:
            raise CyncCloudError("login() first")
        return {"Access-Token": str(self._access_token)}

    async def async_get_homes(self) -> list[dict[str, Any]]:
        """Every home on the account that has a usable mesh, as
        `{"name", "mesh_name", "mesh_password", "devices": [...]}`.

        Homes the API returns with no name or no device array are skipped:
        real accounts carry a number of empty placeholder entries (each
        Wi-Fi hub gets one), and cync-lan's exporter skips them for the same
        reason.
        """
        raw_homes = await self._json(
            "GET",
            f"{API_BASE}user/{self._user_id}/subscribe/devices",
            headers=self._auth_headers,
        )
        homes: list[dict[str, Any]] = []
        for raw in raw_homes or []:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            if "access_key" not in raw or "mac" not in raw:
                continue
            properties = raw.get("properties")
            if properties is None:
                properties = await self._get_properties(raw["product_id"], raw["id"])
            bulbs = (properties or {}).get("bulbsArray")
            if not bulbs:
                continue
            homes.append(
                {
                    "name": raw["name"],
                    # Confirmed on hardware: the home's own `mac` is the Telink
                    # mesh name and its `access_key` is the mesh password. Not
                    # obtainable from the hub - see ARCHITECTURE.md.
                    "mesh_name": str(raw["mac"]),
                    "mesh_password": str(raw["access_key"]),
                    "devices": _parse_devices(bulbs),
                }
            )
        return homes

    async def _get_properties(self, product_id: Any, device_id: Any) -> dict[str, Any]:
        """A home's device list. A home with none answers with an error
        body rather than an empty one, which is normal and not fatal - the
        caller drops the home."""
        try:
            return await self._json(
                "GET",
                f"{API_BASE}product/{product_id}/device/{device_id}/property",
                headers=self._auth_headers,
            )
        except CyncCloudError:
            return {}


def _parse_devices(bulbs: list[Any]) -> list[dict[str, Any]]:
    """Flatten a home's `bulbsArray` into `{"id", "name", "type", "mac"}`.

    `id` is the mesh address commands are addressed to, recovered from the
    tail of `deviceID` exactly as cync-lan's exporter does. Multi-endpoint
    sub-devices (a longer remainder) are skipped: they share their parent's
    mesh address, so emitting them would create duplicate entities pointed
    at the same target.

    `mac` is punctuated here but its byte order is left exactly as the cloud
    gave it - a minority of entries are reversed, and which way round any
    given one goes is settled against the Bluetooth stack at connect time
    rather than guessed at here. See address.py.
    """
    devices: list[dict[str, Any]] = []
    for raw in bulbs or []:
        if not isinstance(raw, dict):
            continue
        if not all(k in raw for k in ("deviceID", "displayName", "mac", "deviceType")):
            continue
        raw_id = str(raw["deviceID"])
        remainder = raw_id[9:]
        if len(remainder) > 4 or not remainder:
            continue
        try:
            mesh_id = int(remainder[-3:])
        except ValueError:
            continue
        devices.append(
            {
                "id": mesh_id,
                "name": str(raw["displayName"]),
                "type": int(raw["deviceType"]),
                "mac": to_colon_form(str(raw["mac"])),
            }
        )
    return devices
