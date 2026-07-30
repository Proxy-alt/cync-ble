"""Constants for the Cync Bluetooth integration."""

from __future__ import annotations

DOMAIN = "cync_ble"
MANUFACTURER = "Savant"

# `cync` is the cloud integration already in home-assistant/core; this is the
# local Bluetooth sibling, named per the yalexs_ble / switchbot convention. See
# ARCHITECTURE.md.

CONF_ACCOUNT_USERNAME = "account_username"
CONF_ACCOUNT_PASSWORD = "account_password"
CONF_HOME_NAME = "home_name"
CONF_MESH_NAME = "mesh_name"
CONF_MESH_PASSWORD = "mesh_password"
CONF_NODE_ADDRESS = "node_address"
CONF_DEVICES = "devices"

# Each entry in CONF_DEVICES: {"id": int, "name": str, "type": int, "mac": str}.
# "id" is the mesh device id `send()` targets; "mac" is that specific node's
# own BLE address, one of several the coordinator may connect to as the
# mesh's entry point (mesh relay means any one of them reaches every device -
# see ARCHITECTURE.md).

# No live status can be pulled on demand over this transport - the only report
# opcode (0xDC) arrives exclusively via the notification path, and subscribing
# to it kills the connection on a local BlueZ adapter (see ARCHITECTURE.md's
# "iot_class is local_polling" section). So this "poll" is a periodic
# connection-health check, not a per-device state fetch: entities report the
# last command they sent (`assumed_state`), and the coordinator's job is
# keeping one authenticated session alive to send the next one. Deliberately
# unhurried - each cycle is a mesh round trip on a link also carrying commands.
DEFAULT_REFRESH_INTERVAL_SECONDS = 300

# Mesh address 0 is broadcast - it commands every device at once. Never a valid
# target for a single entity.
BROADCAST_ADDRESS = 0
