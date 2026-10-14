#!/usr/bin/env python3
"""Tell the conductor that ready work exists which nothing will dispatch, a worker
stalled, was never briefed, or asked with nowhere else to ask, review is owed,
or a sync needs a hand.

THE CONDUCTOR is the session that keeps the work moving. A fleet whose
`fleet.conf` says `CONDUCTOR=on` runs one of its own — the second
`[[sessions]]` of its rendered manifest, opening `orchestration/conductor/` —
and every notice here goes to it WHILE IT RUNS. Otherwise, and whenever that
session is not running, the conductor IS the lead: a single Mission Control is
both the only origin and the conductor, and every notice reaches exactly the
session it always did. `conductor_row` is the one function that says which
session it is. What a task's
ORIGIN is told — its outcome, its worker's question — is `notify_origin.py`'s.
HOW either is said is `notify_delivery.py`'s, shared by both.

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
than silence. The second and third are `notify_delivery.py`'s now, kept there
for this and `notify_origin.py` alike; they are told here where they were
learned:

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
told once and fresh ready work is still news beside it. A worker that SAID it
needs a decision (`fleet queue declare <ref> needs-input`) is not a stall but
the same silence named by the worker itself: `plan --json`'s `needs_input`,
carried the same way as a set of its own.

AN UNPROMPTED WORKER IS THE SILENCE BEFORE THE WORK. A dispatched task whose
record still says `prompted: false` minutes later never got its brief — a
handoff that failed and was never retried, or a session attached by hand — and
it is not at rest, so the stalled set never named it. `plan --json` names it
(`unprompted`: `queue.py`'s `unprompted_tasks` owns the judgement) and it is a
third set here, under the same rules.

PENDING INDEPENDENT REVIEW IS WORK AFTER PUBLICATION. `plan --json` exposes
the configured review contract's obligations, including a missing reviewer,
a finished turn with no assessment, and findings with no existing fixer.
Those are told under the same safe delivery and conversation rules. The
reconciler never replaces the recipient or declares a review clean.

WHOSE NOTICE IT IS. All five operational notices are the conductor's,
whatever a task's origin: `owners.conf` no longer routes them to an owner
session. The one exception is a worker's question. A `needs-input` row whose
task has an origin fleet can reach — a session, an owners rule's session, or a
relay — is handed to `notify_origin.questions` first and goes THERE; one whose
session origin is gone goes nowhere, and stays in the worker's pane. Only what
is left, no origin or an origin fleet has no road to, reaches the conductor.

WHERE THE STATE LIVES. In the reconciler's own runtime directory, beside its
pid, heartbeat and flags — never on the task. "The lead has been told" is a
fact about one machine's loop and one conversation; it is not part of what a
task IS, and writing it onto a record would make this the second writer over
the queue. `scripts/lib/reconcile.py` still writes no record. It is replaced in
one step through `write_record`: written in place, a reader racing the writer
read a torn file as "nothing was told", and the lead was told twice.

AND IT SAYS WHICH CONVERSATION. The record's `lead` names the lead it is true
of, as two ids `session list` carries: `session`, thurbox's id, which owns the
mailbox; `conversation`, the agent's, which owns what was typed. `session
delete` and the extension's self-heal mint both anew, and a fresh lead that
inherited "already told" was never told. So a new conversation forgets what
was typed, a new session also forgets what was posted, and `restart` or
`refuel`, which resume the same conversation, forget nothing. A lead nobody
can see this pass is not a new one: its record stands. A record from before
the key names no lead, and resets once.

Usage (it is `scripts/lib/reconcile.py`'s, and nothing else's):

    uv run fleet queue plan --json | uv run python scripts/lib/notify_conductor.py --state-dir DIR

    uv run fleet sync-checkout --json | uv run python scripts/lib/notify_conductor.py --state-dir DIR --stale

The second is the STALE CHECKOUT: the loop fast-forwarded the checkout and what
arrived needs a hand. `stale()` owns its one difference from the ready notice.

It prints a line worth logging, or nothing, and it exits 0 whatever happens.
A notifier that can fail the pass it rides on would cost the reconciler the
`collect` it just did, which is a real loss traded for a message.

Environment:
  FLEET_LEAD_SESSION       the lead session's name, overriding the rendered
                           extension.toml. The gate uses it, since a rendered
                           manifest belongs to the operator's checkout and is not
                           something a test may write.
  FLEET_CONDUCTOR_SESSION  the conductor session's name, the same way. Where
                           FLEET_LEAD_SESSION is set and this is not, there is
                           no conductor of its own: the override names the
                           whole setup, and the manifest is not read for half.

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
import sys


def _load_sibling(name: str, filename: str):
    """A scripts/lib module under a `fleet_` key, never shadowing the standard library."""
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# HOW a line reaches a session — at rest, into an empty line, else the mailbox
# without a wake — is `notify_delivery.py`'s, shared with `notify_origin.py`.
# This module decides WHO and WHAT; those names are bound here so a reader of
# this file reads the rules by the names they have always had.
delivery = _load_sibling("fleet_notify_delivery", "notify_delivery.py")
origins = _load_sibling("fleet_notify_origin", "notify_origin.py")
fleetqueue = delivery.fleetqueue
AT_REST, NAMED = delivery.AT_REST, delivery.NAMED
session_rows, identity = delivery.session_rows, delivery.identity
composer_empty, wake, post = delivery.composer_empty, delivery.wake, delivery.post

# The file names are the loop's runtime state from before the conductor had a
# name, and they keep them: a `notified.json` the lead was told by is what the
# conductor reads after the rename, so no pass tells it all again.
STATE_FILE = "notified.json"
# A stale-checkout notice not delivered yet. Deleted once it is, so the loop
# asks for a retry only while one waits.
STALE_FILE = "stale.json"

# What the loop remembers, per set: what was typed into the conductor, and what
# was left in its mailbox while it was mid-turn.
MEMORY = ("told", "posted", "stalled", "stalled_posted", "unprompted", "unprompted_posted",
          "needs_input", "needs_input_posted", "reviews", "reviews_posted")
# Which of those a new conversation forgets, and which only a new session does:
# a mailbox note still waiting in the same session's inbox is not news twice.
TYPED = ("told", "stalled", "unprompted", "needs_input", "reviews")
POSTED = ("posted", "stalled_posted", "unprompted_posted", "needs_input_posted", "reviews_posted")


# --- what the loop remembers -------------------------------------------------


def read_state(state_dir: str) -> dict:
    return delivery.read_memory(os.path.join(state_dir, STATE_FILE))


def write_state(state_dir: str, memory: dict, note: str) -> None:
    """Remember what the conductor has been told, and what was last logged about it.

    `memory` holds pairs of ref lists: `told` and `posted` for the ready set,
    `stalled` and `stalled_posted` for the stalled one, `unprompted` and
    `unprompted_posted` for the unprompted one, `needs_input` and
    `needs_input_posted` for the workers that asked — what was typed into the
    conductor, and what was left in its mailbox, plus `reviews` and
    `reviews_posted` for independent review obligations — and `lead`, which
    conversation they are true of (see WHERE THE STATE LIVES). The key is
    still `lead`, the word it was written under, so a file from before the
    conductor reads as the same conversation and tells it nothing twice.

    Into the loop's runtime directory, and NEVER making it — `notify_delivery`'s
    `write_memory` owns that rule and why.
    """
    doc = {**{k: memory.get(k) or [] for k in MEMORY}, "note": note}
    if memory.get("lead"):
        doc["lead"] = memory["lead"]
    delivery.write_memory(os.path.join(state_dir, STATE_FILE), doc)


def say(state_dir: str, memory: dict, note: str) -> int:
    """Print `note` only if it is not the one already standing.

    The deduplication is the difference between a diagnosis and a wall. A
    conductor that is away for an hour, or busy for one, is one fact; at the
    collect cadence it would otherwise be thirty log lines saying it again.
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


