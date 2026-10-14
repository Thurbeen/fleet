"""The operator's own command for a worktree `reap` lets go of.

`BUILD_OUTPUT` is everything reap can name for itself. What a worker raised
OUTSIDE its worktree and keyed by it — a container stack, a database, a daemon
— belongs to a tool fleet has never heard of and may not name, and nothing
took those down: on one machine eleven of them outlived the worktrees they
were keyed to, holding 26 GB that a disk which never shrinks kept for good.

So `WORKTREE_RELEASED_CMD` in the gitignored `orchestration/reconcile.conf` is
the seam. Fleet contributes what only it knows — which worktree, whose task,
and when it is nobody's any more — and runs what it is given, bounded, with
every refusal `free_build_output` makes and none of its own.

These drive `reap` and `collect`, never the function: a seam the queue would
call and nothing calls is the failure this file exists to catch.
"""

import json
import os
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
          f"WORKTREE_RELEASED_CMD={line}\n"
          + (f"WORKTREE_RELEASED_CMD_SECS={secs}\n" if secs else ""))


def ran(stubs) -> list[str]:
    return stubs.calls(HOOK)


def listed(tree) -> str:
    """A worktree as `git worktree list` spells it: forward slashes on Windows.

    What the command is HANDED is this platform's own spelling, so `str(tree)`
    is right for every hand-over assertion. `free_build_output` quotes git's
    answer as it came, and these two tests read that line.
    """
    return str(tree).replace(os.sep, "/")


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
    expect(out, "gone", "released", str(tree), "WORKTREE_RELEASED_CMD")
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


