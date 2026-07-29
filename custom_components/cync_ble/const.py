"""Constants for the Cync Bluetooth integration."""

from __future__ import annotations

DOMAIN = "cync_ble"

# `cync` is the cloud integration already in home-assistant/core; this is the
# local Bluetooth sibling, named per the yalexs_ble / switchbot convention. See
# ARCHITECTURE.md.

CONF_MESH_NAME = "mesh_name"
CONF_MESH_PASSWORD = "mesh_password"
CONF_NODE_ADDRESS = "node_address"
CONF_DEVICES = "devices"

# Inbound status notifications are refused by at least one firmware (it declares
# `notify`, rejects the CCCD write, then drops the link), so state is polled.
# Kept deliberately unhurried: every poll is a mesh round trip, and the mesh is
# also carrying commands.
DEFAULT_SCAN_INTERVAL_SECONDS = 60

# Mesh address 0 is broadcast - it commands every device at once. Never a valid
# target for a single entity.
BROADCAST_ADDRESS = 0
