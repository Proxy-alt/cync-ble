"""Constants for the Cync Bluetooth integration."""

from __future__ import annotations

DOMAIN = "cync_ble"
MANUFACTURER = "Savant"
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
# to it usually kills the connection on a local BlueZ adapter (see
# ARCHITECTURE.md's "iot_class is local_polling" section). So this "poll" is a
# periodic connection-health check, not a per-device state fetch: entities
# report the last command they sent (`assumed_state`) unless a subscription
# has actually taken - see SUBSCRIBE_RETRY_INTERVAL_SECONDS below. Deliberately
# unhurried - each cycle is a mesh round trip on a link also carrying commands.
DEFAULT_REFRESH_INTERVAL_SECONDS = 300

# The coordinator opportunistically tries the real, standards-compliant
# subscribe path on every fresh connection - mirroring what the vendor's own
# iOS app does (confirmed via static analysis of the real app binary: it
# calls setNotifyValue against this same characteristic and ships dedicated
# subscribeRetryCounter/subscriptionRetryTimer machinery specifically because
# that call is known to fail intermittently in production - see
# cync-lan-research's ble_ios_app_subscribe_confirmed.md). When it succeeds,
# state becomes genuinely pushed instead of assumed for the rest of that
# session. When it fails it takes the whole connection down (confirmed on
# hardware - not a soft per-call failure), so a refusal is followed by this
# long a wait before trying again, rather than retrying every reconnect and
# adding connection churn across every device on the mesh for no benefit.
SUBSCRIBE_RETRY_INTERVAL_SECONDS = 1800

# How many nodes to try connecting to before giving up for this cycle. Mesh
# relay means any single one reaches everything, so walking the whole list
# buys nothing and costs a great deal: on a real 46-node mesh an unbounded
# walk held Home Assistant's startup for four and a half minutes and starved
# a lock integration sharing the same adapter. Failing fast is better - the
# coordinator retries on its own schedule, and at startup Home Assistant
# retries a not-ready entry in the background rather than blocking on it.
MAX_CONNECT_ATTEMPTS = 4

# Hard ceiling on one refresh, including everything it does to get a link up.
# Capping the number of attempts turned out not to be enough on real
# hardware: bleak_retry_connector runs its own retry ladder inside a single
# establish_connection call, so even a handful of attempts held Home
# Assistant's startup for minutes. Nothing here needs to be fast, but it does
# need to be bounded - a refresh that gives up is retried on schedule, and at
# startup Home Assistant retries a not-ready entry in the background instead
# of waiting on it.
REFRESH_TIMEOUT_SECONDS = 45

# Mesh address 0 is broadcast - it commands every device at once. Never a valid
# target for a single entity.
BROADCAST_ADDRESS = 0
