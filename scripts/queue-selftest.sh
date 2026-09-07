#!/usr/bin/env bash
# Prove the queue's ordering doctrine and its wake model, rather than assert them.
#
# `scripts/queue.sh` makes claims that are easy to write down and easy to get
# backwards. Each one gets a test here, against a throwaway queue in a temp
# directory (`FLEET_QUEUE_DIR`), so a change that quietly inverts one fails the
# gate instead of a run six weeks later:
#
#   1. Independent work dispatches AT ONCE, with no concurrency cap. One
#      `plan` names every ready task and one `dispatch` launches all of them.
#   2. File overlap does NOT serialize. Two ready tasks touching one file are
#      still both ready; the overlap is reported as a risk note beside them.
#   3. A recorded blocker DOES serialize, and clears only when the task it
#      names is genuinely done — not when that task's session merely stopped.
#   4. A turn ending is not a task finishing. `watch` folds transitions into
#      the record and never closes a task; only `collect`, reading a result
#      the worker wrote, does that.
#   5. A blocker with no category and no reason is refused, so "these touch the
#      same file" cannot be spelled as a dependency.
#   6. A task whose BRIEF.md was never written does not go out.
#
# Test 4 is also the wake proof. The event source is `thurbox-cli watch`, which
# this script replaces with a recorded stream through `FLEET_QUEUE_WATCH_CMD` —
# the same override a different transport would use. What matters is the shape:
# the lead READS a stream when it chooses and READS a file the worker wrote.
# Nothing is delivered into its terminal, which is what `message send` does and
# why the queue does not use it.
#
# Usage: scripts/queue-selftest.sh        (also: ./scripts/check.sh queue)
#
# Requires: python3 (with PyYAML) — the same dependency the rest of the gate has.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

QUEUE="./scripts/queue.sh"
nl=$'\n'
failed=0
tmp=""

# Inline rather than a function: shellcheck cannot see that a trap invokes one.
trap '[ -n "$tmp" ] && rm -rf "$tmp"' EXIT

pass() { printf '  \033[32mok\033[0m    %s\n' "$1"; }

