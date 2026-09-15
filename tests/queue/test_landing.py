"""Claim 3: a recorded blocker serializes, and clears only when its task LANDS.

`outcome: shipped` means a pull request EXISTS. Whether it landed is a question
only the forge can answer, and it is the same question that decides whether a
session may be reaped. A task collected `shipped` used to release its
dependents while its pull request was still open and unreviewed.
"""

from queuekit import S1, S2, result

from harness import expect, refute
from harness import run_queue as q


def test_a_blocker_clears_on_the_merge_and_not_on_the_conclusion(attached, stubs, queue_dir):
    expect(q("collect").out, "0 result")
    expect(q("plan").out, "waiting: 1")

    stubs.session_is(S1, "idle")
    stubs.session_is(S2, "working")
    stubs.pipeline_pr(999, "fix/drop-idle-default")
    result(queue_dir / attached / "01-drop-idle-default", "shipped",
           "Dropped the idle default; an unreported session now reads `unreported`.",
           "https://github.com/Thurbeen/thurbox/pull/999")

    out = q("collect").out
    # The worker's own conclusion and its artifact — and the session is kept.
    expect(out, "shipped", "pull/999", "still open")
    refute(out, "reaped")

    plan = q("plan").out
    expect(plan, "waiting: 1")
    refute(plan, "ready: 2")

    # The merge: the only thing that lands a task, and that authorises
    # deleting the session that produced it.
    stubs.pr_state(999, "MERGED")

    expect(q("reap", "--dry-run").out, "would be landed", "would reap")
    assert stubs.calls("thurbox-cli", "session delete") == [], "a dry run deleted a session"
    expect(q("plan").out, "waiting: 1")

    expect(q("reap").out, "landed", "reaped")
    deletions = "\n".join(stubs.calls("thurbox-cli", "session delete"))
    # Forced, or the worktree is never actually freed; and the session the record held.
    expect(deletions, "--force", S1)

    show = q("show", f"{attached}/01-drop-idle-default").out
    expect(show, "state:       landed", "reaped:")
    refute(show, "session:     11111111")

    plan = q("plan").out
    # 03 is ready once 01 LANDED, and joins 04, which never waited.
    expect(plan, "03-render-detected-agent", "ready: 2")
    refute(plan, "01-drop-idle-default")


def test_the_record_carries_the_topic_view_without_any_new_field(first_landed):
    expect(q("list").out, first_landed, "04-log-state-changes")
    expect(q("check").out, "ok")
