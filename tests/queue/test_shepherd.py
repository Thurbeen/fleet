"""Claim 9, the shepherd: the pull request, after the worker stopped.

The gap this closes, three times in one day: #14 went CONFLICTING when #13
merged and nothing noticed; #11 and #12 were opened outside the pipeline and
nobody saw for hours; a review finding sat in a PR body until a human read it
out. Noticing was never the expensive part, so the claim is that a broken PR
gets a FIXER and a good one gets MERGED — not that a status line is printed.

The list comes from the FORGE and not from the task records, because a task
records one artifact, and #25 was a second PR from a task still pointing at
the already-merged #23. And because this runs unattended against a public repo
with a fork, what it never merges and never fixes matters as much as what it
does.
"""

import json
import os
from pathlib import Path

import pytest
import yaml
from kit_shepherd import BUSY, GONE, Shep, repo, seed

from harness import REPO, expect, git, refute
from harness import run_queue as q


@pytest.fixture
def shep(stubs) -> Shep:
    return Shep(stubs)


@pytest.fixture
def srepo(tmp_path) -> Path:
    return repo(tmp_path / "shepherd-repo")


@pytest.fixture
def stopic(shep, srepo, queue_dir) -> str:
    return seed(shep, srepo, queue_dir)


@pytest.fixture
def first_pass(stopic) -> str:
    """The first real pass over the shepherd topic, and everything it printed."""
    return q("shepherd", "--topic", stopic).out


def test_a_dry_run_says_what_it_would_do_and_does_none_of_it(stopic, shep):
    out = q("shepherd", "--topic", stopic, "--dry-run").out
    expect(out,
           # The conflicting PR, what it would dispatch, and why in the PR's own terms.
           "pull/101", "would-dispatch", "conflicts with main",
           # What it would merge, and the command it would merge with.
           "would-merge", "--squash --delete-branch")
    assert shep.creates() == [], shep.tbx_log()
    assert shep.merged() == [] and "pr merge" not in shep.gh_log(), shep.gh_log()

    # --json is a clean seam for `fleet status`.
    assert "prs" in json.loads(q("shepherd", "--dry-run", "--json").stdout)


def test_a_conflicting_pr_dispatches_exactly_one_fixer_on_the_branch_that_exists(
    first_pass, stopic, shep, srepo, queue_dir
):
    # ONE per broken PR, not one per pass: 101 conflicts, 103 skipped the
    # pipeline, 107's worker is gone and 114 is 08's second PR.
    assert len(shep.creates("01-conflicting")) == 1, first_pass + shep.tbx_log()
    assert len(shep.creates()) == 4, first_pass + shep.tbx_log()
    # Prompted, not left on its trust dialog.
    expect(shep.tbx_log(), "session send")

    created = "\n".join(shep.creates())
    expect(created, "--repo-path")
    # `--worktree-branch` only ever CREATES a branch, and this one already exists.
    refute(created, "--worktree-branch")

    # The fixer's checkout used to be cut under the queue root, inside the
    # control-plane checkout, where Claude Code found the lead's CLAUDE.md and
    # stopped on its external-imports dialog.
    porcelain = git("worktree", "list", "--porcelain", cwd=srepo)
    path = None
    for block in porcelain.split("\n\n"):
        lines = block.splitlines()
        if "branch refs/heads/fix/conflicting" in lines:
            path = Path(lines[0].removeprefix("worktree ")).resolve()
    assert path is not None, f"the existing branch is checked out as a worktree, not renamed aside\n{porcelain}"
    assert not path.is_relative_to(queue_dir.resolve()), path
    assert not path.is_relative_to(REPO.resolve()), path
    assert path.is_relative_to(Path(os.environ["XDG_DATA_HOME"]).resolve() / "fleet" / "worktrees"), path

    briefs = sorted((queue_dir / stopic / "01-conflicting").rglob("fix-*.md"))
    assert briefs, "the fixer gets a written brief of its own"
    expect(briefs[0].read_text(encoding="utf-8"),
           # The condition, not just a PR number; the PR it must update, in place.
           "conflicts with main", "pull/101", "in place",
           # And never a second pull request, and never a merge.
           "Do not open a second", "Do not merge it")