@pytest.fixture
def fixer_checkout(queue_dir, tmp_path):
    """The one worktree two release points both reach.

    `branch_checkout` cuts a fixer's checkout on the TASK'S OWN branch, so git
    lists it among `task_worktrees` too — `release_fixer_checkouts` takes it
    back, and the holders pass right after would hand over the same path.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo / "README.md", "hello\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    ok(q("topic", "add", "disk", "--title", "Let the worktree go", "--prompt", "take the stack down too"))
    ok(q("add", "disk", "build", "--title", "Build something big", "--repo", str(repo),
         "--branch", "fix/build", "--number", "01"))
    fixer = queue_dir / ".worktrees" / "disk__01-build"
    git(repo, "worktree", "add", "-q", "-b", "fix/build", str(fixer))
    return repo, fixer


@pytest.fixture
def fixer_on_the_task_branch(fixer_checkout):
    ok(q("attach", REF, SID))
    ok(q("abandon", REF, "--why", "superseded"))
    return fixer_checkout


def test_one_worktree_is_handed_over_once_however_many_releases_reach_it(
        fixer_on_the_task_branch, stubs, isolated_env):
    """A teardown is not promised to be idempotent, so it is asked for once.

    And the dry run promises only what it can know: whether this checkout is
    handed over depends on a `git worktree remove` it has not tried.
    """
    _repo, fixer = fixer_on_the_task_branch
    hook(isolated_env, stubs)
    dry = ok(q("reap", "--dry-run")).out
    expect(dry, f"would remove fixer checkout {fixer}, then hand it over")
    refute(dry, "would run")
    out = ok(q("reap")).out
    expect(out, "removed    fixer checkout", "released", str(fixer))
    assert ran(stubs) == [f"{HOOK} {fixer} {REF}"], ran(stubs)


def test_a_checkout_git_refuses_to_remove_is_handed_to_nobody(
        fixer_on_the_task_branch, stubs, isolated_env):
    """git keeps it because somebody's unfinished work is in it — which is the answer.

    No other release point may hand it over, and the dry run may not promise
    a command either: whatever is running in there is still theirs.
    """
    _repo, fixer = fixer_on_the_task_branch
    write(fixer / "notes.txt", "work in progress\n")
    hook(isolated_env, stubs)
    refute(ok(q("reap", "--dry-run")).out, "would run")
    out = ok(q("reap")).out
    expect(out, "kept       fixer checkout", str(fixer))
    refute(out, "WORKTREE_RELEASED_CMD", "would run")
    assert ran(stubs) == [], ran(stubs)


def test_the_vanished_sweep_hands_over_no_checkout_nothing_has_tried_to_release(
        fixer_checkout, stubs, isolated_env, queue_dir):
    """The sweep runs BEFORE the removal, and git refuses this removal.

    The order is what made this reachable: a worker that vanished is exactly
    when a fixer is spawned, so its checkout is on disk, on the task's own
    branch, with the fixer's unfinished work in it — and the sweep retires the
    task one release point ahead of the only thing that may hand that checkout
    over.
    """
    _repo, fixer = fixer_checkout
    write(fixer / "notes.txt", "the fixer is mid-change\n")
    hook(isolated_env, stubs)
    ok(q("attach", REF, SID))
    ok(q("collect"))
    path = queue_dir / "disk" / "01-build" / "task.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["vanished"]["since"] = "2020-01-01T00:00:00+00:00"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    out = ok(q("collect")).out
    expect(out, "session gone", "kept       fixer checkout")
    refute(out, "released")
    assert ran(stubs) == [], ran(stubs)


def test_a_worktree_whose_occupant_this_pass_deletes_is_still_handed_over(
        queue_dir, tmp_path, stubs, isolated_env):
    """A refusal is not an answer: the occupant can be a session reap is about to delete.

    Two tasks on one branch share one worktree. The first task's release reads
    the second task's live session sitting in it and keeps it — and then this
    same pass deletes that session with `--force`, which takes the worktree
    with it. The stack keyed to that directory is what this whole seam exists
    to take down, so the hand-over must still happen.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo / "README.md", "hello\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    ok(q("topic", "add", "disk", "--title", "Let the worktree go", "--prompt", "take the stack down too"))
    ok(q("add", "disk", "first", "--title", "The one that is gone", "--repo", str(repo),
         "--branch", "fix/build", "--number", "01"))
    ok(q("add", "disk", "second", "--title", "The one still in it", "--repo", str(repo),
         "--branch", "fix/build", "--number", "02"))
    tree = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "fix/build", str(tree))

    other = "88888888-8888-8888-8888-888888888888"
    write(stubs.root / "sessions" / f"{other}.json", json.dumps({
        "id": other, "name": "still in it", "state": "idle", "agent": "claude",
        "hook_reported": True, "hook_state": "idle", "hook_state_age_secs": 5,
        "cwd": str(tree), "backend_type": "local:tmux",
        "worktrees": [{"worktree_path": str(tree), "created_by_thurbox": True}],
    }))
    ok(q("attach", "disk/01-first", SID))
    ok(q("abandon", "disk/01-first", "--why", "superseded"))
    ok(q("attach", "disk/02-second", other))
    ok(q("abandon", "disk/02-second", "--why", "superseded", "--force"))

    hook(isolated_env, stubs)
    out = ok(q("reap")).out
    expect(out, "kept       WORKTREE_RELEASED_CMD", other, "reaped", "released", str(tree))
    assert ran(stubs) == [f"{HOOK} {tree} disk/02-second"], ran(stubs)


def test_a_worktree_git_lists_without_a_directory_frees_nothing_and_is_still_handed_over(
        gone, stubs, isolated_env):
    """The two questions are not one, which is why the gate is not the only check.

    A directory somebody removed behind git's back holds no build output to
    free, and `free_build_output` says so from its own strict placing of the
    path — while the command still has every reason to run, since what the
    worktree raised outside itself is exactly what outlives the directory.
    """
    _repo, tree = gone
    hook(isolated_env, stubs)
    for path in sorted(tree.rglob("*"), reverse=True):
        path.unlink() if path.is_file() or path.is_symlink() else path.rmdir()
    tree.rmdir()
    out = ok(q("reap")).out
    expect(out, f"kept       build output in {listed(tree)}", "released", str(tree))
    assert ran(stubs) == [f"{HOOK} {tree} {REF}"], ran(stubs)


