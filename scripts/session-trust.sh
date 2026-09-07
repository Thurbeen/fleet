#!/usr/bin/env bash
# Get a freshly created session past its agent's trust dialog, without touching
# anything the operator owns.
#
# THE BUG. thurbox mints a FRESH worktree path per session, and most agents ask
# whether they may work in a directory they have not seen before. So every
# worker fleet spawned sat on that dialog: the session existed, the pane was
# live, the agent had not started. `session send` then typed the brief INTO the
# dialog. Sessions fleet created were broken, every time.
#
# WHY A KEYSTROKE AND NOT A CONFIG EDIT. `scripts/trust-thurbox-dir.sh` can
# seed Claude Code's trust into ~/.claude.json and it still works — it is the
# right fallback when a dialog cannot be answered. It is the wrong DEFAULT: it
# writes to a file the user owns, for a tool fleet did not install, and it
# needs a different file format for every agent. Answering the prompt touches
# nothing that outlives the session, and it is one mechanism for the agents
# whose gate is a prompt at all.
#
# THE FAILURE MODE THIS IS BUILT AROUND: sending a key blindly. If the dialog
# is not there, the key lands in a live agent's composer — noise at best, a
# stray instruction at worst. So this script never sends unless it can SEE the
# dialog, and never reports success unless it can see the dialog is gone.
#
#     confirm  the pane shows this agent's trust dialog
#     answer   the keys that ACCEPT it, which are not the same per agent
#     confirm  the dialog is gone and the agent is up
#
# and if either confirmation fails it sends nothing and says so. A session
# waiting on a dialog is visible and fixable; a session that has been typed
# into randomly is neither.
#
# It is safe to run only in the window between `session create` and the first
# `session send`, which is when `scripts/queue.sh dispatch` runs it: nothing
# has been typed into that pane yet, so there is no composer content to
# corrupt. Do not run it against a session that is already working.
#
# PER-AGENT, and the differences are real (see the table in the code):
#
#   claude          a dialog whose default selection is "No, exit". A bare
#                   Enter DISMISSES it. Down, then Enter.
#   codex           a dialog; Enter accepts. Persists per repo root, so later
#                   worktrees of the same project never show it.
#   pi, pi-signed   a dialog; Enter accepts. Persists per path.
#   grok, kimi      no dialog in a git worktree. Nothing to do.
#   cursor, muse    NOT a keystroke — a launch flag (`--trust`, `--yolo`).
#                   This script refuses them and says where the flag goes:
#                   a profile in orchestration/session-profiles.yaml.
#
# Usage:
#   scripts/session-trust.sh <session-uuid-or-name> [--timeout SECS] [--json]
#
# Exit codes, so a caller can decide without parsing prose:
#   0  the pane is ready for a prompt — a dialog was answered, or there was
#      none and the agent is up
#   2  usage, or the session could not be read
#   3  could NOT confirm. Nothing was sent. Do not send a prompt either.
#
# Requires: thurbox-cli, jq.

set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
timeout_secs=20
as_json=0
session=""

usage() {
	awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$here/session-trust.sh"
}

while [ $# -gt 0 ]; do
	case "$1" in
	-h | --help)
		usage
		exit 0
		;;
	--timeout)
		timeout_secs="${2:-20}"
		shift 2
		;;
	--json)
		as_json=1
		shift
		;;
	-*)
		printf 'error: unknown option %q\n' "$1" >&2
		exit 2
		;;
	*)
		session="$1"
		shift
		;;
	esac
done

[ -n "$session" ] || {
	usage
	exit 2
}
command -v thurbox-cli >/dev/null || {
	echo "error: thurbox-cli not found" >&2
	exit 2
}
command -v jq >/dev/null || {
	echo "error: jq is required" >&2
	exit 2
}

say() {
	if [ "$as_json" -eq 1 ]; then
		jq -nc --arg session "$session" --arg agent "$agent" \
			--arg outcome "$2" --arg detail "$1" \
			'{session:$session, agent:$agent, outcome:$outcome, detail:$detail}'
	else
		printf 'session-trust: %s\n' "$1"
	fi
}

# --- who is in the pane ------------------------------------------------------

info="$(thurbox-cli session get "$session" --json 2>/dev/null)" || info=""
[ -n "$info" ] || {
	agent=""
	say "no such session: $session" error
	exit 2
}
uuid="$(jq -r '.id' <<<"$info")"
# `detected_agent` is what is observably running and wins over the row's
# `agent` when they disagree — a session created as a bare shell that a harness
# launched an agent into is exactly that case.
agent="$(jq -r '.detected_agent // .reports_as // .agent // ""' <<<"$info")"

