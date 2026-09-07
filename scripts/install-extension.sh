#!/usr/bin/env bash
# Render extension.toml from extension.toml.in, then install it into thurbox.
#
# `[[sessions]] repo_path` must be an absolute path to *this clone*. thurbox has
# no token for "my clone" — `{home}` is the extension home, not your checkout —
# and a template cannot ship one machine's path. So the manifest carries the
# `__REPO_PATH__` placeholder and this script substitutes the real path at
# install time. The rendered `extension.toml` is gitignored. See its header,
# which also records the tilde bug that used to be a second reason and no
# longer applies at the manifest's current version floor.
#
# Re-run this after moving the clone: the rendered path is baked in.
#
# Requires: git, thurbox-cli.

set -euo pipefail

die() {
	printf 'error: %s\n' "$1" >&2
	exit 1
}

command -v git >/dev/null || die "git not found"
command -v thurbox-cli >/dev/null || die "thurbox-cli not found; install thurbox first"

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

thurbox-cli extension install "$REPO_ROOT"

cat <<EOF

Installed. Useful follow-ups:

  thurbox-cli extension status fleet      # per-resource health
  thurbox-cli extension deactivate fleet  # the real off-switch
  thurbox-cli extension uninstall fleet   # reverse the install
EOF
