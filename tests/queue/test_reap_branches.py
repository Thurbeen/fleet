"""Reap deletes a finished task's branch only when nothing on it can be lost."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from kit_hosts import Hosts
from queuekit import ok

from harness import expect, git, queue_module, refute, write
from harness import run_queue as q

SID = "eeeeeeee-0000-0000-0000-000000000001"
BRANCH = "plan/topic-1"
EARLIER = {"session": SID, "how": "deleted", "at": "earlier"}


@pytest.fixture
def queue_dir(isolated_env) -> Path:
    ok(q("topic", "add", "plans", "--title", "Plans", "--prompt", "plan it"))
    return Path(os.environ["FLEET_QUEUE_DIR"])


def checkout(root: Path, name: str) -> Path:
    """A checkout whose `origin/main` is its `main`."""
    origin, work = root / f"{name}.git", root / name
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("init", "-q", "-b", "main", str(work))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=work)
    git("remote", "add", "origin", str(origin), cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    git("fetch", "-q", "origin", cwd=work)
    return work


@pytest.fixture
def repo(tmp_path) -> Path:
    return checkout(tmp_path, "app")


def task(queue_dir: Path, repo: Path, *flags: str, slug: str = "plan-it", branch: str = BRANCH,
         base: str = "origin/main", **fields) -> Path:
    """One task on `repo`, added before its branch exists, cut off origin/main, then finished."""
    ok(q("add", "plans", slug, "--title", slug, "--repo", str(repo), "--branch", branch,
         "--base", base, "--publish", "none", *flags))
    record = next((queue_dir / "plans").glob(f"*-{slug}")) / "task.yaml"
    doc = yaml.safe_load(record.read_text(encoding="utf-8"))
    for path in [str(repo), *(doc.get("add_repos") or [])]:
        git("branch", branch, "origin/main", cwd=Path(path))
    doc.update({"state": "landed", "session": None, "reaped": EARLIER, **fields})
    write(record, yaml.safe_dump(doc, sort_keys=False))
    return record


def commit_on(repo: Path, branch: str = BRANCH) -> None:
    tree = git("rev-parse", f"{branch}^{{tree}}", cwd=repo).strip()
    tip = git("commit-tree", tree, "-p", branch, "-m", "the plan", cwd=repo).strip()
    git("update-ref", f"refs/heads/{branch}", tip, cwd=repo)


def has_branch(repo: Path, branch: str = BRANCH) -> bool:
    return branch in git("branch", "--list", branch, cwd=repo)


def released(record: Path):
    return (yaml.safe_load(record.read_text(encoding="utf-8")).get("branch_released") or {}).get("how")


def test_reap_deletes_the_branch_of_a_landed_task_it_releases(queue_dir, repo, stubs):
    record = task(queue_dir, repo, session=SID, reaped=None)
    git("config", f"branch.{BRANCH}.remote", "origin", cwd=repo)
    stubs.session_is(SID, "idle")
    out = ok(q("reap")).out
    expect(out, "reaped", SID, "deleted", f"branch {BRANCH} in {repo}")
    assert not has_branch(repo)
    assert released(record) == "deleted"
    # Its upstream goes with it, so a later task cut under the name inherits none.
    assert subprocess.run(["git", "-C", str(repo), "config", "--get", f"branch.{BRANCH}.remote"],
                          capture_output=True).returncode == 1
    refute(ok(q("reap")).out, BRANCH)


def test_a_task_reaped_before_this_existed_is_swept(queue_dir, repo):
    record = task(queue_dir, repo)
    expect(ok(q("reap")).out, "deleted", BRANCH)
    assert not has_branch(repo)
    assert released(record) == "deleted"


def test_a_dispatched_task_whose_session_left_no_receipt_is_swept(queue_dir, repo):
    record = task(queue_dir, repo, reaped=None, dispatched_at="2026-10-01T00:00:00Z")
    expect(ok(q("reap")).out, "deleted", BRANCH)
    assert released(record) == "deleted"


def test_a_session_attached_by_hand_is_on_a_branch_fleet_did_not_cut(queue_dir, repo):
    record = task(queue_dir, repo, state="queued", reaped=None)
    ok(q("attach", "plans/01-plan-it", SID, "--unprompted"))
    doc = yaml.safe_load(record.read_text(encoding="utf-8"))
    assert doc["attached"] is True
    doc.update({"state": "landed", "session": None, "reaped": EARLIER})
    write(record, yaml.safe_dump(doc, sort_keys=False))
    expect(ok(q("reap")).out, "kept", BRANCH, "fleet did not cut it (attached)")
    assert has_branch(repo)
    assert released(record) == "kept"


def test_head_and_origins_default_branch_are_never_deleted(queue_dir, repo):
    head = task(queue_dir, repo, slug="head-it", branch="plan/head")
    doc = yaml.safe_load(head.read_text(encoding="utf-8"))
    doc["branch"] = "HEAD"
    write(head, yaml.safe_dump(doc, sort_keys=False))
    default = task(queue_dir, repo, branch="trunk")
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk", cwd=repo)
    expect(ok(q("reap")).out, "branch HEAD", "it is a base branch", "branch trunk",
           "it is origin's default branch")
    assert has_branch(repo, "trunk")
    assert released(head) == "kept" and released(default) == "kept"


def test_a_symbolic_branch_is_kept(queue_dir, repo):
    record = task(queue_dir, repo)
    git("branch", "-D", BRANCH, cwd=repo)
    git("symbolic-ref", f"refs/heads/{BRANCH}", "refs/heads/main", cwd=repo)
    expect(ok(q("reap")).out, "kept", BRANCH, "it is a symbolic ref to refs/heads/main")
    assert git("rev-parse", "--verify", "main", cwd=repo)
    assert released(record) == "kept"


def test_a_task_that_never_had_a_session_is_not_touched(queue_dir, repo):
    record = task(queue_dir, repo, reaped=None)
    refute(ok(q("reap")).out, BRANCH)
    assert has_branch(repo)
    assert released(record) is None


def test_a_branch_on_the_local_base_only_is_kept(queue_dir, repo):
    """Only origin's copy of the base counts: a local one can hold commits
    nobody pushed, and can even be the branch itself."""
    git("commit", "-q", "--allow-empty", "-m", "never pushed", cwd=repo)
    record = task(queue_dir, repo, base="main")
    git("branch", "-f", BRANCH, "main", cwd=repo)
    expect(ok(q("reap")).out, "kept", "1 commit(s) not on origin/main")
    assert has_branch(repo)
    assert released(record) == "kept"


@pytest.mark.parametrize("base", ["origin/release", "release", "refs/heads/release"])
def test_a_branch_that_is_its_own_base_is_kept(queue_dir, repo, base):
    record = task(queue_dir, repo, branch="release", base="origin/main")
    doc = yaml.safe_load(record.read_text(encoding="utf-8"))
    doc["base"] = base
    write(record, yaml.safe_dump(doc, sort_keys=False))
    commit_on(repo, "release")
    expect(ok(q("reap")).out, "kept", "branch release", "it is a base branch")
    assert has_branch(repo, "release")
    assert released(record) == "kept"


def test_a_branch_with_a_commit_of_its_own_is_kept_and_says_why(queue_dir, repo):
    record = task(queue_dir, repo)
    commit_on(repo)
    expect(ok(q("reap")).out, "kept", f"branch {BRANCH} in {repo}: 1 commit(s) not on origin/main")
    assert has_branch(repo)
    assert released(record) == "kept"
    refute(ok(q("reap")).out, BRANCH)


def test_one_repository_keeping_its_branch_records_the_task_kept(queue_dir, repo, tmp_path):
    second = checkout(tmp_path, "lib")
    record = task(queue_dir, repo, "--add-repo", str(second))
    commit_on(second)
    expect(ok(q("reap")).out, f"deleted    branch {BRANCH} in {repo}",
           f"kept       branch {BRANCH} in {second}: 1 commit(s)")
    assert not has_branch(repo)
    assert has_branch(second)
    assert released(record) == "kept"


def test_a_branch_checked_out_in_a_worktree_is_asked_again(queue_dir, repo, tmp_path):
    record = task(queue_dir, repo)
    tree = tmp_path / "tree"
    git("worktree", "add", "-q", str(tree), BRANCH, cwd=repo)
    expect(ok(q("reap")).out, "kept", BRANCH, "checked out at", tree.name, "again next pass")
    assert released(record) is None
    # Asked again, and an unchanged reason is not said again.
    refute(ok(q("reap")).out, BRANCH)
    git("worktree", "remove", str(tree), cwd=repo)
    expect(ok(q("reap")).out, "deleted", BRANCH)
    assert not has_branch(repo)
    doc = yaml.safe_load(record.read_text(encoding="utf-8"))
    assert doc["branch_released"]["how"] == "deleted" and "branch_held" not in doc


def test_the_main_checkouts_own_branch_is_kept(queue_dir, repo):
    record = task(queue_dir, repo)
    git("checkout", "-q", BRANCH, cwd=repo)
    # git lists worktree paths with forward slashes, Windows included.
    expect(ok(q("reap")).out, "kept", BRANCH, f"checked out at {repo.as_posix()}")
    assert has_branch(repo)
    assert released(record) is None


def test_a_directory_inside_a_checkout_does_not_answer_for_it(queue_dir, repo):
    inner = repo / "sub"
    inner.mkdir()
    record = task(queue_dir, repo)
    doc = yaml.safe_load(record.read_text(encoding="utf-8"))
    doc["repo"] = str(inner)
    write(record, yaml.safe_dump(doc, sort_keys=False))
    expect(ok(q("reap")).out, "kept", "is not the top of a checkout")
    assert has_branch(repo)


def test_a_base_origin_no_longer_has_is_final(queue_dir, repo):
    """A merged stacked layer's base, which the forge deleted with the merge."""
    record = task(queue_dir, repo)
    doc = yaml.safe_load(record.read_text(encoding="utf-8"))
    doc["base"] = "origin/nowhere"
    write(record, yaml.safe_dump(doc, sort_keys=False))
    expect(ok(q("reap")).out, "kept", BRANCH, "origin has no nowhere")
    refute(ok(q("reap")).out, BRANCH)
    assert has_branch(repo)
    assert released(record) == "kept"


