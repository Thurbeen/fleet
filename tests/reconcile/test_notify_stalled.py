"""A stalled worker reaches the lead once, the way ready work does.

`plan --json` names the stalled set and `notify_lead.py` carries it, under the
same three rules as the ready notice: once per transition, never typed into a
lead mid-turn (its mailbox gets the line instead), and one line.
"""

from __future__ import annotations

import json

from harness import PYTHON, REPO, expect, refute, run, write
from reconcilekit import LEAD

NOTIFY = REPO / "scripts" / "lib" / "notify_lead.py"


def notify(state_dir, ready=(), stalled=()):
    plan = json.dumps({"ready": list(ready), "stalled": [{"task": r, "idle_secs": 7200} for r in stalled]})
    return run([*PYTHON, str(NOTIFY), "--state-dir", str(state_dir)], stdin=plan, FLEET_LEAD_SESSION=LEAD)


def lead(stubs, state: str) -> None:
    """The lead in `state`, with an empty input line: a wake types into nothing else."""
    write(stubs.root / "sessions" / "lead-uuid.json",
          json.dumps({"id": "lead-uuid", "name": LEAD, "state": state}) + "\n")
    stubs.composer("lead-uuid", "")


def test_a_stalled_worker_is_told_once(stubs, tmp_path):
    def sends() -> list[str]:
        return stubs.calls("thurbox-cli", "session send")

    lead(stubs, "idle")
    assert notify(tmp_path, stalled=["alpha/01-first"]).code == 0
    assert len(sends()) == 1, "a stalled worker never reached the lead"
    expect(sends()[0], "alpha/01-first", "stalled", "uv run fleet queue show alpha/01-first")
    refute(sends()[0], "ready and nothing will dispatch")

    notify(tmp_path, stalled=["alpha/01-first"])
    assert len(sends()) == 1, "the same stall is told once, not once per pass"

    notify(tmp_path, ready=["beta/02-second"], stalled=["alpha/01-first"])
    assert len(sends()) == 2, "fresh ready work is still its own transition"
    expect(sends()[-1], "beta/02-second")

    notify(tmp_path, stalled=[])
    notify(tmp_path, stalled=["alpha/01-first"])
    assert len(sends()) == 3, "a stall that clears and comes back is news again"


def test_a_lead_mid_turn_finds_the_stall_in_its_inbox(stubs, tmp_path):
    lead(stubs, "working")
    notify(tmp_path, stalled=["alpha/01-first"])
    assert stubs.calls("thurbox-cli", "session send") == []
    box = stubs.inbox("lead-uuid")
    assert len(box) == 1 and not box[0]["woke"]
    expect(box[0]["body"], "alpha/01-first", "stalled")
    notify(tmp_path, stalled=["alpha/01-first"])
    assert len(stubs.inbox("lead-uuid")) == 1, "posted once per transition"
