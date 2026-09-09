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
# `__LEAD_GLYPH__` is the second placeholder and it is here for a different
# reason: not "one machine's path" but one machine's TERMINAL. thurbox has no
# per-session icon field, so the lead's mark can only live in its name — and
# whether a two-cell emoji renders correctly is a property of the font in front
# of the operator. `orchestration/session-glyphs.example.conf` is the single
# place that choice is made, `session-glyphs.conf` beside it is the gitignored
# override, and this script is what carries the answer into the name thurbox
# spawns. Nothing else in the repo spells the glyph: the pane matches the lead
# without it, and prose calls the lead Mission Control.
#
# CHANGING THE GLYPH IS A RENAME, and this script cannot apply one. It renders
# and installs the new name; the session that is already running keeps the old
# one, because thurbox has no rename verb and `ensure_extension` matches by
# name. `extension.toml.in`'s RENAMING header holds the two sequences that
# actually move it — the check below catches the analogous case for a moved
# clone, and says the same thing about a name.
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
# appears in `plugin list`, and draws nothing. That edit is a guarded block in
# the user's own `layout.lua` — a file every pane on their screen shares — so
# this prints the block and where it goes rather than writing it for them.
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

# The glyph setting, from the operator's own copy when there is one and from the
# tracked defaults when there is not. Read as DATA and never sourced: this file
# is a setting, and a setting that can execute is a different kind of file.
GLYPH_CONF="$REPO_ROOT/orchestration/session-glyphs.conf"
[ -f "$GLYPH_CONF" ] || GLYPH_CONF="$REPO_ROOT/orchestration/session-glyphs.example.conf"
[ -f "$GLYPH_CONF" ] || die "missing the glyph setting: $GLYPH_CONF"

glyph_setting() {
	sed -n "s/^$1=//p" "$GLYPH_CONF" | head -1
}

case "$(glyph_setting GLYPHS)" in
off) LEAD_GLYPH="$(glyph_setting LEAD_GLYPH_OFF)" ;;
on | "") LEAD_GLYPH="$(glyph_setting LEAD_GLYPH_ON)" ;;
*) die "GLYPHS in $GLYPH_CONF is neither 'on' nor 'off'" ;;
esac
[ -n "$LEAD_GLYPH" ] || die "no lead glyph in $GLYPH_CONF"

# `|` is the sed delimiter below and the name goes on to be a shell argument in
# every hint this script prints, so a glyph carrying one of these would break
# the substitution or the quoting rather than draw badly.
case "$LEAD_GLYPH" in
*'|'* | *"'"* | *'"'* | *' '*)
	die "the lead glyph from $GLYPH_CONF contains a quote, a pipe or a space: $LEAD_GLYPH"
	;;
esac

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

sed -e "s|__REPO_PATH__|$REPO_ROOT|g" -e "s|__LEAD_GLYPH__|$LEAD_GLYPH|g" "$IN" >"$tmp"

# Refuse to install a half-rendered manifest: an unsubstituted placeholder would
# register a session pointing at a directory literally named __REPO_PATH__, or
# a lead whose name begins with the word __LEAD_GLYPH__.
if grep -q '__REPO_PATH__\|__LEAD_GLYPH__' "$tmp"; then
	die "placeholder survived substitution; $OUT not written"
fi
[ -s "$tmp" ] || die "rendered manifest is empty; $OUT not written"

mv "$tmp" "$OUT"
trap - EXIT
printf 'rendered %s (repo_path = %s, lead glyph = %s)\n' "$OUT" "$REPO_ROOT" "$LEAD_GLYPH"

# Read the names out of the manifest rather than hardcoding them, so a rename
# (extension.toml.in's header owns the procedure) reaches this script's checks
# and hints for free.
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

	# THE GLYPH FLIP, WHICH LOOKS LIKE SUCCESS AND IS NOT. When the setting
	# moves, the name above is one no session answers to yet — so the check
	# above finds no live cwd to compare and says nothing, while the lead the
	# operator is looking at still wears the old mark. thurbox will spawn the
	# new name beside it on the next activate and `extension status` will call
	# that healthy, which is the RENAMING header's failure mode arriving through
	# a setting instead of an edit. So: if nothing answers to the new name and
	# something answers to the other glyph's, say so here.
	if [ -z "$live_cwd" ]; then
		other="$(glyph_setting LEAD_GLYPH_OFF)"
		[ "$other" = "$LEAD_GLYPH" ] && other="$(glyph_setting LEAD_GLYPH_ON)"
		stale="${session_name#"$LEAD_GLYPH"}"
		stale="$other$stale"
		if [ -n "$other" ] && [ "$stale" != "$session_name" ] &&
			thurbox-cli session list --json 2>/dev/null |
			jq -e --arg n "$stale" 'any(.[]; .name == $n)' >/dev/null 2>&1; then
			cat >&2 <<-EOF

				note: the running lead is still '$stale'.

				The manifest now declares '$session_name', but thurbox names a
				session when it SPAWNS it and has no rename verb, so nothing that
				is already running moved. Applying it is your call and costs
				either the lead's conversation or a fork — extension.toml.in's
				RENAMING header holds both sequences. Until you run one, the old
				session is the live one and the new name is what the next spawn
				would use.
			EOF
		fi
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
			pane on your screen shares that file. Add this block to:

			  ${ui_dir:-<thurbox-cli plugin dir>}/layout.lua

			beside the other side columns, inside the \`columns\` list:

			  if panels.shown("fleetqueue") and filled(ctx, "fleetqueue") then
			    columns[#columns + 1] = { slot = "fleetqueue", pct = 30, min = 34 }
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
