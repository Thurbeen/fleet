"""Claim 19: the run log is something the queue PRODUCES.

`AGENTS.md` said "record the run in orchestration/runs/ as it happens", and two
consecutive runs did not: one was written only because its lead session was
being migrated, the other reconstructed from chat history. An instruction two
leads failed the same way is a tool gap, so the queue writes the half it knows
— inside a fenced block it rewrites rather than appends to — and leaves the
half it cannot know, the lead's prose outside the fence, alone.
"""

import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from queuekit import TOPIC_TITLE, ok, result

from fleet.cli import load
from harness import REPO, expect, write
from harness import run_queue as q

queue = load("queue.py")


def run_log(slug: str) -> Path:
    return Path(os.environ["FLEET_RUNS_DIR"]) / f"{datetime.now(UTC):%Y-%m-%d}-{slug}.md"


@pytest.fixture
def ran(first_landed, stubs, queue_dir) -> Path:
    """The shared topic through dispatch, collect and reap: 01 merged, 02 collected
    with an attested pull request, so the facts are ones the loop really wrote."""
    stubs.pipeline_pr(1001, "fix/document-the-states")
    result(queue_dir / first_landed / "02-document-the-states", "shipped", "Documented the state vocabulary.",
           "https://github.com/Thurbeen/thurbox/pull/1001")
    ok(q("collect"))
    return run_log(first_landed)


def test_opening_a_topic_opens_its_run_log_and_says_where(queue_dir):
    added = ok(q("topic", "add", "opened-log", "--title", "Opened log", "--prompt", "nobody asked for a log"))
    path = run_log(added.stdout.strip())
    assert path.is_file(), f"no {path}: {sorted(p.name for p in path.parent.iterdir())}"
    # On stderr, which is the note, and not in stdout, which is the value.
    expect(added.stderr, str(path))
    assert added.stdout.strip() == "opened-log"
    # The prose sections the lead owns are already there, and the generated block is fenced.
    expect(path.read_text(encoding="utf-8"), "## Outcome", facts_file(path).name)
    expect(facts_file(path).read_text(encoding="utf-8"), "Opened log", "No tasks yet.")


def test_the_facts_the_queue_knows_are_in_it_without_being_retyped(ran):
    ok(q("collect"))
    log = facts_file(ran).read_text(encoding="utf-8")
    expect(log, TOPIC_TITLE, "01-drop-idle-default", "fix/document-the-states", "/pull/1001", "dispatched",
           # The overlap that was accepted rather than serialized.
           "Overlap on `src/state.rs`")


def test_the_leads_prose_survives_and_a_refresh_rewrites_rather_than_appends(ran):
    write(ran, ran.read_text(encoding="utf-8").replace(
        "## Outcome", "## Outcome\n\nSerializing this topic would have been a mistake.", 1))
    q("shepherd", "--dry-run")
    q("collect")
    expect(ran.read_text(encoding="utf-8"), "Serializing this topic would have been a mistake.")

    # `collect` runs many times over one run, and a line appended per pass is the
    # timeline nobody reads.
    before = facts_file(ran).read_text(encoding="utf-8").count("dispatched")
    q("collect")
    q("collect")
    assert facts_file(ran).read_text(encoding="utf-8").count("dispatched") == before


def test_run_names_the_log_and_leaves_one_the_lead_rewrote_alone(ran):
    expect(q("run").out, str(ran))

    taken = ok(q("topic", "add", "taken-over", "--title", "Taken over", "--prompt", "mine now")).stdout.strip()
    path = run_log(taken)
    write(path, "Every word of this is mine.\n")
    ok(q("run"))
    assert path.read_text(encoding="utf-8") == "Every word of this is mine.\n"


def test_the_tracked_template_carries_no_path_and_no_session_id():
    template = (REPO / "orchestration" / "runs" / "_TEMPLATE.md").read_text(encoding="utf-8")
    assert not re.search(r"/home/|/Users/|[0-9a-f]{8}-[0-9a-f]{4}", template)


# --- the facts live beside the log, and the log is the lead's alone ----------
#
# A refresh that rewrote the lead's own file lost what the lead wrote three
# ways: an edit saved between its read and its write (a `collect` the loop runs
# on its own clock), the opening marker quoted in a sentence above the block,
# and the two markers in the wrong order. So the generated facts moved into a
# sidecar only fleet writes, and the log is created once and never rewritten.

LEAD = "## Decisions worth keeping\n\nLEAD-DECISION: serialize 02 behind 01.\n"


def facts_file(log: Path) -> Path:
    return log.with_name(log.name[: -len(".md")] + ".facts.md")


def legacy(slug: str, above: str = "", block: str | None = None) -> Path:
    """A log written before the facts moved out: the fenced block inside it."""
    path = run_log(slug)
    block = block if block is not None else "<!-- fleet:facts -->\nold facts\n<!-- fleet:facts:end -->\n"
    write(path, f"# Run: `{slug}`\n\n{above}{block}\n{LEAD}")
    return path


