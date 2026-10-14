"""Shepherd adopts sessions fleet did not spawn, and keeps one file of every watched PR.

A session the operator opened by hand can open a pull request that goes red
with nothing watching it. Shepherd now lists thurbox's sessions and adopts the
ones on a branch with an open PR — never the lead, never one a task holds — and
TELLS them, once per condition per head and only at rest. It never sends them
a fixer. And every pass writes `prs.json`, the store the board's dots read, for
task sessions and adopted ones alike; a source it cannot read leaves the last
entry in place, marked stale.
"""

import json
import os
from pathlib import Path

import pytest
from kit_shepherd import Shep
from queuekit import ok

from harness import expect, git, refute, write
from harness import run_queue as q

OUTSIDE = "0a0a0a0a-0000-0000-0000-000000000001"
HELD = "0b0b0b0b-0000-0000-0000-000000000002"
LEAD = "0c0c0c0c-0000-0000-0000-000000000003"
LEAD_NAME = "Mission Control"
FAILED = {"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"}


@pytest.fixture
def shep(stubs) -> Shep:
    return Shep(stubs)


def checkout(path: Path, branch: str) -> Path:
    """A real checkout of the public repo, sitting on `branch`, as a session's worktree is."""
    path.mkdir(parents=True)
    git("init", "-q", "-b", "main", str(path))
    git("remote", "add", "origin", "https://github.com/Thurbeen/fleet.git", cwd=path)
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=path)
    git("checkout", "-q", "-b", branch, cwd=path)
    return path


def session(stubs, sid: str, name: str, state: str, worktree: Path | None, branch: str = "") -> None:
    trees = [{"branch": branch, "repo_path": str(worktree), "worktree_path": str(worktree),
              "created_by_thurbox": True}] if worktree else []
    write(stubs.root / "sessions" / f"{sid}.json", json.dumps({
        "id": sid, "name": name, "state": state, "agent": "claude", "hook_reported": True,
        "backend_type": "local-tmux", "base_branch": "origin/main", "worktrees": trees,
    }) + "\n")


def sends(stubs, sid: str) -> list[str]:
    return stubs.calls("thurbox-cli", f"session send {sid}")


def store() -> dict:
    return json.loads((Path(os.environ["FLEET_SHEPHERD_DIR"]) / "prs.json").read_text(encoding="utf-8"))


@pytest.fixture
def outside(shep, stubs, tmp_path) -> Path:
    """A session nobody queued, on a branch whose PR has a failing check."""
    wt = checkout(tmp_path / "outside", "feat/outside")
    session(stubs, OUTSIDE, "hand-made", "working", wt, "feat/outside")
    shep.pr(201, headRefName="feat/outside", body="", statusCheckRollup=[FAILED])
    return wt


def test_an_outside_session_is_told_once_at_rest_and_never_while_working(outside, shep, stubs):
    out = q("shepherd").out
    expect(out, "sessions fleet did not spawn", "pull/201", "checks-failed", "the session is working")
    assert sends(stubs, OUTSIDE) == [], out
    entry = store()["sessions"][OUTSIDE]["prs"][0]
    assert entry["action"] == "left-alone"
    assert entry["sent"] == []

    session(stubs, OUTSIDE, "hand-made", "idle", outside, "feat/outside")
    out = q("shepherd").out
    told = sends(stubs, OUTSIDE)
    assert len(told) == 1, out + "\n".join(told)
    expect(told[0], "PR #201", "checks-failed", "Read ")
    brief = Path(told[0].split("Read ", 1)[1].split(" and do what it says")[0])
    expect(brief.read_text(encoding="utf-8"), "https://github.com/Thurbeen/fleet/pull/201", "lint",
           "will not merge")

    # The same condition on the same head is not news twice.
    q("shepherd")
    assert len(sends(stubs, OUTSIDE)) == 1, shep.tbx_log()
    # And it is never handed a fixer session.
    assert shep.creates() == [], shep.tbx_log()


def test_a_push_resets_what_the_session_was_told(outside, shep, stubs):
    session(stubs, OUTSIDE, "hand-made", "idle", outside, "feat/outside")
    q("shepherd")
    shep.update(201, headRefOid="e" * 40)
    q("shepherd")
    assert len(sends(stubs, OUTSIDE)) == 2, shep.tbx_log()


def test_the_lead_is_never_adopted(shep, stubs, tmp_path):
    wt = checkout(tmp_path / "lead", "feat/lead")
    session(stubs, LEAD, LEAD_NAME, "idle", wt, "feat/lead")
    shep.pr(203, headRefName="feat/lead", body="", statusCheckRollup=[FAILED])
    out = q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME).out
    assert sends(stubs, LEAD) == [], out
    assert LEAD not in store()["sessions"]


