"""A pull request nobody is told about is still recorded, and still reaches the conductor.

The case, from 2026-10-14: a hand-made design session under the lead spawned a
session that opened a pull request, which went CONFLICTING. Shepherd may not
adopt that session — it descends from a lead — and no task recorded the PR, so
the store dropped it, `fleet queue prs` said "nothing recorded yet", `plan`
named nothing and the conductor, asked, said no notice had reached it. Now the
store's `unwatched` list holds every open PR no session is told about, names
the local session whose branch it is, and `plan --json` hands the ones that
need a hand to the notifier. Shepherd still sends that session nothing.
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


@pytest.fixture
def incident(shep, stubs, tmp_path) -> None:
    """The lead, a design session under it, and its child's conflicting pull request."""
    session(stubs, LEAD, LEAD_NAME, None)
    session(stubs, DESIGN, "Design session keys", checkout(tmp_path / "design", "design/session-keys"),
            "design/session-keys", parent=LEAD)
    session(stubs, CHILD, "Add session keys", checkout(tmp_path / "child", "feat/session-keys"),
            "feat/session-keys", parent=DESIGN)
    shep.pr(415, headRefName="feat/session-keys", body="", mergeable="CONFLICTING")
    shep.pr(266, headRefName="fix/issue-258", body="")  # green, nobody's session


def test_a_conflicting_pr_under_the_lead_is_recorded_and_its_session_named(incident, stubs):
    out = q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME).out
    # Adoption is unchanged: the session descends from the lead and is sent nothing.
    assert stubs.calls("thurbox-cli", f"session send {CHILD}") == [], out
    doc = store()
    assert CHILD not in doc["sessions"]
    by_number = {e["number"]: e for e in doc["unwatched"]}
    child = by_number[415]
    assert child["condition"] == "conflicting"
    assert child["mergeable"] == "conflicting"
    assert child["session"] == CHILD
    assert child["session_name"] == "Add session keys"
    assert child["task"] is None
    expect(child["why"], "descends from a lead")
    # The green one nobody is on is listed too, with no session.
    assert by_number[266]["session"] == ""
    assert by_number[266]["condition"] != "policy", "nobody asked an unqueued PR for an attestation"


def test_fleet_queue_prs_shows_it_rather_than_nothing_recorded(incident):
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME)
    out = q("prs").out
    refute(out, "nothing recorded yet")
    expect(out, "nobody is told about", "#415", "conflicting", "on Add session keys's branch", "#266")


def test_plan_names_only_the_one_that_needs_a_hand(incident):
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME)
    plan = json.loads(q("plan", "--json").stdout)
    rows = plan["unwatched_prs"]
    assert [r["condition"] for r in rows] == ["conflicting"], rows
    assert rows[0]["session"] == CHILD
    assert rows[0]["key"].startswith("Thurbeen/fleet#415 conflicting @"), rows
    expect(q("plan").out, "unwatched: 1 pull request(s)", "pull/415", "on Add session keys")


def test_a_push_is_a_new_key(incident, shep):
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME)
    before = json.loads(q("plan", "--json").stdout)["unwatched_prs"][0]["key"]
    shep.update(415, headRefOid="e" * 40)
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME)
    after = json.loads(q("plan", "--json").stdout)["unwatched_prs"][0]["key"]
    assert before != after


def test_an_adopted_session_is_not_also_unwatched(shep, stubs, tmp_path):
    session(stubs, CHILD, "hand-made", checkout(tmp_path / "own", "feat/own"), "feat/own")
    shep.pr(201, headRefName="feat/own", body="", mergeable="CONFLICTING")
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME)
    doc = store()
    assert CHILD in doc["sessions"]
    assert doc["unwatched"] == []
    assert json.loads(q("plan", "--json").stdout)["unwatched_prs"] == []


def test_an_unreadable_forge_keeps_unwatched_entries_marked_stale(incident):
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME)
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME, SHEP_GH_DOWN="1")
    entries = {e["number"]: e for e in store()["unwatched"]}
    assert entries[415]["stale"] is True
    # A stale reading is not news to the conductor.
    assert json.loads(q("plan", "--json").stdout)["unwatched_prs"] == []


def test_fleet_status_points_at_the_prs_no_task_holds(incident):
    """`fleet status` matches PRs to tasks; it now says how many more there are, and where."""
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME)
    out = run_fleet("status").out
    expect(out, "2 open that no task holds", "1 needing a hand", "Thurbeen/fleet#415 conflicting",
           "uv run fleet queue prs")


def test_a_forks_pr_on_the_same_branch_name_is_not_the_sessions(incident, shep):
    """A stranger's fork can name its branch `feat/session-keys` too; that PR is not ours to pin on a session."""
    shep.update(415, isCrossRepository=True, headRepositoryOwner={"login": "a-stranger"})
    q("shepherd", FLEET_LEAD_SESSION=LEAD_NAME)
    entry = {e["number"]: e for e in store()["unwatched"]}[415]
    assert entry["session"] == ""
    assert entry["session_name"] == ""
    assert json.loads(q("plan", "--json").stdout)["unwatched_prs"] == []
