#!/usr/bin/env python3
"""Tell the lead that ready work exists which nothing will dispatch, or a worker stalled.

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

A STALLED WORKER IS THE SAME SILENCE FROM THE OTHER END. Five workers on one
machine stopped without a result and nobody noticed for two days. `plan --json`
names them (`stalled`: dispatched, at rest past half an hour, no result.md, no
new commit — `queue.py`'s `stalled_tasks` owns the judgement) and this carries
them under the four rules above, remembered as their own set, so a stall is
told once and fresh ready work is still news beside it.

WHERE THE STATE LIVES. In the reconciler's own runtime directory, beside its
pid, heartbeat and flags — never on the task. "The lead has been told" is a
fact about one machine's loop and one conversation; it is not part of what a
task IS, and writing it onto a record would make this the second writer over
the queue. `scripts/lib/reconcile.py` still writes no record.

Usage (it is `scripts/lib/reconcile.py`'s, and nothing else's):

    uv run fleet queue plan --json | uv run python scripts/lib/notify_lead.py --state-dir DIR

    uv run fleet sync-checkout --json | uv run python scripts/lib/notify_lead.py --state-dir DIR --stale

The second is the STALE LEAD: the loop fast-forwarded the checkout and what
arrived needs a hand. `stale()` owns its one difference from the ready notice.

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
# A stale-lead notice not delivered yet. Deleted once it is, so the loop asks
# for a retry only while one waits.
STALE_FILE = "stale.json"

# What the loop remembers, per set: what was typed into the lead, and what was
# left in its mailbox while it was mid-turn.
MEMORY = ("told", "posted", "stalled", "stalled_posted")


def _load_queue():
    """Load scripts/lib/queue.py under a name that is not `queue`.

    For `manifest_session` and `checkout_root`, so that "what is the lead
    session called" has one answer here and in queue.py's control-plane guard
    rather than two parsers that agree until someone renames the session. The
    alias is what keeps this directory on sys.path from shadowing the standard
    library's `queue`, and it is the same load fleet_status.py does.
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


# --- what the loop remembers -------------------------------------------------


def read_state(state_dir: str) -> dict:
    try:
        with open(os.path.join(state_dir, STATE_FILE), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def write_state(state_dir: str, memory: dict, note: str) -> None:
    """Remember what the lead has been told, and what was last logged about it.

    `memory` holds four lists of refs: `told` and `posted` for the ready set,
    `stalled` and `stalled_posted` for the stalled one — what was typed into the
    lead, and what was left in its mailbox.

    Best effort on purpose: a runtime directory that cannot be written is worth
    a repeated notification, and is not worth failing a reconciler pass over.

    Into the loop's runtime directory, and NEVER making it: that directory
    being gone is how the loop knows it has nothing left to run for, and a
    notify that made it again mid-pass left an orphaned loop ticking forever.
    """
    try:
        with open(os.path.join(state_dir, STATE_FILE), "w", encoding="utf-8") as fh:
            json.dump({**{k: memory.get(k) or [] for k in MEMORY}, "note": note}, fh)
    except OSError:
        pass


def say(state_dir: str, memory: dict, note: str) -> int:
    """Print `note` only if it is not the one already standing.

    The deduplication is the difference between a diagnosis and a wall. A lead
    that is away for an hour, or busy for one, is one fact; at the collect
    cadence it would otherwise be thirty log lines saying it again.
    """
    if note and read_state(state_dir).get("note") != note:
        print(note)
    write_state(state_dir, memory, note)
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
    Only Codex's exact empty placeholder with its footer also counts. A draft
    or a thurbox that reports no cursor costs a mailbox note.
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
    # Codex draws this placeholder after the cursor in its empty composer.
    # Require the footer immediately below as well, so a multiline draft
    # cannot be mistaken for the one-line placeholder.
    tail = [line.strip() for line in lines[row + 1:] if line.strip()]
    if (before == ["›"] and after.rstrip() == "Ask Codex to do anything"
            and len(tail) == 1 and " · " in tail[0]):
        return True
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


def post(sid: str, text: str, kind: str = "fleet-ready") -> tuple[bool, str]:
    """Leave one line in the lead's thurbox mailbox, and wake nothing.

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


def message(ready: list) -> str:
    """One line: what is ready, and the command that sends it."""
    shown = ", ".join(ready[:NAMED])
    more = "" if len(ready) <= NAMED else f" and {len(ready) - NAMED} more"
    return (
        f"fleet reconciler: {len(ready)} task(s) ready and nothing will dispatch "
        f"them — {shown}{more}. Run: uv run fleet queue dispatch"
    )


def stalled_message(stalled: list) -> str:
    """One line: which workers are at rest with nothing to show, and where to look."""
    shown = ", ".join(stalled[:NAMED])
    more = "" if len(stalled) <= NAMED else f" and {len(stalled) - NAMED} more"
    return (
        f"fleet reconciler: {len(stalled)} worker(s) stalled — at rest with no result "
        f"and no new commit — {shown}{more}. Look: uv run fleet queue show {stalled[0]}"
    )



# --- the stale lead ----------------------------------------------------------


def read_stale(state_dir: str) -> dict:
    try:
        with open(os.path.join(state_dir, STALE_FILE), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def write_stale(state_dir: str, pending: dict, note: str) -> None:
    """Keep a notice still to deliver; with none, remove the file. Into the
    runtime directory and never making it, for `write_state`'s reason."""
    path = os.path.join(state_dir, STALE_FILE)
    try:
        if not pending:
            os.remove(path)
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"pending": pending, "note": note}, fh)
    except OSError:
        pass


