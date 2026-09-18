"""§24: one task, several repositories — the capability thurbox always had.

`session create` has taken `--add-dir` and `--add-repo` all along and the queue
reached for neither, so a task could only ever name one repository. Two halves,
and they are deliberately different weights:

    --add-dir    a directory attached as it is. No worktree, no branch, nothing
                 to publish — so nothing below `dispatch` has anything to say
                 about it. A worker reading a sibling repo or a docs tree.
    --add-repo   a second repository on the SAME branch, in its own worktree.
                 A worker can COMMIT there, and a commit fleet does not verify
                 is the exact failure verification exists to stop — so the
                 artifact model goes plural with it, and `collect`, `reap` and
                 `shepherd` each handle N.

What a single-repo task does is the regression that matters most, and it is
held by every other module in this directory staying green.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from kit_dispatch import ANSWERING_KEYS, next_session
from kit_forges import FAKE_GLAB, GlabStore
from queuekit import fill_brief, ok, result, result_artifacts

from harness import expect, git, refute
from harness import run_queue as q

DOCS = "/tmp/repo-docs"
SIBLING = "/tmp/repo-sibling"


@pytest.fixture
def queue_dir(isolated_env) -> Path:
    return Path(os.environ["FLEET_QUEUE_DIR"])


@pytest.fixture
def topic() -> str:
    return ok(q("topic", "add", "span-repos", "--title", "Span several repositories",
                "--prompt", "one task, several repos")).stdout.strip()


def record(queue_dir: Path, topic: str, tid: str) -> dict:
    return yaml.safe_load((queue_dir / topic / tid / "task.yaml").read_text(encoding="utf-8"))


def creates(stubs) -> list[str]:
    return [c for c in stubs.calls("thurbox-cli", "session create") if "--repo-path" in c]


# --- §24a: --add-dir, which stands alone ------------------------------------


def test_add_dir_is_recorded_repeatably_and_in_order(topic, queue_dir):
    """24a. Repeatable, and the operator's order is kept: thurbox attaches them
    in the order it is given them, and a reordering here would be fleet
    rewriting an instruction it does not own."""
    ok(q("add", topic, "reads-two-trees", "--title", "Reads two trees",
         "--repo", "/tmp/repo-a", "--branch", "fix/reads-two-trees", "--number", "01",
         "--add-dir", DOCS, "--add-dir", SIBLING))
    assert record(queue_dir, topic, "01-reads-two-trees")["add_dirs"] == [DOCS, SIBLING]


def test_a_task_with_no_add_dir_records_an_empty_list(topic, queue_dir):
    """24a. The field is always there and always a list, so every reader below
    is one code path and not two."""
    ok(q("add", topic, "one-repo", "--title", "One repo",
         "--repo", "/tmp/repo-a", "--branch", "fix/one-repo", "--number", "01"))
    assert record(queue_dir, topic, "01-one-repo")["add_dirs"] == []


def test_add_dir_reaches_session_create_verbatim(topic, queue_dir, stubs):
    """24a. Passed through as the operator wrote it. thurbox owns what a path
    there means, and fleet neither resolves nor validates one."""
    ok(q("add", topic, "reads-two-trees", "--title", "Reads two trees",
         "--repo", "/tmp/repo-a", "--branch", "fix/reads-two-trees", "--number", "01",
         "--add-dir", DOCS, "--add-dir", SIBLING))
    fill_brief(queue_dir / topic / "01-reads-two-trees" / "BRIEF.md")
    next_session(stubs, "dddddddd-0000-0000-0000-000000000001")
    stubs.session_is("dddddddd-0000-0000-0000-000000000001", "idle")
    stubs.tool("thurbox-cli", ANSWERING_KEYS)

    ok(q("dispatch"))
    create = creates(stubs)[0]
    assert f"--add-dir {DOCS} --add-dir {SIBLING}" in create, create


def test_a_single_repo_task_passes_no_add_dir_at_all(topic, queue_dir, stubs):
    """24a. The regression that matters: a task that names none spawns exactly
    the command it spawned before this existed."""
    ok(q("add", topic, "one-repo", "--title", "One repo",
         "--repo", "/tmp/repo-a", "--branch", "fix/one-repo", "--number", "01"))
    fill_brief(queue_dir / topic / "01-one-repo" / "BRIEF.md")
    next_session(stubs, "dddddddd-0000-0000-0000-000000000002")
    stubs.session_is("dddddddd-0000-0000-0000-000000000002", "idle")
    stubs.tool("thurbox-cli", ANSWERING_KEYS)

    ok(q("dispatch"))
    refute(creates(stubs)[0], "--add-dir")


def test_the_brief_tells_the_worker_not_to_commit_there(topic, queue_dir):
    """24a. An attached directory is on whatever branch it was already on, and
    a worker that commits in one has committed somewhere nothing publishes."""
    ok(q("add", topic, "reads-two-trees", "--title", "Reads two trees",
         "--repo", "/tmp/repo-a", "--branch", "fix/reads-two-trees", "--number", "01",
         "--add-dir", DOCS))
    brief = " ".join(
        (queue_dir / topic / "01-reads-two-trees" / "BRIEF.md").read_text(encoding="utf-8").split()
    )
    expect(brief, "Also attached", DOCS, "do not commit in them")


def test_a_brief_for_one_repo_names_no_attached_directory(topic, queue_dir):
    """24a. And says nothing at all when there is none."""
    ok(q("add", topic, "one-repo", "--title", "One repo",
         "--repo", "/tmp/repo-a", "--branch", "fix/one-repo", "--number", "01"))
    refute((queue_dir / topic / "01-one-repo" / "BRIEF.md").read_text(encoding="utf-8"),
           "Also attached")


def test_check_refuses_an_add_dirs_that_is_not_a_list_of_paths(topic, queue_dir):
    """24a. Hand-edited records are the only way this can go wrong, and
    `fleet queue check` is where the operator's records are validated."""
    ok(q("add", topic, "one-repo", "--title", "One repo",
         "--repo", "/tmp/repo-a", "--branch", "fix/one-repo", "--number", "01"))
    path = queue_dir / topic / "01-one-repo" / "task.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["add_dirs"] = DOCS
    path.write_text(yaml.safe_dump(doc), encoding="utf-8", newline="\n")
    assert q("check").code != 0
    expect(q("check").out, "add_dirs", "is not a list of paths")


