#!/usr/bin/env python3
"""How the reconciler's notices reach a session: the rules both notifiers keep.

Two modules tell somebody something, and they tell different people.
`notify_conductor.py` carries the five operational notices — ready, stalled,
unprompted, review obligations and a sync that needs a hand — to the session
that keeps the work moving. `notify_origin.py` carries a task's outcome, and a
worker's question, to the conversation the task came from. WHO differs; HOW
does not, and this is the how, kept once so the two cannot drift apart:

  AT REST ONLY.  A session is typed into only when its agent has SAID it is at
      rest (`AT_REST`). Text typed into a turn in flight is an interjection,
      and text typed at a `blocked` session answers the dialog it is sitting on.
  INTO A PROVABLY EMPTY LINE ONLY.  On 2026-10-07 the operator's "c" and a
      notice became one line, "cfleet reconciler: …". So the wake is typed only
      when `composer_empty` can see the cursor right after the prompt glyph
      with nothing either side of it; anything it cannot read is not empty.
  ELSE THE MAILBOX, WITHOUT A WAKE.  A session that may not be typed into gets
      the same line posted with `message send --no-wake`, which enqueues and
      delivers nothing, and the wake waits for rest. The caller remembers that
      it posted, so the mailbox gets one note per change and not one per pass.
  A RELAY IS BOUNDED.  A relay command (`owners.conf`'s `= !<command>`) runs
      under `fleet_platform.run_bounded`, killed with its tree past
      `RELAY_SECS`, and its failure is a line for the log, never a raise.
  MEMORY IS RUNTIME STATE.  What a recipient has been told lives in the
      reconciler's runtime directory, replaced in one step, written INTO that
      directory and never creating it — that directory being gone is how the
      loop knows it has nothing left to run for — and never on a task: the
      reconciler writes no record.

Nothing here decides who to tell or what is news. It is loaded by its two
callers and is not run on its own.

Requires: python3, and `thurbox-cli` for a session send; without it there is
nobody to type into, and each caller says so in its own words.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

# THE ONLY TWO STATES A WAKE IS ALLOWED INTO, and the list is an allowlist
# because the failure modes are asymmetric. `idle` and `done` are the agent's
# own hook saying it is at rest — at rest between turns, and typing at it is
# how the operator would speak to it anyway.
#
# Everything else is refused, and `blocked` is the one to understand: it means
# the agent is sitting on a permission or input prompt, so text sent to it does
# not start a turn, it ANSWERS the dialog. `working` is a turn in flight.
# `running`, `uncovered` and `unreported` are not the agent saying anything at
# all — see the state table in `.agents/skills/thurbox-session/SKILL.md`, whose
# whole point is that the absence of a word is not `idle`.
AT_REST = ("idle", "done")

# How many refs one line spells before it stops naming them. Six is about what
# stays readable in a terminal; the count is always exact.
NAMED = 6

# The most one relay command may run. A relay forwards one line to wherever an
# origin lives; one that takes longer than this is hung, and it rides on a
# `collect` the reconciler bounds as a whole.
RELAY_SECS = 30


def _load_queue():
    """Load scripts/lib/queue.py under a name that is not `queue`.

    The alias is what keeps this directory on sys.path from shadowing the
    standard library's `queue`, and it is the same load fleet_status.py does.
    """
    if "fleet_queue" in sys.modules:
        return sys.modules["fleet_queue"]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.py")
    spec = importlib.util.spec_from_file_location("fleet_queue", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fleet_queue"] = module
    spec.loader.exec_module(module)
    return module


fleetqueue = _load_queue()


# --- memory ------------------------------------------------------------------


def read_memory(path: str) -> dict:
    """A notifier's memory file as a dict; {} for none, or one that is not JSON."""
    try:
        doc = json.loads(fleetqueue.fleet_platform.read_record(path))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def write_memory(path: str, doc: dict) -> None:
    """Replace a notifier's memory in one step, INTO its directory and never making it.

    Best effort on purpose: a runtime directory that cannot be written is
    worth a repeated notice, and is not worth failing the pass it rides on.
    In one step through `write_record`: written in place, a reader racing the
    writer read a torn file as "nothing was told", and the lead was told twice.
    """
    if not os.path.isdir(os.path.dirname(path) or "."):
        return
    try:
        fleetqueue.fleet_platform.write_record(path, json.dumps(doc))
    except OSError:
        pass


def forget_memory(path: str) -> None:
    """Remove a notifier's memory file; one already gone is already forgotten."""
    try:
        os.remove(path)
    except OSError:
        pass


# --- who is listening --------------------------------------------------------