def own_conductor_name() -> str:
    """What this fleet's OWN conductor session is called, or "" when it runs none.

    The rendered manifest's second `[[sessions]]`, read the way `lead_name`
    reads the first. "" is a fleet that did not turn the conductor on, and an
    ordinary one: its lead is the conductor.
    """
    override = os.environ.get("FLEET_CONDUCTOR_SESSION", "").strip()
    if override:
        return override
    if os.environ.get("FLEET_LEAD_SESSION", "").strip():
        return ""
    root = fleetqueue.checkout_root()
    name, _repo = fleetqueue.manifest_conductor(os.path.join(root, "extension.toml"))
    if not name or "__" in name:
        return ""
    return name


def own_conductor_row(rows: list | None = None) -> tuple[str, dict | None, str]:
    """(the own conductor's name, its `session list` row, why there is none); ("", None, "") without one."""
    name = own_conductor_name()
    if not name:
        return "", None, ""
    row, why = session_row(name, fleetqueue.is_conductor_dir, rows)
    return name, row, why


def conductor_row() -> tuple[str, dict | None, str]:
    """(the conductor's name, its `session list` row, why there is none).

    THE ONE PLACE THAT ANSWERS IT. This fleet's own conductor while it runs;
    the lead otherwise — a fleet that runs none, and one whose conductor was
    deleted, closed or never spawned — because a notice nobody receives is the
    six hours this module exists to prevent. A switch either way is a new
    session to `forget_for`, so whichever one takes over is told the standing
    sets afresh rather than inheriting "already told".
    """
    _own, row, _why = own_conductor_row()
    if row:
        return _own, row, ""
    name = lead_name()
    row, status = lead_row(name) if name else (None, "")
    return name, row, status


