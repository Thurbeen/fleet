"""Claim 7: the queue belongs to a CHECKOUT, not to the shell's cwd.

`queue_root()` once resolved against whatever directory the shell was in. A
lead whose shell sat in a second clone opened a topic there and dispatched from
it, while the pane — reading the control plane's queue — showed nothing. No
warning at any point: two queues, silently.

Exercised against a THROWAWAY CLONE rather than this one, so the answer is the
same with thurbox installed and on CI without it: a directory holding a copy of
`scripts/lib/*.py` (the anchor is the module's own path, so that is a whole
clone for this purpose) and a rendered `extension.toml` deciding whether that
clone IS the control plane.
"""

import re
import shutil
from pathlib import Path

import pytest

from harness import REPO, Run, expect, queue_module, refute, write
from harness import run_queue as q


@pytest.fixture
def clone(tmp_path) -> Path:
    fake = tmp_path / "second-clone"
    (fake / "scripts" / "lib").mkdir(parents=True)
    (fake / "deep" / "sub" / "dir").mkdir(parents=True)
    for lib in (REPO / "scripts" / "lib").glob("*.py"):
        shutil.copy(lib, fake / "scripts" / "lib" / lib.name)
    return fake


def declare_control_plane(fake: Path, repo_path: Path) -> None:
    """The manifest install-extension.sh would have rendered, naming the control plane."""
    write(fake / "extension.toml", f'[[sessions]]\nname = "fleet"\nrepo_path = "{repo_path}"\n')


def fq(fake: Path, *args: str, cwd: Path, **env) -> Run:
    env.setdefault("FLEET_QUEUE_DIR", None)
    return q(*args, cwd=cwd, script=fake / "scripts" / "lib" / "queue.py", **env)


def test_the_control_plane_is_anchored_to_its_checkout_from_anywhere_in_it(clone):
    declare_control_plane(clone, clone)
    queue = clone / "orchestration" / "queue"

    from_root = fq(clone, "root", cwd=clone).out
    expect(from_root, str(queue))
    assert fq(clone, "root", cwd=clone / "deep" / "sub" / "dir").out == from_root

    out = fq(clone, "topic", "add", "from-a-subdir", "--prompt", "opened from deep inside the checkout",
             cwd=clone / "deep" / "sub" / "dir").out
    assert (queue / "from-a-subdir").is_dir(), out
    refute(out, "control plane")
    # This clone has no run log template, and intake must not depend on one.
    expect(out, "run log not scaffolded")


def test_a_second_queue_is_refused_and_an_existing_one_is_loud(clone, tmp_path):
    declare_control_plane(clone, clone)
    fq(clone, "topic", "add", "from-a-subdir", "--prompt", "opened while it was the control plane", cwd=clone)

    real = tmp_path / "the-real-control-plane"
    declare_control_plane(clone, real)

    forked = fq(clone, "topic", "add", "forked", "--prompt", "this would have silently forked the queue", cwd=clone)
    assert forked.code != 0, forked.out
    # Naming this checkout, the control plane, and the override that means it.
    expect(forked.out, str(clone), str(real), "FLEET_QUEUE_DIR")
    assert not (clone / "orchestration" / "queue" / "forked").exists()

    listed = fq(clone, "list", cwd=clone).out
    expect(listed, "from-a-subdir", str(clone / "orchestration" / "queue"), str(real))
    expect(fq(clone, "check", cwd=clone).out, str(clone / "orchestration" / "queue"))


def test_fleet_queue_dir_is_honoured_verbatim_with_no_guard(clone, tmp_path):
    declare_control_plane(clone, tmp_path / "the-real-control-plane")
    explicit = tmp_path / "explicit"
    out = fq(clone, "topic", "add", "explicit", "--prompt", "I named the directory I meant",
             cwd=clone, FLEET_QUEUE_DIR=str(explicit)).out
    assert (explicit / "explicit").is_dir(), out
    refute(out, "control plane")


def test_the_session_name_is_the_tables_and_not_the_prose_above_it(tmp_path):
    # The manifest's own header mentions [[sessions]] in prose before the real
    # table, and a substring search read the extension's name instead of the
    # session's — invisible until the two stopped sharing a word.
    manifest = tmp_path / "extension.toml"
    write(manifest,
          "# Some prose that happens to mention [[sessions]] before the real table,\n"
          "# the same way extension.toml.in's own header commentary does.\n"
          'name = "fleet"\n\n[[agents]]\nname = "fleet"\n\n'
          '[[sessions]]\nname = "⌖ Mission Control"\nrepo_path = "/tmp/does-not-matter"\n')
    out = queue_module("print(q.manifest_session(sys.argv[1]))\n", str(manifest))
    expect(out, "('⌖ Mission Control', '/tmp/does-not-matter')")


def test_the_leads_glyph_is_rendered_and_the_words_still_agree_with_the_pane():
    # The manifest must carry the placeholder and not a literal glyph, or the
    # setting is decoration; and whatever it renders to must END in the name the
    # pane matches, or the pane hunts a session nobody spawns.
    template = queue_module("print(q.manifest_session(sys.argv[1])[0])\n", str(REPO / "extension.toml.in")).strip()
    assert template == "__LEAD_GLYPH__ Mission Control"

    pane = (REPO / "interface" / "fleet_queue.lua").read_text(encoding="utf-8")
    found = re.search(r'^local CONTROL_PLANE = "(.*)"$', pane, re.MULTILINE)
    assert found, "the pane names the lead in a CONTROL_PLANE constant"
    pane_name = found.group(1)

    glyphs = (REPO / "orchestration" / "session-glyphs.example.conf").read_text(encoding="utf-8")
    for key in ("LEAD_GLYPH_ON", "LEAD_GLYPH_OFF"):
        value = re.search(rf"^{key}=(.*)$", glyphs, re.MULTILINE)
        assert value and value.group(1), f"{key} has a value in session-glyphs.example.conf"
        glyph = value.group(1)
        assert template.replace("__LEAD_GLYPH__", glyph) == f"{glyph} {pane_name}"
