#!/usr/bin/env bash
# Prove that the TUI queue pane is a GLANCE.
#
# The pane's failure mode is not a crash, it is a wall: it draws every fact it
# holds at equal weight, the operator reads "custom pane displays too many
# elements, it is not clear what we are really working on", and no check in
# this repo has anything to say about it. `check.sh pane`'s greps hold the
# pane's WIRING together — the slot, the chord, the fuel source — and none of
# them can see a row.
#
# So this renders the pane, with `scripts/lib/pane_harness.lua`, against a
# queue built to be the reported screen: a RUNNING topic that also holds a task
# the forge already merged, a blocker chain with a long-cleared edge in it,
# tasks nothing has emitted an event for, and a settled topic underneath. Then
# it asserts the four rules the redesign is:
#
#   1. NO ROW WITH NOTHING ON IT. A cleared blocker, a count of zero, a marker
#      that says a brief exists under a task `dispatch` refuses to send without
#      one — each was drawn under most tasks and said nothing about any of them.
#   2. ONE ROW PER TASK, plus a row only when there is something to add. The id
#      row was a kebab-case restatement of the title beside it.
#   3. FINISHED WORK WEIGHS LESS. A `landed` task drawn at full size inside the
#      RUNNING section is finished work competing with running work.
#   4. THE TWO TALLIES AGREE. The counter row counts TASKS by state; a section
#      heading covers TOPICS. They were both drawn as bare numbers, and
#      `4 running` above `RUNNING 5` is the pane contradicting itself.
#   5b. THE TWO KINDS OF WAIT DO NOT LOOK ALIKE. A blocker naming a task ends
#      when that task lands; a blocker naming a CONDITION outside the queue
#      ends only when somebody runs `block --clear`. Drawn identically, the
#      second reads as "wait", and the operator who is the only actor waits.
#   5. THE PUBLISH ROW SAYS THE NEXT MOVE, AT BOTH WIDTHS. A state word is a
#      fact about the pull request; what the operator does about it is a
#      second thing, and the row's `note` is where it lives. The note was the
#      FIRST thing the width ladder dropped, so it reached neither width this
#      file renders while `attested` — one word under every task in the
#      queue — reached both. These assert the order that fixes it.
#
# And the rule none of that may cost: it still degrades. The pane routinely
# gets thirty columns, so every assertion here runs at 30 as well as at 44.
#
# Usage: scripts/pane-selftest.sh     (also: ./scripts/check.sh pane)
#
# Requires: lua (5.4 or newer, for `utf8.codes`).

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

HARNESS="scripts/lib/pane_harness.lua"
nl=$'\n'
failed=0

pass() { printf '  \033[32mok\033[0m    %s\n' "$1"; }

