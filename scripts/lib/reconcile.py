#!/usr/bin/env python3
"""The reconciler's lifecycle: a supervised loop that keeps the queue's records
level with the world, so the lead never has to remember to run anything.

WHY IT EXISTS. Four failures in one session, all the same shape: a state change
that emitted no event fleet was listening to. progress.jsonl empty on 19 of 20
tasks because nobody was running `watch`; three pull requests merged while the
board said `dispatched`; six workers sat on a spent token limit while thurbox
reported a stale `working`; a session reaped and learned of from an error.

THREE CLASSES, THREE MECHANISMS:

  thurbox EMITS it     a turn ends, a session is deleted. Consume the stream
                       CONTINUOUSLY: `fleet queue watch`, looped.
  only the FORGE knows a pull request merged. Poll it cheaply: `collect` and
                       `shepherd` already batch their forge calls.
  a NON-EVENT          a worker ran out of quota and stopped emitting.
                       Detectable only by ABSENCE, on a timer.

That third row is why hooks alone cannot be the answer: a worker that died on a
token limit fires no hook at all. Hooks are the accelerator (`nudge`); the timer
is the guarantee.

WHAT IT IS NOT. Not a cron: a cron gives no supervision, no adoption of a running
instance and no durable stop. This is a loop the OPERATOR starts and stops:

  `ensure`  start it unless it is running or has been asked down. Adopts a live
            loop on the code it would start with, and replaces one on older
            code (IT RUNS WHAT THE CHECKOUT SAYS, below); never a second one
            over one queue.
  `stop`    writes a DOWN FLAG to disk first, then waits for the loop to see it.
            The flag is what makes "down" survive a restart, a reboot and the
            next skill run.
  `start`   the operator asking for it back. It CLEARS the flag. That is the
            whole difference between the two.

WHAT BRINGS IT BACK. A lock proves it is up and nothing restarted it once it was
not: three leads found it down with no down flag eighteen times, after a reboot
or a lead restart. Two things now do, and neither clears a flag:

  `serve`   what a service manager runs (`reconcile_service.py`, written by
            `fleet install`): the loop in the FOREGROUND, for good. While the
            down flag stands or another loop holds the lock it WAITS rather
            than exits, so its manager never restarts it into a fight, and it
            takes over the moment that other loop dies.
  `ensure --if-lead`  the SessionStart hook in `.claude/settings.json`. Every
            session in a fleet checkout runs that file, and a worker running
            this loop would reap its own session, so it acts only for the
            sessions this checkout's extension names — its lead and, where
            the fleet runs one, its conductor, whose own directory carries
            the same hook — prints nothing for any other, and exits 0
            whatever happens. An origin is neither: many may come and go,
            and none of them owns this checkout's loop.

IS IT UP. The loop holds an exclusive lock on `lock` in its runtime directory for
its whole life. The OS drops a lock when its holder dies however it died, so a
dead loop holds no lock and no stale pid is ever trusted or signalled. Up means
the lock is held AND the loop has written a heartbeat: a supervisor whose loop
cannot run at all holds the lock and never beats, and is never adopted or called
healthy. The pidfile and heartbeat stay, for people.

IT RUNS WHAT THE CHECKOUT SAYS. A supervisor holds the modules it imported, and
the checkout moves under it — its own `sync` moves it. On 2026-10-14 a loop
started before an update kept the old `reconcile.py`, whose notifier that update
had renamed away, and every notice failed for eight hours while `status` said
"up". So the loop records the code it runs (`code.json`: each module of this
checkout it loaded, by mtime and size) and, every pass, compares it with the
disk: a change hands it to a fresh `reconcile ensure`, detached and once, which
brings it down at a boundary and starts the new code. Under `serve` a change is
an EXIT instead: its manager starts the new code, and a detached replacement
would be a loop no manager restarts. Whichever process takes the lock writes
the record as its own, so `serve` taking over from a dead loop on the modules
it already holds is not read as a loop that keeps none. `ensure` and `start`
replace an up loop whose record disagrees with the disk, or that keeps none,
rather than adopting it; `status` names one and exits 1. Children need none of
this: each pass starts them afresh, from the new bytes.

ITS CHILDREN'S FAILURES ARE IN `status`, NOT ONLY ITS LOG. Each child that fails
— a non-zero exit, a script that is not there, a bound — is kept in
`failures.json` by label (`collect`, `plan`, `notify`, `sync`, …) with a count,
since when and its last words, until that child next succeeds; `status` prints
each as a `failing` line and exits 1. So is the loop's OWN pass, as `tick`: one
that raises is restarted, and beats again before it raises again, so a pass
crashing every time held the lock and a fresh heartbeat while running nothing
past `root`. It stays `failing` until a pass runs to its end, and `ensure`
reports it rather than adopting it as healthy. Each notice decision is one `notify:
notice …` log line (`notify_delivery.notice`), so `grep 'notice '` says who was
told what, by which road, or why nobody was.

A LOOP FROM BEFORE THE UV PORT holds no lock: the bash `scripts/reconcile.sh`
supervisor wrote a pidfile only, and outlived the update that deleted its
script, failing every pass. `status` names one still running from this
checkout, and `ensure`, `start`, `stop` and `restart` end it. Proven by
its argv and working directory each time before it is signalled
(`is_legacy`), never by a pid.

IT GOES WITH WHAT IT RUNS FOR. A loop whose runtime directory is deleted — the
`lock` it holds there is the tell, because `nudge` makes the directory again
and never that — or whose FLEET_RECONCILE_PARENT_PID is gone, exits at the next
boundary. Only tests set the second: a run killed before its teardown would
otherwise leak a detached loop ticking against a deleted temp directory.

IT WRITES NO RECORD. Every effect on the queue goes through `fleet queue`, the
only writer over the records. Its own runtime directory is not an exception: a
pid, a heartbeat, a log, the flags, `code.json`, `failures.json`,
`notified.json`, `stale.json` and the `origin_*.json` memories are facts
about this loop on this machine, not about any task. It calls exactly `watch`,
`collect`, `shepherd`, `refuel` and the read-only `plan` (plus `root`, to check
it can). The one thing it changes outside the queue is the CHECKOUT, through
`sync-checkout`, which only ever fast-forwards a clean default branch.

IT DOES NOT DECIDE WHAT RUNS. No dispatch, no cancel, no reorder, and it does not
second-guess `refuel`'s rule about a spent quota window.

BUT IT SAYS WHEN THERE IS SOMETHING TO DECIDE. A task whose blocker clears is
READY and has no actor: this loop may not dispatch, and the lead only acts when
spoken to. So the pass after `collect` reads `plan` and, when the ready set has
grown — or `plan`'s stalled set has, workers at rest with nothing to show,
its unprompted set, workers dispatched and never briefed, or its needs-input
set, workers that said they need a decision —
wakes the CONDUCTOR, which until fleet runs one of its own is the lead: once
per transition and per conversation, never mid-turn, silently when there is
none. `scripts/lib/notify_conductor.py` owns those rules. A worker's question
on a task whose origin fleet can reach goes to that origin instead, and so does
each task's outcome, which `collect` and `reap` tell as they move it:
`scripts/lib/notify_origin.py`, a child of this loop's children and no new
verb in it.

AND IT KEEPS THE LEAD'S CHECKOUT CURRENT. `sync-checkout` ran only at
SessionStart, and one lead conversation lasted sixteen days: a merged fix sat
unpulled for five of them while the lead kept hitting the bug. So the loop runs
it on a clock of its own, honouring every refusal it has, and when what arrived
needs a hand — new instructions, a manifest to re-render, its own code — it
tells the lead once, with the `/update-fleet` that applies it
(`notify_conductor.py --stale`). Code the loop runs as a child, `queue.py` above
all, needs nothing: the next pass runs the new bytes.

Usage:
  uv run fleet reconcile ensure     # start unless running or asked down
  uv run fleet reconcile ensure --if-lead  # the same, from the lead's or conductor's session only
  uv run fleet reconcile serve      # the loop in the foreground, for a service
  uv run fleet reconcile start      # start, and clear a previous `stop`
  uv run fleet reconcile stop       # durably down: writes the flag, then stops
  uv run fleet reconcile restart    # stop and start, clearing the flag
  uv run fleet reconcile status     # ticking? since when? exit 1: down unasked, stalled,
                                   # on code older than the checkout, or a child failing
  uv run fleet reconcile nudge      # advisory: run the periodic pass NOW;
                                   # from a worker's Stop hook, asks once for a missing result.md
  uv run fleet reconcile hook       # print the worker Stop hook that nudges
  uv run fleet reconcile logs [-f]  # the loop's log

Runtime state lives in orchestration/reconcile/ and is GITIGNORED.

THE CADENCES, and why each number is the number. Each is the default of a
setting: `<NAME>_SECS` in `orchestration/reconcile.conf` (gitignored; the
tracked `reconcile.example.conf` sets none), read ONCE when the loop starts —
an edit takes effect at `restart` — and beaten by FLEET_RECONCILE_<NAME>_SECS
in the environment. `queue.py`'s `reconcile_conf` is the file's one reader.

  watch     20s per call, back to back: effectively continuous. The stream is a
            local socket, and `watch` resumes from each task's OWN floor, so
            consecutive calls lose nothing between them. 20s is how long the
            loop sits inside one call before it looks at the clock again, which
            is also the worst-case latency of a nudge and of a stop.
  collect   120s. Reads result.md off the disk, one forge call per task with a
            NEW result. Forty minutes of ignorance became two, and a nudge
            collapses it to seconds.
  refuel    300s. Reads the account's quota window, a network call to the vendor;
            the TUI pane holds the same reading to FUEL_TTL = 300s. The condition
            it looks for stands for STALE_WORKING_SECS = 30 min, so five minutes
            is six looks at a half-hour fact.
  shepherd  900s. The most expensive pass: a pull-request list per repo, then
            checks, reviews and mergeability, and one more call per open pull
            request a task owns, for its review comments. CI does not change
            faster; an operator who wants comments answered sooner sets
            SHEPHERD_SECS and pays for it in forge calls.
  notify    collect's clock. What makes a task ready is a landing `collect` has
            just recorded, so a clock of its own would ask at a worse moment.
  sync      900s. A fetch, bounded by `sync-checkout`'s own timeout. A merge on
            origin is a fact the lead can wait a quarter of an hour for, and it
            had been waiting days. 0 turns it off.

THE BOUNDS. Every child the loop runs is held to one, and past it is killed with
everything it started (`fleet_platform.run_bounded`), logged as one line naming
the verb and the bound, and the pass goes on. A queue command that never
returned froze the loop whole: no heartbeat, no collect, no shepherd, until a
person read `status`.

  watch     its own call plus 60s (WATCH_MARGIN_SECS): it is told how long to
            run, so anything past that and a minute is not a slow stream.
  the rest  600s each, `BOUND_SECS`: collect, shepherd, refuel, plan, the
            notification and the sync. A generous bound and not a tuned one —
            the slowest honest pass is a shepherd over many repositories, and
            it ends in minutes — so it only ever cuts off a child that is
            hung. A record cut off mid-write is not torn: `write_record`
            replaces a file in one step.

It is also what `status` calls STALLED. The heartbeat is written before every
child the loop starts, so one older than the longest child allowance plus
tree termination and pipe draining is a child that outran its bound and was
not ended — a loop that is stuck, not slow — and `status` exits 1 on it, as it
does on DOWN.

Environment:
  FLEET_RECONCILE_DIR           runtime state (default orchestration/reconcile)
  FLEET_RECONCILE_QUEUE_CMD     the queue command it drives, as an argv: a JSON
                                list, or a line split with shell quoting (on
                                Windows a backslash is an ordinary character)
                                and nothing else of a shell (default: `fleet
                                queue` on this interpreter). The seam tests
                                stub.
  FLEET_RECONCILE_WATCH_SECS    seconds per `watch` call   (default 20)
  FLEET_RECONCILE_COLLECT_SECS  seconds between collects   (default 120)
  FLEET_RECONCILE_REFUEL_SECS   seconds between refuels    (default 300)
  FLEET_RECONCILE_SHEPHERD_SECS seconds between shepherds  (default 900)
  FLEET_RECONCILE_SYNC_SECS     seconds between syncs; 0 is off (default 900)
  FLEET_RECONCILE_BOUND_SECS    the most any child but watch may run (default 600)
  FLEET_RECONCILE_SYNC_DIR      the checkout it keeps current (default: this one)
  FLEET_RECONCILE_CONF_ROOT     the checkout whose orchestration/reconcile.conf
                                is read (default: this one; tests set it)
  FLEET_RECONCILE_PARENT_PID    exit once this pid is gone (default: unset; tests set it)
  FLEET_QUEUE_DIR               the queue to reconcile (default: this checkout's)
  FLEET_LEAD_SESSION            the lead (and so conductor) session to wake;
                                read by notify_conductor.py

Requires: uv and whatever the pass needs: thurbox-cli for `watch`, `refuel` and
the notification, `gh` or `glab` for `collect` and `shepherd`, `quota-axi` for
fuel. Each degrades to "could not check" inside the queue, so a missing tool
costs its own pass and never the loop. With no forge CLI at all, shepherd is
skipped while none is on PATH — asked every pass, logged only when that
changes — so a forge installed while the loop runs is picked up with no restart.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field


def _load_sibling(name: str, filename: str):
    """A scripts/lib module under a `fleet_` key, never shadowing the standard library."""
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        # Never handed back half-made: the next load imports it again.
        sys.modules.pop(name, None)
        raise
    return mod


fleet_platform = _load_sibling("fleet_platform", "fleet_platform.py")

CHECKOUT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NOTIFY = os.path.join(CHECKOUT, "scripts", "lib", "notify_conductor.py")
# Where notify_conductor.py keeps a stale-checkout notice it has not delivered yet, and
# deletes it once it has: its presence is the whole question this loop asks.
STALE_FILE = "stale.json"
# The code the running loop started from, and how each child it runs last went
# wrong: what `status` reads to say "running old code" and "failing".
CODE_FILE = "code.json"
FAILURES_FILE = "failures.json"

# How fleet runs itself as a child: this interpreter, `fleet.cli.main`, off this
# checkout. No `uv` round trip per pass, and no shell on any OS.
BOOT = "import sys\nsys.path.insert(0, sys.argv[1])\nfrom fleet.cli import main\nsys.exit(main(sys.argv[2:]))"

COMMANDS = "ensure start stop restart status serve nudge hook logs"

# How long `start` waits for the first heartbeat. The beat is written before the
# first pass, so this waits on the loop being alive, not on a forge round trip.
START_WAIT_SECS = 30

# How long `stop` lets the loop notice the flag before it terminates the pid, on
# top of one watch call. A stop never cuts a queue command short: it lands at the
# next pass boundary, and only a loop still holding the lock past this is
# terminated, with the child it is running. Only a child past its own bound is
# killed mid-run, by the loop itself (THE BOUNDS, above).
STOP_GRACE_SECS = 30
KILL_WAIT_SECS = 10

# How far past its own --for-secs a `watch` may run before it is a hung one.
WATCH_MARGIN_SECS = 60

# A supervisor starting at the instant a `status` probes the lock sees it held
# for that instant; it retries this long before concluding a twin owns it.
LOCK_RETRY_SECS = 2

# How long a loop that handed itself to `ensure` waits, running nothing, to be
# brought down: that `ensure`'s own start-up, with room to spare.
REPLACE_WAIT_SECS = 2 * START_WAIT_SECS

# How often `serve` looks again while a down flag stands or another loop holds
# the lock. Its takeover after that loop dies is at most this late.
SERVE_POLL_SECS = 5

# A log a person can open: a pass every couple of minutes, forever, trims itself.
LOG_MAX_BYTES = 4 * 1024 * 1024
LOG_KEEP_LINES = 2000

# `watch` reports "0 task(s) moved" nearly every call. Anchored, because a moved
# count of 10 or 100 contains the same substring.
QUIET = re.compile(r"(^|\D)0 task\(s\) moved", re.MULTILINE | re.ASCII)


def runtime_dir() -> str:
    """The loop's runtime state: its lock, heartbeat, log and flags."""
    return os.path.join(CHECKOUT, os.environ.get("FLEET_RECONCILE_DIR") or os.path.join("orchestration", "reconcile"))


