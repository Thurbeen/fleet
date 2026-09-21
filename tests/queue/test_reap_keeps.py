"""Claim 9: what is never reaped, and why.

Four sessions had accumulated on one machine, three with merged pull requests,
the oldest holding twenty gigabytes since the previous day. The loop already
said "delete each session as it closes out"; it was documented, it was manual,
and it did not happen. These are the cases where the answer is still "leave
it", and getting any of them wrong kills live work or throws away the only
evidence of a failure.
"""

import json

import pytest
from queuekit import S2, deletions, ok, result

from harness import expect, refute, write
from harness import run_queue as q

# A document served to whoever is going to read it. Off any forge, which is
# the whole reason `served` names one: fleet cannot ask a server it did not
# start whether the reader is done.
DOCUMENT = "http://localhost:8123/review/the-state-vocabulary"


@pytest.fixture
def second_collected(first_landed, stubs, queue_dir) -> str:
    """02 collected with an attested pull request, its session still working."""
    stubs.pipeline_pr(1001, "fix/document-the-states")
    result(queue_dir / first_landed / "02-document-the-states", "shipped", "Documented the state vocabulary.",
           "https://github.com/Thurbeen/thurbox/pull/1001")
    ok(q("collect"))
    return first_landed


def worker(topic: str, number: str, slug: str, title: str, sid: str, state: str, outcome: str, note: str,
           stubs, queue_dir, publish: str | None = None, artifact: str | None = None) -> None:
    ok(q("add", topic, slug, "--title", title, "--repo", "/tmp/repo-a", "--branch", f"fix/{slug}",
         "--number", number, *(("--publish", publish) if publish else ())))
    ok(q("attach", f"{topic}/{number}-{slug}", sid))
    stubs.session_is(sid, state)
    result(queue_dir / topic / f"{number}-{slug}", outcome, note, artifact)


def test_a_session_thurbox_says_is_working_is_kept_whatever_the_record_claims(second_collected, stubs):
    stubs.pr_state(1001, "MERGED")
    # The merge lands the task, and the working session stays anyway.
    expect(q("reap").out, "02-document-the-states", "working")
    refute(deletions(stubs), S2)
    # Landing and releasing are two questions: it landed, and still names its session.
    expect(q("show", f"{second_collected}/02-document-the-states").out, "state:       landed", "22222222")


def test_a_task_the_worker_gave_up_in_keeps_its_session_as_the_evidence(first_landed, stubs, queue_dir):
    sid = "66666666-6666-6666-6666-666666666666"
    worker(first_landed, "06", "investigate-the-crash", "Investigate the crash", sid, "idle", "failed",
           "The crash does not reproduce here. The worktree has the logs.", stubs, queue_dir)
    expect(q("collect").out, "06-investigate-the-crash", "evidence")
    refute(deletions(stubs), sid)


def test_a_task_with_no_artifact_lands_and_collect_releases_its_session(first_landed, stubs, queue_dir):
    """A task that produced no artifact skips straight through rather than wait
    for a merge that is never coming, and `collect` reaps itself, because a step
    only a human remembers does not run."""
    sid = "77777777-7777-7777-7777-777777777777"
    worker(first_landed, "07", "answer-a-question", "Answer a question", sid, "idle", "not-applicable",
           "The behaviour already worked; there was nothing to change.", stubs, queue_dir)
    expect(q("collect").out, "07-answer-a-question", "reaped")
    # With --force, so the worktree actually goes, and the session the record held.
    expect(deletions(stubs), "--force", sid)
    # The record keeps the receipt.
    expect(q("show", f"{first_landed}/07-answer-a-question").out, "deleted")


def test_uncovered_is_not_idle_and_the_lead_is_never_a_candidate(first_landed, stubs, queue_dir):
    # An agent wired to report nothing says nothing by being quiet, so the reap
    # reads the word and never the silence.
    sid = "88888888-8888-8888-8888-888888888888"
    worker(first_landed, "08", "tidy-the-readme", "Tidy the readme", sid, "uncovered", "not-applicable",
           "Nothing to tidy.", stubs, queue_dir)
    expect(q("collect").out, "uncovered")
    refute(deletions(stubs), sid)

    # The lead's own session is refused by name, even if a record names it.
    expect(q("reap", "--dry-run", THURBOX_SESSION=sid).out, "lead")


