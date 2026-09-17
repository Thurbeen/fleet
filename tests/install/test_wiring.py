"""The two pieces of wiring `fleet install` owns: the skills link and the worker Stop nudge.

THE SKILLS LINK. `.claude/skills` is not tracked, because a default Windows
clone checks a tracked link out as a text file holding its target, and Claude
Code then found no skills. So the install makes it: a symlink on POSIX, a
junction on Windows. It replaces that text file, leaves a correct link alone,
and never touches a real directory with something in it.

THE STOP NUDGE. thurbox rewrites its own hooks file from its embedded payload
at every TUI start and every automation tick (its docs/CONFIG.md, and
`materialize_source` in src/session_ops/builtin.rs), so an entry merged into
it is gone within a minute. What thurbox documents as surviving is the agent's
OWN settings, which claude merges with the file thurbox passes: "your own
hooks still fire inside a thurbox session — both run". So the nudge goes into
Claude Code's user settings, merged in beside whatever the operator keeps
there, found again by its command and never duplicated.
"""

from __future__ import annotations

import json
import os

import pytest
from harness import write
from installkit import WINDOWS, load_install

TEXT_LINK = "../.agents/skills"


def settings_file(isolated_env):
    return isolated_env / "home" / ".claude" / "settings.json"


# --- the skills link ------------------------------------------------------------


def test_an_absent_link_is_created_and_resolves_to_the_one_tree(checkout):
    install = load_install()
    assert install.link_state(str(checkout))[0] == "create"
    ok, _ = install.make_link(str(checkout))
    assert ok
    link = checkout / ".claude" / "skills"
    assert link.resolve() == (checkout / ".agents" / "skills").resolve()
    assert (link / "fleet-queue" / "SKILL.md").is_file()
    assert install.link_state(str(checkout))[0] == "ok"


def test_the_text_file_a_default_windows_clone_leaves_is_replaced(checkout):
    link = checkout / ".claude" / "skills"
    write(link, TEXT_LINK)
    install = load_install()
    assert install.link_state(str(checkout))[0] == "replace"
    assert install.make_link(str(checkout))[0]
    assert (link / "fleet-queue" / "SKILL.md").is_file()


def test_a_correct_link_is_left_alone(checkout):
    install = load_install()
    install.make_link(str(checkout))
    link = checkout / ".claude" / "skills"
    before = os.lstat(link)
    ok, message = install.make_link(str(checkout))
    assert ok and "already" in message
    after = os.lstat(link)
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)


def test_a_link_pointing_somewhere_else_is_repointed(checkout, tmp_path):
    install = load_install()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    fp = install.fleet_platform
    fp.make_dir_link(str(checkout / ".claude" / "skills"), str(elsewhere))
    assert install.link_state(str(checkout))[0] == "replace"
    assert install.make_link(str(checkout))[0]
    assert (checkout / ".claude" / "skills").resolve() == (checkout / ".agents" / "skills").resolve()


def test_a_real_directory_with_content_is_refused_and_kept(checkout):
    mine = checkout / ".claude" / "skills" / "mine" / "SKILL.md"
    write(mine, "an operator's own skill\n")
    install = load_install()
    assert install.link_state(str(checkout))[0] == "refuse"
    ok, message = install.make_link(str(checkout))
    assert not ok and ".claude/skills" in message
    assert mine.read_text(encoding="utf-8") == "an operator's own skill\n"


def test_another_text_file_is_refused_and_kept(checkout):
    link = checkout / ".claude" / "skills"
    write(link, "notes\n")
    install = load_install()
    assert install.link_state(str(checkout))[0] == "refuse"
    assert not install.make_link(str(checkout))[0]
    assert link.read_text(encoding="utf-8") == "notes\n"


def test_the_link_leaves_the_tree_clean(checkout):
    from installkit import git

    install = load_install()
    install.make_link(str(checkout))
    assert git("status", "--porcelain", cwd=checkout) == ""


@pytest.mark.skipif(WINDOWS or os.geteuid() == 0, reason="a read-only directory stops a link only for a non-root POSIX user")
def test_a_link_the_filesystem_refuses_is_a_reported_failure_not_a_crash(checkout):
    """A checkout on a volume with no symlinks or junctions: the install still
    reports, and still reaches the hook, the extension and preflight."""
    parent = checkout / ".claude"
    parent.mkdir(exist_ok=True)
    parent.chmod(0o555)
    try:
        install = load_install()
        ok, message = install.make_link(str(checkout))
    finally:
        parent.chmod(0o755)
    assert not ok
    assert ".claude/skills" in message


@pytest.mark.skipif(not WINDOWS, reason="the junction branch")
def test_on_windows_the_link_is_a_junction(checkout):
    import stat

    install = load_install()
    install.make_link(str(checkout))
    assert os.lstat(checkout / ".claude" / "skills").st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT


# --- the Stop nudge -------------------------------------------------------------


def nudge(checkout) -> str:
    from fleet.cli import load

    return load("reconcile.py").hook_command(str(checkout))


