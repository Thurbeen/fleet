"""`thurbox-cli` stand-ins for the tests that spawn and prompt sessions.

Each answers one verb its own way and hands every other call to the stub
package's own `thurbox-cli`, so a session spawned or listed here is spawned or
listed by the same code as in every other queue test. They are scoped per test
and not folded into the shared stub: a dialog that closes on Enter would make a
test about a dialog that must stay exactly as written lie.
"""

from __future__ import annotations

import json
import shutil

from harness import Stubs, write

# The prelude every override shares: `delegate()` runs the package's own answer
# for this argv. The call is already logged, so the stub must not log it again.
DELEGATE = '''
import os
import sys
from pathlib import Path

import fleet_stubs.thurbox as _stub

ROOT = Path(os.environ["FLEET_STUB_ROOT"])
ARGS = sys.argv[1:]


def delegate():
    _stub.called = lambda tool: ROOT
    raise SystemExit(_stub.main())
'''

# `session create` fails for a branch named by BOOM_MATCH, with BOOM_MESSAGE, on
# BOOM_STREAM (stderr unless told otherwise).
FAILING_SPAWN = DELEGATE + '''
match = os.environ.get("BOOM_MATCH")
if ARGS[:2] == ["session", "create"] and match and match in " ".join(ARGS):
    stream = sys.stdout if os.environ.get("BOOM_STREAM") == "stdout" else sys.stderr
    stream.write(os.environ["BOOM_MESSAGE"] + "\\n")
    raise SystemExit(1)
delegate()
'''

# `session create` cuts each worktree the way thurbox does — `git worktree add -b`
# off the base it was handed — and reports them as thurbox's JSON does.
CUTS_WORKTREES = DELEGATE + '''
import json
import subprocess

if ARGS[:2] == ["session", "create"]:
    def values(flag):
        return [ARGS[i + 1] for i, arg in enumerate(ARGS) if arg == flag]

    branch = values("--worktree-branch")[0]
    starts = [(values("--repo-path")[0], values("--base-branch")[0])]
    starts += [tuple(spec.rsplit("@", 1)) for spec in values("--add-repo")]
    trees = []
    for repo, start in starts:
        path = ROOT / "worktrees" / Path(repo).name
        subprocess.run(["git", "-C", repo, "worktree", "add", "-q", "-b", branch, str(path), start], check=True)
        trees.append({"repo_path": repo, "worktree_path": str(path), "branch": branch})
    nxt = ROOT / "next-session.json"
    nxt.write_text(json.dumps({**json.loads(nxt.read_text(encoding="utf-8")), "worktrees": trees}),
                   encoding="utf-8")
delegate()
'''

# `session key <id> enter` closes whatever dialog the pane shows, revealing the
# one queued behind it (`<id>.next.txt`), if any.
ANSWERING_KEYS = DELEGATE + '''
if ARGS[:2] == ["session", "key"]:
    pane = ROOT / "panes" / f"{ARGS[2]}.txt"
    behind = ROOT / "panes" / f"{ARGS[2]}.next.txt"
    if ARGS[3:4] == ["enter"] and pane.is_file():
        if behind.is_file():
            behind.replace(pane)
        else:
            pane.unlink()
    raise SystemExit(0)
delegate()
'''

IMPORTS_DIALOG = """Allow external CLAUDE.md file imports?

  ❯ No, disable external imports
    Yes, allow external imports

Enter to confirm · Esc to cancel"""

FOLDER_DIALOG = """Quick safety check: Is this a project you created or one you trust?

  ❯ No, exit
    Yes, I trust this folder

Enter to confirm · Esc to cancel"""

CODEX_READY = """OpenAI Codex

› Ask Codex to do anything

  model · repository · branch"""

# Codex 0.161 moved the footer: the status line, then a shortcuts hint, both
# below the composer — and, when its config raised any, a warnings line too.
CODEX_READY_SHORTCUTS = """OpenAI Codex

› Ask Codex to do anything

  model · ~/worktrees/example · branch
  ? for shortcuts"""