def conductor_name() -> str:
    """What the conductor session is called right now, or "" when nothing here can tell."""
    return conductor_row()[0]


def control_sessions(rows: list | None = None) -> dict[str, str]:
    """{session id: "lead" | "conductor"} for this checkout's running control sessions.

    What `reap`, `refuel` and `sessions orphans` refuse or enumerate as the
    lead's. Either missing is simply absent; nothing here guesses.

    `rows` is a `session list` the caller already took. `reap` passes its
    pass's one list, so the lead is found in the same answer every other
    judgement of the pass reads; None asks thurbox here.
    """
    found: dict[str, str] = {}
    name = lead_name()
    if name:
        row, _why = lead_row(name, rows)
        if row:
            found[str(row["id"])] = "lead"
    _own, row, _why = own_conductor_row(rows)
    if row:
        found[str(row["id"])] = "conductor"
    return found


def lead_session(name: str) -> tuple[str | None, str]:
    """(session id, state) for the named session, or (None, why not)."""
    row, why = lead_row(name)
    return (str(row["id"]), str(row.get("state") or "unknown")) if row else (None, why)


def lead_row(name: str, rows: list | None = None) -> tuple[dict | None, str]:
    """This checkout's lead's `session list` row, or (None, why not).

    The one answer to "which session is the lead": the wake below and
    `reconcile.py`'s `is_lead` both read it, so they cannot disagree.
    """
    return session_row(name, fleetqueue.is_this_checkout, rows)