def session_rows() -> tuple[list | None, str]:
    """thurbox's `session list`, or (None, why not)."""
    if not shutil.which("thurbox-cli"):
        return None, "no thurbox-cli here, so there is nothing to wake"
    try:
        out = subprocess.run(
            ["thurbox-cli", "session", "list", "--json"],
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"thurbox-cli session list failed: {exc}"
    if out.returncode != 0:
        return None, "thurbox-cli session list failed"
    try:
        rows = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None, "thurbox-cli session list did not answer JSON"
    if not isinstance(rows, list):
        return None, "thurbox-cli session list did not answer a list"
    return rows, ""


def is_local(row: dict) -> bool:
    """Whether a `session list` row runs on THIS machine."""
    return (fleetqueue.session_place(row.get("backend_type") or "") or ("",))[0] == "local"


def owner_row(substring: str, held: set, exclude: str = "",
              rows: list | None = None) -> tuple[dict | None, str]:
    """The `session list` row of the session an owners rule names, or (None, why not).

    NEVER TYPED INTO ON A GUESS. The owner is any session on this machine, so
    the name is all there is to go on, and the rule is correspondingly strict:
    exactly ONE session whose name contains `substring`, running on THIS
    machine — a row thurbox mirrors from another host, wearing the same name,
    is nobody this fleet may type into — that is neither `exclude` (this
    fleet's conductor) nor a worker some task here holds (a worker's title can
    contain any word). Zero, or several, deliver nothing.
    """
    if rows is None:
        rows, why = session_rows()
        if rows is None:
            return None, why
    named = [r for r in rows if isinstance(r, dict) and r.get("id")
             and substring in str(r.get("name") or "")
             and str(r["id"]) != exclude and str(r["id"]) not in held]
    if not named:
        return None, f"no session named like {substring!r} is running"
    here = [r for r in named if is_local(r)]
    if len(here) == 1:
        return here[0], ""
    return None, (f"{len(named)} sessions are named like {substring!r} and "
                  + ("several are" if here else "none of them is") + " on this machine")


def identity(row: dict) -> dict:
    """Which conversation a row is: thurbox's session id, and the agent's conversation.

    A thurbox whose list carries no conversation leaves the session id to
    stand for both, which is what a new session still changes."""
    sid = str(row["id"])
    return {"session": sid, "conversation": str(row.get("agent_session_id") or sid)}


# --- may it be typed into ----------------------------------------------------


def composer_empty(sid: str) -> bool:
    """Whether a session's input line is PROVABLY empty; False whenever it cannot tell.

    `--lines 0` is the visible pane alone, which is what `cursor_row` counts
    in. Empty is the cursor right after one prompt glyph — `❯`, `›`, `>`: a
    word with no letter or digit in it — with nothing after it on the line.
    Only Codex's exact empty placeholder with its footer also counts, and
    Claude Code's suggestion as `claude_placeholder` proves it. A draft or a
    thurbox that reports no cursor costs a mailbox note.
    """
    shown = _capture(sid)
    row, col = shown.get("cursor_row"), shown.get("cursor_col")
    lines = str(shown.get("output") or "").split("\n")
    if not isinstance(row, int) or not isinstance(col, int) or not 0 <= row < len(lines):
        return False
    before, after = lines[row][:col].split(), lines[row][col:]
    if before == ["\u276f"] and after.strip() and claude_placeholder(sid, row, col, lines[row]):
        return True
    # Codex draws this placeholder after the cursor in its empty composer.
    # Require the footer immediately below as well, so a multiline draft
    # cannot be mistaken for the one-line placeholder.
    tail = [line.strip() for line in lines[row + 1:] if line.strip()]
    if (before == ["›"] and after.rstrip() == "Ask Codex to do anything"
            and len(tail) == 1 and " · " in tail[0]):
        return True
    return len(before) == 1 and len(before[0]) <= 2 and not any(c.isalnum() for c in before[0]) and not after.strip()


def _capture(sid: str, *extra: str) -> dict:
    """`session capture --json` of the visible pane, or {} when it cannot be read."""
    try:
        out = subprocess.run(
            ["thurbox-cli", "session", "capture", sid, "--json", "--lines", "0", *extra],
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=10,
        )
        shown = json.loads(out.stdout) if out.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    return shown if isinstance(shown, dict) else {}


# One escape sequence in an `--ansi` capture: a CSI (SGR among them) or an OSC.
_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _faint_after(params: str, faint: bool) -> bool:
    """Whether text is faint after one SGR's parameters: 2 sets it, 0 and 22
    reset it, and a 38/48/58 colour's own arguments — `5;n`, `2;r;g;b` — are
    skipped, so the 2 that opens a truecolour is never read as faint."""
    codes = (params or "0").split(";")
    i = 0
    while i < len(codes):
        code = codes[i]
        if code in ("38", "48", "58"):
            i += {"5": 2, "2": 4}.get(codes[i + 1] if i + 1 < len(codes) else "", 0)
        elif code == "2":
            faint = True
        elif code in ("0", "", "22"):
            faint = False
        i += 1
    return faint


def claude_placeholder(sid: str, row: int, col: int, plain: str) -> bool:
    """Whether what follows `❯` at the cursor is Claude Code's empty-composer suggestion.

    Claude Code rotates the words (`Try "…"`), so no text proves it; the
    STYLE does. Its suggestion is drawn faint (SGR 2) after the cursor, and a
    draft is drawn in the plain foreground. So the pane is captured again with
    `--ansi`, and it is the placeholder only when that capture shows the same
    line under the same cursor, every visible character right of the cursor
    is faint, and the composer's lower rule is the very next line — a second
    line of a draft would sit between them. Anything else is not empty.
    """
    shown = _capture(sid, "--ansi")
    lines = str(shown.get("output") or "").split("\n")
    if (shown.get("cursor_row"), shown.get("cursor_col")) != (row, col) or not row + 1 < len(lines):
        return False
    rule = _ESCAPE.sub("", lines[row + 1]).strip()
    if not rule or set(rule) != {"\u2500"}:
        return False
    faint, column, seen = False, 0, False
    for piece in re.split(f"({_ESCAPE.pattern})", lines[row]):
        if piece.startswith("\x1b"):
            if piece.startswith("\x1b[") and piece.endswith("m"):
                faint = _faint_after(piece[2:-1], faint)
            continue
        for ch in piece:
            if column >= col and not ch.isspace():
                if not faint:
                    return False
                seen = True
            column += 1
    return seen and _ESCAPE.sub("", lines[row]).rstrip() == plain.rstrip()


def held(name: str, row: dict) -> str:
    """Why `row` may not be typed into now, or "" when it may: at rest, line empty."""
    status = str(row.get("state") or "unknown")
    if status not in AT_REST:
        return f"{name} is {status}"
    if not composer_empty(str(row["id"])):
        return f"{name}'s input line is not provably empty"
    return ""


# --- the three ways a line leaves --------------------------------------------


def wake(sid: str, text: str) -> tuple[bool, str]:
    """Type one line into a session's terminal.

    `session send`, not `message send`. The mailbox is what POLICY.md forbids
    workers, because an arriving worker message interrupts whoever is talking
    to the recipient and routine completion is not worth that. This is the
    case the same rule leaves open: it goes only to a session that has said
    it is at rest (`held`), so there is nobody to interrupt.
    """
    try:
        out = subprocess.run(
            ["thurbox-cli", "session", "send", sid, text],
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"session send failed: {exc}"
    if out.returncode != 0:
        detail = (out.stderr or out.stdout or "").strip().splitlines()
        return False, "session send failed: " + (detail[-1] if detail else "no output")
    return True, ""


def post(sid: str, text: str, kind: str = "fleet-ready") -> tuple[bool, str]:
    """Leave one line in a session's thurbox mailbox, and wake nothing.

    `message send --no-wake` only enqueues. Without the flag thurbox pushes
    the body into the recipient's conversation between tool calls, which is
    exactly the interruption POLICY.md forbids, so the flag is not optional
    and a thurbox that does not know it fails here rather than waking anyone.
    """
    try:
        out = subprocess.run(
            ["thurbox-cli", "message", "send", "--to", sid, "--kind", kind,
             "--body", text, "--no-wake", "--json"],
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"message send failed: {exc}"
    if out.returncode != 0:
        return False, "this thurbox has no mailbox"
    return True, ""


def relay(target, text: str) -> tuple[bool, str]:
    """Hand one line to an origin's relay command: (delivered, why not).

    `queue.py`'s `relay_argv` builds the argv — the operator's command with the
    origin and the line appended — and it runs with no shell, bounded by
    RELAY_SECS and killed with its tree past it. Exit 0 is delivered; anything
    else, a command that cannot start included, is a reason and never a raise.
    """
    try:
        argv = fleetqueue.relay_argv(target, text)
    except ValueError as exc:
        return False, f"relay for {target.rule!r} is not a command: {exc}"
    try:
        done = fleetqueue.fleet_platform.run_bounded(
            argv, RELAY_SECS, capture_output=True, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        return False, f"relay for {target.rule!r} could not run: {exc}"
    if done is None:
        return False, f"relay for {target.rule!r} killed after {RELAY_SECS}s"
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        return False, (f"relay for {target.rule!r} exit {done.returncode}"
                       + (f": {detail[-1]}" if detail else ""))
    return True, ""