def test_an_origin_that_cannot_be_reached_is_asked_again_and_said_once(queue_dir, repo, tmp_path):
    record = task(queue_dir, repo)
    url = git("remote", "get-url", "origin", cwd=repo).strip()
    git("remote", "set-url", "origin", str(tmp_path / "gone.git"), cwd=repo)
    expect(ok(q("reap")).out, "kept", BRANCH, "could not fetch origin/main", "again next pass")
    assert released(record) is None
    refute(ok(q("reap")).out, BRANCH)
    git("remote", "set-url", "origin", url, cwd=repo)
    expect(ok(q("reap")).out, "deleted", BRANCH)
    assert released(record) == "deleted"


def test_a_merge_origin_has_and_this_checkout_has_not_fetched_counts(queue_dir, repo, tmp_path):
    """The base is fetched first, so a branch merged a moment ago is on it."""
    record = task(queue_dir, repo)
    commit_on(repo)
    git("push", "-q", "origin", f"{BRANCH}:main", cwd=repo)
    git("update-ref", "refs/remotes/origin/main", "main", cwd=repo)
    expect(ok(q("reap")).out, "deleted", BRANCH)
    assert released(record) == "deleted"


def test_a_branch_already_gone_is_recorded_and_not_spoken_of(queue_dir, repo):
    record = task(queue_dir, repo)
    git("branch", "-D", BRANCH, cwd=repo)
    refute(ok(q("reap")).out, BRANCH)
    assert released(record) == "gone"


