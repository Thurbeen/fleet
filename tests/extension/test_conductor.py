"""The conductor: a second extension-managed session, and only on request.

`CONDUCTOR=on` in `orchestration/fleet.conf` keeps the conductor block of
`extension.toml.in` — a second `[[sessions]]`, bound to `CONDUCTOR_AGENT` and
wearing the `conductor` kind's mark — and renders the directory that session
opens, with its own standing context. These are the claims:

  * unset, the manifest carries nothing of the conductor: the block is the
    only place the template mentions it, and it is cut whole, so the render is
    the one fleet made before the conductor existed;
  * on, the manifest declares it AFTER the lead, so the lead — the first
    `[[sessions]]`, which every reader takes as the lead — is not renamed;
  * its agent and its mark are the settings', with their fallbacks;
  * its directory hands it CONDUCTOR.md and not the lead's FLEET.md, keeps the
    checkout's startup hooks, and is removed again when the setting goes.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path

from panekit import clone, sessions, thurbox

from harness import PYTHON, REPO, expect, git, refute, run, run_fleet, write

LEAD = "📡 Mission Control"
BLOCK = re.compile(r"^#>>> conductor\b.*?^#<<< conductor\n", re.M | re.S)
CONDUCTOR_H1 = "# CONDUCTOR.md — standing context for the conductor session"


def settings(name: str) -> Path:
    return Path(os.environ["FLEET_NAME_ROOT"]) / "orchestration" / name


def conf(name: str, text: str) -> None:
    write(settings(name), text)


def render(dest: Path):
    done = run_fleet("install-extension", "--render-only", str(dest))
    return done


def manifest(dest: Path) -> dict:
    return tomllib.loads((dest / "extension.toml").read_text(encoding="utf-8"))


def home(dest: Path) -> Path:
    return dest / "orchestration" / "conductor"


def test_the_template_mentions_the_conductor_only_inside_its_block():
    """What makes "off renders what it always did" true: cut the block and no
    word of the conductor is left to render."""
    template = (REPO / "extension.toml.in").read_text(encoding="utf-8")
    assert len(BLOCK.findall(template)) == 1
    assert "onductor" not in BLOCK.sub("", template)


def test_unset_renders_one_session_and_no_directory(tmp_path):
    off, on = tmp_path / "off", tmp_path / "on"
    off.mkdir()
    on.mkdir()
    assert render(off).code == 0
    text = (off / "extension.toml").read_text(encoding="utf-8")
    # The checkout's own path is substituted in, and may hold any word.
    refute(text.replace(str(REPO).replace("\\", "\\\\"), "<checkout>"), "onductor", "#>>>", "#<<<")
    assert [s["name"] for s in manifest(off)["sessions"]] == [LEAD]
    assert not home(off).exists()

    # And on is the same manifest with the conductor's part after it, and
    # nothing else moved: not the lead, not the header, not a byte.
    conf("fleet.conf", "CONDUCTOR=on\n")
    assert render(on).code == 0
    both = (on / "extension.toml").read_text(encoding="utf-8")
    assert both.startswith(text), "turning the conductor on changed the manifest above it"
    assert both[len(text):].startswith("\n# --- the conductor")


def test_on_declares_the_conductor_after_the_lead(tmp_path):
    conf("fleet.conf", "CONDUCTOR=on\n")
    done = render(tmp_path)
    assert done.code == 0, done.out
    lead, conductor = manifest(tmp_path)["sessions"]
    assert lead["name"] == LEAD, "adding the conductor renamed the lead"
    assert lead["repo_path"] == str(REPO)
    assert conductor == {
        "name": "🎼 Conductor",
        "agent": "claude",
        "repo_path": str(REPO / "orchestration" / "conductor"),
    }
    expect(done.out, "conductor = 🎼 Conductor", "agent = claude")


def test_its_agent_is_its_own_setting_then_the_leads(tmp_path):
    conf("fleet.conf", "CONDUCTOR=on\n")
    conf("agent.conf", "AGENT=codex\n")
    assert render(tmp_path).code == 0
    lead, conductor = manifest(tmp_path)["sessions"]
    assert (lead["agent"], conductor["agent"]) == ("codex", "codex")

    conf("agent.conf", "AGENT=codex\nCONDUCTOR_AGENT=claude-spare\n")
    assert render(tmp_path).code == 0
    lead, conductor = manifest(tmp_path)["sessions"]
    assert (lead["agent"], conductor["agent"]) == ("codex", "claude-spare")

    conf("agent.conf", "CONDUCTOR_AGENT=not a name\n")
    done = render(tmp_path)
    assert done.code == 1
    expect(done.out, "CONDUCTOR_AGENT")


def test_its_mark_follows_the_glyph_setting_and_its_name_the_fleets(tmp_path):
    conf("fleet.conf", "CONDUCTOR=on\nNAME=acme\n")
    assert render(tmp_path).code == 0
    assert manifest(tmp_path)["sessions"][1]["name"] == "🎼 Conductor · acme"

    conf("session-glyphs.conf", "GLYPHS=off\nLEAD_GLYPH_ON=📡\nLEAD_GLYPH_OFF=⌖\nCONDUCTOR_GLYPH_ON=🎼\n")
    assert render(tmp_path).code == 0
    names = [s["name"] for s in manifest(tmp_path)["sessions"]]
    assert names == ["⌖ Mission Control · acme", "Conductor · acme"]


def test_a_glyph_conf_older_than_the_conductor_says_so(tmp_path):
    conf("fleet.conf", "CONDUCTOR=on\n")
    conf("session-glyphs.conf", "GLYPHS=on\nLEAD_GLYPH_ON=📡\nLEAD_GLYPH_OFF=⌖\n")
    done = render(tmp_path)
    assert done.code == 0, done.out
    assert manifest(tmp_path)["sessions"][1]["name"] == "Conductor"
    expect(done.out, "CONDUCTOR_GLYPH_ON")


def test_a_value_that_is_neither_on_nor_off_is_refused_before_anything_is_written(tmp_path):
    conf("fleet.conf", "CONDUCTOR=yes\n")
    done = render(tmp_path)
    assert done.code == 1
    expect(done.out, "CONDUCTOR", "fleet.conf")
    assert not (tmp_path / "extension.toml").exists()


def test_its_directory_hands_it_its_own_context_and_the_checkouts_hooks(tmp_path):
    conf("fleet.conf", "CONDUCTOR=on\n")
    for name in ("CLAUDE.md", "AGENTS.md"):
        (tmp_path / name).write_text((REPO / name).read_text(encoding="utf-8"), encoding="utf-8")
    assert render(tmp_path).code == 0
    entry = (home(tmp_path) / "CLAUDE.md").read_text(encoding="utf-8")
    # Imports only what is beside it: an import outside the cwd stops Claude on
    # its external-imports dialog, which nobody answers in a background session.
    assert re.findall(r"^@(\S+)\s*$", entry, re.M) == ["CONDUCTOR.rendered.md"]
    payload = (home(tmp_path) / "CONDUCTOR.rendered.md").read_text(encoding="utf-8")
    expect(payload, CONDUCTOR_H1)
    refute(payload, "@OPERATOR_NAME@", "@ASSISTANT_NAME@", "You are the **Mission Control** session")

    hooks = json.loads((home(tmp_path) / ".claude" / "settings.json").read_text(encoding="utf-8"))
    ours = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert hooks["hooks"] == ours["hooks"], "the conductor starts without the checkout's sync and ensure"
    # The checkout's CLAUDE.md is the lead's — FLEET.rendered.md — and is excluded.
    assert hooks["claudeMdExcludes"] == [str(Path(os.path.realpath(tmp_path)) / "CLAUDE.md")]
    assert not (home(tmp_path) / ".codex").exists()


def test_a_codex_conductor_gets_its_payload_as_the_nearer_project_config(tmp_path):
    conf("fleet.conf", "CONDUCTOR=on\n")
    conf("agent.conf", "CONDUCTOR_AGENT=codex\n")
    assert render(tmp_path).code == 0
    config = tomllib.loads((home(tmp_path) / ".codex" / "config.toml").read_text(encoding="utf-8"))
    expect(config["developer_instructions"], CONDUCTOR_H1)
    commands = [h["command"] for h in config["hooks"]["SessionStart"][0]["hooks"]]
    expect(" ".join(commands), "reconcile ensure --if-lead")
    # The lead stays Claude here, so the checkout gets no Codex config of its own.
    assert not (tmp_path / ".codex").exists()


def test_turning_it_off_removes_the_directory_and_never_a_foreign_one(tmp_path):
    conf("fleet.conf", "CONDUCTOR=on\n")
    assert render(tmp_path).code == 0
    assert home(tmp_path).is_dir()
    conf("fleet.conf", "CONDUCTOR=\n")
    done = render(tmp_path)
    assert done.code == 0, done.out
    assert not home(tmp_path).exists()
    expect(done.out, "removed")

    # A directory this did not render is the operator's: refused, not written into.
    write(home(tmp_path) / "notes.md", "mine\n")
    conf("fleet.conf", "CONDUCTOR=on\n")
    done = render(tmp_path)
    assert done.code == 1, done.out
    expect(done.out, "not rendered by this")
    assert (home(tmp_path) / "notes.md").read_text(encoding="utf-8") == "mine\n"
    conf("fleet.conf", "CONDUCTOR=off\n")
    assert render(tmp_path).code == 0
    assert (home(tmp_path) / "notes.md").is_file(), "off removed a directory it never rendered"


def install(fleet: Path):
    return run([*PYTHON, str(fleet / "scripts" / "lib" / "install_extension.py")], cwd=fleet, FLEET_VOICE_CONF=None)


def test_an_install_writes_nothing_tracked_and_a_moved_conductor_is_named(stubs, tmp_path):
    thurbox(stubs)
    fleet = clone(tmp_path)
    conf("fleet.conf", "CONDUCTOR=on\n")
    sessions(stubs, json.dumps([{"name": LEAD, "cwd": str(fleet)}]))
    done = install(fleet)
    assert done.code == 0, done.out
    assert home(fleet).is_dir()
    assert git("status", "--porcelain", cwd=fleet) == "", "the conductor's directory is not ignored"

    # The clone moved and the conductor did not: deleting it alone repoints it.
    sessions(stubs, json.dumps([{"name": LEAD, "cwd": str(fleet)},
                                {"name": "🎼 Conductor", "cwd": str(tmp_path / "old" / "orchestration" / "conductor")}]))
    done = install(fleet)
    assert done.code == 1, done.out
    expect(done.stderr, "'🎼 Conductor' session still opens a different directory",
           "thurbox-cli session delete '🎼 Conductor'")
    refute(done.stderr, "extension deactivate")