# --- §24b: --add-repo, and the artifact model going plural -------------------


def test_add_repo_is_recorded_and_reaches_session_create_verbatim(topic, queue_dir, stubs):
    """24b. `PATH@BASE` is thurbox's syntax and the operator's string reaches it
    whole — fleet splits it only to answer its own two questions about the
    repository, and never to rewrite what was asked for."""
    ok(q("add", topic, "spans-two", "--title", "Spans two",
         "--repo", "/tmp/repo-a", "--branch", "fix/spans-two", "--number", "01",
         "--add-repo", "/tmp/repo-b", "--add-repo", "/tmp/repo-c@release"))
    doc = record(queue_dir, topic, "01-spans-two")
    assert doc["add_repos"] == ["/tmp/repo-b", "/tmp/repo-c@release"]

    fill_brief(queue_dir / topic / "01-spans-two" / "BRIEF.md")
    next_session(stubs, "dddddddd-0000-0000-0000-000000000003")
    stubs.session_is("dddddddd-0000-0000-0000-000000000003", "idle")
    stubs.tool("thurbox-cli", ANSWERING_KEYS)
    ok(q("dispatch"))
    create = creates(stubs)[0]
    assert "--add-repo /tmp/repo-b --add-repo /tmp/repo-c@release" in create, create


def test_the_brief_names_every_repository_and_asks_for_one_artifact_each(topic, queue_dir):
    """24b. The worker shares no context with the lead, so the brief is where it
    learns both that there is a second repository and what its result must say
    about it."""
    ok(q("add", topic, "spans-two", "--title", "Spans two",
         "--repo", "/tmp/repo-a", "--branch", "fix/spans-two", "--number", "01",
         "--add-repo", "/tmp/repo-b@release"))
    raw = (queue_dir / topic / "01-spans-two" / "BRIEF.md").read_text(encoding="utf-8")
    expect(" ".join(raw.split()), "Also on this branch", "/tmp/repo-b` off `release",
           "one artifact PER REPOSITORY")
    # The result contract in the brief IS the plural one, with this task's own
    # paths already written into it.
    expect(raw, "artifacts:\n  /tmp/repo-a: ", "\n  /tmp/repo-b: ")
    refute(raw, "\nartifact: <PR URL")