fail() {
	printf '  \033[31mFAIL\033[0m  %s\n' "$1" >&2
	if [ $# -gt 1 ]; then printf '%s\n' "$2" | sed 's/^/          /' >&2; fi
	failed=1
}

# Herestrings rather than pipelines: this file runs under `set -o pipefail`,
# where a pipeline reports the pipeline's status and not the reader's.
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

refute_line() {
	local label="$1" unwanted="$2" out="$3"
	if grep -qxF -- "$unwanted" <<<"$out"; then
		fail "$label" "did not want a row that is exactly: [$unwanted]${nl}--- got ---${nl}$out"
	else
		pass "$label"
	fi
}

command -v lua >/dev/null || {
	echo "error: lua not found" >&2
	exit 2
}

render() {
	local width="$1"
	shift
	lua "$HARNESS" "$width" "$@"
}

WIDE="$(render 44)" || {
	echo "error: the pane did not render at 44 columns" >&2
	exit 2
}
NARROW="$(render 30)" || {
	echo "error: the pane did not render at 30 columns" >&2
	exit 2
}
MARKS="$(render 44 --marks)"

echo "pane: 44 columns"

# --- 1. no row that carries no information ----------------------------------

refute "no cleared blocker is drawn" "✓ 01-declare-publish-method" "$WIDE"
refute "no event count is drawn" "events" "$WIDE"
refute_line "no row says only that a brief exists" "   brief" "$WIDE"
# An outcome the state already carries: `shipped` under a task drawn with the
# `done` glyph is the same fact twice.
refute "no outcome the state already says" "shipped" "$WIDE"

# --- what still has to be there ---------------------------------------------

expect "a blocker that still holds is loud" "↳ 02-shepherd-records-publish" "$WIDE"

# THE CONDITION FORM, which is a different wait and has to look like one. `↳`
# is a wait with an end — the named task lands and this moves. `⊘` has no such
# event: only `queue.sh block --clear` releases it, so the operator reading the
# row is the actor. A pane that drew the two the same would be telling them to
# wait for nobody.
expect "a condition outside the queue is drawn" "⊘ az login: for the tenant" "$WIDE"
expect "and its kind says which sort of condition" "credential" "$WIDE"
refute "a condition is not drawn as a wait on a task" "↳ az login" "$WIDE"
# The condition arrives as the raw YAML scalar, and free prose is routinely
# quoted on disk. Those quotes belong to the writer; drawing them puts
# punctuation the operator never typed into the one row they have to act on.
refute "the writer's YAML quoting is not drawn" "⊘ 'az login" "$WIDE"
expect "a missing brief is still said out loud" "no brief" "$WIDE"
expect "a result nothing has collected is still said" "uncollected" "$WIDE"
expect "the pull request is still named" "#47" "$WIDE"
expect "the publish verdict is still drawn" "green" "$WIDE"

# --- 5. the publish row says the next move ----------------------------------

# `open` is the state EVERY task passes through — `collect` proved the pull
# request exists and nothing has looked at its checks yet — and it is drawn
# muted, because a fact is not a verdict. Muted beside a collapsed `n landed`
# row reads as settled, which is the opposite of what it means, so the row says
# the move in words instead of borrowing a colour that would claim one.
expect "an open pull request says what to do about it" "open — review" "$WIDE"
expect "and a GitLab merge request is named in GitLab's own notation" "!52" "$WIDE"
expect "a green one still says whose merge it is" "green — yours to merge" "$WIDE"

# The order the ladder gives things up in, pinned: the METHOD is provenance and
# the NOTE is the action, so a row too narrow for both keeps the action. Before
# this, `attested · #47 · green` is what 44 columns drew and the note was
# drawn at no width at all.
refute "the method gives way to the note, not the reverse" "attested · #47" "$WIDE"

# A blocker recorded against a task that is NOT waiting holds nothing —
# `queue.py` clears a `queued` task and no other — so drawing it would explain
# why a task is stuck about a task that is running.
refute "no blocker under a task that is running" "publish-agnostic/03-draw" "$WIDE"

# --- 2. one row per task ----------------------------------------------------

expect "the task's number survives" "01 Cut the pane back" "$WIDE"
refute "the id row is gone" "01-declutter-the-pane" "$WIDE"

rows="$(grep -c . <<<"$WIDE")"
if [ "$rows" -le 26 ]; then
	pass "the whole queue fits in $rows rows"
else
	fail "the whole queue costs $rows rows" "$WIDE"
fi

# --- 3. finished work weighs less -------------------------------------------

refute "a landed task inside a running topic is collapsed" \
	"Declare the publish method on the task" "$WIDE"
expect "and is still counted where it was" "1 landed" "$WIDE"

expect "running work is bold" "B  ◐ 01 Cut the pane back" "$MARKS"
refute "finished work is not" "B  ● 01 Remove the web monitor" "$MARKS"

# --- 4. the two tallies agree -----------------------------------------------

expect "the counter row counts tasks" "3 running" "$WIDE"
expect "the section heading counts topics, and says so" "3 topics" "$WIDE"

# --- and it still degrades --------------------------------------------------

echo "pane: 30 columns"

widest="$(printf '%s\n' "$NARROW" | wc -L)"
if [ "$widest" -le 30 ]; then
	pass "nothing overflows 30 columns (widest row: $widest)"
else
	fail "a row is $widest columns wide in a 30-column pane" "$NARROW"
fi

expect "the running work is still named at 30" "Cut the pane back" "$NARROW"
expect "the blocker that holds is still there at 30" "↳ 02-shepherd" "$NARROW"
expect "and so is the condition, with its own mark" "⊘ az login" "$NARROW"
refute "and still no cleared blocker" "✓ 01-declare" "$NARROW"
# The width the pane routinely gets, which is the whole reason the note moved
# up the ladder: a next move drawn only at 44 is a next move nobody reads.
expect "the next move survives 30 columns" "open — review" "$NARROW"

exit "$failed"
