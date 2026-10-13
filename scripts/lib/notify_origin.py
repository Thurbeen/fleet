#!/usr/bin/env python3
"""Tell a task's origin how its task ended, and what its worker is asking.

Every task has an origin: the conversation it came from (`queue.py`'s
`add_origin`). The reconciler sends that conversation exactly two things, and
this module is both of them. Everything else the loop says — ready, stalled,
unprompted, reviews, a sync that needs a hand — is the conductor's, in
`notify_conductor.py`.

  OUTCOMES.  When `collect` closes a task `shipped`, and when `reap` (which
      `collect` runs) moves one to `landed` or `abandoned`, its origin gets
      one line: the outcome, the artifact, and for `abandoned` the why. Both
      shipped and landed are told: shipped is "the change request is up",
      landed is "it is on the base branch", and the two can be days apart.
      That is what lets a chat front end post the result back into the thread
      the request came from. `queue.py`'s `tell_origins` names the transitions
      — a task that MOVED this pass, never every task standing in a state, so
      a first run after an upgrade tells nobody about last month.
  QUESTIONS.  A worker that declared `needs-input` (`fleet queue declare`) on
      a task whose origin fleet can reach is asked about in that conversation,
      which holds the context to answer, instead of the conductor.
      `notify_conductor.py` hands its needs-input rows here first and keeps
      only what this does not take: no origin, or an origin no session is and
      no rule names, which fleet has no road to.

WHERE IT GOES is `queue.py`'s `origin_target`, the one resolver: a session
origin is that session; an `owners.conf` rule is a session found by name —
exactly one on this machine, neither the conductor nor a worker a task here
holds (`notify_delivery.owner_row`) — or a RELAY command, run with the origin
and the line as its last two arguments. A SESSION ORIGIN THAT IS GONE gets
nothing, and the log says why: the worker's question stays in its own pane,
where its brief told it to ask, and the outcome is in the record as it always
was. Nothing falls back to a lead.

HOW is `notify_delivery.py`'s, the same as the conductor's: typed only into a
session at rest whose input line is provably empty, else posted to its mailbox
`--no-wake` once while the wake waits; a relay bounded and never raised.

EACH ONCE, with a memory of its own beside the reconciler's runtime state and
never on a task — the reconciler writes no record. `origin_questions.json` is
what each origin was asked, per task and per declaration, so a second question
is news and the same one is not; it is written by the notifier the loop runs.
`origin_outcomes.json` holds outcomes not yet delivered, retried on every
`collect`, and the keys of those that were; it is written by `collect` and
`reap` under a lock of its own, since a hand can run either beside the loop.
An outcome whose relay fails is retried RELAY_TRIES times, then logged and
dropped: a command that never succeeds may not run forever. Without the
runtime directory there is no memory to keep, and an outcome is tried once.

Nothing here can fail the pass it rides on: every function returns lines for
the log and raises nothing a caller has to catch.

Requires: python3; `thurbox-cli` to reach a session.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time


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


delivery = _load_sibling("fleet_notify_delivery", "notify_delivery.py")
fleetqueue = delivery.fleetqueue

CHECKOUT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

QUESTIONS_FILE = "origin_questions.json"
OUTCOMES_FILE = "origin_outcomes.json"

# How many times a failing relay is run for one outcome before it is dropped.
RELAY_TRIES = 3
# How many delivered outcome keys are remembered. A transition is told once
# because `tell_origins` only names transitions; the keys only stop a retry
# racing a second `collect` from telling one twice, so the recent ones suffice.
REMEMBERED = 500
# How long a `collect` waits for another one that is delivering outcomes.
LOCK_WAIT_SECS = 60


def runtime_dir() -> str:
    """The reconciler's runtime directory, by `reconcile.py`'s own rule."""
    return os.path.join(CHECKOUT, os.environ.get("FLEET_RECONCILE_DIR") or os.path.join("orchestration", "reconcile"))


def one_line(text) -> str:
    return " ".join(str(text or "").split())


# --- reaching one origin -----------------------------------------------------


class Reach:
    """What one pass knows about who is listening, asked of thurbox at most once."""

    def __init__(self, conductor_id: str = "", held: set | None = None):
        self.conductor_id = conductor_id
        self.held = held or set()
        self._rows = None
        self._why = ""
        self._asked = False

    def rows(self) -> tuple[list | None, str]:
        if not self._asked:
            self._rows, self._why = delivery.session_rows()
            self._asked = True
        return self._rows, self._why

    def ids(self):
        rows, _why = self.rows()
        return None if rows is None else {str(r.get("id")) for r in rows if isinstance(r, dict)}

    def target(self, origin, rules):
        return fleetqueue.origin_target(origin, rules, sessions=self.ids())

    def row(self, target) -> tuple[str, dict | None, str]:
        """(a name for the log, the session row to speak to, why there is none)."""
        rows, why = self.rows()
        if rows is None:
            return target.value, None, why
        if target.kind == "owner":
            row, why = delivery.owner_row(target.value, self.held, self.conductor_id, rows)
            return (str(row.get("name")) if row else target.value), row, why
        row = next((r for r in rows if isinstance(r, dict) and str(r.get("id")) == target.value), None)
        if row is None:
            return target.value, None, f"thurbox does not list session {target.value}"
        return str(row.get("name") or target.value), row, ""


def tell(reach: Reach, target, line: str, entry: dict, kind: str) -> tuple[str, str]:
    """Deliver `line` to `target` once: ("sent" | "wait" | "dropped", a note).

    `entry` is this notice's memory and is updated in place: `posted` once its
    mailbox note is left, `tries` for a relay that failed.
    """
    if target.kind == "relay":
        sent, why = delivery.relay(target, line)
        if sent:
            return "sent", f"relayed to {target.origin} by the rule for {target.rule!r}"
        entry["tries"] = int(entry.get("tries") or 0) + 1
        if entry["tries"] >= RELAY_TRIES:
            return "dropped", f"{why}; given up after {entry['tries']} tries"
        return "wait", why
    name, row, why = reach.row(target)
    if row is None:
        return "wait", f"{why}; it waits"
    sid = str(row["id"])
    busy = delivery.held(name, row)
    if busy:
        if not entry.get("posted"):
            entry["posted"] = delivery.post(sid, line, kind=kind)[0]
        where = " — noted in its inbox" if entry["posted"] else ""
        return "wait", f"{busy}{where}; the wake waits"
    sent, why = delivery.wake(sid, line)
    if not sent:
        return "wait", f"could not wake {name}: {why}"
    return "sent", f"told {name}"


def conductor_id(rules: list) -> str:
    """The conductor's session id, which no owners rule may name; "" when no
    session rule exists to need it, or there is no conductor to name."""
    if not any(kind == "session" for _prefix, kind, _target in rules):
        return ""
    conductor = _load_sibling("fleet_notify_conductor", "notify_conductor.py")
    return str((conductor.conductor_row()[1] or {}).get("id") or "")


def tasks_and_holders():
    """({ref: Task}, the session ids this fleet's tasks hold), or ({}, set()) unread."""
    try:
        tasks = fleetqueue.Queue(fleetqueue.queue_root(), scope="all").tasks
    except Exception:  # noqa: BLE001 — a notifier may not fail the pass it rides on
        return {}, set()
    return tasks, {str(t.doc["session"]) for t in tasks.values() if t.doc.get("session")}


# --- questions ---------------------------------------------------------------


def question_line(ref: str, why: str) -> str:
    """One line: which task asks, what, and the command that answers it."""
    asked = one_line(why) or "(no question given; read its pane)"
    return (f"fleet: {ref} needs input — {asked}. Answer: uv run --project {CHECKOUT} "
            f"fleet queue send {ref} '<answer>'")


def questions(state_dir: str, rows: list, conductor: str = "") -> tuple[set, str]:
    """Route `plan --json`'s needs-input rows to their origins: (refs taken, a note).

    A ref taken is one the conductor is not told of: delivered to its origin,
    waiting for it, or dropped because its session origin is gone. `conductor`
    is the conductor's session id, which no owners rule may name.
    """
    path = os.path.join(state_dir, QUESTIONS_FILE)
    memory = delivery.read_memory(path)
    asked = memory.get("asked") if isinstance(memory.get("asked"), dict) else {}
    refs = {str(r.get("task")) for r in rows}
    # A worker no longer asking is forgotten, so asking again is news.
    asked = {ref: entry for ref, entry in asked.items() if ref in refs and isinstance(entry, dict)}
    if not rows:
        if memory.get("asked"):
            delivery.write_memory(path, {"asked": {}})
        return set(), ""
    tasks, held = tasks_and_holders()
    if not tasks:
        return set(), ""
    rules = fleetqueue.origin_rules()
    reach = Reach(conductor or conductor_id(rules), held)
    taken, notes = set(), []
    for row in sorted(rows, key=lambda r: str(r.get("task"))):
        ref = str(row.get("task"))
        task = tasks.get(ref)
        target = reach.target(task.doc.get("origin"), rules) if task else None
        if target is None or target.kind == "unrouted":
            continue
        taken.add(ref)
        note = ask(reach, target, row, asked)
        if note:
            notes.append(note)
    delivery.write_memory(path, {"asked": asked})
    return taken, "; ".join(notes)


def ask(reach: Reach, target, row: dict, asked: dict) -> str:
    """Put one worker's question to its origin, once per declaration: the note to log.

    `asked` is the questions memory, updated in place: an entry per ref, keyed
    by when the worker declared, `done` once it was told or dropped.
    """
    ref = str(row.get("task"))
    key = str(row.get("declared") or "")
    entry = asked.get(ref)
    if not isinstance(entry, dict) or entry.get("key") != key:
        entry = asked[ref] = {"key": key}
    if entry.get("done"):
        return ""
    if target.kind == "gone":
        entry["done"] = True
        return (f"{ref} needs input, but its origin {target.origin} is gone ({target.why}); "
                "the question stays in its own pane")
    outcome, note = tell(reach, target, question_line(ref, row.get("why")), entry, "fleet-question")
    if outcome != "wait":
        entry["done"] = True
    return f"{ref} needs input: {note}"


# --- outcomes ----------------------------------------------------------------


def outcome_line(task, word: str) -> str:
    """One line: which task, how it ended, its artifact, and for `abandoned` why."""
    line = f"fleet: {task.ref} {word}"
    title = one_line(task.doc.get("title"))
    if title:
        line += f" ({title})"
    artifact = task.doc.get("artifact")
    if isinstance(artifact, list):
        urls = [str(v.get("url") if isinstance(v, dict) else v) for v in artifact if v]
    else:
        urls = [str(artifact)] if artifact else []
    if urls:
        line += " — " + " ".join(one_line(u) for u in urls)
    if word == "abandoned":
        given_up = task.doc.get("abandoned") or {}
        why = one_line(given_up.get("why"))
        how = fleetqueue.ABANDON_HOW.get(str(given_up.get("how") or "hand"), "")
        line += f" — {how}" + (f": {why}" if why else "")
    return line


def outcomes(events: list, state_dir: str | None = None) -> list[str]:
    """Record each (task, word) for its origin, then deliver every one waiting.

    Returns lines for `collect`'s report. Run on every `collect` and `reap`,
    with no events too, which is what retries an outcome that waited.
    """
    state_dir = state_dir or runtime_dir()
    path = os.path.join(state_dir, OUTCOMES_FILE)
    if not os.path.isdir(state_dir):
        # No runtime directory, no memory: try each once and keep nothing.
        doc = {"pending": {}, "sent": []}
        add(doc, events)
        return flush(doc, keep=False)
    try:
        with fleetqueue.fleet_platform.exclusive_lock(path + ".lock", wait=LOCK_WAIT_SECS):
            doc = delivery.read_memory(path)
            add(doc, events)
            lines = flush(doc, keep=True)
            delivery.write_memory(path, {"pending": doc["pending"], "sent": doc["sent"][-REMEMBERED:]})
            return lines
    except fleetqueue.fleet_platform.LockHeld:
        return [f"origin: {task.ref} {word} not told — another collect is delivering outcomes"
                for task, word in events]
    except OSError as exc:
        return [f"origin: outcomes could not be told: {exc}"]


def add(doc: dict, events: list) -> None:
    pending = doc.get("pending") if isinstance(doc.get("pending"), dict) else {}
    sent = [k for k in (doc.get("sent") or []) if isinstance(k, str)]
    for task, word in events:
        origin = task.doc.get("origin")
        key = f"{task.ref}:{word}"
        if not origin or key in sent or key in pending:
            continue
        pending[key] = {"origin": origin, "line": outcome_line(task, word), "at": time.time()}
    doc["pending"], doc["sent"] = pending, sent


def deliver_outcome(reach: Reach, rules: list, key: str, entry: dict, keep: bool) -> tuple[str, str]:
    """Tell one pending outcome to its origin: ("sent" | "wait" | "dropped", a log line)."""
    target = reach.target(entry.get("origin"), rules)
    if target is None or target.kind in ("gone", "unrouted"):
        why = target.why if target else "no origin"
        return "dropped", f"origin: {key} told nobody — {entry.get('origin')}: {why}"
    outcome, note = tell(reach, target, str(entry.get("line")), entry, "fleet-outcome")
    if outcome == "wait" and not keep:
        note += ", but there is no runtime directory to keep it in, so it is dropped"
    return outcome, f"origin: {key} {note}"


def flush(doc: dict, keep: bool) -> list[str]:
    """Deliver every pending outcome, in the order they happened."""
    pending = doc["pending"]
    if not pending:
        return []
    _tasks, held = tasks_and_holders()
    rules = fleetqueue.origin_rules()
    reach = Reach(conductor_id(rules), held)
    lines = []
    for key in sorted(pending, key=lambda k: (pending[k].get("at") or 0, k)):
        outcome, line = deliver_outcome(reach, rules, key, pending[key], keep)
        lines.append(line)
        if outcome == "sent":
            doc["sent"].append(key)
        if outcome != "wait" or not keep:
            pending.pop(key)
    return lines
