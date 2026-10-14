"""An origin is told how its task ended: once at `shipped`, once at `landed` or `abandoned`.

`collect` closing a task on a shipped result, and `reap` (which `collect` runs)
moving it on, each give the task's origin one line — the outcome, the artifact,
and for `abandoned` the why — through `notify_origin.py`. Each is told once,
remembered beside the reconciler's runtime state and never on the record. A
session origin that is gone is told nothing, and a relay that fails fails
neither `collect` nor the task.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import yaml
from queuekit import ok, result

from harness import expect, run_queue as q, write

SID = "5b0c7d2e-0000-4000-8000-0000000000aa"
PR = "https://github.com/Thurbeen/thurbox/pull/"


def runtime() -> Path:
    path = Path(os.environ["FLEET_RECONCILE_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def listed(stubs, sid: str = SID, state: str = "idle") -> None:
    write(stubs.root / "sessions" / f"{sid}.json",
          json.dumps({"name": "Asker", "id": sid, "state": state, "backend_type": "local:tmux"}) + "\n")
    stubs.composer(sid, "")


def shipped(queue_dir: Path, stubs, slug: str, n: int, origin: str = f"thurbox:{SID}") -> str:
    ok(q("topic", "add", "told", "--title", "Told", "--prompt", "p"))
    ref = ok(q("add", "told", slug, "--title", f"Fix {slug}", "--repo", "/tmp/repo-a",
               "--branch", f"fix/{slug}", "--publish", "pr", "--origin", origin,
               THURBOX_SESSION=None)).stdout.strip()
    stubs.plain_pr(n, f"fix/{slug}")
    result(queue_dir / ref, "shipped", "Opened it.", f"{PR}{n}")
    return ref


def told(stubs, sid: str = SID) -> list[str]:
    return [c for c in stubs.calls("thurbox-cli", "session send") if c.split()[3] == sid]


def state(queue_dir: Path, ref: str) -> str:
    return yaml.safe_load((queue_dir / ref / "task.yaml").read_text(encoding="utf-8"))["state"]


def test_shipped_then_landed_are_each_told_once(queue_dir, stubs):
    runtime()
    listed(stubs)
    ref = shipped(queue_dir, stubs, "one", 41)
    out = ok(q("collect")).out
    assert len(told(stubs)) == 1, out
    expect(told(stubs)[0], f"fleet: {ref} shipped", "Fix one", f"{PR}41")

    ok(q("collect"))
    assert len(told(stubs)) == 1, "shipped was told again on a pass that moved nothing"

    stubs.pr_state(41, "MERGED")
    ok(q("collect"))
    assert state(queue_dir, ref) == "landed"
    assert len(told(stubs)) == 2
    expect(told(stubs)[1], f"fleet: {ref} landed", f"{PR}41")
    ok(q("reap"))
    ok(q("collect"))
    assert len(told(stubs)) == 2, "landed was told twice"


def test_abandoned_carries_the_why(queue_dir, stubs):
    runtime()
    listed(stubs)
    ref = shipped(queue_dir, stubs, "two", 42)
    ok(q("collect"))
    stubs.pr_state(42, "CLOSED")
    ok(q("collect"))
    assert state(queue_dir, ref) == "abandoned"
    expect(told(stubs)[-1], f"fleet: {ref} abandoned", "closed unmerged", "closed without merging")


def test_an_origin_mid_turn_is_posted_once_and_woken_at_rest(queue_dir, stubs):
    runtime()
    listed(stubs, state="working")
    ref = shipped(queue_dir, stubs, "three", 43)
    ok(q("collect"))
    ok(q("collect"))
    assert told(stubs) == []
    assert [m["woke"] for m in stubs.inbox(SID)] == [False], "the mailbox note was repeated"
    listed(stubs)
    ok(q("collect"))
    assert len(told(stubs)) == 1, "the waiting outcome was not retried at rest"
    expect(told(stubs)[0], f"{ref} shipped")


def test_a_gone_session_origin_is_told_nothing_and_the_log_says_why(queue_dir, stubs):
    runtime()
    ref = shipped(queue_dir, stubs, "four", 44)
    out = ok(q("collect")).out
    assert stubs.calls("thurbox-cli", "session send") == []
    assert stubs.calls("thurbox-cli", "message send") == []
    expect(out, f"{ref}:shipped told nobody", "thurbox no longer lists the session")
    assert state(queue_dir, ref) == "done", "the outcome is still recorded"


def test_a_failing_relay_does_not_fail_collect(queue_dir, stubs):
    runtime()
    rule = shlex.join([sys.executable, "-c", "import sys; sys.exit(7)"])
    write(Path(os.environ["FLEET_OWNERS_ROOT"]) / "orchestration" / "owners.conf", f"chat: = !{rule}\n")
    ref = shipped(queue_dir, stubs, "five", 45, origin="chat:room/9")
    done = q("collect")
    assert done.code == 0, done.out
    assert state(queue_dir, ref) == "done"
    expect(done.out, f"{ref}:shipped", "exit 7")


def test_a_relay_gets_the_origin_and_the_outcome(queue_dir, stubs, tmp_path):
    runtime()
    got = tmp_path / "relayed.json"
    keep = "import json, sys; open(sys.argv[1], 'w').write(json.dumps(sys.argv[2:]))"
    rule = shlex.join([sys.executable, "-c", keep, str(got)])
    write(Path(os.environ["FLEET_OWNERS_ROOT"]) / "orchestration" / "owners.conf", f"chat: = !{rule}\n")
    ref = shipped(queue_dir, stubs, "six", 46, origin="chat:room/9")
    ok(q("collect"))
    origin, line = json.loads(got.read_text(encoding="utf-8"))
    assert origin == "chat:room/9"
    expect(line, f"fleet: {ref} shipped", f"{PR}46")


def test_a_task_with_no_origin_tells_nobody_and_asks_thurbox_nothing(queue_dir, stubs):
    runtime()
    ok(q("topic", "add", "told", "--title", "Told", "--prompt", "p"))
    ref = ok(q("add", "told", "plain", "--title", "Plain", "--repo", "/tmp/repo-a",
               "--branch", "fix/plain", "--publish", "pr", THURBOX_SESSION=None)).stdout.strip()
    stubs.plain_pr(47, "fix/plain")
    result(queue_dir / ref, "shipped", "Opened it.", f"{PR}47")
    ok(q("collect"))
    assert stubs.calls("thurbox-cli", "session send") == []
    assert not (runtime() / "origin_outcomes.json").exists() or not json.loads(
        (runtime() / "origin_outcomes.json").read_text(encoding="utf-8")).get("pending")


def test_attach_never_reopens_a_concluded_task(queue_dir, stubs):
    """#252: a mistyped `attach` ref on a landed task rewrote it `dispatched`,
    so the next `collect` closed it again and told its origin twice."""
    runtime()
    listed(stubs)
    ref = shipped(queue_dir, stubs, "seven", 48)
    ok(q("collect"))
    stubs.pr_state(48, "MERGED")
    ok(q("collect"))
    ok(q("reap"))
    assert state(queue_dir, ref) == "landed"
    told_before = len(told(stubs))
    record = (queue_dir / ref / "task.yaml").read_text(encoding="utf-8")

    stray = "5b0c7d2e-0000-4000-8000-0000000000bb"
    refused = q("attach", ref, stray)
    assert refused.code != 0, refused.out
    expect(refused.out, ref, "landed")
    assert (queue_dir / ref / "task.yaml").read_text(encoding="utf-8") == record

    ok(q("collect"))
    assert state(queue_dir, ref) == "landed"
    assert len(told(stubs)) == told_before, "the origin was told again"
