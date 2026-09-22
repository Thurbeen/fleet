"""`abandon`: the one way a task that will never run leaves the queue.

Eleven tasks of one topic were superseded when their work shipped as a single
pull request. They sat `queued` behind a condition nobody would ever clear,
read `waiting` in every view, and held their topic open forever — `unfinished()`
counted them. The only ways out were hand-editing `task.yaml`, which breaks
"the queue is the only writer", or clearing the condition, which would have made
them dispatchable.

`abandon` moves a task to the terminal state that already existed, records WHY
in the task and in its progress, and touches no session: `reap` owns those. It
refuses what is not the lead's to give up — a worker still running, unless
forced, and work that already landed — and it names every task left waiting on
the one it abandoned, because a blocker on an abandoned task never clears.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from kit_display import set_state_line
from queuekit import deletions, ok

from harness import expect, refute
from harness import run_queue as q

SUPERSEDED = "superseded: the work shipped as one pull request"
LIVE = "55555555-5555-5555-5555-555555555555"
GONE = "66666666-6666-6666-6666-666666666666"


def refused(run_) -> str:
    assert run_.code != 0, run_.out
    refute(run_.out, "Traceback")
    return run_.out


def record(queue_dir: Path, ref: str) -> dict:
    return yaml.safe_load((queue_dir / ref / "task.yaml").read_text(encoding="utf-8"))


def progress(queue_dir: Path, ref: str) -> list:
    path = queue_dir / ref / "progress.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture
def split(tmp_path, queue_dir) -> str:
    """The case this came from, small: a topic whose remaining tasks were superseded.

    01 landed. 02 and 03 are queued, 03 behind the condition that says why it
    will never run. `downstream/01-consumer`, in another topic, waits on 02.
    """
    repo = tmp_path / "repo-abandon"
    repo.mkdir()
    topic = ok(q("topic", "add", "split-work", "--title", "Split the work into many PRs",
                 "--prompt", "one PR per step")).stdout.strip()
    for n, slug in (("01", "first-step"), ("02", "second-step"), ("03", "third-step")):
        ok(q("add", topic, slug, "--title", slug, "--repo", str(repo), "--branch", f"fix/{slug}",
             "--number", n))
    set_state_line(queue_dir / topic / "01-first-step" / "task.yaml", "landed")
    ok(q("block", f"{topic}/03-third-step", "--condition", "the operator wants one PR",
         "--kind", "undecided", "--why", "superseded"))

    other = ok(q("topic", "add", "downstream", "--title", "Build on the split",
                 "--prompt", "use what step two adds")).stdout.strip()
    ok(q("add", other, "consumer", "--title", "consumer", "--repo", str(repo),
         "--branch", "fix/consumer", "--number", "01"))
    ok(q("block", f"{other}/01-consumer", "--on", f"{topic}/02-second-step",
         "--kind", "semantic-dependency", "--why", "reads what step two adds"))
    return topic


def test_queued_and_condition_held_tasks_are_abandoned_and_the_topic_archives(split, queue_dir):
    refs = [f"{split}/02-second-step", f"{split}/03-third-step"]
    # A reason is the whole record, so there is no abandoning without one.
    refused(q("abandon", *refs))
    refused(q("abandon", *refs, "--why", "   "))
    assert record(queue_dir, refs[0])["state"] == "queued"

    out = ok(q("abandon", *refs, "--why", SUPERSEDED)).out
    expect(out, "02-second-step", "03-third-step", "abandoned")
    for ref in refs:
        doc = record(queue_dir, ref)
        assert doc["state"] == "abandoned"
        assert doc["abandoned"]["why"] == SUPERSEDED
        assert any((e.get("abandoned") or {}).get("why") == SUPERSEDED
                   for e in progress(queue_dir, ref))

    # Every task is terminal now, so the topic archives — the same flag, the
    # same predicate, and no separate command.
    expect((queue_dir / split / "topic.yaml").read_text(encoding="utf-8"), "archived:")
    refute(q("list").out, "split-work —")

    # The reason is on every surface that shows the task.
    expect(q("list", "--all").out, "03-third-step", "abandoned", SUPERSEDED)
    expect(q("show", refs[1]).out, "state:       abandoned", SUPERSEDED)
    runs = list(Path(queue_dir.parent / "runs").glob(f"*-{split}.md"))
    assert runs, "the run log was not written"
    expect(runs[0].read_text(encoding="utf-8"), "abandoned", SUPERSEDED)
    # A record this verb wrote is still one the queue validates.
    expect(q("check").out, "ok")

    # Abandoning twice changes nothing and says so.
    expect(ok(q("abandon", refs[0], "--why", "again")).out, "already abandoned")
    assert record(queue_dir, refs[0])["abandoned"]["why"] == SUPERSEDED


def test_a_live_dispatched_task_is_refused_without_force_and_no_session_is_touched(split, stubs, queue_dir):
    ref = f"{split}/02-second-step"
    stubs.session_is(LIVE, "working")
    ok(q("attach", ref, LIVE))

    out = refused(q("abandon", ref, "--why", SUPERSEDED))
    expect(out, LIVE, "--force")
    assert record(queue_dir, ref)["state"] == "dispatched"

    out = ok(q("abandon", ref, "--why", SUPERSEDED, "--force")).out
    expect(out, "abandoned", "reap")
    doc = record(queue_dir, ref)
    assert doc["state"] == "abandoned"
    assert doc["abandoned"]["forced"] is True
    # The session is reap's, never this verb's.
    assert doc["session"] == LIVE
    assert LIVE not in deletions(stubs)


def test_a_dispatched_task_whose_session_is_gone_needs_no_force(split, stubs, queue_dir):
    ref = f"{split}/02-second-step"
    ok(q("attach", ref, GONE))
    ok(q("abandon", ref, "--why", "its worker died and the work is moot"))
    assert record(queue_dir, ref)["state"] == "abandoned"


def test_landed_is_refused_even_with_force(split, queue_dir):
    ref = f"{split}/01-first-step"
    for extra in ((), ("--force",)):
        expect(refused(q("abandon", ref, "--why", SUPERSEDED, *extra)), "landed")
        assert record(queue_dir, ref)["state"] == "landed"
    # One refusal writes nothing, even for the refs beside it that were fine.
    refused(q("abandon", f"{split}/02-second-step", ref, "--why", SUPERSEDED))
    assert record(queue_dir, f"{split}/02-second-step")["state"] == "queued"


def test_topic_abandons_every_non_terminal_task_and_leaves_landed_alone(split, queue_dir):
    out = ok(q("abandon", "--topic", split, "--why", SUPERSEDED)).out
    expect(out, "02-second-step", "03-third-step")
    assert record(queue_dir, f"{split}/01-first-step")["state"] == "landed"
    assert record(queue_dir, f"{split}/02-second-step")["state"] == "abandoned"
    assert record(queue_dir, f"{split}/03-third-step")["state"] == "abandoned"
    expect((queue_dir / split / "topic.yaml").read_text(encoding="utf-8"), "archived:")

    # Refs and --topic are one or the other.
    refused(q("abandon", f"{split}/02-second-step", "--topic", split, "--why", SUPERSEDED))
    refused(q("abandon", "--topic", "no-such-topic", "--why", SUPERSEDED))


def test_dependants_are_reported_and_stay_blocked(split, queue_dir):
    out = ok(q("abandon", f"{split}/02-second-step", "--why", SUPERSEDED)).out
    # Named, with what happens to them and the two ways the lead can decide.
    expect(out, "downstream/01-consumer", "stays blocked", "block downstream/01-consumer --clear",
           "abandon downstream/01-consumer")
    consumer = record(queue_dir, "downstream/01-consumer")
    assert consumer["state"] == "queued"
    assert consumer["blocked_by"][0]["task"] == f"{split}/02-second-step"
    expect(q("plan").out, "UNCLEARABLE", "which is abandoned")
