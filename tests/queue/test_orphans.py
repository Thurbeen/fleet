"""`fleet sessions orphans`: the sessions the lead spawned that no live task holds.

`reap` only ever releases a session the queue recorded. A review session a
skill spawned, a diagnosis sweep, a worker attached to nothing — all parented
to the lead, all invisible to every queue view — were the ones the operator
asked to clean up, again and again. This lists them, and deletes nothing.
"""

import json

from queuekit import S1, S2

from harness import expect, refute, run_fleet, write

LEAD = "Gate Control"
LEAD_ID = "lead-uuid"
REVIEW = "33333333-3333-3333-3333-333333333333"
STRANGER = "44444444-4444-4444-4444-444444444444"


def session(stubs, sid: str, name: str, parent: str | None, state: str = "idle") -> None:
    write(stubs.root / "sessions" / f"{sid}.json", json.dumps({
        "id": sid, "name": name, "state": state, "parent_session_id": parent,
        "hook_state_age_secs": 7200, "cwd": "/work", "backend_type": "local:tmux", "worktrees": [],
    }))


def test_it_lists_the_leads_children_no_live_task_holds(attached, stubs):
    session(stubs, LEAD_ID, LEAD, None)
    session(stubs, S1, "worker one", LEAD_ID)          # 01's worker
    session(stubs, S2, "worker two", LEAD_ID)          # 02's worker
    session(stubs, REVIEW, "review prs", LEAD_ID)      # a skill's session
    session(stubs, STRANGER, "by hand", None)          # not the lead's

    run = run_fleet("sessions", "orphans", FLEET_LEAD_SESSION=LEAD)
    assert run.code == 0, run.out
    expect(run.out, REVIEW, "review prs", "thurbox-cli session delete")
    refute(run.out, S1, S2, STRANGER)
    assert stubs.calls("thurbox-cli", "session delete") == [], "listing deletes nothing"

    doc = json.loads(run_fleet("sessions", "orphans", "--json", FLEET_LEAD_SESSION=LEAD).stdout)
    assert [row["id"] for row in doc["orphans"]] == [REVIEW]


def test_a_session_whose_task_has_ended_is_an_orphan_and_says_so(attached, stubs, queue_dir):
    session(stubs, LEAD_ID, LEAD, None)
    session(stubs, S1, "worker one", LEAD_ID)
    ref = f"{attached}/01-drop-idle-default"
    path = queue_dir / ref / "task.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("state: dispatched", "state: abandoned"),
                    encoding="utf-8")

    out = run_fleet("sessions", "orphans", FLEET_LEAD_SESSION=LEAD).out
    expect(out, S1, ref, "abandoned")


def test_no_lead_is_said_and_is_not_a_crash(attached, stubs):
    run = run_fleet("sessions", "orphans", FLEET_LEAD_SESSION=LEAD)
    assert run.code != 0
    expect(run.out, "no session named")
    refute(run.out, "Traceback")