def session_row(name: str, here_is, rows: list | None = None) -> tuple[dict | None, str]:
    """The `session list` row named `name` whose cwd `here_is` accepts, or (None, why not).

    `here_is` is the directory the session opens: the checkout for the lead,
    its `orchestration/conductor` for the conductor.

    `session list` and not `session get`: the list carries `id`, `state` and
    `agent_session_id` for every session in one call and probes no pane, which
    is the cheaper of the two answers and the one that does not touch the lead
    to ask about it. `rows` is one already taken; None asks for it.
    """
    why = ""
    if rows is None:
        rows, why = session_rows()
    if rows is None:
        return None, why
    named = [r for r in rows if isinstance(r, dict) and r.get("name") == name and r.get("id")]
    if not named:
        return None, f"no session named {name!r} is running"
    # thurbox mirrors every host it reaches, and a Mission Control on a remote
    # host beside this one is a normal fleet wearing the same name. The lead is
    # the row on THIS machine whose cwd is THIS checkout — `session_place` and
    # `is_this_checkout`, the rule `peers.py` tells a peer from itself by.
    # A sole legacy row without cwd still answers, unless its backend says it
    # runs elsewhere. Known metadata must never contradict checkout ownership.
    here = [r for r in named if delivery.is_local(r) and here_is(r.get("cwd"))]
    if len(here) == 1:
        return here[0], ""
    if len(named) == 1 and not named[0].get("cwd") and delivery.is_local(named[0]):
        return named[0], ""
    return None, (f"{len(named)} sessions are named {name!r} and "
                  + ("several are" if here else "none of them is")
                  + " this checkout's on this machine")


def forget_for(memory: dict, lead: dict) -> dict:
    """`memory` as the conversation `lead` knows it: what another conversation
    was told is not what this one was, and another session's mailbox is not
    its own."""
    was = memory.get("lead") if isinstance(memory.get("lead"), dict) else {}
    kept = dict(memory, lead=lead)
    if was.get("conversation") != lead["conversation"]:
        kept.update({k: [] for k in TYPED})
    if was.get("session") != lead["session"]:
        kept.update({k: [] for k in POSTED})
    return kept


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


def needs_input_message(asking: list) -> str:
    """One line: which workers said they need a decision, and where to read the question."""
    shown = ", ".join(asking[:NAMED])
    more = "" if len(asking) <= NAMED else f" and {len(asking) - NAMED} more"
    return (
        f"fleet reconciler: {len(asking)} worker(s) need input — they said so — "
        f"{shown}{more}. Look: uv run fleet queue show {asking[0]}, then fleet queue send"
    )


def unprompted_message(unprompted: list) -> str:
    """One line: which dispatched workers never got their brief, and how to send it."""
    shown = ", ".join(unprompted[:NAMED])
    more = "" if len(unprompted) <= NAMED else f" and {len(unprompted) - NAMED} more"
    return (
        f"fleet reconciler: {len(unprompted)} worker(s) dispatched and never prompted "
        f"— {shown}{more}. Look at its pane, then: uv run fleet queue prompt {unprompted[0]}"
    )


# --- the stale checkout ------------------------------------------------------


def read_stale(state_dir: str) -> dict:
    return delivery.read_memory(os.path.join(state_dir, STALE_FILE))


def write_stale(state_dir: str, pending: dict, note: str) -> None:
    """Keep a notice still to deliver; with none, remove the file. Into the
    runtime directory and never making it, for `write_state`'s reason."""
    path = os.path.join(state_dir, STALE_FILE)
    if not pending:
        delivery.forget_memory(path)
        return
    delivery.write_memory(path, {"pending": pending, "note": note})


def merge(pending: dict, report: dict) -> dict:
    """Two fast-forwards before the conductor heard of the first are one notice:
    the earliest `before`, the latest `after`, and every action with every path."""
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
        f"Run /update-fleet {before} to apply it — its last step hands the lead over."
    )


