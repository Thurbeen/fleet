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

import pytest

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


# Every shape `unsafe_name` knows, one title each, plus the cap — asked with
# the marks OFF so that the rendered name IS the title: a leading '.' is unsafe
# exactly when no mark precedes it, and this table is about thurbox's rule
# rather than about the mark.
SHAPES = {
    "slash": "Review open change requests on host.example/owner/repo",
    "backslash": "Review open change requests on host.example\\owner\\repo",
    "dot dot": "Review .. and the change requests under it",
    "leading dot": ".hidden sweep of this machine",
    "empty": "",
    "over the cap": "Review every open change request on every project this operator runs",
}

TRY = re.compile(r"^Try this title: (.*)$", re.M)

# A title only the CAP refuses gets no suggestion: what runs over is what comes
# last, which is where the identity is, so shortening it from the end hands two
# projects one name — the `--on-existing adopt` collision the refusal exists to
# prevent.
NO_SUGGESTION = ("empty", "over the cap")


def suggested(stderr: str) -> str:
    """The title the refusal's last line offers, which is a title and not a
    command: `fleet` is not on PATH here, and a command line would have to be
    quoted for one shell on POSIX and another on Windows.

    Read exactly as it is printed, because that is what an operator retypes:
    nothing quotes or escapes it, and `safe_name_title` suggests nothing that
    would need it."""
    found = TRY.search(stderr)
    assert found, stderr
    return found.group(1)


@pytest.mark.parametrize("shape", sorted(SHAPES), ids=sorted(SHAPES))
def test_every_shape_thurbox_refuses_is_refused_when_fleet_renders_it(shape, tmp_path):
    """The rule is thurbox's `validate_safe_name`, mirrored in one place, and
    the cap beside it. Each of these spawned nothing; each now fails here, with
    a way out — a title that works, or the plain instruction when no title can
    be derived from what was typed."""
    off = glyphs_off(tmp_path)
    done = run_fleet("session-name", "review", SHAPES[shape], FLEET_GLYPH_ROOT=off)
    assert done.code == 1, done.out
    if shape in NO_SUGGESTION:
        assert not TRY.search(done.stderr), done.stderr
        expect(done.stderr, "Reword the title.")
        return
    title = suggested(done.stderr)
    assert name("review", title, FLEET_GLYPH_ROOT=off) == title


def test_the_refusal_hands_back_a_name_that_works():
    """The case this was written from: a reviewer titled the way this repo
    identifies a repository everywhere else — host plus path — and the operator
    invented the working name by hand."""
    done = run_fleet(
        "session-name", "review", "Review open change requests on github.com/Thurbeen/fleet")
    assert done.code == 1, done.out
    title = suggested(done.stderr)
    assert title == "Review open change requests on github.com Thurbeen fleet"
    assert name("review", title).endswith(title)


def test_an_empty_title_is_not_handed_an_invented_name():
    """There is nothing to suggest, and a name nobody typed is not an answer."""
    done = run_fleet("session-name", "diagnose", "   ")
    assert done.code == 1, done.out
    assert not TRY.search(done.stderr), done.stderr
    expect(done.stderr, "Reword the title.")


def test_a_title_only_the_cap_refuses_is_not_handed_a_shortened_one():
    """What runs over the cap is the END of the title, and `review-prs` titles a
    reviewer `... on <project>` — so a suggestion made by dropping words from
    there is the SAME name for two projects, which is the `adopt` collision the
    refusal exists to prevent. Two long titles, one suggestion, would be the bug
    shipped as the remedy."""
    for project in ("enterprise-data-platform-ingestion", "customer-identity-access-service"):
        done = run_fleet("session-name", "review", f"Review open change requests on {project}")
        assert done.code == 1, done.out
        assert not TRY.search(done.stderr), done.stderr
        expect(done.stderr, "Reword the title.")


def test_a_slash_that_is_not_a_path_keeps_what_stands_on_both_sides_of_it():
    """`CI/CD`, `A/B`, `and/or`: thurbox refuses the character, not the words
    around it. A repair that read every slash as a path would suggest a title
    saying something else — and `add` was refused over exactly this title once,
    which `session_name_refusal` records."""
    done = run_fleet("session-name", "worker", "Rust crate, CI/CD and the profile model")
    assert done.code == 1, done.out
    assert suggested(done.stderr) == "Rust crate, CI CD and the profile model"