def merge(pending: dict, report: dict) -> dict:
    """Two fast-forwards before the lead heard of the first are one notice: the
    earliest `before`, the latest `after`, and every action with every path."""
    actions = dict(pending.get("actions") or {})
    for action, paths in (report.get("actions") or {}).items():
        have = actions.get(action, "").split()
        actions[action] = " ".join(have + [p for p in str(paths).split() if p not in have])
    return {
        "before": pending.get("before") or report.get("before") or "",
        "after": report.get("after") or pending.get("after") or "",
        "actions": actions,
    }


def stale_message(pending: dict) -> str:
    """One line: what moved, what it needs, and the one command that applies it.

    `/update-fleet <before>` and not the sync's own report: the sync already
    happened, so a lead that ran the skill bare would find nothing behind origin
    and apply nothing. The sha is where the skill diffs from."""
    before = pending.get("before", "")[:8]
    paths = []
    for value in pending.get("actions", {}).values():
        paths += [p for p in value.split() if p not in paths]
    shown = " ".join(paths[:NAMED]) + ("" if len(paths) <= NAMED else f" and {len(paths) - NAMED} more")
    return (
        f"fleet reconciler: fast-forwarded this checkout {before}..{pending.get('after', '')[:8]}; "
        f"it needs {', '.join(pending.get('actions', {}))} ({shown}). "
        f"Run /update-fleet {before} to apply it — its last step hands this lead over."
    )


def stale(state_dir: str, text: str) -> int:
    """Tell the lead the checkout moved under it, once.

    THE SAME RULES AS THE READY NOTICE. Typed only into a lead at rest whose
    input line is provably empty; any other lead gets it posted `--no-wake`
    into its inbox, ONCE, and the typed line waits for rest. The post does not
    count as telling: `--no-wake` only enqueues, nothing delivers it, and a
    notice deleted after one was a lead never told. A new fast-forward folded
    in before the wake is news, and is posted again.

    Kept until delivered, across passes and restarts: by the next pass the sync
    has nothing left to say, so a notice dropped here is lost for good. A fleet
    with no lead configured has nobody to tell, and drops it.
    """
    try:
        report = json.loads(text or "{}")
    except ValueError:
        report = {}
    doc = read_stale(state_dir)
    pending = doc.get("pending") or {}
    if isinstance(report, dict) and report.get("actions"):
        pending = merge(pending, report)
    if not pending:
        return 0

    def wait(note: str) -> int:
        if note != doc.get("note"):
            print(note)
        write_stale(state_dir, pending, note)
        return 0

    name = lead_name()
    if not name:
        write_stale(state_dir, {}, "")
        return 0
    sid, status = lead_session(name)
    if not sid:
        return wait(f"the checkout moved, but {status}; the notice waits")
    line = stale_message(pending)
    held = f"{name} is {status}" if status not in AT_REST else ""
    if not held and not composer_empty(sid):
        held = f"{name}'s input line is not provably empty"
    if held:
        if not pending.get("posted"):
            pending["posted"] = post(sid, line, kind="fleet-stale")[0]
        where = " — noted in its inbox" if pending["posted"] else ""
        return wait(f"the checkout moved; {held}{where}; the wake waits")
    sent, why = wake(sid, line)
    if not sent:
        return wait(f"the checkout moved; could not wake {name}: {why}")
    write_stale(state_dir, {}, "")
    print(f"told {name} the checkout moved: {', '.join(pending['actions'])}")
    return 0


