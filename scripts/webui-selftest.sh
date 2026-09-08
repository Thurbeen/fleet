#!/usr/bin/env bash
# Prove the monitor's lifecycle claims, rather than assert them.
#
# `scripts/webui.sh` makes six promises that are easy to write down and easy
# to get backwards, and five of them are invisible until the day they matter.
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
#   5. A PHANTOM IS NEVER "UP". A supervisor stuck retrying a bind that never
#      succeeds is a live process, not a running monitor — it must never be
#      adopted, and never reported healthy with a blank URL.
#   6. IT RENDERS OFFLINE. The page names no origin it does not serve itself,
#      and the two files the theme needs come out of media/ in this repo. A
#      CDN font is the kind of thing that works on the machine it was written
#      on and fails on a laptop in a train, which is where a monitor is least
#      able to tell you what went wrong.
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

# A herestring, not `printf ... | grep`. This file runs under `set -o pipefail`,
# where a pipeline reports the whole pipeline's status rather than the reader's,
# so a helper built on one can report FAIL for input that plainly matched — seen
# once here against the served page, and a false negative in the helper fails
# the gate for the wrong reason. One command has one status; nothing to misread.
expect() {
	local label="$1" want="$2" out="$3"
	if grep -qF -- "$want" <<<"$out"; then
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

# The HUD's counters are folded server-side out of the same by-state tally the
# pills use, so the bar and the list cannot disagree about what a state means.
expect "the API carries the HUD's counters" '"key": "ready"' "$api"
expect "a HUD counter names the states it buckets" '"dispatched"' "$api"

# --- 6. the theme renders with the network unplugged -------------------------

for asset in assets/press-start-2p.woff2 assets/fleet-banner.jpg; do
	code="$(curl -sS -o /dev/null -w '%{http_code}' "${url}${asset}" 2>&1)"
	if [ "$code" = "200" ]; then
		pass "$asset is served from this repo, not fetched"
	else
		fail "$asset is served from this repo" "got HTTP $code, wanted 200"
	fi
done

if grep -qE 'https?://' <<<"$body"; then
	fail "the page names no off-machine origin" \
		"$(grep -nE 'https?://' <<<"$body" | head -3)"
else
	pass "the page names no off-machine origin"
fi

# The asset table is a whitelist of names, not a document root, so a path that
# is not in it is a 404 whether or not it exists on disk.
code="$(curl -sS --path-as-is -o /dev/null -w '%{http_code}' \
	"${url}assets/../../../etc/passwd" 2>&1)"
if [ "$code" = "404" ]; then
	pass "an asset outside the whitelist is a 404, not a file"
else
	fail "an asset outside the whitelist is a 404" "got HTTP $code, wanted 404"
fi

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

# --- 5. a supervisor that never binds is never mistaken for "up" ------------
#
# A busy port range or a missing dependency makes the python server exit
# immediately, every time; the bash supervisor keeps retrying with backoff
# forever, since nothing sets the down flag. That supervisor is a live
# process, but it has never served anything, and must not be adopted or
# reported healthy. FLEET_WEBUI_PORT set to something non-numeric reproduces
# exactly that: an immediate, permanent exit on every attempt, via the real
# script rather than a stand-in for it.

badrt="$tmp/rt-bad"
mkdir -p "$badrt"

bad_ensure="$(FLEET_WEBUI_DIR="$badrt" FLEET_WEBUI_PORT="not-a-port" "$WEBUI" ensure 2>&1)"
expect "ensure gives up rather than adopting a phantom" "did not come up" "$bad_ensure"

if [ -f "$badrt/pid" ]; then
	fail "a failed launch leaves nothing behind" "$badrt/pid still exists"
else
	pass "a failed launch leaves nothing behind"
fi

bad_status="$(FLEET_WEBUI_DIR="$badrt" FLEET_WEBUI_PORT="not-a-port" "$WEBUI" status 2>&1)"
expect "status reports it down, not adopted" "not running" "$bad_status"

bad_ensure2="$(FLEET_WEBUI_DIR="$badrt" FLEET_WEBUI_PORT="not-a-port" "$WEBUI" ensure 2>&1)"
if grep -qF -- "adopted" <<<"$bad_ensure2"; then
	fail "a second ensure still refuses to adopt the phantom" "$bad_ensure2"
else
	pass "a second ensure still refuses to adopt the phantom"
fi

FLEET_WEBUI_DIR="$badrt" "$WEBUI" stop >/dev/null 2>&1
rm -rf "$badrt"

if [ "$failed" -eq 0 ]; then
	echo "webui selftest: every claim holds"
else
	echo "webui selftest: FAILED" >&2
fi
exit "$failed"
