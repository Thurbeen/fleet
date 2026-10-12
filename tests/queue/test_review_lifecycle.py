"""Publication cannot discharge a configured independent review obligation."""

import json
import os
from pathlib import Path

import pytest
from queuekit import ok
from kit_shepherd import THURBOX
from test_shepherd_comments import PR, WORKER, talk_on

from harness import expect, git, write
from harness import run_queue as q

REVIEWER = "c0ffee00-0000-0000-0000-000000000002"
HEAD = f"{201:040d}"


@pytest.fixture
def lifecycle(stubs, tmp_path, queue_dir):
    talk = talk_on(stubs, tmp_path, queue_dir, vetted=False)
    worker_path = stubs.root / "sessions" / f"{WORKER}.json"
    worker = json.loads(worker_path.read_text(encoding="utf-8"))
    worker["worktrees"] = []
    write(worker_path, json.dumps(worker))
    ok(q("collect"))
    stubs.tool("thurbox-cli", THURBOX.replace(
        'print(json.dumps({"output": ""}))',
        'print(json.dumps({"output": "› ", "cursor_row": 0, "cursor_col": 2}))',
    ))
    # The reviewer is a durable task whose result may already exist. Review
    # responsibility follows its live session, not whether its turn finished.
    ok(q("add", talk.topic, "review", "--title", "Review the repository",
         "--repo", str(tmp_path / "talk-repo"), "--branch", "review/all",
         "--number", "02", "--publish", "none"))
    talk.shep.session(REVIEWER, "idle")
    ok(q("attach", f"{talk.topic}/02-review", REVIEWER))
    settings = Path(os.environ["FLEET_PUBLISH_ROOT"]) / "orchestration" / "publish.conf"
    write(settings, "METHOD=pr\nREVIEW_REPOS=github.com/Thurbeen/fleet\n"
          f"REVIEWER_TASK={talk.topic}/02-review\nREVIEW_SCORE=5\n")
    return talk


def pass_(talk):
    return json.loads(ok(q("shepherd", "--topic", talk.topic, "--no-merge", "--json")).stdout)["prs"]


def record(talk, score=5, findings=0, sid=REVIEWER, head=HEAD):
    talk.comment("501", "fleet-account", f"Review of {head}: {score}/5; findings {findings}.",
                 "2026-10-01T00:00:00Z")
    return q("review", "record", PR, "--head", head, "--score", str(score),
             "--findings", str(findings), "--evidence", PR + "#issuecomment-501",
             THURBOX_SESSION=sid)


def test_publication_waits_and_ci_transition_wakes_resting_reviewer(lifecycle):
    t = lifecycle
    t.shep.update(201, statusCheckRollup=[{"__typename": "CheckRun", "name": "CI",
                                         "status": "IN_PROGRESS", "conclusion": ""}])
    row = pass_(t)[0]
    assert row["review"]["state"] == "pending"
    assert not t.sends(REVIEWER)
    t.shep.update(201, statusCheckRollup=[{"__typename": "CheckRun", "name": "CI",
                                         "status": "COMPLETED", "conclusion": "SUCCESS"}])
    row = pass_(t)[0]
    assert row["condition"] == "review-pending"
    assert len(t.sends(REVIEWER)) == 1
    pass_(t)
    assert len(t.sends(REVIEWER)) == 1
    assert t.task()["state"] != "landed"
    assert "review-pending" in q("plan", "--json").stdout


def test_new_pr_and_head_are_new_obligations(lifecycle):
    t = lifecycle
    pass_(t)
    t.shep.pr(202, headRefName="fix/new", body="Published.")
    pass_(t)
    assert len(t.sends(REVIEWER)) == 2
    ok(record(t))
    assert pass_(t)[0]["review"]["state"] == "clean"
    pass_(t)
    assert len(t.sends(REVIEWER)) == 2
    t.shep.update(201, headRefOid="a" * 40)
    assert pass_(t)[0]["review"]["state"] == "pending"
    assert len(t.sends(REVIEWER)) == 3


