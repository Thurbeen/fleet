#!/usr/bin/env bash
# Prove the reconciler's claims, rather than assert them.
#
# `scripts/reconcile.sh` makes seven promises. Most are invisible until the day
# they cost something — a second loop closing tasks under the first, a stop the
# next onboarding run undoes, a reconciler that decided to dispatch. Each gets a
# test here, against a throwaway queue, a throwaway runtime directory and a
# STUBBED queue command, so a change that quietly inverts one fails the gate:
#
#   1. IT ADOPTS, IT NEVER DUPLICATES. A second `ensure` over a ticking loop
#      prints the same pid and starts nothing. Two reconcilers over one queue
#      would both run `collect`, which closes tasks and reaps sessions.
#   2. DOWN IS DURABLE. `stop` writes a flag, and `ensure` reads it and does
#      nothing — across a restart and a reboot, because the flag is on disk and
#      not in a process.
#   3. `start` IS THE WAY BACK. Only the operator asking for it clears the
#      flag. That is the entire difference between `start` and `ensure`.
#   4. IT FOLDS CONTINUOUSLY AND POLLS ON ITS OWN CLOCKS. `watch` runs back to
#      back; `collect`, `shepherd` and `refuel` each run on their own interval.
#   5. IT IS NOT A WRITER. Every effect goes through `queue.sh`, and the ONLY
#      subcommands it ever calls are the four reconciling ones — never
#      `dispatch`, `add`, `block`, `archive` or `reap`. Deciding what runs
#      stays the lead's, and the records stay single-writer.
#   6. A NUDGE IS ADVISORY. It brings the periodic pass forward and does
#      nothing else; it runs no queue command of its own, so a worker firing it
#      from a Stop hook cannot collect or reap itself.
#   7. A PHANTOM IS NEVER "UP". A supervisor restarting a tick loop that cannot
#      run at all is a live process and not a running reconciler — never
#      adopted, never reported healthy.
#
# Tests 2 and 5 are the ones to read first. 2 is the operator's stop actually
# meaning stop; 5 is the rule that keeps this a reconciler and not a second
# control plane.
#
# HOW IT RUNS OFFLINE. `FLEET_RECONCILE_QUEUE_CMD` is the seam — the same shape
# as `FLEET_QUEUE_WATCH_CMD` in queue.sh — and this replaces the real
# `queue.sh` with a recorder that appends its own argv to a file and prints
# what the loop expects to read. So nothing here needs thurbox, `gh`,
# `quota-axi` or a network, and the intervals are compressed to seconds so a
# whole day of cadence fits in a few of them.
#
# Usage: scripts/reconcile-selftest.sh    (also: ./scripts/check.sh reconcile)
#
# Requires: bash. Nothing else — the queue command is a stub.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

RECON="./scripts/reconcile.sh"
nl=$'\n'
failed=0
tmp=""

# Inline rather than a function: shellcheck cannot see that a trap invokes one.
# The stop is unconditional — a selftest that leaks a supervisor leaves a loop
# running over a queue directory it is about to delete.
trap '[ -n "$tmp" ] && FLEET_RECONCILE_DIR="$tmp/rt" "$RECON" stop >/dev/null 2>&1; [ -n "$tmp" ] && FLEET_RECONCILE_DIR="$tmp/rt-bad" "$RECON" stop >/dev/null 2>&1; [ -n "$tmp" ] && rm -rf "$tmp"' EXIT

pass() { printf '  \033[32mok\033[0m    %s\n' "$1"; }

