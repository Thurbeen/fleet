"""Reap must not take another session's working directory away."""

import json

import pytest
import yaml
from queuekit import S1, S2, ok, result

from harness import expect, refute, write
from harness import run_queue as q

OTHER = "manual-review-session"


def session(stubs, sid, **fields):
    path = stubs.root / "sessions" / f"{sid}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.update(fields)
    write(path, json.dumps(doc))


@pytest.fixture
def landed(attached, stubs, queue_dir, tmp_path):
    stubs.session_is(S1, "idle")
    stubs.session_is(S2, "working")
    stubs.pipeline_pr(999, "fix/drop-idle-default")
    stubs.pr_state(999, "MERGED")
    task = queue_dir / attached / "01-drop-idle-default"
    result(task, "shipped", "Finished.", "https://github.com/Thurbeen/thurbox/pull/999")
    ok(q("collect", "--no-reap"))
    tree = tmp_path / "owned"
    tree.mkdir()
    (tree / "src").mkdir()
    (tmp_path / "unrelated").mkdir()
    session(stubs, S1, cwd=str(tree), backend_type="local-tmux", worktrees=[{
        "worktree_path": str(tree), "created_by_thurbox": True,
    }])
    session(stubs, S2, cwd=str(tmp_path / "unrelated"), backend_type="local-tmux")
    stubs.session_is(OTHER, "working")
    session(stubs, OTHER, cwd=str(tree / "src"), backend_type="local-tmux")
    return task, tree


def assert_kept(stubs, task):
    assert not stubs.calls("thurbox-cli", "session delete")
    doc = yaml.safe_load((task / "task.yaml").read_text(encoding="utf-8"))
    assert doc["state"] == "landed"
    assert doc["session"] == S1
    assert "reaped" not in doc


@pytest.mark.parametrize("state", ["working", "idle", "done", "stopped", "uncovered", "unreported"])
@pytest.mark.parametrize("nested", [False, True])
def test_reap_keeps_worktree_used_by_unqueued_session(landed, stubs, state, nested):
    task, tree = landed
    session(stubs, OTHER, state=state, cwd=str(tree / "src" if nested else tree))
    out = ok(q("reap")).out
    assert_kept(stubs, task)
    expect(out, "kept", S1, OTHER, str(tree))


def test_dry_run_reports_the_same_conflict(landed, stubs):
    task, tree = landed
    out = ok(q("reap", "--dry-run")).out
    assert_kept(stubs, task)
    expect(out, "kept", S1, OTHER, str(tree))
    refute(out, "would reap")


def test_retries_and_records_receipt_when_other_session_leaves(landed, stubs):
    task, _ = landed
    ok(q("reap"))
    assert_kept(stubs, task)
    (stubs.root / "sessions" / f"{OTHER}.json").unlink()
    expect(ok(q("reap")).out, "reaped", S1)
    expect("\n".join(stubs.calls("thurbox-cli", "session delete")), S1, "--force")
    doc = yaml.safe_load((task / "task.yaml").read_text(encoding="utf-8"))
    assert doc["session"] is None
    assert doc["reaped"]["session"] == S1
    assert doc["reaped"]["how"] == "deleted"


@pytest.mark.parametrize("cwd", [None, "", 42, "relative/path"])
def test_unknown_cwd_keeps_session(landed, stubs, cwd):
    task, _ = landed
    session(stubs, OTHER, cwd=cwd)
    expect(ok(q("reap")).out, "kept", "cwd", OTHER)
    assert_kept(stubs, task)


@pytest.mark.parametrize("worktrees", [None, {}, [None], [{}], [{
    "worktree_path": "relative", "created_by_thurbox": True,
}]])
def test_unknown_worktrees_keep_session(landed, stubs, worktrees):
    task, _ = landed
    session(stubs, S1, worktrees=worktrees)
    expect(ok(q("reap")).out, "kept", "worktree", S1)
    assert_kept(stubs, task)


