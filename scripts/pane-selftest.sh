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
#   8. EVERY FUEL LABEL SAYS WHAT IT IS. No threshold posing as a reading, no
#      bare age, and a window under the reserve says `low` in words.
#   9. THE HIDE HINT IS A BUTTON. A click on it hides the column, and a pill in
#      the action band is the clickable way back.
#
# And the rule none of that may cost: it still degrades. The pane routinely
# gets thirty columns, so every assertion here runs at 30 as well as at 44.
#
# Usage: scripts/pane-selftest.sh     (also: ./scripts/check.sh pane)
#
# Requires: lua (5.4 or newer, for `utf8.codes`).

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

# The render reads nothing but its fixture today; this keeps it that way, so
# nothing of this machine's or this checkout's operator can reach it later.
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
# shellcheck source=scripts/lib/selftest-env.sh
. scripts/lib/selftest-env.sh
selftest_isolate "$tmp/env"

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

# 27 rather than 26 since the fuel block draws every window: the fixture's
# account holds two, and the detail row that named only the binding one is
# gone, so the block costs one row more for the same reading.
rows="$(grep -c . <<<"$WIDE")"
if [ "$rows" -le 27 ]; then
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

# --- 7. every fuel window, all the time -------------------------------------

# The block drew only the window that binds right now, so on an account with a
# five-hour and a seven-day window the row flipped between the two whenever
# their percentages crossed. It draws every window the reading carries, in the
# record's order (shortest first), and marks the binding one with a theme role
# instead of hiding the others.
echo "pane: every fuel window"

# The row that carries `label`, as its line number in a render ("" if none).
row_of() { grep -n -- "$1" <<<"$2" | head -1 | cut -d: -f1; }

ACCENT="$(render 44 --accent)"
for width in 44 30; do
	out="$WIDE"
	[ "$width" = 30 ] && out="$NARROW"
	short="$(row_of " session " "$out")"
	long="$(row_of " week " "$out")"
	if [ -n "$short" ] && [ -n "$long" ] && [ "$short" -lt "$long" ]; then
		pass "both windows are drawn at $width, shortest first"
	else
		fail "both windows are drawn at $width, shortest first" "$out"
	fi
	expect "the window that does not bind keeps its number at $width" "62%" "$out"
	expect "and the one that binds keeps its own at $width" "18%" "$out"
done
expect "each window says when it comes back" "3h" "$(grep -- " session " <<<"$WIDE")"
expect "on its own clock" "4d" "$(grep -- " week " <<<"$WIDE")"
expect "the binding window is marked with a theme role" "A " "$(grep -- " week " <<<"$ACCENT")"
refute "and the other is not" "A " "$(grep -- " session " <<<"$ACCENT")"

# --- 8. every fuel label says what it is ------------------------------------

# The head row read `⛽ fuel  reserve 20%  1m`. `reserve 20%` is fleet's own
# dispatch floor — a constant, not a reading — and read as "20% fuel left";
# `1m` was the age of the cached reading with nothing saying so. A label a new
# reader has to look up in the source is a label that misleads.
echo "pane: fuel labels"

for width in 44 30; do
	out="$WIDE"
	[ "$width" = 30 ] && out="$NARROW"
	head_row="$(grep -m1 -- "fuel" <<<"$out")"
	refute "the fuel head row does not show the reserve at $width" "reserve" "$head_row"
	expect "and says the numbers are what is left at $width" "fuel left" "$head_row"
	# A reading inside the probe's own TTL is the ordinary case, so its age is
	# not news and costs no columns.
	refute "a fresh reading carries no age at $width" "ago" "$head_row"
	expect "a window under the reserve says so at $width" "low" "$(grep -- " week " <<<"$out")"
	refute "and a window above it does not at $width" "low" "$(grep -- " session " <<<"$out")"
	expect "a reset says it is a reset at $width" "resets 4d" "$(grep -- " week " <<<"$out")"
done

