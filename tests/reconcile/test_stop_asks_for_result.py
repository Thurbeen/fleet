"""A worker that published and stopped without its result.md is asked for it, once.

The recurring failure: a worker opens its pull request, ends its turn, and
never writes the result.md its brief names, so its task sits `dispatched`
until a person notices. The Stop hook already runs `fleet reconcile nudge` in
that worker. When the session is a dispatched task's, the task has no
result.md and its branch has commits ahead of its base, the nudge answers
Claude Code's Stop hook with a block decision naming the path — and never when
the hook says it is already continuing from one, so it blocks once. Every
other stop is today's: the nudge, and nothing on stdout.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from harness import expect, run, run_fleet, write

SID = "worker-uuid"
REF = "alpha/01-first"
BRANCH = "feat/first"


def git(repo: Path, *argv: str) -> None:
    done = run(["git", "-C", str(repo), *argv])
    assert done.code == 0, done.out


def worker(tmp_path: Path, *, ahead: bool = True, session: str = SID) -> Path:
    """A repository with the task's branch, and a dispatched task bound to `session`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    git(repo, "branch", BRANCH)
    if ahead:
        git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), BRANCH)
        git(tmp_path / "wt", "commit", "-q", "--allow-empty", "-m", "the work")
    queue = Path(os.environ["FLEET_QUEUE_DIR"])
    write(queue / "alpha" / "topic.yaml", "title: Alpha\n")
    task = queue / "alpha" / "01-first"
    write(task / "task.yaml", json.dumps({
        "title": "First", "repo": str(repo), "branch": BRANCH, "base": "main",
        "state": "dispatched", "session": session, "touches": [],
    }) + "\n")
    return task


def stop(active: bool = False, session: str | None = SID):
    hook = {"hook_event_name": "Stop", "stop_hook_active": active, "session_id": "conversation"}
    return run_fleet("reconcile", "nudge", stdin=json.dumps(hook), THURBOX_SESSION=session)


def nudged(isolated_env: Path) -> bool:
    return (isolated_env / "reconcile" / "nudge").is_file()


def test_a_published_worker_without_its_result_is_asked_for_it(tmp_path, isolated_env):
    task = worker(tmp_path)
    out = stop()
    assert out.code == 0, out.out
    decision = json.loads(out.stdout)
    assert decision["decision"] == "block", out.stdout
    expect(decision["reason"], str(task / "result.md"), "result.md")
    assert nudged(isolated_env), "the nudge is still left"


def test_a_stop_continuing_from_a_block_is_allowed(tmp_path, isolated_env):
    worker(tmp_path)
    out = stop(active=True)
    assert out.code == 0, out.out
    assert out.stdout.strip() == "", "blocked twice: the agent can never stop"
    assert nudged(isolated_env)


def test_a_worker_that_wrote_its_result_stops(tmp_path, isolated_env):
    task = worker(tmp_path)
    write(task / "result.md", "status: done\n")
    out = stop()
    assert out.code == 0, out.out
    assert out.stdout.strip() == "", out.stdout
    assert nudged(isolated_env)


def test_a_session_that_is_no_tasks_is_todays_nudge(tmp_path, isolated_env):
    worker(tmp_path, session="somebody-else")
    out = stop()
    assert out.code == 0, out.out
    assert out.stdout.strip() == "", out.stdout
    expect(out.stderr, "nudged")
    assert nudged(isolated_env)


def test_a_branch_with_nothing_on_it_stops(tmp_path, isolated_env):
    worker(tmp_path, ahead=False)
    out = stop()
    assert out.code == 0, out.out
    assert out.stdout.strip() == "", out.stdout
    assert nudged(isolated_env)


def test_hook_input_that_is_not_json_never_fails_the_stop(tmp_path, isolated_env):
    worker(tmp_path)
    out = run_fleet("reconcile", "nudge", stdin="not json", THURBOX_SESSION=SID)
    assert out.code == 0, out.out
    assert out.stdout.strip() == "", out.stdout
    assert nudged(isolated_env)


@pytest.mark.parametrize("hook", [{}, {"hook_event_name": "PreToolUse"}])
def test_a_nudge_with_non_stop_json_never_blocks(tmp_path, isolated_env, hook):
    worker(tmp_path)
    out = run_fleet("reconcile", "nudge", stdin=json.dumps(hook), THURBOX_SESSION=SID)
    assert out.code == 0, out.out
    assert out.stdout.strip() == "", "only a Stop hook may ask the worker to continue"
    assert nudged(isolated_env)


