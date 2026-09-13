#!/usr/bin/env bash
# Get a freshly created session past its agent's trust dialog, without touching
# anything the operator owns.
#
# A FORWARDER. The rules — confirm the dialog is on the pane, answer it with
# that agent's keys, confirm it is gone — and the per-agent table live in
# scripts/lib/session_trust.py, which `scripts/queue.sh dispatch` calls
# in-process so that a machine with no bash can dispatch. This file keeps the
# command every skill, playbook and hook names working, with the same CLI and
# the same exit codes. `--help` prints the whole of it.
#
# Usage:
#   scripts/session-trust.sh <session-uuid-or-name> [--timeout SECS] [--json]
#
# Requires: python3, thurbox-cli.

set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

command -v python3 >/dev/null || {
	echo "error: python3 not found" >&2
	exit 2
}

exec python3 "$here/lib/session_trust.py" "$@"