fail() {
	printf '  \033[31mFAIL\033[0m  %s\n' "$1" >&2
	if [ $# -gt 1 ]; then printf '%s\n' "$2" | sed 's/^/          /' >&2; fi
	failed=1
}

# Assert a command's combined output contains (or does not contain) a string.
# The whole output goes into the failure message, because a queue report is
# short and the interesting part is always the bit that is missing.
expect() {
	local label="$1" want="$2" out="$3"
	if printf '%s' "$out" | grep -qF -- "$want"; then
		pass "$label"
	else
		fail "$label" "expected to find: $want${nl}--- got ---${nl}$out"
	fi
}

refute() {
	local label="$1" unwanted="$2" out="$3"
	if printf '%s' "$out" | grep -qF -- "$unwanted"; then
		fail "$label" "expected NOT to find: $unwanted${nl}--- got ---${nl}$out"
	else
		pass "$label"
	fi
}

command -v python3 >/dev/null || {
	echo "error: python3 not found" >&2
	exit 2
}

tmp="$(mktemp -d)"
export FLEET_QUEUE_DIR="$tmp/queue"

echo "queue-selftest: $FLEET_QUEUE_DIR"

# --- a topic, and four tasks under it ----------------------------------------
#
# One prompt, two repos, four units of work: the shape the queue exists for.
# `01` and `02` are independent. `03` genuinely depends on `01`. `04` edits the
# same file as `01` and depends on nothing.

if ! topic="$($QUEUE topic add report-status-honestly \
	--title 'Make thurbox report agent status honestly' \
	--prompt 'idle should mean the agent said it is at rest, nothing else' 2>&1)"; then
	fail "topic add" "$topic"
	exit 1
fi
expect "topic add returns a topic id" "report-status-honestly" "$topic"

for spec in \
	"01:drop-idle-default:Stop defaulting an unreported session to idle:/tmp/repo-a:src/state.rs" \
	"02:document-the-states:Document the state vocabulary:/tmp/repo-b:docs/states.md" \
	"03:render-detected-agent:Render detected_agent in the session list:/tmp/repo-a:src/list.rs" \
	"04:log-state-changes:Log every state change:/tmp/repo-a:src/state.rs"; do
	IFS=: read -r n slug title repo touches <<<"$spec"
	if ! out="$($QUEUE add "$topic" "$slug" --title "$title" --repo "$repo" \
		--branch "fix/$slug" --touches "$touches" --number "$n" 2>&1)"; then
		fail "add $slug" "$out"
	fi
done

# --- 5. a blocker must name a category and a reason --------------------------

if out="$($QUEUE block "$topic/03-render-detected-agent" \
	--on "$topic/01-drop-idle-default" 2>&1)"; then
	fail "a blocker with no reason is refused" "$out"
else
	expect "a blocker with no reason is refused" "--kind" "$out"
fi

if out="$($QUEUE block "$topic/04-log-state-changes" --on "$topic/01-drop-idle-default" \
	--kind file-overlap --why 'both edit src/state.rs' 2>&1)"; then
	fail "file overlap cannot be spelled as a blocker kind" "$out"
else
	expect "file overlap cannot be spelled as a blocker kind" "semantic-dependency" "$out"
	expect "and the refusal says where overlap belongs instead" "--touches" "$out"
fi

if ! out="$($QUEUE block "$topic/03-render-detected-agent" --on "$topic/01-drop-idle-default" \
	--kind semantic-dependency \
	--why 'reads the detected_agent field 01 introduces' 2>&1)"; then
	fail "record a real blocker" "$out"
fi

if out="$($QUEUE block "$topic/01-drop-idle-default" --on "$topic/03-render-detected-agent" \
	--kind semantic-dependency --why 'closes a loop' 2>&1)"; then
	fail "a blocker that closes a cycle is refused" "$out"
else
	expect "a blocker that closes a cycle is refused" "cycle" "$out"
fi

# --- 1 and 2. the first plan -------------------------------------------------

plan="$($QUEUE plan 2>&1)"
expect "ready set names 01" "01-drop-idle-default" "$plan"
expect "ready set names 02" "02-document-the-states" "$plan"
expect "ready set names 04 despite the file overlap" "04-log-state-changes" "$plan"
expect "three tasks are ready at once" "ready: 3" "$plan"
expect "01 and 04 are reported as an overlap risk" "src/state.rs" "$plan"
expect "the overlap note refuses to be a reason to wait" "not a reason to wait" "$plan"
expect "03 waits" "waiting: 1" "$plan"
expect "03's blocker is durable and stated" "reads the detected_agent field" "$plan"

ready="$($QUEUE plan --json 2>&1 |
	python3 -c 'import json,sys; print(len(json.load(sys.stdin)["ready"]))')"
if [ "$ready" = 3 ]; then
	pass "plan --json agrees: 3 ready"
else
	fail "plan --json ready count" "got $ready"
fi

# --- 6. an unwritten brief stops the dispatch --------------------------------

if out="$($QUEUE dispatch --dry-run 2>&1)"; then
	fail "a task with an unwritten BRIEF.md does not go out" "$out"
else
	expect "a task with an unwritten BRIEF.md does not go out" "BRIEF.md" "$out"
fi

for t in 01-drop-idle-default 02-document-the-states 03-render-detected-agent \
	04-log-state-changes; do
	brief="$FLEET_QUEUE_DIR/$topic/$t/BRIEF.md"
	python3 - "$brief" <<'PY'
import sys
path = sys.argv[1]
body = open(path).read().replace(
    "<!-- WRITE THE INSTRUCTIONS HERE -->",
    "Make the change, open a pull request, and write the result file below.",
)
open(path, "w").write(body)
PY
done