fail() {
	printf '  \033[31mFAIL\033[0m  %s\n' "$1" >&2
	if [ $# -gt 1 ]; then printf '%s\n' "$2" | sed 's/^/          /' >&2; fi
	failed=1
}

# A herestring, not `printf ... | grep`: under `set -o pipefail` a pipeline
# reports the whole pipeline's status rather than the reader's, so a helper
# built on one can report FAIL for input that plainly matched.
expect() {
	local label="$1" want="$2" out="$3"
	if grep -qF -- "$want" <<<"$out"; then
		pass "$label"
	else
		fail "$label" "expected to find: $want${nl}--- got ---${nl}$out"
	fi
}

# Wait for a condition rather than sleeping a guessed amount. Every timing
# assertion below goes through this, so a slow machine makes the test slower
# and never makes it flaky.
wait_for() {
	local secs="$1" i=0
	shift
	while [ "$i" -lt $((secs * 10)) ]; do
		"$@" && return 0
		sleep 0.1
		i=$((i + 1))
	done
	return 1
}

tmp="$(mktemp -d)"
calls="$tmp/calls"
: >"$calls"

# --- the queue command, stubbed ---------------------------------------------
#
# It records every invocation and answers the way the real one does: `root`
# prints a path (the loop's own precondition check), `watch` prints the summary
# line whose "N task(s) moved" the loop reads to decide whether the pass was
# worth logging. It is itself an assertion — a subcommand this does not know is
# an error, so a reconciler that grew a call to `dispatch` fails here.
cat >"$tmp/queue-stub.sh" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CALLS"
case "${1:-}" in
root)
	# `root --foreign` is the control-plane guard's question, and the honest
	# answer for a throwaway queue is "this IS the control plane" — silence
	# and a non-zero status, exactly as queue.py answers it.
	[ "${2:-}" = "--foreign" ] && exit 1
	printf '%s\n' "${FLEET_QUEUE_DIR:-/nowhere}"
	;;
watch)
	# The real `watch` BLOCKS for --for-secs reading the stream. A stub that
	# returned instantly would hide the loop's pacing floor, which is the one
	# thing standing between an empty queue and a busy loop.
	shift
	[ "${1:-}" = "--for-secs" ] && sleep "${2:-1}"
	printf 'watch: %s task(s) moved, stream at seq 7\n' "$(cat "$MOVED" 2>/dev/null || echo 0)"
	;;
collect) echo "collect: nothing new" ;;
shepherd) echo "shepherd: 0 open pull request(s)" ;;
refuel) echo "refuel: no task here is holding a session" ;;
*)
	echo "queue-stub: REFUSING an unexpected subcommand: $*" >&2
	exit 3
	;;
esac
exit 0
STUB
chmod +x "$tmp/queue-stub.sh"

export CALLS="$calls"
export MOVED="$tmp/moved"
export FLEET_QUEUE_DIR="$tmp/queue"
mkdir -p "$FLEET_QUEUE_DIR"
export FLEET_RECONCILE_DIR="$tmp/rt"
export FLEET_RECONCILE_QUEUE_CMD="$tmp/queue-stub.sh"

# Compressed cadences: seconds instead of minutes, so the whole clock is
# observable inside a gate run. The ORDER is what is being tested — watch
# fastest, then collect, then refuel, then shepherd — not the real numbers,
# which are argued in reconcile.sh's header.
export FLEET_RECONCILE_WATCH_SECS=1
export FLEET_RECONCILE_COLLECT_SECS=2
export FLEET_RECONCILE_REFUEL_SECS=4
export FLEET_RECONCILE_SHEPHERD_SECS=6

count_calls() { grep -c "^$1" "$calls" 2>/dev/null || true; }

# Predicates, not expressions. `wait_for 20 test "$(count_calls watch)" -ge 3`
# reads the count ONCE, when the argv is built, and then waits twenty seconds
# on a number that can no longer change — a test that passes or fails on the
# first sample and calls it patience. These are re-run on every attempt.
# shellcheck disable=SC2317,SC2329  # invoked indirectly, as wait_for's predicate
atleast() { [ "$(count_calls "$1")" -ge "$2" ]; }
# shellcheck disable=SC2317,SC2329  # invoked indirectly, as wait_for's predicate
grew() { [ "$(count_calls "$1")" -gt "$2" ]; }

# --- 1. it adopts a ticking loop, never duplicating it -----------------------

first="$("$RECON" ensure 2>&1)"
expect "ensure starts the loop" "ticking (pid " "$first"

pid_before="$(cat "$FLEET_RECONCILE_DIR/pid" 2>/dev/null)"

second="$("$RECON" ensure 2>&1)"
expect "a second ensure adopts rather than starting a twin" "adopted, not restarted" "$second"

pid_after="$(cat "$FLEET_RECONCILE_DIR/pid" 2>/dev/null)"
if [ -n "$pid_before" ] && [ "$pid_before" = "$pid_after" ]; then
	pass "the adopted loop is the same process (pid $pid_after)"
else
	fail "the adopted loop is the same process" "before=$pid_before after=$pid_after"
