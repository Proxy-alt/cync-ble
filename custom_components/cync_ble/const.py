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

# Which local Bluetooth adapter, if any, to drive directly over
# HCI_CHANNEL_USER instead of going through Home Assistant's stack. See
# adapters.py - this takes the adapter away from BlueZ entirely, so it is
# opt-in, defaults to off, and must never point at the adapter Home
# Assistant is itself using.
CONF_DIRECT_ADAPTER = "direct_adapter"

# Each entry in CONF_DEVICES: {"id": int, "name": str, "type": int, "mac": str}.
# "id" is the mesh device id `send()` targets; "mac" is that specific node's
# own BLE address, one of several the coordinator may connect to as the
# mesh's entry point (mesh relay means any one of them reaches every device -
# see ARCHITECTURE.md).

# How often to harvest the mesh's state. Each cycle costs one BLE connection
# on a radio Home Assistant shares with every other Bluetooth integration, so
# this trades responsiveness against how much of that radio we occupy.
#
# Set by measurement, and by getting the diagnosis wrong before getting it
# right.
#
# At 45s every refresh failed, 100% of them, for two hours. The explanation
# recorded here at the time - that reconnect churn wedges the stack - was
# wrong. The logs said plainly what was happening: two specific nodes sat at
# the head of the candidate list, both answered "the adapter is out of
# connection slots", and bleak's default ladder spent ~36 seconds retrying
# each one nine times. Two nodes consumed the entire budget every cycle while
# forty others advertised untouched, one second old.
#
# That is fixed at the source - one bounded attempt per node, and a candidate
# order that walks every node before repeating any (see _candidate_macs) - so
# this interval is no longer doing the work of hiding it. Kept at 120s until a
# fast interval has been observed healthy for a sustained period rather than
# assumed to follow.
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
# Each is now a single connection attempt rather than bleak's default
# nine-try ladder, so trying more nodes is cheaper than trying one node
# harder - which is the right shape when any node reaches the whole mesh.
MAX_CONNECT_ATTEMPTS = 3


# When a cycle stops starting new connection attempts. Deliberately a
# deadline rather than a timeout: cancelling an in-flight
# establish_connection leaks the connection slot it reserved from Home
# Assistant's Bluetooth manager, and a leaked pool never recovers on its own
# - it survived restarts, because the first cycle after boot re-leaked it.
#
# That was the cause of two separate total outages here, once from a timeout
# around each attempt and once from the refresh-wide timeout above it. Both
# looked like the adapter being exhausted; in both cases BlueZ had zero open
# connections and a raw BleakClient connected instantly.
#
# So nothing here cancels a connection. A cycle simply stops starting new
# ones when it is out of time, and picks up where it left off next refresh.
HARVEST_DEADLINE_SECONDS = 45

# How long a node rests after serving a harvest before it is preferred again.
# The harvest ends by having its link killed, and that node then refuses the
# next connection - with only one proven node this showed up as a rock-steady
# 72s cycle, every one succeeding but only after the proven node failed first.
# Resting it pushes the walk into never-tried nodes, which is how a second
# proven node gets found and real rotation becomes possible.
NODE_REST_SECONDS = 150

# Consecutive failed harvests before state polling gives up and the
# integration falls back to command-only.
#
# The fallback is not a degraded mode invented for the occasion - it is what
# this integration did before harvesting existed, and it worked: commands
# connect on demand, entities report what they last sent (`assumed_state`),
# and the radio is untouched between user actions. Losing state readback is a
# real loss; holding a shared adapter for ~72s every couple of minutes to keep
# failing at it is worse, and starves whatever else needs that radio.
#
# Confirmed necessary on real hardware: on a Pi's built-in Broadcom adapter
# shared with a lock and an iBeacon integration, harvesting oscillated between
# working perfectly and failing every single cycle for hours.
HARVEST_FAILURE_LIMIT = 3

# How long before a fallen-back integration tries harvesting again. Conditions
# genuinely can improve - an ESPHome proxy appearing is the obvious case, since
# it brings its own GATT client and its own uncontended connection slots - so
# this is not permanent. Long enough that a mesh which cannot support it is not
# repeatedly disturbed.
HARVEST_RETRY_AFTER_SECONDS = 3600

# Mesh address 0 is broadcast - it commands every device at once. Never a valid
# target for a single entity.
BROADCAST_ADDRESS = 0
