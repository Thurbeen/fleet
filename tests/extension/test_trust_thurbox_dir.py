"""Seeding Claude Code's directory trust: the fallback to answering the dialog.

Trust lives in ~/.claude.json as `.projects["<absolute path>"]
.hasTrustDialogAccepted = true`, keyed by exact absolute path. The seed keeps
every other key, refuses a relative path before writing anything, and leaves
the file untouched when it cannot read it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from harness import expect, run_fleet, write


def claude_json(doc: dict) -> Path:
    path = Path(os.environ["HOME"]) / ".claude.json"
    write(path, json.dumps(doc))
    return path


def test_each_path_is_trusted_and_everything_else_kept(tmp_path):
    one, two = str(tmp_path / "w1"), str(tmp_path / "w2")
    path = claude_json({"projects": {one: {"allowedTools": ["x"]}}, "numStartups": 3})
    done = run_fleet("trust-thurbox-dir", one, two)
    assert done.code == 0, done.out
    expect(done.out, f"trusted  {one}", f"trusted  {two}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["projects"][one] == {"allowedTools": ["x"], "hasTrustDialogAccepted": True}
    assert doc["projects"][two] == {"hasTrustDialogAccepted": True}
    assert doc["numStartups"] == 3
    assert [p.name for p in path.parent.iterdir() if p.name.startswith(".claude.json")] == [".claude.json"]


def test_a_relative_path_is_refused_and_nothing_written():
    path = claude_json({"projects": {}})
    before = path.read_bytes()
    done = run_fleet("trust-thurbox-dir", "relative/dir")
    assert done.code == 1, done.out
    expect(done.out, "path must be absolute")
    assert path.read_bytes() == before


def test_claude_json_is_the_one_named_by_the_environment(tmp_path):
    other = tmp_path / "other.json"
    write(other, "{}")
    done = run_fleet("trust-thurbox-dir", str(tmp_path / "w"), CLAUDE_JSON=str(other))
    assert done.code == 0, done.out
    assert json.loads(other.read_text(encoding="utf-8"))["projects"][str(tmp_path / "w")]["hasTrustDialogAccepted"]


def test_a_missing_or_unreadable_file_is_an_error_and_left_alone(tmp_path):
    missing = run_fleet("trust-thurbox-dir", str(tmp_path / "w"))
    assert missing.code == 1
    expect(missing.out, "no such file")
    path = claude_json({})
    write(path, "{not json")
    done = run_fleet("trust-thurbox-dir", str(tmp_path / "w"))
    assert done.code == 1, done.out
    assert path.read_text(encoding="utf-8") == "{not json"


def test_all_worktrees_trusts_every_worktree_two_levels_down(tmp_path):
    trees = tmp_path / "worktrees"
    for rel in ("a/one", "a/two", "b/three"):
        (trees / rel).mkdir(parents=True)
    write(trees / "a" / "not-a-dir", "")
    path = claude_json({"projects": {}})
    done = run_fleet("trust-thurbox-dir", "--all-worktrees", THURBOX_WORKTREES=str(trees))
    assert done.code == 0, done.out
    projects = json.loads(path.read_text(encoding="utf-8"))["projects"]
    assert sorted(projects) == sorted(str(trees / rel) for rel in ("a/one", "a/two", "b/three"))


def test_all_worktrees_defaults_to_thurboxs_own_data_directory():
    trees = Path(os.environ["XDG_DATA_HOME"]) / "thurbox" / "worktrees"
    (trees / "repo" / "tree").mkdir(parents=True)
    path = claude_json({})
    done = run_fleet("trust-thurbox-dir", "--all-worktrees")
    assert done.code == 0, done.out
    assert str(trees / "repo" / "tree") in json.loads(path.read_text(encoding="utf-8"))["projects"]


def test_no_worktrees_says_so_and_exits_0(tmp_path):
    (tmp_path / "worktrees").mkdir()
    claude_json({})
    done = run_fleet("trust-thurbox-dir", "--all-worktrees", THURBOX_WORKTREES=str(tmp_path / "worktrees"))
    assert done.code == 0, done.out
    expect(done.out, "no worktrees under")


def test_no_argument_is_a_usage_error():
    claude_json({})
    done = run_fleet("trust-thurbox-dir")
    assert done.code == 1
    expect(done.out, "usage")
