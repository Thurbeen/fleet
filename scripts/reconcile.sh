#!/usr/bin/env bash
# The reconciler's lifecycle — a supervised loop that keeps the queue's records
# level with the world, so the lead never has to remember to run anything.
#
# WHY THIS EXISTS. Four failures in one session on 2026-09-08, and every one of
# them is the same shape: a state change that emitted no event fleet was
# listening to.
#
#   1. progress.jsonl was empty on 19 of 20 tasks. `watch` folds transitions
#      only while somebody is running it, and nobody was.
#   2. Three pull requests merged and three tasks landed while the board still
#      said `dispatched`. The lead found out forty minutes later by happening
#      to run `collect`.
#   3. Six workers hit the account's token limit and sat. thurbox went on
#      reporting `working` off a 39-minute-stale hook state. The OPERATOR
#      noticed, not the fleet.
#   4. A session was reaped and the lead learned of it from a
#      `Session not found:` error.
#
# THREE CLASSES, THREE MECHANISMS, and the third is the one that decides the
# design:
#
#   thurbox EMITS it       a turn ends, a session is deleted. Consume the
#                          stream CONTINUOUSLY rather than on the lead's
#                          cadence — that is `queue.sh watch`, looped.
#   only the FORGE knows   a pull request merged. Nothing local will ever say
#                          so, so poll it cheaply — `collect` and `shepherd`
#                          already batch their forge calls.
#   a NON-EVENT            a worker ran out of quota and stopped emitting.
#                          Detectable only by ABSENCE, on a timer. Nothing will
#                          ever fire.
#
# That third row is why hooks alone cannot be the answer. A Claude Code hook
# runs inside the worker's own process, so a worker that died on a token limit
# fires nothing at all. Hooks are a fine accelerator and a bad guarantee: the
# timer below is the guarantee, and `reconcile.sh nudge` is the accelerator.
#
# WHAT IT IS NOT. It is not a cron, and FLEET.md's `## What you are not` still
# means what it says. A cron gives no supervision, no adoption of a running
# instance, and no durable stop; it also cannot be asked what it is doing. This
# is a loop the OPERATOR starts and the operator stops, modelled line for line
# on scripts/webui.sh, which already solved this lifecycle:
#
#   `ensure`  start it unless it is running or has been asked down. Adopts a
#             live loop; never a second one over one queue.
#   `stop`    writes a DOWN FLAG to disk before it kills anything. The flag is
#             what makes "down" mean something across a restart, a reboot and
#             the next skill run.
#   `start`   the operator asking for it back. It CLEARS the flag. That is the
#             whole difference between the two.
#
# IT WRITES NOTHING ITSELF. Every effect it has goes through
# `./scripts/queue.sh`, which stays the only writer over the records — the same
# rule the monitor lives under, and what keeps the queue single-writer. Grep
# this file for a write to a task and you will not find one.
#
# IT DOES NOT DECIDE WHAT RUNS. No dispatch, no cancel, no reorder. It
# reconciles recorded state with observed state; choosing the work stays the
# lead's. It does not second-guess `refuel` either — the rule that nothing is
# restarted into a spent quota window lives there, and this calls the command.
#
# Usage:
#   scripts/reconcile.sh ensure     # start unless running or asked down
#   scripts/reconcile.sh start      # start, and clear a previous `stop`
#   scripts/reconcile.sh stop       # durably down: writes the flag, then stops
#   scripts/reconcile.sh restart    # stop and start, clearing the flag
#   scripts/reconcile.sh status     # ticking? since when? on what queue?
#   scripts/reconcile.sh nudge      # advisory: run the periodic pass NOW
#   scripts/reconcile.sh hook       # print the worker Stop hook that nudges
#   scripts/reconcile.sh logs [-f]  # the loop's log
#
# Runtime state lives in orchestration/reconcile/ and is GITIGNORED — a pid, a
# heartbeat, a log and the down flag are true on one machine only.
#
# THE CADENCES, and why each number is the number:
#
#   watch     20s per call, back to back — effectively continuous. The stream
#             is a local socket and costs nothing, and `watch` resumes from
#             each task's OWN floor, so consecutive calls lose no event in the
#             gap between them. 20s is not a polling interval, it is how long
#             the loop is willing to sit inside one call before it looks at the
#             clock again — which is also the worst-case latency of a nudge.
#   collect   120s. Reads result.md off the disk; its only forge call is one
#             `gh pr view` per task that has a NEW result to verify. Failure 2
#             above was forty minutes of ignorance; two minutes is the same
#             answer inside the same train of thought, and a nudge collapses it
#             to seconds.
#   refuel    300s. Reads the ACCOUNT's quota window through quota-axi, which
#             is a network call to the vendor — the same reading the TUI pane
#             rate-limits to FUEL_TTL = 300s, and this holds to that number
#             rather than inventing a second cost model. The condition it looks
#             for is a `working` state that has stood past
#             STALE_WORKING_SECS = 30 min, so five minutes is six looks at a
#             half-hour fact: shorter buys nothing and spends the window it is
#             reporting on.
#   shepherd  900s. The most expensive pass by a distance — a `gh pr list` per
#             distinct repo, then per-PR checks, reviews and mergeability. A
#             pull request's own CI does not change state faster than that, so
#             a tighter interval would ask the forge the same question several
#             times for one answer.
#
# Every one is overridable for a test or an unusual fleet; see Environment.
#
# Environment:
#   FLEET_RECONCILE_DIR         runtime state (default orchestration/reconcile)
#   FLEET_RECONCILE_QUEUE_CMD   the command it drives (default ./scripts/queue.sh).
#                               The seam the selftest stubs, exactly as
#                               FLEET_QUEUE_WATCH_CMD is queue.sh's.
#   FLEET_RECONCILE_WATCH_SECS      seconds per `watch` call   (default 20)
#   FLEET_RECONCILE_COLLECT_SECS    seconds between collects    (default 120)
#   FLEET_RECONCILE_REFUEL_SECS     seconds between refuels     (default 300)
#   FLEET_RECONCILE_SHEPHERD_SECS   seconds between shepherds   (default 900)
#   FLEET_QUEUE_DIR             the queue to reconcile (default: THIS
#                               CHECKOUT's — see scripts/queue.sh root)
#
# Requires: python3 and whatever the pass it is running needs — thurbox-cli for
# `watch` and `refuel`, `gh` for `collect` and `shepherd`, `quota-axi` for the
# fuel reading. Every one of those degrades to "could not check" inside
# queue.sh rather than to a guess, so a missing tool costs its own pass and
# never the loop.

