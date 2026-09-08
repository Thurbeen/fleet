#!/usr/bin/env bash
# Render extension.toml from extension.toml.in, then install it into thurbox.
#
# `[[sessions]] repo_path` must be an absolute path to *this clone*. thurbox has
# no token for "my clone" — `{home}` is the extension home, not your checkout —
# and a committed file cannot carry one machine's path. So the manifest has the
# `__REPO_PATH__` placeholder and this script substitutes the real path at
# install time. The rendered `extension.toml` is gitignored. See its header,
# which also records the tilde bug that used to be a second reason and no
# longer applies at the manifest's current version floor.
#
# MOVED THE CLONE? Re-running this is NOT enough on its own, and the closing
# check below is what tells you so. thurbox's `ensure_extension` looks an
# extension's session up by NAME and reuses the one it finds; it never compares
# or updates that session's directory. So a re-install rewrites the manifest,
# reports success, and leaves the live session opening the OLD path — with
# `extension status` still calling it healthy. The fix is to tear the session
# down first, which is what `extension deactivate` does:
#
#     thurbox-cli extension deactivate <name>   # deletes the session
#     ./scripts/install-extension.sh            # respawns it at the new path
#
# That discards the lead session's conversation history, so this script refuses
# to do it for you — it detects the drift, names the remedy, and exits non-zero.
#
# Requires: git, thurbox-cli, jq.

set -euo pipefail

die() {
	printf 'error: %s\n' "$1" >&2
	exit 1
}

command -v git >/dev/null || die "git not found"
command -v thurbox-cli >/dev/null || die "thurbox-cli not found; install thurbox first"
command -v jq >/dev/null || die "jq not found"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" ||
	die "not inside a git repository; run this from your clone"

IN="$REPO_ROOT/extension.toml.in"
OUT="$REPO_ROOT/extension.toml"

[ -f "$IN" ] || die "missing $IN"

case "$REPO_ROOT" in
/*) ;;
*) die "repo root is not an absolute path: $REPO_ROOT" ;;
esac

# `|` as the sed delimiter, and the path must not contain one.
case "$REPO_ROOT" in
*'|'*) die "repo path contains '|', which breaks the substitution: $REPO_ROOT" ;;
esac

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

sed "s|__REPO_PATH__|$REPO_ROOT|g" "$IN" >"$tmp"

# Refuse to install a half-rendered manifest: an unsubstituted placeholder would
# register a session pointing at a directory literally named __REPO_PATH__.
if grep -q '__REPO_PATH__' "$tmp"; then
	die "placeholder survived substitution; $OUT not written"
fi
[ -s "$tmp" ] || die "rendered manifest is empty; $OUT not written"

mv "$tmp" "$OUT"
trap - EXIT
printf 'rendered %s (repo_path = %s)\n' "$OUT" "$REPO_ROOT"

# Read the names out of the manifest rather than hardcoding them, so a rename
# (README's "Renaming" section) reaches this script's checks and hints for free.
# The extension name is the first top-level `name`; the session's is the first
# one after `[[sessions]]`.
ext_name="$(sed -n 's/^name *= *"\(.*\)"/\1/p' "$OUT" | head -1)"
session_name="$(sed -n '/^\[\[sessions\]\]/,$p' "$OUT" |
	sed -n 's/^name *= *"\(.*\)"/\1/p' | head -1)"
[ -n "$ext_name" ] || die "could not read the extension name from $OUT"

thurbox-cli extension install "$REPO_ROOT"

# thurbox reuses an existing session of the same name without moving it, so the
# only honest confirmation is the live session's own directory. See the header.
if [ -n "$session_name" ]; then
	live_cwd="$(thurbox-cli session list --json 2>/dev/null |
		jq -r --arg n "$session_name" \
			'.[] | select(.name == $n) | .cwd' 2>/dev/null | head -1)" || live_cwd=""
	if [ -n "$live_cwd" ] && [ "${live_cwd%/}" != "$REPO_ROOT" ]; then
		cat >&2 <<-EOF

			error: the '$session_name' session still opens a different directory.

			  live session: $live_cwd
			  this clone:   $REPO_ROOT

			The manifest was rendered and installed, but thurbox reuses an existing
			session by name and never moves it, so nothing changed for the session
			that actually runs. To repoint it — this DELETES that session and its
			conversation history, so it is your call, not this script's:

			  thurbox-cli extension deactivate $ext_name
			  ./scripts/install-extension.sh
		EOF
		exit 1
	fi
fi

cat <<EOF

Installed. Useful follow-ups:

  thurbox-cli extension status $ext_name      # per-resource health
  thurbox-cli extension deactivate $ext_name  # the real off-switch
  thurbox-cli extension uninstall $ext_name   # reverse the install
EOF
