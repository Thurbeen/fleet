"""A lead that lost its conversation reads where every run stands in one capped command.

`fleet context` writes nothing and asks nothing of the lead's judgement: it
reads the same records `plan` reads, so a ready task, a condition only a
person clears, and a worker that concluded `stuck` each surface as a line with
the command that acts on it.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from contextkit import BRIEF, context, parse_toon, plant

from harness import REPO, run_queue, run_queue_batch, write


@pytest.fixture
def runs(tmp_path) -> Path:
    brief = tmp_path / "brief.md"
    write(brief, BRIEF)
    add = ["--publish", "none", "--brief-file", str(brief)]
    assert run_queue_batch([
        ["topic", "add", "ship-it", "--title", "Ship it", "--prompt", "ship"],
        ["add", "ship-it", "ready-one", "--title", "Ready", "--repo", "/nowhere/a", "--branch", "a", *add],
        ["add", "ship-it", "held-one", "--title", "Held", "--repo", "/nowhere/b", "--branch", "b", *add],
        ["topic", "add", "went-wrong", "--title", "Went wrong", "--prompt", "oops"],
        ["add", "went-wrong", "stuck-one", "--title", "Stuck", "--repo", "/nowhere/c", "--branch", "c", *add],
    ]).code == 0
    assert run_queue("block", "ship-it/02-held-one", "--condition", "the operator picks a name",
                     "--kind", "undecided", "--why", "the name is theirs").code == 0
    queue = Path(os.environ["FLEET_QUEUE_DIR"])
    stuck = queue / "went-wrong" / "01-stuck-one"
    record = stuck / "task.yaml"
    write(record, record.read_text(encoding="utf-8").replace("state: queued", "state: dispatched"))
    write(stuck / "result.md", "---\noutcome: stuck\n---\nThe API it needs does not exist.\n")
    assert run_queue("collect").code == 0
    return queue


def test_the_summary_counts_what_is_open_ready_and_recently_learned(runs):
    today = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    plant("github.com/acme/app", "20990101-00000001", "Fresh.", at=today)
    plant("github.com/acme/app", "20000101-00000002", "Old.", at="2000-01-01T00:00:00Z")
    doc = parse_toon(context().stdout)
    assert doc["topics"].startswith("2 open · ready 1 · stalled 0 · facts 1 since "), doc
    states = {row["slug"]: row for row in doc["open"]}
    assert states["ship-it"]["state"] == "ready", doc
    assert states["went-wrong"]["state"] == "stuck", doc


def test_pending_names_each_decision_and_the_command_that_makes_it(runs):
    doc = parse_toon(context("pending").stdout)
    rows = {row["task"]: row for row in doc["tasks"]}
    assert rows["ship-it/01-ready-one"]["why"] == "ready"
    assert "fleet queue dispatch ship-it/01-ready-one" in rows["ship-it/01-ready-one"]["next"]
    assert rows["ship-it/02-held-one"]["why"] == "condition"
    assert "the operator picks a name" in rows["ship-it/02-held-one"]["next"]
    assert rows["went-wrong/01-stuck-one"]["why"] == "stuck"
    assert rows["went-wrong/01-stuck-one"]["next"].endswith("result.md")
    assert doc["pending"].startswith("3 · "), doc


def test_reading_context_writes_nothing(runs):
    before = {p: p.stat().st_mtime_ns for p in runs.rglob("*") if p.is_file()}
    for args in ([], ["pending"], ["repo", "github.com/acme/app"]):
        assert context(*args).code == 0
    after = {p: p.stat().st_mtime_ns for p in runs.rglob("*") if p.is_file()}
    assert before == after


def test_fleet_md_has_the_lead_read_context_on_its_first_turn():
    fleet = (REPO / "FLEET.md").read_text(encoding="utf-8")
    first = fleet[fleet.index("## First, every session"):fleet.index("## What you do")]
    assert "uv run fleet context" in first, first
