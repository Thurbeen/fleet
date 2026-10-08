"""§15, READING THE FORGE NOW: `queue list --live` and `fleet watch`.

Both exist because the leads did this by hand and got it wrong. A status table
built from the records listed change requests that had already merged as still
waiting on the operator, again and again; and every lead wrote its own pipeline
poller out of `gh`/`glab` calls and `sleep`s, which broke on emoji job names,
on shell quoting, and on a flag one of them got wrong — and then printed
merges that never happened.

So both go through the forge seam and nothing else. The fake forge from §13
answers here with `gh` as a tripwire, which is the proof that neither reaches
around `scripts/lib/forge.py`; the GitHub and GitLab adapters' own parsing of
the new questions is pinned at the end, over a scripted `gh` and `glab`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kit_forges import FAKE_GLAB, FakeForgeStore, GlabStore, tripwire
from queuekit import ok, result, result_artifacts

from harness import Run, expect, git, lib, refute, run_fleet
from harness import run_queue as q

MR = "https://forge.test:8443/acme/widgets/-/merge_requests/"
PIPE = "https://forge.test:8443/acme/widgets/-/pipelines/"
TOPIC = "read-the-forge"


class World:
    def __init__(self, root: Path, store: FakeForgeStore, repo: Path):
        self.root, self.store, self.repo = root, store, repo
        self.queue = root / "queue-live"

    def env(self, **extra: str | None) -> dict:
        return {"FLEET_QUEUE_DIR": str(self.queue), "FLEET_FORGE_PLUGINS": str(self.store.plugin),
                "FAKE_FORGE_DIR": str(self.store.root), **extra}

    def q(self, *args: str, **env: str | None) -> Run:
        return q(*args, **self.env(**env))

    def watch(self, *args: str, **env: str | None) -> Run:
        return run_fleet("watch", *args, "--interval", "0", **self.env(**env))

    def task(self, tid: str) -> Path:
        return self.queue / TOPIC / tid


@pytest.fixture
def world(tmp_path, stubs) -> World:
    stubs.tool("gh", tripwire("gh: nothing in this section may reach GitHub"))
    store = FakeForgeStore(tmp_path / "fake-forge")
    repo = tmp_path / "fake-forge" / "repo"
    git("init", "-q", "-b", "main", str(repo))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=repo)
    git("remote", "add", "origin", "https://forge.test:8443/acme/widgets.git", cwd=repo)
    w = World(tmp_path, store, repo)
    ok(w.q("topic", "add", TOPIC, "--title", "Read the forge, not the record", "--prompt", "say what is true now"))
    return w


def shipped(w: World, n: str, slug: str, number: int) -> None:
    ok(w.q("add", TOPIC, slug, "--title", f"Change {slug}", "--repo", str(w.repo),
           "--branch", f"fix/{slug}", "--number", n))
    result(w.task(f"{n}-{slug}"), "shipped", "Shipped it.", f"{MR}{number}")


def no_gh(stubs) -> None:
    assert stubs.calls("gh") == [], "a code path ran `gh` while a different forge was configured"


# --- §15a: list --live -------------------------------------------------------


def test_list_live_says_what_the_forge_says_and_where_the_record_lags(world, stubs):
    """15a. The record says `done`; the forge says merged. The plain view is the
    record and keeps saying so; `--live` is the forge, and it names the lag."""
    for n, slug, number in (("01", "merged-since", 301), ("02", "red-and-argued", 302),
                            ("03", "unreadable", 303), ("04", "threads-unreadable", 304)):
        shipped(world, n, slug, number)
        world.store.cr(number, head_branch=f"fix/{slug}")
    ok(world.q("collect"))
    # Then, behind the records' back: one merges, one goes red and grows threads.
    world.store.cr(301, head_branch="fix/merged-since", state="merged")
    world.store.cr(302, head_branch="fix/red-and-argued", checks=[["gate", "failed"], ["lint", "pending"]],
                   threads=2, mergeable="conflicting")
    (world.store.root / "crs" / "303.json").unlink()
    world.store.cr(304, head_branch="fix/threads-unreadable", threads="down")

    plain = world.q("list").out
    refute(plain, "merged on the forge")

    live = world.q("list", "--live").out
    expect(live, "acme/widgets#301", "merged on the forge", "the record has not caught up")
    expect(live, "acme/widgets#302", "open", "1 failed", "1 pending", "2 unresolved threads", "conflicting")
    expect(live, "acme/widgets#303", "could not be read", "no change request 303")
    # A thread count nobody could read is said, never left out to read as zero.
    expect(live, "acme/widgets#304", "threads not read: the threads are unreachable")
    # One closing line that a "waiting on you" table can be built from.
    expect(live, "live: 4 change request(s) read: 1 merged, 2 open, 1 unreadable")
    no_gh(stubs)


def test_list_live_reads_nothing_for_a_finished_task(world, stubs):
    """15b. A landed task is history: `--live` asks the forge only about work
    that is still open, so a long queue does not cost a call per old task."""
    shipped(world, "01", "long-gone", 311)
    world.store.cr(311, head_branch="fix/long-gone", state="merged")
    # Not started yet, and naming no change request: it keeps the topic in view.
    ok(world.q("add", TOPIC, "not-yet", "--title", "Not started", "--repo", str(world.repo),
               "--branch", "fix/not-yet", "--number", "02"))
    ok(world.q("collect"))  # lands it
    out = world.q("list", "--live").out
    expect(out, "landed", "live: 0 change request(s) read")
    refute(out, "merged on the forge")


# --- §15b: fleet watch -------------------------------------------------------


def test_watch_a_pipeline_prints_one_line_per_change_and_ends_on_its_verdict(world, stubs):
    """15c. Job names are data and never parsed: an emoji, a space and a slash
    survive whole. A job that did not change prints nothing."""
    world.store.pipeline(
        77,
        {"verdict": "pending", "jobs": [["🧪 test ✨", "pending"], ["deploy / prod", "pending"]]},
        {"verdict": "pending", "jobs": [["🧪 test ✨", "passed"], ["deploy / prod", "pending"]]},
        {"verdict": "pending", "jobs": [["🧪 test ✨", "passed"], ["deploy / prod", "pending"]]},
        {"verdict": "passed", "jobs": [["🧪 test ✨", "passed"], ["deploy / prod", "passed"]]},
    )
    run = world.watch(f"{PIPE}77")
    assert run.code == 0, run.out
    lines = run.stdout.splitlines()
    assert [ln for ln in lines if "🧪 test ✨" in ln and "passed" in ln], run.out
    # pending once, passed once: four reads, two changes for this job.
    assert len([ln for ln in lines if "🧪 test ✨" in ln]) == 2, run.out
    assert len([ln for ln in lines if "deploy / prod" in ln]) == 2, run.out
    expect(lines[-1], "watch:", "passed")
    no_gh(stubs)


def test_watch_a_failed_pipeline_exits_non_zero_and_names_the_job(world, stubs):
    """15d."""
    world.store.pipeline(78, {"verdict": "failed", "jobs": [["plan", "passed"], ["apply 🚀", "failed"]]})
    run = world.watch(f"{PIPE}78")
    assert run.code == 1, run.out
    expect(run.stdout.splitlines()[-1], "failed", "apply 🚀")


def test_watch_gives_up_at_its_timeout_and_says_what_it_last_saw(world, stubs):
    """15e. A timeout is its own exit code, never a verdict about the pipeline,
    and an unreadable read is reported and polled again rather than ending it."""
    world.store.pipeline(79, {"verdict": "pending", "jobs": [["slow", "pending"]]})
    run = world.watch(f"{PIPE}79", "--timeout", "0")
    assert run.code == 124, run.out
    expect(run.stdout.splitlines()[-1], "timed out", "pending")

    run = world.watch(f"{PIPE}404", "--timeout", "0")
    assert run.code == 124, run.out
    expect(run.out, "could not read", "no pipeline 404")


def test_watch_refuses_what_it_cannot_name(world, stubs):
    """15f."""
    run = world.watch("https://example.invalid/not/a/pipeline")
    assert run.code == 2, run.out
    expect(run.out, "not a pipeline, change request or task")


def test_watch_a_task_waits_for_its_change_requests_checks(world, stubs):
    """15g. A task ref names its change requests through its record; the watch
    ends when none of their checks is still running."""
    shipped(world, "01", "checks-run", 321)
    # `collect` puts the artifact on the record, and reads the change request once.
    world.store.cr(321, head_branch="fix/checks-run", checks=[["gate", "pending"]],
                   seq=[{}, {"checks": [["gate", "passed"]]}])
    ok(world.q("collect"))
    world.store.rewind()
    run = world.watch(f"{TOPIC}/01-checks-run")
    assert run.code == 0, run.out
    expect(run.out, "acme/widgets#321", "1 pending", "1 passed")
    expect(run.stdout.splitlines()[-1], "watch:", "checks passed")


def test_watch_follow_goes_from_the_merge_to_the_main_pipeline_and_its_apply(world, stubs):
    """15h. `--follow`: wait for the merge, find the pipeline its merge commit
    started on the base branch, watch that, then print what the apply said."""
    world.store.cr(331, head_branch="fix/infra", checks=[["gate", "passed"]],
                   seq=[{}, {"state": "merged", "merge_sha": "c0ffee"}])
    world.store.commit_pipelines("c0ffee", 90)
    world.store.pipeline(
        90,
        {"verdict": "pending", "jobs": [["plan", "passed"], ["🌍 apply", "pending"]]},
        {"verdict": "passed", "jobs": [["plan", "passed"], ["🌍 apply", "passed"]]},
    )
    world.store.job_log(90, 2, "Refreshing state...\n"
                        "\x1b[32mApply complete! Resources: 1 added, 0 changed, 0 destroyed.\x1b[0m\n")
    run = world.watch(f"{MR}331", "--follow")
    assert run.code == 0, run.out
    expect(run.out, "merged", "pipelines/90", "🌍 apply",
           "Apply complete! Resources: 1 added, 0 changed, 0 destroyed.")
    refute(run.out, "\x1b[")
    expect(run.stdout.splitlines()[-1], "watch:", "passed")


def test_watch_a_change_request_that_closes_unmerged_is_a_failure(world, stubs):
    """15i."""
    world.store.cr(341, head_branch="fix/dropped", seq=[{}, {"state": "closed"}])
    run = world.watch(f"{MR}341", "--until", "merged")
    assert run.code == 1, run.out
    expect(run.stdout.splitlines()[-1], "closed")


# --- §15c: the two real adapters --------------------------------------------

GH_RUNS = r'''
import json, sys
args = sys.argv[1:]
log = __import__("os").environ["FLEET_STUB_ROOT"] + "/gh-watch.log"
open(log, "a", encoding="utf-8").write(" ".join(args) + "\n")
if args[:2] == ["run", "view"] and "--log" in args:
    print("apply\tTerraform apply\t2026-10-01T00:00:00Z Apply complete! Resources: 0 added, 2 changed, 0 destroyed.")
elif args[:2] == ["run", "view"]:
    print(json.dumps({"status": "completed", "conclusion": "failure", "headSha": "abc", "headBranch": "main",
                      "url": "https://github.com/acme/widgets/actions/runs/5", "attempt": 2, "jobs": [
        {"databaseId": 11, "name": "✅ lint", "status": "completed", "conclusion": "success"},
        {"databaseId": 12, "name": "apply", "status": "completed", "conclusion": "failure"},
        {"databaseId": 13, "name": "notify", "status": "in_progress", "conclusion": ""}]}))
elif args[:2] == ["run", "list"]:
    print(json.dumps([{"databaseId": 5, "url": "https://github.com/acme/widgets/actions/runs/5"}]))
elif args[:2] == ["api", "graphql"]:
    print(json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": False},
        "nodes": [{"isResolved": False}, {"isResolved": True}, {"isResolved": False}]}}}}}))
else:
    sys.stderr.write("gh: not taught %r\n" % args)
    raise SystemExit(1)
'''


@pytest.fixture
def forge_mod(monkeypatch):
    monkeypatch.delenv("GH_HOST", raising=False)
    monkeypatch.delenv("GITLAB_HOST", raising=False)
    mod = lib("forge.py")
    mod.reset()
    yield mod
    mod.reset()


def test_github_answers_the_new_questions_through_gh(forge_mod, stubs):
    """15j. `gh run view` answers lowercase conclusions; a job still running is
    pending whatever its empty conclusion says."""
    stubs.tool("gh", GH_RUNS)
    gh = forge_mod.GitHubForge()
    ref = gh.parse_pipeline_url("https://github.com/acme/widgets/actions/runs/5/attempts/2")
    assert ref == forge_mod.PipelineRef(forge_mod.RepoId("github.com", "acme/widgets"), 5,
                                        "https://github.com/acme/widgets/actions/runs/5")
    assert gh.parse_pipeline_url("https://github.com/acme/widgets/pull/5") is None

    p, why = gh.pipeline(ref)
    assert not why, why
    assert p.verdict == "failed" and p.sha == "abc"
    assert [(j.name, j.verdict) for j in p.jobs] == [("✅ lint", "passed"), ("apply", "failed"),
                                                      ("notify", "pending")]

    found, why = gh.pipelines_for_commit(ref.repo, "abc")
    assert not why and [r.id for r in found] == [5]
    text, why = gh.job_log(ref, p.jobs[1])
    assert "Apply complete!" in text, why

    cr = forge_mod.ChangeRef(ref.repo, 9, "https://github.com/acme/widgets/pull/9")
    assert gh.threads(cr) == (2, "")
    calls = (Path(stubs.root) / "gh-watch.log").read_text(encoding="utf-8")
    # The host travels with every call, so GitHub Enterprise is asked and not github.com.
    assert "-R github.com/acme/widgets" in calls and "--hostname github.com" in calls, calls
    # Strings go as strings: `-F` would send a repository named `2048` as a number.
    assert "-f owner=acme" in calls and "-f name=widgets" in calls, calls


@pytest.fixture
def glab(tmp_path, stubs, monkeypatch) -> GlabStore:
    store = GlabStore(tmp_path / "gitlab")
    stubs.tool("glab", FAKE_GLAB)
    monkeypatch.setenv("FAKE_GLAB_DIR", str(store.root))
    return store


def test_gitlab_answers_the_new_questions_through_glab(forge_mod, glab):
    """15k. GitLab's own words: `canceled` with one `l`, `manual` is waiting for a
    person, a resolvable note left unresolved is a thread, and the commit a
    squash merge puts on the base branch is the one its pipeline runs for."""
    glab.api("pipeline", {"id": 6, "status": "manual", "sha": "def", "ref": "main",
                          "web_url": "https://gitlab.example.com/acme/group/widgets/-/pipelines/6"})
    glab.api("jobs", [{"id": 21, "name": "🔍 plan", "status": "success"},
                      {"id": 22, "name": "apply", "status": "manual"},
                      {"id": 23, "name": "old", "status": "canceled"}])
    glab.api("pipelines", [{"id": 6, "web_url": "https://gitlab.example.com/acme/group/widgets/-/pipelines/6"}])
    glab.api("discussions", [
        {"notes": [{"resolvable": True, "resolved": False}]},
        {"notes": [{"resolvable": True, "resolved": True}]},
        {"notes": [{"resolvable": False, "resolved": False}]},
    ])
    gl = forge_mod.GitLabForge(hosts=["gitlab.example.com"])
    ref = gl.parse_pipeline_url("https://gitlab.example.com/acme/group/widgets/-/pipelines/6")
    assert ref is not None and ref.repo.path == "acme/group/widgets" and ref.id == 6

    p, why = gl.pipeline(ref)
    assert not why, why
    assert p.verdict == "manual"
    assert [(j.name, j.verdict) for j in p.jobs] == [("🔍 plan", "passed"), ("apply", "manual"),
                                                      ("old", "cancelled")]
    found, why = gl.pipelines_for_commit(ref.repo, "def")
    assert not why and [r.id for r in found] == [6]
    cr_ref = forge_mod.ChangeRef(ref.repo, 4, "https://gitlab.example.com/acme/group/widgets/-/merge_requests/4")
    assert gl.threads(cr_ref) == (1, "")

    glab.mr(4, state="merged", merge_commit_sha=None, squash_commit_sha="5" * 40)
    cr, why = gl.get(cr_ref)
    assert not why and cr.merge_sha == "5" * 40
    # GitLab's default already leaves retried jobs out, so only the latest attempt is read.
    assert "include_retried" not in glab.calls()


def test_watch_follow_says_when_the_merge_started_no_pipeline_yet(world, stubs):
    """15l. A repository with no base-branch pipeline is not a hang with nothing
    on screen: the watch says what it is waiting for, then times out."""
    world.store.cr(351, head_branch="fix/no-ci", state="merged", merge_sha="beef")
    run = world.watch(f"{MR}351", "--follow", "--timeout", "0")
    assert run.code == 124, run.out
    expect(run.out, "no pipeline on beef yet")


def test_watch_a_task_across_repositories_reports_a_verdict_over_a_timeout(world, stubs, tmp_path):
    """15m. One change request closed unmerged while the other is still running at
    the deadline: the close is a verdict and the timeout is not, so the close wins."""
    other = tmp_path / "fake-forge" / "gadgets"
    git("init", "-q", "-b", "main", str(other))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=other)
    git("remote", "add", "origin", "https://forge.test:8443/acme/gadgets.git", cwd=other)
    ok(world.q("add", TOPIC, "two-repos", "--title", "Change two repositories", "--repo", str(world.repo),
               "--add-repo", str(other), "--branch", "fix/two-repos", "--number", "01"))
    gadgets = "https://forge.test:8443/acme/gadgets/-/merge_requests/"
    result_artifacts(world.task("01-two-repos"), "shipped", "Shipped both.",
                     {str(world.repo): f"{MR}361", str(other): f"{gadgets}362"})
    world.store.cr(361, head_branch="fix/two-repos", state="closed")
    world.store.cr(362, repo="acme/gadgets", head_branch="fix/two-repos", checks=[["gate", "pending"]])
    ok(world.q("collect"))
    run = world.watch(f"{TOPIC}/01-two-repos", "--timeout", "0")
    assert run.code == 1, run.out
    expect(run.stdout.splitlines()[-1], "closed")


# A `gh` that answers ONLY the `--json` fields it was asked for, the way gh does:
# a field the adapter forgot to request is a field that never arrives.
GH_ONE_PR = r'''
import json, sys
args = sys.argv[1:]
FULL = {"number": 9, "url": "https://github.com/acme/widgets/pull/9", "title": "change 9", "state": "OPEN",
        "isDraft": True, "mergeable": "CONFLICTING", "reviewDecision": "CHANGES_REQUESTED",
        "body": "", "headRefName": "fix/x", "baseRefName": "main", "headRefOid": "9" * 40, "commits": [],
        "mergeCommit": None,
        "statusCheckRollup": [{"__typename": "CheckRun", "name": "CI", "status": "COMPLETED",
                               "conclusion": "SUCCESS"}]}
if args[:2] == ["pr", "view"]:
    fields = args[args.index("--json") + 1].split(",")
    print(json.dumps({k: FULL[k] for k in fields if k in FULL}))
elif args[:2] == ["api", "graphql"]:
    print(json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": False}, "nodes": []}}}}}))
else:
    sys.stderr.write("gh: not taught %r\n" % args)
    raise SystemExit(1)
'''


def test_github_get_asks_gh_for_everything_a_live_reading_reports(forge_mod, stubs):
    """15n. `get` is what `list --live` and `watch` read, so the fields they print
    must be fields it requests: checks, draft, mergeable, review, base."""
    stubs.tool("gh", GH_ONE_PR)
    gh = forge_mod.GitHubForge()
    cr, why = gh.get(gh.parse_change_url("https://github.com/acme/widgets/pull/9"))
    assert not why, why
    assert [(c.name, c.verdict) for c in cr.checks] == [("CI", "passed")]
    assert (cr.draft, cr.mergeable, cr.review_decision, cr.base_branch) == (
        True, "conflicting", "changes-requested", "main")


def test_watch_a_green_github_pull_request_ends_on_its_checks(stubs):
    """15o. The case the fake forge could not catch: a real adapter that never
    asked for checks timed out on every green pull request."""
    stubs.tool("gh", GH_ONE_PR)
    run = run_fleet("watch", "https://github.com/acme/widgets/pull/9", "--interval", "0", "--timeout", "0",
                    GH_HOST=None)
    assert run.code == 0, run.out
    expect(run.out, "acme/widgets#9", "draft", "checks: 1 passed", "review: changes-requested")
    expect(run.stdout.splitlines()[-1], "checks passed")


def test_watch_stops_when_no_check_appears_within_its_grace(world, stubs):
    """15p. A change request with no CI has nothing to wait for: once `--grace`
    passes with no check reported, the watch says so and exits 3 — neither a
    pass nor a timeout."""
    world.store.cr(371, head_branch="fix/no-ci-here", checks=[])
    run = world.watch(f"{MR}371", "--grace", "0")
    assert run.code == 3, run.out
    expect(run.stdout.splitlines()[-1], "no check reported")
    # `--until merged` waits for the merge, not for checks, so the grace is not its rule.
    run = world.watch(f"{MR}371", "--until", "merged", "--grace", "0", "--timeout", "0")
    assert run.code == 124, run.out