def topic(slug: str) -> str:
    return ok(q("topic", "add", slug, "--title", slug, "--prompt", "fixture")).stdout.strip()


def test_the_facts_go_beside_the_log_and_never_into_it(ran):
    ok(q("collect"))
    facts = facts_file(ran)
    expect(facts.read_text(encoding="utf-8"), TOPIC_TITLE, "01-drop-idle-default", "/pull/1001")
    log = ran.read_text(encoding="utf-8")
    assert "fleet:facts" not in log, log
    expect(log, facts.name, "## Outcome")

    # Whatever the lead does to its own file, the loop never writes it again —
    # not even to put back the link the lead deleted.
    mine = "Every word of this is mine, the link included.\n"
    write(ran, mine)
    q("shepherd", "--dry-run")
    ok(q("collect"))
    ok(q("run"))
    assert ran.read_text(encoding="utf-8") == mine


def test_an_existing_file_at_the_logs_path_is_never_overwritten_by_topic_add():
    path = run_log("pre-existing")
    write(path, "Written before the topic existed.\n")
    topic("pre-existing")
    assert path.read_text(encoding="utf-8") == "Written before the topic existed.\n"


def test_a_marker_quoted_in_prose_leaves_the_whole_log_alone():
    """D2, reproduction 1: the first marker anywhere used to start the splice."""
    slug = topic("quoted-marker")
    path = legacy(slug, above="Note: the block starts at `<!-- fleet:facts -->`. KEEP-ME-1\n\n")
    before = path.read_text(encoding="utf-8")
    expect(ok(q("run")).out, "malformed")
    assert path.read_text(encoding="utf-8") == before


def test_markers_in_the_wrong_order_leave_the_whole_log_alone():
    """D2, reproduction 2: an end marker first used to drop everything after the begin."""
    slug = topic("swapped-markers")
    path = legacy(slug, block="<!-- fleet:facts:end -->\n\n<!-- fleet:facts -->\n")
    before = path.read_text(encoding="utf-8")
    expect(ok(q("run")).out, "malformed")
    assert path.read_text(encoding="utf-8") == before
    assert "LEAD-DECISION" in before


def test_a_well_formed_fence_migrates_once_into_the_facts_file():
    slug = topic("migrates")
    path = legacy(slug, above="Prose above the block.\n\n")
    expect(ok(q("run")).out, "migrated")
    facts = facts_file(path)
    expect(facts.read_text(encoding="utf-8"), slug, "No tasks yet.")
    log = path.read_text(encoding="utf-8")
    assert "fleet:facts" not in log and "old facts" not in log, log
    expect(log, "Prose above the block.", "LEAD-DECISION", facts.name)
    # The block's place now holds the link, and nothing else moved.
    assert log == f"# Run: `{slug}`\n\nProse above the block.\n\n{FACTS_LINK(facts.name)}\n\n{LEAD}"
    ok(q("run"))
    assert path.read_text(encoding="utf-8") == log


def test_archived_logs_migrate_only_through_run_all():
    slug = topic("archived-log")
    ok(q("archive", slug))
    path = legacy(slug)
    before = path.read_text(encoding="utf-8")
    ok(q("run"))
    ok(q("collect"))
    assert path.read_text(encoding="utf-8") == before
    expect(ok(q("run", "--all")).out, "migrated")
    assert "fleet:facts" not in path.read_text(encoding="utf-8")
    assert facts_file(path).is_file()


def FACTS_LINK(name: str) -> str:  # noqa: N802 - the queue's constant, rendered
    return queue.FACTS_LINK.format(name=name)


def raced(slug: str, path: Path):
    """refresh_run_log with the lead's editor saving while the facts render."""
    real = queue.run_facts

    def lead_saves_meanwhile(qq, s):
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n" + LEAD)
        return real(qq, s) + "\n<!-- changed -->\n"

    queue.run_facts = lead_saves_meanwhile
    try:
        return queue.refresh_run_log(queue.Queue(queue.queue_root()), slug)
    finally:
        queue.run_facts = real


def test_an_edit_saved_while_a_refresh_runs_survives():
    """D1: a collect pass used to write back the log it read before the lead saved."""
    slug = topic("raced-edit")
    path = run_log(slug)
    raced(slug, path)
    assert "LEAD-DECISION" in path.read_text(encoding="utf-8")


def test_an_edit_saved_while_a_migration_runs_survives_and_migrates_next_pass():
    slug = topic("raced-migration")
    path = legacy(slug)
    _, note = raced(slug, path)
    assert "changed" in note, note
    text = path.read_text(encoding="utf-8")
    assert text.count("LEAD-DECISION") == 2 and "fleet:facts" in text, text
    ok(q("run"))
    text = path.read_text(encoding="utf-8")
    assert text.count("LEAD-DECISION") == 2 and "fleet:facts" not in text, text