def test_this_fleets_conductor_is_never_adopted(shep, stubs, tmp_path):
    wt = checkout(tmp_path / "conductor", "feat/conductor")
    session(stubs, LEAD, "Gate Conductor", "idle", wt, "feat/conductor")
    shep.pr(203, headRefName="feat/conductor", body="", statusCheckRollup=[FAILED])
    out = q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME, FLEET_CONDUCTOR_SESSION="Gate Conductor").out
    assert sends(stubs, LEAD) == [], out
    assert LEAD not in store()["sessions"]


def test_a_session_held_by_a_task_is_not_adopted_and_both_land_in_the_store(outside, shep, stubs, tmp_path):
    session(stubs, OUTSIDE, "hand-made", "idle", outside, "feat/outside")
    held = checkout(tmp_path / "held", "main-ish")
    topic = ok(q("topic", "add", "adoption", "--title", "Adoption",
                 "--prompt", "shepherd outside sessions")).stdout.strip()
    ok(q("add", topic, "held", "--title", "A task's own worker", "--repo", str(held),
         "--branch", "feat/held", "--number", "01"))
    git("checkout", "-q", "-b", "feat/held", cwd=held)
    session(stubs, HELD, "queued worker", "idle", held, "feat/held")
    ok(q("attach", f"{topic}/01-held", HELD))
    shep.pr(202, headRefName="feat/held", body="", statusCheckRollup=[FAILED])

    out = q("shepherd").out
    # The task's worker gets the queue's own fix brief, and no adoption line.
    refute("\n".join(sends(stubs, HELD)), "fleet shepherd: PR #")
    assert len(sends(stubs, OUTSIDE)) == 1, out

    sessions = store()["sessions"]
    task, adopted = sessions[HELD], sessions[OUTSIDE]
    assert task["adopted"] is False
    assert task["task"] == f"{topic}/01-held"
    assert adopted["adopted"] is True
    assert adopted["task"] is None
    assert adopted["name"] == "hand-made"
    for s in (task, adopted):
        pr = s["prs"][0]
        assert set(pr) >= {
            "repo", "number", "url", "title", "head_branch", "state", "checks", "failing_checks",
            "review_decision", "unresolved_threads", "mergeable", "condition", "action", "observed_at",
            "stale",
        }, pr
        assert pr["repo"] == "github.com/Thurbeen/fleet"
        assert pr["state"] == "open"
        assert pr["checks"] == "failing"
        assert pr["failing_checks"] == ["lint"]
        assert pr["condition"] == "checks-failed"
        assert pr["stale"] is False
    assert adopted["prs"][0]["action"] == "sent"
    assert task["prs"][0]["number"] == 202

    # `fleet queue prs` prints exactly that file.
    assert json.loads(q("prs", "--json").stdout) == store()
    expect(q("prs").out, "hand-made", "(adopted)", "#201", f"{topic}/01-held")


def test_an_unreadable_forge_keeps_the_old_entry_marked_stale(outside, stubs):
    q("shepherd")
    before = store()["sessions"][OUTSIDE]["prs"][0]
    assert before["stale"] is False

    out = q("shepherd", SHEP_GH_DOWN="1").out
    after = store()["sessions"][OUTSIDE]["prs"][0]
    assert after["stale"] is True, out
    expect(after["stale_reason"], "could not connect")
    assert after["observed_at"] == before["observed_at"]
    assert after["number"] == 201


def test_unreadable_thurbox_keeps_adopted_entries_marked_stale(outside, stubs):
    q("shepherd")
    stubs.tool("thurbox-cli", "import sys\nsys.stderr.write('thurbox: no server\\n')\nraise SystemExit(1)\n")
    q("shepherd")
    entry = store()["sessions"][OUTSIDE]["prs"][0]
    assert entry["stale"] is True
    expect(entry["stale_reason"], "thurbox could not be read")


def test_a_dry_run_sends_nothing_and_writes_no_store(outside, stubs):
    session(stubs, OUTSIDE, "hand-made", "idle", outside, "feat/outside")
    out = q("shepherd", "--dry-run").out
    expect(out, "would-send")
    assert sends(stubs, OUTSIDE) == []
    assert not (Path(os.environ["FLEET_SHEPHERD_DIR"]) / "prs.json").exists()


def session_row(stubs, sid: str, **extra) -> None:
    path = stubs.root / "sessions" / f"{sid}.json"
    write(path, json.dumps({**json.loads(path.read_text(encoding="utf-8")), **extra}) + "\n")


