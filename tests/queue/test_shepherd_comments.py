"""The shepherd's fifth delivery: review feedback nobody requested changes for.

A reviewer who leaves a comment rather than pressing "request changes" used to
reach nobody: the worker had finished its turn and `classify` read only the
review decision. So `shepherd` reads every review, comment and thread on each
open pull request, and sends what is new — once, by id — into the task's own
session when that session says it is at rest. Its own account and bots are
never sent, a turn is never interrupted, and the record that remembers what
was sent never holds back a fixer the same pull request needs next.
"""

import json
from pathlib import Path

import pytest
import yaml
from kit_forges import FAKE_GLAB, GlabStore, tripwire
from kit_shepherd import GH, Shep, repo
from queuekit import ok, result

from harness import expect, git, refute, write
from harness import run_queue as q

ME = "fleet-account"
WORKER = "c0ffee00-0000-0000-0000-000000000001"
PR = "https://github.com/Thurbeen/fleet/pull/201"

# `gh api graphql` for the feedback query, read from `shep/feedback/<n>.json`,
# and `gh api user`, which names the account fleet runs as. Everything else
# falls through to the shepherd's own stand-in.
FEEDBACK_GH = r'''
import json, os, sys
from pathlib import Path

_root = Path(os.environ["FLEET_STUB_ROOT"]) / "shep"
_a = sys.argv[1:]
if _a[:2] == ["api", "graphql"] and any(x.startswith("query=") and "reviews(" in x for x in _a):
    n = next(x for x in _a if x.startswith("number=")).split("=", 1)[1]
    doc = _root / "feedback" / f"{n}.json"
    pr = json.loads(doc.read_text(encoding="utf-8")) if doc.is_file() else {}
    pr = {"reviews": {"nodes": pr.get("reviews", [])}, "comments": {"nodes": pr.get("comments", [])},
          "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": pr.get("threads", [])}}
    print(json.dumps({"data": {"repository": {"pullRequest": pr}}}))
    raise SystemExit(0)
if _a[:1] == ["api"] and _a[-1:] == ["user"]:
    print(json.dumps({"login": "''' + ME + r'''"}))
    raise SystemExit(0)
''' + GH


def author(login: str, bot: bool = False) -> dict:
    return {"login": login, "__typename": "Bot" if bot else "User"}


class Talk:
    """One task, one open pull request, and what people said on it."""

    def __init__(self, shep: Shep, queue_dir: Path):
        self.shep, self.queue_dir = shep, queue_dir
        self.said: dict = {"reviews": [], "comments": [], "threads": []}
        self.topic = ""

    def save(self) -> None:
        write(self.shep.root / "feedback" / "201.json", json.dumps(self.said))

    def review(self, rid: str, who: str, body: str, at: str, state: str = "COMMENTED", bot: bool = False):
        self.said["reviews"].append({"id": rid, "state": state, "body": body, "submittedAt": at,
                                     "url": f"{PR}#pullrequestreview-{rid}", "author": author(who, bot)})
        self.save()

    def comment(self, cid: str, who: str, body: str, at: str, bot: bool = False):
        self.said["comments"].append({"id": cid, "body": body, "createdAt": at,
                                      "url": f"{PR}#issuecomment-{cid}", "author": author(who, bot)})
        self.save()

    def thread(self, tid: str, path: str, line: int, *comments: tuple, resolved: bool = False):
        self.said["threads"].append({
            "id": tid, "isResolved": resolved, "path": path, "line": line, "originalLine": line,
            "comments": {"nodes": [{"id": cid, "body": body, "createdAt": at, "url": f"{PR}#discussion_r{cid}",
                                    "author": author(who)} for cid, who, body, at in comments]},
        })
        self.save()

    def task(self) -> dict:
        return yaml.safe_load((self.queue_dir / self.topic / "01-talk" / "task.yaml").read_text(encoding="utf-8"))

    def sends(self, sid: str = WORKER) -> list[str]:
        return [c for c in self.shep.stubs.calls("thurbox-cli", "session send") if sid in c]

    def briefs(self) -> list[Path]:
        return sorted((self.queue_dir / self.topic / "01-talk").glob("fix-*-comments.md"))

    def pass_(self, *extra: str) -> str:
        return ok(q("shepherd", "--topic", self.topic, *extra)).out


@pytest.fixture
def talk(stubs, tmp_path, queue_dir) -> Talk:
    shep = Shep(stubs)
    stubs.tool("gh", FEEDBACK_GH)
    srepo = repo(tmp_path / "talk-repo")
    t = Talk(shep, queue_dir)
    t.topic = ok(q("topic", "add", "review-talk", "--title", "Comments reach the worker",
                   "--prompt", "send plain review comments to the session")).stdout.strip()
    # `pr`: green is never merged, so the pull request stays open for every pass.
    ok(q("add", t.topic, "talk", "--title", "A PR people talk on", "--repo", str(srepo),
         "--branch", "fix/talk", "--number", "01", "--publish", "pr"))
    git("branch", "fix/talk", cwd=srepo)
    result(queue_dir / t.topic / "01-talk", "shipped", "Opened it.", PR)
    ok(q("collect"))
    shep.perm("maintainer", "admin")
    shep.pr(201, headRefName="fix/talk", body="Plain.\n")
    shep.session(WORKER, "idle")
    ok(q("attach", f"{t.topic}/01-talk", WORKER))
    t.save()
    return t


