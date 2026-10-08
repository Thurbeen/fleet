"""`add` and `block` refuse what they would get wrong, before anything is written.

Four defects, counted across three leads' runs:

- `add topic 01-design` made `01-01-design`: the lead numbered the slug and
  `add` numbered it again. A third of one fleet's records carried the double
  prefix, and every later ref the lead typed from memory missed.
- `block --on` a ref that does not exist said only "no such task", and the
  ref the lead meant was usually the doubled one beside it.
- A title `add` refuses was refused AFTER the archived topic it named had
  been un-archived, so the refusal had already written something.
- The headings a `--brief-file` must carry were named only in the refusal.
"""

from __future__ import annotations

import pytest
from queuekit import ok

from harness import expect, refute, write
from harness import run_queue as q


@pytest.fixture
def gtopic() -> str:
    return ok(q("topic", "add", "add-guards", "--title", "add refuses before it writes",
                "--prompt", "add doubled a numbered slug")).stdout.strip()


def add(topic: str, slug: str, *extra: str):
    return q("add", topic, slug, "--repo", "/tmp/repo-a", "--branch", f"fix/{slug}", *extra)


def test_a_numbered_slug_is_numbered_once_and_the_ref_is_printed(gtopic, queue_dir):
    added = ok(add(gtopic, "01-design"))
    assert added.stdout.strip() == f"{gtopic}/01-design", added.out
    assert not (queue_dir / gtopic / "01-01-design").exists()
    # The ordinal the slug carried is dropped, not trusted: the next free one wins.
    added = ok(add(gtopic, "07-07-build"))
    assert added.stdout.strip() == f"{gtopic}/02-build", added.out
    # The drop is said, so the lead learns the ref it must type from now on.
    expect(added.stderr, "07-07-build", f"{gtopic}/02-build")
    # A slug that is only a year is not an ordinal.
    added = ok(add(gtopic, "2026-roadmap"))
    assert added.stdout.strip() == f"{gtopic}/03-2026-roadmap", added.out


def test_block_on_an_unknown_ref_is_refused_naming_the_close_ones(gtopic):
    ok(add(gtopic, "design"))
    ok(add(gtopic, "build"))
    r = q("block", f"{gtopic}/02-build", "--on", f"{gtopic}/01-01-design",
          "--kind", "semantic-dependency", "--why", "builds what design decides")
    assert r.code != 0, r.out
    expect(r.out, "no such task", f"{gtopic}/01-01-design", f"{gtopic}/01-design")
    refute(q("show", f"{gtopic}/02-build").out, "01-01-design")
    # A bare id is matched against ids, and the match is named by its full ref.
    r = q("block", f"{gtopic}/02-build", "--on", "01-01-design",
          "--kind", "semantic-dependency", "--why", "builds what design decides")
    assert r.code != 0, r.out
    expect(r.out, f"{gtopic}/01-design")


def test_a_refused_add_announces_no_ref(gtopic):
    r = add(gtopic, "01-ci-cd", "--title", "CI/CD")
    assert r.code != 0, r.out
    refute(r.out, "the ref is")


def test_a_title_add_refuses_writes_nothing_first(gtopic, queue_dir):
    meta = queue_dir / gtopic / "topic.yaml"
    write(meta, meta.read_text(encoding="utf-8") + "archived: '2026-10-01T00:00:00Z'\n")
    before = meta.read_text(encoding="utf-8")
    r = add(gtopic, "ci-cd", "--title", "CI/CD")
    assert r.code != 0, r.out
    expect(r.out, "'/'", "Nothing was created")
    assert meta.read_text(encoding="utf-8") == before, "the archived topic stayed archived"
    assert not any(p.is_dir() for p in (queue_dir / gtopic).iterdir())


def test_the_brief_headings_are_named_up_front():
    """In `add --help`, before a `--brief-file` is written, not only once it is refused."""
    # argparse wraps help text, so the words are matched and not the line breaks.
    expect(" ".join(q("add", "--help").out.split()), "## What to do", "## Hard constraints", "## Coordination", "## Done means")
