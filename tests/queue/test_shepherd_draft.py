"""The draft phase: a task's pull request is a draft while bots review it.

publish.conf's DRAFT_REPOS opts a repository in. There, the brief tells the
worker to open a DRAFT, shepherd judges that draft as the pull request it will
become — fixers, threads, a bot's review feedback relayed — and marks it ready
for review once every gate a merge would ask holds. Once: never back to draft,
never merged in the pass that flipped it, and never again once a person put it
back. A draft no task records, and every draft in an unlisted repository, is
still just a draft.
"""

import os
from pathlib import Path

import pytest
from kit_shepherd import repo
from queuekit import PUBLISH_CONF, ok
from test_shepherd_comments import ME, PR, Talk, author, talk_on

from harness import expect, git, refute, write
from harness import run_queue as q

FAILED = {"__typename": "CheckRun", "name": "CI", "status": "COMPLETED", "conclusion": "FAILURE"}
RUNNING = {"__typename": "CheckRun", "name": "CI", "status": "IN_PROGRESS", "conclusion": ""}


def draft_repos(listed: str) -> None:
    conf = Path(os.environ["FLEET_PUBLISH_ROOT"]) / "orchestration" / "publish.conf"
    write(conf, PUBLISH_CONF + f"DRAFT_REPOS={listed}\n")


@pytest.fixture
def draft(stubs, tmp_path, queue_dir) -> Talk:
    """An attested task's draft in a repository fleet merges in and the draft phase covers."""
    talk = talk_on(stubs, tmp_path, queue_dir, vetted=True)
    talk.shep.update(201, isDraft=True)
    draft_repos("github.com/thurbeen/fleet")
    return talk


def test_without_the_setting_a_draft_is_still_only_a_draft(stubs, tmp_path, queue_dir):
    talk = talk_on(stubs, tmp_path, queue_dir, vetted=True)
    talk.shep.update(201, isDraft=True, statusCheckRollup=[FAILED])
    out = talk.pass_()
    expect(out, "undetermined: still a draft")
    assert talk.shep.readied() == [] and talk.shep.merged() == [], out
    refute(talk.shep.tbx_log(), "session send")


def test_a_green_draft_is_marked_ready_once_and_merged_only_on_a_later_pass(draft):
    shep = draft.shep
    # A draft no task records is somebody's work in progress, whatever its checks say.
    shep.pr(202, headRefName="wip/someone", isDraft=True)

    dry = draft.pass_("--dry-run")
    expect(dry, "would-mark-ready")
    assert shep.readied() == [], dry

    out = draft.pass_()
    expect(out, "marked-ready", "still a draft")
    assert shep.readied() == [201], out
    expect(shep.gh_log(), f"pr ready {PR}")
    # Not in the pass that flipped it: whatever "ready for review" starts runs first.
    assert shep.merged() == [], out
    task = draft.task()
    assert task["shepherd_ready"][PR]["head"] == f"{201:040d}", task
    assert task["publish"]["state"] == "draft", task["publish"]

    out = draft.pass_()
    assert shep.merged() == [201], out
    assert shep.readied() == [201], out


def test_a_draft_gets_its_fixer_and_waits_for_its_checks(draft):
    shep = draft.shep
    shep.update(201, statusCheckRollup=[RUNNING])
    out = draft.pass_()
    expect(out, "checks still running")
    assert shep.readied() == [], out

    shep.update(201, statusCheckRollup=[FAILED])
    out = draft.pass_()
    expect(out, "checks-failed", "pinged")
    assert shep.readied() == [], out
    assert any("fix-" in c for c in draft.sends()), shep.tbx_log()


def test_a_bots_open_thread_is_relayed_and_holds_the_flip_until_resolved(draft):
    draft.thread("T1", "src/retry.rs", 42,
                 ("C1", "claude", "This retry never backs off.", "2026-10-01T10:00:00Z"), bot=True)
    # Status, not review: still never relayed.
    draft.comment("I1", "coverage-bot", "Coverage: 91%.", "2026-10-01T10:05:00Z", bot=True)
    out = draft.pass_()
    expect(out, "threads-open", "not marked ready for review", "comments sent: 1 new comment")
    assert draft.shep.readied() == [], out
    text = draft.briefs()[-1].read_text(encoding="utf-8")
    expect(text, "This retry never backs off.")
    refute(text, "Coverage: 91%")

    draft.said["threads"][0]["isResolved"] = True
    draft.save()
    out = draft.pass_()
    expect(out, "marked-ready")
    assert draft.shep.readied() == [201], out