def stale(state_dir: str, text: str) -> int:
    """Tell the conductor the checkout moved under it, once.

    THE SAME RULES AS THE READY NOTICE. Typed only into a conductor at rest
    whose input line is provably empty; any other gets it posted `--no-wake`
    into its inbox, ONCE, and the typed line waits for rest. The post does not
    count as telling: `--no-wake` only enqueues, nothing delivers it, and a
    notice deleted after one was a conductor never told. A new fast-forward
    folded in before the wake is news, and is posted again.

    Kept until delivered, across passes and restarts: by the next pass the sync
    has nothing left to say, so a notice dropped here is lost for good. A fleet
    with no conductor configured has nobody to tell, and drops it.
    """
    doc = read_stale(state_dir)
    pending = stale_pending(doc, text)
    if not pending:
        return 0
    name, row, status = conductor_row()
    if not name:
        write_stale(state_dir, {}, "")
        return 0
    note = stale_wait(name, row, status, pending)
    if note:
        if note != doc.get("note"):
            print(note)
        write_stale(state_dir, pending, note)
        return 0
    write_stale(state_dir, {}, "")
    print(f"told {name} the checkout moved: {', '.join(pending['actions'])}")
    return 0


def stale_pending(doc: dict, text: str) -> dict:
    """The notice still owed: what waited, with sync-checkout's new report folded in."""
    try:
        report = json.loads(text or "{}")
    except ValueError:
        report = {}
    pending = doc.get("pending") or {}
    if isinstance(report, dict) and report.get("actions"):
        pending = merge(pending, report)
    return pending


def stale_wait(name: str, row: dict | None, status: str, pending: dict) -> str:
    """Try to type the stale notice: "" once it is, else the note to log while it waits.

    A conductor that may not be typed into gets the notice in its mailbox
    once, recorded in `pending["posted"]`."""
    if not row:
        return f"the checkout moved, but {status}; the notice waits"
    sid = str(row["id"])
    line = stale_message(pending)
    why = delivery.held(name, row)
    if why:
        if not pending.get("posted"):
            pending["posted"] = post(sid, line, kind="fleet-stale")[0]
        where = " — noted in its inbox" if pending["posted"] else ""
        return f"the checkout moved; {why}{where}; the wake waits"
    sent, why = wake(sid, line)
    return "" if sent else f"the checkout moved; could not wake {name}: {why}"


def reviews_message(reviews: list) -> str:
    return (
        f"fleet reconciler: {len(reviews)} independent review obligation(s) pending. "
        "Run: uv run fleet queue plan --json; restore the configured reviewer or "
        "fixer if absent, and finish review of each current head."
    )


# --- the pass ----------------------------------------------------------------


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(
        description="wake the conductor when ready work has no actor, or a worker has stalled or was never prompted",
    )
    ap.add_argument(
        "--state-dir",
        required=True,
        help="the reconciler's runtime directory, where what-was-told lives",
    )
    ap.add_argument(
        "--stale",
        action="store_true",
        help="tell the conductor what a fast-forward needs: sync-checkout's --json report on stdin, or none to retry",
    )
    args = ap.parse_args(argv)
    if args.stale:
        return stale(args.state_dir, sys.stdin.read())

    try:
        plan = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(plan, dict):
        return 0
    ready = sorted(str(r) for r in (plan.get("ready") or []))
    # `plan` writes a row per stalled, unprompted or asking task; a bare ref is
    # read as one too.
    stalled, unprompted = (
        sorted(str(r.get("task") if isinstance(r, dict) else r) for r in (plan.get(key) or []))
        for key in ("stalled", "unprompted")
    )
    reviews = sorted(str(r.get("task")) for r in (plan.get("reviews") or []) if isinstance(r, dict))
    notes = []

    # A QUESTION IS ITS ORIGIN'S. A worker that declared `needs-input` on a task
    # with an origin fleet can reach is asked about in THAT conversation, which
    # holds the context to answer; one whose session origin is gone is told to
    # nobody, and its question stays in its own pane. What is left — no origin,
    # or one fleet has no way to — is the conductor's, as it always was.
    rows = [r if isinstance(r, dict) else {"task": str(r)} for r in (plan.get("needs_input") or [])]
    try:
        theirs, note = origins.questions(args.state_dir, rows)
    except Exception as exc:  # noqa: BLE001 — a notifier may not fail the pass it rides on
        theirs, note = set(), f"questions could not be routed to their origins: {exc}"
    notes.append(note)
    asking = sorted(str(r.get("task")) for r in rows if str(r.get("task")) not in theirs)

    now = {"told": ready, "stalled": stalled, "unprompted": unprompted,
           "needs_input": asking, "reviews": reviews}
    for told, posted in zip(TYPED, POSTED):
        now[posted] = now[told]

    # PRUNE, THEN COMPARE, and the prune is what makes a re-entry news. A task
    # that leaves a set — dispatched, blocked again, abandoned, or a worker
    # that came back to life — is forgotten, so if it comes back it is a
    # transition again rather than something the conductor was already told
    # about weeks ago.
    state = read_state(args.state_dir)
    memory = {k: [r for r in (state.get(k) or []) if r in now[k]] for k in MEMORY}
    memory["lead"] = state.get("lead")
    memory, note = deliver(now, memory, conductor_row, "no conductor session is configured")
    notes.insert(0, note)
    return say(args.state_dir, memory, "; ".join(n for n in notes if n))