class BadSetting(ValueError):
    """A setting `Config.from_env` refuses. `main` turns it into exit 1; a hook
    catches it like any other error, and so never fails over one."""


@dataclass
class Config:
    rt: str
    queue_cmd: list
    queue_label: str
    watch: int
    collect: int
    refuel: int
    shepherd: int
    parent: int = 0
    sync: int = 0
    sync_dir: str = CHECKOUT
    bound: int = 600
    # {checkout-relative path: [mtime_ns, size]} of every module this process
    # runs from the checkout, taken once its loop starts (`record_code`).
    code: dict = field(default_factory=dict)
    # Run by `serve`: a code change ends the process, for its manager to restart.
    served: bool = False

    @classmethod
    def from_env(cls) -> Config:
        rt = runtime_dir()
        override = os.environ.get("FLEET_RECONCILE_QUEUE_CMD", "").strip()
        if not override:
            cmd, label = [sys.executable, "-c", BOOT, CHECKOUT, "queue"], "fleet queue"
        else:
            try:
                cmd = ([str(a) for a in json.loads(override)] if override.startswith("[")
                       else fleet_platform.split_command(override))
            except ValueError as exc:
                raise BadSetting(f"fleet reconciler: FLEET_RECONCILE_QUEUE_CMD: {exc}") from exc
            label = " ".join(cmd)

        # READ ONCE, HERE, at start — not every pass. The clocks are what
        # `stop` waits on (one watch call plus its grace) from another process,
        # and a loop that re-read them would have its stop computed against
        # numbers it was no longer running on. An edit takes effect at the next
        # `fleet reconcile restart`, which is what the file's header says.
        conf = _load_sibling("fleet_queue", "queue.py").reconcile_conf()

        def secs(name: str, default: int) -> int:
            env = os.environ.get(f"FLEET_RECONCILE_{name}_SECS")
            if env:
                return int(env)
            said = conf.get(f"{name}_SECS", "").strip()
            if not said:
                return default
            try:
                return int(said)
            except ValueError:
                raise BadSetting(
                    f"fleet reconciler: {name}_SECS={said!r} in reconcile.conf is not a whole number"
                ) from None

        return cls(rt, cmd, label, secs("WATCH", 20), secs("COLLECT", 120), secs("REFUEL", 300), secs("SHEPHERD", 900),
                   int(os.environ.get("FLEET_RECONCILE_PARENT_PID") or 0), secs("SYNC", 900),
                   os.environ.get("FLEET_RECONCILE_SYNC_DIR") or CHECKOUT, secs("BOUND", 600))

    def path(self, name: str) -> str:
        return os.path.join(self.rt, name)

    @property
    def log(self) -> str:
        return self.path("reconcile.log")

    @property
    def stall(self) -> int:
        # A NOTE on status, never an adoption test: one slow shepherd sweep is not
        # a wedged loop, and adoption turning on freshness would start a twin.
        return max(self.watch + WATCH_MARGIN_SECS, self.bound) + 2 * fleet_platform.BOUNDED_CLEANUP_SECS