set -uo pipefail

# Resolved before the cd, because the supervisor is re-executed by path from
# the repo root and a relative $0 would not survive the move.
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

cd "$(dirname "$SELF")/.." || exit 2

RT="${FLEET_RECONCILE_DIR:-orchestration/reconcile}"
PIDFILE="$RT/pid"
DOWNFILE="$RT/down"
BEATFILE="$RT/heartbeat"
NUDGEFILE="$RT/nudge"
LOG="$RT/reconcile.log"

QUEUE_CMD="${FLEET_RECONCILE_QUEUE_CMD:-./scripts/queue.sh}"

WATCH_SECS="${FLEET_RECONCILE_WATCH_SECS:-20}"
COLLECT_SECS="${FLEET_RECONCILE_COLLECT_SECS:-120}"
REFUEL_SECS="${FLEET_RECONCILE_REFUEL_SECS:-300}"
SHEPHERD_SECS="${FLEET_RECONCILE_SHEPHERD_SECS:-900}"

# How stale a heartbeat has to be before `status` calls the loop stalled rather
# than ticking. Generous on purpose: one pass is a `watch` call plus, at worst,
# a shepherd sweep over every repo the queue names, and a slow forge is not a
# wedged reconciler. It is a NOTE on status and never an adoption test — if
# adoption turned on freshness, one slow pass would let a second loop start.
STALL_SECS=$((WATCH_SECS + 600))

# Keep the log to something a person can open. The reconciler is far chattier
# than the monitor — a pass every couple of minutes, forever — so unlike
# webui.sh this one trims itself.
LOG_MAX_BYTES=$((4 * 1024 * 1024))
LOG_KEEP_LINES=2000

