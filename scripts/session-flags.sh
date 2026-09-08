#!/usr/bin/env bash
# Render one session profile into `thurbox-cli session create` flags.
#
# `orchestration/session-profiles.yaml` holds the settings that shape the
# AGENT — environment, and optionally the command that launches it. This turns
# one profile into the flags `session create` takes, so a playbook never
# hand-assembles `--env` pairs and a profile that breaks a rule fails here
# rather than in a worker that started wrong.
#
# One file, one layer: every profile there is lives in the file above, and it
# is yours to edit.
#
# Usage:
#   scripts/session-flags.sh                 # the `default` profile
#   scripts/session-flags.sh sweep           # a named profile
#   scripts/session-flags.sh --check         # validate every profile
#   scripts/session-flags.sh sweep | tr '\0' '\n'   # eyeball the result
#
# Flags come out NUL-separated, because a `--arg` value is frequently a whole
# command line and may contain anything execve accepts. Read them with:
#
#   mapfile -d '' -t flags < <(scripts/session-flags.sh sweep)
#   thurbox-cli session create --name '...' --repo-path "$repo" "${flags[@]}"
#
# A profile carrying `command` renders `--command`, so drop `--agent` from
# that call: thurbox refuses both together.
#
# Requires: python3 (with PyYAML) — the same dependency scripts/check.sh has.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

profiles="orchestration/session-profiles.yaml"
wanted="default"

for arg in "$@"; do
	case "$arg" in
	-h | --help)
		sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
		exit 0
		;;
	--check) wanted="--check" ;;
	-*)
		printf 'error: unknown option %q (want: --check)\n' "$arg" >&2
		exit 2
		;;
	*) wanted="$arg" ;;
	esac
done

if ! command -v python3 >/dev/null; then
	echo "error: python3 not found" >&2
	exit 2
fi

exec python3 scripts/lib/session_profiles.py "$profiles" "$wanted"
