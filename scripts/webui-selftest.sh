#!/usr/bin/env bash
# Prove the monitor's lifecycle claims, rather than assert them.
#
# `scripts/webui.sh` makes four promises that are easy to write down and easy
# to get backwards, and three of them are invisible until the day they matter.
# Each gets a test here, against a throwaway queue and a throwaway runtime
# directory, so a change that quietly inverts one fails the gate:
#
#   1. IT ADOPTS, IT NEVER DUPLICATES. A second `ensure` over a running
#      monitor prints the same URL and leaves the same pid. Two servers over
#      one queue is the failure the lifecycle exists to prevent.
#   2. DOWN IS DURABLE. `stop` writes a flag, and `ensure` — the call the
#      onboarding skill makes — reads it and does nothing. A stop that the
#      next skill run undoes is a stop that does not stop, which is the whole
#      point of the flag being on disk rather than in a process.
#   3. `start` IS THE WAY BACK. Only the operator asking for it clears the
#      flag. That is the entire difference between `start` and `ensure`.
#   4. IT IS A READER. Every write verb is a 405 and the page binds loopback.
#      A monitor that could dispatch would be a second writer over records
#      `queue.sh` owns.
#
# Test 2 is the one to read first. It is the captain's third sentence — always
# up unless the user asks it down — and the half that is not free.
#
# Usage: scripts/webui-selftest.sh        (also: ./scripts/check.sh webui)
#
# Requires: python3 (with PyYAML) and curl — the gate's own dependencies plus
# curl, which is the only way to prove a bound socket answers.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

WEBUI="./scripts/webui.sh"
QUEUE="./scripts/queue.sh"
nl=$'\n'
failed=0
tmp=""

# Inline rather than a function: shellcheck cannot see that a trap invokes one.
# The stop is unconditional — a selftest that leaks a bound socket makes the
# NEXT run of itself fail on a port that is not free.
trap '[ -n "$tmp" ] && FLEET_WEBUI_DIR="$tmp/rt" "$WEBUI" stop >/dev/null 2>&1; [ -n "$tmp" ] && rm -rf "$tmp"' EXIT

pass() { printf '  \033[32mok\033[0m    %s\n' "$1"; }

fail() {
	printf '  \033[31mFAIL\033[0m  %s\n' "$1" >&2
	if [ $# -gt 1 ]; then printf '%s\n' "$2" | sed 's/^/          /' >&2; fi
	failed=1
}

expect() {
	local label="$1" want="$2" out="$3"
	if printf '%s' "$out" | grep -qF -- "$want"; then
		pass "$label"
	else
		fail "$label" "expected to find: $want${nl}--- got ---${nl}$out"
	fi
}

for tool in python3 curl; do
	command -v "$tool" >/dev/null || {
		echo "error: $tool not found" >&2
		exit 2
	}
done

tmp="$(mktemp -d)"
export FLEET_QUEUE_DIR="$tmp/queue"
export FLEET_WEBUI_DIR="$tmp/rt"
# Well away from the default, so running this never fights a monitor the
# operator has up over their real queue.
export FLEET_WEBUI_PORT=17413
mkdir -p "$FLEET_QUEUE_DIR"

# A queue with something in it, so the view is exercised rather than an empty
# page that would pass whatever it rendered.
"$QUEUE" topic add selftest --title "Selftest topic" --prompt 'the prompt, verbatim' >/dev/null
printf 'Do the thing.\n' >"$tmp/brief.md"
"$QUEUE" add selftest a-task --title "A task" --repo "$tmp/repo" --branch t/a \
	--brief-file "$tmp/brief.md" >/dev/null

# --- 1. it adopts a running monitor, never duplicating it ---------------------

first="$("$WEBUI" ensure 2>&1)"
expect "ensure brings the monitor up" "up at http://127.0.0.1:" "$first"

url="$("$WEBUI" url)"
pid_before="$(cat "$FLEET_WEBUI_DIR/pid" 2>/dev/null)"

second="$("$WEBUI" ensure 2>&1)"
expect "a second ensure adopts rather than starting a twin" "adopted, not restarted" "$second"

pid_after="$(cat "$FLEET_WEBUI_DIR/pid" 2>/dev/null)"
if [ -n "$pid_before" ] && [ "$pid_before" = "$pid_after" ]; then
	pass "the adopted monitor is the same process (pid $pid_after)"
else
	fail "the adopted monitor is the same process" "before=$pid_before after=$pid_after"
fi

# --- 4. it is a reader ------------------------------------------------------

body="$(curl -sS "$url" 2>&1)"
expect "the page serves" "fleet monitor" "$body"

api="$(curl -sS "${url}api/queue" 2>&1)"
expect "the API reports the topic" '"slug": "selftest"' "$api"
expect "the API reports the task's state" '"display_state": "queued"' "$api"

for verb in POST PUT DELETE PATCH; do
	code="$(curl -sS -o /dev/null -w '%{http_code}' -X "$verb" "${url}api/queue" 2>&1)"
	if [ "$code" = "405" ]; then
		pass "$verb is refused — the monitor displays, it does not control"
	else
		fail "$verb is refused" "got HTTP $code, wanted 405"
	fi
done

expect "it binds loopback and nothing wider by default" "127.0.0.1" \
	"$(cat "$FLEET_WEBUI_DIR/host")"

# --- 2. down is durable across the call the skill makes ----------------------

stopped="$("$WEBUI" stop 2>&1)"
expect "stop reports it is durable" "down, durably" "$stopped"

if [ -f "$FLEET_WEBUI_DIR/down" ]; then
	pass "stop left a flag on disk, not a fact in a process"
else
	fail "stop left a flag on disk" "no $FLEET_WEBUI_DIR/down"
fi

if curl -sS --max-time 3 "$url" >/dev/null 2>&1; then
	fail "the socket is closed after stop" "$url still answers"
else
	pass "the socket is closed after stop"
fi

# THE TEST THIS FILE EXISTS FOR: the onboarding skill's own call, run against
# a monitor the operator asked down, must leave it down.
after="$("$WEBUI" ensure 2>&1)"
expect "ensure — the skill's call — respects the flag" "staying down" "$after"

if curl -sS --max-time 3 "$url" >/dev/null 2>&1; then
	fail "ensure did not resurrect it" "$url answers again after ensure"
else
	pass "ensure did not resurrect it"
fi

status="$("$WEBUI" status 2>&1)"
expect "status says it was asked down" "asked down" "$status"

# --- 3. start is the way back ------------------------------------------------

back="$("$WEBUI" start 2>&1)"
expect "start clears the flag" "clearing the down flag" "$back"
expect "start brings it back up" "up at http://127.0.0.1:" "$back"

if [ -f "$FLEET_WEBUI_DIR/down" ]; then
	fail "the flag is gone after start" "$FLEET_WEBUI_DIR/down is still there"
else
	pass "the flag is gone after start"
fi

url="$("$WEBUI" url)"
expect "the monitor answers again" "fleet monitor" "$(curl -sS "$url" 2>&1)"

"$WEBUI" stop >/dev/null 2>&1
rm -f "$FLEET_WEBUI_DIR/down"

if [ "$failed" -eq 0 ]; then
	echo "webui selftest: every claim holds"
else
	echo "webui selftest: FAILED" >&2
fi
exit "$failed"