# The hidden subcommands. `supervisor` is the process `launch` puts under
# setsid, and the word has to appear in its argv so a recycled pid belonging to
# something else is never mistaken for the reconciler and never signalled.
# `tick` is the loop it supervises, in its own process for the same reason
# webui.sh's server is: the supervisor can then restart it without restarting
# itself.
SUPERVISE="__fleet-reconcile-supervisor"
TICK="__fleet-reconcile-tick"

say() { printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }

log() { printf '[%s] %s\n' "$(date -Is)" "$*" >>"$LOG"; }

# --- is it up? ---------------------------------------------------------------

# Echo the live supervisor pid, or nothing. A pidfile alone proves nothing: it
# outlives a reboot, and pids are reused. Says nothing about whether the loop
# it supervises ever got going — use this only to signal it.
supervisor_pid() {
	local pid
	pid="$(cat "$PIDFILE" 2>/dev/null)" || return 1
	[ -n "$pid" ] || return 1
	kill -0 "$pid" 2>/dev/null || return 1
	ps -o args= -p "$pid" 2>/dev/null | grep -qF -- "$SUPERVISE" || return 1
	printf '%s\n' "$pid"
}

# Echo the live supervisor pid, but only once the loop has actually beaten
# once. A supervisor stuck restarting a tick loop that exits immediately — no
# queue command on the machine, a queue directory it cannot read — is a live
# process and is not a running reconciler: it must never be adopted, and never
# reported healthy.
running_pid() {
	local pid
	pid="$(supervisor_pid)" || return 1
	[ -s "$BEATFILE" ] || return 1
	printf '%s\n' "$pid"
}

asked_down() { [ -f "$DOWNFILE" ]; }

beat_age() {
	local beat
	beat="$(cat "$BEATFILE" 2>/dev/null)" || return 1
	[ -n "$beat" ] || return 1
	printf '%s\n' "$(($(date +%s) - beat))"
}

# --- the tick loop -----------------------------------------------------------

trim_log() {
	local size
	size="$(wc -c <"$LOG" 2>/dev/null)" || return 0
	[ "${size:-0}" -gt "$LOG_MAX_BYTES" ] || return 0
	tail -n "$LOG_KEEP_LINES" "$LOG" >"$LOG.trim" 2>/dev/null &&
		mv "$LOG.trim" "$LOG" &&
		log "log trimmed to the last $LOG_KEEP_LINES lines"
}

# Run one queue command, and put its output in the log only when it said
# something. `quiet-unless-moved` is for `watch`, which runs every few seconds
# and reports "0 task(s) moved" nearly every time; logging that would bury the
# passes that matter under a wall of nothing.
run_pass() {
	local label="$1" quiet="$2"
	shift 2
	local out rc
	out="$($QUEUE_CMD "$@" 2>&1)"
	rc=$?
	if [ "$rc" -ne 0 ]; then
		log "$label: exit $rc"
		printf '%s\n' "$out" | sed 's/^/    /' >>"$LOG"
		return "$rc"
	fi
	if [ "$quiet" = "quiet-unless-moved" ] && grep -qF -- "0 task(s) moved" <<<"$out"; then
		return 0
	fi
	log "$label:"
	printf '%s\n' "$out" | sed 's/^/    /' >>"$LOG"
	return 0
}

