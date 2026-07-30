"""Device-type classification for cync_ble.

Mirrors cync_lan.devices.CyncDevice's is_light/is_switch logic exactly (see
that class's docstrings for the full reasoning, especially the dimmable-switch
carve-out), but as plain functions over a device-type int rather than methods
on a stateful class - cync_ble has no use for CyncDevice's TCP/MQTT/relay
fields, only the static per-type metadata cync_lan already ships.

Deliberately switch/light only, per ARCHITECTURE.md's build order: fan
controllers, plugs-as-a-special-case, sensors, thermostats and bridges are
recognised but return False from both is_light() and is_switch() here, so a
device of one of those kinds is skipped (with a log message) rather than
mis-routed to a platform that can't represent it.
"""

from __future__ import annotations

from cync_lan.metadata.model_info import (
    DeviceClassification,
    DeviceTypeInfo,
    device_type_map,
)


def type_info(dev_type: int) -> DeviceTypeInfo | None:
    return device_type_map.get(dev_type)


def is_light(dev_type: int) -> bool:
    info = type_info(dev_type)
    if info is None:
        return False
    if info.type == DeviceClassification.LIGHT:
        return True
    if info.type == DeviceClassification.SWITCH:
        # A dimmable switch is dimming a light, not a fan - Cync sells fan
        # speed control as its own dedicated "Fan Controller" product, so any
        # other dimmable switch type is safe to assume is a light dimmer.
        caps = info.capabilities
        dimmable = bool(caps and getattr(caps, "dimmable", False))
        return dimmable and not caps.fan and not caps.plug
    return False


def is_switch(dev_type: int) -> bool:
    info = type_info(dev_type)
    if info is None or info.type != DeviceClassification.SWITCH:
        return False
    return not is_light(dev_type) and not is_fan_controller(dev_type)


def is_plug(dev_type: int) -> bool:
    info = type_info(dev_type)
    if info is None or info.type != DeviceClassification.SWITCH:
        return False
    return bool(info.capabilities and info.capabilities.plug)


def is_fan_controller(dev_type: int) -> bool:
    info = type_info(dev_type)
    if info is None or info.type != DeviceClassification.SWITCH:
        return False
    return bool(info.capabilities and info.capabilities.fan)


def is_sol_lamp(dev_type: int) -> bool:
    info = type_info(dev_type)
    return bool(info and info.opcodes.sol_lamp)


def is_dimmable(dev_type: int) -> bool:
    info = type_info(dev_type)
    if info is None or info.capabilities is None:
        return False
    return bool(getattr(info.capabilities, "dimmable", False))


def model_name(dev_type: int) -> str | None:
    info = type_info(dev_type)
    return info.model_name if info else None
