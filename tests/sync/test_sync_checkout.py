"""`fleet sync-checkout` reports what actually happened.

It is the SessionStart hook, so it runs before anyone is watching and its
output is the only evidence it produces. That makes one failure mode worse than
all the others: reporting a refusal that did not happen. A sync that says
"offline" on a machine that is online looks like a network blip, so nobody
investigates, and every session silently inherits a stale `main`, which is the
one outcome it exists to prevent.

That is not hypothetical: `timeout` is GNU coreutils and is NOT on a stock
macOS, so the bash sync's `timeout 15 git fetch` exited 127 there and every
session reported "could not reach origin (offline?)" while GitHub answered in
under a second. The rest hold the promises around it: a refusal changes no
tracked state, a genuinely unreachable origin is still reported, and a
fast-forward that brings instructions says restart-lead.

Every case runs against a throwaway origin on disk, so nothing reaches a network.
"""

from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path

from harness import REPO, expect, git, lib, refute, run_fleet, write
from synckit import advance_origin, head_of, new_repo, remove_on_origin


def sync(work: Path, **env):
    return run_fleet("sync-checkout", cwd=work, **env)


def origin_main(root: Path) -> str:
    return git("rev-parse", "main", cwd=root / "origin.git").strip()


# --- §1 the regression: no `timeout` is not "offline" --------------------------


def test_a_reachable_origin_is_never_reported_unreachable_and_no_coreutils_timeout_is_asked(tmp_path, stubs):
    """The bound on the fetch is the child's own timeout, so a machine with no
    `timeout` or `gtimeout` fetches exactly like one with both. Tripwires stand
    in for them and exit 127, the code that read as "offline" on a Mac."""
    for tool in ("timeout", "gtimeout"):
        stubs.tool(tool, "raise SystemExit(127)")
    work = new_repo(tmp_path)
    advance_origin(tmp_path)

    done = sync(work)

    refute(done.out, "could not reach origin")
    expect(done.out, "fast-forwarded 'main' 1 commit(s)")
    assert head_of(work) == origin_main(tmp_path), "the checkout actually moved to origin/main"
    assert stubs.calls("timeout") == [] and stubs.calls("gtimeout") == []


def test_a_fetch_that_hangs_is_given_up_on_and_reported_rather_than_waited_out(tmp_path, stubs, monkeypatch, capsys):
    """A session start is the wrong place to wait on a network: an unbounded
    fetch in the hook would hold the session open."""
    work = new_repo(tmp_path)
    advance_origin(tmp_path)
    real_git = shutil.which("git")
    stubs.tool("git", (
        "import subprocess, sys, time\n"
        "if 'fetch' in sys.argv[1:]:\n"
        "    time.sleep(20)\n"
        f"raise SystemExit(subprocess.run([{real_git!r}, *sys.argv[1:]]).returncode)\n"
    ))
    before = head_of(work)
    module = lib("sync_checkout.py")
    monkeypatch.setattr(module, "FETCH_TIMEOUT_SECS", 1)
    monkeypatch.chdir(work)

    assert module.main([]) == 0
    expect(capsys.readouterr().out, "could not reach origin")
    assert head_of(work) == before


# --- §2 an unreachable origin is still reported -------------------------------


def test_an_origin_that_really_is_unreachable_still_says_so_and_changes_nothing(tmp_path):
    """The fix must not buy §1 by dropping the report altogether."""
    work = new_repo(tmp_path)
    git("remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"), cwd=work)
    before = head_of(work)

    done = sync(work)

    expect(done.out, "could not reach origin")
    assert head_of(work) == before


# --- §3 already current is silent ---------------------------------------------


def test_nothing_to_do_says_nothing(tmp_path):
    work = new_repo(tmp_path)
    done = sync(work)
    assert done.code == 0
    assert done.out.strip() == ""


# --- §4 the three refusals change no tracked state ----------------------------


def test_a_dirty_tree_is_reported_and_not_fast_forwarded(tmp_path):
    work = new_repo(tmp_path)
    advance_origin(tmp_path)
    with open(work / "README.md", "a", encoding="utf-8", newline="\n") as fh:
        fh.write("local edit\n")
    before = head_of(work)

    done = sync(work)

    expect(done.out, "the tree is dirty. Not fast-forwarding.")
    assert head_of(work) == before


