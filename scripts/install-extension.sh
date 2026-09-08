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
# IT ALSO INSTALLS THE TUI PANE. `interface/fleet_queue.lua` is a thurbox
# interface pane, and a pane does not travel in an extension manifest — it is
# recorded in the user's `plugins.toml` by `thurbox-cli plugin install`, which is
# the front door thurbox intends for one. `extension.toml.in`'s header argues
# why it is not an `[[external_files]]` payload instead.
#
# What this script will NOT do is place it. A pane names a slot and the
# arrangement decides where that slot goes, so a pane nothing places loads,
# appears in `plugin list`, and draws nothing. That edit is one line in the
# user's own `layout.lua` — a file every pane on their screen shares — so this
# prints the line and where it goes rather than writing it for them.
#
# TAKING THE PANE BACK is `plugin remove`, and its argument is the DESTINATION
# PATH below, not the file's basename:
#
#     thurbox-cli plugin remove plugins/91_fleet_queue.lua
#
# `plugin remove 91_fleet_queue.lua` answers "not listed in plugins.toml" and
# removes nothing. Because the pane went in through `plugin install`, that one
# command takes back the file, its `plugins.toml` entry and the lock together —
# `plugin list` says where it came from in the meantime.
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

# --- the TUI queue pane -------------------------------------------------------
#
# Separate from the extension install above, and deliberately not fatal to it: a
# control plane with no pane still works, and a `plugin install` that failed
# should not make the manifest look like it did too.

PANE_SRC="$REPO_ROOT/interface/fleet_queue.lua"
PANE_DEST="plugins/91_fleet_queue.lua"

if [ ! -f "$PANE_SRC" ]; then
	printf '\nwarning: %s is missing; the TUI queue pane was not installed\n' "$PANE_SRC" >&2
elif ! thurbox-cli plugin install "$PANE_SRC" --as "$PANE_DEST" --text; then
	printf '\nwarning: could not install the TUI queue pane from %s\n' "$PANE_SRC" >&2
else
	# `plugin check` loads the interface the way thurbox does and exits non-zero
	# on the failure that looks like success — a pane that loads and is placed by
	# no arrangement. It is the only thing that can tell those two apart, so its
	# verdict is read here rather than assumed.
	pane_report="$(thurbox-cli plugin check --text 2>&1)" && pane_ok=1 || pane_ok=0
	ui_dir="$(thurbox-cli plugin dir --text 2>/dev/null | head -1)" || true

	if [ "$pane_ok" = 1 ]; then
		printf '\nThe fleet queue pane is installed and placed. Press F3 in thurbox.\n'
	else
		cat <<-EOF

			The fleet queue pane is installed but NOT PLACED, so it will draw
			nothing yet. Nothing here will edit your arrangement for you — every
			pane on your screen shares that file. Add one line to:

			  ${ui_dir:-<thurbox-cli plugin dir>}/layout.lua

			beside the other side columns, inside the \`columns\` list:

			  if panels.shown("fleetqueue") and filled(ctx, "fleetqueue") then
			    columns[#columns + 1] = { slot = "fleetqueue", pct = 26, min = 32 }
			  end

			The \`panels.shown\` guard is not optional: without it the column is
			carved on every frame and F3 toggles a value nothing reads, so the
			pane opens and never closes. \`panels\` and \`filled\` both already
			exist in the stock layout.lua, beside the same guard on the session
			list. Then \`thurbox-cli plugin check\` goes green and F3 opens and
			closes the pane. What it reported:

		EOF
		printf '%s\n' "$pane_report" | sed 's/^/  /'
	fi
fi

cat <<EOF

Installed. Useful follow-ups:

  thurbox-cli extension status $ext_name      # per-resource health
  thurbox-cli extension deactivate $ext_name  # the real off-switch
  thurbox-cli extension uninstall $ext_name   # reverse the install
EOF
