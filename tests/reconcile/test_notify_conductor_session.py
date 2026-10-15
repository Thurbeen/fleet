"""The notices go to the fleet's own conductor while it runs, and to the lead
whenever it does not.

A fleet with `CONDUCTOR=session` declares a second session whose whole job is the
reconciler's notices. The lead keeps the operator's conversation. But a
conductor that was deleted, closed or never spawned must not be a notice
delivered to nobody — that is the six hours `notify_conductor.py` exists to
prevent — so the lead takes the notices back the moment the conductor is not
listed, and hands them over again when it is.
"""

from __future__ import annotations

import json

from harness import PYTHON, REPO, expect, run, write
from reconcilekit import LEAD

NOTIFY = REPO / "scripts" / "lib" / "notify_conductor.py"
CONDUCTOR = "Gate Conductor"


def notify(state_dir, ready=(), **env):
    plan = json.dumps({"ready": list(ready), "stalled": []})
    env = {"FLEET_LEAD_SESSION": LEAD, "FLEET_CONDUCTOR_SESSION": CONDUCTOR, **env}
    return run([*PYTHON, str(NOTIFY), "--state-dir", str(state_dir)], stdin=plan, **env)


def listed(stubs, *rows: tuple[str, str]) -> None:
    """Exactly these sessions, each at rest with an empty line."""
    for old in (stubs.root / "sessions").glob("*.json"):
        old.unlink()
    for sid, name in rows:
        write(stubs.root / "sessions" / f"{sid}.json",
              json.dumps({"id": sid, "name": name, "state": "idle", "agent_session_id": f"c-{sid}"}) + "\n")
        stubs.composer(sid, "")


def sends(stubs) -> list[str]:
    return stubs.calls("thurbox-cli", "session send")


def test_the_conductor_is_told_and_the_lead_is_not(stubs, tmp_path):
    listed(stubs, ("lead-1", LEAD), ("conductor-1", CONDUCTOR))
    notify(tmp_path, ready=["alpha/01-first"])
    assert len(sends(stubs)) == 1, sends(stubs)
    expect(sends(stubs)[0], "session send conductor-1", "alpha/01-first")


def test_no_conductor_running_is_the_lead_told_and_its_return_is_told_again(stubs, tmp_path):
    listed(stubs, ("lead-1", LEAD))
    notify(tmp_path, ready=["alpha/01-first"])
    expect(sends(stubs)[-1], "session send lead-1", "alpha/01-first")

    # The conductor comes up: a new session to the memory, so it is told the
    # standing set rather than inheriting the lead's "already told".
    listed(stubs, ("lead-1", LEAD), ("conductor-1", CONDUCTOR))
    notify(tmp_path, ready=["alpha/01-first"])
    assert len(sends(stubs)) == 2, sends(stubs)
    expect(sends(stubs)[-1], "session send conductor-1", "alpha/01-first")

    # And deleted again: the lead takes the notices back.
    listed(stubs, ("lead-1", LEAD))
    notify(tmp_path, ready=["alpha/01-first", "beta/02-second"])
    expect(sends(stubs)[-1], "session send lead-1", "beta/02-second")


def test_a_fleet_that_runs_no_conductor_tells_the_lead_as_it_always_did(stubs, tmp_path):
    """FLEET_LEAD_SESSION alone names the whole setup: no conductor is looked for."""
    listed(stubs, ("lead-1", LEAD), ("conductor-1", CONDUCTOR))
    notify(tmp_path, ready=["alpha/01-first"], FLEET_CONDUCTOR_SESSION=None)
    expect(sends(stubs)[-1], "session send lead-1", "alpha/01-first")


def test_a_conductor_mid_turn_gets_the_mailbox_note_and_not_the_lead(stubs, tmp_path):
    listed(stubs, ("lead-1", LEAD), ("conductor-1", CONDUCTOR))
    record = stubs.root / "sessions" / "conductor-1.json"
    row = json.loads(record.read_text(encoding="utf-8"))
    write(record, json.dumps({**row, "state": "working"}) + "\n")
    notify(tmp_path, ready=["alpha/01-first"])
    assert sends(stubs) == [], "a conductor mid-turn is not typed into, and the lead is not a fallback for busy"
    assert stubs.inbox("conductor-1"), "the note waits in the conductor's mailbox"
    assert not stubs.inbox("lead-1")
