"""The operator's own command for a worktree `reap` lets go of.

`BUILD_OUTPUT` is everything reap can name for itself. What a worker raised
OUTSIDE its worktree and keyed by it — a container stack, a database, a daemon
— belongs to a tool fleet has never heard of and may not name, and nothing
took those down: on one machine eleven of them outlived the worktrees they
were keyed to, holding 26 GB that a disk which never shrinks kept for good.

So `ON_WORKTREE_RELEASED` in the gitignored `orchestration/reconcile.conf` is
the seam. Fleet contributes what only it knows — which worktree, whose task,
and when it is nobody's any more — and runs what it is given, bounded, with
every refusal `free_build_output` makes and none of its own.

These drive `reap` and `collect`, never the function: a seam the queue would
call and nothing calls is the failure this file exists to catch.
"""

import json
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from queuekit import ok

from harness import expect, refute, write
from harness import run_queue as q

SID = "99999999-9999-9999-9999-999999999999"
REF = "disk/01-build"
HOOK = "worktree-gone"

# What the operator's command does when it is run. The stub logs its own argv
# before running this, so `ran` below is exactly what fleet asked for.
TOOK_IT_DOWN = "print('took the stack down')\n"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


def hook(isolated_env, stubs, script: str = TOOK_IT_DOWN, line: str = HOOK, secs: str = "") -> None:
    """Configure the operator's command, and say what it does when run."""
    stubs.tool(HOOK, script)
    write(isolated_env / "settings" / "orchestration" / "reconcile.conf",
          f"ON_WORKTREE_RELEASED={line}\n"
          + (f"ON_WORKTREE_RELEASED_SECS={secs}\n" if secs else ""))


def ran(stubs) -> list[str]:
    return stubs.calls(HOOK)


