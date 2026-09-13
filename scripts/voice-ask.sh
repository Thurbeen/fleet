#!/usr/bin/env bash
# Ask what the lead calls the operator and what it answers to — once, before
# the extension renders them.
#
# WHY THIS EXISTS. `scripts/install-extension.sh` renders both names into
# `FLEET.rendered.md`, the lead's standing context, from the operator's
# gitignored `orchestration/voice.conf` or else the tracked defaults in
# `voice.example.conf`. Nothing asked, so every install took the defaults
# without the operator ever choosing. Onboarding's step 5 runs this BEFORE the
# install, because a name chosen after it is a re-install and a lead restart.
#
# WHY IT ASKS ONCE. The answer is `voice.conf` itself, and a file that exists
# is an answer: it is kept, never re-asked, and never overwritten without
# `--replace`, which is the operator's say-so. Record the defaults too when the
# operator accepts them, or the next run asks again.
#
# THE RENDERER DECIDES WHICH NAMES ARE REFUSED. Each answer is rendered through
# `install-extension.sh --render-only` before it is written, so the characters
# that break the substitution have one owner and this holds no second copy of
# the list. A name spanning two lines is refused here, because the reader takes
# the first line and would drop the rest in silence.
#
# Usage:
#   scripts/voice-ask.sh                                  # `ask`, or `skip: <the names kept>`
#   scripts/voice-ask.sh set <operator> <lead>            # record the answer
#   scripts/voice-ask.sh set --replace <operator> <lead>  # ... over an existing voice.conf
#
# The first word of the output is the answer. Exit: 0; 1 when a name is refused
# or voice.conf already holds an answer, and nothing was written; 2 on a usage
# error.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

EXAMPLE="orchestration/voice.example.conf"
CONF="orchestration/voice.conf"

setting() {
	sed -n "s/^$2=//p" "$1" | head -1
}

usage() {
	printf 'usage: %s [set [--replace] <operator> <lead>]\n' "$0" >&2
	exit 2
}

case "${1:-}" in
"")
	if [ -f "$CONF" ]; then
		printf 'skip: already answered — the lead calls the operator %s and answers to %s (%s)\n' \
			"$(setting "$CONF" OPERATOR_NAME)" "$(setting "$CONF" ASSISTANT_NAME)" "$CONF"
		exit 0
	fi
	cat <<-EOF
		ask: nobody has said what the lead calls the operator or what it answers to.
		Ask the operator both, before the extension is installed:
		  what should the lead call you?     default: $(setting "$EXAMPLE" OPERATOR_NAME)
		  what should the lead answer to?    default: $(setting "$EXAMPLE" ASSISTANT_NAME)
		Then record the answer, defaults included, so it is asked once:
		  ./scripts/voice-ask.sh set '<operator>' '<lead>'
	EOF
	;;
set)
	shift
	replace=0
	if [ "${1:-}" = --replace ]; then
		replace=1
		shift
	fi
	[ $# -eq 2 ] || usage
	op="$1"
	lead="$2"

	if [ -f "$CONF" ] && [ "$replace" -eq 0 ]; then
		printf 'refused: %s already holds an answer (%s, %s). Nothing was written.\n' \
			"$CONF" "$(setting "$CONF" OPERATOR_NAME)" "$(setting "$CONF" ASSISTANT_NAME)" >&2
		printf 'Replacing it is the operator'"'"'s call: ./scripts/voice-ask.sh set --replace <operator> <lead>\n' >&2
		exit 1
	fi

	for name in "$op" "$lead"; do
		case "$name" in
		*$'\n'* | *$'\r'*)
			printf 'refused: a name spans two lines. Nothing was written.\n' >&2
			exit 1
			;;
		esac
	done

	tmp="$(mktemp -d)" || exit 1
	trap 'rm -rf "$tmp"' EXIT
	printf 'OPERATOR_NAME=%s\nASSISTANT_NAME=%s\n' "$op" "$lead" >"$tmp/voice.conf"
	if ! report="$(FLEET_VOICE_CONF="$tmp/voice.conf" \
		./scripts/install-extension.sh --render-only "$tmp" 2>&1)"; then
		printf 'refused: %s\nNothing was written.\n' \
			"$(printf '%s\n' "$report" | sed -n 's/^error: //p' | head -1)" >&2
		exit 1
	fi

	{
		printf '# The operator'"'"'s answer, written by scripts/voice-ask.sh. Gitignored;\n'
		printf '# orchestration/voice.example.conf documents both settings.\n'
		cat "$tmp/voice.conf"
	} >"$CONF.tmp" && mv "$CONF.tmp" "$CONF" || exit 1
	printf 'recorded: the lead calls the operator %s and answers to %s (%s)\n' "$op" "$lead" "$CONF"

	# A lead rendered before this answer keeps the names it was rendered with.
	if [ "$replace" -eq 1 ] || [ -f FLEET.rendered.md ]; then
		printf 'A lead that is already running still uses the old names: re-install and restart it,\n'
		printf 'which .agents/skills/update-fleet/ owns.\n'
	else
		printf 'Next: ./scripts/install-extension.sh renders them.\n'
	fi
	;;
-h | --help)
	sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
	;;
*)
	usage
	;;
esac
