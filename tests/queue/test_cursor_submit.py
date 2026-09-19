"""Fresh cursor-agent swallows Enter sent before its TUI owns the tty.

`session create` returns when the tmux window is live, not when the composer
is. `session send` then pastes, waits 200ms, and presses Enter into a pane
that is still echoing in cooked mode: the text later appears next to `→`,
unsubmitted. A send after the TUI is up submits. So dispatch types without
Enter, waits until the brief is in the composer, and only then submits —
and only for the command that was measured. A `--agent` spawn is unchanged.
"""

from __future__ import annotations

import json

from kit_dispatch import ANSWERING_KEYS, next_session
from kit_hosts import Hosts
from queuekit import fill_brief, ok

from harness import expect, queue_module, refute, write
from harness import run_queue as q

CURSOR_SID = "c0a50000-0000-0000-0000-000000000001"
CLAUDE_SID = "c1a50000-0000-0000-0000-000000000002"
HOST_SID = "c2a50000-0000-0000-0000-000000000003"

# Measured 2026-09-19 on a 229-character refuel-shaped send: the composer
# wraps onto a second line and keeps the whole brief; it does not scroll
# the tail off the pane. `composer_holds` squeezes whitespace so this matches.
REFUEL_BRIEF = (
    "Read /tmp/enter-verify-long/BRIEF.md and do what it says. Your session was "
    "restarted to recover a dead pane or a quota limit mid-task, so the "
    "conversation above is yours: continue from where you stopped rather "
    "than starting over."
)
WRAPPED_PANE = (
    "  → Read /tmp/enter-verify-long/BRIEF.md and do what it says. Your session "
    "was restarted to recover a dead pane or a quota limit mid-task, so the "
    "conversation above is yours: continue       \n"
    "    from where you stopped rather than starting over.\n"
)


def _cursor_session(stubs, sid: str) -> None:
    write(stubs.root / "sessions" / f"{sid}.json", json.dumps({
        "id": sid, "agent": "cursor-agent", "reports_as": None,
        "detected_agent": None, "hook_reported": False, "state": "uncovered",
        "hook_coverage": "none",
        "foreground_command": "cursor-agent --trust",
    }) + "\n")


def test_dispatch_submits_a_cursor_brief_only_after_it_is_in_the_composer(
    stubs, queue_dir
):
    """The sequence `dispatch` actually runs, not a helper called in isolation.

    A test that only invoked `trust_and_send` would stay green if the spawn
    never called it. This goes through `fleet queue dispatch` and reads the
    stub's recorded argv.
    """
    topic = ok(q(
        "topic", "add", "cursor-enter", "--title", "Cursor enter",
        "--prompt", "do not send enter before the composer exists",
    )).stdout.strip()
    ok(q(
        "add", topic, "commanded", "--title", "Commanded",
        "--repo", "/tmp/repo-cursor-enter", "--branch", "fix/cursor-enter",
        "--number", "01", "--profile", "cursor-trusted",
    ))
    fill_brief(queue_dir / topic / "01-commanded" / "BRIEF.md")
    next_session(stubs, CURSOR_SID)
    _cursor_session(stubs, CURSOR_SID)
    stubs.tool("thurbox-cli", ANSWERING_KEYS)

    out = ok(q("dispatch")).out
    refute(out, "NOT PROMPTED")

    tbx = stubs.calls("thurbox-cli")
    create = [c for c in tbx if "session create" in c and "fix/cursor-enter" in c]
    assert create, out
    expect(create[-1], "--command cursor-agent", "--arg --trust")
    # Option 1 would put the brief on argv. Restart replays --command/--arg.
    refute(create[-1], "Read ", "--arg Read")

    sid_calls = [c for c in tbx if CURSOR_SID in c]
    sends = [c for c in sid_calls if "session send" in c]
    captures = [c for c in sid_calls if "session capture" in c]
    keys = [c for c in sid_calls if "session key" in c]
    assert len(sends) == 1, sid_calls
    expect(sends[0], "--no-enter", f"session send {CURSOR_SID} Read")
    assert captures, sid_calls
    assert keys == [f"thurbox-cli session key {CURSOR_SID} enter"], sid_calls
    send_at = sid_calls.index(sends[0])
    cap_at = sid_calls.index(captures[0])
    key_at = sid_calls.index(keys[0])
    assert send_at < cap_at < key_at, sid_calls