def test_a_session_that_checked_out_somebody_elses_branch_is_not_told_to_push_it(shep, stubs, tmp_path):
    """`gh pr checkout` in a reviewer's worktree: the branch is born from its own remote copy."""
    wt = checkout(tmp_path / "reviewer", "review-201")
    git("update-ref", "refs/remotes/origin/feat/theirs", "HEAD", cwd=wt)
    git("switch", "-q", "feat/theirs", cwd=wt)
    session(stubs, OUTSIDE, "reviewer", "idle", wt, "review-201")
    shep.pr(201, headRefName="feat/theirs", body="", statusCheckRollup=[FAILED])
    out = q("shepherd").out
    assert sends(stubs, OUTSIDE) == [], out
    assert OUTSIDE not in store()["sessions"]


def test_a_session_that_started_another_branch_of_its_own_is_still_adopted(shep, stubs, tmp_path):
    wt = checkout(tmp_path / "moved", "created-by-thurbox")
    git("checkout", "-q", "-b", "fix/its-own", cwd=wt)
    session(stubs, OUTSIDE, "moved on", "idle", wt, "created-by-thurbox")
    shep.pr(201, headRefName="fix/its-own", body="", statusCheckRollup=[FAILED])
    out = q("shepherd").out
    assert len(sends(stubs, OUTSIDE)) == 1, out


@pytest.mark.parametrize(("parent_name", "adopted"), [
    ("\U0001f4e1 Mission Control · other-fleet", False),
    ("\U0001f3bc Conductor · other-fleet", False),
    ("\U0001f3bc Conductor", False),
    ("an ordinary session", True),
])
def test_a_session_parented_to_any_fleets_lead_is_that_fleets_and_not_adopted(
    outside, shep, stubs, parent_name, adopted
):
    session(stubs, LEAD, parent_name, "idle", None)
    session(stubs, OUTSIDE, "hand-made", "idle", outside, "feat/outside")
    session_row(stubs, OUTSIDE, parent_session_id=LEAD)
    out = q("shepherd").out
    assert len(sends(stubs, OUTSIDE)) == (1 if adopted else 0), out
    assert (OUTSIDE in store()["sessions"]) is adopted


def test_another_fleets_lead_is_never_adopted(shep, stubs, tmp_path):
    wt = checkout(tmp_path / "peer-lead", "feat/peer")
    session(stubs, LEAD, "\U0001f4e1 Mission Control · other-fleet", "idle", wt, "feat/peer")
    shep.pr(203, headRefName="feat/peer", body="", statusCheckRollup=[FAILED])
    out = q("shepherd").out
    assert sends(stubs, LEAD) == [], out


GONE = "0d0d0d0d-0000-0000-0000-000000000004"
MIDDLE = "0e0e0e0e-0000-0000-0000-000000000005"
PEER_LEAD = "\U0001f4e1 Mission Control · other-fleet"


def test_a_session_whose_parent_is_gone_is_adopted(outside, shep, stubs):
    """Its parent is not listed any more. Nothing says it is another fleet's, so it is told."""
    session(stubs, LEAD, PEER_LEAD, "idle", None)  # another fleet's lead, but not this one's parent
    session(stubs, OUTSIDE, "hand-made", "idle", outside, "feat/outside")
    session_row(stubs, OUTSIDE, parent_session_id=GONE)
    out = q("shepherd").out
    assert len(sends(stubs, OUTSIDE)) == 1, out
    assert OUTSIDE in store()["sessions"]


@pytest.mark.parametrize(("grandparent", "adopted"), [
    (PEER_LEAD, False),
    (None, True),  # the grandparent is not listed
    ("the operator's own session", True),
    (LEAD_NAME, True),  # this fleet's own lead: its descendants are told, too
])
def test_a_session_is_judged_by_its_whole_ancestry(outside, shep, stubs, grandparent, adopted):
    """Another fleet's worker's child is still that fleet's: the chain is walked to its root."""
    session(stubs, MIDDLE, "a worker's helper", "idle", None)
    session_row(stubs, MIDDLE, parent_session_id=LEAD if grandparent else GONE)
    if grandparent:
        session(stubs, LEAD, grandparent, "idle", None)
    session(stubs, OUTSIDE, "hand-made", "idle", outside, "feat/outside")
    session_row(stubs, OUTSIDE, parent_session_id=MIDDLE)
    out = q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME).out
    assert len(sends(stubs, OUTSIDE)) == (1 if adopted else 0), out
    assert (OUTSIDE in store()["sessions"]) is adopted


def test_a_child_of_this_queues_worker_is_told_itself(outside, shep, stubs, tmp_path):
    held = checkout(tmp_path / "held", "main-ish")
    topic = ok(q("topic", "add", "adoption", "--title", "Adoption",
                 "--prompt", "shepherd outside sessions")).stdout.strip()
    ok(q("add", topic, "held", "--title", "A task's own worker", "--repo", str(held),
         "--branch", "feat/held", "--number", "01"))
    git("checkout", "-q", "-b", "feat/held", cwd=held)
    session(stubs, HELD, "queued worker", "idle", held, "feat/held")
    ok(q("attach", f"{topic}/01-held", HELD))
    session(stubs, OUTSIDE, "hand-made", "idle", outside, "feat/outside")
    session_row(stubs, OUTSIDE, parent_session_id=HELD)
    out = q("shepherd").out
    assert len(sends(stubs, OUTSIDE)) == 1, out
    assert OUTSIDE in store()["sessions"]


