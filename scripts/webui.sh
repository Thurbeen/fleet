#!/usr/bin/env bash
# The fleet monitor's lifecycle — a local, read-only web view of the queue.
#
# WHAT IT SHOWS. orchestration/queue, exactly as it is on disk: every topic
# with the prompt that opened it, classified by what its tasks are doing, and
# under each topic the plan (BRIEF.md), the progress (progress.jsonl) and the
# outcome (result.md). It displays and does not control — there is no route
# that dispatches, cancels or reorders anything. scripts/lib/webui.py owns the
# view and says why.
#
# ALWAYS UP, UNLESS ASKED DOWN. That is one sentence with two halves, and the
# second is the one that is easy to get wrong:
#
#   `ensure`  is what the onboarding skill runs. It starts the monitor if it
#             is absent, ADOPTS it if it is already running, and does nothing
#             at all if the operator has asked it down. Never a second server.
#   `stop`    writes a DOWN FLAG to disk before it kills anything. The flag is
#             what makes "down" mean something: the next `ensure`, the next
#             skill run, the next reboot all read it and leave the monitor
#             alone. An in-memory stop would be undone by the next onboarding
#             invocation, which is a stop that does not stop.
#   `start`   is the operator asking for it back. It CLEARS the flag. That is
#             the whole difference between the two, and it is why the skill
#             must call `ensure` and never `start`.
#
# So, concretely, across the three ways a server goes away:
#
#   a crash    the supervisor loop below restarts it, unless the down flag is
#              set — in which case the exit was intentional and it stops.
#   a reboot   nothing survives it; the next `ensure` (a skill run, or a login
#              hook of your own) brings it back. The flag survives, so a
#              monitor asked down before the reboot stays down after it.
#   a second   `ensure` and `start` both adopt a live monitor and print its
#   invocation URL. Two servers over one queue is the failure this prevents.
#
# Usage:
#   scripts/webui.sh ensure     # start unless running or asked down (the skill's call)
#   scripts/webui.sh start      # start, and clear a previous `stop` (the operator's call)
#   scripts/webui.sh stop       # durably down: writes the flag, then stops it
#   scripts/webui.sh restart    # stop and start, clearing the flag
#   scripts/webui.sh status     # running? on what URL? asked down?
#   scripts/webui.sh url        # just the URL, for scripting
#   scripts/webui.sh logs [-f]  # the server log
#
# Runtime state lives in orchestration/webui/ and is GITIGNORED — the port it
# chose, its pid, its log and the down flag are this instance's, not the
# template's. The server's code is tracked; nothing it writes is.
#
# Binding: 127.0.0.1. Anything wider is an explicit FLEET_WEBUI_HOST the
# operator sets, never a default they discover — this serves their prompts,
# their plans and their workers' output.
#
# Environment:
#   FLEET_WEBUI_HOST  bind address (default 127.0.0.1)
#   FLEET_WEBUI_PORT  first port to try (default 7413), then the next 20 —
#                     so a second fleet on one machine gets its own
#   FLEET_WEBUI_DIR   runtime state (default orchestration/webui)
#   FLEET_QUEUE_DIR   the queue to read (default orchestration/queue)
#
# Requires: python3 (with PyYAML) — the same dependency the rest of the gate
# has. No other dependency, no build step, no node_modules: this repo is
# cloned by people who should not pay a toolchain to look at their own queue.

set -uo pipefail

# Resolved before the cd, because the supervisor is re-executed by path from
# the repo root and a relative $0 would not survive the move.
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

cd "$(dirname "$SELF")/.." || exit 2

RT="${FLEET_WEBUI_DIR:-orchestration/webui}"
PIDFILE="$RT/pid"
DOWNFILE="$RT/down"
URLFILE="$RT/url"
LOG="$RT/server.log"

# The hidden subcommand `launch` runs under setsid. It doubles as the marker
# that identifies our own process in `ps`, so a recycled pid belonging to
# something else is never mistaken for the monitor and never signalled.
SUPERVISE="__fleet-webui-supervisor"

say() { printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }

# --- is it up? ---------------------------------------------------------------

# Echo the live supervisor pid, or nothing. A pidfile alone proves nothing: it
# outlives a reboot, and pids are reused.
running_pid() {
	local pid
	pid="$(cat "$PIDFILE" 2>/dev/null)" || return 1
	[ -n "$pid" ] || return 1
	kill -0 "$pid" 2>/dev/null || return 1
	ps -o args= -p "$pid" 2>/dev/null | grep -qF -- "$SUPERVISE" || return 1
	printf '%s\n' "$pid"
}

url_of() { cat "$URLFILE" 2>/dev/null; }

asked_down() { [ -f "$DOWNFILE" ]; }

# --- the supervisor ----------------------------------------------------------

# Restart the server when it dies, and only then. The down flag is the exit
# condition, so an intentional stop is never fought by a restart — that would
# be a supervisor that makes `stop` impossible.
#
# Runs under setsid as its own process-group leader, so `stop` can signal the
# supervisor and its current child together and neither is left behind.
supervise() {
	local rc backoff=1
	# The supervisor records ITSELF, rather than the launcher recording it.
	# `setsid` execs in some shells and forks in others (it forks when the
	# caller is already a process-group leader, which job control makes true),
	# so the launcher's `$!` is not reliably this process. After setsid this
	# pid is also its own process-group id, which is what `terminate` signals.
	printf '%s\n' "$$" >"$PIDFILE"
	while :; do
		python3 scripts/lib/webui.py >>"$LOG" 2>&1
		rc=$?
		if [ -f "$DOWNFILE" ]; then
			printf '[%s] asked down; supervisor exiting\n' "$(date -Is)" >>"$LOG"
			return 0
		fi
		if [ "$rc" -eq 0 ]; then
			printf '[%s] server exited cleanly; supervisor exiting\n' "$(date -Is)" >>"$LOG"
			return 0
		fi
		printf '[%s] server exited %d; restarting in %ds\n' \
			"$(date -Is)" "$rc" "$backoff" >>"$LOG"
		sleep "$backoff"
		# Back off to a minute so a server that cannot bind at all is not a
		# busy loop writing a log file until the disk fills.
		[ "$backoff" -lt 60 ] && backoff=$((backoff * 2))
	done
}

