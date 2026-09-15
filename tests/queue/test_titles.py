"""§20: a title `dispatch` cannot spawn, and a spawn failure that says why.

Both halves of one run. `add` took the title `Rust crate, CI/CD and the profile
model`; `dispatch` then died with nothing but thurbox's exit status echoed back,
and the cause — thurbox refuses a session name containing `/` — was found by
running the printed `session create` by hand. The repair was a hand-edit of
`title` in task.yaml, because there is no retitle verb.
"""

from __future__ import annotations

import pytest
from kit_dispatch import FAILING_SPAWN, next_session
from queuekit import ok

from harness import expect, queue_module, refute, write
from harness import run_queue as q

LONG = "Codify the out-of-band identity and patch settings on the box"


@pytest.fixture
def ntopic() -> str:
    return ok(q("topic", "add", "unspawnable-titles", "--title", "A title becomes a session name",
                "--prompt", "add accepted a title dispatch could not spawn")).stdout.strip()


def add(topic: str, slug: str, title: str, n: str, **env):
    return q("add", topic, slug, "--title", title, "--repo", "/tmp/repo-a", "--branch", f"feat/{slug}",
             "--number", n, **env)


def test_a_title_that_cannot_become_a_session_name_is_refused_at_add(ntopic, queue_dir):
    """(a) The title from the run, verbatim — the same bargain as the `--branch` refusal."""
    refused = add(ntopic, "ci-cd", "Rust crate, CI/CD and the profile model", "01")
    assert refused.code != 0, refused.out
    # The character, that it is the SESSION NAME that cannot carry it, and the name itself.
    expect(refused.out, "'/'", "session name", "Rust crate, CI")
    assert not (queue_dir / ntopic / "01-ci-cd").exists(), "the repair is one re-run and not an edit"


def test_the_check_is_on_the_rendered_name_and_nothing_else(ntopic, tmp_path):
    """(b) The glyph goes in front and the title is cut to the byte cap, so a title
    made long only by the rendering is fine, and a leading `.` is unsafe exactly
    when no glyph precedes it."""
    ok(add(ntopic, "long-title", LONG, "02"))
    sizes = queue_module('title = sys.argv[1]\n'
                         'print(len(("\\N{ROCKET} " + title).encode()), '
                         'len(q.session_name(title, "\\N{ROCKET}").encode()))\n', LONG)
    expect(sizes, "66 64")

    glyphs = tmp_path / "glyphs"
    conf = glyphs / "orchestration" / "session-glyphs.conf"
    write(conf, "GLYPHS=on\nLEAD_GLYPH_ON=📡\nLEAD_GLYPH_OFF=⌖\nWORKER_GLYPH_ON=🚀\n")
    ok(add(ntopic, "dot-with-mark", ".hidden agenda", "03", FLEET_GLYPH_ROOT=str(glyphs)))

    write(conf, "GLYPHS=off\nLEAD_GLYPH_ON=📡\nLEAD_GLYPH_OFF=⌖\nWORKER_GLYPH_ON=🚀\n")
    refused = add(ntopic, "dot-no-mark", ".hidden agenda", "04", FLEET_GLYPH_ROOT=str(glyphs))
    assert refused.code != 0, refused.out
    expect(refused.out, "beginning with '.'")


def test_it_mirrors_thurboxs_rule_and_narrows_nothing_further():
    """(c) The four unsafe names are thurbox's own `unsafe_names_are_rejected`; every
    character in the accepted ones thurbox takes. A title is human-facing text."""
    out = queue_module(
        'unsafe = [".hidden", "foo/bar", "foo..bar", "foo\\\\bar"]\n'
        'safe = ["Rust crate, CI-CD and the profile model", "Fix the parser (again!)",\n'
        '        "Ship v2.1: metrics & alerts @ 99% — done?", "Réécrire le lecteur ~ étape 1",\n'
        '        "a.b.c and #42 + [brackets] {braces} <angles>", "trailing dot."]\n'
        'for name in unsafe + safe:\n'
        '    print("REFUSED" if q.session_name_refusal(name, "") else "ACCEPTED", name, sep="\\t")\n'
    )
    lines = out.splitlines()
    assert [ln.split("\t")[1] for ln in lines if ln.startswith("REFUSED")] == \
        [".hidden", "foo/bar", "foo..bar", "foo\\bar"], out
    assert len([ln for ln in lines if ln.startswith("ACCEPTED")]) == 6, out


def test_a_failing_spawn_says_what_thurbox_said(ntopic, stubs, queue_dir):
    """(d) From whichever stream it used, and the three things already right stay
    right: the task is left `queued`, the rest of the set still goes out, and a
    re-run does not spawn what already went."""
    stubs.tool("thurbox-cli", FAILING_SPAWN)
    for n, slug, title in (("10", "goes-out", "Goes out anyway"), ("11", "boom", "Spawn fails on stderr"),
                           ("12", "stdout-boom", "Spawn fails on stdout")):
        ok(add(ntopic, slug, title, n))
        write(queue_dir / ntopic / f"{n}-{slug}" / "BRIEF.md", f"# {title}\n\nA brief with real content in it.\n")

    first = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    next_session(stubs, first)
    stubs.session_is(first, "idle")

    out = q("dispatch", f"{ntopic}/10-goes-out", f"{ntopic}/11-boom",
            BOOM_MATCH="feat/boom", BOOM_MESSAGE="Name contains invalid characters").out
    expect(out, "Name contains invalid characters", "10-goes-out", first)
    refute(out, "returned non-zero exit status")
    state = [ln for ln in q("show", f"{ntopic}/11-boom").out.splitlines() if "state:" in ln]
    expect("\n".join(state), "queued")

    # thurbox prints its structured failure on STDOUT, which is why reading only
    # stderr left the operator with an exit status and nothing else.
    out = q("dispatch", f"{ntopic}/12-stdout-boom", BOOM_MATCH="feat/stdout-boom", BOOM_STREAM="stdout",
            BOOM_MESSAGE='{"error":"Name contains invalid characters","suggestion":"the command ran and failed"}').out
    expect(out, "Name contains invalid characters")
    refute(out, "suggestion")

    resent = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    next_session(stubs, resent)
    stubs.session_is(resent, "idle")
    out = q("dispatch", BOOM_MATCH="no-such-branch").out
    expect(out, "11-boom")
    refute(out, "10-goes-out")
    expect(q("show", f"{ntopic}/10-goes-out").out, first)
