#!/usr/bin/env bash
# Where everything stands, in one call — the fuel, the queue, the workers, the
# pull requests, the monitor and this checkout, on one screen.
#
# It exists because orienting used to cost three to five commands across three
# checkouts and four tools, and most of a long lead session's tool calls were
# that rather than work. Run it reflexively; it is one `session list` (no pane
# probing), one `gh pr list` per repo in flight, and files on disk.
#
# Usage:
#   scripts/fleet-status.sh          # the screen
#   scripts/fleet-status.sh --json   # the same reading, machine-readable
#
# IT DEGRADES AND NEVER FAILS. No thurbox, no `gh`, no network, no monitor, no
# queue: each costs exactly its own section, which then says what it could not
# determine and why, and the exit status stays 0. The code is 0 for "this
# command ran", never for "the fleet is healthy" — read the sections.
#
# IT READS. It starts, stops, syncs and dispatches nothing.
#
# The queue root comes from `queue.sh root` and the monitor's line from
# `webui.sh status`, so the MONITOR section can tell you the dashboard is
# serving a different queue than the one above it — which is the whole answer
# to "why is the dashboard not updated?".
#
# FUEL IS THE ACCOUNT'S, NOT A SESSION'S. It comes from `quota-axi`, the only
# source that has a number at all — `thurbox-cli session get --json` carries no
# token, usage, cost or limit field. quota-axi measures the subscription window
# every session spends at once, so there is one reading and no per-worker
# breakdown to be had. FLEET.md's `## Fuel` section owns the reserve and what
# the lead does near it.
#
# Environment: FLEET_QUEUE_DIR and FLEET_WEBUI_DIR, honoured exactly as
# scripts/queue.sh and scripts/webui.sh honour them.
#
# Requires: python3 (with PyYAML). thurbox-cli, gh, git and quota-axi are each
# optional and cost only their own section — quota-axi in particular is a tool
# on the operator's PATH, never a dependency this repo vendors.
# scripts/fleet-status-selftest.sh proves it.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

case "${1:-}" in
-h | --help)
	awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
	exit 0
	;;
esac

if ! command -v python3 >/dev/null; then
	echo "error: python3 not found" >&2
	exit 2
fi

exec python3 scripts/lib/fleet_status.py "$@"
