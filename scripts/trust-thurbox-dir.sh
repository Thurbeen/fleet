#!/usr/bin/env bash
# Mark a directory as trusted for Claude Code, so a session started there does
# not stop on the workspace-trust dialog.
#
# WHY THIS EXISTS
#
# Directory trust is NOT a settings.json key — the settings schema has no such
# field. It lives in ~/.claude.json as:
#
#     .projects["<absolute path>"].hasTrustDialogAccepted = true
#
# keyed by *exact absolute path*. There is no glob and no prefix rule.
#
# WHICH PATH, THOUGH — observed live on 2026-09-07, and it is not what this
# header used to claim. Answering the dialog inside a thurbox worktree records
# the trust against the REPOSITORY's main worktree path, not the worktree's
# own. So the FIRST worker in a repo meets the dialog and later ones do not.
# That is milder than "every worker", and worse in one specific way that
# matters here: fleet dispatches a whole ready set at once, so every worker in
# the first wave against a repo draws the dialog simultaneously, and a dialog
# already on screen stays on screen after another session records the trust.
#
# THIS IS NO LONGER THE PRIMARY PATH. `scripts/session-trust.sh` answers the
# dialog with a keystroke instead, which touches nothing that outlives the
# session — see its header. Seeding is the FALLBACK, for when a dialog cannot
# be confirmed or answered, and for pre-seeding a batch of worktrees ahead of
# an unattended run. It writes to a file the operator owns, so reach for it
# deliberately rather than by default.
#
# TRUST IS A REAL GUARD. Accepting it in advance says "I vouch for the code in
# this directory". Only ever point this at worktrees of repos you already trust
# — which is what --all-worktrees does. Never at a path you did not create.
#
# CAVEAT: ~/.claude.json is rewritten by every running Claude Code process. A
# concurrent write can clobber this edit. Seed a path *before* the session that
# uses it starts, and prefer running this while few sessions are live.
#
# Usage:
#   scripts/trust-thurbox-dir.sh <absolute-dir> [<absolute-dir> ...]
#   scripts/trust-thurbox-dir.sh --all-worktrees   # every existing thurbox worktree

set -euo pipefail

CLAUDE_JSON="${CLAUDE_JSON:-$HOME/.claude.json}"
THURBOX_WORKTREES="${THURBOX_WORKTREES:-$HOME/.local/share/thurbox/worktrees}"

die() {
	printf 'error: %s\n' "$1" >&2
	exit 1
}

[ -f "$CLAUDE_JSON" ] || die "no such file: $CLAUDE_JSON"
command -v jq >/dev/null || die "jq is required"

dirs=()
if [ "${1:-}" = "--all-worktrees" ]; then
	[ -d "$THURBOX_WORKTREES" ] || die "no such dir: $THURBOX_WORKTREES"
	while IFS= read -r d; do
		dirs+=("$d")
	done < <(find "$THURBOX_WORKTREES" -mindepth 2 -maxdepth 2 -type d)
	[ ${#dirs[@]} -gt 0 ] || {
		echo "no worktrees under $THURBOX_WORKTREES"
		exit 0
	}
else
	[ $# -gt 0 ] || die "usage: $0 <absolute-dir>... | --all-worktrees"
	dirs=("$@")
fi

for d in "${dirs[@]}"; do
	case "$d" in
	/*) ;;
	*) die "path must be absolute: $d" ;;
	esac
done

backup="$CLAUDE_JSON.bak.$$"
cp -p "$CLAUDE_JSON" "$backup"

# Build a JSON array of the paths. Passing them as jq positional `--args` does
# not work here: jq would consume "$CLAUDE_JSON" as a positional string rather
# than as its input file, read nothing, and emit nothing.
dirs_json="$(printf '%s\n' "${dirs[@]}" | jq -R . | jq -s .)"

tmp="$CLAUDE_JSON.tmp.$$"
if ! jq --argjson dirs "$dirs_json" '
      reduce $dirs[] as $d (.;
        .projects[$d].hasTrustDialogAccepted = true)
    ' "$CLAUDE_JSON" >"$tmp"; then
	rm -f "$tmp"
	die "jq failed; $CLAUDE_JSON untouched (backup at $backup)"
fi

# Refuse to install a truncated file.
[ -s "$tmp" ] || {
	rm -f "$tmp"
	die "refusing to write empty file (backup at $backup)"
}
jq -e '.projects | type == "object"' "$tmp" >/dev/null || {
	rm -f "$tmp"
	die "result lost .projects (backup at $backup)"
}

mv "$tmp" "$CLAUDE_JSON"
rm -f "$backup"

for d in "${dirs[@]}"; do
	printf 'trusted  %s\n' "$d"
done