def test_a_parent_cycle_does_not_hang(outside, shep, stubs):
    session(stubs, MIDDLE, "loop", "idle", None)
    session_row(stubs, MIDDLE, parent_session_id=OUTSIDE)
    session(stubs, OUTSIDE, "hand-made", "idle", outside, "feat/outside")
    session_row(stubs, OUTSIDE, parent_session_id=MIDDLE)
    out = q("shepherd").out
    assert len(sends(stubs, OUTSIDE)) == 1, out


def test_a_session_spanning_two_repositories_is_told_about_each(shep, stubs, tmp_path):
    one = checkout(tmp_path / "one", "feat/both")
    two = checkout(tmp_path / "two", "feat/both")
    git("remote", "set-url", "origin", "https://github.com/Thurbeen/thurbox.git", cwd=two)
    trees = [{"branch": "feat/both", "repo_path": str(p), "worktree_path": str(p), "created_by_thurbox": True}
             for p in (one, two)]
    session(stubs, OUTSIDE, "hand-made", "idle", None)
    session_row(stubs, OUTSIDE, worktrees=trees)
    shep.pr(201, headRefName="feat/both", body="", statusCheckRollup=[FAILED])
    out = q("shepherd").out
    prs = store()["sessions"][OUTSIDE]["prs"]
    assert [p["repo"] for p in prs] == ["github.com/Thurbeen/fleet"], out
    expect(out, "pull/201")


def test_a_task_session_thurbox_no_longer_lists_leaves_the_store(shep, stubs, tmp_path):
    """The docstring's contract: thurbox listing every session but that one proves it gone."""
    held = checkout(tmp_path / "held", "main-ish")
    topic = ok(q("topic", "add", "adoption", "--title", "Adoption",
                 "--prompt", "shepherd outside sessions")).stdout.strip()
    ok(q("add", topic, "held", "--title", "A task's own worker", "--repo", str(held),
         "--branch", "feat/held", "--number", "01"))
    git("checkout", "-q", "-b", "feat/held", cwd=held)
    session(stubs, HELD, "queued worker", "working", held, "feat/held")
    ok(q("attach", f"{topic}/01-held", HELD))
    shep.pr(202, headRefName="feat/held", body="")
    q("shepherd")
    assert HELD in store()["sessions"]

    (stubs.root / "sessions" / f"{HELD}.json").unlink()
    out = q("shepherd").out
    assert HELD not in store()["sessions"], out


def test_a_git_failure_keeps_an_adopted_session_and_what_it_was_told(shep, stubs, tmp_path):
    """git that cannot be read is not git that said "another branch": the entry and its `sent` stay."""
    wt = checkout(tmp_path / "moved", "created-by-thurbox")
    git("checkout", "-q", "-b", "fix/its-own", cwd=wt)
    session(stubs, OUTSIDE, "moved on", "idle", wt, "created-by-thurbox")
    shep.pr(201, headRefName="fix/its-own", body="", statusCheckRollup=[FAILED])
    q("shepherd")
    assert len(sends(stubs, OUTSIDE)) == 1, shep.tbx_log()

    head = wt / ".git" / "HEAD"
    good = head.read_text(encoding="utf-8")
    head.write_text("garbage\n", encoding="utf-8")  # every git read in the worktree now fails
    out = q("shepherd").out
    entry = store()["sessions"][OUTSIDE]["prs"][0]
    assert entry["stale"] is True, out
    assert len(entry["sent"]) == 1, out

    head.write_text(good, encoding="utf-8")
    out = q("shepherd").out
    assert len(sends(stubs, OUTSIDE)) == 1, out + shep.tbx_log()


def test_a_session_git_says_moved_to_another_branch_is_dropped(shep, stubs, tmp_path):
    """A branch that WAS read and is not the PR's is definitive: ownership does not linger."""
    wt = checkout(tmp_path / "moved", "created-by-thurbox")
    git("checkout", "-q", "-b", "fix/its-own", cwd=wt)
    session(stubs, OUTSIDE, "moved on", "idle", wt, "created-by-thurbox")
    shep.pr(201, headRefName="fix/its-own", body="", statusCheckRollup=[FAILED])
    q("shepherd")
    git("checkout", "-q", "-b", "fix/next-thing", cwd=wt)
    out = q("shepherd").out
    assert OUTSIDE not in store()["sessions"], out
