#!/usr/bin/env bash
# Where everything stands, in one call — the fuel, the queue, the workers, the
# pull requests and this checkout, on one screen.
#
# It exists because orienting used to cost three to five commands across three
# checkouts and four tools, and most of a long lead session's tool calls were
# that rather than work. Run it reflexively; it is one `session list` (no pane
# probing), one `gh pr list` per repo in flight, and files on disk.
#
# Usage:
#   scripts/fleet-status.sh          # the screen
#   scripts/fleet-status.sh --json   # the same reading, machine-readable
#   scripts/fleet-status.sh --fuel   # the fuel section alone, one field per line
#
# IT DEGRADES AND NEVER FAILS. No thurbox, no `gh`, no network, no queue:
# each costs exactly its own section, which then says what it could not
# determine and why, and the exit status stays 0. The code is 0 for "this
# command ran", never for "the fleet is healthy" — read the sections.
#
# IT READS. It starts, stops, syncs and dispatches nothing.
#
# The queue root comes from `queue.sh root`, so this is never a second opinion
# about which records it is reading.
#
# FUEL IS THE ACCOUNT'S, NOT A SESSION'S. It comes from `quota-axi`, the only
# source that has a number at all — `thurbox-cli session get --json` carries no
# token, usage, cost or limit field. quota-axi measures the subscription window
# every session spends at once, so there is one reading per authenticated
# provider and no per-worker breakdown to be had. FLEET.md's `## Fuel` section
# owns the reserve and what the lead does near it.
#
# `--fuel` IS THAT SECTION ALONE, as `name<TAB>value` records — one per
# provider, separated by a blank line. It exists for the TUI queue pane, which
# draws the same readings and can afford neither `--json` (which collects
# every section, so a `gh pr list` per repo in flight) nor a JSON parser — a
# thurbox pane is Lua with no `os` and no `json`. It prints `probe_fuel_all()`'s
# own fields under their own names, so the pane and this screen cannot come to
# different conclusions about what quota-axi said.
#
# Environment: FLEET_QUEUE_DIR, honoured exactly as scripts/queue.sh honours
# it.
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
