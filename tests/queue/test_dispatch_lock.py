"""Many actors may dispatch, and one task is still spawned once (#227).

`dispatch` read `queued`, spent seconds in `session create`, then saved
`dispatched`, so two dispatchers could both pass the check; the second was
stopped only by `--on-existing fail` refusing the duplicate name. A per-task
`dispatch.lock`, held from the readiness re-check to that save, makes the
second write nothing. A dispatcher that dies inside that window leaves a
session no record names, and the next `dispatch` adopts it rather than failing
on its name. And a bare `dispatch` can be scoped to one origin or one topic, so
an origin sends its own work and nobody else's.
"""

from __future__ import annotations

import json
import subprocess
import time

import pytest
import yaml
from kit_dispatch import DELEGATE, next_session
from queuekit import fill_brief, ok

from harness import PYTHON, REPO, expect, queue_module, refute, write
from harness import run_queue as q

SID = "d15a0000-0000-0000-0000-000000000001"
OTHER = "d15a0000-0000-0000-0000-000000000002"
TOPIC = "many-dispatchers"

# `session create` announces itself, then holds until the test releases it —
# the seconds a real spawn takes, made long enough to dispatch into.
HELD_SPAWN = DELEGATE + '''
import time
if ARGS[:2] == ["session", "create"]:
    (ROOT / "spawn-started").write_text("1", encoding="utf-8")
    deadline = time.monotonic() + 60
    while not (ROOT / "spawn-release").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
delegate()
'''


def add(slug: str, number: str, *extra: str, topic: str = TOPIC) -> str:
    q("topic", "add", topic, "--title", "Many dispatchers", "--prompt", "one spawn per task")
    ok(q("add", topic, slug, "--title", f"Task {slug}", "--repo", "/tmp/repo-a",
         "--branch", f"fix/{slug}", "--number", number, "--publish", "none", *extra))
    return f"{topic}/{number}-{slug}"


def briefed(queue_dir, ref: str) -> str:
    fill_brief(queue_dir / ref / "BRIEF.md")
    return ref


def record(queue_dir, ref: str) -> dict:
    return yaml.safe_load((queue_dir / ref / "task.yaml").read_text(encoding="utf-8"))


def creates(stubs) -> list[str]:
    return stubs.calls("thurbox-cli", "session create")


def sends(stubs) -> list[str]:
    return stubs.calls("thurbox-cli", "session send")


def snapshot(task_dir) -> dict:
    return {p.name: p.read_bytes() for p in task_dir.iterdir() if p.name != "dispatch.lock"}


def worker_name(title: str) -> str:
    return queue_module("print(q.session_name(sys.argv[1], q.worker_glyph()))", title).strip()


def left_behind(stubs, sid: str, title: str, branch: str, state: str,
                repos: tuple = ("/tmp/repo-a",)) -> None:
    """A session a dispatch spawned under this task's name, on its branch, and never recorded."""
    write(stubs.root / "sessions" / f"{sid}.json", json.dumps({
        "id": sid, "name": worker_name(title), "state": state, "agent": "claude",
        "hook_reported": True, "hook_state": state, "state_source": "hook",
        "backend_type": "local-tmux",
        "worktrees": [{"branch": branch, "repo_path": repo,
                       "worktree_path": f"/tmp/wt/{sid}-{n}", "created_by_thurbox": True}
                      for n, repo in enumerate(repos)],
    }) + "\n")


def test_two_dispatches_of_one_task_spawn_one_session_and_the_loser_writes_nothing(stubs, queue_dir):
    ref = briefed(queue_dir, add("raced", "01"))
    next_session(stubs, SID)
    stubs.session_is(SID, "idle")
    stubs.tool("thurbox-cli", HELD_SPAWN)

    first = subprocess.Popen(
        [*PYTHON, str(REPO / "scripts" / "lib" / "queue.py"), "dispatch"],
        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
    )
    try:
        deadline = time.monotonic() + 30
        while not (stubs.root / "spawn-started").exists():
            assert first.poll() is None and time.monotonic() < deadline, "the first dispatch never spawned"
            time.sleep(0.05)
        before = snapshot(queue_dir / ref)

        second = ok(q("dispatch"))
        expect(second.out, f"{ref}: being dispatched by another process")
        assert snapshot(queue_dir / ref) == before, "the dispatcher that lost wrote to the task"
        assert record(queue_dir, ref)["state"] == "queued"
    finally:
        write(stubs.root / "spawn-release", "1")
        out, _ = first.communicate(timeout=60)
    assert first.returncode == 0, out

    assert len(creates(stubs)) == 1, creates(stubs)
    doc = record(queue_dir, ref)
    assert (doc["state"], doc["session"], doc["prompted"]) == ("dispatched", SID, True)
    # The lock is released with its holder, so a later dispatch finds nothing ready.
    expect(ok(q("dispatch")).out, "nothing ready")


def test_a_held_task_does_not_stop_the_rest_of_the_wave(stubs, queue_dir):
    held = briefed(queue_dir, add("held", "01"))
    free = briefed(queue_dir, add("free", "02"))
    next_session(stubs, SID)
    stubs.session_is(SID, "idle")
    holder = (
        "import sys, time\nsys.path.insert(0, 'scripts/lib')\nimport fleet_platform\n"
        "with fleet_platform.exclusive_lock(sys.argv[1]):\n"
        "    print('held', flush=True)\n"
        "    sys.stdin.readline()\n"
    )
    lock = subprocess.Popen(
        [*PYTHON, "-c", holder, str(queue_dir / held / "dispatch.lock")],
        cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE, encoding="utf-8",
    )
    try:
        assert lock.stdout.readline().strip() == "held"
        out = ok(q("dispatch")).out
    finally:
        lock.communicate("\n", timeout=30)
    expect(out, f"{held}: being dispatched by another process", f"{free}  -> {SID}")
    assert record(queue_dir, held)["state"] == "queued"
    assert record(queue_dir, free)["state"] == "dispatched"
    assert len(creates(stubs)) == 1


