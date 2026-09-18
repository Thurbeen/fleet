"""`fleet session-name`: the name a session fleet spawns is created under.

The third command that renders a mark, and the one a SKILL calls. `dispatch`
renders a worker's name in-process and `fleet install-extension` renders the
lead's into the manifest; a session a skill spawns is created by a
`thurbox-cli session create` line in that skill's prose, which can read no
setting of its own — so it interpolates what this prints.

Every expectation here is read out of the tracked setting, never spelled in
this file: a test that spelled a glyph would be exactly the second copy the
setting exists to prevent, and `GLYPHS=off` would not reach it.
"""

import re

from harness import REPO, expect, run_fleet, write

GLYPHS = REPO / "orchestration" / "session-glyphs.example.conf"
KINDS = ("worker", "diagnose", "review")
TITLE = "Diagnose this machine and free what is dead"


def glyph(key: str) -> str:
    found = re.search(rf"^{key}=(.*)$", GLYPHS.read_text(encoding="utf-8"), re.M)
    assert found, f"no {key} in {GLYPHS}"
    return found.group(1)


def glyphs_off(tmp_path) -> str:
    """A checkout whose own `session-glyphs.conf` turns every mark off."""
    root = tmp_path / "glyphs-off"
    text = GLYPHS.read_text(encoding="utf-8").replace("\nGLYPHS=on\n", "\nGLYPHS=off\n")
    assert "\nGLYPHS=off\n" in text, "the tracked setting no longer reads GLYPHS=on"
    write(root / "orchestration" / "session-glyphs.conf", text)
    return str(root)


def without(tmp_path, key: str) -> str:
    """A checkout whose own `session-glyphs.conf` predates one of the words.

    `conf_path` picks the operator's copy INSTEAD of the tracked example, so
    nothing merges a new key into a file somebody copied a release ago.
    """
    root = tmp_path / f"no-{key}"
    kept = [ln for ln in GLYPHS.read_text(encoding="utf-8").splitlines(True)
            if not ln.startswith(key + "=")]
    assert len(kept) < len(GLYPHS.read_text(encoding="utf-8").splitlines(True))
    write(root / "orchestration" / "session-glyphs.conf", "".join(kept))
    return str(root)


def name(*args: str, **env: str | None) -> str:
    done = run_fleet("session-name", *args, **env)
    assert done.code == 0, done.out
    return done.stdout.rstrip("\n")


def test_a_diagnose_session_wears_the_mark_the_setting_carries():
    assert name("diagnose", TITLE) == f"{glyph('DIAGNOSE_GLYPH_ON')} {TITLE}"


def test_a_review_session_wears_a_mark_of_its_own():
    title = "Review open change requests"
    assert name("review", title) == f"{glyph('REVIEW_GLYPH_ON')} {title}"
    assert glyph("REVIEW_GLYPH_ON") != glyph("DIAGNOSE_GLYPH_ON")


def test_a_worker_is_rendered_the_same_mark_dispatch_puts_on():
    assert name("worker", TITLE) == f"{glyph('WORKER_GLYPH_ON')} {TITLE}"


def test_glyphs_off_gives_every_kind_its_plain_title_back(tmp_path):
    off = glyphs_off(tmp_path)
    for kind in KINDS:
        assert name(kind, TITLE, FLEET_GLYPH_ROOT=off) == TITLE


def test_a_title_the_byte_cap_would_cut_is_refused_rather_than_cut():
    """`dispatch` cuts because it has nobody to ask. Here a person is typing,
    and two titles differing only past the cut become ONE name that
    `--on-existing adopt` would then match against the wrong session."""
    done = run_fleet("session-name", "diagnose", "a" * 70)
    assert done.code == 1, done.out
    expect(done.stderr, "64", "adopt")


def test_an_empty_or_blank_title_is_refused():
    """thurbox accepts a name that is just the mark and a space, so nothing
    downstream catches this one; a session called `🩺 ` tells a reader nothing."""
    for title in ("", "   "):
        done = run_fleet("session-name", "diagnose", title)
        assert done.code == 1, done.out
        expect(done.stderr, "empty")


