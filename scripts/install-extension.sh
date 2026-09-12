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
# `__LEAD_AGENT__` is the third, and it is here because WHICH agent runs the
# lead is the operator's answer and this repo is public. `AGENT` in
# `orchestration/agent.conf` — the same setting every worker spawn reads — is
# where it lives, and with none set this renders thurbox's stock `claude`,
# which is a name thurbox must be given rather than a coupling to one vendor.
#
# IT ALSO RENDERS THE PAYLOAD, for the same reason and out of a second setting.
# `FLEET.md` is the lead's standing context, and the two names it is written
# around — what the lead calls the operator, and what it answers to — are the
# operator's, not this repo's. So FLEET.md carries `@OPERATOR_NAME@` and
# `@ASSISTANT_NAME@` — `@`-delimited and not `__`-delimited like the manifest's
# placeholders, because markdown reads `__x__` as bold and the linter says so —
# `orchestration/voice.example.conf` is the one place they
# are chosen (`voice.conf` beside it is the gitignored override), and this
# script writes the substituted copy to a gitignored `FLEET.rendered.md` that
# the manifest ships as its `[[files]]` payload. Rendering to a SECOND file and
# not in place is what keeps `scripts/sync-checkout.sh` a clean tree to
# fast-forward: an operator who renamed themselves has changed no tracked file.
#
# AND A RENDERED PAYLOAD REACHES NO RUNNING LEAD. The session froze FLEET.md at
# launch, so a new name is a re-install AND a restart — the same two steps a
# glyph needs, and `.agents/skills/update-fleet/` owns them.
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
# the user's own `layout.lua` — a file every pane on their screen shares — and
# `scripts/place-pane.sh` is what writes it, on the user's word and never as a
# side effect of an install. So this closes by naming that command and printing
# the block, for whichever of the two they want — and the first Mission Control
# session asks the same question once, through `scripts/pane-ask.sh`.
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
# Usage:
#   ./scripts/install-extension.sh                 # render, then install
#   ./scripts/install-extension.sh --render-only <dir>
#                                                  # render both files into
#                                                  # <dir> and stop. Touches
#                                                  # nothing thurbox owns and
#                                                  # needs neither thurbox-cli
#                                                  # nor jq, which is how
#                                                  # `scripts/check.sh voice`
#                                                  # exercises the substitution
#                                                  # without installing.
#
# Requires: git, thurbox-cli, jq — the last two only for a real install.

set -euo pipefail

die() {
	printf 'error: %s\n' "$1" >&2
	exit 1
}

RENDER_ONLY=""
case "${1-}" in
--render-only)
	[ $# -eq 2 ] || die "--render-only takes a directory"
	RENDER_ONLY="$2"
	[ -d "$RENDER_ONLY" ] || die "not a directory: $RENDER_ONLY"
	;;
"") ;;
*) die "unknown argument: $1 (usage: $0 [--render-only <dir>])" ;;
esac

command -v git >/dev/null || die "git not found"
if [ -z "$RENDER_ONLY" ]; then
	command -v thurbox-cli >/dev/null || die "thurbox-cli not found; install thurbox first"
	command -v jq >/dev/null || die "jq not found"
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" ||
	die "not inside a git repository; run this from your clone"

DEST="${RENDER_ONLY:-$REPO_ROOT}"

IN="$REPO_ROOT/extension.toml.in"
OUT="$DEST/extension.toml"

FLEET_IN="$REPO_ROOT/FLEET.md"
FLEET_OUT="$DEST/FLEET.rendered.md"

[ -f "$IN" ] || die "missing $IN"
[ -f "$FLEET_IN" ] || die "missing $FLEET_IN"

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

# The agent the LEAD session binds to, read the same way and from the same pair
# the worker spawns read: `AGENT` in the operator's `orchestration/agent.conf`,
# else the tracked example, which names none. thurbox needs a name here, so an
# unset setting falls back to its own stock `claude` — the agent it ships and
# the one this manifest was written against. FLEET_AGENT_ROOT relocates the
# pair so the gate can render an operator's answer without having one.
AGENT_ROOT="${FLEET_AGENT_ROOT:-$REPO_ROOT}"
AGENT_CONF="$AGENT_ROOT/orchestration/agent.conf"
[ -f "$AGENT_CONF" ] || AGENT_CONF="$AGENT_ROOT/orchestration/agent.example.conf"
LEAD_AGENT=""
[ -f "$AGENT_CONF" ] && LEAD_AGENT="$(sed -n 's/^AGENT=//p' "$AGENT_CONF" | head -1)"
[ -n "$LEAD_AGENT" ] || LEAD_AGENT="claude"

