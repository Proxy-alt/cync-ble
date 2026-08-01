"""Choosing a Bluetooth adapter to drive directly.

Background, because this option is unusual and the hazard is real.

This firmware refuses the CCCD write BlueZ performs when subscribing, and
BlueZ responds by destroying the connection - captured on the wire, with
notifications already arriving. Driving an adapter directly over
`HCI_CHANNEL_USER` avoids the problem entirely, because the application
becomes the ATT client and no descriptor write is ever needed.

The catch is what `HCI_CHANNEL_USER` means: the adapter is taken away from
BlueZ **completely** for as long as it is held. Everything else using that
radio - other integrations, Home Assistant's own scanner - loses it. So this
must never be pointed at the adapter Home Assistant is already using, and
this module exists mainly to make that mistake hard to commit.

Hence: opt-in, off by default, and taking an adapter Home Assistant is
already using requires an explicit second confirmation naming what will
break. It is allowed - it is the user's machine, and dedicating the only
adapter is a legitimate choice - but it should never happen by accident from
a dropdown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Sentinel for "don't do this" - the default, and the value that keeps the
# integration on Home Assistant's own Bluetooth stack.
ADAPTER_NONE = "none"


@dataclass(frozen=True)
class AdapterChoice:
    """One adapter, and whether it can be dedicated to direct mode."""

    adapter: str
    address: str
    label: str
    in_use_by_hass: bool

    @property
    def selectable(self) -> bool:
        return not self.in_use_by_hass


def _hass_adapters(hass: HomeAssistant) -> set[str]:
    """Adapters currently backing a Home Assistant scanner.

    Read from the live scanners rather than from config, because what
    matters is which radio would actually be pulled out from under
    something, not which one was configured at some point.
    """
    in_use: set[str] = set()
    try:
        for scanner in bluetooth.async_current_scanners(hass):
            adapter = getattr(scanner, "adapter", None)
            if adapter:
                in_use.add(adapter)
    except Exception:
        _LOGGER.debug("Could not read current scanners", exc_info=True)
        return set()
    return in_use


async def async_list_adapters(hass: HomeAssistant) -> list[AdapterChoice]:
    """Every local Bluetooth adapter, annotated with whether it is free.

    Deliberately returns adapters Home Assistant is using as well, marked
    unselectable, so the UI can explain rather than silently omit.
    """
    busy = _hass_adapters(hass)

    manager = bluetooth.get_adapters()
    # get_adapters() only constructs the backend - `adapters` is empty until
    # refresh() has done the D-Bus/sysfs work. Skipping this is why the
    # picker showed nothing but "use Home Assistant's Bluetooth": the list
    # was always empty, and a blanket `except` further down meant it failed
    # silently rather than saying so.
    try:
        await manager.refresh()
    except Exception:
        _LOGGER.exception("Could not enumerate local Bluetooth adapters")
        return []

    choices: list[AdapterChoice] = []
    for adapter, details in sorted(manager.adapters.items()):
        address = details.get("address", "") if details else ""
        try:
            label = bluetooth.adapter_title(adapter, details)
        except Exception:
            label = f"{adapter} ({address})" if address else adapter
        choices.append(
            AdapterChoice(
                adapter=adapter,
                address=address,
                label=label,
                in_use_by_hass=adapter in busy,
            )
        )
    _LOGGER.debug(
        "Found %d local Bluetooth adapter(s); %d in use by Home Assistant",
        len(choices),
        sum(1 for c in choices if c.in_use_by_hass),
    )
    return choices


def selection_options(choices: list[AdapterChoice]) -> dict[str, str]:
    """{value: label} for a picker, with the opt-out first.

    An adapter Home Assistant is using appears, but says so in its label and
    is filtered out of the accepted values by the caller - showing it with a
    reason is friendlier than an empty list on a single-adapter machine.
    """
    options = {ADAPTER_NONE: "Use Home Assistant's Bluetooth (recommended)"}
    for choice in choices:
        if choice.selectable:
            options[choice.adapter] = choice.label
        else:
            options[choice.adapter] = f"{choice.label} - in use by Home Assistant"
    return options


def is_known(choices: list[AdapterChoice], adapter: str) -> bool:
    """Whether this is an adapter that actually exists right now.

    A stored choice can name a dongle that has since been unplugged, so
    this is checked on submit rather than trusted from the config entry.
    """
    if adapter == ADAPTER_NONE:
        return True
    return any(c.adapter == adapter for c in choices)


def needs_takeover_confirmation(
    choices: list[AdapterChoice], adapter: str
) -> AdapterChoice | None:
    """The adapter, if choosing it would take Bluetooth from Home Assistant.

    Returns None when the choice is harmless - either opting out, or an
    adapter nothing else is using. A non-None return is the caller's cue to
    ask a second time, with the consequence spelled out.
    """
    if adapter == ADAPTER_NONE:
        return None
    for choice in choices:
        if choice.adapter == adapter and choice.in_use_by_hass:
            return choice
    return None
