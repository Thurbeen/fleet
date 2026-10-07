"""Exercise thurbox's actual occupied-slot verdict in an isolated interface."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from harness import REPO, run, write

# Capture before the autouse fixture places the test's CLI stubs on PATH.
NATIVE_CLI = shutil.which("thurbox-cli")
if NATIVE_CLI:
    version = subprocess.run([NATIVE_CLI, "--version"], capture_output=True, text=True,
                             encoding="utf-8", timeout=10)
    if version.returncode or not version.stdout.startswith("thurbox-cli "):
        NATIVE_CLI = None  # Nested isolation runs expose a script stub instead.


def verify_placement(tmp_path: Path, column: Path, overlay: bool = True):
    ui = tmp_path / "ui"
    for name in ("hover", "panels", "theme", "ui", "widgets", "scroll"):
        # Only Lua dependencies are inert; placement is the real Rust kernel.
        write(ui / "lib" / f"{name}.lua", "return {}\n")
    for name in ("fleet_reader", "fleet_board"):
        write(ui / "lib" / f"{name}.lua", (REPO / "interface" / f"{name}.lua").read_text(encoding="utf-8"))
    write(ui / "plugins" / "column.lua", column.read_text(encoding="utf-8"))
    if overlay:
        write(ui / "plugins" / "board.lua", (REPO / "interface" / "fleet_kanban.lua").read_text(encoding="utf-8"))
    write(ui / "plugins" / "center.lua", 'return {name="empty",slot="center",render=function() return nil end}\n')
    env = {
        "HOME": str(tmp_path / "home"),
        "THURBOX_CONFIG_DIR": str(tmp_path / "config"),
        "THURBOX_DATA_DIR": str(tmp_path / "data"),
        "THURBOX_UI_DIR": str(ui),
    }
    for name in ("home", "config", "data"):
        (tmp_path / name).mkdir(exist_ok=True)
    write(ui / "layout.lua", 'return function(ctx) return {slot="center"} end\n')
    unplaced = run([NATIVE_CLI, "plugin", "check", "--text"], **env)
    assert unplaced.code == 1, unplaced.out
    assert 'nothing places slot "fleetqueue"' in unplaced.out, unplaced.out
    write(ui / "layout.lua", 'return function(ctx) return {type="box",axis="horizontal",children={{slot="center"},{slot="fleetqueue"}}} end\n')
    placed = run([NATIVE_CLI, "plugin", "check", "--text"], **env)
    assert placed.code == 0, placed.out
    assert "✓ loads" in placed.out and "nothing places" not in placed.out, placed.out
    assert not (tmp_path / "data" / "sessions").exists()


@pytest.mark.skipif(not NATIVE_CLI, reason="native thurbox-cli is not installed")
def test_real_kernel_rejects_unplaced_column_and_accepts_placed_column(tmp_path):
    verify_placement(tmp_path, REPO / "interface" / "fleet_queue.lua")