def test_a_green_attested_pr_in_an_allowlisted_repo_is_squash_merged(first_pass, shep):
    expect(first_pass, "merged")
    assert 102 in shep.merged(), first_pass
    # A squash, the only method the remote allows.
    expect(shep.gh_log(), "pr merge https://github.com/Thurbeen/fleet/pull/102 --squash --delete-branch")


def test_remote_merge_is_reported_after_local_branch_cleanup_fails(stopic, shep, queue_dir):
    run = q("shepherd", "--topic", stopic, "--json", SHEP_MERGE_CLEANUP_ERROR="102")
    assert run.code == 0, run.out
    row = next(pr for pr in json.loads(run.stdout)["prs"] if pr["pr"].endswith("/pull/102"))
    assert row["action"] == "merged", row
    expect(row["note"], "remote confirmed", "could not delete local branch")
    refute(row["note"], "branch deleted")
    assert 102 in shep.merged()
    expect(shep.gh_log(), "pr merge https://github.com/Thurbeen/fleet/pull/102 --squash --delete-branch",
           "pr view https://github.com/Thurbeen/fleet/pull/102 --json state")
    task = yaml.safe_load((queue_dir / stopic / "02-green" / "task.yaml").read_text(encoding="utf-8"))
    assert task["shepherd"]["condition"] == "merged", task
    expect(task["shepherd"]["detail"], "could not delete local branch")


@pytest.mark.parametrize("env,reason,remote_merged", [
    ({"SHEP_MERGE_REJECTED": "102"}, "merge rejected", False),
    ({"SHEP_MERGE_REJECTED": "102", "SHEP_VIEW_DOWN": "102"}, "merge rejected", False),
    ({"SHEP_MERGE_CLEANUP_ERROR": "102", "SHEP_VIEW_DOWN": "102"},
     "could not delete local branch", True),
])
def test_a_failed_or_unconfirmed_merge_is_not_reported_as_merged(stopic, shep, queue_dir,
                                                                 env, reason, remote_merged):
    run = q("shepherd", "--topic", stopic, "--json", **env)
    assert run.code == 0, run.out
    row = next(pr for pr in json.loads(run.stdout)["prs"] if pr["pr"].endswith("/pull/102"))
    assert row["action"] == "merge-failed", row
    expect(row["note"], reason)
    assert (102 in shep.merged()) == remote_merged
    task = yaml.safe_load((queue_dir / stopic / "02-green" / "task.yaml").read_text(encoding="utf-8"))
    assert task.get("shepherd", {}).get("condition") != "merged", task


def test_what_is_never_merged_is_named_and_not_passed_over(first_pass, shep):
    merged = shep.merged()
    # 103 skipped the pipeline, however green, and gets a fixer's condition instead.
    assert 103 not in merged
    expect(first_pass, "policy")
    # 106's checks have not reported: an empty rollup is its own answer, not a pass.
    assert 106 not in merged
    expect(first_pass, "no check has reported")
    # 104 is outside the allowlist, and is handed back to the operator by name.
    assert 104 not in merged
    expect(first_pass, "fleet does not merge in")


def test_every_open_pr_is_shepherded_whether_or_not_a_task_recorded_it(first_pass, shep):
    # 108: no task records it. Discovered from the forge, merged like any other,
    # and named as belonging to no task rather than passed over in silence.
    expect(first_pass, "pull/108", "no task")
    assert 108 in shep.merged(), first_pass
    # A second PR on a task's branch links back to that task, and its fixer
    # works in that task's own branch checkout.
    expect(first_pass, "08-second")
    assert shep.creates("08-second"), shep.tbx_log()


