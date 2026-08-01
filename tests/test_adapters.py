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
    is_selectable,
    selection_options,
)

FREE = AdapterChoice("hci1", "AA:BB:CC:DD:EE:01", "Spare dongle", in_use_by_hass=False)
BUSY = AdapterChoice("hci0", "AA:BB:CC:DD:EE:00", "Built-in", in_use_by_hass=True)


def test_doing_nothing_is_always_allowed():
    assert is_selectable([], ADAPTER_NONE)
    assert is_selectable([BUSY], ADAPTER_NONE)


def test_a_free_adapter_may_be_taken():
    assert is_selectable([FREE, BUSY], "hci1")


def test_the_adapter_hass_is_using_may_not_be_taken():
    """The guardrail. Taking hci0 here would black out every other
    Bluetooth integration on the machine."""
    assert not is_selectable([FREE, BUSY], "hci0")


def test_an_unknown_adapter_is_refused():
    """A stored choice for a dongle that has since been unplugged must not
    be silently honoured."""
    assert not is_selectable([FREE], "hci9")


def test_busy_adapters_are_shown_with_a_reason_not_hidden():
    """On a single-adapter machine, hiding the only entry would leave the
    user wondering where the option went."""
    options = selection_options([BUSY])
    assert "hci0" in options
    assert "in use by Home Assistant" in options["hci0"]


def test_opting_out_is_offered_first():
    options = selection_options([FREE, BUSY])
    assert next(iter(options)) == ADAPTER_NONE