# The loop. Read the cadences in the header before changing a number here.
tick() {
	# Validated BEFORE the first heartbeat, so a reconciler that cannot run the
	# one command it drives never reads as up. The supervisor will retry it
	# with backoff and say so in the log, which is the same shape as the
	# monitor failing to bind.
	if ! $QUEUE_CMD root >/dev/null 2>&1; then
		printf '[%s] cannot run %q — nothing to reconcile\n' \
			"$(date -Is)" "$QUEUE_CMD" >&2
		return 2
	fi

	local last_collect=0 last_refuel=0 last_shepherd=0 nudged stamp
	while :; do
		# The down flag is checked at the TOP of every pass as well as by the
		# supervisor, so a `stop` that lands mid-pass is honoured at the next
		# boundary instead of one whole watch call later.
		[ -f "$DOWNFILE" ] && return 0

		stamp="$(date +%s)"
		printf '%s\n' "$stamp" >"$BEATFILE"

		# A nudge is ADVISORY and it is consumed here. It brings the periodic
		# pass forward and nothing else: it cannot make the reconciler collect
		# a task twice, and if it never arrives the timers below still catch
		# everything it would have caught.
		nudged=0
		if [ -f "$NUDGEFILE" ]; then
			log "nudged: $(head -1 "$NUDGEFILE" 2>/dev/null)"
			rm -f "$NUDGEFILE"
			nudged=1
		fi

		# collect first, and on its own: it is the one that CLOSES tasks, and
		# shepherd's view of which pull requests still matter is better for
		# running after it.
		if [ "$nudged" -eq 1 ] || [ $((stamp - last_collect)) -ge "$COLLECT_SECS" ]; then
			run_pass collect always collect
			last_collect="$stamp"
		fi
		if [ $((stamp - last_shepherd)) -ge "$SHEPHERD_SECS" ]; then
			run_pass shepherd always shepherd
			last_shepherd="$stamp"
		fi
		if [ $((stamp - last_refuel)) -ge "$REFUEL_SECS" ]; then
			run_pass refuel always refuel
			last_refuel="$stamp"
		fi

		trim_log

		# And then the continuous half, which is also what paces the loop: this
		# call blocks for WATCH_SECS reading the stream, so there is no window
		# in which events go unread.
		#
		# THE FLOOR IS NOT OPTIONAL. `watch` returns IMMEDIATELY, without
		# reading anything, when no task has a session attached yet — and it
		# returns 0, because that is not an error. Without this the loop would
		# spin at the speed of process creation for as long as the queue is
		# empty, which is most of the time and exactly when nobody is looking
		# at it. So the pass is timed and the remainder of WATCH_SECS is slept:
		# a call that really did block costs nothing extra, and one that did
		# not still paces the loop.
		local began ran
		began="$(date +%s)"
		run_pass watch quiet-unless-moved watch --for-secs "$WATCH_SECS"
		ran=$(($(date +%s) - began))
		[ "$ran" -lt "$WATCH_SECS" ] && sleep $((WATCH_SECS - ran))
	done
}

# --- the supervisor ----------------------------------------------------------

# Restart the tick loop when it dies, and only then. The down flag is the exit
# condition, so an intentional stop is never fought by a restart.
#
# Runs under setsid as its own process-group leader, so `stop` can signal the
# supervisor and its current child together and neither is left behind.
supervise() {
	local rc backoff=1
	# The supervisor records ITSELF, rather than the launcher recording it.
	# `setsid` execs in some shells and forks in others, so the launcher's `$!`
	# is not reliably this process. After setsid this pid is also its own
	# process-group id, which is what `terminate` signals.
	printf '%s\n' "$$" >"$PIDFILE"
	while :; do
		"$SELF" "$TICK" >>"$LOG" 2>&1
		rc=$?
		if [ -f "$DOWNFILE" ]; then
			log "asked down; supervisor exiting"
			return 0
		fi
		if [ "$rc" -eq 0 ]; then
			log "tick loop exited cleanly; supervisor exiting"
			return 0
		fi
		log "tick loop exited $rc; restarting in ${backoff}s"
		sleep "$backoff"
		# Back off to a minute so a loop that cannot run at all is not a busy
		# loop writing a log file until the disk fills.
		[ "$backoff" -lt 60 ] && backoff=$((backoff * 2))
	done
}

# --- start / stop ------------------------------------------------------------

launch() {
	mkdir -p "$RT" || return 1
	rm -f "$BEATFILE" "$PIDFILE"
	log "starting"

	setsid "$SELF" "$SUPERVISE" >>"$LOG" 2>&1 &
	disown 2>/dev/null

	# Wait for the first heartbeat rather than assuming one. The first pass
	# runs `collect`, `shepherd` and `refuel` — the loop catches up the moment
	# it starts — but the beat is written before any of them, so this waits on
	# the loop being alive and not on a forge round trip.
	local waited=0
	while [ "$waited" -lt 120 ]; do
		[ -s "$BEATFILE" ] && break
		sleep 0.25
		waited=$((waited + 1))
	done

	if [ ! -s "$BEATFILE" ]; then
		err "fleet reconciler: did not tick within 30s. Last log lines:"
		tail -n 15 "$LOG" >&2
		# A supervisor whose loop never got going is still out there retrying —
		# kill it rather than leave it behind a stale pid file.
		terminate
		rm -f "$PIDFILE"
		return 1
	fi
	return 0
}