# --- 1. one dispatch launches all of them ------------------------------------

out="$($QUEUE dispatch --dry-run 2>&1)"
spawns="$(printf '%s\n' "$out" | grep -c 'session create')"
if [ "$spawns" = 3 ]; then
	pass "one dispatch spawns 3 sessions"
else
	fail "one dispatch spawns 3 sessions" "counted $spawns${nl}$out"
fi
refute "dispatch does not spawn the blocked task" "03-render-detected-agent" "$out"
expect "dispatch says the ready set goes out together" "no concurrency cap" "$out"
expect "each worker is pointed at its own brief and nothing else" "BRIEF.md and do what it says" "$out"
expect "every spawn answers the trust dialog before it prompts" "session-trust.sh" "$out"
expect "and the trust step comes before the prompt" \
	"trust dialog first" "$out"

# --- 4. a turn ending is not a task finishing --------------------------------

# The stream is already at seq 100 before either task attaches, so attach can
# prove it seeds the cursor from the high-water mark rather than from 0 or
# from "now".
events="$tmp/events.jsonl"
cat >"$events" <<'EOF'
{"seq":100,"at":1788792150000,"session":"11111111-1111-1111-1111-111111111111","event":"present","from_state":null,"to_state":"working","state":"working","reason":null}
{"seq":100,"at":1788792150000,"session":"22222222-2222-2222-2222-222222222222","event":"present","from_state":null,"to_state":"working","state":"working","reason":null}
EOF
export FLEET_QUEUE_WATCH_CMD="cat $events"

$QUEUE attach "$topic/01-drop-idle-default" 11111111-1111-1111-1111-111111111111 >/dev/null
$QUEUE attach "$topic/02-document-the-states" 22222222-2222-2222-2222-222222222222 >/dev/null

cursor="$(cat "$FLEET_QUEUE_DIR/.cursor" 2>/dev/null || echo '<missing>')"
if [ "$cursor" = 100 ]; then
	pass "attach seeds the cursor from the stream's high-water mark"
else
	fail "attach seeds the cursor from the stream's high-water mark" "cursor: $cursor"
fi

cat >>"$events" <<'EOF'
{"seq":101,"at":1788792159766,"session":"11111111-1111-1111-1111-111111111111","event":"state","from_state":null,"to_state":"working","state":"working","reason":"hook"}
{"seq":102,"at":1788792160000,"session":"22222222-2222-2222-2222-222222222222","event":"state","from_state":null,"to_state":"working","state":"working","reason":"hook"}
{"seq":103,"at":1788792199000,"session":"11111111-1111-1111-1111-111111111111","event":"state","from_state":"working","to_state":"done","state":"done","reason":"hook"}
EOF

out="$($QUEUE watch --for-secs 1 2>&1)"
expect "watch folds transitions into the record" "01-drop-idle-default" "$out"
expect "watch sees the stream's sequence numbers" "seq 101" "$out"
expect "watch reports the turn that ended with no result" "no result" "$out"
refute "watch never closes a task" "done: " "$out"

state="$($QUEUE show "$topic/01-drop-idle-default" 2>&1 | grep -F 'state:')"
expect "the task whose turn ended is still dispatched" "dispatched" "$state"

progress="$FLEET_QUEUE_DIR/$topic/01-drop-idle-default/progress.jsonl"
if [ -s "$progress" ]; then
	pass "progress.jsonl records what happened when"
else
	fail "progress.jsonl records what happened when" "empty or missing: $progress"
fi

out="$($QUEUE watch --for-secs 1 2>&1)"
refute "the cursor resumes, so a second watch replays nothing" "seq 101" "$out"