def test_a_task_spanning_repositories_hands_over_each_worktree(worktree, stubs, isolated_env, tmp_path):
    """One artifact per repository, and one worktree per repository to let go of."""
    repo, tree = worktree
    second = tmp_path / "other"
    second.mkdir()
    git(second, "init", "-q", "-b", "main")
    write(second / "README.md", "hello\n")
    git(second, "add", "-A")
    git(second, "commit", "-q", "-m", "init")
    ok(q("add", "disk", "spanning", "--title", "Both of them", "--repo", str(repo),
         "--add-repo", str(second), "--branch", "fix/span", "--number", "02"))
    here = tmp_path / "wt-here"
    there = tmp_path / "wt-there"
    git(repo, "worktree", "add", "-q", "-b", "fix/span", str(here))
    git(second, "worktree", "add", "-q", "-b", "fix/span", str(there))
    ok(q("attach", "disk/02-spanning", SID))
    ok(q("abandon", "disk/02-spanning", "--why", "superseded"))
    hook(isolated_env, stubs)
    out = ok(q("reap")).out
    expect(out, "released", str(here), str(there))
    assert ran(stubs) == [f"{HOOK} {here} disk/02-spanning",
                          f"{HOOK} {there} disk/02-spanning"], ran(stubs)


def test_without_the_setting_nothing_is_run(gone, stubs, queue_dir):
    _repo, _tree = gone
    stubs.tool(HOOK, TOOK_IT_DOWN)
    out = ok(q("reap")).out
    expect(out, "gone")
    refute(out, "WORKTREE_RELEASED_CMD", "would run")
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
    expect(out, "gone", "kept       WORKTREE_RELEASED_CMD", "by-hand")
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
    """The same refusal the build output gets, from the same unreadable answer.

    With a lead and a conductor named, as every installed fleet has them: their
    ids are read off the pass's one list too, or a second list taken for them
    spends the only answer and the drop never happens. Named here rather than
    left to the checkout's rendered manifest, which only an operator's has.
    """
    _repo, tree = gone
    hook(isolated_env, stubs)
    stubs.tool("thurbox-cli", STOPS_ANSWERING)
    out = ok(q("reap", FLEET_LEAD_SESSION="Gate Control", FLEET_CONDUCTOR_SESSION="Gate Conductor")).out
    expect(out, "gone", f"kept       build output in {listed(tree)}",
           f"kept       WORKTREE_RELEASED_CMD for {tree}")
    assert ran(stubs) == []


def test_a_failing_command_is_reported_and_the_task_still_lands(gone, stubs, isolated_env, queue_dir):
    _repo, tree = gone
    hook(isolated_env, stubs, "print('cannot reach the daemon')\nraise SystemExit(3)\n")
    out = ok(q("reap")).out
    expect(out, "FAILED", "WORKTREE_RELEASED_CMD exit 3", "cannot reach the daemon")
    assert ran(stubs) == [f"{HOOK} {tree} {REF}"], ran(stubs)
    doc = state(queue_dir)
    assert doc["state"] == "abandoned"
    assert doc["reaped"]["how"] == "already gone"


def test_a_line_that_is_not_a_command_is_named_and_nothing_runs(gone, stubs, isolated_env, queue_dir):
    _repo, _tree = gone
    hook(isolated_env, stubs, line=f"{HOOK} 'unclosed")
    out = ok(q("reap")).out
    expect(out, "NOT RUN", "WORKTREE_RELEASED_CMD", "unclosed")
    refute(out, "Traceback")
    assert ran(stubs) == []
    assert state(queue_dir)["state"] == "abandoned"