def test_a_dry_run_deletes_nothing_and_records_nothing(queue_dir, repo):
    record = task(queue_dir, repo)
    expect(ok(q("reap", "--dry-run")).out, "would delete", f"branch {BRANCH} in {repo}")
    assert has_branch(repo)
    assert released(record) is None


def test_a_dry_run_reads_a_session_it_would_reap_as_gone(queue_dir, repo, stubs, tmp_path):
    """The session's own worktree goes with it, so it is no reason to keep."""
    record = task(queue_dir, repo, session=SID, reaped=None)
    tree = tmp_path / "tree"
    git("worktree", "add", "-q", str(tree), BRANCH, cwd=repo)
    stubs.session_is(SID, "idle")
    path = stubs.root / "sessions" / f"{SID}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["worktrees"] = [{"worktree_path": str(tree), "created_by_thurbox": True}]
    write(path, json.dumps(doc))
    expect(ok(q("reap", "--dry-run")).out, "would reap", SID, "would delete", BRANCH)
    assert has_branch(repo)
    assert released(record) is None


def test_a_dry_run_reads_a_session_it_would_drop_as_gone(queue_dir, repo, stubs):
    record = task(queue_dir, repo, session=SID, reaped=None)
    expect(ok(q("reap", "--dry-run")).out, "would drop", SID, "would delete", BRANCH)
    assert has_branch(repo)
    assert released(record) is None


