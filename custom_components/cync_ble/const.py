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

# State arrives as notifications, so this is a backstop rather than the primary
# path - see ARCHITECTURE.md. Deliberately unhurried: a refresh is a mesh round
# trip, and the mesh is also carrying commands.
#
# An earlier revision polled because notifications were believed refused. They
# are not: BlueZ's StartNotify is rejected, but writing 0x01 to the notification
# characteristic's value turns reporting on regardless, which is what
# python-dimond has always done.
DEFAULT_REFRESH_INTERVAL_SECONDS = 300

# Mesh address 0 is broadcast - it commands every device at once. Never a valid
# target for a single entity.
BROADCAST_ADDRESS = 0