def test_a_covered_agent_dispatch_still_sends_in_one_shot(stubs, queue_dir):
    """claude/codex do not lose Enter on spawn; do not rewrite their send."""
    topic = ok(q(
        "topic", "add", "claude-enter", "--title", "Claude enter",
        "--prompt", "a covered agent keeps the one-shot send",
    )).stdout.strip()
    ok(q(
        "add", topic, "covered", "--title", "Covered",
        "--repo", "/tmp/repo-claude-enter", "--branch", "fix/claude-enter",
        "--number", "01",
    ))
    fill_brief(queue_dir / topic / "01-covered" / "BRIEF.md")
    next_session(stubs, CLAUDE_SID)
    stubs.session_is(CLAUDE_SID, "idle")
    stubs.tool("thurbox-cli", ANSWERING_KEYS)

    out = ok(q("dispatch")).out
    refute(out, "NOT PROMPTED")
    sends = [c for c in stubs.calls("thurbox-cli", "session send") if CLAUDE_SID in c]
    assert sends, out
    refute("\n".join(sends), "--no-enter")
    assert not [
        c for c in stubs.calls("thurbox-cli", "session key") if CLAUDE_SID in c
    ], stubs.calls("thurbox-cli")


def test_a_cursor_task_on_a_host_keeps_the_one_shot_send(stubs, queue_dir):
    """`session capture` has no pane for a `--host` session on this machine.

    Verifying the composer here would wait out the timeout and report that
    the brief never appeared, about a pane that does not exist locally.
    """
    hosts = Hosts(stubs)
    topic = ok(q(
        "topic", "add", "cursor-host", "--title", "Cursor on a host",
        "--prompt", "a remote cursor session has no local pane to poll",
    )).stdout.strip()
    ok(q(
        "add", topic, "remote", "--title", "Remote",
        "--repo", "/srv/code/app", "--host", "devbox",
        "--branch", "fix/cursor-host", "--number", "01",
        "--profile", "cursor-trusted",
    ))
    fill_brief(queue_dir / topic / "01-remote" / "BRIEF.md")
    hosts.next_session(HOST_SID)
    hosts.session(
        HOST_SID, name="Remote", state="uncovered", agent="cursor-agent",
        reports_as=None, hook_reported=False, hook_coverage="none",
        foreground_command="cursor-agent --trust",
        worktrees=[{"repo_path": "/srv/code/app",
                    "worktree_path": "/srv/worktrees/app-1234/fix-cursor-host",
                    "branch": "fix/cursor-host"}],
    )
    stubs.tool("thurbox-cli", ANSWERING_KEYS)

    out = ok(q("dispatch")).out
    refute(out, "NOT PROMPTED", "did not appear in the composer")
    sends = [c for c in stubs.calls("thurbox-cli", "session send") if HOST_SID in c]
    assert sends, out
    refute("\n".join(sends), "--no-enter")
    assert not [
        c for c in stubs.calls("thurbox-cli", "session capture") if HOST_SID in c
    ], stubs.calls("thurbox-cli")


def test_composer_holds_a_wrapped_refuel_brief():
    """A 229-character refuel send wraps; the squeezed pane still holds it.

    The idle `→ Plan, search…` placeholder plus the same text elsewhere
    must not count — the arrow has to sit on the brief itself.
    """
    held = queue_module(
        "print(q.composer_holds(sys.argv[1], sys.argv[2]))\n",
        WRAPPED_PANE, REFUEL_BRIEF,
    ).strip()
    assert held == "True", held
    idle = "  → Plan, search, build anything\n" + REFUEL_BRIEF + "\n"
    stray = queue_module(
        "print(q.composer_holds(sys.argv[1], sys.argv[2]))\n",
        idle, REFUEL_BRIEF,
    ).strip()
    assert stray == "False", stray