def test_a_commented_review_reaches_an_idle_worker_once_and_not_twice(talk):
    talk.review("R1", "reviewer", "Why is the retry unbounded here?", "2026-10-01T10:00:00Z")
    out = talk.pass_()
    expect(out, "comments sent: 1 new comment")
    assert len(talk.sends()) == 1, talk.shep.tbx_log()
    [brief] = talk.briefs()
    expect(brief.read_text(encoding="utf-8"), "reviewer, review", "Why is the retry unbounded here?",
           f"{PR}#pullrequestreview-R1")
    expect(talk.sends()[0], str(brief))
    assert talk.task()["shepherd_comments"][PR]["sent"] == ["R1"]

    # The same comment, the next pass: nothing sent, nothing written.
    out = talk.pass_()
    refute(out, "comments sent")
    assert len(talk.sends()) == 1 and len(talk.briefs()) == 1, talk.shep.tbx_log()

    # A new line comment is new, and is sent alone, with where it sits.
    talk.thread("T1", "src/retry.rs", 42, ("C1", "reviewer", "Cap this at five.", "2026-10-01T11:00:00Z"))
    expect(talk.pass_(), "comments sent: 1 new comment")
    assert len(talk.sends()) == 2
    text = talk.briefs()[-1].read_text(encoding="utf-8")
    expect(text, "line comment on `src/retry.rs:42`", "Cap this at five.")
    refute(text, "Why is the retry unbounded")


def test_a_busy_worker_is_left_alone_until_it_is_at_rest(talk):
    talk.comment("I1", "reviewer", "Can this land after the release?", "2026-10-01T10:00:00Z")
    for state in ("working", "running"):
        talk.shep.session(WORKER, state)
        out = talk.pass_()
        expect(out, "comments left-alone", f"is {state}", "never into a turn")
        assert talk.sends() == [] and talk.briefs() == [], talk.shep.tbx_log()
        assert "shepherd_comments" not in talk.task()

    talk.shep.session(WORKER, "idle")
    expect(talk.pass_(), "comments sent")
    assert len(talk.sends()) == 1


def test_its_own_account_bots_and_answered_comments_are_not_sent(talk):
    talk.comment("I1", "renovate", "Bump the lockfile.", "2026-10-01T09:00:00Z", bot=True)
    talk.comment("I2", ME, "Done — rebased.", "2026-10-01T09:30:00Z")
    # Answered: fleet's account posted after it.
    talk.comment("I0", "reviewer", "Rebase please.", "2026-10-01T09:10:00Z")
    # A thread somebody resolved is answered too.
    talk.thread("T0", "a.py", 1, ("C0", "reviewer", "Typo.", "2026-10-01T09:20:00Z"), resolved=True)
    # An approval's thank-you is not a request.
    talk.review("R0", "reviewer", "LGTM, thanks!", "2026-10-01T09:40:00Z", state="APPROVED")
    out = talk.pass_()
    refute(out, "comments sent")
    assert talk.sends() == [], talk.shep.tbx_log()

    talk.comment("I3", "reviewer", "One more: rename the flag.", "2026-10-01T10:00:00Z")
    expect(talk.pass_(), "comments sent: 1 new comment")
    text = talk.briefs()[-1].read_text(encoding="utf-8")
    expect(text, "rename the flag")
    refute(text, "Bump the lockfile", "Done — rebased", "Rebase please", "Typo.", "LGTM")


def test_a_comment_record_does_not_hold_back_a_failing_check_fixer(talk):
    talk.review("R1", "reviewer", "Nit: name.", "2026-10-01T10:00:00Z")
    expect(talk.pass_(), "comments sent")
    talk.shep.update(201, statusCheckRollup=[{"__typename": "CheckRun", "name": "CI", "status": "COMPLETED",
                                              "conclusion": "FAILURE"}])
    out = talk.pass_()
    expect(out, "checks-failed", "dispatched")
    assert len(talk.sends()) == 2, talk.shep.tbx_log()


def test_a_change_request_whose_review_requested_changes_is_the_fixer_s(talk):
    talk.shep.update(201, reviewDecision="CHANGES_REQUESTED", mergeable="CONFLICTING")
    talk.review("R1", "reviewer", "Please split this.", "2026-10-01T10:00:00Z", state="CHANGES_REQUESTED")
    out = talk.pass_()
    expect(out, "conflicting", "dispatched")
    refute(out, "comments sent")
    assert talk.briefs() == [] and "shepherd_comments" not in talk.task()


