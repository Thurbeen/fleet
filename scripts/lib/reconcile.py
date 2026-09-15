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
            loop; never a second one over one queue.
  `stop`    writes a DOWN FLAG to disk first, then waits for the loop to see it.
            The flag is what makes "down" survive a restart, a reboot and the
            next skill run.
  `start`   the operator asking for it back. It CLEARS the flag. That is the
            whole difference between the two.

IS IT UP. The loop holds an exclusive lock on `lock` in its runtime directory for
its whole life. The OS drops a lock when its holder dies however it died, so a
dead loop holds no lock and no stale pid is ever trusted or signalled. Up means
the lock is held AND the loop has written a heartbeat: a supervisor whose loop
cannot run at all holds the lock and never beats, and is never adopted or called
healthy. The pidfile and heartbeat stay, for people.

A LOOP FROM BEFORE THE UV PORT holds no lock: the bash `scripts/reconcile.sh`
supervisor wrote a pidfile only, and outlived the update that deleted its
script, failing every pass. `status` names one still running from this
checkout, and `ensure`, `start`, `stop` and `restart` end it first. Proven by
its argv and working directory each time before it is signalled
(`is_legacy`), never by a pid.

IT GOES WITH WHAT IT RUNS FOR. A loop whose runtime directory is deleted, or
whose FLEET_RECONCILE_PARENT_PID is gone, exits at the next boundary. Only
tests set the second: a run killed before its teardown would otherwise leak a
detached loop ticking against a deleted temp directory.

IT WRITES NO RECORD. Every effect on the queue goes through `fleet queue`, the
only writer over the records. Its own runtime directory is not an exception: a
pid, a heartbeat, a log, the flags and `notified.json` are facts about this loop
on this machine, not about any task. It calls exactly `watch`, `collect`,
`shepherd`, `refuel` and the read-only `plan` (plus `root`, to check it can).

IT DOES NOT DECIDE WHAT RUNS. No dispatch, no cancel, no reorder, and it does not
second-guess `refuel`'s rule about a spent quota window.

BUT IT SAYS WHEN THERE IS SOMETHING TO DECIDE. A task whose blocker clears is
READY and has no actor: this loop may not dispatch, and the lead only acts when
spoken to. So the pass after `collect` reads `plan` and, when the ready set has
grown, wakes the lead: once per transition, never mid-turn, silently when there
is no lead. `scripts/lib/notify_lead.py` owns those rules.

Usage:
  uv run fleet reconcile ensure     # start unless running or asked down
  uv run fleet reconcile start      # start, and clear a previous `stop`
  uv run fleet reconcile stop       # durably down: writes the flag, then stops
  uv run fleet reconcile restart    # stop and start, clearing the flag
  uv run fleet reconcile status     # ticking? since when? on what queue?
  uv run fleet reconcile nudge      # advisory: run the periodic pass NOW
  uv run fleet reconcile hook       # print the worker Stop hook that nudges
  uv run fleet reconcile logs [-f]  # the loop's log

Runtime state lives in orchestration/reconcile/ and is GITIGNORED.