# --- the pass ----------------------------------------------------------------


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(
        description="wake the lead when ready work has no actor, or a worker has stalled",
    )
    ap.add_argument(
        "--state-dir",
        required=True,
        help="the reconciler's runtime directory, where what-was-told lives",
    )
    ap.add_argument(
        "--stale",
        action="store_true",
        help="tell the lead what a fast-forward needs: sync-checkout's --json report on stdin, or none to retry",
    )
    args = ap.parse_args(argv)
    if args.stale:
        return stale(args.state_dir, sys.stdin.read())

    try:
        plan = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(plan, dict):
        return 0
    ready = sorted(str(r) for r in (plan.get("ready") or []))
    # `plan` writes a row per stalled task; a bare ref is read as one too.
    stalled = sorted(
        str(r.get("task") if isinstance(r, dict) else r) for r in (plan.get("stalled") or [])
    )
    now = {"told": ready, "posted": ready, "stalled": stalled, "stalled_posted": stalled}

    # PRUNE, THEN COMPARE, and the prune is what makes a re-entry news. A task
    # that leaves a set — dispatched, blocked again, abandoned, or a worker
    # that came back to life — is forgotten, so if it comes back it is a
    # transition again rather than something the lead was already told about
    # weeks ago.
    state = read_state(args.state_dir)
    memory = {k: [r for r in (state.get(k) or []) if r in now[k]] for k in MEMORY}
    fresh = [r for r in ready if r not in memory["told"]]
    fresh_stalled = [r for r in stalled if r not in memory["stalled"]]
    if not fresh and not fresh_stalled:
        return say(args.state_dir, memory, "")

    what = "ready work" if fresh else "a stalled worker"
    name = lead_name()
    if not name:
        return say(args.state_dir, memory, f"{what}, but no lead session is configured")
    sid, status = lead_session(name)
    if not sid:
        return say(args.state_dir, memory, f"{what}, but {status}")

    # ONE LINE, carrying whichever set has news; a set with nothing new is not
    # repeated just because the other one changed.
    parts = []
    if fresh:
        parts.append(("told", "posted", message(ready)))
    if fresh_stalled:
        parts.append(("stalled", "stalled_posted", stalled_message(stalled)))
    line = " ".join(text for _, _, text in parts)

    held = f"{name} is {status}" if status not in AT_REST else ""
    if not held and not composer_empty(sid):
        held = f"{name}'s input line is not provably empty"
    if held:
        # The note is posted on its own transition, so a lead busy for an hour
        # finds one line per change in its mailbox and not one per pass.
        unposted = [
            r for told, posted, _ in parts for r in now[told]
            if r not in memory[told] and r not in memory[posted]
        ]
        if unposted:
            sent, _why = post(sid, line)
            if sent:
                for _, posted, _text in parts:
                    memory[posted] = now[posted]
        noted = all(
            r in memory[posted] for told, posted, _ in parts
            for r in now[told] if r not in memory[told]
        )
        where = " — noted in its inbox" if noted else ""
        return say(args.state_dir, memory, f"{what}; {held}{where}; the wake waits")

    sent, why = wake(sid, line)
    if not sent:
        return say(args.state_dir, memory, f"could not wake {name}: {why}")

    # Only a delivered wake is remembered, so a send that failed is retried on
    # the next pass rather than swallowed.
    for told, _, _text in parts:
        memory[told] = now[told]
    named = ready if fresh else stalled
    return say(
        args.state_dir,
        memory,
        f"woke {name}: {len(named)} task(s) {'ready' if fresh else 'stalled'} — "
        f"{', '.join(named[:NAMED])}",
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