def test_unreadable_session_list_keeps_session(landed, stubs):
    task, _ = landed
    write(stubs.root / "sessions" / "broken.json", "not JSON")
    expect(ok(q("reap")).out, "kept", "session list failed")
    assert_kept(stubs, task)


def test_sibling_prefix_is_not_inside_worktree(landed, stubs):
    _, tree = landed
    sibling = tree.with_name(tree.name + "-other")
    sibling.mkdir()
    session(stubs, OTHER, cwd=str(sibling))
    expect(ok(q("reap")).out, "reaped", S1)


def test_borrowed_worktree_is_not_removed_by_thurbox(landed, stubs):
    _, tree = landed
    session(stubs, S1, worktrees=[{"worktree_path": str(tree), "created_by_thurbox": False}])
    expect(ok(q("reap")).out, "reaped", S1)


def test_checks_every_owned_worktree_not_just_cwd(landed, stubs, tmp_path):
    task, tree = landed
    (tmp_path / "primary").mkdir()
    session(stubs, S1, cwd=str(tmp_path), worktrees=[
        {"worktree_path": str(tmp_path / "primary"), "created_by_thurbox": True},
        {"worktree_path": str(tree), "created_by_thurbox": True},
    ])
    expect(ok(q("reap")).out, "kept", S1, OTHER, str(tree))
    assert_kept(stubs, task)


@pytest.mark.parametrize("answer", ["not JSON", "null", "{}", '[null]', '[{}]',
                                    '[{"id":"same"},{"id":"same"}]'])
def test_invalid_snapshot_is_not_permission_to_delete(landed, stubs, answer):
    task, _ = landed
    write(stubs.root / "session-list.json", answer)
    expect(ok(q("reap")).out, "kept", "session list")
    assert_kept(stubs, task)


@pytest.mark.parametrize("field,value", [
    ("backend_type", None),
    ("worktrees", [{"worktree_path": "/example", "created_by_thurbox": "true"}]),
])
def test_unknown_target_metadata_is_not_permission_to_delete(landed, stubs, field, value):
    task, _ = landed
    (stubs.root / "sessions" / f"{OTHER}.json").unlink()
    session(stubs, S1, **{field: value})
    expect(ok(q("reap")).out, "kept", S1)
    assert_kept(stubs, task)


def test_absent_created_by_thurbox_is_owned(landed, stubs):
    """thurbox treats a missing flag as true on the wire; so does this check."""
    task, tree = landed
    session(stubs, S1, worktrees=[{"worktree_path": str(tree)}])
    expect(ok(q("reap")).out, "kept", S1, OTHER, str(tree))
    assert_kept(stubs, task)


REMOTE_TREE = "/srv/worktrees/app-1234/fix-build-on-devbox"


def _remote_target(stubs, task):
    (stubs.root / "sessions" / f"{OTHER}.json").unlink()
    session(stubs, S1, backend_type="ssh:devbox", cwd=REMOTE_TREE, worktrees=[{
        "worktree_path": REMOTE_TREE, "created_by_thurbox": True,
    }])
    return task


def test_remote_session_is_reaped_when_the_host_lists_no_occupant(landed, stubs):
    _remote_target(stubs, landed[0])
    expect(ok(q("reap")).out, "reaped", S1)
    expect("\n".join(stubs.calls("thurbox-cli", "session delete")), S1, "--force")


def test_remote_session_is_kept_when_the_host_lists_an_occupant(landed, stubs):
    task = _remote_target(stubs, landed[0])
    write(stubs.root / "ssh-state" / "me@devbox.session-list.json", json.dumps([
        {"id": S1, "cwd": REMOTE_TREE, "backend_type": "local-tmux",
         "worktrees": [{"worktree_path": REMOTE_TREE, "created_by_thurbox": True}]},
        {"id": OTHER, "cwd": REMOTE_TREE + "/src", "backend_type": "local-tmux"},
    ]))
    out = ok(q("reap")).out
    expect(out, "kept", S1, OTHER, REMOTE_TREE)
    assert_kept(stubs, task)


