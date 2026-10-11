"""The extension: rendered from extension.toml.in and FLEET.md, then installed.

`--render-only` touches nothing thurbox owns and needs no thurbox-cli. A real
install installs the extension and the pane, never places the pane, and exits
non-zero when the live lead still opens another directory — thurbox reuses a
session by name and never moves it.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import pytest
from panekit import STOCK, clone, sessions, thurbox

from harness import PYTHON, REPO, expect, queue_module, refute, run, run_fleet, write


def render_only(dest: Path, **env):
    return run_fleet("install-extension", "--render-only", str(dest), **env)


def voice(path: Path, operator: str, lead: str) -> Path:
    write(path, f"OPERATOR_NAME={operator}\nASSISTANT_NAME={lead}\n")
    return path


def settings(name: str) -> Path:
    return Path(os.environ["FLEET_GLYPH_ROOT"]) / "orchestration" / name


def install(fleet: Path, *args: str):
    return run([*PYTHON, str(fleet / "scripts" / "lib" / "install_extension.py"), *args],
               cwd=fleet, FLEET_VOICE_CONF=None)


def test_render_only_writes_both_files_and_installs_nothing(stubs, tmp_path):
    thurbox(stubs)
    done = render_only(tmp_path)
    assert done.code == 0, done.out
    expect(done.out, "--render-only: nothing was installed.")
    assert not stubs.calls("thurbox-cli")
    manifest = tomllib.loads((tmp_path / "extension.toml").read_text(encoding="utf-8"))
    lead = manifest["sessions"][0]
    assert lead["name"] == "📡 Mission Control"
    assert lead["agent"] == "claude"
    assert Path(lead["repo_path"]).resolve() == REPO.resolve()
    payload = (tmp_path / "FLEET.rendered.md").read_text(encoding="utf-8")
    expect(payload, "Slayer", "VEGA")
    refute(payload, "@OPERATOR_NAME@", "@ASSISTANT_NAME@")


def test_render_only_needs_no_thurbox_cli(tmp_path):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    done = render_only(tmp_path, PATH=str(empty))
    assert done.code == 0, done.out


def test_a_hard_link_to_the_rendered_payload_sees_a_re_render(tmp_path):
    """Without privilege, thurbox on Windows makes `[[symlinks]]` into hard
    links, and a payload replaced by a new file leaves them on the old names."""
    first = voice(tmp_path / "first.conf", "Ripley", "Mother")
    assert render_only(tmp_path, FLEET_VOICE_CONF=str(first)).code == 0
    link = tmp_path / "CLAUDE.md"
    os.link(tmp_path / "FLEET.rendered.md", link)
    second = voice(tmp_path / "second.conf", "Dallas", "Ash")
    assert render_only(tmp_path, FLEET_VOICE_CONF=str(second)).code == 0
    text = link.read_text(encoding="utf-8")
    expect(text, "Dallas", "Ash")
    refute(text, "Ripley")


def test_glyphs_off_renders_the_one_cell_mark(tmp_path):
    write(settings("session-glyphs.conf"), "GLYPHS=off\nLEAD_GLYPH_ON=📡\nLEAD_GLYPH_OFF=⌖\n")
    assert render_only(tmp_path).code == 0
    manifest = tomllib.loads((tmp_path / "extension.toml").read_text(encoding="utf-8"))
    assert manifest["sessions"][0]["name"] == "⌖ Mission Control"


def test_a_glyph_setting_that_is_neither_on_nor_off_is_refused_and_nothing_written(tmp_path):
    write(settings("session-glyphs.conf"), "GLYPHS=sideways\nLEAD_GLYPH_ON=📡\n")
    done = render_only(tmp_path)
    assert done.code == 1, done.out
    expect(done.out, "neither 'on' nor 'off'")
    assert not (tmp_path / "extension.toml").exists()


def test_the_agent_setting_is_rendered_and_a_name_that_is_not_bare_is_refused(tmp_path):
    write(settings("agent.conf"), "AGENT=opencode\n")
    assert render_only(tmp_path).code == 0
    manifest = tomllib.loads((tmp_path / "extension.toml").read_text(encoding="utf-8"))
    assert manifest["sessions"][0]["agent"] == "opencode"
    write(settings("agent.conf"), "AGENT=open code\n")
    done = render_only(tmp_path)
    assert done.code == 1, done.out
    expect(done.out, "not a bare agent name")


def test_the_manifest_reads_a_conf_by_the_rule_every_other_reader_uses(tmp_path):
    """One grammar for every `orchestration/*.conf`, and it is `agent_settings.read_conf`:
    the LAST occurrence of a key wins, spaces around `=` are not part of it. The
    installer kept a reader of its own that took the FIRST line, so a line
    appended below a stale one — the edit a first-match read waves through —
    spawned every worker as one agent and rendered the lead as another."""
    write(settings("agent.conf"), "AGENT=claude\n# the line above is stale\nAGENT = opencode\n")
    assert render_only(tmp_path).code == 0
    manifest = tomllib.loads((tmp_path / "extension.toml").read_text(encoding="utf-8"))
    assert manifest["sessions"][0]["agent"] == "opencode"
    # The same file, read by the queue for every spawn, gives the same answer.
    assert queue_module("print(q.configured_agent())").strip() == "opencode"


def test_usage_errors_exit_1(tmp_path):
    expect(run_fleet("install-extension", "--render-only").out, "--render-only takes a directory")
    missing = run_fleet("install-extension", "--render-only", str(tmp_path / "nope"))
    assert missing.code == 1
    expect(missing.out, "not a directory")
    assert run_fleet("install-extension", "--bogus").code == 1


def test_an_install_needs_thurbox_cli(tmp_path):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    done = run_fleet("install-extension", PATH=str(empty))
    assert done.code == 1, done.out
    expect(done.out, "thurbox-cli not found")


def test_an_install_installs_the_extension_and_the_pane_and_places_nothing(stubs, tmp_path):
    ui = thurbox(stubs)
    fleet = clone(tmp_path)
    done = install(fleet)
    assert done.code == 0, done.out
    assert stubs.calls("thurbox-cli", f"extension install {fleet}")
    pane = fleet / "interface" / "fleet_queue.lua"
    assert stubs.calls("thurbox-cli", f"plugin install {pane} --as plugins/92_fleet_queue.lua --text")
    board = fleet / "interface" / "fleet_board.lua"
    assert stubs.calls("thurbox-cli", f"plugin install {board} --as lib/fleet_board.lua --text")
    reader = fleet / "interface" / "fleet_reader.lua"
    overlay = fleet / "interface" / "fleet_kanban.lua"
    assert stubs.calls("thurbox-cli", f"plugin install {reader} --as lib/fleet_reader.lua --text")
    assert stubs.calls("thurbox-cli", f"plugin install {overlay} --as plugins/91_fleet_queue.lua --text")
    key = fleet / "interface" / "fleet_lead.lua"
    home = fleet / "interface" / "fleet_home.lua"
    assert stubs.calls("thurbox-cli", f"plugin install {key} --as plugins/93_fleet_lead.lua --text")
    assert stubs.calls("thurbox-cli", f"plugin install {home} --as lib/fleet_home.lua --text")
    assert str(fleet).replace("\\", "\\\\") in home.read_text(encoding="utf-8")
    expect(done.out, "Ctrl+X focuses this fleet's Mission Control", "installed last")
    assert ui.read_bytes() == STOCK.read_bytes()
    assert not list(ui.parent.glob("layout.lua.bak-*"))
    expect(done.out, "NOT PLACED", "uv run fleet place-pane", str(ui), "thurbox-cli extension status fleet")
    assert not run(["git", "status", "--porcelain"], cwd=fleet).stdout, "a rendered file is tracked"


def test_a_slot_thurbox_kept_is_retired_so_each_key_has_one_plugin(stubs, tmp_path):
    """The board's slot once held the combined column; a copy thurbox will not overwrite must not outlive the install."""
    ui = thurbox(stubs).parent
    fleet = clone(tmp_path)
    column = (fleet / "interface" / "fleet_queue.lua").read_bytes()
    stale = column + b"-- edited in place\n"
    write(ui / "plugins" / "91_fleet_queue.lua", stale.decode("utf-8"))
    write(stubs.root / "delivered" / "plugins" / "91_fleet_queue.lua", column.decode("utf-8"))
    done = install(fleet)
    assert done.code == 0, done.out
    board = (fleet / "interface" / "fleet_kanban.lua").read_bytes()
    assert (ui / "plugins" / "91_fleet_queue.lua").read_bytes() == board, done.out
    claimants = [p.name for p in sorted((ui / "plugins").glob("*.lua")) if b'"fleetqueue.toggle"' in p.read_bytes()]
    assert claimants == ["92_fleet_queue.lua"], claimants
    assert [p.name for p in (ui / "plugins").glob("*.lua") if b'"fleetqueue.board"' in p.read_bytes()] == ["91_fleet_queue.lua"]
    backups = list((ui / "plugins").glob("91_fleet_queue.lua.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == stale, "the edit thurbox protected was lost"
    expect(done.out, "held a file thurbox would not replace", str(backups[0]), "The Kanban board is installed")
    # A second run finds its own delivery and moves nothing.
    again = install(fleet)
    assert again.code == 0, again.out
    assert len(list((ui / "plugins").glob("*.bak-*"))) == 1
    refute(again.out, "would not replace")


DELIVERIES = [
    ("lib/fleet_reader.lua", "fleet_reader.lua"),
    ("lib/fleet_board.lua", "fleet_board.lua"),
    ("lib/fleet_home.lua", "fleet_home.lua"),
    ("plugins/92_fleet_queue.lua", "fleet_queue.lua"),
    ("plugins/91_fleet_queue.lua", "fleet_kanban.lua"),
    ("plugins/93_fleet_lead.lua", "fleet_lead.lua"),
]


@pytest.mark.parametrize("destination,source", DELIVERIES)
@pytest.mark.parametrize("recorded", [True, False], ids=["kept", "never-recorded"])
def test_every_delivery_thurbox_declines_is_set_aside_and_made(stubs, tmp_path, destination, source, recorded):
    """`kept` and exit 0 for an edited copy; a refusal for a file thurbox never recorded. Neither is an install."""
    ui = thurbox(stubs).parent
    fleet = clone(tmp_path)
    write(ui / destination, "-- somebody's\n")
    if recorded:
        write(stubs.root / "delivered" / destination, "-- what thurbox last wrote\n")
    done = install(fleet)
    assert done.code == 0, done.out
    assert (ui / destination).read_bytes() == (fleet / "interface" / source).read_bytes(), done.out
    backups = list((ui / destination).parent.glob("*.bak-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "-- somebody's\n", done.out
    expect(done.out, f"{destination} held a file thurbox would not replace", str(backups[0]))
    refute(done.out, "could not install")


@pytest.mark.parametrize("recorded", [True, False], ids=["recorded", "never-recorded"])
def test_an_install_that_fails_for_another_reason_leaves_a_working_slot_alone(stubs, tmp_path, recorded):
    """Only `kept` and a refusal of an unrecorded file move anything; a source thurbox cannot read does not."""
    ui = thurbox(stubs).parent
    fleet = clone(tmp_path)
    assert install(fleet).code == 0
    slot = ui / "plugins" / "91_fleet_queue.lua"
    if recorded:
        working = slot.read_bytes()
    else:
        working = b"-- somebody's\n"
        slot.write_bytes(working)
        (stubs.root / "delivered" / "plugins" / "91_fleet_queue.lua").unlink()
    write(stubs.root / "refuse-install" / "fleet_kanban.lua", "")
    done = install(fleet)
    assert slot.read_bytes() == working, done.out
    assert not list((ui / "plugins").glob("*.bak-*")), done.out
    expect(done.out, "could not install the TUI plugin")
    refute(done.out, "installed in its place")
    if recorded:
        assert (stubs.root / "delivered" / "plugins" / "91_fleet_queue.lua").exists(), "thurbox's record was dropped"
        assert not stubs.calls("thurbox-cli", "plugin remove plugins/91_fleet_queue.lua --text")


def test_a_plugin_the_operator_deleted_stays_deleted_and_is_named(stubs, tmp_path):
    ui = thurbox(stubs).parent
    fleet = clone(tmp_path)
    assert install(fleet).code == 0
    (ui / "plugins" / "92_fleet_queue.lua").unlink()
    done = install(fleet)
    assert done.code == 0, done.out
    assert not (ui / "plugins" / "92_fleet_queue.lua").exists()
    expect(done.out, "plugins/92_fleet_queue.lua is not installed", "thurbox-cli plugin remove plugins/92_fleet_queue.lua")
    assert (ui / "plugins" / "93_fleet_lead.lua").exists(), "the deliveries after it were skipped"


def placed(stubs) -> None:
    ui = thurbox(stubs)
    ui.write_text(STOCK.read_text(encoding="utf-8") + '\n-- x\nlocal _ = { slot = "fleetqueue" }\n', encoding="utf-8")


def test_a_key_another_plugin_wins_is_reported_and_not_announced(stubs, tmp_path):
    placed(stubs)
    write(stubs.root / "check-claims.txt",
          "  ! plugins/93_fleet_lead.lua — ctrl+x is claimed by both review.open and fleetlead.focus; "
          "review.open wins (declared in review/plugins/40_review.lua)\n"
          "  ! files/90_files.lua — f3 is claimed by both fleetqueue.toggle and files.focus; fleetqueue.toggle wins\n")
    done = install(clone(tmp_path))
    assert done.code == 0, done.out
    expect(done.out, "ctrl+x is claimed by both review.open and fleetlead.focus; review.open wins",
           "Bind `fleetlead.focus`", "The Kanban board is installed", "installed and placed.")
    refute(done.out, "Ctrl+X focuses this fleet's Mission Control (", "Bind `fleetqueue.toggle`", "Press F3", "NOT KNOWN")


def test_a_board_key_another_plugin_wins_is_not_announced_either(stubs, tmp_path):
    placed(stubs)
    write(stubs.root / "check-claims.txt",
          "  ! plugins/91_fleet_queue.lua — alt+k is claimed by both other.go and fleetqueue.board; other.go wins\n")
    done = install(clone(tmp_path))
    expect(done.out, "Bind `fleetqueue.board`", "Ctrl+X focuses this fleet's Mission Control (")
    refute(done.out, "The Kanban board is installed", "Press F3")


def test_a_key_two_fleet_files_both_claim_is_reported(stubs, tmp_path):
    placed(stubs)
    write(stubs.root / "check-claims.txt",
          "  ! a, b — f3 is claimed by both fleetqueue.toggle and fleetqueue.toggle; fleetqueue.toggle wins\n")
    done = install(clone(tmp_path))
    expect(done.out, "Bind `fleetqueue.toggle`")


def test_an_unplaced_column_leaves_the_keys_unchecked_and_says_so(stubs, tmp_path):
    """thurbox names a chord claimed twice only once every pane is placed, so silence then is not a clean bill."""
    thurbox(stubs)
    done = install(clone(tmp_path))
    expect(done.out, "NOT PLACED", "NOT KNOWN yet")


def test_a_placed_pane_is_reported_placed(stubs, tmp_path):
    ui = thurbox(stubs)
    ui.write_text(STOCK.read_text(encoding="utf-8") + '\n-- x\nlocal _ = { slot = "fleetqueue" }\n', encoding="utf-8")
    done = install(clone(tmp_path))
    assert done.code == 0, done.out
    expect(done.out, "installed and placed. Press F3")
    refute(done.out, "NOT KNOWN")


def test_a_lead_opening_another_directory_is_a_non_zero_exit_naming_the_remedy(stubs, tmp_path):
    thurbox(stubs)
    fleet = clone(tmp_path)
    sessions(stubs, json.dumps([{"name": "📡 Mission Control", "cwd": str(tmp_path / "old-clone")}]))
    done = install(fleet)
    assert done.code == 1, done.out
    expect(done.stderr, "still opens a different directory", "thurbox-cli extension deactivate fleet",
           "uv run fleet install-extension")


def test_a_lead_opening_this_clone_is_not_a_mismatch(stubs, tmp_path):
    thurbox(stubs)
    fleet = clone(tmp_path)
    sessions(stubs, json.dumps([{"name": "📡 Mission Control", "cwd": str(fleet) + os.sep}]))
    done = install(fleet)
    assert done.code == 0, done.out
    refute(done.out, "different directory")


def test_a_flipped_glyph_names_the_lead_that_is_still_running(stubs, tmp_path):
    """The new name is one no session answers to yet, so the directory check
    has nothing to compare and would say nothing."""
    thurbox(stubs)
    fleet = clone(tmp_path)
    write(settings("session-glyphs.conf"), "GLYPHS=off\nLEAD_GLYPH_ON=📡\nLEAD_GLYPH_OFF=⌖\n")
    sessions(stubs, json.dumps([{"name": "📡 Mission Control", "cwd": str(fleet)}]))
    done = install(fleet)
    assert done.code == 0, done.out
    expect(done.stderr, "note: the running lead is still '📡 Mission Control'")


@pytest.mark.parametrize("name", ["R|D", "a&b"])
def test_a_name_the_render_cannot_carry_is_refused(tmp_path, name):
    conf = voice(tmp_path / "voice.conf", name, "Mother")
    done = render_only(tmp_path, FLEET_VOICE_CONF=str(conf))
    assert done.code == 1, done.out
    assert not (tmp_path / "FLEET.rendered.md").exists()
