#!/usr/bin/env python3
"""Tell the lead that ready work exists which nothing will dispatch.

WHY THIS EXISTS, measured. On 2026-09-10 at 01:37 a pull request merged,
`forge-agnostic/01-01-forge-seam` landed, and the `semantic-dependency` blocker
on `forge-agnostic/02-02-gitlab-adapter` cleared. That task was dispatched at
08:05, because the operator typed "Status" and the lead looked. Six hours and
twenty-eight minutes of work that was ready, unclaimed, and had no actor.

Nothing was broken. The reconciler may not dispatch — `AGENTS.md` says it
reconciles and does not decide, and `tests/reconcile/` holds it to the verbs
it is allowed to want. The lead is the only actor that may dispatch, and it is
an interactive session that acts when someone speaks to it.
`fleet reconcile nudge` wakes the LOOP; there was no path in the other
direction.

NOTIFYING IS NOT DECIDING, and that is the whole seam. This changes no record,
moves no task and launches nothing. It reads the ready set and puts it in front
of the one actor that may act on it, along with the command that acts. The loop
keeps its constraint; the lead keeps the decision.

FIVE THINGS MAKE IT SURVIVABLE, and each is a way this could have been worse
than silence:

  IT FIRES ON THE TRANSITION.  A loop that says "one task is ready" every
      pass is turned off within the day, and then the six hours come back with
      the notification disabled on top. So the delivered ready set is
      remembered, and a pass whose ready set holds nothing new says nothing.
  IT DOES NOT INTERRUPT A TURN.  `session send` types into the lead's
      terminal. Typed into a session mid-turn that is an interjection in the
      middle of somebody's work — `shepherd` declines to touch a working
      session for exactly this reason. Only a lead that has SAID it is at rest
      is woken; anything else and the wake waits, which is not the same as
      being dropped. What a lead mid-turn gets instead is the same line in
      its thurbox mailbox, posted with `--no-wake`: that enqueues and nothing
      else, so nothing is typed and nothing is pushed into its conversation.
      The lead, or the operator looking at thurbox, reads it when they choose.
  IT DOES NOT TYPE INTO SOMEBODY'S LINE.  At rest is not enough: on
      2026-10-07 the operator's "c" and the notice became one line, "cfleet
      reconciler: …". So the wake is typed only when `session capture` shows
      the cursor right after the agent's prompt glyph with nothing either side
      of it. Anything else — text typed, a capture with no cursor, a pane this
      cannot read — gets the mailbox note a busy lead gets, and the wake waits.
  IT IS ONE LINE.  The lead is a token budget. Which tasks, and the command
      that sends them. No table, no narration.
  IT SURVIVES THE LEAD NOT EXISTING.  No lead session is an ordinary fleet —
      the operator closed it, or never installed the extension. That produces
      no send, no error and, because the note is deduplicated, one log line
      rather than one per pass.

WHAT "READY" MEANS HERE IS THE QUEUE'S ANSWER, NOT A SECOND ONE. This reads
`plan --json`'s ready set and derives nothing, which is what kept it honest
through the defect of 2026-09-11: a task whose brief began by reading Azure was
ready by every record fleet kept, because `block` had no way to record a wait on
an unauthenticated `az`, and the line below was correctly typed about work
nobody could do. The fix is `queue.py`'s second form of blocker — a CONDITION,
which `is_ready` never clears — and it reaches this file for free, through the
one reading it takes. A rule about conditions written HERE as well would be the
second opinion the single reading exists to prevent;
`tests/queue/test_conditions.py` asserts the outcome instead, as the case it
came from.

WHERE THE STATE LIVES. In the reconciler's own runtime directory, beside its
pid, heartbeat and flags — never on the task. "The lead has been told" is a
fact about one machine's loop and one conversation; it is not part of what a
task IS, and writing it onto a record would make this the second writer over
the queue. `scripts/lib/reconcile.py` still writes no record.

Usage (it is `scripts/lib/reconcile.py`'s, and nothing else's):

    uv run fleet queue plan --json | uv run python scripts/lib/notify_lead.py --state-dir DIR

It prints a line worth logging, or nothing, and it exits 0 whatever happens.
A notifier that can fail the pass it rides on would cost the reconciler the
`collect` it just did, which is a real loss traded for a message.

Environment:
  FLEET_LEAD_SESSION  the lead session's name, overriding the rendered
                      extension.toml. The gate uses it, since a rendered
                      manifest belongs to the operator's checkout and is not
                      something a test may write.

Requires: python3. `thurbox-cli` for the send itself — without it there is
nothing to wake and this says so once. A thurbox with no mailbox refuses the
`--no-wake` post, and the lead mid-turn then gets what it got before the
mailbox existed: the wait.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
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

# How many refs the one line spells before it stops naming them. Six is about
# what stays readable in a terminal; the count is always exact, and `dispatch`
# sends the whole set regardless of what was named.
NAMED = 6

STATE_FILE = "notified.json"


def _load_queue():
    """Load scripts/lib/queue.py under a name that is not `queue`.

    For `manifest_session` and `checkout_root`, so that "what is the lead
    session called" has one answer here and in queue.py's control-plane guard
    rather than two parsers that agree until someone renames the session. The
    alias is what keeps this directory on sys.path from shadowing the standard
    library's `queue`, and it is the same load fleet_status.py does.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.py")
    spec = importlib.util.spec_from_file_location("fleet_queue", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fleet_queue"] = module
    spec.loader.exec_module(module)
    return module


fleetqueue = _load_queue()


# --- what the loop remembers -------------------------------------------------


def read_state(state_dir: str) -> dict:
    try:
        with open(os.path.join(state_dir, STATE_FILE), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def write_state(state_dir: str, told: list, note: str, posted: list | None = None) -> None:
    """Remember what the lead has been told, and what was last logged about it.

    Best effort on purpose: a runtime directory that cannot be written is worth
    a repeated notification, and is not worth failing a reconciler pass over.

    Into the loop's runtime directory, and NEVER making it: that directory
    being gone is how the loop knows it has nothing left to run for, and a
    notify that made it again mid-pass left an orphaned loop ticking forever.
    """
    try:
        with open(os.path.join(state_dir, STATE_FILE), "w", encoding="utf-8") as fh:
            json.dump({"told": told, "note": note, "posted": posted or []}, fh)
    except OSError:
        pass


def say(state_dir: str, told: list, note: str, posted: list | None = None) -> int:
    """Print `note` only if it is not the one already standing.

    The deduplication is the difference between a diagnosis and a wall. A lead
    that is away for an hour, or busy for one, is one fact; at the collect
    cadence it would otherwise be thirty log lines saying it again.
    """
    if note and read_state(state_dir).get("note") != note:
        print(note)
    write_state(state_dir, told, note, posted)
    return 0


# --- who to wake -------------------------------------------------------------


def lead_name() -> str:
    """What the lead session is called, or "" when nothing here can tell.

    The RENDERED extension.toml only. `fleet install-extension` writes it into the
    control-plane clone, so its presence is the claim; the tracked
    `extension.toml.in` beside it carries a `__LEAD_GLYPH__` placeholder, which
    names a session that does not exist. Unknown stays silent — a fleet driven
    without the extension is legitimate and may not be nagged by a guess.
    """
    override = os.environ.get("FLEET_LEAD_SESSION", "").strip()
    if override:
        return override
    root = fleetqueue.checkout_root()
    name, _repo = fleetqueue.manifest_session(os.path.join(root, "extension.toml"))
    if not name or "__" in name:
        return ""
    return name


def lead_session(name: str) -> tuple[str | None, str]:
    """(session id, state) for the named session, or (None, why not).

    `session list` and not `session get`: the list carries `id` and `state` for
    every session in one call and probes no pane, which is the cheaper of the
    two answers and the one that does not touch the lead to ask about it.
    """
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
    for row in rows:
        if isinstance(row, dict) and row.get("name") == name and row.get("id"):
            return str(row["id"]), str(row.get("state") or "unknown")
    return None, f"no session named {name!r} is running"


def composer_empty(sid: str) -> bool:
    """Whether the lead's input line is PROVABLY empty; False whenever it cannot tell.

    `--lines 0` is the visible pane alone, which is what `cursor_row` counts
    in. Empty is the cursor right after one prompt glyph — `❯`, `›`, `>`: a
    word with no letter or digit in it — with nothing after it on the line.
    A placeholder, a second line of a draft or a thurbox that reports no
    cursor all read as not empty, and cost a mailbox note, not a garbled turn.
    """
    try:
        out = subprocess.run(
            ["thurbox-cli", "session", "capture", sid, "--json", "--lines", "0"],
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=10,
        )
        shown = json.loads(out.stdout) if out.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False
    if not isinstance(shown, dict):
        return False
    row, col = shown.get("cursor_row"), shown.get("cursor_col")
    lines = str(shown.get("output") or "").split("\n")
    if not isinstance(row, int) or not isinstance(col, int) or not 0 <= row < len(lines):
        return False
    before, after = lines[row][:col].split(), lines[row][col:]
    return len(before) == 1 and len(before[0]) <= 2 and not any(c.isalnum() for c in before[0]) and not after.strip()


def wake(sid: str, text: str) -> tuple[bool, str]:
    """Type one line into the lead's terminal.

    `session send`, not `message send`. The mailbox is what POLICY.md forbids
    workers, because an arriving worker message interrupts whoever is talking
    to the lead and routine completion is not worth that. This is the case the
    same rule leaves open: it goes only to a lead that has said it is at rest,
    so there is nobody to interrupt, and the thing it carries is the one fact
    the lead cannot learn any other way.
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


def post(sid: str, text: str) -> tuple[bool, str]:
    """Leave one line in the lead's thurbox mailbox, and wake nothing.

    `message send --no-wake` only enqueues. Without the flag thurbox pushes
    the body into the recipient's conversation between tool calls, which is
    exactly the interruption POLICY.md forbids, so the flag is not optional
    and a thurbox that does not know it fails here rather than waking anyone.
    """
    try:
        out = subprocess.run(
            ["thurbox-cli", "message", "send", "--to", sid, "--kind", "fleet-ready",
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


def message(ready: list) -> str:
    """One line: what is ready, and the command that sends it."""
    shown = ", ".join(ready[:NAMED])
    more = "" if len(ready) <= NAMED else f" and {len(ready) - NAMED} more"
    return (
        f"fleet reconciler: {len(ready)} task(s) ready and nothing will dispatch "
        f"them — {shown}{more}. Run: uv run fleet queue dispatch"
    )


# --- the pass ----------------------------------------------------------------


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(
        description="wake the lead when ready work has no actor",
    )
    ap.add_argument(
        "--state-dir",
        required=True,
        help="the reconciler's runtime directory, where what-was-told lives",
    )
    args = ap.parse_args(argv)

    try:
        plan = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(plan, dict):
        return 0
    ready = sorted(str(r) for r in (plan.get("ready") or []))

    # PRUNE, THEN COMPARE, and the prune is what makes a re-entry news. A task
    # that leaves the ready set — dispatched, blocked again, abandoned — is
    # forgotten, so if it comes back it is a transition again rather than
    # something the lead was already told about weeks ago.
    state = read_state(args.state_dir)
    told = [r for r in state.get("told", []) if r in ready]
    posted = [r for r in state.get("posted", []) if r in ready]
    fresh = [r for r in ready if r not in told]
    if not fresh:
        return say(args.state_dir, told, "")

    name = lead_name()
    if not name:
        return say(args.state_dir, told, "ready work, but no lead session is configured")
    sid, status = lead_session(name)
    if not sid:
        return say(args.state_dir, told, f"ready work, but {status}")
    held = f"{name} is {status}" if status not in AT_REST else ""
    if not held and not composer_empty(sid):
        held = f"{name}'s input line is not provably empty"
    if held:
        # The note is posted on its own transition, so a lead busy for an hour
        # finds one line per change in its mailbox and not one per pass.
        if [r for r in fresh if r not in posted]:
            sent, _why = post(sid, message(ready))
            posted = ready if sent else posted
        noted = all(r in posted for r in fresh)
        where = " — noted in its inbox" if noted else ""
        return say(
            args.state_dir, told, f"ready work; {held}{where}; the wake waits", posted
        )

    sent, why = wake(sid, message(ready))
    if not sent:
        return say(args.state_dir, told, f"could not wake {name}: {why}")

    # Only a delivered wake is remembered, so a send that failed is retried on
    # the next pass rather than swallowed.
    return say(
        args.state_dir,
        ready,
        f"woke {name}: {len(ready)} task(s) ready — {', '.join(ready[:NAMED])}",
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
