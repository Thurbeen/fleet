#!/usr/bin/env bash
# Prove that `scripts/fleet-status.sh` degrades rather than fails.
#
# A status command is read by an agent that is about to decide something, so
# its two failure modes are both silent and both expensive:
#
#   1. IT DIES WHEN A PROBE DIES. No network, no `gh`, no thurbox running, no
#      monitor — any one of those must cost exactly its own section and
#      nothing else. A status command that exits non-zero because one probe
#      failed is worse than none, because the lead learns nothing at all.
#   2. IT FLATTENS THE STATE VOCABULARY. `idle`, `running`, `uncovered` and
#      `unreported` are four different facts (thurbox-session SKILL §4a), and
#      reporting any of the last three as `idle` reports a worker mid-turn as
#      finished. This asserts the words survive the trip.
#
# It also holds the line on the third promise: the command READS. It must not
# start the monitor, dispatch a task, or touch a single byte of the queue.
#
# Every probe is a stub on a sandboxed PATH, so the run is hermetic: no real
# thurbox, no GitHub, no monitor, and no queue but the throwaway one.
#
# Usage: scripts/fleet-status-selftest.sh     (also: ./scripts/check.sh status)
#
# Requires: python3 (with PyYAML) and git — the gate's own dependencies.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

STATUS="./scripts/fleet-status.sh"
QUEUE="./scripts/queue.sh"
nl=$'\n'
failed=0
tmp=""

trap '[ -n "$tmp" ] && rm -rf "$tmp"' EXIT

pass() { printf '  \033[32mok\033[0m    %s\n' "$1"; }

