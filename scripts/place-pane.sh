#!/usr/bin/env bash
# Put the fleet queue pane's column into the operator's thurbox layout.lua —
# on their word, with a backup, and refusing rather than guessing.
#
# WHAT CHANGED AND WHY. Installing the pane and SEEING the pane are two
# different things: `thurbox-cli plugin install` succeeds, `plugin list` shows
# it, and the pane draws nothing until an arrangement places its slot. For a
# long time this repo printed the block and stopped there, on the grounds that
# `layout.lua` is the operator's file and a mistake in it takes the whole
# interface. Both halves of that are still true — and the result was operators
# finishing onboarding with an invisible pane, which is the failure with no
# symptom the fleet-pane skill is mostly about. So the block is still never
# applied unasked: onboarding ASKS, and this is what runs when the answer is
# yes.
#
# WHAT KEEPS IT SAFE, in the order it matters:
#
#   1. It refuses what it cannot read. No `columns` list it recognises, no
#      edit — it prints the block and exits non-zero.
#   2. It is idempotent. A layout that already names the slot is left exactly
#      as it is, whatever else the operator has done to it.
#   3. It backs up first, to layout.lua.bak-<timestamp> beside the original.
#   4. It re-reads the result with `lua` and RESTORES the backup if the file
#      no longer parses, so a bad edit cannot survive this script.
#   5. It verifies with `thurbox-cli plugin check`, the one command that can
#      tell a placed pane from a loaded one.
#
# THE BLOCK IT WRITES carries the `panels.shown` guard and not only the slot.
# Without the guard the column is carved on every frame, so the pane's F-key
# flips a state nothing reads and the column opens and never closes — which
# `plugin check` cannot catch, because the pane DOES draw.
#
# THE SLOT IS READ FROM THE PANE, never spelled here: interface/fleet_queue.lua
# declares it, and a second copy in this file is a rename waiting to place a
# slot nothing fills.
#
# Usage:
#   scripts/place-pane.sh              # place it to the RIGHT of the terminal
#   scripts/place-pane.sh --left       # between the session list and the terminal
#   scripts/place-pane.sh --dry-run    # print the file, the anchor and the block
#   scripts/place-pane.sh --check      # is it placed? changes nothing
#   scripts/place-pane.sh --layout P   # a layout.lua somewhere else
#
# Exit: 0 placed (or already placed), 1 not placed (--check), 2 usage or no
# layout file, 3 refused — the layout has no `columns` list this recognises.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

PANE="interface/fleet_queue.lua"
SIDE="right"
DRY=0
CHECK=0
LAYOUT=""

while [ $# -gt 0 ]; do
	case "$1" in
	--left) SIDE="left" ;;
	--right) SIDE="right" ;;
	--dry-run) DRY=1 ;;
	--check) CHECK=1 ;;
	--layout)
		LAYOUT="${2:-}"
		shift
		;;
	-h | --help)
		sed -n '2,47p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
		exit 0
		;;
	*)
		printf 'usage: %s [--left|--right] [--dry-run|--check] [--layout PATH]\n' "$0" >&2
		exit 2
		;;
	esac
	shift
done

die() {
	printf 'error: %s\n' "$1" >&2
	exit "${2:-2}"
}

# --- the slot, from the pane that declares it ---------------------------------

[ -f "$PANE" ] || die "$PANE is missing; there is no pane to place"
SLOT="$(sed -n 's/^local SLOT = "\(.*\)"$/\1/p' "$PANE" | head -1)"
[ -n "$SLOT" ] || die "could not read the slot name from $PANE"

# The column's share and its floor. A queue column narrower than this draws
# task titles one word wide; the pane degrades to 30 and no further.
PCT=30
MIN=34

