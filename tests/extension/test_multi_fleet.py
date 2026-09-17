"""Several fleets on one machine: one per checkout, each its own Mission Control.

A fleet used to be a singleton by accident of NAMING. The manifest spelled one
extension id (`fleet`) and one session name (`<glyph> Mission Control`), and
thurbox resolves both by name — so a second clone installing the same manifest
registered over the first extension and was handed the first's live session,
which `install-extension` could only report as a directory mismatch. Nothing was
wrong with the queue, the pane or the loop: every one of those is already
anchored to a CHECKOUT. Only the two names were global.

`orchestration/fleet.conf` is the whole fix — one `NAME=`, read here at render
time and nowhere else, exactly as the glyph and the agent are. Unset, a fleet
renders the two names it always did, so an operator who never wanted a second
one never sees this feature. Set, it renders an extension and a lead of its own,
which is what makes two of them able to coexist.

These are the claims:

  * an unnamed fleet renders byte-for-byte what it rendered before,
  * a named one renders `fleet-<name>` and `<glyph> Mission Control · <name>`,
  * a name that could not be an extension directory or a session name is
    refused before anything is written,
  * and the two failure modes at install time are told apart: a fleet whose
    clone MOVED (same name, another directory) is still the error it was, and a
    SECOND fleet beside a first is not an error at all.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import pytest
from panekit import clone, sessions, thurbox

from harness import PYTHON, expect, refute, run, run_fleet, write

LEAD = "📡 Mission Control"


def settings(name: str) -> Path:
    """A setting in the root the render reads, which the harness isolates."""
    return Path(os.environ["FLEET_NAME_ROOT"]) / "orchestration" / name


def name_fleet(fleet: str) -> None:
    write(settings("fleet.conf"), f"NAME={fleet}\n")


def render_only(dest: Path, **env):
    return run_fleet("install-extension", "--render-only", str(dest), **env)


def manifest(dest: Path) -> dict:
    return tomllib.loads((dest / "extension.toml").read_text(encoding="utf-8"))


def install(fleet: Path, *args: str):
    return run([*PYTHON, str(fleet / "scripts" / "lib" / "install_extension.py"), *args],
               cwd=fleet, FLEET_VOICE_CONF=None)


def test_an_unnamed_fleet_renders_the_names_it_always_did(tmp_path):
    """The tracked default is no name, so a fleet that never heard of this
    setting keeps its extension id and its lead — and needs no rename."""
    assert render_only(tmp_path).code == 0
    doc = manifest(tmp_path)
    assert doc["name"] == "fleet"
    assert doc["sessions"][0]["name"] == LEAD


def test_a_named_fleet_renders_an_extension_and_a_lead_of_its_own(tmp_path):
    name_fleet("acme")
    done = render_only(tmp_path)
    assert done.code == 0, done.out
    doc = manifest(tmp_path)
    assert doc["name"] == "fleet-acme"
    assert doc["sessions"][0]["name"] == f"{LEAD} · acme"
    # The report says which fleet was rendered: two clones, two answers.
    expect(done.out, "acme")


def test_two_named_fleets_collide_in_neither_name(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    name_fleet("acme")
    assert render_only(first).code == 0
    name_fleet("lab")
    assert render_only(second).code == 0
    a, b = manifest(first), manifest(second)
    assert a["name"] != b["name"], "two fleets registered as one extension"
    assert a["sessions"][0]["name"] != b["sessions"][0]["name"], "two fleets share one lead"


def test_the_glyph_setting_still_reaches_a_named_fleet(tmp_path):
    write(settings("session-glyphs.conf"), "GLYPHS=off\nLEAD_GLYPH_ON=📡\nLEAD_GLYPH_OFF=⌖\n")
    name_fleet("acme")
    assert render_only(tmp_path).code == 0
    assert manifest(tmp_path)["sessions"][0]["name"] == "⌖ Mission Control · acme"


@pytest.mark.parametrize("bad", ["two words", "../escape", "acme/prod", "-acme", "acme!", "a" * 40])
def test_a_name_that_could_not_be_a_directory_or_a_session_is_refused(tmp_path, bad):
    """It becomes an extension home under thurbox's config and a session name
    that is a path segment there, so the grammar is bare and nothing is written
    until it holds."""
    name_fleet(bad)
    done = render_only(tmp_path)
    assert done.code == 1, done.out
    expect(done.out, "fleet.conf")
    assert not (tmp_path / "extension.toml").exists()


def test_a_name_that_would_read_as_a_placeholder_is_refused(tmp_path):
    """`__x__` is how this repo spells an unrendered placeholder, and
    `notify_lead.py` reads a session name carrying `__` as a manifest nobody
    rendered — so a fleet called `dev__two` would be a lead the reconciler
    stops waking, silently, when ready work appears."""
    name_fleet("dev__two")
    done = render_only(tmp_path)
    assert done.code == 1, done.out
    expect(done.out, "double underscore")
    assert not (tmp_path / "extension.toml").exists()

    # A single underscore is a name, and stays one.
    name_fleet("dev_two")
    assert render_only(tmp_path).code == 0
    assert manifest(tmp_path)["sessions"][0]["name"] == f"{LEAD} · dev_two"


def test_a_second_fleet_is_not_the_first_fleets_lead_moving(stubs, tmp_path):
    """The crux. A live `📡 Mission Control` in another directory used to be the
    one thing install could not proceed past; for a NAMED fleet it is simply
    somebody else's lead, and this install has its own."""
    thurbox(stubs)
    fleet = clone(tmp_path)
    sessions(stubs, json.dumps([{"name": LEAD, "cwd": str(tmp_path / "the-first-fleet")}]))
    name_fleet("acme")
    done = install(fleet)
    assert done.code == 0, done.out
    refute(done.out, "different directory")
    assert stubs.calls("thurbox-cli", f"extension install {fleet}")
    expect(done.out, "thurbox-cli extension status fleet-acme")


def test_an_unnamed_second_fleet_is_told_how_to_become_one(stubs, tmp_path):
    """Same name, another directory, which is two different situations: the
    clone moved, or this is a second fleet that has not been named yet. The
    remedy for the first deletes a conversation, so both are spelled."""
    thurbox(stubs)
    fleet = clone(tmp_path)
    sessions(stubs, json.dumps([{"name": LEAD, "cwd": str(tmp_path / "the-first-fleet")}]))
    done = install(fleet)
    assert done.code == 1, done.out
    expect(done.stderr, "still opens a different directory",
           "thurbox-cli extension deactivate fleet",
           "orchestration/fleet.conf", "NAME=")