def test_a_single_repo_brief_keeps_the_scalar_contract(topic, queue_dir):
    """24b. And the task that spans none is untouched, down to the line."""
    ok(q("add", topic, "one-repo", "--title", "One repo",
         "--repo", "/tmp/repo-a", "--branch", "fix/one-repo", "--number", "01"))
    raw = (queue_dir / topic / "01-one-repo" / "BRIEF.md").read_text(encoding="utf-8")
    expect(raw, "artifact: <PR URL, commit URL for a `push` task")
    refute(raw, "artifacts:", "Also on this branch")


def test_add_refuses_a_branch_that_already_exists_in_the_second_repository(topic, tmp_path):
    """24b. thurbox cuts a worktree on the same branch in EVERY repository, so
    the branch check `add` already made for the primary is made once per
    `--add-repo` — the spawn fails just as dead for the second one."""
    second = tmp_path / "second"
    git("init", "-q", "-b", "main", str(second))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=second)
    git("branch", "fix/taken", cwd=second)

    run = q("add", topic, "spans-two", "--title", "Spans two",
            "--repo", "/tmp/repo-a", "--branch", "fix/taken", "--number", "01",
            "--add-repo", str(second))
    assert run.code != 0
    expect(run.out, f"--add-repo {second}", "already exists")


# --- §24c: one verdict per repository, and the fold that holds the task ------