THE CADENCES, and why each number is the number:

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
            checks, reviews and mergeability. CI does not change faster.
  notify    collect's clock. What makes a task ready is a landing `collect` has
            just recorded, so a clock of its own would ask at a worse moment.

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
  FLEET_RECONCILE_PARENT_PID    exit once this pid is gone (default: unset; tests set it)
  FLEET_QUEUE_DIR               the queue to reconcile (default: this checkout's)
  FLEET_LEAD_SESSION            the lead session to wake; read by notify_lead.py

Requires: uv and whatever the pass needs: thurbox-cli for `watch`, `refuel` and
the notification, `gh` for `collect` and `shepherd`, `quota-axi` for fuel. Each
degrades to "could not check" inside the queue, so a missing tool costs its own
pass and never the loop.
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
from dataclasses import dataclass


def _load_sibling(name: str, filename: str):
    """A scripts/lib module under a `fleet_` key, never shadowing the standard library."""
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fleet_platform = _load_sibling("fleet_platform", "fleet_platform.py")

CHECKOUT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NOTIFY = os.path.join(CHECKOUT, "scripts", "lib", "notify_lead.py")

# How fleet runs itself as a child: this interpreter, `fleet.cli.main`, off this
# checkout. No `uv` round trip per pass, and no shell on any OS.
BOOT = "import sys\nsys.path.insert(0, sys.argv[1])\nfrom fleet.cli import main\nsys.exit(main(sys.argv[2:]))"

COMMANDS = "ensure start stop restart status nudge hook logs"

# How long `start` waits for the first heartbeat. The beat is written before the
# first pass, so this waits on the loop being alive, not on a forge round trip.
START_WAIT_SECS = 30

# How long `stop` lets the loop notice the flag before it terminates the pid, on
# top of one watch call. The loop never kills a queue command in flight: one cut
# off mid-write leaves a torn record, and on Windows it would orphan the
# thurbox-cli stream beneath it. So a stop lands at the next pass boundary, and
# only a loop still holding the lock past this is terminated.
STOP_GRACE_SECS = 30
KILL_WAIT_SECS = 10

# A supervisor starting at the instant a `status` probes the lock sees it held
# for that instant; it retries this long before concluding a twin owns it.
LOCK_RETRY_SECS = 2

# A log a person can open: a pass every couple of minutes, forever, trims itself.
LOG_MAX_BYTES = 4 * 1024 * 1024
LOG_KEEP_LINES = 2000

# `watch` reports "0 task(s) moved" nearly every call. Anchored, because a moved
# count of 10 or 100 contains the same substring.
QUIET = re.compile(r"(^|[^0-9])0 task\(s\) moved", re.MULTILINE)


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

    @classmethod
    def from_env(cls) -> Config:
        rt = os.path.join(CHECKOUT, os.environ.get("FLEET_RECONCILE_DIR") or os.path.join("orchestration", "reconcile"))
        override = os.environ.get("FLEET_RECONCILE_QUEUE_CMD", "").strip()
        if not override:
            cmd, label = [sys.executable, "-c", BOOT, CHECKOUT, "queue"], "fleet queue"
        else:
            try:
                cmd = ([str(a) for a in json.loads(override)] if override.startswith("[")
                       else fleet_platform.split_command(override))
            except ValueError as exc:
                raise SystemExit(f"fleet reconciler: FLEET_RECONCILE_QUEUE_CMD: {exc}") from exc
            label = " ".join(cmd)

        def secs(name: str, default: int) -> int:
            return int(os.environ.get(f"FLEET_RECONCILE_{name}_SECS") or default)

        return cls(rt, cmd, label, secs("WATCH", 20), secs("COLLECT", 120), secs("REFUEL", 300), secs("SHEPHERD", 900),
                   int(os.environ.get("FLEET_RECONCILE_PARENT_PID") or 0))

    def path(self, name: str) -> str:
        return os.path.join(self.rt, name)

    @property
    def log(self) -> str:
        return self.path("reconcile.log")

    @property
    def stall(self) -> int:
        # A NOTE on status, never an adoption test: one slow shepherd sweep is not
        # a wedged loop, and adoption turning on freshness would start a twin.
        return self.watch + 600


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


def pid_of(cfg: Config) -> int:
    try:
        return int(read(cfg.path("pid")).strip())
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
    a deleted temp directory forever."""
    if not os.path.isdir(cfg.rt):
        return f"{cfg.rt} is gone"
    if cfg.parent and not fleet_platform.alive(cfg.parent):
        return f"parent pid {cfg.parent} is gone"
    return ""


def over(cfg: Config) -> bool:
    return asked_down(cfg) or bool(orphaned(cfg))


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


def run_queue(cfg: Config, args: list, merge: bool = True) -> tuple[int, str]:
    try:
        done = subprocess.run(
            cfg.queue_cmd + args, cwd=CHECKOUT, env=child_env(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT if merge else subprocess.DEVNULL,
            encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        return 127, f"cannot run {cfg.queue_label}: {exc}"
    return done.returncode, done.stdout


def run_pass(cfg: Config, label: str, args: list, quiet: bool = False) -> int:
    """One queue command, logged only when it said something worth reading."""
    rc, out = run_queue(cfg, args)
    if rc != 0:
        log(cfg, f"{label}: exit {rc}")
        log_raw(cfg, indented(out, "    "))
        return rc
    if quiet and QUIET.search(out):
        return 0
    log(cfg, f"{label}:")
    log_raw(cfg, indented(out, "    "))
    return 0


def notify_lead(cfg: Config) -> None:
    """The one thing this loop says out loud, and the one question it asks the queue.

    `plan` is a READ: it prints the ready set and moves nothing. It cannot fail
    the pass: a message is not worth the `collect` the loop just did.
    """
    rc, plan = run_queue(cfg, ["plan", "--json"], merge=False)
    if rc != 0 or not plan.strip():
        return
    try:
        done = subprocess.run(
            [sys.executable, NOTIFY, "--state-dir", cfg.rt], input=plan, cwd=CHECKOUT, env=child_env(),
            capture_output=True, encoding="utf-8", errors="replace",
        )
    except OSError:
        return
    out = (done.stdout + done.stderr).strip()
    if out:
        log(cfg, f"notify: {out}")


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


def tick(cfg: Config) -> int:
    """The loop. Read the cadences in the docstring before changing a number here."""
    # Before the first heartbeat, so a loop that cannot run the one command it
    # drives never reads as up.
    rc, _ = run_queue(cfg, ["root"], merge=False)
    if rc != 0:
        log(cfg, f"cannot run {cfg.queue_label} — nothing to reconcile")
        return 2

    last = {"collect": float("-inf"), "shepherd": float("-inf"), "refuel": float("-inf")}
    while True:
        # Checked at the top of every pass, so a stop is honoured at the next boundary.
        if over(cfg):
            return 0
        stamp = time.monotonic()
        fleet_platform.write_record(cfg.path("heartbeat"), f"{int(time.time())}\n")

        # ADVISORY, and consumed here: it brings the periodic pass forward and
        # nothing else. If it never arrives the timers catch everything anyway.
        nudge = cfg.path("nudge")
        nudged = os.path.isfile(nudge)
        if nudged:
            log(cfg, "nudged: " + (read(nudge).splitlines() or [""])[0])
            remove(nudge)

        # collect first: it is the one that CLOSES tasks, and shepherd's view of
        # which pull requests still matter is better for running after it.
        did_collect = nudged or stamp - last["collect"] >= cfg.collect
        for verb, every in (("collect", cfg.collect), ("shepherd", cfg.shepherd), ("refuel", cfg.refuel)):
            if (verb == "collect" and did_collect) or (verb != "collect" and stamp - last[verb] >= every):
                run_pass(cfg, verb, [verb])
                last[verb] = stamp
                if over(cfg):
                    return 0

        # LAST, on collect's clock: it sees the landings collect just recorded.
        if did_collect:
            notify_lead(cfg)

        trim_log(cfg)

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
        if pid and fleet_platform.alive(pid):
            fleet_platform.terminate_tree(pid, force)
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
    rc, out = run_queue(cfg, ["root", "--foreign"], merge=False)
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


def come_up(cfg: Config) -> int:
    if running(cfg):
        say(f"fleet reconciler: already ticking (pid {pid_of(cfg)}) — adopted, not restarted")
        return 0
    if not guard_control_plane(cfg) or not launch(cfg):
        return 1
    say(f"fleet reconciler: ticking (pid {pid_of(cfg)})")
    return 0


def cmd_ensure(cfg: Config) -> int:
    if asked_down(cfg):
        say("fleet reconciler: down, and staying down — you asked for it:")
        sys.stdout.write(indented(read(cfg.path("down")), "    "))
        say("    bring it back with uv run fleet reconcile start")
        return 0
    return come_up(cfg)


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
    if bring_down(cfg):
        say(f"fleet reconciler: down, durably — {down} keeps it down across a")
        say("    restart, a reboot and the next onboarding run.")
        say("    bring it back with uv run fleet reconcile start")
        return 0
    err(f"fleet reconciler: could not stop pid {pid_of(cfg) or '?'}")
    err("    the down flag is written, so nothing will restart it.")
    return 1


def cmd_restart(cfg: Config) -> int:
    os.makedirs(cfg.rt, exist_ok=True)
    fleet_platform.write_record(cfg.path("down"), f"restarting {now()} by {who()}\n")
    stopped = bring_down(cfg)
    remove(cfg.path("down"))
    if not stopped:
        err(f"fleet reconciler: could not stop pid {pid_of(cfg) or '?'}")
        return 1
    return come_up(cfg)


def cmd_status(cfg: Config) -> int:
    # Asked of the queue itself, so this line and `fleet queue list` cannot disagree.
    rc, out = run_queue(cfg, ["root"], merge=False)
    queue = out.strip() if rc == 0 and out.strip() else os.environ.get("FLEET_QUEUE_DIR", "orchestration/queue")
    for pid, argv in legacy_loops():
        say(f"legacy    a bash reconciler from before the uv port is still running (pid {pid}):")
        say(f"          {argv}")
        say("          its script is gone, so every pass fails; uv run fleet reconcile ensure stops it")
    if running(cfg):
        age = beat_age(cfg)
        if age is not None and age > cfg.stall:
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
        say("down      not running, and no down flag — uv run fleet reconcile ensure starts it")
    say(f"queue     {queue}")
    say(f"watch     every {cfg.watch}s, back to back — the continuous fold")
    say(f"collect   every {cfg.collect}s")
    say(f"refuel    every {cfg.refuel}s")
    say(f"shepherd  every {cfg.shepherd}s")
    say(f"log       {cfg.log}")
    nudge = cfg.path("nudge")
    if os.path.isfile(nudge):
        say("nudge     one is waiting: " + (read(nudge).splitlines() or [""])[0])
    return 0


def cmd_nudge(cfg: Config) -> int:
    """The accelerator, and the ONLY verb safe from a worker: one flag file, no
    queue command, so a worker calling it cannot collect or reap anything.
    A nudge with the loop down waits in the file for the next start."""
    by = os.environ.get("THURBOX_SESSION") or who()
    try:
        os.makedirs(cfg.rt, exist_ok=True)
        fleet_platform.write_record(cfg.path("nudge"), f"nudged {now()} by {by}\n")
    except OSError as exc:
        say(f"fleet reconciler: could not leave a nudge ({exc}); the loop's own timers still run")
        return 0
    if running(cfg):
        say(f"fleet reconciler: nudged — the next pass runs within {cfg.watch}s")
    else:
        say("fleet reconciler: nudged, but nothing is ticking; the nudge waits")
    return 0


HOOK_BARE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/._:@+=,~-")


def hook_command(checkout: str = CHECKOUT) -> str:
    """One command every shell parses the same: forward slashes, which bash does
    not eat and Windows accepts; the path bare when every character is plain,
    in double quotes when it holds a space, an apostrophe or the like, and in a
    POSIX shell's single quotes only when a double-quoted shell would expand it."""
    project = checkout.replace("\\", "/")
    if set(project) <= HOOK_BARE:
        pass
    elif not any(c in project for c in '"$`!'):
        project = f'"{project}"'
    else:
        project = shlex.quote(project)
    return f"uv run --project {project} fleet reconcile nudge"


def cmd_hook(cfg: Config) -> int:
    """The worker Stop hook that nudges the loop, as `fleet install` merges it.

    It lives in Claude Code's user settings, which Claude Code merges with the
    settings thurbox hands each worker. Not in thurbox's own hooks file: thurbox
    rewrites that from its embedded payload on every start and heartbeat tick."""
    settings = fleet_platform.claude_settings_file()
    block = json.dumps({"type": "command", "command": hook_command(), "timeout": 10}, indent=2)
    say(f'`uv run fleet install` adds this to the "Stop" hooks in {settings}.')
    say("To add it by hand, put it there as well: thurbox rewrites its own hooks")
    say("file on every start. It is a NUDGE")
    say("and nothing more: it tells the reconciler to run its periodic pass now")
    say(f"instead of waiting out {cfg.collect}s. It closes nothing, it reaps")
    say("nothing, and if it never fires the loop's own timers still catch")
    say("everything it would have caught.")
    say("")
    sys.stdout.write(indented(block, "          "))
    say("")
    say("It is one command with nothing a shell reads differently, so bash, cmd")
    say("and PowerShell run it alike. `nudge` exits 0 whatever happens, and a Stop")
    say("hook blocks the agent only on exit 2, so this one never blocks it.")
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
        try:
            return cmd_nudge(Config.from_env())
        except Exception as exc:
            say(f"fleet reconciler: could not nudge ({exc})")
            return 0
    cfg = Config.from_env()
    if cmd == "__supervise":
        return with_lock(cfg, supervise)
    if cmd == "__loop":
        return with_lock(cfg, tick)
    # Before anything they decide: a legacy loop is a twin none of them can see.
    if cmd in ("ensure", "start", "stop", "restart") and not stop_legacy(cfg):
        return 1
    handlers = {
        "ensure": cmd_ensure, "start": cmd_start, "stop": cmd_stop, "restart": cmd_restart,
        "status": cmd_status, "hook": cmd_hook,
    }
    if cmd in handlers:
        return handlers[cmd](cfg)
    if cmd == "logs":
        return cmd_logs(cfg, argv[1:2] == ["-f"])
    err(f"error: unknown command {cmd!r} (want: {COMMANDS})")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
