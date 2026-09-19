"""Claim 9: what is never reaped, and why.

Four sessions had accumulated on one machine, three with merged pull requests,
the oldest holding twenty gigabytes since the previous day. The loop already
said "delete each session as it closes out"; it was documented, it was manual,
and it did not happen. These are the cases where the answer is still "leave
it", and getting any of them wrong kills live work or throws away the only
evidence of a failure.
"""

import pytest
from queuekit import S2, deletions, ok, result

from harness import expect, refute
from harness import run_queue as q


@pytest.fixture
def second_collected(first_landed, stubs, queue_dir) -> str:
    """02 collected with an attested pull request, its session still working."""
    stubs.pipeline_pr(1001, "fix/document-the-states")
    result(queue_dir / first_landed / "02-document-the-states", "shipped", "Documented the state vocabulary.",
           "https://github.com/Thurbeen/thurbox/pull/1001")
    ok(q("collect"))
    return first_landed


def worker(topic: str, number: str, slug: str, title: str, sid: str, state: str, outcome: str, note: str,
           stubs, queue_dir) -> None:
    ok(q("add", topic, slug, "--title", title, "--repo", "/tmp/repo-a", "--branch", f"fix/{slug}",
         "--number", number))
    ok(q("attach", f"{topic}/{number}-{slug}", sid))
    stubs.session_is(sid, state)
    result(queue_dir / topic / f"{number}-{slug}", outcome, note)


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


def test_collect_can_be_told_to_leave_every_session_alone(first_landed):
    out = q("collect", "--no-reap").out
    expect(out, "no-reap")
    refute(out, "reaped")
