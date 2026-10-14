"""A pull request on a session's branch that shepherd tells nobody about reaches the conductor once.

`plan --json`'s `unwatched_prs` names them, keyed by PR, condition and head,
and `notify_conductor.py` carries them as a set of their own, under the same
rules as the ready notice. Before this, the conductor of 2026-10-14 had seen
only sync notices while a pull request on its fleet's machine sat CONFLICTING.
"""

from __future__ import annotations

import json

from harness import PYTHON, REPO, expect, refute, run, write
from reconcilekit import LEAD

NOTIFY = REPO / "scripts" / "lib" / "notify_conductor.py"
KEY = "Thurbeen/thurbox#1415 conflicting @0000abcd"


def notify(state_dir, *keys: str, ready=()):
    plan = json.dumps({"ready": list(ready), "stalled": [],
                       "unwatched_prs": [{"key": k, "pr": "https://example.invalid/pull/1415",
                                          "condition": "conflicting", "session": "s-1",
                                          "session_name": "Add session keys"} for k in keys]})
    return run([*PYTHON, str(NOTIFY), "--state-dir", str(state_dir)], stdin=plan, FLEET_LEAD_SESSION=LEAD)


def lead(stubs, state: str) -> None:
    write(stubs.root / "sessions" / "lead-uuid.json",
          json.dumps({"id": "lead-uuid", "name": LEAD, "state": state}) + "\n")
    stubs.composer("lead-uuid", "")


def sends(stubs) -> list[str]:
    return stubs.calls("thurbox-cli", "session send")


def test_an_unwatched_conflict_is_told_once(stubs, tmp_path):
    lead(stubs, "idle")
    done = notify(tmp_path, KEY)
    assert done.code == 0
    assert len(sends(stubs)) == 1, "a conflicting PR nobody is told about never reached the conductor"
    expect(sends(stubs)[0], KEY, "shepherd tells nobody", "uv run fleet queue prs")
    refute(sends(stubs)[0], "ready and nothing will dispatch")
    expect(done.stdout, "unwatched-pr", "1 pull request(s) need a hand")

    notify(tmp_path, KEY)
    assert len(sends(stubs)) == 1, "the same PR, condition and head is told once"

    notify(tmp_path, "Thurbeen/thurbox#1415 conflicting @1111ffff")
    assert len(sends(stubs)) == 2, "a push that leaves it conflicting is news again"


def test_a_conductor_mid_turn_finds_it_in_its_inbox(stubs, tmp_path):
    lead(stubs, "working")
    notify(tmp_path, KEY)
    assert sends(stubs) == []
    box = stubs.inbox("lead-uuid")
    assert len(box) == 1 and not box[0]["woke"]
    expect(box[0]["body"], KEY)
    notify(tmp_path, KEY)
    assert len(stubs.inbox("lead-uuid")) == 1, "posted once per transition"


def test_a_plan_from_before_the_key_tells_nothing_new(stubs, tmp_path):
    lead(stubs, "idle")
    plan = json.dumps({"ready": [], "stalled": []})
    assert run([*PYTHON, str(NOTIFY), "--state-dir", str(tmp_path)], stdin=plan,
               FLEET_LEAD_SESSION=LEAD).code == 0
    assert sends(stubs) == []