def pushable(root: Path, name: str) -> dict:
    """A real repository with a bare `origin` — "the commit reached the base
    branch" is a fact of git, and stubbing git would prove nothing."""
    origin, work = root / f"{name}-origin", root / f"{name}-work"
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("init", "-q", "-b", "main", str(work))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=work)
    git("remote", "add", "origin", str(origin), cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    landed = git("rev-parse", "HEAD", cwd=work).strip()
    git("checkout", "-q", "-b", "aside", cwd=work)
    git("commit", "-q", "--allow-empty", "-m", "never pushed", cwd=work)
    aside = git("rev-parse", "HEAD", cwd=work).strip()
    git("checkout", "-q", "main", cwd=work)
    return {"work": str(work), "landed": landed, "aside": aside}


@pytest.fixture
def two_pushes(tmp_path, topic, queue_dir) -> dict:
    """One `push` task over two real repositories, asked of each one's own git."""
    first, second = pushable(tmp_path, "first"), pushable(tmp_path, "second")
    ok(q("add", topic, "commits-in-both", "--title", "Commits in both",
         "--repo", first["work"], "--branch", "fix/commits-in-both", "--number", "01",
         "--add-repo", second["work"], "--publish", "push"))
    return {"first": first, "second": second,
            "dir": queue_dir / topic / "01-commits-in-both", "topic": topic}


def test_every_repository_is_verified_against_its_own_checkout(two_pushes, queue_dir):
    """24c. Two repositories, two verdicts, each read out of the repository the
    artifact claims to be in — not out of the primary's."""
    both = two_pushes
    result_artifacts(both["dir"], "shipped", "Committed in both.", {
        both["first"]["work"]: f"https://github.com/acme/first/commit/{both['first']['landed']}",
        both["second"]["work"]: f"https://github.com/acme/second/commit/{both['second']['landed']}",
    })
    out = ok(q("collect", "--no-reap")).out
    expect(out, "01-commits-in-both  shipped", "2 repositories:", "[publish verified: push]")
    doc = record(queue_dir, both["topic"], "01-commits-in-both")
    # A `push` task has nothing left to ask the forge, so it lands in this same
    # pass — for two repositories exactly as for one.
    assert doc["state"] == "landed", doc
    # The record carries one artifact per repository, each keyed by it.
    assert [a["repo"] for a in doc["artifact"]] == [both["first"]["work"], both["second"]["work"]]
    assert [r["verdict"] for r in doc["artifact_check"]["repos"]] == ["passed", "passed"]


def test_one_unverified_repository_holds_the_whole_task_open(two_pushes, queue_dir):
    """24c. The claim this whole half exists for: a commit fleet does not verify
    is the failure verification exists to stop, and half a task published is not
    a published task. One report, naming which repository."""
    both = two_pushes
    result_artifacts(both["dir"], "shipped", "Committed in both.", {
        both["first"]["work"]: f"https://github.com/acme/first/commit/{both['first']['landed']}",
        # Never pushed: this commit is not on its own base branch.
        both["second"]["work"]: f"https://github.com/acme/second/commit/{both['second']['aside']}",
    })
    out = q("collect", "--no-reap").out
    expect(out, "NOT CLOSED — nothing proves this task published",
           "1 of 2 repositories did not verify",
           both["second"]["work"], "NOT VERIFIED", "never reached the base branch",
           "in EVERY repository it spans")
    doc = record(queue_dir, both["topic"], "01-commits-in-both")
    assert doc["state"] == "queued", "a held task is not closed"
    assert doc["artifact_check"]["verdict"] == "missing"
    verdicts = {r["repo"]: r["verdict"] for r in doc["artifact_check"]["repos"]}
    assert verdicts == {both["first"]["work"]: "passed", both["second"]["work"]: "missing"}


def test_a_repository_the_result_names_nothing_for_is_held_open(two_pushes):
    """24c. A `shipped` claim with one repository left out is exactly a `shipped`
    claim with no artifact at all: there is nothing to check, and nothing proves
    it published."""
    both = two_pushes
    result(both["dir"], "shipped", "Committed in the first one.",
           f"https://github.com/acme/first/commit/{both['first']['landed']}")
    out = q("collect", "--no-reap").out
    expect(out, "NOT CLOSED", both["second"]["work"], "(no artifact given)")


def test_allow_unverified_still_closes_a_task_that_spans_repositories(two_pushes, queue_dir):
    """24c. The operator's override is not narrowed by any of this."""
    both = two_pushes
    result_artifacts(both["dir"], "shipped", "Committed in both.", {
        both["first"]["work"]: f"https://github.com/acme/first/commit/{both['first']['landed']}",
        both["second"]["work"]: f"https://github.com/acme/second/commit/{both['second']['aside']}",
    })
    expect(ok(q("collect", "--allow-unverified", "--no-reap")).out, "publish NOT verified")
    assert record(queue_dir, both["topic"], "01-commits-in-both")["state"] == "landed"


# --- §24d: landing, and the blocker that must not clear early ----------------


@pytest.fixture
def spans_and_dependent(topic, queue_dir, stubs) -> dict:
    """A task over two repositories with a change request in each, and a second
    task waiting on it. Both change requests are attested for their own head."""
    ok(q("add", topic, "spans-two", "--title", "Spans two",
         "--repo", "/tmp/repo-a", "--branch", "fix/spans-two", "--number", "01",
         "--add-repo", "/tmp/repo-b"))
    ok(q("add", topic, "waits-on-it", "--title", "Waits on it",
         "--repo", "/tmp/repo-a", "--branch", "fix/waits-on-it", "--number", "02"))
    ok(q("block", f"{topic}/02-waits-on-it", "--on", f"{topic}/01-spans-two",
         "--kind", "semantic-dependency", "--why", "reads what 01 renames in both repos"))
    stubs.pipeline_pr(701, "fix/spans-two")
    stubs.pipeline_pr(702, "fix/spans-two")
    result_artifacts(queue_dir / topic / "01-spans-two", "shipped", "Opened one in each.", {
        "/tmp/repo-a": "https://github.com/acme/first/pull/701",
        "/tmp/repo-b": "https://github.com/acme/second/pull/702",
    })
    return {"topic": topic, "dir": queue_dir}


def test_a_half_merged_task_does_not_land_and_does_not_release_its_dependents(
    spans_and_dependent, queue_dir
):
    """24d. The bug this is here to stop, one size larger than the one AGENTS.md
    records: a task promoted on half its repositories releases a dependent onto
    code that is not on `main`."""
    topic = spans_and_dependent["topic"]
    expect(ok(q("collect")).out, "01-spans-two  shipped", "2 repositories:")
    assert record(queue_dir, topic, "01-spans-two")["state"] == "done"



def test_landing_needs_every_repository(spans_and_dependent, queue_dir, stubs):
    """24d. `landed` only when every one of them merged, and the blocker clears
    exactly then — never on the first."""
    topic = spans_and_dependent["topic"]
    ok(q("collect"))

    stubs.pr_state(701, "MERGED")
    ok(q("reap"))
    doc = record(queue_dir, topic, "01-spans-two")
    assert doc["state"] == "done", doc
    # The record says which repository is holding it, and with what.
    assert doc["landing"]["state"] == "open"
    expect(doc["landing"]["detail"], "pull/701 is merged", "pull/702 is still open")
    # Its dependent is still held, and the blocker line says so.
    expect(ok(q("show", f"{topic}/02-waits-on-it")).out, "held by semantic-dependency")

    stubs.pr_state(702, "MERGED")
    expect(ok(q("reap")).out, "01-spans-two", "landed")
    assert record(queue_dir, topic, "01-spans-two")["state"] == "landed"
    # And only now is the dependent released.
    expect(ok(q("show", f"{topic}/02-waits-on-it")).out, "cleared:")


def test_a_closed_change_request_abandons_the_task_however_many_merged(
    spans_and_dependent, queue_dir, stubs
):
    """24d. A task holding one change request that was closed unmerged can never
    reach `landed`, so it is `abandoned` — and a blocker does not clear on that,
    which is what keeps its dependents waiting."""
    topic = spans_and_dependent["topic"]
    ok(q("collect"))
    stubs.pr_state(701, "MERGED")
    stubs.pr_state(702, "CLOSED")
    expect(ok(q("reap")).out, "01-spans-two", "abandoned")
    expect(ok(q("show", f"{topic}/02-waits-on-it")).out, "UNCLEARABLE")


def test_a_forge_that_cannot_be_asked_leaves_the_task_where_it_is(
    spans_and_dependent, queue_dir, stubs
):
    """24d. `unknown` never collapses into another word, and a fold of N is where
    it would have been easiest to let it: one unreadable change request beside a
    merged one must not add up to a merge."""
    topic = spans_and_dependent["topic"]
    ok(q("collect"))
    stubs.pr_state(701, "MERGED")
    (Path(os.environ["FLEET_STUB_ROOT"]) / "pr-bodies" / "702.md").unlink()
    ok(q("reap"))
    assert record(queue_dir, topic, "01-spans-two")["state"] == "done"


# --- §24e: a record written before any of this ------------------------------


def test_a_scalar_artifact_record_still_loads_lists_shows_and_reaps(topic, queue_dir, stubs):
    """24e. `task.yaml` carried a scalar `artifact:` before a task could span
    repositories, and one written then must load, show and reap unchanged — the
    way the retired `no-mistakes` method is still read as `attested`."""
    ok(q("add", topic, "written-before", "--title", "Written before",
         "--repo", "/tmp/repo-a", "--branch", "fix/written-before", "--number", "01"))
    path = queue_dir / topic / "01-written-before" / "task.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    # Exactly the shape an older fleet wrote: a scalar artifact, and none of the
    # fields this change introduced.
    doc.update({"state": "done", "outcome": "shipped",
                "artifact": "https://github.com/acme/first/pull/703"})
    doc.pop("add_dirs", None)
    doc.pop("add_repos", None)
    path.write_text(yaml.safe_dump(doc), encoding="utf-8", newline="\n")

    assert q("check").code == 0, q("check").out
    expect(ok(q("list")).out, "01-written-before")
    expect(ok(q("show", f"{topic}/01-written-before")).out,
           "artifact:    https://github.com/acme/first/pull/703")

    stubs.plain_pr(703, "fix/written-before")
    stubs.pr_state(703, "MERGED")
    expect(ok(q("reap")).out, "01-written-before", "landed", "pull/703 is merged")


# --- §24f: one task, two different forges -----------------------------------
#
# `scripts/lib/forge.py` identifies a repository by HOST plus path precisely so
# that `github.com/a/b` and a self-hosted `gitlab/a/b` are different
# repositories. A task that spans repositories is where that stops being a
# nicety: its primary can be on GitHub and its `--add-repo` on GitLab, and each
# artifact has to be verified by the forge that actually holds it. Driven, not
# asserted — §13 and §14's bar.

GL_MR = "https://gitlab.example.com/acme/group/widgets/-/merge_requests/"


class CrossForge:
    def __init__(self, queue: Path, env: dict, topic: str, gh: Path, gl: Path, store: GlabStore):
        self.queue, self.env, self.topic = queue, env, topic
        self.gh, self.gl, self.store = gh, gl, store

    def q(self, *args: str) -> object:
        return q(*args, **self.env)

    @property
    def dir(self) -> Path:
        return self.queue / self.topic / "01-both-forges"


@pytest.fixture
def cross_forge(tmp_path, topic, queue_dir, stubs) -> CrossForge:
    store = GlabStore(tmp_path / "gitlab")
    stubs.tool("glab", FAKE_GLAB)
    gh, gl = tmp_path / "gh-repo", tmp_path / "gl-repo"
    for repo, origin in ((gh, "https://github.com/acme/first.git"),
                         (gl, "https://gitlab.example.com/acme/group/widgets.git")):
        git("init", "-q", "-b", "main", str(repo))
        git("commit", "-q", "--allow-empty", "-m", "base", cwd=repo)
        git("remote", "add", "origin", origin, cwd=repo)
    env = {"FAKE_GLAB_DIR": str(store.root), "GITLAB_HOST": "gitlab.example.com"}
    ok(q("add", topic, "both-forges", "--title", "Both forges", "--repo", str(gh),
         "--branch", "fix/both-forges", "--number", "01", "--add-repo", str(gl), **env))
    stubs.pipeline_pr(801, "fix/both-forges")
    store.mr(501, source_branch="fix/both-forges")
    cf = CrossForge(queue_dir, env, topic, gh, gl, store)
    yield cf
    worktrees = Path(os.environ["XDG_DATA_HOME"]) / "fleet" / "worktrees"
    for wt in worktrees.glob(f"{topic}__*"):
        git("worktree", "remove", "--force", str(wt), cwd=gl)


def test_a_task_spanning_two_forges_is_verified_by_each_of_them(cross_forge, queue_dir):
    """24f. Each artifact asked of the forge that holds it, in one `collect`."""
    cf = cross_forge
    result_artifacts(cf.dir, "shipped", "Opened one on each forge.", {
        str(cf.gh): "https://github.com/acme/first/pull/801",
        str(cf.gl): f"{GL_MR}501",
    })
    out = ok(cf.q("collect")).out
    expect(out, "01-both-forges  shipped", "pull/801", "merge_requests/501",
           "[publish verified: attested]")
    doc = record(queue_dir, cf.topic, "01-both-forges")
    assert doc["state"] == "done", doc
    assert [r["verdict"] for r in doc["artifact_check"]["repos"]] == ["passed", "passed"]


def test_it_lands_only_when_both_forges_say_merged(cross_forge, queue_dir, stubs):
    """24f. And the fold reaches across forges: GitHub merged with GitLab still
    open is a task that has not landed."""
    cf = cross_forge
    result_artifacts(cf.dir, "shipped", "Opened one on each forge.", {
        str(cf.gh): "https://github.com/acme/first/pull/801",
        str(cf.gl): f"{GL_MR}501",
    })
    ok(cf.q("collect"))

    stubs.pr_state(801, "MERGED")
    ok(cf.q("reap"))
    assert record(queue_dir, cf.topic, "01-both-forges")["state"] == "done"

    cf.store.set_state(501, "merged")
    expect(ok(cf.q("reap")).out, "01-both-forges", "landed")


def test_shepherd_watches_every_repository_the_task_names(cross_forge):
    """24f. The repository set `shepherd` enumerates is every one the tasks name,
    each `--add-repo` included — a change request in the second repository that
    nothing watched would go bad exactly the way #25 did."""
    cf = cross_forge
    result_artifacts(cf.dir, "shipped", "Opened one on each forge.", {
        str(cf.gh): "https://github.com/acme/first/pull/801",
        str(cf.gl): f"{GL_MR}501",
    })
    ok(cf.q("collect"))
    out = ok(cf.q("shepherd", "--topic", cf.topic, "--dry-run")).out
    expect(out, "first on github.com", "acme/group/widgets on gitlab.example.com",
           "merge_requests/501")


def test_a_fixer_is_cut_from_the_repository_the_change_request_is_in(cross_forge, stubs):
    """24f. A fixer sent into the primary's checkout would rebase the wrong tree
    and push the wrong branch. The checkout is chosen from the repository the
    change request is actually open in."""
    cf = cross_forge
    git("branch", "fix/both-forges", cwd=cf.gl)
    cf.store.mr(501, source_branch="fix/both-forges", has_conflicts=True,
                detailed_merge_status="conflict")
    result_artifacts(cf.dir, "shipped", "Opened one on each forge.", {
        str(cf.gh): "https://github.com/acme/first/pull/801",
        str(cf.gl): f"{GL_MR}501",
    })
    ok(cf.q("collect"))

    sid = "ffffffff-0000-0000-0000-000000000001"
    next_session(stubs, sid)
    stubs.session_is(sid, "idle")
    stubs.tool("thurbox-cli", ANSWERING_KEYS)
    expect(ok(cf.q("shepherd", "--topic", cf.topic)).out, "dispatched", "Rebase")

    cut = f"{cf.topic}__01-both-forges"
    assert cut in git("worktree", "list", cwd=cf.gl), "the fixer's worktree is off the GitLab checkout"
    assert cut not in git("worktree", "list", cwd=cf.gh), "and not off the primary"
