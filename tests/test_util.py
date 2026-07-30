"""Tests for cync_ble's cync_lan-bridging helpers in util.py."""

from __future__ import annotations

from custom_components.cync_ble.util import read_exported_homes


async def test_read_exported_homes_returns_the_exported_homes_mapping(tmp_path):
    (tmp_path / "cync_mesh.yaml").write_text(
        "exported_homes:\n"
        "  My Home:\n"
        "    mac: meshname1\n"
        "    access_key: meshpass1\n"
        "    devices: {}\n"
    )
    homes = await read_exported_homes(str(tmp_path))
    assert homes == {
        "My Home": {"mac": "meshname1", "access_key": "meshpass1", "devices": {}}
    }


async def test_read_exported_homes_missing_key_returns_empty(tmp_path):
    (tmp_path / "cync_mesh.yaml").write_text("something_else: {}\n")
    homes = await read_exported_homes(str(tmp_path))
    assert homes == {}
