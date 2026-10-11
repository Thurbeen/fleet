"""Who asked: `--by` on a topic and a task, recorded, resolved once, and shown everywhere.

A Mission Control fronted by a chat channel starts work "under this person", so
the queue records whom each topic and task is for. It is free text, a task with
no `--by` takes its topic's name AS IT WAS AT `add`, and nothing keys on it —
it is displayed, never acted on. A record written before the field existed
reads as "not recorded" and every verb treats it exactly as before.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml
from queuekit import ok

from harness import PYTHON, REPO, expect, refute, run, run_fleet, write
from harness import run_queue as q

PROBE = REPO / "scripts" / "lib" / "pane_probe.py"


def record(queue_dir: Path, ref: str) -> dict:
    return yaml.safe_load((queue_dir / ref / "task.yaml").read_text(encoding="utf-8"))


def asked(queue_dir: Path) -> str:
    """A topic alice asked for: one task inherits her, one names bob, one is filled in later."""
    topic = ok(q("topic", "add", "asked", "--title", "Asked for", "--prompt", "p", "--by", "  alice  ")).stdout.strip()
    ok(q("add", topic, "inherits", "--title", "Inherits", "--repo", "/tmp/repo-a", "--branch", "fix/a",
         "--publish", "none"))
    ok(q("add", topic, "overrides", "--title", "Overrides", "--repo", "/tmp/repo-a", "--branch", "fix/b",
         "--publish", "none", "--by", "@bob"))
    return topic


def test_a_task_inherits_its_topics_requester_and_its_own_by_overrides_it(queue_dir):
    topic = asked(queue_dir)
    meta = yaml.safe_load((queue_dir / topic / "topic.yaml").read_text(encoding="utf-8"))
    assert meta["requested_by"] == "alice"
    assert record(queue_dir, f"{topic}/01-inherits")["requested_by"] == "alice"
    assert record(queue_dir, f"{topic}/02-overrides")["requested_by"] == "@bob"
    expect((queue_dir / topic / "01-inherits" / "BRIEF.md").read_text(encoding="utf-8"), "**Requested by.** alice")


def test_the_task_stores_the_resolved_name_so_a_later_topic_edit_rewrites_no_history(queue_dir):
    topic = asked(queue_dir)
    path = queue_dir / topic / "topic.yaml"
    write(path, path.read_text(encoding="utf-8").replace("requested_by: alice", "requested_by: carol"))
    ok(q("add", topic, "later", "--title", "Later", "--repo", "/tmp/repo-a", "--branch", "fix/c",
         "--publish", "none"))
    assert record(queue_dir, f"{topic}/01-inherits")["requested_by"] == "alice"
    assert record(queue_dir, f"{topic}/03-later")["requested_by"] == "carol"


def test_no_by_anywhere_is_valid_and_records_nobody(queue_dir):
    topic = ok(q("topic", "add", "nobody", "--title", "Nobody", "--prompt", "p")).stdout.strip()
    ok(q("add", topic, "plain", "--title", "Plain", "--repo", "/tmp/repo-a", "--branch", "fix/p",
         "--publish", "none"))
    assert "requested_by" not in (queue_dir / topic / "topic.yaml").read_text(encoding="utf-8")
    assert record(queue_dir, f"{topic}/01-plain")["requested_by"] is None
    refute((queue_dir / topic / "01-plain" / "BRIEF.md").read_text(encoding="utf-8"), "Requested by")
    expect(ok(q("show", f"{topic}/01-plain")).stdout, "not recorded")


def test_a_newline_a_tab_or_an_essay_in_by_is_refused_and_nothing_is_created(queue_dir):
    for bad in ("alice\nbob", "alice\tbob", "x" * 81):
        done = q("topic", "add", "bad", "--title", "Bad", "--prompt", "p", "--by", bad)
        assert done.code != 0, done.out
        assert not (queue_dir / "bad").exists()
    topic = asked(queue_dir)
    done = q("add", topic, "bad", "--title", "Bad", "--repo", "/tmp/repo-a", "--branch", "fix/x",
             "--publish", "none", "--by", "carol\nmallory")
    assert done.code != 0
    expect(done.out, "newline")
    assert not any(p.name.endswith("-bad") for p in (queue_dir / topic).iterdir())


def test_a_record_written_before_the_field_loads_lists_and_validates_unchanged(queue_dir):
    topic = ok(q("topic", "add", "legacy", "--title", "Legacy", "--prompt", "p")).stdout.strip()
    ok(q("add", topic, "old", "--title", "Old", "--repo", "/tmp/repo-a", "--branch", "fix/o",
         "--publish", "none"))
    path = queue_dir / topic / "01-old" / "task.yaml"
    write(path, path.read_text(encoding="utf-8").replace("requested_by: null\n", ""))
    assert "requested_by" not in path.read_text(encoding="utf-8")
    listed = ok(q("list")).stdout
    expect(listed, "01-old")
    refute(listed, " by ")
    ok(q("check"))
    expect(ok(q("show", f"{topic}/01-old")).stdout, "not recorded")


def test_a_hand_edited_requester_that_is_not_one_line_fails_the_record_check(queue_dir):
    topic = asked(queue_dir)
    path = queue_dir / topic / "01-inherits" / "task.yaml"
    write(path, path.read_text(encoding="utf-8").replace("requested_by: alice", "requested_by: [a, b]"))
    expect(q("check").out, "requested_by")


def test_list_show_status_json_and_the_run_facts_name_the_requester(queue_dir):
    topic = asked(queue_dir)
    listed = ok(q("list")).stdout
    expect(listed, "asked — Asked for  by alice", "by @bob")
    expect(ok(q("show", f"{topic}/02-overrides")).stdout, "by:          @bob")

    doc = json.loads(ok(run_fleet("status", "--json")).stdout)
    entry = next(t for t in doc["queue"]["topics"] if t["slug"] == topic)
    assert entry["requested_by"] == "alice"
    assert {t["id"]: t["requested_by"] for t in entry["tasks"]} == {"01-inherits": "alice", "02-overrides": "@bob"}
    expect(ok(run_fleet("status")).stdout, "by alice", "by @bob")

    ok(q("run", topic))
    runs = Path(os.environ["FLEET_RUNS_DIR"])
    facts = next(runs.glob(f"*-{topic}.facts.md")).read_text(encoding="utf-8")
    expect(facts, "**Requested by.** alice", "`02-overrides` was requested by @bob")
    refute(facts, "`01-inherits` was requested by")


def test_the_pane_probe_carries_the_requester_on_the_topic_and_the_task(queue_dir):
    topic = asked(queue_dir)
    done = run([*PYTHON, str(PROBE)])
    assert done.code == 0, done.out
    records = [line.split("\t") for line in done.stdout.splitlines()]
    assert ["T", topic, "Asked for", "alice"] in records
    board = {r[1]: r for r in records if r[0] == "B"}
    assert board[f"{topic}/01-inherits"][8] == "alice"
    assert board[f"{topic}/02-overrides"][8] == "@bob"
    # The K record keeps its fifteen positions: additive, never renumbered.
    assert all(len(r) == 15 for r in records if r[0] == "K")