# --- small things -------------------------------------------------------------


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def say(text: str) -> None:
    print(text, flush=True)


def err(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def indented(text: str, pad: str) -> str:
    return "".join(pad + line + "\n" for line in text.rstrip("\n").splitlines())


def log(cfg: Config, text: str) -> None:
    try:
        fleet_platform.append_record(cfg.log, f"[{now()}] {text}\n")
    except OSError:
        pass


def log_raw(cfg: Config, text: str) -> None:
    if text:
        try:
            fleet_platform.append_record(cfg.log, text)
        except OSError:
            pass


def who() -> str:
    return os.environ.get("USER") or os.environ.get("USERNAME") or "someone"


def child_env() -> dict:
    # A Python child writing into a pipe uses the locale's code page on Windows.
    return dict(os.environ, PYTHONIOENCODING="utf-8")


def wait_until(predicate, secs: float, step: float = 0.25) -> bool:
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


# --- is it up? ----------------------------------------------------------------


def lock_held(cfg: Config) -> bool:
    """Whether a live loop holds the lock. A dead one cannot, whatever its pidfile says."""
    lock = cfg.path("lock")
    if not os.path.exists(lock):
        return False
    try:
        with fleet_platform.exclusive_lock(lock):
            return False
    except fleet_platform.LockHeld:
        return True
    except OSError:
        return False


def running(cfg: Config) -> bool:
    """Up: the lock is held AND the loop has beaten once. A supervisor restarting a
    loop that cannot run is a live process and not a running reconciler."""
    try:
        beaten = os.path.getsize(cfg.path("heartbeat")) > 0
    except OSError:
        beaten = False
    return beaten and lock_held(cfg)


def pid_of(cfg: Config, name: str = "pid") -> int:
    """The supervisor's pid, or with `child` the one of the command it is running."""
    try:
        return int(read(cfg.path(name)).strip())
    except ValueError:
        return 0


def asked_down(cfg: Config) -> bool:
    return os.path.isfile(cfg.path("down"))


def beat_age(cfg: Config) -> int | None:
    try:
        return int(time.time()) - int(read(cfg.path("heartbeat")).strip())
    except ValueError:
        return None


def orphaned(cfg: Config) -> str:
    """Why this loop has nothing left to run for, or "".

    Its runtime directory was deleted under it, or the process it was told to
    watch is gone. The second is set only by tests: a test run killed before its
    teardown never stops the loop it started, which would otherwise tick against
    a deleted temp directory forever.

    THE LOCK FILE AND NOT THE DIRECTORY. `with_lock` makes both before the loop
    starts and the lock stays for its life, while `nudge` makes the directory
    alone whenever it is missing — from a worker's Stop hook, at any moment. A
    loop that read the directory would be brought back from the dead by a nudge
    racing its exit, and would go on ticking over a runtime nobody owns."""
    if not os.path.isfile(cfg.path("lock")):
        return f"{cfg.path('lock')} is gone"
    if cfg.parent and not fleet_platform.alive(cfg.parent):
        return f"parent pid {cfg.parent} is gone"
    return ""


def over(cfg: Config) -> bool:
    return asked_down(cfg) or bool(orphaned(cfg))


# --- the code it runs ---------------------------------------------------------


def loaded_code() -> dict:
    """{checkout-relative path: [mtime_ns, size]} of every module of this checkout
    this process has loaded — the code a long-lived loop holds in memory.

    What it RUNS AS A CHILD is not here: `fleet queue`, the notifier and the
    sync start afresh every pass and read the new bytes by themselves."""
    home = os.path.realpath(CHECKOUT)
    files = {}
    for mod in list(sys.modules.values()):
        path = getattr(mod, "__file__", None)
        if not path:
            continue
        try:
            rel = os.path.relpath(os.path.realpath(path), home).replace(os.sep, "/")
        except ValueError:  # Windows: another drive is not this checkout
            continue
        if not rel.startswith(("fleet/", "scripts/")):
            continue
        try:
            st = os.stat(os.path.join(home, rel))
        except OSError:
            continue
        files[rel] = [st.st_mtime_ns, st.st_size]
    return files


def code_changed(files: dict) -> list[str]:
    """Which of `files` differ on disk now — new bytes, or gone."""
    changed = []
    for rel, was in sorted(files.items()):
        try:
            st = os.stat(os.path.join(CHECKOUT, rel))
            if [st.st_mtime_ns, st.st_size] == list(was):
                continue
        except (OSError, TypeError):
            pass
        changed.append(rel)
    return changed


def record_code(cfg: Config, files: dict | None = None) -> dict:
    """Take the loop's code as it starts — or keep `files`, the code it already
    holds — and leave it, under this pid, where `status` reads it."""
    files = loaded_code() if files is None else files
    try:
        fleet_platform.write_record(cfg.path(CODE_FILE), json.dumps(
            {"pid": os.getpid(), "started": now(), "files": files}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return files


def stale_code(cfg: Config) -> tuple[list[str], str]:
    """(what the running loop's code is older than on disk, when it started), from its record.

    Asked of a loop that is up, which writes its record before its first beat.
    So an up loop with no record of its own — none, or another pid's — is one
    started before loops kept one, and it is older than any checkout that does."""
    try:
        doc = json.loads(read(cfg.path(CODE_FILE)) or "{}")
    except ValueError:
        doc = {}
    pid = pid_of(cfg)
    if (not isinstance(doc, dict) or not isinstance(doc.get("files"), dict)
            or (pid and doc.get("pid") not in (None, pid))):
        return ["its code (it keeps no record of it)"], "an unknown time"
    return code_changed(doc["files"]), str(doc.get("started") or "?")


def shown(paths: list[str]) -> str:
    return ", ".join(paths[:6]) + ("" if len(paths) <= 6 else f" and {len(paths) - 6} more")


def replace_me(cfg: Config, changed: list[str]) -> None:
    """Ask a fresh `reconcile ensure` — the NEW code — to replace this loop.

    A process cannot reload what it imported, and on 2026-10-14 the loop that
    fast-forwarded its own checkout kept running the old code for eight hours,
    calling a notifier that update had renamed away. `ensure` already knows how
    to bring a loop down at a pass boundary and start the next one, honours a
    down flag, and runs from the checkout as it is now; so the loop hands
    itself to it, detached, once, and waits for it (`tick`)."""
    log(cfg, f"its own code changed on disk ({shown(changed)}); `reconcile ensure` replaces it")
    try:
        with open(cfg.log, "ab") as out:
            fleet_platform.spawn_detached([sys.executable, "-c", BOOT, CHECKOUT, "reconcile", "ensure"],
                                          cwd=CHECKOUT, stdout=out, stderr=out)
    except OSError as exc:
        log(cfg, f"could not start `reconcile ensure` to replace it: {exc}")


# --- the loop from before the uv port -----------------------------------------

# The bash reconciler put this word in its supervisor's argv, so that nothing
# else would be mistaken for it. Its loop outlived the update that deleted its
# script, failing every pass, and it held no lock for `running` to see.
LEGACY_SCRIPT = os.path.join("scripts", "reconcile.sh")
LEGACY_SUPERVISOR = "__fleet-reconcile-supervisor"


def is_legacy(pid: int) -> str:
    """The argv of this checkout's legacy supervisor, if `pid` is one, else "".

    Proven, never guessed: its argv runs `scripts/reconcile.sh
    __fleet-reconcile-supervisor`, that script resolved from its working directory
    is this checkout's, and the working directory is this checkout, where the
    script put itself. Asked afresh every time, so a pid that has since become
    somebody else's is never signalled."""
    facts = fleet_platform.process_argv_cwd(pid)
    if not facts:
        return ""
    argv, cwd = facts
    home = os.path.realpath(CHECKOUT)
    if os.path.realpath(cwd) != home:
        return ""
    script = os.path.join(home, LEGACY_SCRIPT)
    for arg, following in zip(argv, argv[1:]):
        if following == LEGACY_SUPERVISOR and os.path.realpath(os.path.join(cwd, arg)) == script:
            return " ".join(argv)
    return ""


def legacy_loops() -> list[tuple[int, str]]:
    """Every legacy supervisor running from this checkout, as (pid, argv).

    Not its pidfile: that holds no lock, and its pid may be anybody's by now."""
    found = []
    for pid in fleet_platform.process_ids():
        argv = is_legacy(pid)
        if argv:
            found.append((pid, argv))
    return found


def stop_legacy(cfg: Config) -> bool:
    """End every legacy supervisor and the pass it is running, and say so. False if one survives."""
    stopped = True
    for pid, _ in legacy_loops():
        for force in (False, True):
            if not is_legacy(pid):
                break
            # Its process group: setsid made it the leader, and its pass is in it.
            fleet_platform.terminate_tree(pid, force)
            if wait_until(lambda pid=pid: not is_legacy(pid), KILL_WAIT_SECS):
                break
        if is_legacy(pid):
            err(f"fleet reconciler: could not stop the legacy bash reconciler (pid {pid})")
            stopped = False
            continue
        line = f"stopped a legacy bash reconciler (pid {pid}), left running from before the uv port"
        log(cfg, line)
        say(f"fleet reconciler: {line}")
    return stopped


# --- the loop -----------------------------------------------------------------


# What a child killed at its bound reads as: GNU timeout's exit code.
TIMED_OUT = 124


def bounded(cfg: Config, label: str, argv: list, secs: int, loop: bool = True, **popen):
    """One child, held to `secs`; None when it ran past them, logged as one line.

    `loop` is the loop's own child, whose pid sits in `child` for as long as it
    runs: it leads a process group of its own, which a `stop` that ends the
    supervisor's group would not reach. `status` and the control-plane guard
    run beside a live loop and must not touch that file."""
    track = cfg.path("child")
    if loop:
        # Beaten before EVERY child, not once a pass: a pass runs several, each
        # with its own bound, and two slow-but-honest ones would otherwise age
        # the beat past `stall` and have `status` fail a healthy loop. So a beat
        # older than the longest child allowance and cleanup is past its bound — a
        # loop that is genuinely stuck. Only REFRESHED, never started here: the
        # first beat is the pass's own, so a loop whose startup `root` cannot
        # run still never reads as up.
        if os.path.isfile(cfg.path("heartbeat")):
            fleet_platform.write_record(cfg.path("heartbeat"), f"{int(time.time())}\n")
    try:
        done = fleet_platform.run_bounded(
            argv, secs, env=child_env(), encoding="utf-8", errors="replace",
            on_start=(lambda pid: fleet_platform.write_record(track, f"{pid}\n")) if loop else None, **popen,
        )
    finally:
        if loop:
            remove(track)
    if loop and done is None:
        log(cfg, f"{label}: killed after {secs}s, its bound; the next pass runs it again")
    return done


def run_queue(cfg: Config, args: list, merge: bool = True, loop: bool = True) -> tuple[int, str]:
    secs = cfg.watch + WATCH_MARGIN_SECS if args[0] == "watch" else cfg.bound
    try:
        done = bounded(
            cfg, args[0], cfg.queue_cmd + args, secs, loop, cwd=CHECKOUT, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT if merge else subprocess.PIPE,
        )
    except OSError as exc:
        return 127, f"cannot run {cfg.queue_label}: {exc}"
    if done is None:
        return TIMED_OUT, ""
    # Unmerged, stdout is a reading the caller parses; stderr is said only on a failure.
    if done.returncode != 0 and not merge:
        return done.returncode, done.stdout + (done.stderr or "")
    return done.returncode, done.stdout


def last_words(text: str) -> str:
    """A failed child's last non-blank line: almost always the one that says why."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


def child_went(cfg: Config, label: str, why: str = "") -> None:
    """Remember how the loop's last `label` child went: `why` it failed, or "" for fine.

    THE LOG IS NOT WHERE ANYBODY LOOKS. On 2026-10-14 the notifier the loop
    runs had been renamed away under it, and every notice failed for eight
    hours — 515 lines in a megabyte of log — while `status` said "up". So each
    child's failure is kept here, as a count, since when, and its last words,
    until the same child next succeeds, and `status` prints it and exits 1.
    Written only when it changes, so a healthy pass writes nothing."""
    path = cfg.path(FAILURES_FILE)
    try:
        doc = json.loads(read(path) or "{}")
    except ValueError:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    if not why:
        if label not in doc:
            return
        doc.pop(label)
    else:
        was = doc.get(label) if isinstance(doc.get(label), dict) else {}
        doc[label] = {"count": int(was.get("count") or 0) + 1, "since": was.get("since") or now(),
                      "last": now(), "why": why}
    try:
        fleet_platform.write_record(path, json.dumps(doc, ensure_ascii=False) + "\n")
    except OSError:
        pass


def failures(cfg: Config) -> dict:
    """{label: {count, since, last, why}} for each child failing now."""
    try:
        doc = json.loads(read(cfg.path(FAILURES_FILE)) or "{}")
    except ValueError:
        return {}
    return {k: v for k, v in doc.items() if isinstance(v, dict)} if isinstance(doc, dict) else {}


def run_pass(cfg: Config, label: str, args: list, quiet: bool = False) -> int:
    """One queue command, logged only when it said something worth reading."""
    rc, out = run_queue(cfg, args)
    if rc == TIMED_OUT and not out:
        child_went(cfg, label, "killed at its bound")
        return rc  # `bounded` said so, in one line
    if rc != 0:
        log(cfg, f"{label}: exit {rc}")
        log_raw(cfg, indented(out, "    "))
        child_went(cfg, label, f"exit {rc}" + (f": {last_words(out)}" if last_words(out) else ""))
        return rc
    child_went(cfg, label)
    if quiet and QUIET.search(out):
        return 0
    log(cfg, f"{label}:")
    log_raw(cfg, indented(out, "    "))
    return 0


def run_notifier(cfg: Config, label: str, argv: list, stdin: str) -> None:
    """`notify_conductor.py`, held to the bound: each line it prints logged as its
    own `notify:` line, and a failure — not there, a crash, a bound — kept for
    `status`. Never a failure of the pass."""
    try:
        done = bounded(cfg, label, [sys.executable, NOTIFY, "--state-dir", cfg.rt, *argv], cfg.bound,
                       input=stdin, cwd=CHECKOUT, capture_output=True)
    except OSError as exc:
        log(cfg, f"{label}: could not run {NOTIFY}: {exc}")
        child_went(cfg, label, f"could not run: {exc}")
        return
    if done is None:
        child_went(cfg, label, "killed at its bound")
        return
    out = (done.stdout + done.stderr).strip()
    if done.returncode != 0:
        log(cfg, f"{label}: exit {done.returncode}")
        log_raw(cfg, indented(out, "    "))
        child_went(cfg, label, f"exit {done.returncode}" + (f": {last_words(out)}" if out else ""))
        return
    child_went(cfg, label)
    for line in out.splitlines():
        if line.strip():
            log(cfg, f"notify: {line.strip()}")


def notify_conductor(cfg: Config) -> None:
    """What this loop says out loud about the queue, and the one question it asks it.

    `plan` is a READ: it prints the ready set and moves nothing. It cannot fail
    the pass: a message is not worth the `collect` the loop just did. A `plan`
    that fails is logged and kept for `status`, as any child's failure is.
    """
    rc, plan = run_queue(cfg, ["plan", "--json"], merge=False)
    if rc != 0:
        log(cfg, f"plan: exit {rc}")
        log_raw(cfg, indented(plan, "    "))
        child_went(cfg, "plan", f"exit {rc}" + (f": {last_words(plan)}" if last_words(plan) else ""))
        return
    child_went(cfg, "plan")
    if plan.strip():
        run_notifier(cfg, "notify", [], plan)


def sync_checkout(cfg: Config, said: str) -> tuple[dict, str]:
    """Fast-forward the checkout, as the SessionStart hook does: `sync-checkout`
    itself, so every refusal it has — a dirty tree, a feature branch, a
    divergence — is this loop's too, and nothing here forces anything.

    A fresh child, never this process's copy: the sync is what brings new code
    in. Its message is logged only when it differs from the last one, so a
    standing refusal is one line and not one per pass. Returns the report and
    the message now standing."""
    try:
        done = bounded(cfg, "sync", [sys.executable, "-c", BOOT, CHECKOUT, "sync-checkout", "--json"], cfg.bound,
                       cwd=cfg.sync_dir, stdin=subprocess.DEVNULL, capture_output=True)
    except OSError as exc:
        child_went(cfg, "sync", f"could not run: {exc}")
        return {}, said
    if done is None:
        child_went(cfg, "sync", "killed at its bound")
        return {}, said
    try:
        report = json.loads(done.stdout)
    except ValueError:
        report = None
    if done.returncode != 0 or not isinstance(report, dict):
        words = last_words(done.stderr) or last_words(done.stdout)
        child_went(cfg, "sync", f"exit {done.returncode}, no report" + (f": {words}" if words else ""))
        return {}, said
    child_went(cfg, "sync")
    msg = report.get("message") or ""
    if msg and msg != said:
        log(cfg, "sync:")
        log_raw(cfg, indented(msg, "    "))
    return report, msg


def notify_stale(cfg: Config, report: dict) -> None:
    """Hand what a fast-forward needs done to the conductor, once: `notify_conductor.py
    --stale` owns who, when and how, and keeps it until it is delivered. An
    empty report is a retry of one still waiting."""
    run_notifier(cfg, "notify --stale", ["--stale"], json.dumps(report))


def trim_log(cfg: Config) -> None:
    """In place, not by replace: on Windows the supervisor's own stderr holds the log open."""
    try:
        if os.path.getsize(cfg.log) <= LOG_MAX_BYTES:
            return
        with open(cfg.log, "r+b") as fh:
            kept = b"".join(fh.read().splitlines(keepends=True)[-LOG_KEEP_LINES:])
            fh.seek(0)
            fh.write(kept)
            fh.truncate()
    except OSError:
        return
    log(cfg, f"log trimmed to the last {LOG_KEEP_LINES} lines")


def rest(cfg: Config, secs: float) -> None:
    """Sleep, but not through a stop, nor past what the loop runs for."""
    wait_until(lambda: over(cfg), max(secs, 0), step=0.2)


def _consume_nudge(cfg: Config) -> bool:
    """Take the advisory nudge flag, if one is waiting, and say whether it was."""
    # ADVISORY, and consumed here: it brings the periodic pass forward and
    # nothing else. If it never arrives the timers catch everything anyway.
    nudge = cfg.path("nudge")
    nudged = os.path.isfile(nudge)
    if nudged:
        log(cfg, "nudged: " + (read(nudge).splitlines() or [""])[0])
        remove(nudge)
    return nudged


def _note_forge(cfg: Config, forge, has_forge):
    """Ask whether a forge CLI is on PATH, log only a change, return the answer."""
    now_forge = bool(forge.available())
    if now_forge != has_forge:
        if not now_forge:
            log(cfg, f"shepherd: skipped while no forge is available — {forge.no_forge_reason()}")
        elif has_forge is not None:
            log(cfg, "shepherd: resumed — a forge CLI is on PATH now")
        has_forge = now_forge
    return has_forge


def _run_due(cfg: Config, last: dict, stamp: float, did_collect: bool, has_forge) -> bool:
    """Run each timed verb whose clock is up; True when a stop came in between."""
    for verb, every in (("collect", cfg.collect), ("shepherd", cfg.shepherd), ("refuel", cfg.refuel)):
        if verb == "shepherd" and not has_forge:
            continue
        if (verb == "collect" and did_collect) or (verb != "collect" and stamp - last[verb] >= every):
            run_pass(cfg, verb, [verb])
            last[verb] = stamp
            if over(cfg):
                return True
    return False


def _sync_and_tell(cfg: Config, last: dict, stamp: float, did_collect: bool, synced: str) -> str:
    """The checkout itself, on a clock of its own. What a fast-forward needs a
    hand for is told on it, and a notice still waiting is retried on collect's."""
    report = {}
    if cfg.sync > 0 and stamp - last["sync"] >= cfg.sync:
        report, synced = sync_checkout(cfg, synced)
        last["sync"] = stamp
    if report.get("actions") or (did_collect and os.path.isfile(cfg.path(STALE_FILE))):
        notify_stale(cfg, report if report.get("actions") else {})
    return synced


def tick(cfg: Config) -> int:
    """The loop. Read the cadences in the docstring before changing a number here."""
    # Before the first heartbeat, so a loop that cannot run the one command it
    # drives never reads as up.
    rc, out = run_queue(cfg, ["root"], merge=False)
    if rc != 0:
        log(cfg, f"cannot run {cfg.queue_label} — nothing to reconcile")
        child_went(cfg, "root", f"exit {rc}" + (f": {last_words(out)}" if last_words(out) else ""))
        return 2
    child_went(cfg, "root")

    # With no forge CLI on this machine a shepherd pass has nothing to ask.
    # Asked EVERY pass — `available()` is a PATH lookup, so a forge installed
    # while the loop runs (`fleet install --forge …`) is shepherded at the next
    # pass with no restart — and said only when the answer CHANGES, so a
    # local-only machine logs one line rather than one per pass.
    forge = _load_sibling("fleet_forge", "forge.py")
    has_forge = None

    # What it runs from now on, taken once per process: a tick restarted after
    # a crash is the same process, on the same modules. A new loop's failures
    # are its own, so a previous loop's are cleared with it.
    if not cfg.code:
        cfg.code = record_code(cfg)
        remove(cfg.path(FAILURES_FILE))
    elif cfg.served:
        # `serve` taking over: the record is the dead loop's, under its pid.
        record_code(cfg, cfg.code)
    replacing = False

    last = {"collect": float("-inf"), "shepherd": float("-inf"), "refuel": float("-inf"), "sync": float("-inf")}
    synced = ""
    while True:
        # Checked at the top of every pass, so a stop is honoured at the next boundary.
        if over(cfg):
            return 0
        stamp = time.monotonic()
        fleet_platform.write_record(cfg.path("heartbeat"), f"{int(time.time())}\n")

        # Its own code, asked every pass: a stat per module. Handed over, it
        # starts no child while `ensure` brings it down — one started now would
        # be cut off at the stop's grace — and goes on reconciling with what it
        # has only if nothing came for it.
        changed = [] if replacing else code_changed(cfg.code)
        if changed and cfg.served:
            return 0  # `serve` says why, and exits
        if changed:
            replace_me(cfg, changed)
            replacing = True
            rest(cfg, REPLACE_WAIT_SECS)
            continue

        nudged = _consume_nudge(cfg)

        # collect first: it is the one that CLOSES tasks, and shepherd's view of
        # which pull requests still matter is better for running after it.
        did_collect = nudged or stamp - last["collect"] >= cfg.collect
        has_forge = _note_forge(cfg, forge, has_forge)
        if _run_due(cfg, last, stamp, did_collect, has_forge):
            return 0

        # LAST, on collect's clock: it sees the landings collect just recorded.
        if did_collect:
            notify_conductor(cfg)

        synced = _sync_and_tell(cfg, last, stamp, did_collect, synced)

        trim_log(cfg)
        # A whole pass ran: a crash `supervise` recorded is over.
        child_went(cfg, "tick")

        # The continuous half, which also paces the loop. THE FLOOR IS NOT
        # OPTIONAL: `watch` returns at once, exit 0, when no task has a session
        # yet, and without the sleep the loop would spin at the speed of process
        # creation for as long as the queue is empty.
        began = time.monotonic()
        run_pass(cfg, "watch", ["watch", "--for-secs", str(cfg.watch)], quiet=True)
        rest(cfg, cfg.watch - (time.monotonic() - began))


def with_lock(cfg: Config, body) -> int:
    """Run `body` holding the loop's lock for its whole life, or exit if a twin holds it."""
    os.makedirs(cfg.rt, exist_ok=True)
    deadline = time.monotonic() + LOCK_RETRY_SECS
    while True:
        try:
            with fleet_platform.exclusive_lock(cfg.path("lock")):
                return body(cfg)
        except fleet_platform.LockHeld:
            if time.monotonic() >= deadline:
                log(cfg, "another reconciler holds the lock; this one exits")
                return 0
            time.sleep(0.1)


def supervise(cfg: Config) -> int:
    """Restart the loop when it fails, and only then. The down flag is the exit
    condition, so an intentional stop is never fought by a restart."""
    fleet_platform.write_record(cfg.path("pid"), f"{os.getpid()}\n")
    backoff = 1
    try:
        while True:
            try:
                rc = tick(cfg)
            except Exception:
                log(cfg, "tick loop raised:")
                log_raw(cfg, indented(traceback.format_exc(), "    "))
                # Kept for `status`: restarted, the pass beats again before it
                # crashes again, so the heartbeat alone reads it as up.
                child_went(cfg, "tick", "raised: " + last_words(traceback.format_exc()))
                rc = 1
            why = orphaned(cfg)
            if why:
                log(cfg, f"{why}; supervisor exiting")
                return 0
            if asked_down(cfg):
                log(cfg, "asked down; supervisor exiting")
                return 0
            if rc == 0:
                log(cfg, "tick loop exited cleanly; supervisor exiting")
                return 0
            log(cfg, f"tick loop exited {rc}; restarting in {backoff}s")
            rest(cfg, backoff)
            # Back off to a minute, so a loop that cannot run is not a busy loop.
            if backoff < 60:
                backoff *= 2
    finally:
        remove(cfg.path("pid"))


# --- start / stop -------------------------------------------------------------


def terminate(cfg: Config) -> bool:
    """End the lock holder and the queue command it is running, gently then by force.

    The command too: a refuel or shepherd left running after its loop is gone
    keeps acting, and the next loop's first pass would do the same work beside it."""
    if not lock_held(cfg):
        return True
    wait_until(lambda: pid_of(cfg) > 0 or not lock_held(cfg), LOCK_RETRY_SECS)
    pid = pid_of(cfg)
    for force in (False, True):
        # The command leads a group of its own (`bounded`), so it is ended by name.
        for each in (pid, pid_of(cfg, "child")):
            if each and fleet_platform.alive(each):
                fleet_platform.terminate_tree(each, force)
        # Windows releases a killed process's lock when it gets to it.
        if wait_until(lambda: not lock_held(cfg), KILL_WAIT_SECS):
            return True
    return False


def bring_down(cfg: Config) -> bool:
    """With the flag already written: let the loop see it, then terminate it if it did not."""
    if not lock_held(cfg):
        return True
    grace = cfg.watch + STOP_GRACE_SECS
    say(f"fleet reconciler: asked down; letting the pass in flight finish (up to {grace}s)")
    if wait_until(lambda: not lock_held(cfg), grace):
        return True
    return terminate(cfg)


def launch(cfg: Config) -> bool:
    os.makedirs(cfg.rt, exist_ok=True)
    if lock_held(cfg):
        # A loop another `ensure` just started beats within moments: adopt it.
        if wait_until(lambda: running(cfg), START_WAIT_SECS):
            return True
        # A supervisor holding the lock without ever beating is a phantom: end
        # it rather than leave it retrying behind the new one.
        terminate(cfg)
    remove(cfg.path("heartbeat"))
    remove(cfg.path("pid"))
    remove(cfg.path("child"))
    log(cfg, "starting")
    with open(cfg.log, "ab") as errors:
        fleet_platform.spawn_detached(
            [sys.executable, "-c", BOOT, CHECKOUT, "reconcile", "__supervise"],
            cwd=CHECKOUT, stdout=subprocess.DEVNULL, stderr=errors,
        )
    if wait_until(lambda: running(cfg) and pid_of(cfg) > 0, START_WAIT_SECS):
        return True
    err(f"fleet reconciler: did not tick within {START_WAIT_SECS}s. Last log lines:")
    sys.stderr.write("".join(read(cfg.log).splitlines(keepends=True)[-15:]))
    # Still out there retrying: kill it rather than leave it behind a stale pid.
    terminate(cfg)
    remove(cfg.path("pid"))
    return False


def guard_control_plane(cfg: Config) -> bool:
    """Refuse to run in a checkout that is provably not the control plane.

    `collect` closes tasks and reaps sessions, and a worker running this loop
    would reap its own session. Silent when nothing can tell; only a positive
    identification of a DIFFERENT checkout is loud, and then it is fatal.
    """
    rc, out = run_queue(cfg, ["root", "--foreign"], merge=False, loop=False)
    owner = out.strip()
    if rc != 0 or not owner:
        return True
    err("fleet reconciler: refusing to run outside the control plane.")
    err(f"    this checkout: {CHECKOUT}")
    err(f"    control plane: {owner}")
    err("  This loop runs `collect`, which closes tasks and reaps sessions.")
    err("  A worker running it would reap its own session. Start it there:")
    err(f"      uv run --project {owner} fleet reconcile start")
    return False


# --- commands -----------------------------------------------------------------


def replace(cfg: Config, why: str) -> bool:
    """Bring the running loop down at its next boundary, leaving no flag of its own behind.

    Only its OWN flag: a `stop` that lands while it waits writes the operator's,
    which is theirs to clear, and `come_up` then launches nothing."""
    os.makedirs(cfg.rt, exist_ok=True)
    flag = f"{why} {now()} by {who()}\n"
    fleet_platform.write_record(cfg.path("down"), flag)
    stopped = bring_down(cfg)
    if read(cfg.path("down")) == flag:
        remove(cfg.path("down"))
    return stopped


def come_up(cfg: Config) -> int:
    # A legacy loop holds no lock, so neither adoption nor launch can see the twin.
    if not stop_legacy(cfg):
        return 1
    if running(cfg):
        # ADOPTED ONLY ON THE CODE IT WOULD START WITH. A loop on older code is
        # the one this checkout no longer describes, and SessionStart `ensure`
        # used to adopt it silently while every notice it sent failed.
        changed, started = stale_code(cfg)
        crash = failures(cfg).get("tick")
        if not changed and crash:
            # A fresh process on the same code would crash the same way: say
            # so, rather than calling it adopted and healthy.
            say(f"fleet reconciler: ticking (pid {pid_of(cfg)}), but its pass crashes: {crash.get('why')}")
            say(f"    uv run fleet reconcile status says more; the traceback is in {cfg.log}")
            return 1
        if not changed:
            say(f"fleet reconciler: already ticking (pid {pid_of(cfg)}) — adopted, not restarted")
            return 0
        say(f"fleet reconciler: ticking (pid {pid_of(cfg)}) on code older than the checkout — "
            f"{shown(changed)} changed since it started at {started}; replacing it")
        if not guard_control_plane(cfg):
            return 1
        if not replace(cfg, "replacing a loop on old code"):
            err(f"fleet reconciler: could not stop pid {pid_of(cfg) or '?'}")
            return 1
    elif not guard_control_plane(cfg):
        return 1
    # Asked again: a `stop` may have landed while a replacement brought the
    # old loop down.
    if asked_down(cfg):
        say("fleet reconciler: asked down meanwhile, and staying down")
        return 0
    if not launch(cfg):
        return 1
    say(f"fleet reconciler: ticking (pid {pid_of(cfg)})")
    return 0


def cmd_ensure(cfg: Config) -> int:
    if asked_down(cfg):
        gone = stop_legacy(cfg)
        say("fleet reconciler: down, and staying down — you asked for it:")
        sys.stdout.write(indented(read(cfg.path("down")), "    "))
        say("    bring it back with uv run fleet reconcile start")
        return 0 if gone else 1
    return come_up(cfg)


def is_lead() -> bool:
    """Whether this process runs in the session this checkout's extension names
    as its lead, or as its conductor.

    notify_conductor.py's readings, so "who is the lead" has one answer: the
    name from the rendered manifest, and the id of the row `lead_row` picks for
    it — this checkout's, on this machine, not the first a remote mirror lists.
    The conductor counts for the reason the lead does: it is a session this
    checkout's extension keeps alive, so it is up whenever the loop should be.
    """
    # THURBOX_SESSION is thurbox's id for it; THURBOX_SESSION_ID is the agent's
    # conversation, which a fresh lead changes and a restart keeps — the session
    # id is the one that says which session this is.
    sid = os.environ.get("THURBOX_SESSION") or ""
    if not sid:
        return False
    notify = _load_sibling("fleet_notify_conductor", "notify_conductor.py")
    name = notify.lead_name()
    if name and notify.lead_session(name)[0] == sid:
        return True
    _own, row, _why = notify.own_conductor_row()
    return bool(row) and str(row.get("id")) == sid


def cmd_ensure_if_lead() -> int:
    """`ensure`, from the lead's or conductor's SessionStart hook: silent elsewhere, never a failure."""
    try:
        if is_lead():
            cmd_ensure(Config.from_env())
    except Exception as exc:
        say(f"fleet reconciler: could not ensure it is up ({exc}); uv run fleet reconcile ensure")
    return 0


def cmd_serve(cfg: Config) -> int:
    """The loop in the foreground, for a service manager: waits, never fights.

    It exits when the control plane guard refuses, when its own code changed
    on disk — its manager restarts it on the new bytes — or, tests only, when
    FLEET_RECONCILE_PARENT_PID is gone; everything else is a wait, because an
    exit is what its manager restarts."""
    # What it imported at boot, for the takeover before its first pass, which
    # has recorded nothing yet. Before the guard: that runs a whole `fleet
    # queue`, and an edit landing during it was read as the code serve runs.
    boot = loaded_code()
    if not guard_control_plane(cfg):
        return 1
    cfg.served = True
    while not (cfg.parent and not fleet_platform.alive(cfg.parent)):
        changed = code_changed(cfg.code or boot)
        if changed:
            log(cfg, f"its own code changed on disk ({shown(changed)}); serve exits for its manager to restart")
            return 0
        if not asked_down(cfg) and not lock_held(cfg) and stop_legacy(cfg):
            with_lock(cfg, supervise)
        time.sleep(SERVE_POLL_SECS)
    return 0


def cmd_start(cfg: Config) -> int:
    if asked_down(cfg):
        remove(cfg.path("down"))
        say("fleet reconciler: clearing the down flag")
    return come_up(cfg)


def cmd_stop(cfg: Config) -> int:
    # The flag FIRST: it is what the loop polls to exit, and what keeps the stop
    # durable if the loop cannot be ended.
    os.makedirs(cfg.rt, exist_ok=True)
    down = cfg.path("down")
    fleet_platform.write_record(down, f"stopped {now()} by {who()}\n")
    # After the flag, so a legacy loop that will not die cannot keep this one up.
    legacy_gone = stop_legacy(cfg)
    if bring_down(cfg) and legacy_gone:
        say(f"fleet reconciler: down, durably — {down} keeps it down across a")
        say("    restart, a reboot and the next onboarding run.")
        say("    bring it back with uv run fleet reconcile start")
        return 0
    err(f"fleet reconciler: could not stop pid {pid_of(cfg) or '?'}")
    err("    the down flag is written, so nothing will restart it.")
    return 1


def cmd_restart(cfg: Config) -> int:
    if not replace(cfg, "restarting"):
        err(f"fleet reconciler: could not stop pid {pid_of(cfg) or '?'}")
        return 1
    return come_up(cfg)


def _say_state(cfg: Config) -> bool:
    """`cmd_status`'s first lines: up, STALLED, down or DOWN. True when STALLED."""
    stalled = False
    if running(cfg):
        age = beat_age(cfg)
        stalled = age is not None and age > cfg.stall
        if stalled:
            say(f"STALLED   ticking, but the last pass was {age}s ago (over {cfg.stall}s)")
            say(f"          look at {cfg.log} before you restart it")
        else:
            say(f"up        reconciling, last pass {'?' if age is None else age}s ago")
        say(f"pid       {pid_of(cfg)}")
    elif asked_down(cfg):
        say("down      asked down, and it will stay down:")
        sys.stdout.write(indented(read(cfg.path("down")), "          "))
        say("          uv run fleet reconcile start brings it back")
    else:
        say("DOWN      not running, and nothing asked it down: nothing is collecting,")
        say("          shepherding or telling the lead what is ready.")
        say("          uv run fleet reconcile ensure starts it")
        service = _load_sibling("fleet_reconcile_service", "reconcile_service.py").installed(CHECKOUT)
        if service:
            say(f"          its service ({service}) did not keep it up: read its log first")
        else:
            say("          and nothing brings it back after a reboot but the lead's next session:")
            say("          uv run fleet install installs a user service where this machine has one")
    return stalled


def cmd_status(cfg: Config) -> int:
    """Exit 1 when it is down and nothing asked it down, or when it is STALLED:
    each is a fault, not a reading, and an exit code is all a script reads."""
    # Asked of the queue itself, so this line and `fleet queue list` cannot disagree.
    rc, out = run_queue(cfg, ["root"], merge=False, loop=False)
    queue = out.strip() if rc == 0 and out.strip() else os.environ.get("FLEET_QUEUE_DIR", "orchestration/queue")
    for pid, argv in legacy_loops():
        say(f"legacy    a bash reconciler from before the uv port is still running (pid {pid}):")
        say(f"          {argv}")
        say("          its script is gone, so every pass fails; uv run fleet reconcile ensure stops it")
    stalled = _say_state(cfg)
    faults = stalled
    if running(cfg):
        changed, started = stale_code(cfg)
        if changed:
            faults = True
            say(f"stale     running code older than the checkout: {shown(changed)} changed since it")
            say(f"          started at {started}; uv run fleet reconcile restart replaces it, as ensure does")
        for label, failed in sorted(failures(cfg).items()):
            faults = True
            say(f"failing   {label}: {failed.get('count')} time(s) since {failed.get('since')}, "
                f"last {failed.get('last')}")
            say(f"          {failed.get('why')}")
    say(f"queue     {queue}")
    say(f"watch     every {cfg.watch}s, back to back — the continuous fold")
    say(f"collect   every {cfg.collect}s")
    say(f"refuel    every {cfg.refuel}s")
    say(f"shepherd  every {cfg.shepherd}s")
    say(f"sync      every {cfg.sync}s — fast-forwards a clean checkout" if cfg.sync > 0 else "sync      off")
    say(f"log       {cfg.log}")
    nudge = cfg.path("nudge")
    if os.path.isfile(nudge):
        say("nudge     one is waiting: " + (read(nudge).splitlines() or [""])[0])
    if faults:
        return 1
    return 0 if running(cfg) or asked_down(cfg) else 1


def cmd_nudge(cfg: Config) -> int:
    """The accelerator, and the ONLY verb safe from a worker: one flag file, no
    queue command, so a worker calling it cannot collect or reap anything.
    (`stop_decision`, run after it, only READS the queue.)
    A nudge with the loop down waits in the file for the next start.

    It reports on stderr: a hook's stdout is the agent's protocol channel, and
    Codex fails a Stop hook whose stdout is not JSON."""
    by = os.environ.get("THURBOX_SESSION") or who()
    try:
        os.makedirs(cfg.rt, exist_ok=True)
        fleet_platform.write_record(cfg.path("nudge"), f"nudged {now()} by {by}\n")
    except OSError as exc:
        err(f"fleet reconciler: could not leave a nudge ({exc}); the loop's own timers still run")
        return 0
    if running(cfg):
        err(f"fleet reconciler: nudged — the next pass runs within {cfg.watch}s")
    else:
        err("fleet reconciler: nudged, but nothing is ticking; the nudge waits")
    return 0


def stop_decision() -> dict | None:
    """The Stop-hook answer that asks a worker for its missing result.md, or None.

    Only a thurbox session can be a task's worker, and only a hook pipes its
    input: a nudge typed at a terminal reads nothing and decides nothing. Claude
    Code's protocol is `{"decision": "block", "reason": ...}` on stdout, and it
    sets `stop_hook_active` on the stop that follows one: that stop is always
    allowed, so a worker is asked ONCE and can never be held. `queue.py`'s
    `missing_result` owns which stops are asked about at all; any error is a
    stop allowed, because a hook that fails a stop is worse than one that never
    fired, and the loop's own timer still reads the queue."""
    sid = os.environ.get("THURBOX_SESSION", "").strip()
    if not sid or sys.stdin is None or sys.stdin.isatty():
        return None
    try:
        hook = json.loads(sys.stdin.read() or "null")
        if (not isinstance(hook, dict) or hook.get("hook_event_name") != "Stop"
                or hook.get("stop_hook_active")):
            return None
        reason = _load_sibling("fleet_queue", "queue.py").missing_result(sid)
    except Exception:
        return None
    return {"decision": "block", "reason": reason} if reason else None


HOOK_BARE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/._:@+=,~-")


def hook_command(checkout: str = CHECKOUT) -> str:
    """One command every shell parses the same: forward slashes, which bash does
    not eat and Windows accepts; the path bare when every character is plain,
    in double quotes when it holds a space, an apostrophe or the like, and in a
    POSIX shell's single quotes only when a double-quoted shell would expand it."""
    project = _shell_neutral(checkout.replace("\\", "/"))
    return f"uv run --project {project} fleet reconcile nudge"


def _shell_neutral(project: str) -> str:
    """`hook_command`'s quoting of the checkout path: the least that is still one word."""
    if set(project) <= HOOK_BARE:
        return project
    if not any(c in project for c in '"$`!'):
        return f'"{project}"'
    return shlex.quote(project)


def cmd_hook(cfg: Config) -> int:
    """The worker Stop hook that nudges the loop, as `fleet install` merges it.

    It lives in Claude Code's user settings, which Claude Code merges with the
    settings thurbox hands each worker. Not in thurbox's own hooks file: thurbox
    rewrites that from its embedded payload on every start and heartbeat tick."""
    settings = fleet_platform.claude_settings_file()
    block = json.dumps({"type": "command", "command": hook_command(), "timeout": 10}, indent=2)
    say(f'`uv run fleet install` adds this to the "Stop" hooks in {settings}.')
    say("To add it by hand, put it there as well: thurbox rewrites its own hooks")
    say("file on every start. It tells the reconciler to run its periodic pass now")
    say(f"instead of waiting out {cfg.collect}s. It closes nothing, it reaps")
    say("nothing, and if it never fires the loop's own timers still catch")
    say("everything it would have caught.")
    say("")
    sys.stdout.write(indented(block, "          "))
    say("")
    say("It is one command with nothing a shell reads differently, so bash, cmd")
    say("and PowerShell run it alike. `nudge` exits 0 whatever happens. It blocks")
    say("one stop and no other: a dispatched task's worker stopping with commits")
    say("ahead of its base and no result.md is told, once, to write it.")
    return 0


def cmd_logs(cfg: Config, follow: bool) -> int:
    if not os.path.isfile(cfg.log):
        err(f"fleet reconciler: no log at {cfg.log} yet")
        return 1
    sys.stdout.write("".join(read(cfg.log).splitlines(keepends=True)[-50:]))
    sys.stdout.flush()
    if not follow:
        return 0
    pos = os.path.getsize(cfg.log)
    try:
        while True:
            time.sleep(0.5)
            size = os.path.getsize(cfg.log)
            if size < pos:  # trimmed in place
                pos = 0
            if size > pos:
                with open(cfg.log, "rb") as fh:
                    fh.seek(pos)
                    sys.stdout.write(fh.read().decode("utf-8", errors="replace"))
                    sys.stdout.flush()
                pos = size
    except KeyboardInterrupt:
        return 0


# --- entry point --------------------------------------------------------------


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd in ("", "-h", "--help"):
        sys.stdout.write(__doc__)
        return 0
    if cmd == "nudge":
        # Whatever goes wrong, a hook must not fail: not even a bad setting.
        # The stop decision needs no Config, so a bad setting costs the nudge
        # and nothing else.
        # Its stdout is the hook's protocol channel: the one block decision, or nothing.
        try:
            cmd_nudge(Config.from_env())
        except Exception as exc:
            err(f"fleet reconciler: could not nudge ({exc})")
        decision = stop_decision()
        if decision:
            sys.stdout.write(json.dumps(decision) + "\n")
        return 0
    if cmd == "ensure" and "--if-lead" in argv[1:]:
        # Before the settings are read: a SessionStart hook never fails, not even on a bad one.
        return cmd_ensure_if_lead()
    try:
        cfg = Config.from_env()
    except BadSetting as exc:
        err(str(exc))
        return 1
    if cmd == "__supervise":
        return with_lock(cfg, supervise)
    if cmd == "__loop":
        return with_lock(cfg, tick)
    handlers = {
        "ensure": cmd_ensure, "start": cmd_start, "stop": cmd_stop, "restart": cmd_restart,
        "status": cmd_status, "hook": cmd_hook, "serve": cmd_serve,
    }
    if cmd in handlers:
        return handlers[cmd](cfg)
    if cmd == "logs":
        return cmd_logs(cfg, argv[1:2] == ["-f"])
    err(f"error: unknown command {cmd!r} (want: {COMMANDS})")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