# A thurbox agent name is a bare identifier; anything else would either break
# the substitution below or register a session against an agent that cannot
# exist.
case "$LEAD_AGENT" in
*[!A-Za-z0-9_-]*)
	die "AGENT in $AGENT_CONF is not a bare agent name: $LEAD_AGENT"
	;;
esac

# The voice setting, read the same way and from the same kind of pair: the
# operator's own copy when there is one, the tracked defaults when there is not.
# FLEET_VOICE_CONF is the seam `scripts/check.sh voice` renders through, so the
# gate can prove the substitution without writing over the operator's answer.
VOICE_CONF="${FLEET_VOICE_CONF:-}"
if [ -z "$VOICE_CONF" ]; then
	VOICE_CONF="$REPO_ROOT/orchestration/voice.conf"
	[ -f "$VOICE_CONF" ] || VOICE_CONF="$REPO_ROOT/orchestration/voice.example.conf"
fi
[ -f "$VOICE_CONF" ] || die "missing the voice setting: $VOICE_CONF"

voice_setting() {
	sed -n "s/^$1=//p" "$VOICE_CONF" | head -1
}

OPERATOR_NAME="$(voice_setting OPERATOR_NAME)"
ASSISTANT_NAME="$(voice_setting ASSISTANT_NAME)"
[ -n "$OPERATOR_NAME" ] || die "no OPERATOR_NAME in $VOICE_CONF"
[ -n "$ASSISTANT_NAME" ] || die "no ASSISTANT_NAME in $VOICE_CONF"

# `|` is the sed delimiter below, and a backslash or a `&` in the replacement is
# sed's own syntax rather than the name the operator typed. `@` is the
# placeholder delimiter itself: a name containing `@ASSISTANT_NAME@` or
# `@OPERATOR_NAME@` would have the OTHER substitution rewrite it after this
# one applied, silently swapping one operator's name for the other's. Refuse
# rather than render something they did not write.
for name in "$OPERATOR_NAME" "$ASSISTANT_NAME"; do
	case "$name" in
	*'|'* | *\\* | *'&'* | *"'"* | *'"'* | *'@'*)
		die "a name in $VOICE_CONF contains a quote, a pipe, a backslash, an '&' or an '@': $name"
		;;
	esac
done

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

sed -e "s|__REPO_PATH__|$REPO_ROOT|g" -e "s|__LEAD_GLYPH__|$LEAD_GLYPH|g" \
	-e "s|__LEAD_AGENT__|$LEAD_AGENT|g" "$IN" >"$tmp"

# Refuse to install a half-rendered manifest: an unsubstituted placeholder would
# register a session pointing at a directory literally named __REPO_PATH__, a
# lead whose name begins with the word __LEAD_GLYPH__, or one bound to an agent
# thurbox has never heard of.
if grep -q '__REPO_PATH__\|__LEAD_GLYPH__\|__LEAD_AGENT__' "$tmp"; then
	die "placeholder survived substitution; $OUT not written"
fi
[ -s "$tmp" ] || die "rendered manifest is empty; $OUT not written"

mv "$tmp" "$OUT"
trap - EXIT
printf 'rendered %s (repo_path = %s, lead glyph = %s, agent = %s)\n' \
	"$OUT" "$REPO_ROOT" "$LEAD_GLYPH" "$LEAD_AGENT"

# The payload, from the same tracked source and under the same refusal: a
# surviving placeholder would ship the lead a context file telling it to address
# the operator as @OPERATOR_NAME@.
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

sed -e "s|@OPERATOR_NAME@|$OPERATOR_NAME|g" \
	-e "s|@ASSISTANT_NAME@|$ASSISTANT_NAME|g" "$FLEET_IN" >"$tmp"

if grep -q '@OPERATOR_NAME@\|@ASSISTANT_NAME@' "$tmp"; then
	die "placeholder survived substitution; $FLEET_OUT not written"
fi
[ -s "$tmp" ] || die "rendered payload is empty; $FLEET_OUT not written"

mv "$tmp" "$FLEET_OUT"
trap - EXIT
printf 'rendered %s (operator = %s, lead answers to = %s)\n' \
	"$FLEET_OUT" "$OPERATOR_NAME" "$ASSISTANT_NAME"

if [ -n "$RENDER_ONLY" ]; then
	printf '\n--render-only: nothing was installed.\n'
	exit 0
fi

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
			nothing yet. Mission Control asks you once, on its first session,
			whether to put it on screen. To do it now instead, one command puts
			it to the right of the terminal, and it is not run for you — every
			pane on your screen shares that file:

			  ./scripts/place-pane.sh --dry-run   # what it would write, where
			  ./scripts/place-pane.sh             # place it (--left for the other side)

			It backs the file up first, refuses an arrangement it cannot read,
			and re-reads its own edit. To do it by hand instead, add this block
			to:

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
