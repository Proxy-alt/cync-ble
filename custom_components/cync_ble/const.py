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

# How often to harvest the mesh's state. Each cycle costs one BLE connection
# on a radio Home Assistant shares with every other Bluetooth integration, so
# this trades responsiveness against how much of that radio we occupy.
#
# Set by measurement, and by getting it wrong twice.
#
# Shortening the harvest window (see HARVEST_WINDOW_SECONDS) cut ~21s from
# every cycle and made 45s look reachable. At 45s every refresh instead hit
# its own ceiling and failed - not slowly degrading, but 100% failure once it
# started, with no progress logged at all before the timeout.
#
# The tempting explanation, that connecting simply costs too much, is wrong: a
# healthy cycle here takes **8-10 seconds**, nearly all of it connecting, so
# 45s should have been ~20% duty. What actually happens is that reconnecting
# to the same node that often produces churn the stack does not recover from -
# a failed refresh appears to leave state behind that makes the next connect
# fail too, so it compounds instead of degrading gently. Whether that is
# BlueZ's teardown lagging or the device's own connection handling is **not
# established**, so this is set from what was observed to work rather than
# from a mechanism that is understood.
#
# 120s runs cleanly, leaves the radio free ~90% of the time, and is still
# well over twice as responsive as the 300s this started at. Going faster is
# plausibly possible with more careful teardown between cycles; it is not a
# matter of simply lowering this number.
DEFAULT_REFRESH_INTERVAL_SECONDS = 120

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
#
# The window is short because the sweep is: measured on a 46-node mesh, all
# 38 responding devices reported within **1.1 seconds** of the enable-write
# (50% inside 0.5s). The first implementation waited 25s for data that had
# entirely arrived in the first second, which held the shared adapter ~23x
# longer than necessary and is what made a fast poll interval impossible.
#
# Kept at several times the observed figure rather than trimmed to it - a
# quiet mesh or a more distant relay node has room, and the cost of being
# generous here is now small.
HARVEST_WINDOW_SECONDS = 4

# How many nodes to try before giving up on reaching the mesh this cycle.
# Mesh relay means any single one reaches everything, so walking the whole
# list buys nothing and costs a lot: on a real 46-node mesh an unbounded walk
# held Home Assistant's startup for four and a half minutes and starved a
# lock integration sharing the adapter.
MAX_CONNECT_ATTEMPTS = 4

# Hard ceiling on one refresh, harvest included. Capping the number of
# attempts was not enough on real hardware - bleak_retry_connector runs its
# own retry ladder inside a single establish_connection call, so a handful of
# attempts is a handful of ladders.
#
# Deliberately below DEFAULT_REFRESH_INTERVAL_SECONDS: a refresh that outlives
# its own interval would have the next one queued behind it permanently, and
# the adapter would never be handed back. Also comfortably above a healthy
# cycle (~20s, nearly all of it connecting) - set too close to that, as it
# briefly was, and normal connection variance reads as failure.
REFRESH_TIMEOUT_SECONDS = 75

# `disconnect()` returning is not the same as the stack having finished
# tearing the link down. Starting the next connect while the previous one is
# still unwinding is the churn a 45s poll interval collapsed under - 100%
# failure once it began, rather than gradual degradation. So teardown waits
# for the client to actually report itself disconnected, then pauses briefly
# on top.
#
# The settle pause is empirical, not derived: BlueZ exposes no "fully torn
# down" signal to wait on, so this is a guess with a margin rather than a
# guarantee.
DISCONNECT_CONFIRM_TIMEOUT_SECONDS = 10.0
DISCONNECT_SETTLE_SECONDS = 2.0

# Mesh address 0 is broadcast - it commands every device at once. Never a valid
# target for a single entity.
BROADCAST_ADDRESS = 0