def test_a_commit_landing_after_the_check_keeps_the_branch(queue_dir, repo, stubs):
    """The delete names the tip it checked, so git refuses one that moved."""
    record = task(queue_dir, repo)
    real = shutil.which("git")
    stubs.tool("git", (
        "import subprocess, sys\n"
        f"real, repo, branch = {real!r}, {str(repo)!r}, {BRANCH!r}\n"
        "if 'update-ref' in sys.argv and '-d' in sys.argv:\n"
        "    tree = subprocess.run([real, '-C', repo, 'rev-parse', branch + '^{tree}'],\n"
        "                          capture_output=True, text=True).stdout.strip()\n"
        "    moved = subprocess.run([real, '-C', repo, 'commit-tree', tree, '-p', branch, '-m', 'late'],\n"
        "                           capture_output=True, text=True).stdout.strip()\n"
        "    subprocess.run([real, '-C', repo, 'update-ref', 'refs/heads/' + branch, moved], check=True)\n"
        "sys.exit(subprocess.run([real, *sys.argv[1:]]).returncode)\n"
    ))
    expect(ok(q("reap")).out, "kept", BRANCH, "git refused the delete")
    assert has_branch(repo)
    assert released(record) is None


@pytest.mark.skipif(os.name == "nt", reason="the fake host's login shell is a real /bin/sh, which Windows has not got")
def test_a_host_tasks_branch_is_deleted_on_its_host(queue_dir, repo, stubs):
    """The host's shell is a real one over this machine's disk, so the same
    repository stands for the host's checkout."""
    hosts = Hosts(stubs)
    record = task(queue_dir, repo, "--host", "profilebox")
    expect(ok(q("reap", "--dry-run")).out, "would delete", BRANCH)
    assert has_branch(repo)
    expect(ok(q("reap")).out, "deleted", BRANCH)
    assert not has_branch(repo)
    assert released(record) == "deleted"
    # The whole log: a remote script spans lines, and `calls` reads only the first.
    expect((stubs.root / "calls.log").read_text(encoding="utf-8"), "update-ref",
           "core.hooksPath=/dev/null")
    del hosts


@pytest.mark.skipif(os.name == "nt", reason="the fake host's login shell is a real /bin/sh, which Windows has not got")
def test_a_host_that_does_not_answer_is_asked_once_and_again_next_pass(queue_dir, repo, stubs):
    hosts = Hosts(stubs)
    first = task(queue_dir, repo, "--host", "profilebox")
    second = task(queue_dir, repo, "--host", "profilebox", slug="plan-more", branch="plan/topic-2")
    hosts.flag("me@profilebox", "down")
    out = ok(q("reap")).out
    expect(out, "kept", BRANCH, "No route to host", "again next pass")
    refute(out, "plan/topic-2")
    assert len(stubs.calls("ssh")) == 1
    assert released(first) is None and released(second) is None
    hosts.unflag("me@profilebox", "down")
    expect(ok(q("reap")).out, f"deleted    branch {BRANCH}", "deleted    branch plan/topic-2")
    assert released(first) == "deleted" and released(second) == "deleted"


@pytest.mark.skipif(os.name == "nt", reason="the fake host's login shell is a real /bin/sh, which Windows has not got")
def test_a_checkout_missing_on_its_host_is_gone(queue_dir, repo, stubs, tmp_path):
    Hosts(stubs)
    record = task(queue_dir, repo, "--host", "profilebox")
    doc = yaml.safe_load(record.read_text(encoding="utf-8"))
    doc["repo"] = str(tmp_path / "not-there")
    write(record, yaml.safe_dump(doc, sort_keys=False))
    refute(ok(q("reap")).out, BRANCH)
    assert released(record) == "gone"


def test_a_host_no_longer_configured_is_recorded_kept(queue_dir, repo):
    record = task(queue_dir, repo, host="gonebox")
    expect(ok(q("reap")).out, "kept", BRANCH, "host gonebox is not configured")
    assert released(record) == "kept"
    assert has_branch(repo)


def test_powershell_refuses_a_path_it_cannot_pass_to_git():
    out = queue_module(
        "for repo in ('C:\\\\src\\\\widget', 'C:\\\\a\"b', 'C:\\\\a\\nb'):\n"
        "    print(bool(q.POWERSHELL.git_refusal(repo)))\n"
        "try:\n"
        "    q.POWERSHELL.git('C:\\\\a\"b', ['status'])\n"
        "except ValueError:\n"
        "    print('refused')\n"
    )
    assert out.split() == ["False", "True", "True", "refused"], out


def test_git_is_never_run_with_the_repositorys_hooks(queue_dir, repo):
    task(queue_dir, repo)
    hook = repo / ".git" / "hooks" / "reference-transaction"
    write(hook, f"#!/bin/sh\necho ran > '{repo / 'hook-ran'}'\n")
    hook.chmod(0o755)
    expect(ok(q("reap")).out, "deleted", BRANCH)
    assert not (repo / "hook-ran").exists()
    assert subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
                           f"refs/heads/{BRANCH}"], capture_output=True).returncode == 1