fail() {
	printf '  \033[31mFAIL\033[0m  %s\n' "$1" >&2
	if [ $# -gt 1 ]; then printf '%s\n' "$2" | sed 's/^/          /' >&2; fi
	failed=1
}

# A herestring, not a pipeline: this file runs under `set -o pipefail`, where a
# pipeline reports the whole pipeline's status rather than the reader's, and a
# helper built on one can report FAIL for input that plainly matched.
expect() {
	local label="$1" want="$2" out="$3"
	if grep -qF -- "$want" <<<"$out"; then
		pass "$label"
	else
		fail "$label" "expected to find: $want${nl}--- got ---${nl}$out"
	fi
}

refute() {
	local label="$1" unwanted="$2" out="$3"
	if grep -qF -- "$unwanted" <<<"$out"; then
		fail "$label" "did not want: $unwanted${nl}--- got ---${nl}$out"
	else
		pass "$label"
	fi
}

for tool in python3 git; do
	command -v "$tool" >/dev/null || {
		echo "error: $tool not found" >&2
		exit 2
	}
done

tmp="$(mktemp -d)"
export FLEET_QUEUE_DIR="$tmp/queue"
# The monitor's runtime state, pointed somewhere empty so this reads "down"
# without going anywhere near the operator's real one.
export FLEET_WEBUI_DIR="$tmp/rt"
mkdir -p "$FLEET_QUEUE_DIR" "$tmp/rt" "$tmp/repo"

# A sandboxed PATH holding only the tools the command is allowed to find. This
# is the whole point: `gh` and `thurbox-cli` exist on the machine running the
# gate, so "absent" has to be constructed rather than assumed.
REAL_PATH="$PATH"
sandbox() {
	local dir="$1" tool src
	shift
	rm -rf "$dir"
	mkdir -p "$dir"
	for tool in "$@"; do
		src="$(PATH="$REAL_PATH" command -v "$tool")" || continue
		ln -sf "$src" "$dir/$tool"
	done
	printf '%s\n' "$dir"
}

BASE_TOOLS=(bash env sh python3 git dirname basename awk sed cat grep ps uname)
bare="$(sandbox "$tmp/bin-bare" "${BASE_TOOLS[@]}")"
stubbed="$(sandbox "$tmp/bin-stubbed" "${BASE_TOOLS[@]}")"

# --- a queue with something in it -------------------------------------------

"$QUEUE" topic add selftest --title "Selftest topic" --prompt 'the prompt, verbatim' >/dev/null
printf 'Do the thing.\n' >"$tmp/brief.md"
"$QUEUE" add selftest dispatched-task --title "A dispatched task" --repo "$tmp/repo" \
	--branch t/dispatched --touches FLEET.md --brief-file "$tmp/brief.md" >/dev/null
"$QUEUE" add selftest ready-task --title "A ready task" --repo "$tmp/repo" \
	--branch t/ready --touches FLEET.md --brief-file "$tmp/brief.md" >/dev/null
"$QUEUE" add selftest waiting-task --title "A waiting task" --repo "$tmp/repo" \
	--branch t/waiting --brief-file "$tmp/brief.md" >/dev/null
"$QUEUE" block selftest/03-waiting-task --on selftest/01-dispatched-task \
	--kind semantic-dependency --why 'consumes the flag the first one adds' >/dev/null
"$QUEUE" attach selftest/01-dispatched-task 11111111-1111-1111-1111-111111111111 >/dev/null

# --- 1. every probe missing, and it still answers ----------------------------

out="$(PATH="$bare" "$STATUS" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ]; then
	pass "exits 0 with no thurbox-cli, no gh and no monitor"
else
	fail "exits 0 with every probe missing" "exit $rc${nl}--- got ---${nl}$out"
fi

for section in FUEL QUEUE SESSIONS PRS MONITOR CHECKOUT; do
	expect "$section still prints when the probes are gone" "$section" "$out"
done

expect "it names the tool it could not find (thurbox-cli)" "thurbox-cli not found" "$out"
expect "it names the tool it could not find (gh)" "gh not found" "$out"
expect "it names the tool it could not find (quota-axi)" "quota-axi not found" "$out"
expect "the queue section survives the others failing" "01-dispatched-task" "$out"
expect "a blocked task says what is holding it" "semantic-dependency" "$out"
expect "and why, in the words that were recorded" "consumes the flag" "$out"
expect "a ready task is called ready, not queued" "ready" "$out"
expect "file overlap is reported as a risk, not a blocker" "FLEET.md" "$out"
expect "the monitor reads as down rather than as an error" "down" "$out"

# --- 2. --json degrades in the same shape ------------------------------------

js="$(PATH="$bare" "$STATUS" --json 2>&1)"
rc=$?
if [ "$rc" -eq 0 ]; then pass "--json exits 0 too"; else
	fail "--json exits 0" "exit $rc${nl}$js"
fi

probe="$(PATH="$bare" python3 - "$js" <<'PY' 2>&1
import json, sys
doc = json.loads(sys.argv[1])
for key in ("fuel", "queue", "sessions", "prs", "monitor", "checkout"):
    assert key in doc, f"missing section {key}"
    assert "unavailable" in doc[key], f"{key} has no unavailable field"
assert doc["sessions"]["unavailable"], "sessions should be unavailable"
assert doc["prs"]["unavailable"], "prs should be unavailable"
assert doc["fuel"]["unavailable"], "fuel should be unavailable"
assert doc["fuel"]["remaining"] is None, "no reading is None, never 0"
assert doc["queue"]["unavailable"] is None, "the queue is on disk and readable"
assert len(doc["queue"]["topics"][0]["tasks"]) == 3, "three tasks expected"
print("parsed")
PY
)"
expect "--json is valid, has every section, and marks the missing ones" "parsed" "$probe"

# --- 3. a queue directory that is not there ----------------------------------

gone="$(PATH="$bare" FLEET_QUEUE_DIR="$tmp/no-such-queue" "$STATUS" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ]; then
	pass "a missing queue directory costs the queue section and nothing else"
else
	fail "a missing queue directory does not fail the command" "exit $rc${nl}$gone"
fi
expect "the checkout still reports with no queue at all" "CHECKOUT" "$gone"

# --- 4. the state vocabulary is not flattened --------------------------------
#
# THE TEST THIS FILE EXISTS FOR. `uncovered` means "wired to report nothing",
# `unreported` means "can report and has not", `running` means "something holds
# the pane". Printing any of them as `idle` tells the lead a worker mid-turn
# has finished.

cat >"$stubbed/thurbox-cli" <<'STUB'
#!/bin/sh
# Only `session list --json` is read; anything else is not this stub's business.
cat <<'JSON'
[{"id":"11111111-1111-1111-1111-111111111111","name":"A dispatched task",
  "state":"uncovered","state_source":"process","hook_state_age_secs":null,
  "stopped":false,"hook_reported":false},
 {"id":"22222222-2222-2222-2222-222222222222","name":"Someone else's session",
  "state":"unreported","state_source":"process","hook_state_age_secs":null,
  "stopped":false,"hook_reported":false}]
