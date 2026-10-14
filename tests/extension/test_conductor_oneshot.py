"""`CONDUCTOR=on` is the one-shot conductor: no session in the manifest, and a
directory for each run to start in that hands it nothing frozen.

  * the manifest is byte-for-byte the one unset renders: a run is no session,
    so nothing is declared, spawned or renamed;
  * the directory keeps the lead's CLAUDE.md out and holds no payload and no
    hooks — a run is handed its instructions in its prompt, read that moment;
  * moving from `session` drops what the session needed, and a conductor
    session still running is named, with the command that ends it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from panekit import clone, sessions, thurbox

from harness import PYTHON, expect, refute, run, run_fleet, write

LEAD = "📡 Mission Control"


def conf(name: str, text: str) -> None:
    write(Path(os.environ["FLEET_NAME_ROOT"]) / "orchestration" / name, text)


def render(dest: Path):
    return run_fleet("install-extension", "--render-only", str(dest))


def home(dest: Path) -> Path:
    return dest / "orchestration" / "conductor"


def install(fleet: Path):
    return run([*PYTHON, str(fleet / "scripts" / "lib" / "install_extension.py")], cwd=fleet, FLEET_VOICE_CONF=None)


def test_on_renders_the_manifest_unset_renders(tmp_path):
    off, on = tmp_path / "off", tmp_path / "on"
    off.mkdir()
    on.mkdir()
    assert render(off).code == 0
    conf("fleet.conf", "CONDUCTOR=on\n")
    done = render(on)
    assert done.code == 0, done.out
    assert (on / "extension.toml").read_bytes() == (off / "extension.toml").read_bytes()
    expect(done.out, "conductor = one-shot", "agent = claude")


def test_its_directory_keeps_the_lead_out_and_freezes_nothing(tmp_path):
    conf("fleet.conf", "CONDUCTOR=on\n")
    assert render(tmp_path).code == 0
    entry = (home(tmp_path) / "CLAUDE.md").read_text(encoding="utf-8")
    refute(entry, "@CONDUCTOR.rendered.md")
    assert not (home(tmp_path) / "CONDUCTOR.rendered.md").exists()
    assert not (home(tmp_path) / ".codex").exists()
    doc = json.loads((home(tmp_path) / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert doc == {"claudeMdExcludes": [str((tmp_path / "CLAUDE.md").resolve())]}, doc


def test_from_session_to_on_drops_the_sessions_payload_and_off_removes_it(tmp_path):
    conf("fleet.conf", "CONDUCTOR=session\n")
    conf("agent.conf", "CONDUCTOR_AGENT=codex\n")
    assert render(tmp_path).code == 0
    assert (home(tmp_path) / "CONDUCTOR.rendered.md").is_file()
    assert (home(tmp_path) / ".codex" / "config.toml").is_file()

    conf("fleet.conf", "CONDUCTOR=on\n")
    assert render(tmp_path).code == 0
    assert not (home(tmp_path) / "CONDUCTOR.rendered.md").exists()
    assert not (home(tmp_path) / ".codex" / "config.toml").exists()

    conf("fleet.conf", "CONDUCTOR=off\n")
    done = render(tmp_path)
    assert done.code == 0, done.out
    assert not home(tmp_path).exists()


def test_a_word_it_does_not_know_is_refused(tmp_path):
    conf("fleet.conf", "CONDUCTOR=oneshot\n")
    done = render(tmp_path)
    assert done.code == 1
    expect(done.out, "'on', 'session' or 'off'")
    assert not (tmp_path / "extension.toml").exists()


def test_a_conductor_session_still_running_is_named_with_its_delete(stubs, tmp_path):
    thurbox(stubs)
    fleet = clone(tmp_path)
    conf("fleet.conf", "CONDUCTOR=on\n")
    sessions(stubs, json.dumps([{"name": LEAD, "cwd": str(fleet)},
                                {"name": "🎼 Conductor", "cwd": str(home(fleet))}]))
    done = install(fleet)
    assert done.code == 0, done.out
    expect(done.stderr, "one-shot now", "thurbox-cli session delete '🎼 Conductor' --force")
