"""A worker may SAY it is waiting, and `plan` takes its word for a bounded while.

`stalled` is an inference — dispatched, at rest past half an hour, no result,
no new commit — so a worker waiting on CI or a reviewer by design looked
exactly like one that ended its turn early, and a worker that needed a decision
had no way to say so except ending its turn. `fleet queue declare` is the
worker's word, appended to its own progress.jsonl: `paused` until a time, which
keeps it out of `stalled` no longer than PAUSE_CAP_SECS, or `needs-input`,
which `plan` names in a set of its own. Only a declaration newer than the
task's last dispatch, restart or message counts.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from queuekit import S1, S2, ok

from harness import PYTHON, expect, refute, run
from harness import run_queue as q

HOUR = 3600
REF = "report-status-honestly/01-drop-idle-default"


def plan() -> dict:
    return json.loads(ok(q("plan", "--json")).stdout)


def stalled() -> list:
    return [r["task"] for r in plan().get("stalled", [])]


def stamp(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).replace(microsecond=0).isoformat()


def at_rest(stubs, queue_dir) -> None:
    """Dispatched before all historical declarations used below, so pause tests
    exercise expiry and the cap rather than dispatch invalidation."""
    stubs.session_is(S1, "idle", age=2 * HOUR)
    stubs.session_is(S2, "working", age=2 * HOUR)
    record = queue_dir / REF / "task.yaml"
    doc = yaml.safe_load(record.read_text(encoding="utf-8"))
    doc["dispatched_at"] = stamp(-timedelta(hours=9))
    record.write_text(yaml.safe_dump(doc), encoding="utf-8")


def test_a_future_pause_keeps_a_resting_worker_out_of_stalled(attached, stubs, queue_dir):
    at_rest(stubs, queue_dir)
    assert REF in stalled(), "the precondition: this worker is stall-eligible"
    ok(q("declare", REF, "paused", "--until", "2h", "--why", "waiting on CI"))
    assert REF not in stalled()
    rows = plan()["paused"]
    assert [r["task"] for r in rows] == [REF] and rows[0]["why"] == "waiting on CI"
    expect(q("plan").out, "paused: 1", "waiting on CI")


def test_a_pause_that_has_run_out_is_a_stall_again(attached, stubs, queue_dir):
    at_rest(stubs, queue_dir)
    line = {"declare": {"state": "paused", "until": stamp(-timedelta(minutes=5)), "why": "CI"},
            "observed": stamp(-timedelta(hours=1))}
    with open(queue_dir / REF / "progress.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    assert REF in stalled()


def test_a_pause_past_the_cap_counts_only_up_to_the_cap(attached, stubs, queue_dir):
    """Declared seven hours ago for a day: the cap ran out an hour ago."""
    at_rest(stubs, queue_dir)
    line = {"declare": {"state": "paused", "until": stamp(timedelta(hours=17)), "why": "review"},
            "observed": stamp(-timedelta(hours=7))}
    with open(queue_dir / REF / "progress.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    assert REF in stalled()


def test_the_command_refuses_a_pause_beyond_the_cap_or_in_the_past(attached):
    beyond = q("declare", REF, "paused", "--until", "7h", "--why", "a long review")
    assert beyond.code != 0
    expect(beyond.out, "6h", "block")
    assert q("declare", REF, "paused", "--until", stamp(-timedelta(hours=1)), "--why", "x").code != 0
    assert q("declare", REF, "paused", "--until", "2026-01-01T00:00:00", "--why", "x").code != 0, \
        "a time with no offset is refused rather than read as some machine's local time"


def test_needs_input_is_its_own_set_and_not_a_stall(attached, stubs, queue_dir):
    at_rest(stubs, queue_dir)
    ok(q("declare", REF, "needs-input", "--why", "keep the old flag or drop it?"))
    doc = plan()
    assert REF not in [r["task"] for r in doc["stalled"]]
    assert [r["task"] for r in doc["needs_input"]] == [REF]
    assert doc["needs_input"][0]["why"] == "keep the old flag or drop it?"
    expect(q("plan").out, "needs input: 1", "keep the old flag or drop it?")


def clocked_queue(instant: datetime, *args: str):
    """Real CLI and records, with each operation pinned within the same second."""
    boot = '''import sys
from datetime import datetime
from fleet.cli import load, main

instant = datetime.fromisoformat(sys.argv.pop(1))

class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return instant.astimezone(tz)

load("queue.py").datetime = Clock
sys.exit(main(sys.argv[1:]))
'''
    return run([*PYTHON, "-c", boot, instant.isoformat(), "queue", *args])


@pytest.mark.parametrize("state, bucket", [("needs-input", "needs_input"), ("paused", "paused")])
def test_a_message_answers_an_earlier_declaration_in_the_same_second(attached, stubs, queue_dir, state, bucket):
    at_rest(stubs, queue_dir)
    second = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=1)
    until = ["--until", "2h"] if state == "paused" else []
    ok(clocked_queue(second.replace(microsecond=100000), "declare", REF, state, "--why", "which?", *until))
    assert [row["task"] for row in plan()[bucket]] == [REF]
    ok(clocked_queue(second.replace(microsecond=200000), "send", REF, "drop it"))
    assert plan()[bucket] == []


@pytest.mark.parametrize("state, bucket", [("needs-input", "needs_input"), ("paused", "paused")])
def test_a_declaration_after_a_message_in_the_same_second_still_counts(attached, stubs, queue_dir, state, bucket):
    at_rest(stubs, queue_dir)
    second = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=1)
    ok(clocked_queue(second.replace(microsecond=100000), "send", REF, "drop it"))
    until = ["--until", "2h"] if state == "paused" else []
    ok(clocked_queue(second.replace(microsecond=200000), "declare", REF, state, "--why", "new wait", *until))
    assert [row["task"] for row in plan()[bucket]] == [REF]


@pytest.mark.parametrize("boundary", ["dispatch", "restart"])
def test_conversation_boundaries_order_declarations_within_a_second(attached, stubs, queue_dir, boundary):
    at_rest(stubs, queue_dir)
    second = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=1)
    ok(clocked_queue(second.replace(microsecond=100000), "declare", REF, "needs-input", "--why", "old"))
    if boundary == "dispatch":
        ok(clocked_queue(second.replace(microsecond=200000), "attach", REF, S1, "--prompted"))
    else:
        record = queue_dir / REF / "task.yaml"
        doc = yaml.safe_load(record.read_text(encoding="utf-8"))
        doc["refuels"] = [{"at": second.replace(microsecond=200000).isoformat(),
                           "session": S1, "why": "quota", "prompted": True}]
        record.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert plan()["needs_input"] == []
    ok(clocked_queue(second.replace(microsecond=300000), "declare", REF, "needs-input", "--why", "new"))
    assert [row["why"] for row in plan()["needs_input"]] == ["new"]


def test_an_undelivered_message_does_not_answer_a_declaration(attached, stubs, queue_dir):
    at_rest(stubs, queue_dir)
    second = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=1)
    ok(clocked_queue(second.replace(microsecond=100000), "declare", REF, "needs-input", "--why", "which?"))
    stubs.tool("thurbox-cli", "import sys; sys.exit(1)")
    failed = clocked_queue(second.replace(microsecond=200000), "send", REF, "drop it")
    assert failed.code != 0
    expect(failed.out, "NOT DELIVERED")
    assert [row["task"] for row in plan()["needs_input"]] == [REF]


def test_a_message_in_the_same_second_as_the_question_answers_it(attached, stubs, queue_dir):
    """An exact timestamp tie is ambiguous, so it goes to the
    message: the task falls back to the inference rather than hiding behind a question
    the lead may already have answered. CI on Windows ran declare and send inside
    one second and kept the question standing."""
    at_rest(stubs, queue_dir)
    ok(q("declare", REF, "needs-input", "--why", "which?"))
    ok(q("send", REF, "drop it"))
    record = yaml.safe_load((queue_dir / REF / "task.yaml").read_text(encoding="utf-8"))
    sent = record["sends"][-1]["at"]
    progress = queue_dir / REF / "progress.jsonl"
    rows = [json.loads(r) for r in progress.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if "declare" in row:
            row["observed"] = sent
    progress.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert plan()["needs_input"] == []


def test_a_declaration_before_a_restart_is_ignored(attached, stubs, queue_dir):
    at_rest(stubs, queue_dir)
    line = {"declare": {"state": "needs-input", "why": "old question"},
            "observed": stamp(-timedelta(hours=1))}
    pause = {"declare": {"state": "paused", "until": stamp(timedelta(hours=2)), "why": "old wait"},
             "observed": stamp(-timedelta(hours=1))}
    with open(queue_dir / REF / "progress.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n" + json.dumps(pause) + "\n")
    record = queue_dir / REF / "task.yaml"
    doc = yaml.safe_load(record.read_text(encoding="utf-8"))
    doc["refuels"] = [{"at": stamp(-timedelta(minutes=10)), "session": S1, "why": "quota", "prompted": True}]
    record.write_text(yaml.safe_dump(doc), encoding="utf-8")
    after = plan()
    assert after["needs_input"] == [] and after["paused"] == []
    assert REF in [r["task"] for r in after["stalled"]]


def test_the_declaration_is_no_stream_event_and_no_transition(attached, queue_dir):
    ok(q("declare", REF, "paused", "--until", "30m", "--why", "CI"))
    rows = [json.loads(r) for r in (queue_dir / REF / "progress.jsonl").read_text(encoding="utf-8").splitlines()]
    mine = [r for r in rows if "declare" in r]
    assert len(mine) == 1
    assert "seq" not in mine[0] and "at" not in mine[0], "watch's cursor and send's movement read those"


def test_only_a_dispatched_task_declares(briefed):
    refused = q("declare", "report-status-honestly/04-log-state-changes", "needs-input", "--why", "x")
    assert refused.code != 0
    expect(refused.out, "dispatched")


def test_a_local_brief_names_the_command(attached, queue_dir):
    brief = (queue_dir / REF / "BRIEF.md").read_text(encoding="utf-8")
    expect(brief, f"fleet queue declare {REF}", "needs-input")
    refute(brief, "<checkout>")