def test_a_declared_uncovered_session_that_reports_is_reaped_like_any_other(
    first_landed, stubs, queue_dir
):
    """The mirror of the test above, and the other half of the same rule: the
    reap reads the WORD thurbox publishes, and a profile's `uncovered: true`
    is not one of its inputs.

    An operator who wires their agent's own hooks — cursor takes them from a
    config file rather than a flag — gets a session that says `done` through
    `state_source: hook` with `hook_coverage: none`. That is the agent saying
    it is at rest, so the session is released rather than left for a hand.
    """
    sid = "99999999-9999-9999-9999-999999999999"
    ok(q("add", first_landed, "port-the-hooks", "--title", "Port the hooks",
         "--repo", "/tmp/repo-a", "--branch", "fix/port-the-hooks", "--number", "09",
         "--profile", "cursor-trusted"))
    ok(q("attach", f"{first_landed}/09-port-the-hooks", sid))
    write(stubs.root / "sessions" / f"{sid}.json", json.dumps({
        "id": sid, "name": f"worker {sid}", "agent": "cursor-agent",
        "reports_as": None, "hook_reported": True, "hook_coverage": "none",
        "hook_state": "done", "hook_state_age_secs": 12,
        "state": "done", "state_source": "hook",
        "cwd": str(stubs.root), "backend_type": "local-tmux", "worktrees": [],
    }) + "\n")
    result(queue_dir / first_landed / "09-port-the-hooks", "not-applicable",
           "The hooks were already wired.")

    expect(q("collect").out, "09-port-the-hooks", "reaped")
    expect(deletions(stubs), "--force", sid)


def test_collect_can_be_told_to_leave_every_session_alone(first_landed):
    out = q("collect", "--no-reap").out
    expect(out, "no-reap")
    refute(out, "reaped")


def test_a_served_document_keeps_its_session_until_a_person_closes_the_review(
    first_landed, stubs, queue_dir
):
    """The keep `none` could not express, and the only thing that ends it.

    Five tasks whose deliverable was a served document were collected, landed
    and reaped on the same pass, and every reader who annotated one of those
    documents and sent it back was told "No agent is listening right now".
    The document was published the moment it was served; the REVIEW had not
    started. `served` is that distinction, and nothing but a person saying the
    reader is done closes it — fleet cannot poll a server it did not start.
    """
    sid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    ref = f"{first_landed}/10-serve-the-explainer"
    worker(first_landed, "10", "serve-the-explainer", "Serve the explainer", sid, "idle", "shipped",
           "The explainer is served; the reader has not answered yet.", stubs, queue_dir,
           publish="served", artifact=DOCUMENT)

    # The task concludes on the worker's word, exactly as a `none` one does —
    # and the session stays, which is the whole difference.
    out = q("collect").out
    expect(out, "10-serve-the-explainer", "shipped", DOCUMENT, "served")
    refute(out, "reaped")
    refute(deletions(stubs), sid)

    # Every later pass keeps it for the same reason, and names what ends it.
    expect(q("reap").out, "10-serve-the-explainer", "kept", "reviewed")
    refute(deletions(stubs), sid)
    expect(q("show", ref).out, "publish:     served", "reader")

    # The person who read it says so, and only then is the session released.
    expect(q("reviewed", ref).out, "reap")
    expect(q("reap").out, "landed", "reaped")
    expect(deletions(stubs), "--force", sid)
    expect(q("show", ref).out, "state:       landed", "reviewed:")


def test_a_none_task_whose_deliverable_no_forge_holds_is_still_released(
    first_landed, stubs, queue_dir
):
    """The other direction, and the reason `served` is a word of its own.

    `none` also covers an issue filed and a machine swept — deliverables
    nobody is waiting to answer. Keeping every `none` session would have been
    the cheap fix and it would leak one session per sweep.
    """
    sid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    worker(first_landed, "11", "file-the-issue", "File the issue", sid, "idle", "shipped",
           "Filed the issue; there is nothing to merge and nobody to answer.", stubs, queue_dir,
           publish="none", artifact="https://github.com/Thurbeen/thurbox/issues/77")
    expect(q("collect").out, "11-file-the-issue", "reaped")
    expect(deletions(stubs), "--force", sid)