def state(queue_dir) -> dict:
    return yaml.safe_load((queue_dir / "disk" / "01-build" / "task.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def worktree(queue_dir, tmp_path):
    """A task whose branch is checked out in a worktree on this machine."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo / "README.md", "hello\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")

    ok(q("topic", "add", "disk", "--title", "Let the worktree go", "--prompt", "take the stack down too"))
    ok(q("add", "disk", "build", "--title", "Build something big", "--repo", str(repo),
         "--branch", "fix/build", "--number", "01"))
    tree = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "fix/build", str(tree))
    (tree / "src").mkdir()
    return repo, tree


@pytest.fixture
def gone(worktree):
    """The worktree of a session thurbox no longer has: the directory is still there."""
    ok(q("attach", REF, SID))
    ok(q("abandon", REF, "--why", "superseded"))
    return worktree


@pytest.fixture
def live(worktree, stubs):
    """A worker at rest, owning its worktree, on a task reap will delete it for."""
    _repo, tree = worktree
    write(stubs.root / "sessions" / f"{SID}.json", json.dumps({
        "id": SID, "name": "worker", "state": "idle", "agent": "claude",
        "hook_reported": True, "hook_state": "idle", "hook_state_age_secs": 5,
        "cwd": str(tree), "backend_type": "local:tmux",
        "worktrees": [{"worktree_path": str(tree), "created_by_thurbox": True}],
    }))
    ok(q("attach", REF, SID))
    ok(q("abandon", REF, "--why", "superseded", "--force"))
    return worktree


def test_a_dropped_session_hands_its_worktree_over_with_the_path_and_the_ref(gone, stubs, isolated_env):
    _repo, tree = gone
    hook(isolated_env, stubs)
    out = ok(q("reap")).out
    expect(out, "gone", "released", str(tree), "ON_WORKTREE_RELEASED")
    assert ran(stubs) == [f"{HOOK} {tree} {REF}"], ran(stubs)


def test_a_deleted_session_hands_over_the_worktree_it_took_with_it(live, stubs, isolated_env, queue_dir):
    """`session delete --force` removes the worktree, so the path is read first.

    The command still has something to do about it: a stack keyed by that
    directory's name outlives the directory.
    """
    _repo, tree = live
    hook(isolated_env, stubs)
    out = ok(q("reap")).out
    expect(out, "reaped", SID, "released", str(tree))
    assert ran(stubs) == [f"{HOOK} {tree} {REF}"], ran(stubs)
    # The deletion is still what it was, and the task still moved.
    expect("\n".join(stubs.calls("thurbox-cli", "session delete")), SID, "--force")
    assert state(queue_dir)["reaped"]["how"] == "deleted"


def test_a_retiring_vanished_session_hands_its_worktree_over(worktree, stubs, isolated_env, queue_dir):
    """The other end reap releases a worktree at: `collect`'s vanished sweep."""
    _repo, tree = worktree
    hook(isolated_env, stubs)
    ok(q("attach", REF, SID))
    ok(q("collect"))
    assert ran(stubs) == [], "one look is not an end, and nothing is released on it"

    path = queue_dir / "disk" / "01-build" / "task.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["vanished"]["since"] = "2020-01-01T00:00:00+00:00"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    expect(ok(q("collect")).out, "session gone", "released", str(tree))
    assert ran(stubs) == [f"{HOOK} {tree} {REF}"], ran(stubs)


def test_a_fixers_checkout_is_handed_over_when_it_is_taken_back(gone, stubs, isolated_env, queue_dir):
    """A fixer's checkout is a worktree an agent worked in too."""
    repo, _tree = gone
    fixer = queue_dir / ".worktrees" / "disk__01-build"
    git(repo, "worktree", "add", "-q", "-b", "fix/fixer", str(fixer))
    hook(isolated_env, stubs)
    out = ok(q("reap")).out
    expect(out, "removed    fixer checkout", "released", str(fixer))
    assert f"{HOOK} {fixer} {REF}" in ran(stubs), ran(stubs)


def test_without_the_setting_nothing_is_run(gone, stubs, queue_dir):
    _repo, _tree = gone
    stubs.tool(HOOK, TOOK_IT_DOWN)
    out = ok(q("reap")).out
    expect(out, "gone")
    refute(out, "ON_WORKTREE_RELEASED", "would run")
    assert ran(stubs) == []
    assert state(queue_dir)["reaped"]["how"] == "already gone"


def test_a_worktree_another_session_sits_in_is_not_handed_over(gone, stubs, isolated_env):
    """Whoever is carrying the work on may be running exactly what it would take down."""
    _repo, tree = gone
    hook(isolated_env, stubs)
    write(stubs.root / "sessions" / "by-hand.json", json.dumps({
        "id": "by-hand", "name": "carrying on", "state": "working",
        "cwd": str(tree / "src"), "backend_type": "local:tmux", "worktrees": [],
    }))
    out = ok(q("reap")).out
    expect(out, "gone", "kept       ON_WORKTREE_RELEASED", "by-hand")
    assert ran(stubs) == []


# A session list that answers once and then stops answering: the drop itself
# reads the first one, and the occupancy question comes after it. Nothing else
# can put the two apart, and they must not disagree.
STOPS_ANSWERING = '''
import json
import sys
from fleet_stubs import root, read
where = root()
args = sys.argv[1:]
if args[:2] == ["session", "list"]:
    asked = read(where / "listed")
    with open(where / "listed", "a", encoding="utf-8") as fh:
        fh.write("x")
    if not asked:
        print(json.dumps([json.loads(read(p)) for p in (where / "sessions").glob("*.json")]))
    else:
        print("null")
elif args[:2] == ["session", "get"]:
    print(read(where / "sessions" / (args[2] + ".json")))
'''


def test_occupancy_that_cannot_be_judged_is_not_a_release(gone, stubs, isolated_env):
    """The same refusal the build output gets, from the same unreadable answer."""
    _repo, tree = gone
    hook(isolated_env, stubs)
    stubs.tool("thurbox-cli", STOPS_ANSWERING)
    out = ok(q("reap")).out
    expect(out, "gone", f"kept       build output in {tree}",
           f"kept       ON_WORKTREE_RELEASED for {tree}")
    assert ran(stubs) == []


def test_a_failing_command_is_reported_and_the_task_still_lands(gone, stubs, isolated_env, queue_dir):
    _repo, tree = gone
    hook(isolated_env, stubs, "print('cannot reach the daemon')\nraise SystemExit(3)\n")
    out = ok(q("reap")).out
    expect(out, "FAILED", "ON_WORKTREE_RELEASED exit 3", "cannot reach the daemon")
    assert ran(stubs) == [f"{HOOK} {tree} {REF}"], ran(stubs)
    doc = state(queue_dir)
    assert doc["state"] == "abandoned" and doc["reaped"]["how"] == "already gone"


def test_a_line_that_is_not_a_command_is_named_and_nothing_runs(gone, stubs, isolated_env, queue_dir):
    _repo, _tree = gone
    hook(isolated_env, stubs, line=f"{HOOK} 'unclosed")
    out = ok(q("reap")).out
    expect(out, "NOT RUN", "ON_WORKTREE_RELEASED", "unclosed")
    refute(out, "Traceback")
    assert ran(stubs) == []
    assert state(queue_dir)["state"] == "abandoned"


@pytest.mark.parametrize("secs", ["0", "never"])
def test_a_bound_that_is_not_a_number_of_seconds_runs_nothing(gone, stubs, isolated_env, secs):
    """A bound fleet cannot read is not permission to run unbounded."""
    hook(isolated_env, stubs, secs=secs)
    out = ok(q("reap")).out
    expect(out, "NOT RUN", f"ON_WORKTREE_RELEASED_SECS={secs}")
    assert ran(stubs) == []


# A grandchild holding the inherited pipe is what makes the bound load-bearing:
# the child exits at once and the pipe stays open, which is the shape that left
# `watch` and `shepherd` waiting for ever on output that never ended.
# `fleet_platform.run_bounded` ends the whole tree, and `tests/test_platform.py`
# is where that mechanism itself is proved.
HOLDS_THE_PIPE = """
import subprocess
import sys
subprocess.Popen([sys.executable, "-c",
                  "import pathlib, time; time.sleep(2); pathlib.Path(%r).write_text('late', encoding='utf-8')"])
"""


def test_a_command_that_hangs_is_bounded_and_the_task_still_lands(gone, stubs, isolated_env, tmp_path, queue_dir):
    _repo, tree = gone
    marker = tmp_path / "grandchild-survived"
    hook(isolated_env, stubs, HOLDS_THE_PIPE % str(marker), secs="1")
    started = time.monotonic()
    out = ok(q("reap")).out
    took = time.monotonic() - started
    expect(out, "FAILED", "ON_WORKTREE_RELEASED killed after 1s", str(tree))
    assert took < 30, f"the reap waited {took:.1f}s on a command bounded at 1s"
    doc = state(queue_dir)
    assert doc["state"] == "abandoned" and doc["reaped"]["how"] == "already gone"
    # Killed with what it started, and not merely ahead of its own exit.
    time.sleep(3)
    assert not marker.exists(), "a grandchild outlived the command's bound"


def test_dry_run_prints_the_command_and_runs_nothing(gone, stubs, isolated_env, queue_dir):
    _repo, tree = gone
    hook(isolated_env, stubs)
    out = ok(q("reap", "--dry-run")).out
    expect(out, "would run", HOOK, str(tree), REF)
    assert ran(stubs) == []
    assert "reaped" not in state(queue_dir)


def test_dry_run_prints_it_for_a_session_it_would_delete_too(live, stubs, isolated_env):
    """And the session sitting in its own worktree is not its own occupant."""
    _repo, tree = live
    hook(isolated_env, stubs)
    out = ok(q("reap", "--dry-run")).out
    expect(out, "would reap", SID, "would run", str(tree), REF)
    refute(out, "kept       ON_WORKTREE_RELEASED")
    assert ran(stubs) == []
    assert not stubs.calls("thurbox-cli", "session delete")
