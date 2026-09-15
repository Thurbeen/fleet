"""Claim 10: the shepherd writes down the publish state it already saw.

Every fact arrives in the ONE `gh pr list` the pass already makes, and the pass
used to throw all of it away: the record said `shipped` and nothing else, so
"are its checks still running or did they fail an hour ago" meant running the
command again. A real pass now stamps `publish.state` on every task it linked.

The gate is method-aware. A `pr` task whose pull request is green, mergeable
and ours is recorded `green`, gets no fixer and is NOT merged — the forge is
happy and nothing vetted the head that would land. An `attested` task with no
attestation is recorded `unattested` and still gets its fixer.
"""

import os
from pathlib import Path

import pytest
from kit_shepherd import Shep, repo, seed
from queuekit import ok, result

from harness import expect, git, refute
from harness import run_queue as q


@pytest.fixture
def shep(stubs) -> Shep:
    return Shep(stubs)


@pytest.fixture
def srepo(tmp_path) -> Path:
    return repo(tmp_path / "shepherd-repo")


@pytest.fixture
def stopic(shep, srepo, queue_dir) -> str:
    topic = seed(shep, srepo, queue_dir)
    ok(q("shepherd", "--topic", topic))
    return topic


def worktrees(srepo: Path) -> set[Path]:
    porcelain = git("worktree", "list", "--porcelain", cwd=srepo)
    return {Path(line.removeprefix("worktree ")).resolve() for line in porcelain.splitlines()
            if line.startswith("worktree ")}


def test_a_green_pr_nobody_attested_is_recorded_green_and_never_merged(stopic, shep, srepo):
    ok(q("add", stopic, "plain-pr", "--title", "A PR opened by whatever this repo uses", "--repo", str(srepo),
         "--branch", "fix/plain-pr", "--number", "09", "--publish", "pr",
         "--how", "run the release script this repo already has"))
    # A real branch, so "no fixer was sent" means fleet chose not to send one.
    git("branch", "fix/plain-pr", cwd=srepo)
    # 102's own fixture with another branch and a body nobody attested.
    shep.pr(115, headRefName="fix/plain-pr", body="Opened with this repo's own release script.\n")

    # A dry run classifies it too, and records nothing.
    expect(q("shepherd", "--topic", stopic, "--dry-run").out, "pull/115")
    refute(q("show", f"{stopic}/09-plain-pr").out, "published:")

    out = q("shepherd", "--topic", stopic).out
    lines = out.splitlines()
    row = "\n".join(next(lines[i:i + 3] for i, line in enumerate(lines) if "pull/115" in line))
    state = q("show", f"{stopic}/09-plain-pr").out
    # What it saw, and the command that looked — and never `ready`.
    expect(state, "published:   green", "(shepherd,")
    refute(state, "published:   ready")
    assert 115 not in shep.merged(), shep.merged()
    # Handed to the operator with the reason, and never called a policy breach.
    expect(row, "not attested")
    refute(row, "policy:")
    assert shep.creates("09-plain-pr") == [], out + shep.tbx_log()

    # The `attested` half of the same gate, unchanged.
    expect(q("show", f"{stopic}/03-skipped").out, "published:   unattested")
    assert shep.creates("03-skipped"), shep.tbx_log()


def test_the_landing_sweep_writes_merged_and_closed_in_the_same_place(stubs, queue_dir):
    ok(q("topic", "add", "landings", "--title", "What the landing sweep writes down",
         "--prompt", "the publish block must say what the sweep learned"))
    for slug, title in (("merged-pr", "A pull request that merged"),
                        ("closed-pr", "A pull request that was closed unmerged")):
        ok(q("add", "landings", slug, "--title", title, "--repo", "/tmp/repo-a", "--branch", f"fix/{slug}",
             "--publish", "pr"))
    stubs.plain_pr(1020, "fix/merged-pr")
    stubs.plain_pr(1021, "fix/closed-pr")
    result(queue_dir / "landings" / "01-merged-pr", "shipped", "Opened it.", "https://github.com/acme/app/pull/1020")
    result(queue_dir / "landings" / "02-closed-pr", "shipped", "Opened it.", "https://github.com/acme/app/pull/1021")

    ok(q("collect", "--no-reap"))
    expect(q("show", "landings/01-merged-pr").out, "published:   open")

    stubs.pr_state(1020, "MERGED")
    stubs.pr_state(1021, "CLOSED")
    q("reap")
    expect(q("show", "landings/01-merged-pr").out, "published:   merged")
    expect(q("show", "landings/02-closed-pr").out, "published:   closed")


def test_reap_takes_back_a_landed_tasks_fixer_checkout(stopic, shep, srepo, queue_dir):
    """git cut the fixer's checkout, not thurbox, so `session delete` never
    removes it. 01's is where fixers go now; 02's is cut where they used to go,
    under the queue root, the kind still on disk from before the move."""
    fixers = Path(os.environ["XDG_DATA_HOME"]) / "fleet" / "worktrees"
    legacy = queue_dir / ".worktrees" / f"{stopic}__02-green"
    git("worktree", "add", "-q", str(legacy), "fix/green", cwd=srepo)
    shep.update(101, state="MERGED")
    shep.update(102, state="MERGED")

    out = q("reap").out
    expect(q("show", f"{stopic}/01-conflicting").out, "landed")
    for wt in (fixers / f"{stopic}__01-conflicting", legacy):
        assert not wt.exists(), out
        assert wt.resolve() not in worktrees(srepo), out
    # And the checkout of a task that has not landed stays.
    assert (fixers / f"{stopic}__03-skipped").exists(), out
