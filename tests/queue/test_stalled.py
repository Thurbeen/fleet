"""A worker at rest with nothing to show for it is `stalled`, and `plan` says so.

Five workers on one machine stopped without a result and nobody noticed for two
days: a worker that ended its turn early, asked a question nobody saw, or lost
its brief looks exactly like one still working from every view the lead reads.
The judgement is a conjunction, because each half alone is ordinary: the task
is dispatched, its session has been at rest longer than STALLED_IDLE_SECS, no
result.md was written, and its branch has no commit in that window.
"""

import json
import subprocess

from queuekit import S1, S2, result

from harness import expect, refute
from harness import run_queue as q

HOUR = 3600


def stalled(*args: str) -> list:
    return json.loads(q("plan", "--json", *args).stdout).get("stalled", [])


def test_a_worker_at_rest_with_no_result_is_stalled(attached, stubs):
    stubs.session_is(S1, "idle", age=2 * HOUR)
    stubs.session_is(S2, "working", age=2 * HOUR)
    rows = stalled()
    assert [r["task"] for r in rows] == [f"{attached}/01-drop-idle-default"]
    plan = q("plan").out
    expect(plan, "stalled: 1", "01-drop-idle-default")
    refute(plan.split("stalled:")[1], "02-document-the-states")


def test_a_short_rest_is_not_a_stall(attached, stubs):
    stubs.session_is(S1, "idle", age=60)
    stubs.session_is(S2, "done", age=60)
    assert stalled() == []
    refute(q("plan").out, "stalled:")


def test_a_worker_that_wrote_its_result_is_collects_and_not_stalled(attached, stubs, queue_dir):
    stubs.session_is(S1, "idle", age=2 * HOUR)
    stubs.session_is(S2, "working")
    result(queue_dir / attached / "01-drop-idle-default", "stuck", "Needs a credential.")
    assert stalled() == []


def test_a_fresh_commit_on_the_branch_is_not_a_stall(queue_dir, stubs, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "init")
    q("topic", "add", "busy", "--title", "Busy", "--prompt", "work")
    q("add", "busy", "work", "--title", "Work", "--repo", str(repo), "--branch", "fix/work", "--number", "01")
    git("branch", "fix/work")
    git("checkout", "-q", "fix/work")
    git("commit", "-q", "--allow-empty", "-m", "just now")
    q("attach", "busy/01-work", S1)
    stubs.session_is(S1, "idle", age=2 * HOUR)
    assert stalled() == []