def test_a_refused_title_does_not_also_report_a_missing_mark(tmp_path):
    """One message per invocation: a title with no name has no mark to miss."""
    done = run_fleet("session-name", "diagnose", "a" * 70,
                     FLEET_GLYPH_ROOT=without(tmp_path, "DIAGNOSE_GLYPH_ON"))
    assert done.code == 1, done.out
    assert "DIAGNOSE_GLYPH_ON" not in done.stderr, done.stderr


def test_a_title_thurbox_would_refuse_is_refused_here_first():
    """A session name becomes a path segment in thurbox, so a '/' in a title
    spawns nothing — and this repo identifies a repository by host plus path,
    which is the shape somebody reaches for when titling a reviewer."""
    done = run_fleet("session-name", "review", "Review open change requests on host.example/o/r")
    assert done.code == 1, done.out
    expect(done.stderr, "'/'")


def test_a_title_that_breaks_two_rules_is_told_both_at_once():
    """Reporting the first alone sends the operator round twice — and the
    character rule is asked about the WHOLE name, so a '/' past the cut is not
    hidden by the cut."""
    done = run_fleet("session-name", "diagnose", "a" * 70 + "/x")
    assert done.code == 1, done.out
    expect(done.stderr, "'/'", "64")
    assert "a" * 70 in done.stderr, "the refusal quotes a name the operator never typed"


def test_the_refusals_hold_with_the_marks_off_too(tmp_path):
    """`GLYPHS=off` renders the bare title, so it is thurbox's rule on that
    title and not a rule about the mark."""
    off = glyphs_off(tmp_path)
    for title in ("Review .. open requests/here", ".hidden sweep"):
        done = run_fleet("session-name", "review", title, FLEET_GLYPH_ROOT=off)
        assert done.code == 1, f"{title!r}: {done.out}"
    assert name("review", "Review open change requests", FLEET_GLYPH_ROOT=off) == "Review open change requests"


def test_a_title_that_exactly_fills_the_cap_is_rendered():
    """The boundary from the other side: the mark and its space are part of
    the 64, so the longest title is measured against what the setting carries
    rather than against a number this file spells."""
    room = 64 - len(f"{glyph('DIAGNOSE_GLYPH_ON')} ".encode())
    rendered = name("diagnose", "a" * room)
    assert len(rendered.encode()) == 64
    assert rendered.endswith("a" * room)


def test_it_renders_the_same_name_from_any_directory(tmp_path):
    assert name("diagnose", TITLE) == run_fleet(
        "session-name", "diagnose", TITLE, cwd=tmp_path).stdout.rstrip("\n")


def test_an_unknown_kind_is_a_usage_error_naming_the_kinds_that_exist():
    done = run_fleet("session-name", "lead", TITLE)
    assert done.code == 2, done.out
    expect(done.stderr, *KINDS)


def test_a_kind_with_no_title_is_a_usage_error():
    done = run_fleet("session-name", "diagnose")
    assert done.code == 2, done.out


def test_help_prints_the_usage():
    done = run_fleet("session-name", "--help")
    assert done.code == 0, done.out
    expect(done.stdout, "fleet session-name", "GLYPHS=off")



def test_a_setting_missing_this_kinds_word_names_the_gap(tmp_path):
    """An operator's own conf, copied before the kind existed, carries no key
    for it — and `GLYPHS=on` rendering a mark-less name is the one state no
    reader can tell from `off`. The name still prints; the gap is named."""
    done = run_fleet("session-name", "diagnose", TITLE,
                     FLEET_GLYPH_ROOT=without(tmp_path, "DIAGNOSE_GLYPH_ON"))
    assert done.code == 0, done.out
    assert done.stdout.rstrip("\n") == TITLE
    expect(done.stderr, "DIAGNOSE_GLYPH_ON", "session-glyphs")