JSON
STUB
chmod +x "$stubbed/thurbox-cli"

cat >"$stubbed/gh" <<'STUB'
#!/bin/sh
cat <<'JSON'
[{"number":13,"url":"https://github.com/Thurbeen/fleet/pull/13",
  "title":"Add one-call fleet status","headRefName":"t/dispatched","state":"OPEN",
  "statusCheckRollup":[{"__typename":"CheckRun","name":"gate","status":"COMPLETED","conclusion":"SUCCESS"}]}]
JSON
STUB
chmod +x "$stubbed/gh"

# quota-axi in its real shape (schemaVersion 5): three windows that reset
# independently, carrying BOTH the measured fields and the projected ones — so
# section 6 can prove which of them reach the screen.
fuel_stub() {
	cat >"$1/quota-axi" <<STUB
#!/bin/sh
cat <<'JSON'
{"generatedAt":"2026-03-15T16:42:00.000Z","schemaVersion":5,"providers":[
 {"provider":"claude","plan":"max","source":"oauth",
  "windows":[
    {"id":"five_hour","label":"session","kind":"session","percentRemaining":90,
     "resetsAt":"2026-03-15T20:10:48.000Z",
     "pace":{"status":"behind","reservePercentPoints":12.4,"burnMultiple":0.5921,
             "projectedExhaustedAt":"2026-03-15T18:02:11.000Z"}},
    {"id":"seven_day","label":"week","kind":"weekly","percentRemaining":$2,
     "resetsAt":"2026-03-20T17:59:45.600Z",
     "pace":{"status":"ahead","reservePercentPoints":-8.2,"burnMultiple":1.295,
             "projectedExhaustedAt":"2026-03-19T03:43:45.600Z"}},
    {"id":"model:fable","label":"Fable week","kind":"model","percentRemaining":100,
     "resetsAt":"2026-03-20T08:25:12.000Z"}],
  "state":{"status":"fresh","stale":false},
  "quotaSemantics":{"status":"known","effectiveAvailability":[
    {"scope":"all_models","status":"known","effectivePercentRemaining":$2,
     "boundedBy":["five_hour","seven_day"],"limitingWindowIds":["seven_day"],
     "runway":{"status":"projected_exhaustion","usableRunwaySeconds":298906,
               "projectedExhaustedAt":"2026-03-19T03:43:45.600Z",
               "limitingWindowId":"seven_day","projectionConfidence":"established"}}]}}]}
JSON
STUB
	chmod +x "$1/quota-axi"
}
fuel_stub "$stubbed" 64

full="$(PATH="$stubbed" "$STATUS" 2>&1)"
expect "thurbox's own word for the session survives the trip" "uncovered" "$full"
refute "and is not flattened to idle" "idle" "$full"
refute "nor to unknown" "unknown" "$full"

# --- 5. it reports the artifact, with its checks -----------------------------

expect "an open PR is reported against the task that owns the branch" \
	"Thurbeen/fleet#13" "$full"
expect "with its check status, not just its existence" "passing" "$full"

# --- 6. fuel is measured, never projected ------------------------------------
#
# quota-axi hands back measurements AND forecasts in one document. The
# operator's standing rule forbids fleet from carrying a forecast, so the
# second half of that document must not reach the screen — and `resetsAt`, the
# fact that says when a spent window comes back, must.

expect "the binding window's headroom is the reading" "64% remaining" "$full"
expect "the reserve is on the same line, so the rule is checkable" "reserve 20%" "$full"
expect "the window that binds is named" "binding seven_day" "$full"
expect "and every window is printed, since they reset independently" "five_hour" "$full"
expect "including the per-model one" "model:fable" "$full"
expect "each with its own reset" "resets 2026-03-20T17:59:45.600Z" "$full"
refute "quota-axi's projected exhaustion instant does not reach the screen" \
	"2026-03-19T03:43:45.600Z" "$full"
refute "nor its runway in seconds" "298906" "$full"
refute "nor its pace residual, which is a different thing from fleet's reserve" \
	"-8.2" "$full"
refute "nor the burn multiple built on them" "1.295" "$full"

