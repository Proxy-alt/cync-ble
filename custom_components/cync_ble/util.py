"""Bridge Home Assistant's per-entry config model onto the upstream
cync_lan package's environment-variable-driven CyncCloudAPI.

Mirrors cync-lan's own custom_components/cync_lan/util.py, which solved
this exact problem first (see its docstrings for the full reasoning). Not
importable from here directly - that's a sibling repository's HA
integration, not a library this one depends on - so the relevant pieces are
duplicated rather than shared. What's duplicated is deliberately the
minimum: cync_ble only ever uses CyncCloudAPI for a one-shot cloud login to
retrieve mesh credentials and a device list, never for cync-lan's ongoing
TCP-daemon concerns (hub envelope, max connections, etc.), so none of that
belongs here.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from cync_lan.cloud_api import CyncCloudAPI

_LOGGER = logging.getLogger(__name__)


async def configure_environment(
    hass: HomeAssistant, username: str, password: str
) -> None:
    """Point cync_lan's env-var-driven config at this config flow.

    Must run before the first `import cync_lan.const` anywhere in the
    process - its module-level constants are read once, at import time.
    Both the config flow and a future reauth flow call this before
    touching anything under the `cync_lan` package.

    CYNC_EXPORT_SOURCE is explicitly unset (never just left alone) so an
    unrelated env var - e.g. a cync-lan add-on's own file-based export,
    running elsewhere on the same host - can never be picked up here by
    accident; this integration always talks to the cloud directly.
    """
    config_dir = hass.config.path("cync_ble")
    await hass.async_add_executor_job(os.makedirs, config_dir, 0o755, True)
    os.environ["CYNC_ACCOUNT_USERNAME"] = username
    os.environ["CYNC_ACCOUNT_PASSWORD"] = password
    os.environ["CYNC_CONFIG_DIR"] = config_dir
    os.environ.pop("CYNC_EXPORT_SOURCE", None)
    os.environ.setdefault("CYNC_SECRET_KEY", await stable_secret(hass))


def get_cloud_api(hass: HomeAssistant) -> CyncCloudAPI:
    """Construct CyncCloudAPI with Home Assistant's shared aiohttp session
    instead of letting it open (and potentially leak) its own.

    CyncCloudAPI is a singleton that only overwrites its session when one
    is explicitly passed, so every call site should go through this helper
    rather than constructing it bare.
    """
    from cync_lan.cloud_api import CyncCloudAPI
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    return CyncCloudAPI(session=async_get_clientsession(hass))


async def read_exported_homes(config_dir: str) -> dict:
    """Read back the YAML export CyncCloudAPI.export_config_file() just
    wrote, and return its `exported_homes` mapping.

    A second file round-trip rather than reaching into
    CyncCloudAPI._parse_raw_export's return value directly - export_config_file()
    is the public, documented contract; the leading-underscore method is not.
    """
    import yaml

    def _read() -> dict:
        path = Path(config_dir) / "cync_mesh.yaml"
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    data = await asyncio.get_running_loop().run_in_executor(None, _read)
    return data.get("exported_homes", {})


async def stable_secret(hass: HomeAssistant) -> str:
    """Derive a stable local secret for CyncCloudAPI's token-cache cipher.

    Not a network secret - only protects the on-disk cached cloud token
    from casual reading. Must be stable across HA restarts: Home
    Assistant's own persisted instance UUID already serves this exact
    purpose elsewhere in core, and cync-lan's own integration uses the
    same source for the same reason (see its util.stable_secret).
    """
    from homeassistant.helpers import instance_id

    return await instance_id.async_get(hass)