def test_two_repositories_of_the_same_name_are_not_suggested_one_title():
    """Keeping only the last segment of a path would drop the host and the
    owner, which is the part that tells them apart — and `--on-existing adopt`
    matches on the name, so the second reviewer would adopt the first's
    session."""
    titles = {
        suggested(run_fleet("session-name", "review", f"Review requests on {repo}").stderr)
        for repo in ("github.com/Thurbeen/fleet", "gitlab.example.com/team/fleet")
    }
    assert len(titles) == 2, titles


def test_a_title_the_repair_cannot_bring_under_the_cap_gets_no_suggestion():
    """The two halves have to agree: a suggestion this command would itself
    refuse is the one thing `safe_name_title` promises never to hand back."""
    done = run_fleet(
        "session-name", "review",
        "Review open change requests on github.com/some-long-owner-name/repository")
    assert done.code == 1, done.out
    assert not TRY.search(done.stderr), done.stderr
    expect(done.stderr, "Reword the title.")


def test_redundant_whitespace_is_what_a_cap_only_refusal_may_be_repaired_by():
    """No WORD is dropped to fit the cap, but a run of spaces is bytes like any
    other, and collapsing it drops nothing that tells two sessions apart."""
    done = run_fleet("session-name", "review",
                     "Review open change requests on      enterprise-data-platform")
    assert done.code == 1, done.out
    expect(done.stderr, "cap")
    assert suggested(done.stderr) == "Review open change requests on enterprise-data-platform"


def test_a_leading_dot_is_kept_where_the_mark_stands_in_front_of_it():
    """The mark is what makes such a name safe, so dropping the dot under one
    would edit a title thurbox never objected to."""
    done = run_fleet("session-name", "review", ".hidden sweep of a/b")
    assert done.code == 1, done.out
    assert suggested(done.stderr) == ".hidden sweep of a b"


def test_a_suggestion_never_begins_with_the_space_a_removal_left():
    """A title nobody can copy by eye is not a suggestion."""
    done = run_fleet("session-name", "review", "/x on a/b")
    assert done.code == 1, done.out
    assert suggested(done.stderr) == "x on a b"


def test_no_suggestion_is_made_that_the_same_command_would_refuse(tmp_path):
    """The last guard, asked of `unsafe_name`: a second leading dot survives
    the first one being dropped, and what is left is a name thurbox refuses."""
    done = run_fleet("session-name", "review", ". . foo",
                     FLEET_GLYPH_ROOT=glyphs_off(tmp_path))
    assert done.code == 1, done.out
    assert not TRY.search(done.stderr), done.stderr
    expect(done.stderr, "Reword the title.")


def test_a_title_that_cannot_be_typed_back_is_not_suggested_at_all():
    """An escape sequence or a zero-width space survives no route to the
    operator: printed as itself it colours the terminal or vanishes, and
    escaped it grows a backslash — one of the characters thurbox refuses, so
    the title read back off the screen is refused in its turn."""
    for title in ("Review \x1b[31mred\x1b[0m on a/b", "Review PRs\u200b on a/b"):
        done = run_fleet("session-name", "review", title)
        assert done.code == 1, done.out
        assert not TRY.search(done.stderr), done.stderr
        expect(done.stderr, "Reword the title.")


def test_a_suggestion_is_printed_as_itself_and_never_escaped():
    """`repr` would quote a title holding an apostrophe with backslashes, and a
    backslash is a character thurbox refuses: the title typed back off the
    screen would be refused, and suggest itself again."""
    done = run_fleet("session-name", "review", "Don\'t ship \"beta\" on a/b")
    assert done.code == 1, done.out
    title = suggested(done.stderr)
    assert title == "Don\'t ship \"beta\" on a b", title
    assert "\\" not in title
    assert name("review", title).endswith(title)


def test_a_merely_unusual_title_is_rendered_rather_than_refused():
    """Everything thurbox accepts, fleet accepts. Narrowing the rule would be a
    defect of its own: a title is human-facing text, and spaces, punctuation,
    non-ASCII and emoji are all names thurbox creates."""
    title = "Révision : « PRs » — 100 % ✨"
    assert name("review", title).endswith(title)