lowfuel="$(sandbox "$tmp/bin-lowfuel" "${BASE_TOOLS[@]}")"
fuel_stub "$lowfuel" 8
low="$(PATH="$lowfuel" "$STATUS" 2>&1)"
expect "under the reserve, the reading is still just the reading" "8% remaining" "$low"
expect "and the floor is named as the thing it is under" "under the 20% reserve" "$low"

# THE CASE THAT BITES, taken from a live run. A rate-limited fetch answers with
# an EMPTY `quota[]` and a `headroom_unknown` row per scope, while the numbers
# survive in `windows[]` from cache. That is a degraded reading, not zero fuel
# and not an error — so the number is reported, and its age is reported with it.

stale="$(sandbox "$tmp/bin-stale" "${BASE_TOOLS[@]}")"
cat >"$stale/quota-axi" <<'STUB'
#!/bin/sh
cat <<'JSON'
{"generatedAt":"2026-09-08T21:29:21.000Z","schemaVersion":5,"providers":[
 {"provider":"claude","plan":"max","source":"cache",
  "windows":[
    {"id":"five_hour","label":"session","kind":"session","percentRemaining":90,
     "resetsAt":"2026-09-09T02:09:59.840656+00:00",
     "pace":{"status":"unknown","reason":"stale"}},
    {"id":"seven_day","label":"week","kind":"weekly","percentRemaining":74,
     "resetsAt":"2026-09-14T23:59:59.840676+00:00",
     "pace":{"status":"unknown","reason":"stale"}},
    {"id":"model:fable","label":"Fable week","kind":"model","percentRemaining":100,
     "resetsAt":"2026-09-15T00:00:00+00:00","pace":{"status":"unknown","reason":"stale"}}],
  "state":{"status":"stale","stale":true,
           "refreshedAt":"2026-09-08T21:28:34.926Z",
           "error":"Claude quota endpoint rate limited retry after 2026-09-08T21:33:47.413Z"},
  "quotaSemantics":{"status":"unknown","effectiveAvailability":[
    {"scope":"all_models","status":"unknown","boundedBy":["five_hour","seven_day"]}]}}]}
JSON
STUB
chmod +x "$stale/quota-axi"
cached="$(PATH="$stale" "$STATUS" 2>&1)"
expect "an empty quota[] still yields a reading, from the cached windows" \
	"74% remaining" "$cached"
refute "and is never rendered as an empty window" "0% remaining" "$cached"
expect "the reading says it is stale" "stale" "$cached"
expect "and how old it is, because the age is part of the fact" \
	"last refreshed 2026-09-08T21:28:34.926Z" "$cached"
expect "and why it could not be refreshed" "rate limited" "$cached"

# A provider with no window at all is the other half: an absent reading is
# reported as absent, in quota-axi's own words, and never as a zero.
mute="$(sandbox "$tmp/bin-mute" "${BASE_TOOLS[@]}")"
cat >"$mute/quota-axi" <<'STUB'
#!/bin/sh
cat <<'JSON'
{"generatedAt":"2026-03-15T16:42:00.000Z","schemaVersion":5,"providers":[
 {"provider":"claude","windows":[],
  "state":{"status":"auth_required","stale":false,"error":"Claude sign-in required",
           "reason":"credentials_missing"},
  "quotaSemantics":{"status":"unknown","effectiveAvailability":[]}}]}
JSON
STUB
chmod +x "$mute/quota-axi"
silent="$(PATH="$mute" "$STATUS" 2>&1)"
expect "a provider with no window says so in quota-axi's words" \
	"unavailable — auth_required; Claude sign-in required" "$silent"
refute "and never invents a zero" "0% remaining" "$silent"

# --- 6b. `--fuel` is the same reading, in one record and at one probe's cost --
#
# The TUI queue pane draws this reading too, and a pane cannot afford `--json`:
# that collects every section, which is a `gh pr list` per repo in flight and a
# `thurbox-cli session list`. So `--fuel` exists — the fuel section alone, one
# `name<TAB>value` line per field, parseable by a reader with no JSON at all.
# What it must NOT be is a second reading: it is `probe_fuel()`'s own dict,
# printed.

