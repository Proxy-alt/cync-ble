"""The coordinator resolves a stored MAC against the Bluetooth stack.

This is the step that failed on a real install: every stored address was
bare hex, so `async_ble_device_from_address` matched nothing and setup
reported all 46 devices unreachable when 44 of them were plainly
advertising.
"""

from __future__ import annotations

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cync_ble.const import (
    CONF_DEVICES,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    DOMAIN,
)
from custom_components.cync_ble.coordinator import CyncBleCoordinator

FORWARD_STORED = "F4BCDA32A971"
FORWARD_REAL = "F4:BC:DA:32:A9:71"
REVERSED_STORED = "152232dabcf4"
REVERSED_REAL = "F4:BC:DA:32:22:15"


def _coordinator(hass, macs):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="meshname",
        data={
            CONF_MESH_NAME: "meshname",
            CONF_MESH_PASSWORD: "meshpass",
            CONF_DEVICES: [
                {"id": i, "name": f"d{i}", "type": 38, "mac": m}
                for i, m in enumerate(macs, start=1)
            ],
        },
    )
    entry.add_to_hass(hass)
    return CyncBleCoordinator(hass, entry)


def _stack_seeing(*addresses):
    """Stand in for Home Assistant's Bluetooth stack, which only answers for
    addresses it has actually seen advertising."""
    known = set(addresses)

    def _lookup(_hass, address, connectable=True):
        return object() if address in known else None

    return patch(
        "custom_components.cync_ble.coordinator.bluetooth."
        "async_ble_device_from_address",
        side_effect=_lookup,
    )


async def test_bare_hex_is_punctuated_before_lookup(hass):
    """The original bug: the stack is asked about 'F4BCDA32A971', which it
    can never match, so a device that is right there reads as absent."""
    coordinator = _coordinator(hass, [FORWARD_STORED])
    with _stack_seeing(FORWARD_REAL):
        resolved = coordinator._resolve(FORWARD_STORED)
    assert resolved is not None
    assert resolved[1] == FORWARD_REAL


async def test_reversed_entries_fall_back_to_the_other_orientation(hass):
    coordinator = _coordinator(hass, [REVERSED_STORED])
    with _stack_seeing(REVERSED_REAL):
        resolved = coordinator._resolve(REVERSED_STORED)
    assert resolved is not None
    assert resolved[1] == REVERSED_REAL


async def test_the_resolved_address_is_the_one_returned(hass):
    """It is used for the session key as well as the connection, so handing
    back the stored form instead would authenticate against nothing - and
    would do it without any visible error."""
    coordinator = _coordinator(hass, [REVERSED_STORED])
    with _stack_seeing(REVERSED_REAL):
        _, addr = coordinator._resolve(REVERSED_STORED)
    assert addr != coordinator.devices[0]["mac"]
    assert addr == REVERSED_REAL


async def test_a_genuinely_absent_device_resolves_to_nothing(hass):
    coordinator = _coordinator(hass, [FORWARD_STORED])
    with _stack_seeing():
        assert coordinator._resolve(FORWARD_STORED) is None


async def test_only_one_lookup_when_the_stored_form_is_right(hass):
    """The common case must not cost two lookups per device across a mesh
    of ~46 nodes."""
    coordinator = _coordinator(hass, [FORWARD_STORED])
    with _stack_seeing(FORWARD_REAL) as mock:
        coordinator._resolve(FORWARD_STORED)
    assert mock.call_count == 1