# A reading older than the TTL means the refresh is not happening, and then
# its age is the most important thing on the row — said in words.
STALE_READ="$(render 44 --fuel-read 900)"
expect "an overdue reading says how old it is" "read 15m ago" "$(grep -m1 -- "fuel" <<<"$STALE_READ")"
STALE_NARROW="$(render 30 --fuel-read 900)"
expect "and still says it at 30" "read 15m ago" "$(grep -m1 -- "fuel" <<<"$STALE_NARROW")"

# --- 9. the hide hint is a button, and the pane has a way back --------------

# The pane cannot hold focus, so the only ways to reach it were a chord. The
# hint that names the chord is also a click target now, and — since a closed
# column draws nothing to click — the action band carries a pill that opens it.
echo "pane: the hide button and the pill"

expect "the hide button names the chord" "F3" "$(render 44 --frame | grep '^top_right:')"
expect "and a rebind moves it" "F5" "$(render 44 --frame --chord f5 | grep '^top_right:')"
refute "and it is not spelled a second time in the title" "F3" "$(render 44 --frame | grep '^title:')"
expect "clicking the hint hides the column" "toggled fleetqueue" "$(render 44 --click F3)"
expect "and it still works at 30" "toggled fleetqueue" "$(render 30 --click F3)"
expect "clicking the title does nothing" "nothing toggled" "$(render 44 --click Fleet)"
expect "the pane declares an action-band pill for its toggle" \
	"pill fleetqueue.toggle Fleet" "$(render 44 --pills)"

# --- 10. a queue longer than the pane scrolls, and says where it is ---------

# Forty running tasks in a pane twenty-four rows tall. The head rows — fuel and
# the counters — stay put above the window; the window moves under the wheel,
# the clickable marks and the palette's page actions, and the frame's bottom
# border says which rows are on screen out of how many.
echo "pane: a queue longer than the pane"

# The position on the bottom border, as `<first> <last> <total>`.
position() { sed -n 's/^bottom_right: *\([0-9]*\)-\([0-9]*\) of \([0-9]*\) *$/\1 \2 \3/p' <<<"$1"; }

long() { render 44 --long 40 --height 24 "$@"; }

# THE WHEEL NEVER REACHED MOST OF THIS PANE. The kernel offers a tick to the
# pane whose click target is under the pointer, and records a pane's own rect
# as a target only for a FOCUSABLE pane — which this one is not. So the tick
# landed only over the rows that carried a link. A root node with an identity
# is a target over its whole rect, which is the one thing a text render can
# check about a rule that lives in the kernel.
root="$(long --frame | sed -n 's/^root: //p')"
if [ -n "$root" ]; then
	pass "the pane's root carries an identity, so the wheel has a target over all of it ($root)"
else
	fail "the pane's root carries no identity, so the kernel records no wheel target over it" "$(long --frame)"
fi

read -r first last total <<<"$(position "$(long --frame)")"
if [ "${first:-}" = 1 ] && [ -n "${last:-}" ] && [ "${total:-0}" -gt "$last" ]; then
	pass "a list that does not fit says which rows are on screen ($first-$last of $total)"
else
	fail "a list that does not fit says which rows are on screen" "$(long --frame)"
fi
expect "and marks that more is below" "↓" "$(long)"
refute "and nothing above, at the top" "↑" "$(long)"
refute "a list that fits says no position" "of" "$(render 44 --frame | grep '^bottom_right:')"

read -r wfirst wlast _ <<<"$(position "$(long --frame --wheel 2)")"
if [ "${wfirst:-0}" -gt 1 ] && [ "${wlast:-0}" -gt "${last:-0}" ]; then
	pass "the wheel moves the window, and the position follows ($wfirst-$wlast)"
else
	fail "the wheel moves the window, and the position follows" "$(long --frame --wheel 2)"
fi
expect "and a mark says what is above" "↑" "$(long --wheel 2)"