reclaim="$(sandbox "$tmp/bin-record" "${BASE_TOOLS[@]}")"
fuel_stub "$reclaim" 64
# Tripwires: a probe this flag is not allowed to spend. They record being run
# and answer nothing, so calling one costs a file rather than a hang.
for tool in gh thurbox-cli; do
	cat >"$reclaim/$tool" <<STUB
#!/bin/sh
: >"$tmp/spent-$tool"
exit 1
STUB
	chmod +x "$reclaim/$tool"
done
rm -f "$tmp/spent-gh" "$tmp/spent-thurbox-cli"

rec="$(PATH="$reclaim" "$STATUS" --fuel 2>&1)"
rc=$?
if [ "$rc" -eq 0 ]; then pass "--fuel exits 0"; else
	fail "--fuel exits 0" "exit $rc${nl}$rec"
fi

if [ -e "$tmp/spent-gh" ] || [ -e "$tmp/spent-thurbox-cli" ]; then
	fail "--fuel probes fuel and nothing else" "it ran a section it was not asked for"
else
	pass "--fuel spends neither gh nor thurbox-cli"
fi

expect "the record carries the reading" "remaining	64" "$rec"
expect "and the reserve, so a reader never spells the number itself" "reserve	20" "$rec"
expect "and the binding window" "limited_by	seven_day" "$rec"
expect "and when it comes back" "resets_at	2026-03-20T17:59:45.600Z" "$rec"
expect "and quota-axi's own word for the reading's freshness" "state	fresh" "$rec"
refute "no projection reaches the record either" "2026-03-19T03:43:45.600Z" "$rec"
refute "nor the runway it was built on" "298906" "$rec"

# `read_at` is EPOCH SECONDS and is the whole reason the pane can say how old a
# cached reading is: a pane has no `os` and cannot parse an instant, so the age
# has to be subtractable where it is drawn.
age="$(PATH="$reclaim" python3 - "$rec" <<'PY' 2>&1
import sys, time
fields = dict(
    line.split("\t", 1) for line in sys.argv[1].splitlines() if "\t" in line
)
read_at = int(fields["read_at"])
assert abs(time.time() - read_at) < 300, f"read_at is not now: {read_at}"
print("epoch")
PY
)"
expect "read_at is epoch seconds a pane can subtract" "epoch" "$age"

# The unavailable case is the one a pane gets wrong: it must be a REASON, never
# a zero and never an empty record that reads as 0% left.
mutrec="$(PATH="$mute" "$STATUS" --fuel 2>&1)"
expect "an unreadable reading is a reason, in quota-axi's words" \
	"unavailable	auth_required; Claude sign-in required" "$mutrec"
refute "and carries no invented number" "remaining	" "$mutrec"
expect "while still naming the reserve the reader colours against" "reserve	20" "$mutrec"

# One model, one renderer: the flag prints `probe_fuel()`'s fields, so the
# screen and the pane cannot disagree about what was read.
same="$(PATH="$reclaim" python3 - <<'PY' 2>&1
import json, subprocess
record = subprocess.run(
    ["./scripts/fleet-status.sh", "--fuel"], capture_output=True, text=True
).stdout
fields = dict(line.split("\t", 1) for line in record.splitlines() if "\t" in line)
doc = json.loads(
    subprocess.run(
        ["./scripts/fleet-status.sh", "--fuel", "--json"], capture_output=True, text=True
    ).stdout
)
assert str(doc["remaining"]) == fields["remaining"], "record and json disagree"
assert str(doc["reserve"]) == fields["reserve"], "reserve disagrees"
print("agree")
PY
)"
expect "the record and --fuel --json are the same reading" "agree" "$same"

# --- 7. it reads, and only reads ---------------------------------------------

snapshot() { find "$FLEET_QUEUE_DIR" "$tmp/rt" -type f -exec sha256sum {} + | sort; }
before="$(snapshot)"
PATH="$stubbed" "$STATUS" >/dev/null 2>&1
PATH="$stubbed" "$STATUS" --json >/dev/null 2>&1
after="$(snapshot)"
if [ "$before" = "$after" ]; then
	pass "neither the queue nor the monitor's runtime state was touched"
else
	fail "the command is read-only" "$(diff <(printf '%s\n' "$before") <(printf '%s\n' "$after"))"
fi

if [ "$failed" -eq 0 ]; then
	echo "fleet-status selftest: all claims hold"
fi
exit "$failed"