def test_a_dirty_tracked_profiles_file_names_the_overlay_that_frees_it(tmp_path):
    """#135: an operator who wrote a profile into the tracked file is told where it goes."""
    work = new_repo(tmp_path)
    profiles = work / "orchestration" / "session-profiles.yaml"
    write(profiles, "profiles:\n  default:\n    env: {}\n")
    git("add", "orchestration/session-profiles.yaml", cwd=work)
    git("commit", "--quiet", "-m", "profiles", cwd=work)
    git("push", "--quiet", "origin", "main", cwd=work)
    advance_origin(tmp_path)
    with open(profiles, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("  mine:\n    env: {ANTHROPIC_MODEL: some-model}\n")

    done = sync(work)

    expect(done.out, "the tree is dirty. Not fast-forwarding.", "orchestration/session-profiles.local.yaml")
    # Any other dirty file is not told about profiles.
    git("checkout", "--", "orchestration/session-profiles.yaml", cwd=work)
    with open(work / "README.md", "a", encoding="utf-8", newline="\n") as fh:
        fh.write("local edit\n")
    refute(sync(work).out, "session-profiles.local.yaml")


def test_an_untracked_file_does_not_block_a_fast_forward(tmp_path):
    """Only tracked modifications block: a stray note would otherwise wedge every sync."""
    work = new_repo(tmp_path)
    advance_origin(tmp_path)
    write(work / "NOTES.md", "a stray note\n")

    expect(sync(work).out, "fast-forwarded 'main' 1 commit(s)")


def test_a_feature_branch_is_reported_and_not_merged(tmp_path):
    work = new_repo(tmp_path)
    advance_origin(tmp_path)
    git("checkout", "--quiet", "-b", "feature/x", cwd=work)
    before = head_of(work)

    done = sync(work)

    expect(done.out, "on 'feature/x'", "origin/main is 1 commit(s) ahead")
    assert head_of(work) == before


def test_a_divergence_is_reported_and_never_reconciled(tmp_path):
    work = new_repo(tmp_path)
    advance_origin(tmp_path)
    write(work / "LOCAL.md", "local commit\n")
    git("add", "LOCAL.md", cwd=work)
    git("commit", "--quiet", "-m", "local work", cwd=work)
    before = head_of(work)

    done = sync(work)

    expect(done.out, "has diverged")
    assert head_of(work) == before


# --- §5 a fast-forward that brings instructions says restart-lead -------------


def test_incoming_instructions_raise_the_hand_over_and_name_the_path(tmp_path):
    """The hand-over is the half no command can perform, so it has to be said.
    If this line stops appearing, a lead runs on stale instructions and reports
    the update as applied."""
    work = new_repo(tmp_path)
    advance_origin(tmp_path, "AGENTS.md")

    done = sync(work)

    expect(done.out, "restart-lead:", "AGENTS.md")


def test_an_unrelated_path_does_not_raise_the_hand_over(tmp_path):
    work = new_repo(tmp_path)
    advance_origin(tmp_path, "docs/unrelated.md")

    refute(sync(work).out, "restart-lead:", "restart-reconciler:")


def test_a_sync_that_removes_the_reconcilers_code_says_to_restart_it(tmp_path):
    """A loop runs the code it started with. The bash reconciler outlived the
    update that deleted its script and failed every pass for hours, and the
    sync that deleted it said nothing."""
    work = new_repo(tmp_path)
    advance_origin(tmp_path, "scripts/reconcile.sh")
    sync(work)
    remove_on_origin(tmp_path, "scripts/reconcile.sh")

    done = sync(work)

    expect(done.out, "restart-reconciler: yes", "scripts/reconcile.sh", "uv run fleet reconcile status")


def test_new_loop_code_says_to_restart_the_reconciler_too(tmp_path):
    work = new_repo(tmp_path)
    advance_origin(tmp_path, "scripts/lib/reconcile.py")

    expect(sync(work).out, "restart-reconciler: yes", "scripts/lib/reconcile.py")


def test_the_lead_is_named_as_the_installed_manifest_spells_it(tmp_path):
    """The glyph in front of the lead's name is a setting, so the name comes
    from the rendered manifest and never from a literal."""
    work = new_repo(tmp_path)
    write(work / "extension.toml", '[extension]\nname = "fleet"\n\n[[sessions]]\nname = "* Mission Control"\n')
    advance_origin(tmp_path, "FLEET.md")

    done = sync(work)

    expect(done.out, "thurbox-cli session restart '* Mission Control'", "reinstall-extension: yes")


# --- §6 the contract with the hook --------------------------------------------


def test_the_hook_contract_is_one_json_object_and_exit_0(tmp_path):
    """Claude Code parses stdout as one JSON object and a sync problem must never
    block a session, so the exit code is always 0 and the payload always parses."""
    work = new_repo(tmp_path)
    advance_origin(tmp_path)

    done = sync(work)

    assert done.code == 0
    payload = json.loads(done.stdout)
    assert "systemMessage" in payload and "suppressOutput" in payload


def test_no_git_on_path_is_said_and_still_exits_0(tmp_path):
    empty = tmp_path / "no-tools"
    empty.mkdir()
    work = new_repo(tmp_path)

    done = sync(work, PATH=str(empty))

    assert done.code == 0
    assert "git not found" in json.loads(done.stdout)["systemMessage"]


def test_an_unexpected_failure_is_said_and_still_exits_0(tmp_path, monkeypatch, capsys):
    """The hook tolerates failure because the command does: nothing it can raise
    reaches Claude Code as a non-zero exit."""
    work = new_repo(tmp_path)
    module = lib("sync_checkout.py")

    def broken(*_a, **_k):
        raise RuntimeError("something nobody planned for")

    monkeypatch.setattr(module, "fetch_bounded", broken)
    monkeypatch.chdir(work)

    assert module.main([]) == 0
    expect(json.loads(capsys.readouterr().out)["systemMessage"], "something nobody planned for")


def test_the_session_start_hook_is_one_command_every_shell_parses_the_same():
    """Claude Code hands the hook to whatever shell the platform has: bash on
    Linux and macOS, and on Windows a shell with no `$(...)`, `[ -x ]` or `||`.
    So the hook carries no shell syntax at all, and tolerating failure is the
    command's job, proven above."""
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    hooks = [h for entry in settings["hooks"]["SessionStart"] for h in entry["hooks"]]
    assert len(hooks) == 1, hooks
    command = hooks[0]["command"]

    shell_syntax = set("$`()[]{}|&;<>\"'%\\*?!~")
    assert not shell_syntax & set(command), f"shell syntax in the hook: {command}"
    words = shlex.split(command)
    assert words[:2] == ["uv", "run"] and words[-2:] == ["fleet", "sync-checkout"], command