fi

# --- 4. it folds continuously and polls on its own clocks --------------------

# The first pass runs all three periodic commands, so the loop catches up the
# moment it is started rather than waiting out its longest interval.
for c in collect shepherd refuel; do
	if wait_for 15 grep -q "^$c" "$calls"; then
		pass "the first pass runs $c — starting it is catching up"
	else
		fail "the first pass runs $c" "$(cat "$calls")"
	fi
done

# `watch` back to back is what makes the fold continuous: several calls inside
# the time ONE collect interval takes.
if wait_for 20 atleast watch 3; then
	pass "watch runs back to back — the fold is continuous, not a poll"
else
	fail "watch runs back to back" "watch calls: $(count_calls watch)${nl}$(cat "$calls")"
fi

# And the cadences are separate clocks, not one. Over the same window the
# fastest interval has fired strictly more often than the slowest.
if wait_for 25 atleast collect 3; then
	pass "collect runs on its own interval, repeatedly"
else
	fail "collect runs on its own interval" "collect calls: $(count_calls collect)"
fi

# shellcheck disable=SC2317,SC2329  # invoked indirectly, as wait_for's predicate
four_clocks() {
	[ "$(count_calls watch)" -gt "$(count_calls collect)" ] &&
		[ "$(count_calls collect)" -ge "$(count_calls shepherd)" ]
}
if wait_for 30 four_clocks; then
	pass "the intervals are four clocks, not one (watch $(count_calls watch) > collect $(count_calls collect) >= shepherd $(count_calls shepherd))"
else
	fail "the intervals are four clocks, not one" \
		"watch=$(count_calls watch) collect=$(count_calls collect) shepherd=$(count_calls shepherd)"
fi

# --- regression: a moved count of 10, 20, 100... is not mistaken for "quiet" -
#
# `run_pass`'s quiet-unless-moved check reads the literal "0 task(s) moved" out
# of the watch summary line. A moved count whose decimal form ends in 0 — 10,
# 20, 100 — contains that same substring ("...1[0 task(s) moved]..."), so an
# unanchored match would treat a pass that moved real tasks as the nothing-
# happened case and never write it to the log. Drive the stub to report 10
# moved and prove the pass is logged rather than swallowed.
echo 10 >"$MOVED"
if wait_for 10 grep -qF -- "10 task(s) moved" "$FLEET_RECONCILE_DIR/reconcile.log"; then
	pass "a watch pass reporting 10 moved is logged, not swallowed as quiet"
else
	fail "a watch pass reporting 10 moved is logged" "$(cat "$FLEET_RECONCILE_DIR/reconcile.log" 2>/dev/null)"
fi
echo 0 >"$MOVED"

# --- 5. it is not a writer ---------------------------------------------------
#
# The stub refuses any subcommand it was not taught, so a `dispatch` would have
# failed the loop already. This states the claim positively as well: over the
# whole run, the set of things the reconciler asked the queue to do is exactly
# the four reconciling ones plus the `root` precondition check.
verbs="$(awk '{print $1}' "$calls" | sort -u | tr '\n' ' ')"
if [ "$verbs" = "collect refuel root shepherd watch " ]; then
	pass "it calls only watch/collect/shepherd/refuel — never dispatch, add or reap"
else
	fail "it calls only the reconciling subcommands" "called: $verbs"
fi

# It writes to its own runtime directory and to nothing else. The queue
# directory is untouched by the reconciler — every record the real queue.sh
# would write is written by queue.sh, in its own process.
if [ -z "$(ls -A "$FLEET_QUEUE_DIR")" ]; then
	pass "it wrote nothing into the queue directory"
else
	fail "it wrote nothing into the queue directory" "$(ls -A "$FLEET_QUEUE_DIR")"
fi

# --- 6. a nudge is advisory, and runs no queue command itself ----------------

before_nudge="$(count_calls collect)"
: >"$calls.n"
nudged="$("$RECON" nudge 2>&1)"
expect "nudge reports the loop will pick it up" "nudged" "$nudged"

# The nudge process itself asked the queue for nothing. This is the constraint
# that makes it safe in a worker's Stop hook: it cannot collect or reap.
if [ "$(count_calls collect)" = "$before_nudge" ] || [ -f "$FLEET_RECONCILE_DIR/nudge" ]; then
	pass "the nudge is a flag file, not a queue command"