def test_missing_reviewer_stays_pending_and_replacement_is_not_deduplicated(lifecycle):
    t = lifecycle
    pass_(t)
    (t.shep.stubs.root / "sessions" / f"{REVIEWER}.json").unlink()
    row = pass_(t)[0]
    assert row["review"]["state"] == "pending"
    assert row["review"]["action"] == "needs-lead"
    plan = json.loads(ok(q("plan", "--json")).stdout)
    assert plan["reviews"]
    replacement = "c0ffee00-0000-0000-0000-000000000003"
    t.shep.session(replacement, "idle")
    ok(q("attach", f"{t.topic}/02-review", replacement))
    pass_(t)
    assert len(t.sends(replacement)) == 1
    assert not t.shep.creates()


def test_findings_reach_resting_fixer_and_fix_needs_independent_reassessment(lifecycle):
    t = lifecycle
    pass_(t)
    ok(record(t, score=4, findings=1))
    row = pass_(t)[0]
    assert row["review"]["state"] == "findings"
    assert len(t.sends(WORKER)) == 1
    assert not t.shep.creates()
    pass_(t)
    assert len(t.sends(WORKER)) == 1
    t.shep.update(201, headRefOid="b" * 40)
    row = pass_(t)[0]
    assert row["review"]["state"] == "pending"
    assert len(t.sends(REVIEWER)) == 2
    assert not record(t, sid=WORKER, head="b" * 40).code == 0
    ok(record(t, head="b" * 40))
    assert pass_(t)[0]["review"]["state"] == "clean"


def test_clean_review_uses_existing_merge_gates_and_merge_cleans_obligation(lifecycle):
    t = lifecycle
    pass_(t)
    ok(record(t))
    t.shep.files(201, "scripts/lib/example.py")
    # REVIEW clean supplements the existing allowlist, author, CI, threads,
    # mergeability and human gates; it is never a parallel merger.
    out = ok(q("shepherd", "--topic", t.topic, "--json")).stdout
    assert 201 in t.shep.merged(), out
    collected = ok(q("collect")).out
    pass_(t)
    assert json.loads(ok(q("plan", "--json")).stdout)["reviews"] == []
    assert not t.sends(WORKER)
    assert t.shep.stubs.calls("thurbox-cli", f"session delete {WORKER}"), collected
    assert not t.task().get("session")
    git("status", "--short", cwd=Path(t.task()["repo"]))


def test_reviewer_cannot_attest_self_or_stale_head(lifecycle):
    t = lifecycle
    expect(record(t, sid=WORKER).out, "configured reviewer")
    t.shep.update(201, headRefOid="c" * 40)
    expect(record(t).out, "current head")


def test_finished_reviewer_result_retains_its_responsibility(lifecycle, queue_dir):
    t = lifecycle
    write(queue_dir / t.topic / "02-review" / "result.md",
          "---\noutcome: shipped\n---\nFinished this pass; CI still pending.\n")
    collected = ok(q("collect")).out
    expect(collected, "configured reviewer remains responsible")
    assert not t.shep.stubs.calls("thurbox-cli", f"session delete {REVIEWER}")
    t.shep.pr(202, headRefName="fix/later", body="Later publication.")
    pass_(t)
    assert len(t.sends(REVIEWER)) == 2


def test_busy_reviewer_gets_one_mailbox_note_then_a_safe_wake(lifecycle):
    t = lifecycle
    t.shep.session(REVIEWER, "working")
    pass_(t)
    pass_(t)
    notes = t.shep.stubs.calls("thurbox-cli", "message send")
    assert len(notes) == 1
    expect(notes[0], REVIEWER, "--no-wake", "--kind fleet-review", PR, HEAD)
    assert not t.sends(REVIEWER)
    t.shep.session(REVIEWER, "done")
    pass_(t)
    assert len(t.sends(REVIEWER)) == 1
    pass_(t)
    assert len(t.sends(REVIEWER)) == 1


