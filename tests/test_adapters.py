"""The adapter picker, and the mistake it exists to prevent.

Selecting the adapter Home Assistant is already using would take Bluetooth
away from every other integration - the lock, the beacons, the scanner -
because HCI_CHANNEL_USER is exclusive. That is the whole reason this is not
a free-text field.
"""

from __future__ import annotations

from custom_components.cync_ble.adapters import (
    ADAPTER_NONE,
    AdapterChoice,
    is_known,
    needs_takeover_confirmation,
    selection_options,
)

FREE = AdapterChoice("hci1", "AA:BB:CC:DD:EE:01", "Spare dongle", in_use_by_hass=False)
BUSY = AdapterChoice("hci0", "AA:BB:CC:DD:EE:00", "Built-in", in_use_by_hass=True)


def test_doing_nothing_needs_no_confirmation():
    assert needs_takeover_confirmation([BUSY], ADAPTER_NONE) is None
    assert is_known([BUSY], ADAPTER_NONE)


def test_a_free_adapter_needs_no_confirmation():
    """Taking a spare dongle costs nobody anything, so it should not nag."""
    assert needs_takeover_confirmation([FREE, BUSY], "hci1") is None


def test_taking_the_adapter_hass_uses_requires_confirmation():
    """Allowed - it is the user's machine - but never straight from a
    dropdown, because it stops every other Bluetooth integration working."""
    target = needs_takeover_confirmation([FREE, BUSY], "hci0")
    assert target is not None
    assert target.adapter == "hci0"


def test_an_unplugged_adapter_is_refused():
    """A stored choice can name a dongle that is no longer there; it must
    not be silently honoured."""
    assert not is_known([FREE], "hci9")
    assert is_known([FREE], "hci1")


def test_busy_adapters_are_shown_with_a_reason_not_hidden():
    """On a single-adapter machine, hiding the only entry would leave the
    user wondering where the option went."""
    options = selection_options([BUSY])
    assert "hci0" in options
    assert "in use by Home Assistant" in options["hci0"]


def test_opting_out_is_offered_first():
    options = selection_options([FREE, BUSY])
    assert next(iter(options)) == ADAPTER_NONE


async def test_adapters_are_refreshed_before_being_read(hass):
    """Regression: the picker showed only "use Home Assistant's Bluetooth".

    `get_adapters()` returns a backend whose `adapters` mapping is empty
    until `refresh()` has done the D-Bus/sysfs work. The first version never
    awaited it, so the list was always empty - and a blanket `except` meant
    that failed silently rather than reporting anything.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from custom_components.cync_ble.adapters import async_list_adapters

    manager = MagicMock()
    manager.refresh = AsyncMock()
    # Empty until refresh() runs, exactly like the real backend.
    manager.adapters = {}

    async def _populate() -> None:
        manager.adapters = {
            "hci0": {"address": "AA:BB:CC:DD:EE:00", "manufacturer": "Test"}
        }

    manager.refresh.side_effect = _populate

    with (
        patch(
            "custom_components.cync_ble.adapters.bluetooth.get_adapters",
            return_value=manager,
        ),
        patch(
            "custom_components.cync_ble.adapters.bluetooth.async_current_scanners",
            return_value=[],
        ),
    ):
        choices = await async_list_adapters(hass)

    manager.refresh.assert_awaited_once()
    assert [c.adapter for c in choices] == ["hci0"]