def test_a_served_task_that_served_nothing_waits_on_nobody(first_landed, stubs, queue_dir):
    """The wait needs a document, or it is a wait with no end.

    `not-applicable` concludes having produced nothing. Read as a served
    document it would stand `open` for good — session held, and every task
    blocked on it held with it — until somebody closed the review of a
    document that was never served.
    """
    sid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    worker(first_landed, "12", "serve-nothing", "Serve nothing", sid, "idle", "not-applicable",
           "There was nothing to write up, so nothing was served.", stubs, queue_dir,
           publish="served")
    expect(q("collect").out, "12-serve-nothing", "reaped")
    expect(deletions(stubs), "--force", sid)


def test_a_served_task_shipped_without_a_url_is_held_like_any_unproven_claim(
    first_landed, stubs, queue_dir
):
    """`shipped` claims a reader can open something. Without an address they cannot.

    Closing it would keep a session for a reader who was never handed the
    document — the same stranded session, reached from the other end.
    """
    sid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    worker(first_landed, "13", "serve-the-nameless", "Serve the nameless", sid, "idle", "shipped",
           "Served it, and forgot to say where.", stubs, queue_dir, publish="served")
    expect(q("collect").out, "13-serve-the-nameless", "NOT CLOSED")
    refute(deletions(stubs), sid)

    # With the address, it closes and the session is kept for the reader.
    result(queue_dir / first_landed / "13-serve-the-nameless", "shipped",
           "Served it, and here is where.", DOCUMENT)
    expect(q("collect").out, "13-serve-the-nameless", "shipped", DOCUMENT)
    refute(deletions(stubs), sid)

    # AND THE REFUSED PASS LEAVES NOTHING BEHIND. `served`'s clean verdict is
    # `skipped`, which writes no publish state for any other method — so the
    # earlier `unverified` stood on the record, in progress.jsonl and as a red
    # UNVERIFIED in the pane, over a document served exactly as asked.
    show = q("show", f"{first_landed}/13-serve-the-nameless").out
    refute(show, "unverified")
    expect(show, "published:   served")


def test_a_review_cannot_be_closed_before_the_worker_has_concluded(
    first_landed, stubs, queue_dir
):
    """Recorded on a task still running, it lands and reaps in the pass that
    first reads the result — the document served and its worker gone in one
    command, which is the failure `served` exists to prevent."""
    sid = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    ok(q("add", first_landed, "serve-the-diagram", "--title", "Serve the diagram",
         "--repo", "/tmp/repo-a", "--branch", "fix/serve-the-diagram", "--number", "14",
         "--publish", "served"))
    ref = f"{first_landed}/14-serve-the-diagram"
    ok(q("attach", ref, sid))
    stubs.session_is(sid, "idle")

    out = q("reviewed", ref)
    assert out.code != 0, out.out
    # The state it is in, and the command that actually moves that state on.
    expect(out.out, "dispatched", "collect")

    # A task that already LANDED is refused too, and never sent to `collect`,
    # which skips a landed task entirely — advice that is a no-op is worse
    # than none.
    worker(first_landed, "15", "serve-nothing-either", "Serve nothing either",
           "ffffffff-ffff-ffff-ffff-ffffffffffff", "idle", "not-applicable",
           "Nothing to serve.", stubs, queue_dir, publish="served")
    ok(q("collect"))
    out = q("reviewed", f"{first_landed}/15-serve-nothing-either")
    assert out.code != 0, out.out
    expect(out.out, "landed", "already landed")
    refute(out.out, "collect")

    # And a task that publishes something else is not held for a reader at all.
    out = q("reviewed", f"{first_landed}/02-document-the-states")
    assert out.code != 0, out.out
    expect(out.out, "attested")
