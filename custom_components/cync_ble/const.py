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

# How often to harvest the mesh's state - see HARVEST_WINDOW_SECONDS. Each
# cycle costs one sacrificial BLE connection on a radio Home Assistant shares
# with everything else, so this is deliberately unhurried rather than as fast
# as the transport would allow.
DEFAULT_REFRESH_INTERVAL_SECONDS = 300

# Every refresh takes a "harvest": one deliberately sacrificial connection
# that subscribes, collects the status sweep the mesh emits, and loses the
# link about 30s later when the firmware refuses the CCCD write.
#
# That refusal is not a failure to work around - it is measured, invariant
# behaviour (14 attempts, always GATT UNLIKELY_ERROR, never once accepted), so
# retrying the way the vendor's iOS app does buys nothing. What the attempt
# DOES buy is the sweep: 17-19 notifications decoding to status for 34-38 of
# this mesh's 46 devices, on every single try, in the seconds before the
# rejection lands. That is a full picture of the house for the price of one
# connection, and it is what makes this integration genuinely local_polling
# rather than a thing that only assumes what it last sent.
#
# Confirmed correct end to end: a device driven to 60 then 25 then off read
# back as exactly 60, 25 and 0.
HARVEST_WINDOW_SECONDS = 25

# How many nodes to try before giving up on reaching the mesh this cycle.
# Mesh relay means any single one reaches everything, so walking the whole
# list buys nothing and costs a lot: on a real 46-node mesh an unbounded walk
# held Home Assistant's startup for four and a half minutes and starved a
# lock integration sharing the adapter.
MAX_CONNECT_ATTEMPTS = 4

# Hard ceiling on one refresh, harvest included. Capping the number of
# attempts was not enough on real hardware - bleak_retry_connector runs its
# own retry ladder inside a single establish_connection call, so a handful of
# attempts is a handful of ladders. Must comfortably exceed
# HARVEST_WINDOW_SECONDS plus the connections around it.
REFRESH_TIMEOUT_SECONDS = 90

# Mesh address 0 is broadcast - it commands every device at once. Never a valid
# target for a single entity.
BROADCAST_ADDRESS = 0