block() {
	local indent="$1"
	cat <<-EOF
		${indent}-- fleet's queue pane. Guarded like the session column: panels.shown is
		${indent}-- what the pane's F-key toggles, and without it the column is carved
		${indent}-- every frame, so the key flips a state nothing reads and the pane
		${indent}-- opens and never closes. Added by fleet's scripts/place-pane.sh.
		${indent}if panels.shown("$SLOT") and filled(ctx, "$SLOT") then
		${indent}  columns[#columns + 1] = { slot = "$SLOT", pct = $PCT, min = $MIN }
		${indent}end
	EOF
}

# --- the layout file ----------------------------------------------------------

if [ -z "$LAYOUT" ]; then
	command -v thurbox-cli >/dev/null 2>&1 ||
		die "thurbox-cli not found, so the interface directory is unknown; pass --layout PATH"
	ui_dir="$(thurbox-cli plugin dir --text 2>/dev/null | head -1)"
	[ -n "$ui_dir" ] || die "thurbox-cli could not name its interface directory"
	LAYOUT="$ui_dir/layout.lua"
fi

if [ ! -f "$LAYOUT" ]; then
	die "no layout.lua at $LAYOUT — thurbox writes one on first run, so start it once first"
fi

# --- already placed? ----------------------------------------------------------
#
# Any mention of the slot counts, and deliberately so: the operator may have
# placed it differently, wider, or on the other side. Owning their arrangement
# means not correcting it.

if grep -q "slot = \"$SLOT\"" "$LAYOUT"; then
	if [ "$CHECK" -eq 1 ] || [ "$DRY" -eq 1 ]; then
		printf 'placed: %s already carves a column for slot "%s"\n' "$LAYOUT" "$SLOT"
	else
		printf 'Already placed — %s carves a column for slot "%s", and nothing was changed.\n' \
			"$LAYOUT" "$SLOT"
	fi
	exit 0
fi

if [ "$CHECK" -eq 1 ]; then
	printf 'not placed: nothing in %s carves a column for slot "%s", so the pane\n' "$LAYOUT" "$SLOT"
	printf 'loads, lists, and draws nothing.\n'
	exit 1
fi

# --- the anchor ---------------------------------------------------------------
#
# The centre pane is the one column every stock layout has and the one that is
# never dropped, so it is what "right of the terminal" and "left of it" are
# measured against. Nothing else here parses Lua: a layout that has been
# rearranged past recognition is one this refuses rather than rewrites.

anchor="$(grep -n 'columns\[#columns + 1\] = { slot = "center" }' "$LAYOUT" | head -1 | cut -d: -f1)"
if [ -z "$anchor" ]; then
	printf 'Refusing to edit %s: it has no line placing the "center" slot in a\n' "$LAYOUT" >&2
	printf 'columns list, which is the only shape this recognises. Your arrangement is\n' >&2
	printf 'yours — add this block beside the other side columns yourself:\n\n' >&2
	block "  " >&2
	exit 3
fi

indent="$(sed -n "${anchor}p" "$LAYOUT" | sed 's/[^[:space:]].*//')"
if [ "$SIDE" = "right" ]; then
	at="$anchor"      # after the centre column
	where="right of the terminal"
else
	at=$((anchor - 1)) # before it
	where="left of the terminal, beside the session list"
fi

if [ "$DRY" -eq 1 ]; then
	printf 'would place the queue pane %s\n\n  file:   %s\n  line:   after line %s\n\n' \
		"$where" "$LAYOUT" "$at"
	block "$indent"
	exit 0
fi

# --- the edit -----------------------------------------------------------------

backup="$LAYOUT.bak-$(date +%Y%m%d%H%M%S)"
cp "$LAYOUT" "$backup" || die "could not back up $LAYOUT"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
{
	[ "$at" -gt 0 ] && sed -n "1,${at}p" "$LAYOUT"
	block "$indent"
	sed -n "$((at + 1)),\$p" "$LAYOUT"
} >"$tmp"

cat "$tmp" >"$LAYOUT"

# A Lua file that no longer parses is a black screen at the next launch, and it
# would be THIS script's doing — so the result is read back before anyone lives
# with it, and the backup goes straight back if it does not load.
if command -v lua >/dev/null 2>&1; then
	if ! lua -e "assert(loadfile('$LAYOUT'))" >/dev/null 2>&1; then
		cp "$backup" "$LAYOUT"
		die "the edited layout no longer parses as Lua; restored $backup" 3
	fi
fi

printf 'Placed the queue pane %s.\n\n  file:   %s\n  backup: %s\n\n' "$where" "$LAYOUT" "$backup"

# The verdict that matters, from the only thing that can give it: a pane that
# loads is not a pane that is placed, and `plugin check` is what tells them
# apart. Its failure is reported, never swallowed — but the edit stands, since
# a check that fails for an unrelated pane is not a reason to undo this one.
if command -v thurbox-cli >/dev/null 2>&1; then
	if report="$(thurbox-cli plugin check --text 2>&1)"; then
		printf '%s\n\nthurbox-cli plugin check is green. Press F3 in thurbox.\n' "$report"
	else
		printf '%s\n\n' "$report" >&2
		printf 'thurbox-cli plugin check is not green. The block is in, so read\n' >&2
		printf 'what it says above: it names the pane it is unhappy about, which\n' >&2
		printf 'may not be this one.\n' >&2
	fi
fi

exit 0