def test_the_flip_is_one_way_and_a_person_who_undoes_it_wins(draft):
    shep = draft.shep
    draft.pass_()
    assert shep.readied() == [201]
    # A bot comments after the flip: relayed, and the thread holds the MERGE —
    # the pull request does not go back to draft.
    draft.thread("T2", "a.py", 1, ("C2", "claude", "One more nit.", "2026-10-02T10:00:00Z"), bot=True)
    out = draft.pass_()
    expect(out, "threads-open", "not merged while a review thread is unresolved")
    assert shep.merged() == [], out

    # A person puts it back in draft, and the thread is resolved: shepherd
    # never flips it a second time.
    draft.said["threads"][0]["isResolved"] = True
    draft.save()
    shep.update(201, isDraft=True)
    out = draft.pass_()
    expect(out, "left-alone", "it is a draft again")
    assert shep.readied() == [201] and shep.merged() == [], out


def test_a_refused_flip_is_reported_and_retried_next_pass(draft):
    out = q("shepherd", "--topic", draft.topic, SHEP_READY_REJECTED="201").out
    expect(out, "mark-ready-failed", "could not mark the pull request ready")
    assert "shepherd_ready" not in draft.task()
    expect(draft.pass_(), "marked-ready")


def test_a_bare_slug_is_refused_and_no_draft_phase_follows(stubs, tmp_path, queue_dir):
    talk = talk_on(stubs, tmp_path, queue_dir, vetted=True)
    talk.shep.update(201, isDraft=True)
    draft_repos("Thurbeen/fleet")
    run = q("shepherd", "--topic", talk.topic)
    expect(run.stderr, "DRAFT_REPOS: ignoring 'Thurbeen/fleet'", "must name its forge")
    expect(run.out, "still a draft")
    assert talk.shep.readied() == []


def test_the_brief_asks_for_a_draft_only_in_a_listed_repository(topic, queue_dir, tmp_path):
    listed = repo(tmp_path / "listed")
    git("remote", "add", "origin", "https://github.com/Thurbeen/fleet.git", cwd=listed)
    other = repo(tmp_path / "other")
    git("remote", "add", "origin", "https://github.com/Thurbeen/thurbox.git", cwd=other)
    draft_repos("github.com/Thurbeen/fleet")

    def brief(slug: str, where: Path, *extra: str) -> str:
        ok(q("add", topic, slug, "--title", slug, "--repo", str(where), "--branch", f"fix/{slug}", *extra))
        [path] = (queue_dir / topic).glob(f"*-{slug}/BRIEF.md")
        return path.read_text(encoding="utf-8")

    expect(brief("in-listed", listed), "**Draft.**", "gh pr create --draft")
    refute(brief("in-other", other), "**Draft.**")
    # A shape with no pull request has nothing to open as a draft.
    refute(brief("pushed", listed, "--publish", "push"), "**Draft.**")


def test_the_operator_agreeing_under_a_bot_s_finding_keeps_it_open(draft):
    """The operator, on the account fleet runs as, answers a bot's finding with
    "agreed let's fix". That reached nobody and counted as the finding's answer;
    it is the operator's word, so both are relayed and the finding stays open."""
    draft.thread("T1", "src/retry.rs", 42,
                 ("C1", "claude", "This retry never backs off.", "2026-10-01T10:00:00Z"), bot=True)
    draft.said["threads"][0]["comments"]["nodes"].append(
        {"id": "C2", "body": "agreed let's fix", "createdAt": "2026-10-01T10:05:00Z",
         "url": f"{PR}#discussion_rC2", "author": author(ME)})
    draft.save()
    out = draft.pass_()
    expect(out, "comments sent: 2 new comments")
    text = draft.briefs()[-1].read_text(encoding="utf-8")
    expect(text, "This retry never backs off.", "agreed let's fix", "In reply to claude:")
