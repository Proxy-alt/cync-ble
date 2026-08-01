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

Hence: opt-in, off by default, and adapters currently backing a Home
Assistant scanner are offered as unselectable rather than silently hidden -
someone with one adapter should be told why they cannot use this, not left
wondering where the option went.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

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
        return set()
    return in_use


async def async_list_adapters(hass: HomeAssistant) -> list[AdapterChoice]:
    """Every local Bluetooth adapter, annotated with whether it is free.

    Deliberately returns adapters Home Assistant is using as well, marked
    unselectable, so the UI can explain rather than silently omit.
    """
    busy = _hass_adapters(hass)

    def _collect() -> list[AdapterChoice]:
        manager = bluetooth.get_adapters()
        found = getattr(manager, "adapters", None) or {}
        choices: list[AdapterChoice] = []
        for adapter, details in sorted(found.items()):
            address = details.get("address", "") if isinstance(details, dict) else ""
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
        return choices

    # get_adapters() touches D-Bus/sysfs, so it does not belong on the loop.
    try:
        return await hass.async_add_executor_job(_collect)
    except Exception:
        return []


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


def is_selectable(choices: list[AdapterChoice], adapter: str) -> bool:
    """Whether a stored/submitted adapter may actually be taken over."""
    if adapter == ADAPTER_NONE:
        return True
    return any(c.adapter == adapter and c.selectable for c in choices)