@pytest.mark.parametrize(("state", "prompted"), [
    ("working", True), ("blocked", True), ("done", True), ("idle", False),
])
def test_a_session_a_crashed_dispatch_left_is_adopted_not_twinned(stubs, queue_dir, state, prompted):
    ref = briefed(queue_dir, add("crashed", "01"))
    left_behind(stubs, OTHER, "Task crashed", "fix/crashed", state)

    out = ok(q("dispatch")).out
    expect(out, f"{ref}: adopted {OTHER}")
    assert creates(stubs) == [], "a session already spawned for the task was spawned again"
    doc = record(queue_dir, ref)
    assert (doc["state"], doc["session"], doc["prompted"]) == ("dispatched", OTHER, True)
    briefs = [c for c in sends(stubs) if "BRIEF.md" in c]
    if prompted:
        assert briefs == [], "a session already at work was briefed a second time"
    else:
        # Idle was adopted unprompted, so the same dispatch's handoff sent it once.
        assert len(briefs) == 1 and OTHER in briefs[0], briefs


def test_a_session_of_the_same_name_on_another_branch_is_not_adopted(stubs, queue_dir):
    ref = briefed(queue_dir, add("namesake", "01"))
    left_behind(stubs, OTHER, "Task namesake", "fix/somebody-else", "working")
    next_session(stubs, SID)
    stubs.session_is(SID, "idle")

    out = ok(q("dispatch")).out
    refute(out, "adopted")
    assert len(creates(stubs)) == 1
    assert record(queue_dir, ref)["session"] == SID


def test_a_session_of_the_same_name_and_branch_in_another_repository_is_not_adopted(stubs, queue_dir):
    ref = briefed(queue_dir, add("elsewhere", "01"))
    left_behind(stubs, OTHER, "Task elsewhere", "fix/elsewhere", "working",
                repos=("/tmp/completely-different-repo",))
    next_session(stubs, SID)
    stubs.session_is(SID, "idle")

    out = ok(q("dispatch")).out
    refute(out, "adopted")
    assert len(creates(stubs)) == 1
    doc = record(queue_dir, ref)
    assert (doc["session"], doc["prompted"]) == (SID, True)


def test_a_spanning_task_adopts_only_a_session_in_every_repository_it_spans(stubs, queue_dir):
    ref = briefed(queue_dir, add("spanning", "01", "--add-repo", "/tmp/repo-b"))
    left_behind(stubs, OTHER, "Task spanning", "fix/spanning", "working")
    next_session(stubs, SID)
    stubs.session_is(SID, "idle")
    refute(ok(q("dispatch")).out, "adopted")
    assert record(queue_dir, ref)["session"] == SID

    ref = briefed(queue_dir, add("spanned", "02", "--add-repo", "/tmp/repo-b"))
    left_behind(stubs, OTHER, "Task spanned", "fix/spanned", "working",
                repos=("/tmp/repo-a", "/tmp/repo-b"))
    expect(ok(q("dispatch")).out, f"{ref}: adopted {OTHER}")
    assert len(creates(stubs)) == 1


def test_origin_and_topic_scope_a_bare_dispatch(stubs, queue_dir):
    mine = briefed(queue_dir, add("mine", "01", "--origin", "slack:C1/1"))
    theirs = briefed(queue_dir, add("theirs", "02", "--origin", "slack:C2/2"))
    elsewhere = briefed(queue_dir, add("elsewhere", "01", "--origin", "slack:C1/1", topic="other-topic"))

    out = ok(q("dispatch", "--dry-run", "--origin", "slack:C1/1")).out
    expect(out, mine, elsewhere, "--origin slack:C1/1")
    refute(out, theirs)

    out = ok(q("dispatch", "--dry-run", "--topic", TOPIC)).out
    expect(out, mine, theirs)
    refute(out, elsewhere)

    out = ok(q("dispatch", "--dry-run", "--origin", "slack:C1/1", "--topic", TOPIC)).out
    expect(out, mine)
    refute(out, theirs, elsewhere)

    expect(ok(q("dispatch", "--dry-run", "--origin", "slack:C9/9")).out, "nothing ready")
    # A prefix is not the origin, exactly as `find --origin` compares it.
    expect(ok(q("dispatch", "--dry-run", "--origin", "slack:C1")).out, "nothing ready")

    out = ok(q("dispatch", "--dry-run")).out
    expect(out, mine, theirs, elsewhere)


@pytest.mark.parametrize("scope", [["--origin", "slack:C1/1"], ["--topic", TOPIC]])
def test_refs_beside_a_scope_are_refused(stubs, queue_dir, scope):
    ref = briefed(queue_dir, add("named", "01", "--origin", "slack:C1/1"))
    done = q("dispatch", ref, *scope)
    assert done.code != 0
    expect(done.out, "not both")
    assert creates(stubs) == []
    assert record(queue_dir, ref)["state"] == "queued"
