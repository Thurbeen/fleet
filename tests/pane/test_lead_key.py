"""Alt+Space: the key's declaration, and which Mission Control it reaches.

The plugin is driven offline by `lead_harness.lua` over a session list, with
the `lib/fleet_home.lua` the installer renders — so the rule that picks the
installing fleet's lead by its cwd, and never by a name or a glyph, is checked
through the same string the installer writes.
"""
from __future__ import annotations

import pytest
from panekit import REAL_LUA

from harness import REPO, lib, run

# scenario -> the checkout the installer rendered, or None for no render at all.
SCENARIOS = {
    "declared": None,
    "installed-fleet": "/fleet-acme",
    "trailing-separator": "/fleet-acme/",
    "windows-path": "C:\\fleet",
    "one-lead": "/moved-clone",
    "remote-only": None,
    "several-unmatched": "/moved-clone",
    "no-lead": None,
}


@pytest.mark.skipif(not REAL_LUA, reason="requires lua")
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_alt_space_focuses_the_installing_fleets_lead(scenario, tmp_path):
    argv = [REAL_LUA, "tests/pane/lead_harness.lua", scenario]
    checkout = SCENARIOS[scenario]
    if checkout is not None:
        home = tmp_path / "fleet_home.lua"
        home.write_text(lib("install_extension.py").home_lib(checkout), encoding="utf-8")
        argv.append(str(home))
    done = run(argv, cwd=REPO)
    assert done.code == 0, done.out


def test_the_key_plugin_is_installed_beside_the_board():
    installer = lib("install_extension.py")
    assert installer.LEAD_DEST.startswith("plugins/") and installer.LEAD_DEST not in (
        installer.PANE_DEST,
        installer.BOARD_DEST,
    )
    assert installer.HOME_LIB.replace("\\", "/") == "interface/fleet_home.lua"
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "/interface/fleet_home.lua" in ignored.splitlines(), "the rendered checkout path would be tracked"