def test_same_head_feedback_and_unreadable_sources_cannot_stay_clean(lifecycle):
    t = lifecycle
    ok(record(t))
    assert pass_(t)[0]["review"]["state"] == "clean"
    t.comment("502", "another-reviewer", "One more caller fails.", "2026-10-01T01:00:00Z")
    assert pass_(t)[0]["review"]["state"] == "pending"
    # Reading changed feedback precedes recording the reassessment.
    ok(record(t))
    assert pass_(t)[0]["review"]["state"] == "clean"
    out = ok(q("shepherd", "--json", SHEP_GH_DOWN="1")).stdout
    assert json.loads(out)["unreadable"]
    assert json.loads(ok(q("plan", "--json")).stdout)["reviews"]
    assert pass_(t)[0]["review"]["state"] == "clean"
    assert json.loads(ok(q("plan", "--json")).stdout)["reviews"] == []


def test_unresolved_threads_and_pending_ci_block_clean_assessment(lifecycle):
    t = lifecycle
    t.thread("T1", "example.py", 1, ("601", "another-reviewer", "Fix this caller.",
                                    "2026-10-01T00:00:00Z"))
    expect(record(t).out, "unresolved review thread")
    t.said["threads"] = []
    t.save()
    t.shep.update(201, statusCheckRollup=[])
    expect(record(t).out, "no check has reported")


def test_delivery_failure_retries_without_spawning(lifecycle):
    t = lifecycle
    original = (t.shep.stubs.root / "scripts" / "thurbox-cli.py").read_text(encoding="utf-8")
    t.shep.stubs.tool("thurbox-cli", "import sys\n"
                     "if sys.argv[1:3] == ['session', 'send']: raise SystemExit(1)\n" + original)
    row = pass_(t)[0]
    assert row["review"]["action"] == "needs-lead"
    assert len(t.sends(REVIEWER)) == 1
    t.shep.stubs.tool("thurbox-cli", original)
    pass_(t)
    assert len(t.sends(REVIEWER)) == 2
    pass_(t)
    assert len(t.sends(REVIEWER)) == 2
    assert not t.shep.creates()


def test_missing_reviewer_obligation_reaches_real_lead_notification(lifecycle, tmp_path):
    from harness import PYTHON, REPO, run

    t = lifecycle
    (t.shep.stubs.root / "sessions" / f"{REVIEWER}.json").unlink()
    lead = "c0ffee00-0000-0000-0000-000000000004"
    write(t.shep.stubs.root / "sessions" / f"{lead}.json",
          json.dumps({"id": lead, "name": "Review Control", "state": "idle"}))
    pass_(t)
    plan = ok(q("plan", "--json")).stdout
    # Suppress unrelated intake rows so the integration proves reviews alone
    # are a reason to wake; review rows themselves are the real plan output.
    doc = json.loads(plan)
    doc.update(ready=[], stalled=[], unprompted=[])
    (tmp_path / "notified").mkdir()
    for _ in range(2):
        ok(run([*PYTHON, str(REPO / "scripts/lib/notify_lead.py"), "--state-dir", str(tmp_path / "notified")],
               stdin=json.dumps(doc), FLEET_LEAD_SESSION="Review Control"))
    calls = t.shep.stubs.calls("thurbox-cli", f"session send {lead}")
    assert len(calls) == 1
    expect(calls[0], "independent review", "uv run fleet queue plan --json", "restore")
    assert not t.shep.creates()


def test_dry_run_keeps_obligation_and_notification_records_unchanged(lifecycle):
    t = lifecycle
    pass_(t)
    store = Path(os.environ["FLEET_SHEPHERD_DIR"]) / "reviews"
    before = {p.name: p.read_bytes() for p in store.glob("*.json")}
    calls = t.shep.tbx_log()
    t.shep.pr(202, headRefName="fix/next", body="Next.")
    ok(q("shepherd", "--topic", t.topic, "--dry-run"))
    assert {p.name: p.read_bytes() for p in store.glob("*.json")} == before
    assert len(t.sends(REVIEWER)) == 1
    assert calls.count("session send") == t.shep.tbx_log().count("session send")


