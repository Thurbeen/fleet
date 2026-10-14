"""Every session on this machine is told about its pull request, and every other open one is still recorded.

The case, from 2026-10-14: a hand-made design session under the lead spawned a
session that opened a pull request, which went CONFLICTING. Shepherd refused
to adopt that session because it descended from a lead, no task recorded the
PR, the store dropped it, and nobody was told. Now only ANOTHER fleet's lead or
conductor refuses its descendants, so that session is adopted and told like
any other; and the store's `unwatched` list holds every open PR no session
here is on, so `fleet queue prs` and `fleet status` show all of what is open.
"""

import json
import os
from pathlib import Path

import pytest
from kit_shepherd import Shep

from harness import expect, git, refute, run_fleet, write
from harness import run_queue as q

LEAD = "0c0c0c0c-0000-0000-0000-000000000003"
DESIGN = "0f0f0f0f-0000-0000-0000-000000000006"
CHILD = "0a0a0a0a-0000-0000-0000-000000000001"
LEAD_NAME = "Mission Control"


@pytest.fixture
def shep(stubs) -> Shep:
    return Shep(stubs)


def checkout(path: Path, branch: str) -> Path:
    path.mkdir(parents=True)
    git("init", "-q", "-b", "main", str(path))
    git("remote", "add", "origin", "https://github.com/Thurbeen/fleet.git", cwd=path)
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=path)
    git("checkout", "-q", "-b", branch, cwd=path)
    return path


def session(stubs, sid: str, name: str, worktree: Path | None, branch: str = "", parent: str = "") -> None:
    trees = [{"branch": branch, "repo_path": str(worktree), "worktree_path": str(worktree),
              "created_by_thurbox": True}] if worktree else []
    write(stubs.root / "sessions" / f"{sid}.json", json.dumps({
        "id": sid, "name": name, "state": "idle", "agent": "claude", "hook_reported": True,
        "backend_type": "local-tmux", "base_branch": "origin/main", "worktrees": trees,
        "parent_session_id": parent or None,
    }) + "\n")


def store() -> dict:
    return json.loads((Path(os.environ["FLEET_SHEPHERD_DIR"]) / "prs.json").read_text(encoding="utf-8"))


def sends(stubs, sid: str) -> list[str]:
    return stubs.calls("thurbox-cli", f"session send {sid}")


@pytest.fixture
def incident(shep, stubs, tmp_path) -> None:
    """The lead, a design session under it, and its child's conflicting pull request."""
    session(stubs, LEAD, LEAD_NAME, None)
    session(stubs, DESIGN, "Design session keys", checkout(tmp_path / "design", "design/session-keys"),
            "design/session-keys", parent=LEAD)
    session(stubs, CHILD, "Add session keys", checkout(tmp_path / "child", "feat/session-keys"),
            "feat/session-keys", parent=DESIGN)
    shep.pr(415, headRefName="feat/session-keys", body="", mergeable="CONFLICTING")
    shep.pr(266, headRefName="fix/issue-258", body="")  # green, and no session here is on it


def test_a_session_under_this_fleets_lead_is_told_about_its_conflict(incident, stubs):
    out = q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME).out
    told = sends(stubs, CHILD)
    assert len(told) == 1, out
    expect(told[0], "PR #415", "conflicting", "Read ")
    entry = store()["sessions"][CHILD]
    assert entry["adopted"] is True
    assert entry["prs"][0]["condition"] == "conflicting"
    assert entry["prs"][0]["action"] == "sent"
    # Once per condition per head, exactly as for any adopted session.
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME)
    assert len(sends(stubs, CHILD)) == 1
    # The lead itself is still never adopted or told.
    assert sends(stubs, LEAD) == []
    assert LEAD not in store()["sessions"]


def test_a_pr_no_session_here_is_on_is_recorded_as_unwatched(incident):
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME)
    unwatched = {e["number"]: e for e in store()["unwatched"]}
    assert set(unwatched) == {266}, "the adopted session's PR is filed under it, not unwatched"
    assert unwatched[266]["task"] is None
    assert unwatched[266]["condition"] != "policy", "nobody asked an unqueued PR for an attestation"
    expect(unwatched[266]["why"], "no session on this machine")


def test_fleet_queue_prs_shows_everything_open(incident):
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME)
    out = q("prs").out
    refute(out, "nothing recorded yet")
    expect(out, "Add session keys", "#415", "conflicting", "no session on this machine is on their branch", "#266")


def test_a_pass_that_found_nothing_says_when_rather_than_nothing_recorded(shep):
    q("shepherd")
    out = q("prs").out
    refute(out, "nothing recorded yet")
    expect(out, "as of", "0 watched session(s), 0 open pull request(s)")


def test_an_unreadable_forge_keeps_unwatched_entries_marked_stale(incident):
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME)
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME, SHEP_GH_DOWN="1")
    entries = {e["number"]: e for e in store()["unwatched"]}
    assert entries[266]["stale"] is True


def test_fleet_status_points_at_the_prs_no_task_holds(incident):
    """`fleet status` matches PRs to tasks; it now says how many more there are, and where."""
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME)
    out = run_fleet("status").out
    expect(out, "2 more that no task holds: 1 on sessions shepherd adopted, 1 that no session here is on",
           "uv run fleet queue prs")