terminate() {
	local pid
	pid="$(supervisor_pid)" || return 0
	# Signal the whole process group: the supervisor, the tick loop and the
	# queue command it is currently running, in one shot.
	kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
	local waited=0
	while [ "$waited" -lt 40 ]; do
		supervisor_pid >/dev/null || return 0
		sleep 0.25
		waited=$((waited + 1))
	done
	kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
	sleep 0.5
	supervisor_pid >/dev/null && return 1
	return 0
}

# THE ONE THING A WORKER MUST NEVER DO. `collect` closes tasks and runs `reap`,
# and a worker reaping its own session deletes both the evidence of what it did
# and the cheap way to fix the pull request it just opened. So the loop refuses
# to start in a checkout that is provably not the control plane — the same
# question `queue.sh` already answers for `topic add` and `add`, asked here
# rather than answered a second way.
#
# Silent when nothing can tell (no rendered extension.toml, no live lead) and
# silent when FLEET_QUEUE_DIR names a directory outright, because someone who
# set that meant it. Only a positive identification of a DIFFERENT checkout is
# loud, and then it is fatal.
guard_control_plane() {
	local owner
	owner="$($QUEUE_CMD root --foreign 2>/dev/null)" || return 0
	[ -n "$owner" ] || return 0
	err "fleet reconciler: refusing to run outside the control plane."
	err "    this checkout: $(pwd)"
	err "    control plane: $owner"
	err "  This loop runs \`collect\`, which closes tasks and reaps sessions."
	err "  A worker running it would reap its own session. Start it there:"
	err "      $owner/scripts/reconcile.sh start"
	return 1
}

# --- commands ----------------------------------------------------------------

cmd_ensure() {
	if asked_down; then
		say "fleet reconciler: down, and staying down — you asked for it:"
		sed 's/^/    /' "$DOWNFILE"
		say "    bring it back with ./scripts/reconcile.sh start"
		return 0
	fi
	local pid
	if pid="$(running_pid)"; then
		say "fleet reconciler: already ticking (pid $pid) — adopted, not restarted"
		return 0
	fi
	guard_control_plane || return 1
	launch || return 1
	say "fleet reconciler: ticking (pid $(cat "$PIDFILE"))"
}

cmd_start() {
	if asked_down; then
		rm -f "$DOWNFILE"
		say "fleet reconciler: clearing the down flag"
	fi
	local pid
	if pid="$(running_pid)"; then
		say "fleet reconciler: already ticking (pid $pid) — adopted, not restarted"
		return 0
	fi
	guard_control_plane || return 1
	launch || return 1
	say "fleet reconciler: ticking (pid $(cat "$PIDFILE"))"
}

cmd_stop() {
	# The flag FIRST, then the kill. In this order the supervisor reads a set
	# flag when its child dies and exits; the other order races it into a
	# restart. It is also what makes the stop durable if the kill fails.
	mkdir -p "$RT" || return 1
	printf 'stopped %s by %s\n' "$(date -Is)" "${USER:-someone}" >"$DOWNFILE"
	if terminate; then
		say "fleet reconciler: down, durably — $DOWNFILE keeps it down across a"
		say "    restart, a reboot and the next onboarding run."
		say "    bring it back with ./scripts/reconcile.sh start"
		return 0
	fi
	err "fleet reconciler: could not stop pid $(cat "$PIDFILE" 2>/dev/null)"
	err "    the down flag is written, so nothing will restart it."
	return 1
}

cmd_restart() {
	rm -f "$DOWNFILE"
	terminate || return 1
	guard_control_plane || return 1
	launch || return 1
	say "fleet reconciler: ticking (pid $(cat "$PIDFILE"))"
}