def test_a_dry_run_says_what_it_would_send_and_sends_nothing(talk):
    talk.review("R1", "reviewer", "Why?", "2026-10-01T10:00:00Z")
    expect(talk.pass_("--dry-run"), "comments would-send: 1 new comment -> " + WORKER)
    assert talk.sends() == [] and talk.briefs() == []
    assert "shepherd_comments" not in talk.task()


def test_a_gone_worker_gets_a_fresh_session_for_its_comments(talk):
    # Cleaned up: thurbox no longer lists the worker's id.
    (talk.shep.stubs.root / "sessions" / f"{WORKER}.json").unlink()
    talk.review("R1", "reviewer", "Why?", "2026-10-01T10:00:00Z")
    out = talk.pass_()
    expect(out, "comments sent")
    [create] = talk.shep.creates()
    expect(create, "Answer the review comments on PR #201", "--repo-path")
    sid = talk.task()["shepherd_comments"][PR]["session"]
    assert sid.startswith("f1xe4000"), sid
    # The next comment goes to that session, not a second new one.
    talk.comment("I1", "reviewer", "And this?", "2026-10-01T11:00:00Z")
    expect(talk.pass_(), "comments sent")
    assert len(talk.shep.creates()) == 1 and len(talk.sends(sid)) == 2, talk.shep.tbx_log()


# --- GitLab: the same delivery, through `glab` --------------------------------------

MR = "https://gitlab.example.com/acme/group/widgets/-/merge_requests/401"


def test_gitlab_discussions_reach_the_worker_through_glab(tmp_path, stubs, queue_dir, monkeypatch):
    store = GlabStore(tmp_path / "gitlab")
    stubs.tool("glab", FAKE_GLAB)
    stubs.tool("gh", tripwire("gh: a GitLab merge request must never be asked about with gh"))
    env = {"FAKE_GLAB_DIR": str(store.root), "GITLAB_HOST": "gitlab.example.com"}
    glrepo = tmp_path / "gitlab-repo"
    git("init", "-q", "-b", "main", str(glrepo))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=glrepo)
    git("remote", "add", "origin", "https://gitlab.example.com/acme/group/widgets.git", cwd=glrepo)

    topic = ok(q("topic", "add", "gl-talk", "--title", "Comments on GitLab", "--prompt", "same, on GitLab",
                 **env)).stdout.strip()
    ok(q("add", topic, "talk", "--title", "An MR people talk on", "--repo", str(glrepo), "--branch", "fix/talk",
         "--number", "01", "--publish", "pr", **env))
    git("branch", "fix/talk", cwd=glrepo)
    result(queue_dir / topic / "01-talk", "shipped", "Opened it.", MR)
    store.mr(401, source_branch="fix/talk", description="Plain.")
    ok(q("collect", **env))
    stubs.session_is(WORKER, "idle")
    ok(q("attach", f"{topic}/01-talk", WORKER, **env))

    store.api("user", {"username": ME})
    store.api("discussions", [
        {"id": "d1", "notes": [{"id": 11, "body": "Why a new table?", "system": False, "resolvable": False,
                                "author": {"username": "reviewer"}, "created_at": "2026-10-01T10:00:00.000Z"}]},
        {"id": "d2", "notes": [{"id": 12, "body": "Off by one here.", "system": False, "resolvable": True,
                                "resolved": False, "author": {"username": "reviewer"},
                                "position": {"new_path": "db/schema.sql", "new_line": 7},
                                "created_at": "2026-10-01T10:05:00.000Z"}]},
        {"id": "d3", "notes": [{"id": 13, "body": "added 1 commit", "system": True,
                                "author": {"username": "reviewer"}, "created_at": "2026-10-01T10:06:00.000Z"}]},
        {"id": "d4", "notes": [{"id": 14, "body": "Pipeline is slow.", "system": False, "resolvable": False,
                                "author": {"username": "project_42_bot_abc"},
                                "created_at": "2026-10-01T10:07:00.000Z"}]},
        {"id": "d5", "notes": [{"id": 15, "body": "Will do.", "system": False, "resolvable": False,
                                "author": {"username": "helper", "bot": True},
                                "created_at": "2026-10-01T10:08:00.000Z"}]},
    ])

    out = ok(q("shepherd", "--topic", topic, **env)).out
    expect(out, "comments sent: 2 new comments")
    [brief] = sorted((queue_dir / topic / "01-talk").glob("fix-*-comments.md"))
    text = brief.read_text(encoding="utf-8")
    expect(text, "Why a new table?", "line comment on `db/schema.sql:7`", f"{MR}#note_12")
    refute(text, "added 1 commit", "Pipeline is slow", "Will do.")
    assert len([c for c in stubs.calls("thurbox-cli", "session send") if WORKER in c]) == 1

    # Sent once: a second pass over the same discussions sends nothing.
    refute(ok(q("shepherd", "--topic", topic, **env)).out, "comments sent")
    assert len([c for c in stubs.calls("thurbox-cli", "session send") if WORKER in c]) == 1
    assert stubs.calls("gh") == []
