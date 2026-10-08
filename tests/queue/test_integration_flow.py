"""Two ways a delivered task used to stay NOT CLOSED, and the record call it `abandoned`.

An INTEGRATION BRANCH. Some repositories never take a change request from a
task's branch onto `main`: the work reaches `develop`, and `develop` goes to
`main` in one change request of its own. `collect` compared that change
request's head branch with the task's and refused it every time, so shipped
work was closed by hand or abandoned. `orchestration/flow.conf` names those
repositories and their integration branch, and a change request from it
verifies when it CONTAINS the task's branch head — still nothing a worker can
write for itself, since the forge lists the commits.

A NO-OP REPOSITORY. A task that spans repositories can find one of them needs
nothing. `artifacts:` takes `no change needed — <reason>` for it, which verifies
on its own and is never waited on to land.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from queuekit import ok, result, result_artifacts

from harness import expect, git, write
from harness import run_queue as q

PR = "https://github.com/acme/app/pull/"


def record(queue_dir: Path, topic: str, tid: str) -> dict:
    return yaml.safe_load((queue_dir / topic / tid / "task.yaml").read_text(encoding="utf-8"))


def flow(isolated_env, text: str) -> None:
    write(isolated_env / "settings" / "orchestration" / "flow.conf", text)


@pytest.fixture
def worked(tmp_path, topic, queue_dir) -> dict:
    """A `pr` task over a real checkout whose task branch carries one commit."""
    work = tmp_path / "app"
    git("init", "-q", "-b", "main", str(work))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=work)
    ok(q("add", topic, "vend", "--title", "Vend", "--repo", str(work),
         "--branch", "feat/vend", "--number", "01", "--publish", "pr"))
    # What the worker's worktree leaves behind: the task's branch, one commit on.
    git("checkout", "-q", "-b", "feat/vend", cwd=work)
    git("commit", "-q", "--allow-empty", "-m", "feat: vend", cwd=work)
    tip = git("rev-parse", "HEAD", cwd=work).strip()
    git("checkout", "-q", "main", cwd=work)
    return {"work": str(work), "tip": tip, "dir": queue_dir / topic / "01-vend", "topic": topic}


def develop_pr(stubs, n: int, *commits: str) -> None:
    stubs.plain_pr(n, "develop")
    stubs.pr_history(n, *(f"{c} a commit" for c in commits))


# --- the integration branch ---------------------------------------------------


def test_a_develop_pr_containing_the_task_head_verifies(worked, stubs, isolated_env, queue_dir):
    flow(isolated_env, "github.com/acme/app = develop\n")
    develop_pr(stubs, 1200, "a" * 40, worked["tip"], "b" * 40)
    result(worked["dir"], "shipped", "Merged into develop; develop→main is open.", PR + "1200")
    expect(ok(q("collect", "--no-reap")).out, "[publish verified: pr]")
    doc = record(queue_dir, worked["topic"], "01-vend")
    assert doc["state"] == "done", doc
    expect(doc["artifact_check"]["detail"], "integration branch develop", worked["tip"][:8])


def test_without_a_flow_the_develop_pr_is_still_refused(worked, stubs, queue_dir):
    """The tracked example names none, so nothing changes until the operator says so."""
    develop_pr(stubs, 1201, worked["tip"])
    result(worked["dir"], "shipped", "develop→main is open.", PR + "1201")
    expect(q("collect", "--no-reap").out, "NOT CLOSED", "is from branch develop")
    assert record(queue_dir, worked["topic"], "01-vend")["state"] == "queued"


def test_a_develop_pr_without_the_task_head_is_refused(worked, stubs, isolated_env, queue_dir):
    """Somebody else's develop→main is the pasted-PR hole this check exists to close."""
    flow(isolated_env, "github.com/acme/app = develop\n")
    develop_pr(stubs, 1202, "c" * 40)
    result(worked["dir"], "shipped", "develop→main is open.", PR + "1202")
    expect(q("collect", "--no-reap").out, "NOT CLOSED", "does not contain", worked["tip"][:8])
    assert record(queue_dir, worked["topic"], "01-vend")["state"] == "queued"


def test_an_attested_task_still_needs_the_develop_pr_attested_or_merged(
    tmp_path, topic, queue_dir, stubs, isolated_env
):
    """The flow replaces the branch check only: an attestation is still a verdict
    about the head that would merge, so develop→main needs its own, or its merge."""
    flow(isolated_env, "github.com/acme/app = develop\n")
    work = tmp_path / "attested-app"
    git("init", "-q", "-b", "main", str(work))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=work)
    ok(q("add", topic, "vend-attested", "--title", "Vend attested", "--repo", str(work),
         "--branch", "feat/vend-attested", "--number", "02", "--publish", "attested"))
    git("checkout", "-q", "-b", "feat/vend-attested", cwd=work)
    git("commit", "-q", "--allow-empty", "-m", "feat: vend", cwd=work)
    tip = git("rev-parse", "HEAD", cwd=work).strip()
    develop_pr(stubs, 1210, tip)
    result(queue_dir / topic / "02-vend-attested", "shipped", "develop→main is open.", PR + "1210")
    expect(q("collect", "--no-reap").out, "NOT CLOSED", "attestation")

    stubs.pr_state(1210, "MERGED")
    expect(ok(q("collect", "--no-reap")).out, "[publish verified: attested]")


