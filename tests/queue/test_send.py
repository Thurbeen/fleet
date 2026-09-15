"""Claim 18: a message the lead sent is comparable against what moved after it.

From a real session on 2026-09-09: the lead sent new scope to a parked worker,
`session send` reported success, and ten minutes later the session read `done`,
age 3043s — a state from BEFORE the message. The worker looked dead. It had
taken the message, done the work and committed it, and the lead found out only
by opening the worker's worktree and running `git log`.

So `send` records the instant and a baseline of the branch head; `list` and
`show` report a commit or a transition dated after it as movement, a silence as
a silence and never a verdict, a git this machine cannot read as `not checked`,
a send that did not go in as `NOT DELIVERED`, and a task nobody messaged as
nothing at all. Nothing here writes `state` or `outcome`.
"""

import json
from datetime import datetime, timedelta

import pytest
import yaml
from kit_display import rows, set_field
from queuekit import ok

from harness import expect, git, refute, write
from harness import run_queue as q

MOVED = "cccccccc-0000-0000-0000-000000000001"
QUIET = "cccccccc-0000-0000-0000-000000000002"
NEVER = "cccccccc-0000-0000-0000-000000000003"
REMOTE = "cccccccc-0000-0000-0000-000000000004"


@pytest.fixture
def live_repo(tmp_path):
    """A real checkout, because the branch head is read out of the task's own repo:
    a worker's worktree shares this object store, so its commit moves the ref here."""
    repo = tmp_path / "live-repo"
    git("init", "-q", "-b", "main", str(repo))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=repo)
    return repo


@pytest.fixture
def messaged(live_repo, stubs, queue_dir):
    topic = ok(q("topic", "add", "course-correct", "--title", "Message a worker mid-flight",
                 "--prompt", "tell a parked worker about new scope")).stdout.strip()

    def task(slug: str, number: str, sid: str) -> str:
        ok(q("add", topic, slug, "--title", f"Task {slug}", "--repo", str(live_repo),
             "--branch", f"feat/{slug}", "--number", number))
        ok(q("attach", f"{topic}/{number}-{slug}", sid))
        stubs.session_is(sid, "done", 3043)
        # The worker's own spawn is what creates the branch.
        git("branch", f"feat/{slug}", cwd=live_repo)
        return f"{topic}/{number}-{slug}"

    task.topic = topic
    return task


def test_a_send_is_delivered_and_recorded_with_a_baseline(messaged, stubs):
    ref = messaged("moved", "01", MOVED)
    out = q("send", ref, "Also update the changelog.").out
    # The queue sends it itself, so the lead stops reaching past it.
    expect(out, "delivered", "baseline:")
    expect("\n".join(stubs.calls("thurbox-cli", "session send")), "Also update the changelog.")


def test_a_worker_that_moved_after_the_message_is_visible_as_moved(messaged, live_repo, queue_dir):
    ref = messaged("moved", "01", MOVED)
    q("send", ref, "Also update the changelog.")

    # The exact evidence the lead had to dig out of a foreign worktree.
    git("commit", "-q", "--allow-empty", "-m", "the work the lead thought had never happened", cwd=live_repo)
    git("branch", "-f", "feat/moved", "HEAD", cwd=live_repo)
    row = rows(q("list", "--topic", messaged.topic).out, "01-moved")
    expect(row, "committed")
    refute(row, "no commit since")

    # A transition dated after the message is the half `watch` produces. The older
    # event is the trap: folded in AFTER the message, and still not movement.
    # Dated against the RECORDED send, so one is unambiguously before and one after.
    task = queue_dir / ref
    sent = datetime.fromisoformat(yaml.safe_load((task / "task.yaml").read_text(encoding="utf-8"))["sends"][-1]["at"])
    now = datetime.now(sent.tzinfo).isoformat()
    write(task / "progress.jsonl", "".join(json.dumps(r) + "\n" for r in (
        {"seq": 1, "at": (sent - timedelta(hours=2)).isoformat(), "to": "working", "observed": now},
        {"seq": 2, "at": (sent + timedelta(seconds=1)).isoformat(), "to": "done", "observed": now},
    )))
    expect(q("show", ref).out, "transitioned", "messaged:")


def test_a_message_with_nothing_moving_since_is_a_fact_and_never_a_verdict(messaged, queue_dir):
    ref = messaged("quiet", "02", QUIET)
    q("send", ref, "Anything to report?")

    out = q("show", ref).out
    expect(out, "no commit since", "no transition since", "not what the worker is doing")
    refute(out, "is stuck", "is dead", "unreachable")
    # The record keeps the send itself, not a flag — and a message is not a completion.
    expect((queue_dir / ref / "task.yaml").read_text(encoding="utf-8"), "sends:")
    expect(out, "state:       dispatched")
    refute(out, "outcome:     shipped")

    # Once the task closes, the send is part of the RECORD and not of what is happening.
    set_field(queue_dir, ref, "state", "done")
    refute(rows(q("list", "--topic", messaged.topic).out, "02-quiet"), "no commit since")
    expect(q("show", ref).out, "this task concluded")


def test_a_task_no_lead_wrote_to_reads_as_it_always_did(messaged):
    # Named without the word it refutes: pytest puts the test's name in every path `show` prints.
    ref = messaged("never", "03", NEVER)
    out = q("show", ref).out
    refute(out, "messaged", "no commit since")
    refute(rows(q("list", "--topic", messaged.topic).out, "03-never"), "messaged")


def test_a_git_this_machine_cannot_read_is_not_checked(messaged, queue_dir):
    """A task that runs on a host is the case that matters: its git is over there."""
    ref = messaged("remote", "04", REMOTE)
    set_field(queue_dir, ref, "host", "devbox")
    q("send", ref, "How is it going?")
    expect(q("show", ref).out, "commit not checked", "devbox")


def test_a_send_that_did_not_go_in_is_not_delivered(messaged):
    """The trust step cannot get past a session thurbox has never heard of, so
    nothing was typed — written down, rather than guessed at from a silence."""
    ref = messaged("never", "03", NEVER)
    ok(q("attach", ref, "cccccccc-0000-0000-0000-0000000dead1"))
    expect(q("send", ref, "Are you there?").out, "NOT DELIVERED")
    expect(q("show", ref).out, "NOT DELIVERED")