def test_host_list_without_this_sessions_id_is_not_an_occupant(landed, stubs):
    """A host row in the worktree with a different id is not proof of occupancy.

    Excluding the target by id only works when the host lists that same id. If
    it does not, treating the other row as an occupant keeps the session
    forever under the occupancy line — the shape this revision exists to close.
    """
    task = _remote_target(stubs, landed[0])
    write(stubs.root / "ssh-state" / "me@devbox.session-list.json", json.dumps([
        {"id": OTHER, "cwd": REMOTE_TREE, "backend_type": "local-tmux"},
    ]))
    out = ok(q("reap")).out
    expect(out, "kept", "has no row", S1)
    refute(out, f"session {OTHER} uses worktree")
    assert_kept(stubs, task)


def test_unlistable_host_session_list_keeps_the_remote_session(landed, stubs):
    task = _remote_target(stubs, landed[0])
    write(stubs.root / "ssh-state" / "me@devbox.session-list.json", "Welcome\nnot json")
    expect(ok(q("reap")).out, "kept", "session list on host", S1)
    assert_kept(stubs, task)


def test_unknown_backend_does_not_promise_a_later_pass(landed, stubs):
    task, _ = landed
    (stubs.root / "sessions" / f"{OTHER}.json").unlink()
    session(stubs, S1, backend_type="mystery")
    out = ok(q("reap")).out
    expect(out, "kept", "cannot judge backend", S1)
    refute(out, "next pass", "retry")
    assert_kept(stubs, task)


def test_missing_cwd_directory_is_unknown(landed, stubs, tmp_path):
    task, _ = landed
    session(stubs, OTHER, cwd=str(tmp_path / "missing"))
    expect(ok(q("reap")).out, "kept", "cwd", OTHER)
    assert_kept(stubs, task)


def test_wsl_session_in_the_worktree_is_protected(landed, stubs):
    task, tree = landed
    session(stubs, OTHER, backend_type="wsl:Ubuntu", cwd=str(tree / "src"))
    expect(ok(q("reap")).out, "kept", S1, OTHER, str(tree))
    assert_kept(stubs, task)


def test_unresolvable_remote_cwd_does_not_block_a_local_reap(landed, stubs):
    _, _tree = landed
    session(stubs, OTHER, backend_type="ssh:devbox", cwd="/no-such-host/worktree")
    expect(ok(q("reap")).out, "reaped", S1)


def test_symlink_cwd_inside_worktree_is_protected(landed, stubs, tmp_path):
    task, tree = landed
    link = tmp_path / "alias"
    try:
        link.symlink_to(tree, target_is_directory=True)
    except OSError:
        pytest.skip("creating directory symlinks is unavailable")
    session(stubs, OTHER, cwd=str(link / "src"))
    expect(ok(q("reap")).out, "kept", S1, OTHER, str(tree))
    assert_kept(stubs, task)


@pytest.mark.parametrize("second_answer", ["new-session", "invalid"])
def test_refreshes_snapshot_immediately_before_release(landed, stubs, second_answer):
    task, tree = landed
    stubs.tool("thurbox-cli", f'''
import json
import sys
from fleet_stubs import root, read
where = root()
args = sys.argv[1:]
rows = [json.loads(read(p)) for p in (where / "sessions").glob("*.json")]
if args[:2] == ["session", "list"]:
    calls = read(where / "calls.log").count("thurbox-cli session list")
    if calls == 1:
        print(json.dumps([row for row in rows if row["id"] != {OTHER!r}]))
    elif {second_answer!r} == "invalid":
        print("null")
    else:
        print(json.dumps(rows))
elif args[:2] == ["session", "get"]:
    print(read(where / "sessions" / (args[2] + ".json")))
''')
    out = ok(q("reap")).out
    assert_kept(stubs, task)
    expect(out, "kept", S1)
    if second_answer == "new-session":
        expect(out, OTHER, str(tree))
    else:
        expect(out, "session list did not answer a list")
