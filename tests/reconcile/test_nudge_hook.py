"""A nudge is advisory, the hook cannot hurt the worker it runs in, and status tells the truth.

`nudge` is the only verb safe in a worker's Stop hook: it writes one flag file
and runs no queue command, so a worker firing it can never collect or reap
itself. It brings the periodic pass forward and nothing else. `fleet install`
merges the hook into Claude Code's user settings, never into thurbox's hooks
file, which thurbox rewrites on every start; `hook` prints the same block for
an operator who adds it by hand.
"""

from __future__ import annotations

import json
import os
import shlex

from harness import REPO, expect, write
from reconcilekit import wait_for


def test_nudge_runs_no_queue_command_and_waits_for_a_loop(recon):
    out = recon("nudge")
    assert out.code == 0, out.out
    expect(out.out, "nudged, but nothing is ticking")
    assert recon.calls() == [], "the nudge asked the queue for something"
    assert (recon.rt / "nudge").is_file()


def test_nudge_exits_zero_even_when_it_cannot_write(recon, isolated_env):
    """A hook that fails is a hook that can block the agent; this one has nothing worth blocking for."""
    blocker = isolated_env / "a-file"
    write(blocker, "not a directory\n")
    out = recon("nudge", FLEET_RECONCILE_DIR=str(blocker / "reconcile"))
    assert out.code == 0, out.out


def test_a_nudged_pass_collects_without_waiting_out_the_interval(recon, monkeypatch):
    monkeypatch.setenv("FLEET_RECONCILE_COLLECT_SECS", "3600")
    recon("ensure")
    assert wait_for(lambda: recon.count("collect") == 1 and recon.count("watch") >= 1)

    nudged = recon("nudge")
    expect(nudged.out, "nudged — the next pass runs within")
    assert wait_for(lambda: recon.count("collect") == 2, 10), "the nudge did not bring collect forward"
    assert wait_for(lambda: not (recon.rt / "nudge").exists()), "the loop consumes the nudge"
    expect(recon.log(), "nudged: ")


def test_status_says_up_and_names_its_queue(recon):
    down = recon("status")
    assert down.code == 0
    expect(down.out, "not running, and no down flag")

    recon("ensure")
    up = recon("status")
    expect(up.out, "up        reconciling", str(recon.queue), "watch     every 1s", "shepherd  every 6s")


def hook_block(text: str) -> dict:
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "{")
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "}")
    return json.loads("\n".join(lines[start:end + 1]))


def test_the_hook_is_one_shell_neutral_nudge(recon, monkeypatch, isolated_env):
    claude = isolated_env / "claude config"
    thurbox = isolated_env / "thurbox config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("THURBOX_CONFIG_DIR", str(thurbox))
    out = recon("hook")
    assert out.code == 0, out.out
    # Where it lasts, and the command that puts it there.
    expect(out.out, os.path.join(str(claude), "settings.json"), "uv run fleet install")
    assert os.path.join(str(thurbox), "hooks", "claude.json") not in out.out, \
        "thurbox rewrites its hooks file on every start, so a nudge added there does not last"

    block = hook_block(out.stdout)
    assert block["type"] == "command"
    command = block["command"]
    assert shlex.split(command) == ["uv", "run", "--project", REPO.as_posix(), "fleet", "reconcile", "nudge"]
    # Nothing a shell reads differently from another: no redirection, no
    # chaining, no variable, and no backslash for bash to eat.
    for char in ("|", "&", ">", "<", ";", "$", "%", "`", "\\", "'"):
        assert char not in command, f"{char!r} parses differently across bash, cmd and PowerShell: {command}"
