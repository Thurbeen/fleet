"""`fleet queue prs --records`: the PR store as the board's dots read it.

The board is Lua with no JSON parser, so the store comes to it as one `S` line
per session and one `P` line per change request, each carrying the one word its
dot is drawn as. The word is decided here, once: terminal is terminal, an entry
the shepherd could not refresh — or has not refreshed in two passes — is
`stale`, and nothing unknown is ever `ready`. A fleet whose shepherd never ran
prints nothing at all.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from harness import run_queue as q

def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def entry(number: int, **extra) -> dict:
    e = {
        "repo": "github.com/example/repo", "number": number, "url": f"https://github.com/example/repo/pull/{number}",
        "title": f"PR {number}", "head_branch": "feat/x", "head_sha": "", "state": "open", "checks": "passing",
        "failing_checks": [], "review_decision": None, "unresolved_threads": 0, "mergeable": "mergeable",
        "condition": "ready", "detail": "", "action": "none", "action_note": "",
        "observed_at": now().isoformat(), "stale": False, "stale_reason": "",
    }
    e.update(extra)
    return e


def store(sessions: dict) -> None:
    root = Path(os.environ["FLEET_SHEPHERD_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    doc = {"schema": 1, "written_at": now().isoformat(), "sessions": sessions}
    (root / "prs.json").write_text(json.dumps(doc), encoding="utf-8")


def records(**env) -> list[list[str]]:
    out = q("prs", "--records", **env)
    assert out.code == 0, out.out
    return [line.split("\t") for line in out.stdout.splitlines()]


def dots(rows) -> dict[int, str]:
    return {int(r[3]): r[2] for r in rows if r[0] == "P"}


def test_every_dot_word_from_the_store():
    old = (now() - timedelta(hours=2)).isoformat()
    store({"w1": {"session": "w1", "name": "worker", "task": "topic/01-work", "adopted": False, "prs": [
        entry(1),
        entry(2, checks="pending"),
        entry(3, checks="failing", mergeable="conflicting"),
        entry(4, mergeable="conflicting"),
        entry(5, review_decision="changes-requested"),
        entry(6, unresolved_threads=2),
        entry(7, state="merged", observed_at=old),
        entry(8, state="closed"),
        entry(9, stale=True, stale_reason="gh could not be read"),
        entry(10, observed_at=old),
        entry(11, mergeable=None),
        entry(12, state="draft"),
        entry(13, checks="none"),
    ]}})
    rows = records()
    assert dots(rows) == {
        1: "ready", 2: "pending", 3: "failing", 4: "conflicting", 5: "changes", 6: "changes", 7: "merged",
        8: "closed", 9: "stale", 10: "stale", 11: "pending", 12: "pending", 13: "pending",
    }
    by = {int(r[3]): r for r in rows if r[0] == "P"}
    assert by[9][12] == "gh could not be read"
    assert by[10][12], "a dot gone stale by age says why"
    assert by[6][9] == "2" and by[5][8] == "changes-requested"


def test_the_freshness_window_is_two_shepherd_passes():
    store({"w1": {"session": "w1", "name": "w", "task": "t/01", "adopted": False,
                  "prs": [entry(1, observed_at=(now() - timedelta(minutes=25)).isoformat())]}})
    assert dots(records()) == {1: "ready"}, "25 minutes is inside two 15-minute passes"
    assert dots(records(FLEET_RECONCILE_SHEPHERD_SECS="600")) == {1: "stale"}


def test_sessions_say_whose_they_are_and_no_field_can_split_a_record():
    store({
        "a1": {"session": "a1", "name": "hand\tmade", "task": None, "adopted": True,
               "prs": [entry(7, title="two\nlines\tand a tab")]},
        "w1": {"session": "w1", "name": "worker", "task": "topic/01-work", "adopted": False, "prs": []},
    })
    rows = records()
    assert ["S", "a1", "", "1", "hand made"] in rows
    assert ["S", "w1", "topic/01-work", "0", "worker"] in rows
    pr = next(r for r in rows if r[0] == "P")
    assert len(pr) == 14 and pr[1] == "a1" and pr[13] == "two lines and a tab"


def test_no_store_prints_nothing_and_an_unreadable_one_is_one_E_line():
    out = q("prs", "--records")
    assert out.code == 0 and out.stdout == "", out.out
    root = Path(os.environ["FLEET_SHEPHERD_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "prs.json").write_text("{not json", encoding="utf-8")
    out = q("prs", "--records")
    assert out.code == 0 and out.stdout.startswith("E\t") and len(out.stdout.splitlines()) == 1, out.out