# --- start / stop ------------------------------------------------------------

launch() {
	mkdir -p "$RT" || return 1
	rm -f "$URLFILE" "$PIDFILE" "$RT/port" "$RT/host"
	printf '[%s] starting\n' "$(date -Is)" >>"$LOG"

	setsid "$SELF" "$SUPERVISE" >>"$LOG" 2>&1 &
	disown 2>/dev/null

	# The port is chosen at bind time, so the URL is not knowable here — wait
	# for the server to write it rather than guessing and printing a lie.
	local waited=0
	while [ "$waited" -lt 60 ]; do
		[ -s "$URLFILE" ] && break
		sleep 0.25
		waited=$((waited + 1))
	done

	if [ ! -s "$URLFILE" ]; then
		err "fleet monitor: did not come up within 15s. Last log lines:"
		tail -n 15 "$LOG" >&2
		return 1
	fi
	return 0
}

terminate() {
	local pid
	pid="$(running_pid)" || return 0
	# Signal the whole process group: the supervisor and the python server it
	# is currently running, in one shot, so neither outlives the other.
	kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
	local waited=0
	while [ "$waited" -lt 40 ]; do
		running_pid >/dev/null || return 0
		sleep 0.25
		waited=$((waited + 1))
	done
	kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
	sleep 0.5
	running_pid >/dev/null && return 1
	return 0
}

# --- commands ----------------------------------------------------------------

cmd_ensure() {
	if asked_down; then
		say "fleet monitor: down, and staying down — you asked for it:"
		sed 's/^/    /' "$DOWNFILE"
		say "    bring it back with ./scripts/webui.sh start"
		return 0
	fi
	local pid
	if pid="$(running_pid)"; then
		say "fleet monitor: already up at $(url_of) (pid $pid) — adopted, not restarted"
		return 0
	fi
	launch || return 1
	say "fleet monitor: up at $(url_of)"
}

cmd_start() {
	if asked_down; then
		rm -f "$DOWNFILE"
		say "fleet monitor: clearing the down flag"
	fi
	local pid
	if pid="$(running_pid)"; then
		say "fleet monitor: already up at $(url_of) (pid $pid) — adopted, not restarted"
		return 0
	fi
	launch || return 1
	say "fleet monitor: up at $(url_of)"
}

cmd_stop() {
	# The flag FIRST, then the kill. In this order the supervisor reads a set
	# flag when its child dies and exits; the other order races it into a
	# restart. It is also what makes the stop durable if the kill fails.
	mkdir -p "$RT" || return 1
	printf 'stopped %s by %s\n' "$(date -Is)" "${USER:-someone}" >"$DOWNFILE"
	if terminate; then
		say "fleet monitor: down, durably — $DOWNFILE keeps it down across a"
		say "    restart, a reboot and the next onboarding run."
		say "    bring it back with ./scripts/webui.sh start"
		return 0
	fi
	err "fleet monitor: could not stop pid $(cat "$PIDFILE" 2>/dev/null)"
	err "    the down flag is written, so nothing will restart it."
	return 1
}

cmd_restart() {
	rm -f "$DOWNFILE"
	terminate || return 1
	launch || return 1
	say "fleet monitor: up at $(url_of)"
}

cmd_status() {
	local pid
	if pid="$(running_pid)"; then
		say "up        $(url_of)"
		say "pid       $pid"
		say "queue     ${FLEET_QUEUE_DIR:-orchestration/queue}"
		say "log       $LOG"
		asked_down && say "NOTE      a down flag is present but something is still running"
		return 0
	fi
	if asked_down; then
		say "down      asked down, and it will stay down:"
		sed 's/^/          /' "$DOWNFILE"
		say "          ./scripts/webui.sh start brings it back"
		return 0
	fi
	say "down      not running, and no down flag — ./scripts/webui.sh ensure starts it"
	return 0
}

cmd_url() {
	running_pid >/dev/null || {
		err "fleet monitor: not running"
		return 1
	}
	url_of
}

cmd_logs() {
	[ -f "$LOG" ] || {
		err "fleet monitor: no log at $LOG yet"
		return 1
	}
	if [ "${1:-}" = "-f" ]; then tail -f "$LOG"; else tail -n 50 "$LOG"; fi
}

# --- entry point -------------------------------------------------------------

case "${1:-}" in
"$SUPERVISE")
	# Not in the usage: this is the process `launch` puts under setsid, and
	# the word has to appear in its argv for running_pid to recognise it.
	supervise
	exit $?
	;;
-h | --help | "")
	awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$SELF"
	exit 0
	;;
esac

if ! command -v python3 >/dev/null; then
	err "error: python3 not found"
	exit 2
fi

case "$1" in
ensure) cmd_ensure ;;
start) cmd_start ;;
stop) cmd_stop ;;
restart) cmd_restart ;;
status) cmd_status ;;
url) cmd_url ;;
logs) cmd_logs "${2:-}" ;;
*)
	err "$(printf 'error: unknown command %q (want: ensure start stop restart status url logs)' "$1")"
	exit 2
	;;
esac
