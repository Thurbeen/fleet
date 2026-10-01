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


# thurbox now writes every route qualified — `<machine>:<mux>` — and still
# reads the older spellings; `src/session/route.rs` in thurbox is the grammar.
# A reap that knew only `local-tmux` kept every newly spawned session forever.

@pytest.fixture
def abandoned_typed(attached, stubs, queue_dir, tmp_path):
    """An abandoned task whose stopped worker owns a worktree, on a typed local route."""
    task = queue_dir / attached / "01-drop-idle-default"
    stubs.session_is(S1, "stopped")
    stubs.session_is(S2, "working")
    ok(q("abandon", f"{attached}/01-drop-idle-default", "--why", "moot", "--force"))
    tree = tmp_path / "typed"
    (tree / "src").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    session(stubs, S1, cwd=str(tree), backend_type="local:tmux", worktrees=[{
        "worktree_path": str(tree), "created_by_thurbox": True,
    }])
    session(stubs, S2, cwd=str(tmp_path / "elsewhere"), backend_type="local:tmux")
    return task, tree


def assert_abandoned_kept(stubs, task):
    assert not stubs.calls("thurbox-cli", "session delete")
    doc = yaml.safe_load((task / "task.yaml").read_text(encoding="utf-8"))
    assert doc["state"] == "abandoned"
    assert doc["session"] == S1


def test_typed_local_route_is_reaped_when_nothing_else_uses_its_worktree(abandoned_typed, stubs):
    out = ok(q("reap")).out
    refute(out, "cannot judge backend")
    expect(out, "reaped", S1)
    expect("\n".join(stubs.calls("thurbox-cli", "session delete")), S1, "--force")


@pytest.mark.parametrize("route", ["local:tmux", "local:psmux", "local-tmux", "tmux", ""])
def test_typed_local_route_is_kept_while_another_session_uses_its_worktree(
        abandoned_typed, stubs, route):
    task, tree = abandoned_typed
    stubs.session_is(OTHER, "working")
    session(stubs, OTHER, cwd=str(tree / "src"), backend_type=route)
    out = ok(q("reap")).out
    expect(out, "kept", S1, OTHER, str(tree))
    assert_abandoned_kept(stubs, task)


@pytest.mark.parametrize("route", ["local:screen", "local:", "ssh:", "wsl:", "ssh::tmux", "local-mystery"])
def test_a_route_naming_nothing_still_refuses(abandoned_typed, stubs, route):
    task, _ = abandoned_typed
    stubs.session_is(OTHER, "working")
    session(stubs, OTHER, backend_type=route)
    expect(ok(q("reap")).out, "kept", "cannot judge backend", OTHER)
    assert_abandoned_kept(stubs, task)


@pytest.mark.parametrize("route", ["ssh:devbox:tmux", "wsl:Ubuntu:tmux"])
def test_a_typed_remote_occupant_off_this_filesystem_does_not_block(abandoned_typed, stubs, route):
    stubs.session_is(OTHER, "working")
    session(stubs, OTHER, backend_type=route, cwd="/no-such-host/worktree")
    expect(ok(q("reap")).out, "reaped", S1)


def test_a_typed_wsl_occupant_inside_the_worktree_is_protected(abandoned_typed, stubs):
    task, tree = abandoned_typed
    stubs.session_is(OTHER, "working")
    session(stubs, OTHER, backend_type="wsl:Ubuntu:tmux", cwd=str(tree / "src"))
    expect(ok(q("reap")).out, "kept", S1, OTHER, str(tree))
    assert_abandoned_kept(stubs, task)


def test_typed_ssh_route_is_judged_on_its_own_host(landed, stubs):
    """`ssh:devbox:tmux` is host `devbox`, not a host called `devbox:tmux`."""
    task = _remote_target(stubs, landed[0])
    session(stubs, S1, backend_type="ssh:devbox:tmux")
    write(stubs.root / "ssh-state" / "me@devbox.session-list.json", json.dumps([
        {"id": S1, "cwd": REMOTE_TREE, "backend_type": "local:tmux",
         "worktrees": [{"worktree_path": REMOTE_TREE, "created_by_thurbox": True}]},
        {"id": OTHER, "cwd": REMOTE_TREE + "/src", "backend_type": "local:tmux"},
    ]))
    out = ok(q("reap")).out
    expect(out, "kept", S1, OTHER, REMOTE_TREE)
    refute(out, "devbox:tmux")
    assert_kept(stubs, task)
    (stubs.root / "ssh-state" / "me@devbox.session-list.json").unlink()
    expect(ok(q("reap")).out, "reaped", S1)


def test_typed_wsl_target_is_reaped_when_unoccupied(landed, stubs):
    (stubs.root / "sessions" / f"{OTHER}.json").unlink()
    session(stubs, S1, backend_type="wsl:Ubuntu:tmux", cwd="/home/user/tree", worktrees=[{
        "worktree_path": "/home/user/tree", "created_by_thurbox": True,
    }])
    expect(ok(q("reap")).out, "reaped", S1)