def post_news(sid: str, line: str, parts: list, now: dict, memory: dict) -> bool:
    """Leave `line` in a busy conductor's mailbox, once per change; True when
    everything new in it is noted there.

    The note is posted on its own transition, so a conductor busy for an hour
    finds one line per change in its mailbox and not one per pass. `memory`
    is updated in place.
    """
    unposted = [
        r for told, posted, _ in parts for r in now[told]
        if r not in memory[told] and r not in memory[posted]
    ]
    if unposted and post(sid, line)[0]:
        for _, posted, _text in parts:
            memory[posted] = now[posted]
    return all(
        r in memory[posted] for told, posted, _ in parts
        for r in now[told] if r not in memory[told]
    )


# Each set, in the order its news is spoken: (typed, posted, line, what, word).
SETS = (
    ("told", "posted", message, "ready work", "ready"),
    ("stalled", "stalled_posted", stalled_message, "a stalled worker", "stalled"),
    ("unprompted", "unprompted_posted", unprompted_message, "an unprompted worker", "unprompted"),
    ("needs_input", "needs_input_posted", needs_input_message,
     "a worker that needs input", "asking for input"),
    ("reviews", "reviews_posted", reviews_message, "pending independent reviews", "review-pending"),
)


def deliver(now: dict, memory: dict, resolve, unnamed: str) -> tuple[dict, str]:
    """Tell the conductor what is news to it: (its memory now, the note to log).

    `resolve()` is (name, row, why not) — `conductor_row`.
    """
    if not any(now[k] for k in TYPED):
        return memory, ""

    # WHO IS LISTENING, before what is news to them: only asked while a set
    # holds something, so an idle fleet costs thurbox nothing.
    name, row, status = resolve()
    if row:
        memory = forget_for(memory, identity(row))
    news = [s for s in SETS if any(r not in memory[s[0]] for r in now[s[0]])]
    if not news:
        return memory, ""

    what = news[0][3]
    if not name:
        return memory, f"{what}, but {unnamed}"
    if not row:
        return memory, f"{what}, but {status}"
    sid = str(row["id"])

    # ONE LINE, carrying whichever set has news; a set with nothing new is not
    # repeated just because another one changed.
    parts = [(told, posted, say_it(now[told])) for told, posted, say_it, _w, _n in news]
    line = " ".join(text for _, _, text in parts)

    why = delivery.held(name, row)
    if why:
        where = " — noted in its inbox" if post_news(sid, line, parts, now, memory) else ""
        return memory, f"{what}; {why}{where}; the wake waits"

    sent, why = wake(sid, line)
    if not sent:
        return memory, f"could not wake {name}: {why}"

    # Only a delivered wake is remembered, so a send that failed is retried on
    # the next pass rather than swallowed.
    for told, _, _text in parts:
        memory[told] = now[told]
    named, word = now[news[0][0]], news[0][4]
    return memory, f"woke {name}: {len(named)} task(s) {word} — {', '.join(named[:NAMED])}"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
