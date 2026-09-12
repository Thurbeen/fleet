#!/usr/bin/env bash
# Ask the operator whether to put the queue pane on their screen — once, ever.
#
# WHY THIS EXISTS. `install.sh` and `scripts/install-extension.sh` install the
# pane plugin and never place it: placing it is an edit to the operator's own
# layout.lua, and that edit waits for a yes. Something has to ask for that yes,
# or the operator finishes setup with a pane that loads and draws nothing. The
# first Mission Control session is that something, and this is what it runs.
#
# WHY A SCRIPT AND NOT A HOOK. A `SessionStart` hook would ask only on the one
# agent that has that hook. FLEET.md is the lead's own standing context under
# every agent, and it tells the lead to run this and ask whatever it says to
# ask — with its CLI's question tool or in plain words.
#
# WHY IT ASKS ONCE, EVER. The answer lives in `orchestration/first-run/pane`,
# gitignored beside the rest of fleet's runtime state, and not in the lead's
# conversation: that session is disposable and a restart would ask again. A
# layout that already places the pane — by `place-pane.sh`, by onboarding, by
# hand — is never asked about, and is recorded as `placed`.
#
# IT NEVER PLACES WITHOUT A YES. `yes` is the only verb that edits anything,
# and it does so through `scripts/place-pane.sh`, whose header owns what makes
# that edit safe.
#
# Usage:
#   scripts/pane-ask.sh              # `ask`, or `skip: <why>`
#   scripts/pane-ask.sh yes          # they said yes: place it right of the terminal
#   scripts/pane-ask.sh yes --left   # ... between the session list and the terminal
#   scripts/pane-ask.sh no           # they said no: remember it
#
# The first word of the output is the answer. Exit: 0, or 2 on a usage error;
# `yes` exits with place-pane.sh's own code when that could not place it.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

STATE_DIR="orchestration/first-run"
STATE="$STATE_DIR/pane"

record() {
	mkdir -p "$STATE_DIR" && printf '%s %s\n' "$1" "$(date +%Y-%m-%d)" >"$STATE"
}

case "${1:-}" in
"")
	if [ -f "$STATE" ]; then
		read -r answer when <"$STATE"
		printf 'skip: already answered (%s, %s)\n' "${answer:-?}" "${when:-?}"
		exit 0
	fi

	report="$(./scripts/place-pane.sh --check 2>&1)"
	case $? in
	0)
		record placed
		printf 'skip: already on screen — %s\n' "$report"
		;;
	1)
		cat <<-'EOF'
			ask: the queue pane is installed and not on screen, and nobody has asked.
			Ask the operator once — put the queue pane on your screen?
			  right of the terminal (recommended)   ./scripts/pane-ask.sh yes
			  left of the terminal                  ./scripts/pane-ask.sh yes --left
			  not now                               ./scripts/pane-ask.sh no
			Run the line for their answer. Never place it without a yes.
		EOF
		;;
	*)
		# No thurbox to name the layout, or no layout yet. Nothing could be
		# placed on a yes, so nothing is asked and nothing recorded — a later
		# session, inside thurbox, asks.
		printf 'skip: cannot tell whether the pane is placed — %s\n' "$(printf '%s\n' "$report" | head -1)"
		;;
	esac
	exit 0
	;;
yes)
	side=()
	case "${2:-}" in
	"") ;;
	--left | --right) side=("$2") ;;
	*)
		printf 'usage: %s yes [--left|--right]\n' "$0" >&2
		exit 2
		;;
	esac
	./scripts/place-pane.sh "${side[@]}"
	code=$?
	case $code in
	0) record yes ;;
	3)
		# Refused: the layout is not one place-pane.sh can edit, and it printed
		# the block for the operator to add. The question was still answered.
		record yes
		printf '\nThe answer is recorded; the block above is yours to add.\n'
		;;
	esac
	exit "$code"
	;;
no)
	record no
	printf 'Recorded. Nothing will ask about the pane again.\n'
	printf 'To put it on screen later: ./scripts/place-pane.sh (--left for the other side)\n'
	;;
-h | --help)
	sed -n '2,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
	;;
*)
	printf 'usage: %s [yes [--left|--right] | no]\n' "$0" >&2
	exit 2
	;;
esac
