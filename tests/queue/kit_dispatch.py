"""`thurbox-cli` stand-ins for the tests that spawn and prompt sessions.

Each answers one verb its own way and hands every other call to the stub
package's own `thurbox-cli`, so a session spawned or listed here is spawned or
listed by the same code as in every other queue test. They are scoped per test
and not folded into the shared stub: a dialog that closes on Enter would make a
test about a dialog that must stay exactly as written lie.
"""

from __future__ import annotations

import json

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


def behind_dialog(stubs: Stubs, sid: str, pane: str, queued: str | None = None) -> None:
    """A session whose agent has not reported yet, as it has not behind a modal dialog."""
    write(stubs.root / "sessions" / f"{sid}.json", json.dumps(
        {"id": sid, "name": f"worker {sid}", "state": "unreported", "agent": "claude", "hook_reported": False}
    ) + "\n")
    write(stubs.root / "panes" / f"{sid}.txt", pane + "\n")
    if queued is not None:
        write(stubs.root / "panes" / f"{sid}.next.txt", queued + "\n")


def next_session(stubs: Stubs, sid: str) -> None:
    """The id the next `session create` hands back."""
    write(stubs.root / "next-session.json", json.dumps({"id": sid, "created": True}) + "\n")


def keys(stubs: Stubs) -> list[str]:
    return [c.removeprefix("thurbox-cli ") for c in stubs.calls("thurbox-cli", "session key")]
