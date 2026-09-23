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
    assert stubs.calls("thurbox-cli", f"plugin install {pane} --as plugins/91_fleet_queue.lua --text")
    assert ui.read_bytes() == STOCK.read_bytes()
    assert not list(ui.parent.glob("layout.lua.bak-*"))
    expect(done.out, "NOT PLACED", "uv run fleet place-pane", str(ui), "thurbox-cli extension status fleet")
    assert not run(["git", "status", "--porcelain"], cwd=fleet).stdout, "a rendered file is tracked"


def test_a_placed_pane_is_reported_placed(stubs, tmp_path):
    ui = thurbox(stubs)
    ui.write_text(STOCK.read_text(encoding="utf-8") + '\n-- x\nlocal _ = { slot = "fleetqueue" }\n', encoding="utf-8")
    done = install(clone(tmp_path))
    assert done.code == 0, done.out
    expect(done.out, "installed and placed. Press F3")


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
