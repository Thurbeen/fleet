"""`ON_LANDED` and `ON_CLOSED`: what a finished task's session is left as.

`reap` deletes a task's session once its work merged or its change request
closed unmerged, and that is still the default. An operator who wants the
conversation for follow-ups sets `notify` in `orchestration/reconcile.conf`:
the task still lands — blockers clear, topics archive — and the session is
kept and told so, in one line, once, and only when it is at rest. It is then
an orphan the operator deletes when done with it.
"""

import json

import yaml
from queuekit import S1, deletions, ok, result

from harness import expect, refute, run_fleet, write
from harness import run_queue as q

LEAD, LEAD_ID = "Gate Control", "lead-uuid"
REF = "01-drop-idle-default"


def conf(isolated_env, text: str) -> None:
    write(isolated_env / "settings" / "orchestration" / "reconcile.conf", text)


def shipped(attached, stubs, queue_dir) -> None:
    stubs.session_is(S1, "idle")
    stubs.pipeline_pr(999, "fix/drop-idle-default")
    result(queue_dir / attached / REF, "shipped", "Done.", "https://github.com/Thurbeen/thurbox/pull/999")
    ok(q("collect"))


def task(queue_dir, attached) -> dict:
    return yaml.safe_load((queue_dir / attached / REF / "task.yaml").read_text(encoding="utf-8"))


def told(stubs) -> list[str]:
    return [c for c in stubs.calls("thurbox-cli", "session send") if S1 in c]


def test_by_default_a_landed_task_s_session_is_deleted(attached, stubs, queue_dir):
    shipped(attached, stubs, queue_dir)
    stubs.pr_state(999, "MERGED")
    expect(ok(q("reap")).out, "reaped")
    assert S1 in deletions(stubs) and told(stubs) == []


def test_notify_keeps_the_session_tells_it_once_and_still_lands(attached, stubs, queue_dir, isolated_env):
    conf(isolated_env, "ON_LANDED=notify\n")
    shipped(attached, stubs, queue_dir)
    stubs.pr_state(999, "MERGED")
    out = ok(q("reap")).out
    expect(out, "told", "ON_LANDED=notify")
    assert S1 not in deletions(stubs), deletions(stubs)
    [line] = told(stubs)
    expect(line, "landed", "kept for follow-ups")
    doc = task(queue_dir, attached)
    assert doc["state"] == "landed"
    assert doc["reaped"]["how"] == "kept" and doc["session"] is None
    # 03 waited on 01: the landing released it exactly as a delete would have.
    expect(ok(q("plan")).out, "03-render-detected-agent")

    # Told once: later passes neither send again nor delete.
    ok(q("reap"))
    ok(q("collect"))
    assert len(told(stubs)) == 1 and S1 not in deletions(stubs)

    # And it is the operator's now: an orphan, listed for deletion.
    write(stubs.root / "sessions" / f"{LEAD_ID}.json",
          json.dumps({"id": LEAD_ID, "name": LEAD, "state": "idle", "parent_session_id": None}))
    record = json.loads((stubs.root / "sessions" / f"{S1}.json").read_text(encoding="utf-8"))
    write(stubs.root / "sessions" / f"{S1}.json", json.dumps({**record, "parent_session_id": LEAD_ID}))
    expect(run_fleet("sessions", "orphans", FLEET_LEAD_SESSION=LEAD).out, S1, "landed")


def test_notify_waits_for_a_busy_session_and_never_types_into_a_turn(attached, stubs, queue_dir, isolated_env):
    conf(isolated_env, "ON_LANDED=notify\n")
    shipped(attached, stubs, queue_dir)
    stubs.session_is(S1, "working")
    stubs.pr_state(999, "MERGED")
    expect(ok(q("reap")).out, "kept", "told once it is at rest")
    assert told(stubs) == [] and S1 not in deletions(stubs)
    doc = task(queue_dir, attached)
    assert doc["state"] == "landed" and doc["session"] == S1

    stubs.session_is(S1, "idle")
    expect(ok(q("reap")).out, "told")
    assert len(told(stubs)) == 1 and S1 not in deletions(stubs)


def test_on_closed_is_its_own_setting(attached, stubs, queue_dir, isolated_env):
    conf(isolated_env, "ON_CLOSED=notify\n")
    shipped(attached, stubs, queue_dir)
    stubs.pr_state(999, "CLOSED")
    out = ok(q("reap")).out
    expect(out, "told", "ON_CLOSED=notify")
    [line] = told(stubs)
    expect(line, "abandoned", "closed without merging")
    doc = task(queue_dir, attached)
    assert doc["state"] == "abandoned" and doc["abandoned"]["how"] == "closed-unmerged"
    assert S1 not in deletions(stubs)


def test_on_landed_does_not_keep_a_closed_one(attached, stubs, queue_dir, isolated_env):
    conf(isolated_env, "ON_LANDED=notify\n")
    shipped(attached, stubs, queue_dir)
    stubs.pr_state(999, "CLOSED")
    ok(q("reap"))
    assert S1 in deletions(stubs) and told(stubs) == []


def test_a_word_it_does_not_know_keeps_the_session_and_says_so(attached, stubs, queue_dir, isolated_env):
    conf(isolated_env, "ON_LANDED=notfy\n")
    shipped(attached, stubs, queue_dir)
    stubs.pr_state(999, "MERGED")
    out = ok(q("reap")).out
    expect(out, "ON_LANDED=notfy is neither delete nor notify")
    assert S1 not in deletions(stubs) and told(stubs) == []
    refute(out, "reaped ")