def test_final_merge_recheck_refuses_unreadable_head_and_preserves_allowlist(lifecycle):
    t = lifecycle
    ok(record(t))
    t.shep.files(201, "example.py")
    out = ok(q("shepherd", "--topic", t.topic, SHEP_VIEW_DOWN="201")).out
    expect(out, "not-merged", "could not be re-read")
    assert not t.shep.merged()
    out = ok(q("shepherd", "--topic", t.topic, FLEET_AUTO_MERGE_REPOS="github.com/other/project")).out
    expect(out, "fleet does not merge")
    assert not t.shep.merged()


def test_missing_linked_evidence_and_feedback_outage_are_not_assessments(lifecycle):
    t = lifecycle
    expect(q("review", "record", PR, "--head", HEAD, "--score", "5", "--findings", "0",
             "--evidence", PR + "#issuecomment-999", THURBOX_SESSION=REVIEWER).out,
           "linked forge review summary")
    original = (t.shep.stubs.root / "scripts" / "gh.py").read_text(encoding="utf-8")
    ok(record(t))
    t.shep.stubs.tool("gh", "import sys\n"
                     "if sys.argv[1:3] == ['api', 'graphql'] and any('reviews(' in a for a in sys.argv): "
                     "raise SystemExit(1)\n" + original)
    row = pass_(t)[0]
    assert row["review"]["state"] == "pending"
    assert row["review"]["action"] == "needs-lead"
    assert not t.shep.merged()


def test_default_has_no_review_requirement_and_invalid_opt_in_fails_closed(lifecycle):
    t = lifecycle
    settings = Path(os.environ["FLEET_PUBLISH_ROOT"]) / "orchestration" / "publish.conf"
    write(settings, "METHOD=pr\nREVIEW_REPOS=\n")
    assert "review" not in pass_(t)[0]
    assert not t.sends(REVIEWER)
    write(settings, f"METHOD=pr\nREVIEW_REPOS=Thurbeen/fleet\nREVIEWER_TASK={t.topic}/02-review\nREVIEW_SCORE=5\n")
    row = pass_(t)[0]
    assert row["condition"] == "review-pending"
    assert row["review"]["action"] == "needs-lead"
    assert not t.shep.merged()


def test_gitlab_independent_review_goes_through_the_forge_seam(lifecycle, tmp_path, monkeypatch):
    from kit_forges import FAKE_GLAB, GlabStore, tripwire

    t = lifecycle
    store = GlabStore(tmp_path / "gitlab")
    t.shep.stubs.tool("glab", FAKE_GLAB)
    t.shep.stubs.tool("gh", tripwire("a GitLab review must never go through gh"))
    monkeypatch.setenv("FAKE_GLAB_DIR", str(store.root))
    monkeypatch.setenv("GITLAB_HOST", "gitlab.example.com")
    repo = Path(t.task()["repo"])
    git("remote", "add", "origin", "https://gitlab.example.com/acme/group/widgets.git", cwd=repo)
    # Use a new task with no GitHub artifact, so repository discovery does not
    # ask the tripwire about an old artifact from a different forge.
    topic = ok(q("topic", "add", "gl-review", "--title", "Independent review on GitLab", "--prompt", "review")).stdout.strip()
    ok(q("add", topic, "change", "--title", "Change", "--repo", str(repo), "--branch", "fix/gitlab",
         "--number", "01", "--publish", "pr"))
    git("branch", "fix/gitlab", cwd=repo)
    store.mr(401, source_branch="fix/gitlab", description="Published.")
    url = "https://gitlab.example.com/acme/group/widgets/-/merge_requests/401"
    head = f"{401:040d}"
    settings = Path(os.environ["FLEET_PUBLISH_ROOT"]) / "orchestration" / "publish.conf"
    write(settings, "METHOD=pr\nREVIEW_REPOS=gitlab.example.com/acme/group/widgets\n"
          f"REVIEWER_TASK={t.topic}/02-review\nREVIEW_SCORE=5\n")
    rows = json.loads(ok(q("shepherd", "--topic", topic, "--no-merge", "--json")).stdout)["prs"]
    assert rows[0]["condition"] == "review-pending"
    assert len(t.sends(REVIEWER)) == 1
    store.api("discussions", [{"id": "d1", "notes": [{"id": 11, "body": f"Reviewed {head}: 5/5.",
                                                       "system": False, "resolvable": False,
                                                       "author": {"username": "reviewer"},
                                                       "created_at": "2026-10-01T10:00:00.000Z"}]}])
    ok(q("review", "record", url, "--head", head, "--score", "5", "--findings", "0",
         "--evidence", url + "#note_11", THURBOX_SESSION=REVIEWER))
    rows = json.loads(ok(q("shepherd", "--topic", topic, "--no-merge", "--json")).stdout)["prs"]
    assert rows[0]["review"]["state"] == "clean"
    assert not t.shep.stubs.calls("gh")[-1].endswith("gh: a GitLab review must never go through gh")