@pytest.mark.parametrize("change", [{"host": "remote"}, {"state": "shipped"}, {"branch": "missing"}])
def test_a_task_outside_the_local_dispatched_branch_is_not_asked(tmp_path, isolated_env, change):
    task = worker(tmp_path)
    record = json.loads((task / "task.yaml").read_text(encoding="utf-8"))
    write(task / "task.yaml", json.dumps(record | change))
    out = stop()
    assert out.code == 0, out.out
    assert out.stdout.strip() == "", out.stdout
    assert nudged(isolated_env)


def test_the_remote_base_prevents_a_stale_local_base_counting_as_work(tmp_path, isolated_env):
    worker(tmp_path)
    git(tmp_path / "repo", "update-ref", "refs/remotes/origin/main", BRANCH)
    out = stop()
    assert out.code == 0, out.out
    assert out.stdout.strip() == "", "the remote base already contains every worker commit"
    assert nudged(isolated_env)


def test_work_in_an_additional_repository_also_asks_for_the_result(tmp_path, isolated_env):
    task = worker(tmp_path, ahead=False)
    second = tmp_path / "second"
    second.mkdir()
    git(second, "init", "-q", "-b", "develop")
    git(second, "commit", "-q", "--allow-empty", "-m", "base")
    git(second, "switch", "-q", "-c", BRANCH)
    git(second, "commit", "-q", "--allow-empty", "-m", "the work")
    record = json.loads((task / "task.yaml").read_text(encoding="utf-8"))
    write(task / "task.yaml", json.dumps(record | {"add_repos": [f"{second}@develop"]}))
    out = stop()
    assert out.code == 0, out.out
    decision = json.loads(out.stdout)
    assert decision["decision"] == "block", out.stdout
    expect(decision["reason"], str(task / "result.md"))
    assert nudged(isolated_env)


def test_a_bad_setting_still_exits_zero_and_still_asks(tmp_path, isolated_env):
    """A bad setting costs the nudge, never the hook or its one question (#249)."""
    task = worker(tmp_path)
    write(tmp_path / "conf" / "orchestration" / "reconcile.conf", "BOUND_SECS=soon\n")
    hook = {"hook_event_name": "Stop", "stop_hook_active": False, "session_id": "conversation"}
    out = run_fleet("reconcile", "nudge", stdin=json.dumps(hook), THURBOX_SESSION=SID,
                    FLEET_RECONCILE_CONF_ROOT=str(tmp_path / "conf"))
    assert out.code == 0, out.out
    expect(out.stderr, "could not nudge", "is not a whole number")
    expect(json.loads(out.stdout)["reason"], str(task / "result.md"))


# A worker WAITING ON PURPOSE says so with `fleet queue declare`, and its brief
# tells it to before its turn ends. Blocking that stop asked a question the
# worker had already answered, on every turn the wait lasted.

def declare(*argv: str):
    out = run_fleet("queue", "declare", REF, *argv)
    assert out.code == 0, out.out
    return out


@pytest.mark.parametrize("said", [["paused", "--until", "1h", "--why", "CI is running"],
                                  ["needs-input", "--why", "which base?"]])
def test_a_worker_that_declared_its_wait_stops(tmp_path, isolated_env, said):
    worker(tmp_path)
    declare(*said)
    out = stop()
    assert out.code == 0, out.out
    assert out.stdout.strip() == "", "a declared wait was blocked anyway"
    assert nudged(isolated_env)


def test_an_expired_pause_is_asked_again(tmp_path, isolated_env):
    task = worker(tmp_path)
    write(task / "progress.jsonl", json.dumps({
        "declare": {"state": "paused", "until": "2026-01-01T01:00:00+00:00", "why": "CI"},
        "observed": "2026-01-01T00:00:00+00:00",
    }) + "\n")
    out = stop()
    assert out.code == 0, out.out
    assert json.loads(out.stdout)["decision"] == "block", "a pause past its time still held"


# Asked ONCE per head: `stop_hook_active` only spares the stop that follows a
# block, so a worker resting across many turns was blocked on every one of them.

def test_the_same_head_is_asked_about_once(tmp_path, isolated_env):
    worker(tmp_path)
    assert json.loads(stop().stdout)["decision"] == "block"
    assert stop(active=True).stdout.strip() == ""
    again = stop()
    assert again.code == 0, again.out
    assert again.stdout.strip() == "", "the next turn's stop was blocked over the same head"


def test_a_new_commit_is_asked_about_again(tmp_path, isolated_env):
    task = worker(tmp_path)
    assert json.loads(stop().stdout)["decision"] == "block"
    git(tmp_path / "wt", "commit", "-q", "--allow-empty", "-m", "more work")
    out = stop()
    assert out.code == 0, out.out
    expect(json.loads(out.stdout)["reason"], str(task / "result.md"))