def stop_commands(path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [h["command"] for entry in data["hooks"]["Stop"] for h in entry["hooks"]]


def test_the_settings_file_is_claude_codes_user_settings(isolated_env, monkeypatch, tmp_path):
    install = load_install()
    assert install.claude_settings_file() == str(settings_file(isolated_env))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    assert install.claude_settings_file() == str(tmp_path / "claude" / "settings.json")


def test_an_absent_settings_file_gets_the_nudge(checkout, isolated_env):
    install = load_install()
    path = str(settings_file(isolated_env))
    command = nudge(checkout)
    assert install.hook_state(path, command)[0] == "add"
    assert install.apply_hook(path, command)[0]
    assert stop_commands(settings_file(isolated_env)) == [command]
    entry = json.loads(settings_file(isolated_env).read_text(encoding="utf-8"))["hooks"]["Stop"][0]["hooks"][0]
    assert entry == {"type": "command", "command": command, "timeout": 10}


def test_a_nudge_already_there_is_left_byte_for_byte(checkout, isolated_env):
    install = load_install()
    path = str(settings_file(isolated_env))
    command = nudge(checkout)
    install.apply_hook(path, command)
    before = settings_file(isolated_env).read_bytes()
    mtime = os.stat(path).st_mtime_ns
    assert install.hook_state(path, command)[0] == "ok"
    ok, message = install.apply_hook(path, command)
    assert ok and "already" in message
    assert settings_file(isolated_env).read_bytes() == before
    assert os.stat(path).st_mtime_ns == mtime


def test_the_operators_own_settings_and_hooks_are_kept(checkout, isolated_env):
    theirs = {
        "model": "their-choice",
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "their-stop-hook"}]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "their-guard"}]}],
        },
    }
    write(settings_file(isolated_env), json.dumps(theirs, indent=2) + "\n")
    install = load_install()
    command = nudge(checkout)
    assert install.apply_hook(str(settings_file(isolated_env)), command)[0]
    data = json.loads(settings_file(isolated_env).read_text(encoding="utf-8"))
    assert data["model"] == "their-choice"
    assert data["hooks"]["PreToolUse"] == theirs["hooks"]["PreToolUse"]
    assert stop_commands(settings_file(isolated_env)) == ["their-stop-hook", command]


def test_a_moved_checkouts_nudge_is_repointed_not_duplicated(checkout, isolated_env, tmp_path):
    """The checkout it names is GONE, so it is this fleet having moved and the
    hook is the only thing that still points at where it was."""
    install = load_install()
    path = str(settings_file(isolated_env))
    old = nudge(tmp_path / "old-place")
    install.apply_hook(path, old)
    assert install.hook_state(path, nudge(checkout))[0] == "update"
    assert install.apply_hook(path, nudge(checkout))[0]
    assert stop_commands(settings_file(isolated_env)) == [nudge(checkout)]


def test_a_second_fleets_nudge_is_kept_and_this_ones_added_beside_it(checkout, isolated_env, tmp_path):
    """A machine may run several fleets, and this file is ONE file shared by
    every worker on it. A worker does not know which fleet dispatched it — its
    Stop hook nudges every loop, and each loop reconciles its own queue. Taking
    the first fleet's nudge away, which is what repointing did, left that
    fleet's loop woken by nothing but its own timer."""
    other = tmp_path / "the-other-fleet"
    other.mkdir()
    install = load_install()
    path = str(settings_file(isolated_env))
    install.apply_hook(path, nudge(other))

    state, why = install.hook_state(path, nudge(checkout))
    assert state == "add", why
    assert install.apply_hook(path, nudge(checkout))[0]
    assert stop_commands(settings_file(isolated_env)) == [nudge(other), nudge(checkout)]

    # And re-running this fleet's install leaves both exactly as they are.
    assert install.hook_state(path, nudge(checkout))[0] == "ok"
    assert install.hook_state(path, nudge(other))[0] == "ok"


def test_a_settings_file_with_a_byte_order_mark_is_merged_into(checkout, isolated_env):
    """Windows PowerShell 5.1's `Set-Content -Encoding UTF8` writes one."""
    path = settings_file(isolated_env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"model": "their-choice"}).encode("utf-8"))
    install = load_install()
    assert install.hook_state(str(path), nudge(checkout))[0] == "add"
    assert install.apply_hook(str(path), nudge(checkout))[0]
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    assert data["model"] == "their-choice"
    assert stop_commands_of(data) == [nudge(checkout)]


def stop_commands_of(data: dict) -> list[str]:
    return [h["command"] for entry in data["hooks"]["Stop"] for h in entry["hooks"]]


def test_a_settings_file_that_is_not_json_is_refused_and_untouched(checkout, isolated_env):
    write(settings_file(isolated_env), "{ not json\n")
    install = load_install()
    path = str(settings_file(isolated_env))
    assert install.hook_state(path, nudge(checkout))[0] == "refuse"
    ok, message = install.apply_hook(path, nudge(checkout))
    assert not ok and "settings.json" in message
    assert settings_file(isolated_env).read_text(encoding="utf-8") == "{ not json\n"
