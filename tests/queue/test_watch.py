"""Claim 4: a turn ending is not a task finishing.

`watch` folds transitions into each task's record and closes nothing. It is
also the wake proof: the lead READS a stream when it chooses, and reads a file
the worker wrote; nothing is delivered into its terminal.

The stream is thurbox's own `thurbox-cli watch --json`, replayed by the stub
from a recorded file — the real command path, flags and all, rather than the
`FLEET_QUEUE_WATCH_CMD` override, which replaces the whole command.
"""

from queuekit import S1, S2, ok

from harness import expect, refute
from harness import run_queue as q
from harness import stream_event


def floors(path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("watch_from:")]


def test_a_turn_ending_is_not_a_task_finishing(attached, stubs, queue_dir):
    tasks = queue_dir / attached
    # The stream was at seq 100 before either task attached, so each task's floor
    # comes from the high-water mark — not from 0 and not from "now".
    for task in ("01-drop-idle-default", "02-document-the-states"):
        assert floors(tasks / task / "task.yaml") == ["watch_from: 100"]
    assert not (queue_dir / ".cursor").exists(), "a queue-wide cursor another task could consume"

    stubs.stream(stream_event(101, S1, "working"), stream_event(102, S2, "working"),
                 stream_event(103, S1, "done"))

    out = q("watch", "--for-secs", "1").out
    expect(out, "01-drop-idle-default", "seq 101", "no result")
    refute(out, "done: ")

    state = [line for line in q("show", f"{attached}/01-drop-idle-default").out.splitlines() if "state:" in line]
    expect("\n".join(state), "dispatched")
    assert (tasks / "01-drop-idle-default" / "progress.jsonl").stat().st_size > 0

    # Each floor resumes, so a second watch replays nothing.
    refute(q("watch", "--for-secs", "1").out, "seq 101")


def test_a_genuine_zero_floor_is_not_treated_as_no_floor(stubs, queue_dir):
    # `attach` stamps 0 when the stream's high-water mark really is 0 — a
    # brand-new thurbox — and that must stay distinct from no floor at all, or
    # the first watch drops `--since` and starts from "now", losing whatever
    # happened in between.
    topic = ok(q("topic", "add", "zero-cursor", "--prompt", "prove a real zero cursor is not dropped")).stdout.strip()
    ok(q("add", topic, "only-task", "--title", "only task", "--repo", "/tmp/repo-z",
         "--branch", "fix/only-task", "--number", "01"))
    ok(q("attach", f"{topic}/01-only-task", "33333333-3333-3333-3333-333333333333"))
    q("watch", "--for-secs", "0")

    assert floors(queue_dir / topic / "01-only-task" / "task.yaml") == ["watch_from: 0"]
    watched = [c for c in stubs.calls("thurbox-cli", "watch") if "--initial" not in c]
    expect("\n".join(watched), "--since 0")


def test_a_declared_uncovered_session_that_reports_is_folded_like_any_other(briefed, stubs, queue_dir):
    """`watch` folds by SESSION ID off thurbox's own stream, and reads no
    profile at all.

    A `cursor-trusted` worker declares `uncovered: true` because fleet can
    wire it no hook family — but an operator whose agent takes its hooks from
    a config file rather than a flag has one reaching the stream all the same,
    with `state_source: hook` beside a `hook_coverage: none`. Its transitions
    land in the record exactly as a covered agent's do.
    """
    sid = "44444444-4444-4444-4444-444444444444"
    ok(q("add", briefed, "port-the-hooks", "--title", "Port the hooks", "--repo", "/tmp/repo-a",
         "--branch", "fix/port-the-hooks", "--number", "05", "--profile", "cursor-trusted"))
    stubs.stream(stream_event(200, sid, "working", event="present"))
    ok(q("attach", f"{briefed}/05-port-the-hooks", sid))

    reported = stream_event(201, sid, "done")
    stubs.stream({**reported, "state_source": "hook", "hook_coverage": "none"})
    expect(q("watch", "--for-secs", "1").out, "05-port-the-hooks", "seq 201")
    assert (queue_dir / briefed / "05-port-the-hooks" / "progress.jsonl").stat().st_size > 0