# --- 7. a genuinely zero cursor is not treated as "no cursor" ----------------
#
# seed_cursor legitimately writes 0 when the stream's high-water mark really
# is 0 (a brand-new thurbox instance). read_cursor must tell that apart from
# "no cursor file at all": cmd_watch used to test the cursor for truthiness,
# so a real 0 was silently treated the same as "no cursor" and the --since
# flag was dropped from the real `thurbox-cli watch` call, starting the first
# watch after a dispatch from "now" instead of replaying from seq 0 — quietly
# losing any transition in between. This needs the real command path (not
# FLEET_QUEUE_WATCH_CMD, which replaces the whole command and never sees the
# flags), so it stubs `thurbox-cli` on PATH and reads what it was called with.
zerotmp="$(mktemp -d)"
fakebin="$zerotmp/bin"
mkdir -p "$fakebin"
cliargs="$zerotmp/cli-args.log"
cat >"$fakebin/thurbox-cli" <<SH
#!/bin/sh
echo "\$@" >>"$cliargs"
exit 0
SH
chmod +x "$fakebin/thurbox-cli"

(
	export PATH="$fakebin:$PATH"
	unset FLEET_QUEUE_WATCH_CMD
	zt="$(FLEET_QUEUE_DIR="$zerotmp/queue" $QUEUE topic add zero-cursor \
		--prompt 'prove a real zero cursor is not dropped')" || exit 1
	FLEET_QUEUE_DIR="$zerotmp/queue" $QUEUE add "$zt" only-task --title 'only task' \
		--repo /tmp/repo-z --branch fix/only-task --number 01 >/dev/null
	FLEET_QUEUE_DIR="$zerotmp/queue" $QUEUE attach "$zt/01-only-task" \
		33333333-3333-3333-3333-333333333333 >/dev/null
	FLEET_QUEUE_DIR="$zerotmp/queue" $QUEUE watch --for-secs 0 >/dev/null 2>&1
)

cursor="$(cat "$zerotmp/queue/.cursor" 2>/dev/null || echo '<missing>')"
if [ "$cursor" = 0 ]; then
	pass "seed_cursor writes a genuine zero when the stream's high-water mark is 0"
else
	fail "seed_cursor writes a genuine zero when the stream's high-water mark is 0" "cursor: $cursor"
fi

expect "watch passes --since 0 to the real stream rather than dropping it" \
	"--since 0" "$(cat "$cliargs" 2>/dev/null)"

rm -rf "$zerotmp"

# --- 3. the blocker clears only when the task it names is really done --------

out="$($QUEUE collect 2>&1)"
expect "collect finds nothing to conclude yet" "0 result" "$out"

plan="$($QUEUE plan 2>&1)"
expect "03 still waits after its blocker's turn ended" "waiting: 1" "$plan"

cat >"$FLEET_QUEUE_DIR/$topic/01-drop-idle-default/result.md" <<'EOF'
---
outcome: shipped
artifact: https://github.com/Thurbeen/thurbox/pull/999
---
Dropped the idle default; an unreported session now reads `unreported`.
EOF

out="$($QUEUE collect 2>&1)"
expect "collect reads the worker's own conclusion" "shipped" "$out"
expect "collect names the artifact" "pull/999" "$out"

plan="$($QUEUE plan 2>&1)"
expect "03 becomes ready once 01 is genuinely done" "03-render-detected-agent" "$plan"
expect "and it joins 04, which never waited" "ready: 2" "$plan"
refute "01 has left the plan entirely" "01-drop-idle-default" "$plan"

# --- the record carries the topic view without any new field -----------------

out="$($QUEUE list 2>&1)"
expect "list groups by topic" "$topic" "$out"
expect "list is one line per task, not a brief" "04-log-state-changes" "$out"

out="$($QUEUE check 2>&1)"
expect "check validates every record" "ok" "$out"

echo
if [ "$failed" -eq 0 ]; then
	printf '\033[32mqueue-selftest: every claim holds\033[0m\n'
else
	printf '\033[31mqueue-selftest: failed\033[0m\n' >&2
fi
exit "$failed"