def test_explicitly_retired_reviewer_is_not_woken(lifecycle):
    t = lifecycle
    ok(q("abandon", f"{t.topic}/02-review", "--why", "reviewer retired", "--force"))
    row = pass_(t)[0]
    assert row["review"]["action"] == "needs-lead"
    assert not t.sends(REVIEWER)


def test_stopped_flag_and_condition_blocker_prevent_review_wakes(lifecycle):
    t = lifecycle
    reviewer_path = t.shep.stubs.root / "sessions" / f"{REVIEWER}.json"
    doc = json.loads(reviewer_path.read_text(encoding="utf-8"))
    doc["stopped"] = True
    write(reviewer_path, json.dumps(doc))
    row = pass_(t)[0]
    assert row["review"]["action"] == "needs-lead"
    assert not t.sends(REVIEWER)
    doc.pop("stopped")
    write(reviewer_path, json.dumps(doc))
    ok(q("block", f"{t.topic}/02-review", "--condition", "reviewer on hold",
         "--kind", "awaiting-approval", "--why", "operator must release the review"))
    row = pass_(t)[0]
    assert row["review"]["action"] == "needs-lead"
    assert not t.sends(REVIEWER)


def test_latest_recipient_state_cannot_wake_a_turn_that_just_started(lifecycle):
    t = lifecycle
    original = (t.shep.stubs.root / "scripts" / "thurbox-cli.py").read_text(encoding="utf-8")
    t.shep.stubs.tool("thurbox-cli", '''import json, os, sys
from pathlib import Path
if sys.argv[1:3] == ["session", "get"] and sys.argv[3] == "''' + REVIEWER + '''":
    counter = Path(os.environ["FLEET_STUB_ROOT"]) / "reviewer-gets"
    count = int(counter.read_text(encoding="utf-8")) + 1 if counter.exists() else 1
    counter.write_text(str(count), encoding="utf-8")
    print(json.dumps({"id": "''' + REVIEWER + '''", "state": "idle" if count == 1 else "working"}))
    raise SystemExit(0)
''' + original)
    pass_(t)
    assert not t.sends(REVIEWER)
    notes = t.shep.stubs.calls("thurbox-cli", "message send")
    assert len(notes) == 1
    expect(notes[0], "--no-wake", REVIEWER)


def test_repository_case_does_not_duplicate_watches_or_hide_a_forge_absence(lifecycle, tmp_path):
    t = lifecycle
    original = (t.shep.stubs.root / "scripts" / "gh.py").read_text(encoding="utf-8")
    # GitHub resolves this case variation to the same canonical repository.
    t.shep.stubs.tool("gh", original.replace("== repo:", ".casefold() == repo.casefold():"))
    settings = Path(os.environ["FLEET_PUBLISH_ROOT"]) / "orchestration" / "publish.conf"
    write(settings, "METHOD=pr\nREVIEW_REPOS=github.com/thurbeen/fleet\n"
          f"REVIEWER_TASK={t.topic}/02-review\nREVIEW_SCORE=5\n")
    ok(record(t))
    rows = json.loads(ok(q("shepherd", "--no-merge", "--json")).stdout)["prs"]
    assert len(rows) == 1
    assert rows[0]["review"]["state"] == "clean"
    empty = tmp_path / "no-tools"
    empty.mkdir()
    out = ok(q("shepherd", "--json", PATH=str(empty))).stdout
    assert json.loads(out)["unavailable"]
    assert json.loads(ok(q("plan", "--json")).stdout)["reviews"]
