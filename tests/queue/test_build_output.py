"""A worktree the queue lets go of gives its build output back.

`session delete --force` takes a session's worktree with it. A session that is
already gone does not: its worktree stays on disk, and on one machine worker
Rust `target/` directories alone held 25-30 GB each until the root disk filled.
So when `reap` drops a session thurbox no longer has, it removes the
regenerable build output in the task's worktree — and only that: the worktree
itself may hold uncommitted work, a tracked directory that happens to be called
`target` is source, and a link to a shared store is somebody else's.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from queuekit import ok

from harness import expect, write
from harness import run_queue as q

SID = "99999999-9999-9999-9999-999999999999"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


@pytest.fixture
def worktree(queue_dir, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo / ".gitignore", "target/\nnode_modules/\n.venv/\n")
    write(repo / "README.md", "hello\n")
    write(repo / "docs" / "target" / "page.md", "a tracked directory that is called target\n")
    git(repo, "add", "-A")
    git(repo, "add", "-f", "docs/target/page.md")
    git(repo, "commit", "-q", "-m", "init")

    ok(q("topic", "add", "disk", "--title", "Give the disk back", "--prompt", "free build output"))
    ok(q("add", "disk", "build", "--title", "Build something big", "--repo", str(repo),
         "--branch", "fix/build", "--number", "01"))
    tree = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "fix/build", str(tree))

    write(tree / "target" / "debug" / "app", "x" * 1024)
    write(tree / "web" / "node_modules" / "pkg" / "index.js", "module.exports = 1\n")
    write(tree / ".venv" / "bin" / "python", "#!/bin/sh\n")
    write(tree / "notes.txt", "uncommitted work the worker left\n")
    shared = tmp_path / "shared-store"
    write(shared / "pkg" / "index.js", "shared\n")
    try:
        (tree / "node_modules").symlink_to(shared, target_is_directory=True)
    except OSError:
        pytest.skip("creating directory symlinks is unavailable")

    ok(q("attach", "disk/01-build", SID))
    return tree, shared


def assert_freed(tree: Path, shared: Path) -> None:
    assert not (tree / "target").exists(), "target/ was left behind"
    assert not (tree / "web" / "node_modules").exists(), "a nested node_modules was left behind"
    assert not (tree / ".venv").exists(), ".venv was left behind"
    # Everything that is not regenerable build output stays.
    assert (tree / "notes.txt").is_file(), "uncommitted work was deleted"
    assert (tree / "docs" / "target" / "page.md").is_file(), "a tracked `target` was deleted"
    assert os.path.islink(tree / "node_modules"), "the link to a shared store was removed"
    assert (shared / "pkg" / "index.js").is_file(), "a shared store's content was deleted"


def test_a_vanished_sessions_worktree_gives_its_build_output_back(worktree, queue_dir):
    tree, shared = worktree
    ok(q("collect"))
    assert (tree / "target").is_dir(), "one look is not an end, and nothing is freed on it"

    path = queue_dir / "disk" / "01-build" / "task.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["vanished"]["since"] = "2020-01-01T00:00:00+00:00"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    out = ok(q("collect")).out
    expect(out, "session gone", "freed", "target")
    assert_freed(tree, shared)


def test_a_task_abandoned_by_hand_frees_it_when_reap_drops_the_session(worktree):
    tree, shared = worktree
    ok(q("abandon", "disk/01-build", "--why", "superseded"))
    expect(ok(q("reap", "--dry-run")).out, "would free")
    assert (tree / "target").is_dir(), "a dry run deleted build output"
    expect(ok(q("reap")).out, "gone", "freed")
    assert_freed(tree, shared)


def test_a_worktree_another_session_sits_in_keeps_its_build_output(worktree, stubs):
    """Someone carrying on in that worktree may be building in it right now."""
    tree, _shared = worktree
    write(stubs.root / "sessions" / "by-hand.json", json.dumps({
        "id": "by-hand", "name": "carrying on", "state": "working",
        "cwd": str(tree / "web"), "backend_type": "local:tmux", "worktrees": [],
    }))
    ok(q("abandon", "disk/01-build", "--why", "superseded"))
    expect(ok(q("reap")).out, "gone", "kept", "by-hand")
    assert (tree / "target").is_dir(), "build output was freed under a live session"