# `inf`, `nan` and `1e400` are why this is read as a whole number and not as a
# float: each one is positive (or compares as neither), so a `> 0` test waves
# it through, and the wait then goes to the OS, which raises something no
# `run_bounded` caller catches — out of the command, out of the reap, out of
# the `collect` the reconciler runs every two minutes. `3000000` is the same
# end reached by a value a human could type.
@pytest.mark.parametrize(
    "secs", ["0", "never", "-5", "0.5", "inf", "nan", "1e400", "3601", "3000000"])
def test_a_bound_that_is_not_a_whole_number_of_seconds_runs_nothing(gone, stubs, isolated_env, secs):
    """A bound fleet cannot use is not permission to run unbounded."""
    hook(isolated_env, stubs, secs=secs)
    out = ok(q("reap")).out
    expect(out, "NOT RUN", f"WORKTREE_RELEASED_CMD_SECS={secs}")
    refute(out, "Traceback")
    assert ran(stubs) == []


def test_the_largest_bound_fleet_accepts_runs(gone, stubs, isolated_env):
    """The ceiling itself is usable; one second past it is the refusal above."""
    _repo, tree = gone
    hook(isolated_env, stubs, secs="3600")
    expect(ok(q("reap")).out, "released", str(tree))
    assert ran(stubs) == [f"{HOOK} {tree} {REF}"], ran(stubs)


def test_the_operators_own_arguments_come_first_and_no_shell_reads_the_line(gone, stubs, isolated_env):
    """The line is split as a command line and run directly: two appends, no shell."""
    _repo, tree = gone
    hook(isolated_env, stubs, line=f"{HOOK} --quiet 'two words' | grep nothing")
    ok(q("reap"))
    assert ran(stubs) == [f"{HOOK} --quiet two words | grep nothing {tree} {REF}"], ran(stubs)


# A teardown that hangs holds a child of its own — a process it started, which
# on POSIX also keeps the inherited pipe open, the shape that left `watch` and
# `shepherd` waiting for ever on output that never ended. The child must go
# with it: `fleet_platform.run_bounded` ends the whole tree, and
# `tests/test_platform.py` is where that mechanism itself is proved per OS.
# The command sleeps far past its own bound so the timeout is the command's
# and not the pipe's, which is the only shape both OSes agree on — Windows
# hands no inherited handle to a grandchild.
HANGS_WITH_A_CHILD = """
import subprocess
import sys
import time
subprocess.Popen([sys.executable, "-c",
                  "import pathlib, time; time.sleep(3); pathlib.Path(%r).write_text('late', encoding='utf-8')"])
time.sleep(60)
"""


def test_a_command_that_hangs_is_bounded_and_the_task_still_lands(gone, stubs, isolated_env, tmp_path, queue_dir):
    _repo, tree = gone
    marker = tmp_path / "child-survived"
    hook(isolated_env, stubs, HANGS_WITH_A_CHILD % str(marker), secs="1")
    started = time.monotonic()
    out = ok(q("reap")).out
    took = time.monotonic() - started
    expect(out, "FAILED", "WORKTREE_RELEASED_CMD killed after 1s", str(tree))
    assert took < 40, f"the reap waited {took:.1f}s on a command bounded at 1s"
    doc = state(queue_dir)
    assert doc["state"] == "abandoned"
    assert doc["reaped"]["how"] == "already gone"
    # Killed with what it started, and not merely ahead of its own exit.
    time.sleep(4)
    assert not marker.exists(), "a child outlived the command's bound"


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
    refute(out, "kept       WORKTREE_RELEASED_CMD")
    assert ran(stubs) == []
    assert not stubs.calls("thurbox-cli", "session delete")


def test_a_remote_task_hands_over_nothing(worktree, stubs, isolated_env, queue_dir):
    """Its worktree is on another machine, and the operator's command is on this one."""
    hook(isolated_env, stubs)
    path = queue_dir / "disk" / "01-build" / "task.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["host"] = "devbox"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    ok(q("attach", REF, SID))
    ok(q("abandon", REF, "--why", "superseded", "--force"))
    expect(ok(q("reap")).out, "gone")
    assert ran(stubs) == []
