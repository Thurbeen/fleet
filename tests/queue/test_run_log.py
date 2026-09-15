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

from harness import REPO, expect, write
from harness import run_queue as q


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
    expect(path.read_text(encoding="utf-8"), "Opened log", "## Outcome", "<!-- fleet:facts -->")


def test_the_facts_the_queue_knows_are_in_it_without_being_retyped(ran):
    ok(q("collect"))
    log = ran.read_text(encoding="utf-8")
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
    before = ran.read_text(encoding="utf-8").count("dispatched")
    q("collect")
    q("collect")
    assert ran.read_text(encoding="utf-8").count("dispatched") == before


def test_run_names_the_log_and_leaves_one_the_lead_took_over_alone(ran):
    """A log whose fence was removed is a log the lead took over."""
    expect(q("run").out, str(ran))

    taken = ok(q("topic", "add", "taken-over", "--title", "Taken over", "--prompt", "mine now")).stdout.strip()
    path = run_log(taken)
    kept = "".join(line for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
                   if "fleet:facts" not in line)
    write(path, kept + "Every word of this is mine.\n")
    expect(q("run").out, "left alone")
    expect(path.read_text(encoding="utf-8"), "Every word of this is mine.")


def test_the_tracked_template_carries_no_path_and_no_session_id():
    template = (REPO / "orchestration" / "runs" / "_TEMPLATE.md").read_text(encoding="utf-8")
    assert not re.search(r"/home/|/Users/|[0-9a-f]{8}-[0-9a-f]{4}", template)