cmd_status() {
	local pid queue age
	# Asked of the queue itself rather than re-derived here, so this line and
	# `queue.sh list`'s cannot disagree about which queue is in play.
	queue="$($QUEUE_CMD root 2>/dev/null)" || queue="${FLEET_QUEUE_DIR:-orchestration/queue}"
	if pid="$(running_pid)"; then
		age="$(beat_age)" || age=""
		if [ -n "$age" ] && [ "$age" -gt "$STALL_SECS" ]; then
			say "STALLED   ticking, but the last pass was ${age}s ago (over ${STALL_SECS}s)"
			say "          look at $LOG before you restart it"
		else
			say "up        reconciling, last pass ${age:-?}s ago"
		fi
		say "pid       $pid"
	elif asked_down; then
		say "down      asked down, and it will stay down:"
		sed 's/^/          /' "$DOWNFILE"
		say "          ./scripts/reconcile.sh start brings it back"
	else
		say "down      not running, and no down flag — ./scripts/reconcile.sh ensure starts it"
	fi
	say "queue     $queue"
	say "watch     every ${WATCH_SECS}s, back to back — the continuous fold"
	say "collect   every ${COLLECT_SECS}s"
	say "refuel    every ${REFUEL_SECS}s"
	say "shepherd  every ${SHEPHERD_SECS}s"
	say "log       $LOG"
	[ -f "$NUDGEFILE" ] && say "nudge     one is waiting: $(head -1 "$NUDGEFILE" 2>/dev/null)"
	return 0
}

# The accelerator, and the ONLY verb that is safe to call from a worker. It
# touches one flag file and runs no queue command whatsoever, so a worker
# calling it cannot collect or reap anything — least of all itself.
#
# Advisory by construction: a nudge that never arrives costs at most one
# COLLECT_SECS of latency, and a nudge that arrives with the reconciler down
# sits in the file until it is next started, where it is simply the first pass
# that would have run anyway.
cmd_nudge() {
	mkdir -p "$RT" || return 1
	printf 'nudged %s by %s\n' "$(date -Is)" "${THURBOX_SESSION:-${USER:-someone}}" >"$NUDGEFILE"
	if running_pid >/dev/null; then
		say "fleet reconciler: nudged — the next pass runs within ${WATCH_SECS}s"
	else
		say "fleet reconciler: nudged, but nothing is ticking; the nudge waits"
	fi
	return 0
}

# PROPOSED, NOT INSTALLED — FLEET.md's rule, and it applies especially here
# because the file this belongs in is thurbox's, not fleet's:
# `~/.config/thurbox/hooks/claude.json` is what `thurbox-cli agent launch-args
# claude` passes as `--settings`, and thurbox owns its contents. Fleet writing
# into it would be one tool editing another's file behind its back, and a
# thurbox update would silently take it away again.
#
# So this PRINTS the block. Paste it into that file's `Stop` array beside the
# `session signal --state done` entry already there; the two are independent
# and either can fail without touching the other.
cmd_hook() {
	cat <<-HOOK
		Add this to the "Stop" array in ~/.config/thurbox/hooks/claude.json —
		the file thurbox passes to every worker as --settings. It is a NUDGE
		and nothing more: it tells the reconciler to run its periodic pass now
		instead of waiting out ${COLLECT_SECS}s. It closes nothing, it reaps
		nothing, and if it never fires the loop's own timers still catch
		everything it would have caught.

		          {
		            "type": "command",
		            "command": "$SELF nudge >/dev/null 2>&1 || true",
		            "timeout": 5
		          }

		The trailing \`|| true\` is the whole safety story: a hook that fails is
		a hook that blocks the agent, and this one has nothing worth blocking
		for. fleet does not write that file for you — it is thurbox's, and a
		thurbox update rewrites it.
	HOOK
}

cmd_logs() {
	[ -f "$LOG" ] || {
		err "fleet reconciler: no log at $LOG yet"
		return 1
	}
	if [ "${1:-}" = "-f" ]; then tail -f "$LOG"; else tail -n 50 "$LOG"; fi
}

# --- entry point -------------------------------------------------------------

case "${1:-}" in
"$SUPERVISE")
	supervise
	exit $?
	;;
"$TICK")
	tick
	exit $?
	;;
-h | --help | "")
	awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$SELF"
	exit 0
	;;
esac

case "$1" in
ensure) cmd_ensure ;;
start) cmd_start ;;
stop) cmd_stop ;;
restart) cmd_restart ;;
status) cmd_status ;;
nudge) cmd_nudge ;;
hook) cmd_hook ;;
logs) cmd_logs "${2:-}" ;;
*)
	err "$(printf 'error: unknown command %q (want: ensure start stop restart status nudge hook logs)' "$1")"
	exit 2
	;;
esac
