"""`FIXER` in reconcile.conf: what a broken pull request gets, and how often.

`message` types the fix brief into the task's own worker, which `reap` keeps
alive while the pull request is open for exactly this; `session` spawns a new
one on the branch. The claims are the ones a two-minute shepherd clock leans
on: each mode reaches the right session, a second pass over an unchanged pull
request sends nothing in either, a busy worker is never typed into, a gone one
falls back out loud, and a fixer at rest IS sent again once the pull request
breaks in a new way — another condition, or the same one on a pushed head.
"""

import os
from pathlib import Path

import pytest
import yaml
from kit_shepherd import Shep, repo, result
from queuekit import ok

from harness import expect, git, refute, write
from harness import run_queue as q

WORKER = "70e4e400-0000-0000-0000-000000000001"
REF_DIR = "01-broken"


@pytest.fixture
def shep(stubs) -> Shep:
    return Shep(stubs)


@pytest.fixture
def ftask(shep, tmp_path, queue_dir) -> str:
    """One task, shipped as PR 201 which now conflicts, its worker at rest."""
    srepo = repo(tmp_path / "fixer-repo")
    topic = ok(q("topic", "add", "fixer-mode", "--title", "Fix a PR two ways",
                 "--prompt", "send the fix to the worker or a new session")).stdout.strip()
    ok(q("add", topic, "broken", "--title", "A PR that breaks", "--repo", str(srepo),
         "--branch", "fix/broken", "--number", "01"))
    result(queue_dir / topic / REF_DIR, "https://github.com/Thurbeen/fleet/pull/201")
    git("branch", "fix/broken", cwd=srepo)
    shep.perm("maintainer", "admin")
    shep.pr(201, mergeable="CONFLICTING", headRefName="fix/broken")
    ok(q("collect"))
    shep.session(WORKER, "idle")
    ok(q("attach", f"{topic}/{REF_DIR}", WORKER))
    return topic


def fixer(mode: str) -> None:
    write(Path(os.environ["FLEET_RECONCILE_CONF_ROOT"]) / "orchestration" / "reconcile.conf", f"FIXER={mode}\n")


def record(queue_dir: Path, topic: str) -> dict:
    return yaml.safe_load((queue_dir / topic / REF_DIR / "task.yaml").read_text(encoding="utf-8"))


def sends_to(shep: Shep, sid: str) -> list[str]:
    return shep.stubs.calls("thurbox-cli", f"session send {sid}")


def test_message_is_the_default_and_types_into_the_workers_own_session_once(ftask, shep, queue_dir):
    out = q("shepherd", "--topic", ftask).out
    expect(out, "dispatched", "messaged its own worker")
    assert len(sends_to(shep, WORKER)) == 1, shep.tbx_log()
    assert shep.creates() == [], shep.tbx_log()
    doc = record(queue_dir, ftask)
    assert doc["shepherd"]["via"] == "message" and doc["shepherd"]["session"] == WORKER
    assert doc["shepherd"]["head"] == f"{201:040d}"
    # `fleet queue send`'s own receipt, beside the ones a person sent.
    assert doc["sends"][-1]["session"] == WORKER and doc["sends"][-1]["delivered"]
    expect(doc["sends"][-1]["text"], "fix-01-conflicting.md")

    # A two-minute clock comes round on the same pull request: nothing again.
    out = q("shepherd", "--topic", ftask).out
    expect(out, "in-flight")
    assert len(sends_to(shep, WORKER)) == 1, out + shep.tbx_log()
    assert len(list((queue_dir / ftask / REF_DIR).glob("fix-*.md"))) == 1


def test_a_new_head_or_a_new_condition_at_rest_is_a_new_message(ftask, shep, queue_dir):
    q("shepherd", "--topic", ftask)
    # The worker pushed a rebase that still conflicts, and is at rest again.
    shep.update(201, headRefOid="a" * 40)
    out = q("shepherd", "--topic", ftask).out
    expect(out, "dispatched", "is still conflicting")
    assert len(sends_to(shep, WORKER)) == 2, out + shep.tbx_log()
    assert record(queue_dir, ftask)["shepherd"]["head"] == "a" * 40

    # Same head, the conflict gone and the review now asking for changes.
    shep.update(201, mergeable="MERGEABLE", reviewDecision="CHANGES_REQUESTED")
    out = q("shepherd", "--topic", ftask).out
    expect(out, "dispatched", "it is changes-requested now")
    assert len(sends_to(shep, WORKER)) == 3, out + shep.tbx_log()

    # And unchanged once more: nothing.
    q("shepherd", "--topic", ftask)
    assert len(sends_to(shep, WORKER)) == 3, shep.tbx_log()


def test_a_busy_worker_waits_for_a_later_pass_in_either_mode(ftask, shep):
    for mode in ("message", "session"):
        fixer(mode)
        shep.session(WORKER, "working")
        out = q("shepherd", "--topic", ftask).out
        expect(out, "left-alone", "a later pass")
        assert sends_to(shep, WORKER) == [] and shep.creates() == [], out + shep.tbx_log()

    fixer("message")
    shep.session(WORKER, "idle")
    q("shepherd", "--topic", ftask)
    assert len(sends_to(shep, WORKER)) == 1, shep.tbx_log()


def test_message_falls_back_to_a_new_session_when_the_worker_is_gone_and_says_so(ftask, shep, queue_dir):
    (shep.stubs.root / "sessions" / f"{WORKER}.json").unlink()
    out = q("shepherd", "--topic", ftask, "--dry-run").out
    expect(out, "would-dispatch", "is gone", "fell back to a new session")

    out = q("shepherd", "--topic", ftask).out
    expect(out, "dispatched", f"its own worker {WORKER} is gone; FIXER=message fell back to a new session")
    assert len(shep.creates()) == 1 and sends_to(shep, WORKER) == [], shep.tbx_log()
    rec = record(queue_dir, ftask)["shepherd"]
    assert rec["via"] == "session" and "fell back" in rec["fallback"]

    q("shepherd", "--topic", ftask)
    assert len(shep.creates()) == 1, shep.tbx_log()


def test_session_spawns_a_new_session_and_never_types_into_the_worker(ftask, shep, queue_dir):
    fixer("session")
    out = q("shepherd", "--topic", ftask, "--dry-run").out
    expect(out, "a fresh session on the branch")
    refute(out, "messaging")

    out = q("shepherd", "--topic", ftask).out
    expect(out, "dispatched")
    assert len(shep.creates()) == 1, out + shep.tbx_log()
    assert sends_to(shep, WORKER) == [], shep.tbx_log()
    rec = record(queue_dir, ftask)["shepherd"]
    assert rec["via"] == "session" and rec["session"] != WORKER and "fallback" not in rec

    out = q("shepherd", "--topic", ftask).out
    expect(out, "in-flight")
    assert len(shep.creates()) == 1, out + shep.tbx_log()


def test_a_fixer_mode_it_does_not_know_sends_nothing_and_says_why(ftask, shep):
    fixer("both")
    out = q("shepherd", "--topic", ftask).out
    expect(out, "not-dispatched", "FIXER=both", "neither 'message' nor 'session'")
    assert sends_to(shep, WORKER) == [] and shep.creates() == [], shep.tbx_log()