# A host's OS is `hosts.toml`'s `platform`, independent of its multiplexer;
# only when it is unset does `multiplexer = "psmux"` mean Windows (thurbox's
# `host_config.rs`). The route's own multiplexer never says it: thurbox serves
# psmux on a POSIX host and tmux on a Windows one alike.
PLATFORM_HOSTS = (
    '[[hosts]]\nname = "pxbox"\ndestination = "me@pxbox"\n'
    'multiplexer = "psmux"\nplatform = "posix"\n'
    '[[hosts]]\nname = "wtbox"\ndestination = "me@wtbox"\n'
    'multiplexer = "tmux"\nplatform = "windows"\n'
    '[[hosts]]\nname = "oddbox"\ndestination = "me@oddbox"\nplatform = "beos"\n'
)
POSIX_TREE = "/srv/Tree"
WINDOWS_TREE = "C:\\w\\Tree"


def _platform_target(stubs, host, route, tree, occupant_cwd=None):
    write(stubs.root / "hosts.toml", PLATFORM_HOSTS)
    (stubs.root / "sessions" / f"{OTHER}.json").unlink()
    session(stubs, S1, backend_type=route, cwd=tree, worktrees=[{
        "worktree_path": tree, "created_by_thurbox": True,
    }])
    rows = [{"id": S1, "cwd": tree, "backend_type": "local:tmux"}]
    if occupant_cwd:
        rows.append({"id": OTHER, "cwd": occupant_cwd, "backend_type": "local:tmux"})
    write(stubs.root / "ssh-state" / f"me@{host}.session-list.json", json.dumps(rows))
    if host == "wtbox":
        # A host that answers only what a PowerShell sshd answers.
        write(stubs.root / "ssh-state" / "me@wtbox.windows", "")


@pytest.mark.parametrize("route", ["ssh:pxbox:psmux", "ssh:pxbox:tmux", "ssh:pxbox"])
def test_a_posix_host_running_psmux_is_judged_by_posix_paths(landed, stubs, route):
    """POSIX paths are case-sensitive: `/srv/tree` is not inside `/srv/Tree`."""
    _platform_target(stubs, "pxbox", route, POSIX_TREE, "/srv/tree/src")
    expect(ok(q("reap")).out, "reaped", S1)


@pytest.mark.parametrize("route", ["ssh:pxbox:psmux", "ssh:pxbox"])
def test_a_posix_host_running_psmux_still_protects_its_occupant(landed, stubs, route):
    task = landed[0]
    _platform_target(stubs, "pxbox", route, POSIX_TREE, POSIX_TREE + "/src")
    expect(ok(q("reap")).out, "kept", S1, OTHER, POSIX_TREE)
    assert_kept(stubs, task)


@pytest.mark.parametrize("route", ["ssh:wtbox:tmux", "ssh:wtbox:psmux", "ssh:wtbox"])
def test_a_windows_host_running_tmux_is_judged_by_windows_paths(landed, stubs, route):
    _platform_target(stubs, "wtbox", route, WINDOWS_TREE)
    expect(ok(q("reap")).out, "reaped", S1)


@pytest.mark.parametrize("route", ["ssh:wtbox:tmux", "ssh:wtbox"])
def test_a_windows_host_running_tmux_protects_an_occupant_spelled_in_another_case(landed, stubs, route):
    task = landed[0]
    _platform_target(stubs, "wtbox", route, WINDOWS_TREE, "c:\\w\\tree\\src")
    expect(ok(q("reap")).out, "kept", S1, OTHER, WINDOWS_TREE)
    assert_kept(stubs, task)


def test_a_host_platform_fleet_cannot_name_is_refused(landed, stubs):
    task = landed[0]
    _platform_target(stubs, "oddbox", "ssh:oddbox:tmux", POSIX_TREE)
    expect(ok(q("reap")).out, "kept", S1, "beos")
    assert_kept(stubs, task)


# The same rule with no `platform` key at all: a route whose multiplexer is
# not the host's still lives on the host's platform.
MUX_HOSTS = (
    '[[hosts]]\nname = "devbox"\ndestination = "me@devbox"\n'
    '[[hosts]]\nname = "winbox"\ndestination = "me@winbox"\nmultiplexer = "psmux"\n'
)


def test_a_tmux_route_on_a_psmux_host_is_judged_by_windows_paths(landed, stubs):
    _platform_target(stubs, "winbox", "ssh:winbox:tmux", WINDOWS_TREE)
    write(stubs.root / "hosts.toml", MUX_HOSTS)
    write(stubs.root / "ssh-state" / "me@winbox.windows", "")
    expect(ok(q("reap")).out, "reaped", S1)


def test_a_psmux_route_on_a_tmux_host_is_judged_by_posix_paths(landed, stubs):
    _platform_target(stubs, "devbox", "ssh:devbox:psmux", POSIX_TREE, "/srv/tree/src")
    write(stubs.root / "hosts.toml", MUX_HOSTS)
    expect(ok(q("reap")).out, "reaped", S1)