# --- the per-agent table -----------------------------------------------------
#
# `signature` is an extended regex matched case-insensitively against the pane.
# `keys` is the space-separated key sequence that ACCEPTS, in order.
# An empty `signature` means this agent has no dialog to answer.

signature=""
keys=""
flag_only=""

case "$agent" in
claude)
	# Observed live on Claude Code, 2026-09-07, in a fresh thurbox worktree:
	#
	#     Quick safety check: Is this a project you created or one you trust?
	#     ❯ No, exit
	#       Yes, I trust this folder
	#     Enter to confirm · Esc to cancel
	#
	# Matched on the accepting option's own label, which is specific enough that
	# ordinary agent output cannot produce it by accident.
	signature='yes, i trust this folder|quick safety check: is this a project you created'
	# THE TRAP, and it is right there in the capture above: the default
	# selection is "No, exit". A bare Enter DISMISSES the dialog and the agent
	# exits. Move the selection down to the accepting option first. This is the
	# one agent where the obvious answer is the wrong one.
	keys="down enter"
	;;
codex)
	signature='do you trust the contents of this directory|do you trust this directory'
	keys="enter"
	;;
pi | pi-signed)
	signature='trust this project|do you trust'
	keys="enter"
	;;
grok | kimi)
	# No dialog when launched inside a git repo root, which a thurbox worktree
	# always is. Nothing to answer; still confirmed as up below.
	;;
cursor | muse)
	flag_only="yes"
	;;
"")
	say "could not tell which agent holds the pane; sending nothing" unconfirmed
	exit 3
	;;
*)
	say "no trust gate is known for '$agent'; sending nothing. If it stops at
             startup, add it to the table in $here/session-trust.sh" unknown-agent
	exit 3
	;;
esac

if [ -n "$flag_only" ]; then
	say "'$agent' is not answered by a keystroke — it takes a launch flag
             (cursor: --trust, muse: --yolo). Put it in a profile in
             orchestration/session-profiles.yaml and spawn under that profile.
             Nothing was sent." flag-required
	exit 3
fi

# --- watch for the dialog ----------------------------------------------------

# `--json` and `.output`, not the plain capture: the human format wraps the
# pane in metadata lines, and a signature could in principle match one of
# those instead of the pane itself.
pane_matches() {
	[ -n "$signature" ] || return 1
	thurbox-cli session capture "$uuid" --lines 60 --json 2>/dev/null |
		jq -r '.output // ""' | grep -qiE -- "$signature"
}

# An agent whose hooks have fired is running its own loop, which is proof there
# is no modal dialog in front of it.
agent_reported() {
	thurbox-cli session get "$uuid" --json 2>/dev/null |
		jq -e '.hook_reported == true' >/dev/null
}

deadline=$((SECONDS + timeout_secs))
saw_dialog=0
while [ "$SECONDS" -lt "$deadline" ]; do
	if pane_matches; then
		saw_dialog=1
		break
	fi
	if agent_reported; then
		say "no dialog: $agent is already reporting; nothing sent" ready
		exit 0
	fi
	sleep 1
done

if [ "$saw_dialog" -eq 0 ]; then
	if [ -z "$signature" ]; then
		say "$agent shows no trust dialog in a git worktree; nothing sent" ready
		exit 0
	fi
	say "no trust dialog seen in ${timeout_secs}s and $agent has not reported.
             Nothing was sent. Look at the pane before prompting it:
               thurbox-cli session capture $uuid" unconfirmed
	exit 3
fi

# --- answer it ---------------------------------------------------------------

for k in $keys; do
	if ! thurbox-cli session key "$uuid" "$k" >/dev/null 2>&1; then
		say "could not send '$k' to the pane; the dialog is still up" send-failed
		exit 3
	fi
	sleep 1
done

# --- confirm it took ---------------------------------------------------------
#
# A send that reports success is not proof the dialog was answered. The dialog
# being GONE is.

for _ in 1 2 3 4 5 6 7 8 9 10; do
	if ! pane_matches; then
		say "answered $agent's trust dialog with '$keys'; the dialog is gone" answered
		exit 0
	fi
	sleep 1
done

say "sent '$keys' but $agent's trust dialog is still on the pane. Do not
             prompt this session; look at it:
               thurbox-cli session capture $uuid
             The config-seeding fallback is:
               $here/trust-thurbox-dir.sh <that session's worktree path>" unconfirmed
exit 3