read -r bfirst blast btotal <<<"$(position "$(long --frame --wheel 999)")"
if [ -n "${blast:-}" ] && [ "$blast" = "$btotal" ]; then
	pass "it clamps at the bottom ($bfirst-$blast of $btotal)"
else
	fail "it clamps at the bottom" "$(long --frame --wheel 999)"
fi
refute "and says nothing is below there" "↓" "$(long --wheel 999)"
read -r ufirst ulast _ <<<"$(position "$(long --frame --wheel 999 --wheel -1)")"
if [ -n "${ulast:-}" ] && [ "$ulast" -lt "$btotal" ]; then
	pass "one tick up from the bottom moves at once, not after winding back ($ufirst-$ulast)"
else
	fail "one tick up from the bottom moves at once" "$(long --frame --wheel 999 --wheel -1)"
fi
read -r tfirst _ _ <<<"$(position "$(long --frame --wheel 3 --wheel -999)")"
if [ "${tfirst:-}" = 1 ]; then
	pass "it clamps at the top"
else
	fail "it clamps at the top" "$(long --frame --wheel 3 --wheel -999)"
fi

head_rows="$(long --wheel 999 | head -2)"
expect "the fuel block stays above the window when scrolled" "fuel left" "$head_rows"
expect "and so do the counters" "running" "$(long --wheel 999 | sed -n '1,/^─/!p' | head -2)"

read -r cfirst _ _ <<<"$(position "$(long --frame --click below)")"
if [ "${cfirst:-0}" -gt 1 ]; then
	pass "clicking the mark below pages down ($cfirst)"
else
	fail "clicking the mark below pages down" "$(long --frame --click below)"
fi
read -r cufirst _ _ <<<"$(position "$(long --frame --wheel 999 --click above)")"
if [ -n "${cufirst:-}" ] && [ "$cufirst" -lt "${bfirst:-0}" ]; then
	pass "clicking the mark above pages up ($cufirst)"
else
	fail "clicking the mark above pages up" "$(long --frame --wheel 999 --click above)"
fi
read -r pfirst _ _ <<<"$(position "$(long --frame --action fleetqueue.page_down)")"
read -r pufirst _ _ <<<"$(position "$(long --frame --wheel 999 --action fleetqueue.page_up)")"
if [ "${pfirst:-0}" -gt 1 ] && [ -n "${pufirst:-}" ] && [ "$pufirst" -lt "${bfirst:-0}" ]; then
	pass "the palette's page actions move it without the wheel"
else
	fail "the palette's page actions move it without the wheel" \
		"down: $(long --frame --action fleetqueue.page_down)${nl}up: $(long --frame --wheel 999 --action fleetqueue.page_up)"
fi
widest="$(long --wheel 5 | wc -L)"
if [ "$widest" -le 44 ]; then
	pass "a scrolled render still fits its column"
else
	fail "a scrolled render is $widest columns wide" "$(long --wheel 5)"
fi

# --- 6. the lead it probes is this machine's --------------------------------

# A lead mirrored from another host is listed FIRST and holds an empty queue.
# Binding to it drew "the queue is empty" over a queue with live work in it.
# One remote lead beside one local one is the normal setup, so the pane binds
# to the local one and says nothing about the other.
echo "pane: a remote lead listed before the local one"

REMOTE_FIRST="$(render 44 --leads remote-first)"
expect "the local queue is drawn" "Cut the pane back" "$REMOTE_FIRST"
refute "the remote lead's empty queue is not" "the queue is empty" "$REMOTE_FIRST"
refute "and no warning is drawn about the remote lead" "Mission Control" "$REMOTE_FIRST"

# Two leads on this machine are two checkouts, and which queue is the real one
# is the operator's to say — picking the first would be the same silent guess.
echo "pane: two local leads"

TWO_LOCAL="$(render 44 --leads two-local)"
expect "two local leads are named as a problem" "2 Mission Control sessions here" "$TWO_LOCAL"
refute "and neither queue is drawn as if it were the one" "Cut the pane back" "$TWO_LOCAL"

exit "$failed"