else
	fail "the nudge is a flag file, not a queue command" "$(cat "$calls")"
fi

if wait_for 10 grew collect "$before_nudge"; then
	pass "the nudged pass ran collect without waiting out the interval"
else
	fail "the nudged pass ran collect" "before=$before_nudge now=$(count_calls collect)"
fi

status="$("$RECON" status 2>&1)"
expect "status names the queue it is reconciling" "$FLEET_QUEUE_DIR" "$status"
expect "status says it is up" "up        reconciling" "$status"

hook="$("$RECON" hook 2>&1)"
expect "the hook it proposes calls nudge and nothing else" "reconcile.sh nudge" "$hook"
expect "the hook cannot block the agent it runs in" "|| true" "$hook"

# --- 2. down is durable across the call a skill makes ------------------------

stopped="$("$RECON" stop 2>&1)"
expect "stop reports it is durable" "down, durably" "$stopped"

if [ -f "$FLEET_RECONCILE_DIR/down" ]; then
	pass "stop left a flag on disk, not a fact in a process"
else
	fail "stop left a flag on disk" "no $FLEET_RECONCILE_DIR/down"
fi

quiet="$(count_calls watch)"
sleep 2
if [ "$(count_calls watch)" = "$quiet" ]; then
	pass "the loop really stopped ticking"
else
	fail "the loop really stopped ticking" "watch went $quiet -> $(count_calls watch)"
fi

# THE TEST THIS FILE EXISTS FOR: `ensure` run against a reconciler the operator
# asked down must leave it down, whatever runs it and however often.
after="$("$RECON" ensure 2>&1)"
expect "ensure respects the flag" "staying down" "$after"

sleep 1
if [ "$(count_calls watch)" = "$quiet" ]; then
	pass "ensure did not resurrect it"
else
	fail "ensure did not resurrect it" "watch went $quiet -> $(count_calls watch)"
fi

down_status="$("$RECON" status 2>&1)"
expect "status says it was asked down" "asked down" "$down_status"

# --- 3. start is the way back ------------------------------------------------

back="$("$RECON" start 2>&1)"
expect "start clears the flag" "clearing the down flag" "$back"
expect "start brings it back" "ticking (pid " "$back"

if [ -f "$FLEET_RECONCILE_DIR/down" ]; then
	fail "the flag is gone after start" "$FLEET_RECONCILE_DIR/down is still there"
else
	pass "the flag is gone after start"
fi

if wait_for 15 grew watch "$quiet"; then
	pass "it is folding again"
else
	fail "it is folding again" "watch stuck at $quiet"
fi

"$RECON" stop >/dev/null 2>&1
rm -f "$FLEET_RECONCILE_DIR/down"

# --- 7. a supervisor whose loop never gets going is never "up" ---------------
#
# A queue command that is not on the machine makes the tick loop exit
# immediately, every time; the supervisor keeps retrying with backoff forever,
# since nothing sets the down flag. That supervisor is a live process that has
# never reconciled anything, and it must not be adopted or called healthy.

badrt="$tmp/rt-bad"
mkdir -p "$badrt"
bad() { env FLEET_RECONCILE_DIR="$badrt" FLEET_RECONCILE_QUEUE_CMD="$tmp/nothing-here" "$RECON" "$@"; }

bad_ensure="$(bad ensure 2>&1)"
expect "ensure gives up rather than adopting a phantom" "did not tick" "$bad_ensure"

if [ -f "$badrt/pid" ]; then
	fail "a failed launch leaves nothing behind" "$badrt/pid still exists"
else
	pass "a failed launch leaves nothing behind"
fi

bad_status="$(bad status 2>&1)"
expect "status reports it down, not adopted" "not running" "$bad_status"

bad_ensure2="$(bad ensure 2>&1)"
if grep -qF -- "adopted" <<<"$bad_ensure2"; then
	fail "a second ensure still refuses to adopt the phantom" "$bad_ensure2"
else
	pass "a second ensure still refuses to adopt the phantom"
fi

bad stop >/dev/null 2>&1
rm -rf "$badrt"

if [ "$failed" -eq 0 ]; then
	echo "reconcile selftest: every claim holds"
else
	echo "reconcile selftest: FAILED" >&2
fi
exit "$failed"