def test_a_full_page_of_commits_without_the_head_is_unchecked_not_refused(
    worked, stubs, isolated_env, queue_dir
):
    """A forge lists a long develop→main one page at a time, so a head missing
    from a full page is not proven absent — the task closes as could-not-check."""
    flow(isolated_env, "github.com/acme/app = develop\n")
    develop_pr(stubs, 1207, *(f"{i:040x}" for i in range(100)))
    result(worked["dir"], "shipped", "develop→main is open.", PR + "1207")
    expect(ok(q("collect", "--no-reap")).out, "publish unchecked", "100 commits")
    assert record(queue_dir, worked["topic"], "01-vend")["state"] == "done"


def test_a_pr_from_a_branch_that_is_not_the_integration_one_is_refused(
    worked, stubs, isolated_env
):
    flow(isolated_env, "github.com/acme/app = develop\n")
    stubs.plain_pr(1203, "release")
    stubs.pr_history(1203, f"{worked['tip']} feat: vend")
    result(worked["dir"], "shipped", "release→main is open.", PR + "1203")
    expect(q("collect", "--no-reap").out, "NOT CLOSED", "is from branch release")


def test_a_flow_for_another_repository_changes_nothing(worked, stubs, isolated_env):
    flow(isolated_env, "github.com/acme/other = develop\n")
    develop_pr(stubs, 1204, worked["tip"])
    result(worked["dir"], "shipped", "develop→main is open.", PR + "1204")
    expect(q("collect", "--no-reap").out, "NOT CLOSED", "is from branch develop")


def test_the_task_branch_own_pr_still_verifies_under_a_flow(worked, stubs, isolated_env):
    """feat→develop is the other half of the flow, and it is from the task's branch."""
    flow(isolated_env, "github.com/acme/app = develop\n")
    stubs.plain_pr(1205, "feat/vend")
    result(worked["dir"], "shipped", "feat→develop is open.", PR + "1205")
    expect(ok(q("collect", "--no-reap")).out, "[publish verified: pr]")


def test_a_flow_entry_that_names_no_forge_is_refused_out_loud(worked, stubs, isolated_env):
    flow(isolated_env, "acme/app = develop\n")
    develop_pr(stubs, 1206, worked["tip"])
    result(worked["dir"], "shipped", "develop→main is open.", PR + "1206")
    run = q("collect", "--no-reap")
    expect(run.out, "must name its forge", "NOT CLOSED")


# --- the no-op repository -----------------------------------------------------


@pytest.fixture
def spans(topic, queue_dir, stubs) -> dict:
    ok(q("add", topic, "spans-two", "--title", "Spans two", "--repo", "/tmp/repo-a",
         "--branch", "fix/spans-two", "--number", "01", "--add-repo", "/tmp/repo-b",
         "--publish", "pr"))
    stubs.plain_pr(801, "fix/spans-two")
    return {"dir": queue_dir / topic / "01-spans-two", "topic": topic}


def test_a_no_op_repository_verifies_with_its_reason(spans, queue_dir, stubs):
    result_artifacts(spans["dir"], "shipped", "Only the first needed a change.", {
        "/tmp/repo-a": "https://github.com/acme/first/pull/801",
        "/tmp/repo-b": "no change needed — it already reads the new field",
    })
    expect(ok(q("collect")).out, "01-spans-two  shipped", "[publish verified: pr]")
    doc = record(queue_dir, spans["topic"], "01-spans-two")
    assert doc["state"] == "done", doc
    verdicts = {r["repo"]: (r["verdict"], r["detail"]) for r in doc["artifact_check"]["repos"]}
    assert verdicts["/tmp/repo-b"][0] == "passed"
    expect(verdicts["/tmp/repo-b"][1], "no change needed", "it already reads the new field")

    # The no-op is never waited on: the one real change request merging lands it.
    stubs.pr_state(801, "MERGED")
    expect(ok(q("reap")).out, "01-spans-two", "landed")
    assert record(queue_dir, spans["topic"], "01-spans-two")["state"] == "landed"


def test_a_no_op_with_no_reason_is_held_open(spans, queue_dir):
    result_artifacts(spans["dir"], "shipped", "Only the first needed a change.", {
        "/tmp/repo-a": "https://github.com/acme/first/pull/801",
        "/tmp/repo-b": "no change needed",
    })
    expect(q("collect", "--no-reap").out, "NOT CLOSED", "/tmp/repo-b", "give the reason")
    assert record(queue_dir, spans["topic"], "01-spans-two")["state"] == "queued"



def test_a_closed_develop_pr_does_not_retire_the_task_as_closed_unmerged(
    worked, stubs, isolated_env, queue_dir
):
    """A closed develop→main is not the task's own change request: its work is
    already on develop, so the task is held NOT CLOSED, as before, and never
    retired in the words that mean its own change request was given up."""
    flow(isolated_env, "github.com/acme/app = develop\n")
    develop_pr(stubs, 1210, worked["tip"])
    stubs.pr_state(1210, "CLOSED")
    result(worked["dir"], "shipped", "develop→main was closed.", PR + "1210")
    out = q("collect", "--no-reap").out
    expect(out, "NOT CLOSED")
    doc = record(queue_dir, worked["topic"], "01-vend")
    assert doc["state"] == "queued", doc
    assert "abandoned" not in doc