def test_a_body_never_authorises_its_own_merge(first_pass, shep):
    """The repo is public and has a fork, and this runs unattended on a timer.
    A body is text anyone can paste; only an attestation for THIS head, on a
    PR someone who can push here opened, authorises a merge."""
    merged = shep.merged()
    # 109: a fork's, green and attested for its own head — never merged, never
    # handed to an agent, and reported by name.
    assert 109 not in merged
    refute(shep.tbx_log(), "patch-1")
    expect(first_pass, "pull/109")
    # 110: an attestation for an earlier head sha, which says so.
    assert 110 not in merged
    expect(first_pass, "attestation")
    # 111: a body that only SAYS the pipeline ran.
    assert 111 not in merged
    # 112: ours by branch, opened by someone with no push access, and whose.
    assert 112 not in merged
    expect(first_pass, "stranger has no access")


def test_a_worker_mid_turn_is_left_alone_and_a_gone_one_is_replaced(first_pass, shep):
    expect(first_pass, "left-alone")
    refute("\n".join(shep.stubs.calls("thurbox-cli", "session send")), BUSY)
    # A brief is never typed at a session id that answers nobody; its PR gets a
    # fresh session on the branch instead.
    assert not shep.stubs.calls("thurbox-cli", f"session send {GONE}"), shep.tbx_log()
    assert shep.creates("07-gone"), shep.tbx_log()


def test_a_second_pass_sends_no_second_fixer_until_the_first_one_dies(first_pass, stopic, shep, queue_dir):
    before = len(shep.creates())
    out = q("shepherd", "--topic", stopic).out
    assert len(shep.creates()) == before, out + shep.tbx_log()
    expect(out, "in-flight")

    # A fixer that crashed or was cleaned up after dispatch must not stall that
    # PR's recovery forever: gone reads as no session, for a fixer too.
    doc = yaml.safe_load((queue_dir / stopic / "01-conflicting" / "task.yaml").read_text(encoding="utf-8"))
    (shep.stubs.root / "sessions" / f"{doc['shepherd']['session']}.json").unlink()
    out = q("shepherd", "--topic", stopic).out
    assert len(shep.creates()) == before + 1, out + shep.tbx_log()
    expect(out, "dispatched:")
    assert len(shep.creates("01-conflicting")) == 2, shep.tbx_log()


def test_an_unreachable_gh_dispatches_nothing_and_merges_nothing(first_pass, stopic, shep):
    """"Could not check" is never "broken", which spawns fixers for healthy PRs,
    and never "fine", which merges PRs nobody looked at."""
    creates, merges = len(shep.creates()), len(shep.stubs.calls("gh", "pr merge"))
    out = q("shepherd", "--topic", stopic, SHEP_GH_DOWN="1").out
    assert len(shep.creates()) == creates, out
    assert len(shep.stubs.calls("gh", "pr merge")) == merges, out
    expect(out, "could not read the pull request")
    refute(out, "would-merge")

    # The shepherd only ever reads and merges.
    refute(shep.gh_log(), "pr close", "pr edit")


def test_a_repo_at_ghs_list_limit_is_unreadable_and_not_silently_capped(stubs, tmp_path, queue_dir):
    """`gh pr list --limit N` is a request cap, not a page size: reaching N means
    there may be more, and an empty answer and a possibly-truncated one are
    not the same claim."""
    from kit_shepherd import GH_MANY, result

    stubs.tool("gh", GH_MANY)
    trepo = repo(tmp_path / "repo-many")
    topic = q("topic", "add", "many-prs", "--title", "A repo at the pagination limit",
              "--prompt", "shepherd a repo with at least GH_PR_LIST_LIMIT open pull requests").stdout.strip()
    q("add", topic, "only", "--title", "only", "--repo", str(trepo), "--branch", "fix/only", "--number", "1")
    result(queue_dir / topic / "1-only", "https://github.com/many-owner/many-repo/pull/9999")
    q("collect")

    out = q("shepherd", "--topic", topic).out
    expect(out, "could not read the pull requests on many-owner/many-repo", "may be truncated")
    refute("\n".join(stubs.calls("gh")), "pr merge")
    refute(out, "no open pull requests")