CODEX_READY_WARNINGS = """OpenAI Codex

› Ask Codex to do anything

  ⚠ 2 warnings · f2 to view
  model · ~/worktrees/example · branch
  ? for shortcuts"""

CODEX_DIALOG = """Do you trust the contents of this directory?

› Yes, continue
  No, exit"""


def codex_pane(stubs: Stubs, sid: str, pane: str = CODEX_READY) -> None:
    """A running Codex pane whose startup hook has not reported."""
    write(stubs.root / "sessions" / f"{sid}.json", json.dumps({
        "id": sid, "name": f"worker {sid}", "agent": "codex",
        "foreground_command": "codex", "state": "running", "hook_reported": False,
    }) + "\n")
    write(stubs.root / "panes" / f"{sid}.txt", pane + "\n")


def behind_dialog(stubs: Stubs, sid: str, pane: str, queued: str | None = None,
                  agent: str = "claude") -> None:
    """A session whose agent has not reported yet, as it has not behind a modal dialog.

    `agent` is what thurbox says is in the pane, and it is what the trust step
    looks a dialog up by — so a second account of an agent, running under a
    name of its own, is spelled here.
    """
    write(stubs.root / "sessions" / f"{sid}.json", json.dumps(
        {"id": sid, "name": f"worker {sid}", "state": "unreported", "agent": agent, "hook_reported": False}
    ) + "\n")
    write(stubs.root / "panes" / f"{sid}.txt", pane + "\n")
    if queued is not None:
        write(stubs.root / "panes" / f"{sid}.next.txt", queued + "\n")


# `git -C <repo> ... fetch ... +refs/heads/X:refs/remotes/origin/X` answered from the
# checkout's own `X`, for a test whose origin is a forge URL nothing can reach:
# dispatch fetches every base before it spawns. Every other call is real git.
OFFLINE_FETCH = '''
import subprocess
import sys

GIT = __GIT__
ARGS = sys.argv[1:]
spec = ARGS[-1] if ARGS[:1] == ["-C"] and "fetch" in ARGS else ""
if spec.startswith("+refs/heads/") and ":" in spec:
    src, dst = spec[1:].split(":", 1)
    head = subprocess.run([GIT, "-C", ARGS[1], "rev-parse", "--verify", "--quiet", src],
                          capture_output=True, text=True, encoding="utf-8")
    if head.returncode:
        sys.stderr.write(f"fatal: couldn't find remote ref {src}\\n")
        raise SystemExit(128)
    raise SystemExit(subprocess.run([GIT, "-C", ARGS[1], "update-ref", dst, head.stdout.strip()]).returncode)
raise SystemExit(subprocess.run([GIT, *ARGS]).returncode)
'''


def offline_fetch(stubs: Stubs) -> None:
    stubs.tool("git", OFFLINE_FETCH.replace("__GIT__", repr(shutil.which("git"))))


def next_session(stubs: Stubs, sid: str) -> None:
    """The id the next `session create` hands back."""
    write(stubs.root / "next-session.json", json.dumps({"id": sid, "created": True}) + "\n")


def keys(stubs: Stubs) -> list[str]:
    return [c.removeprefix("thurbox-cli ") for c in stubs.calls("thurbox-cli", "session key")]


def clocked_fleet(*args: str, **env):
    """Run the real CLI with a virtual clock only in its trust-dialog module.

    Pane changes in these tests happen on stub calls, never on elapsed time.
    Advancing the trust clock preserves polling and timeout paths without
    waiting for a human-facing delay. Other modules keep their real clocks.
    """
    from harness import PYTHON, run

    boot = '''import sys
from fleet.cli import load, main


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


load("session_trust.py").time = Clock()
sys.exit(main(sys.argv[1:]))
'''
    return run([*PYTHON, "-c", boot, *args], **env)


def clocked_queue(*args: str, **env):
    return clocked_fleet("queue", *args, **env)


def clocked_run(argv, **env):
    from pathlib import Path

    from harness import PYTHON

    assert tuple(argv[:len(PYTHON)]) == PYTHON, argv
    script, *args = argv[len(PYTHON):]
    assert Path(script).name == "session_trust.py", argv
    return clocked_fleet("session-trust", *args, **env)
