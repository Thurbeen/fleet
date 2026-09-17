#!/bin/sh
# Install fleet: uv, git and the checkout, then `fleet install` for everything else.
#
#     curl -LsSf https://raw.githubusercontent.com/Thurbeen/fleet/main/install.sh | sh
#
# Or read it before running it, which is what keeping it short is for:
#
#     curl -LsSfo install.sh https://raw.githubusercontent.com/Thurbeen/fleet/main/install.sh
#     less install.sh
#     sh install.sh
#
# Windows has its own: install.ps1, which does the same four steps.
#
# A BOOTSTRAP, AND NOTHING MORE. It gets the three things `fleet install` cannot
# get for itself, and hands over:
#
#   1. uv         astral's own installer, which needs no root
#   2. git        the OS package manager, after saying so and asking once
#   3. checkout   clone it, or fast-forward the one already there
#   4. hand-off   uv run --project <checkout> fleet install
#
# `fleet install` (scripts/lib/install.py) owns the rest: every missing
# dependency listed and installed on one answer, the skills link, the
# reconciler's Stop nudge, the thurbox extension and queue pane, and preflight.
# Re-running any of it is safe: what is already in place is left as it is.
#
# WHERE THE CLONE GOES IS STICKY. The extension bakes this path in, and thurbox
# reuses the lead session by name without ever moving it, so a clone that
# moves later costs the lead its conversation. The directory is, in order:
#
#   --dir DIR, or FLEET_DIR     what the operator said
#   the lead's own checkout     a Mission Control session already opens one, so
#                               a second clone would be one thurbox never uses
#   ~/fleet, or ~/fleet-NAME    a plain directory the operator can find and
#                               back up - the queue and the map live in it
#
# A SECOND FLEET IS --name. One machine may run several, one clone each, each
# with a Mission Control of its own; `--name acme` says this run is another one,
# so it does not land on the fleet already installed here. It clones to
# ~/fleet-acme by default and writes orchestration/fleet.conf, which is the one
# setting that decides the extension and the lead the install then renders.
# It never renames a fleet that already named itself something else: that is a
# rename, and extension.toml.in's RENAMING header says what one costs.
#
# With SEVERAL fleets installed and neither --dir nor --name given, there is no
# honest default left, so it refuses rather than guess which one you meant.
#
# NOTHING EXISTING IS OVERWRITTEN. A directory that is not a fleet clone is
# refused. An existing clone is fast-forwarded and only fast-forwarded: on
# another branch, diverged from origin, or behind with uncommitted changes to
# tracked files, it is refused and left exactly as it was.
#
# POSIX sh, because `| sh` runs whatever /bin/sh is. The body is one function
# called on the last line, so the shell has read all of it before any child
# runs - under `| sh` stdin IS the rest of this file, and a child that read it
# would swallow every line after itself. For the same reason every question is
# read from /dev/tty and never from stdin. With no terminal, `--yes` (or
# FLEET_YES=1) is the answer; without either, it refuses and prints the command.
#
# Settings, from the environment:
#   FLEET_DIR     where the checkout goes (see above)
#   FLEET_NAME    this fleet's name, as --name does (see above)
#   FLEET_REPO    what to clone (default: https://github.com/Thurbeen/fleet.git)
#   FLEET_BRANCH  the branch to track (default: main)
#   FLEET_YES     1 answers every question yes, as --yes does
#   UV_INSTALL_DIR, UV_NO_MODIFY_PATH   passed through to astral's uv installer
#   FLEET_TEST_UV_INSTALLER   TESTS ONLY: a local script run instead of
#                 downloading astral's installer; nothing reads it unless set
#
# Usage: sh install.sh [--dir DIR] [--name NAME] [--yes]
# Exit: 0 installed, 1 refused or a step failed, 2 usage.

say() { printf '%s\n' "$*"; }

die() {
	printf '\nfleet install: %s\n' "$1" >&2
	exit "${2:-1}"
}

has_tty() { (exec </dev/tty) 2>/dev/null; }

# 0 yes, 1 no, 2 nobody to ask.
confirm() {
	[ "$YES" = 1 ] && return 0
	has_tty || return 2
	printf '%s [y/N] ' "$1" >/dev/tty
	read -r answer </dev/tty || return 1
	case "$answer" in [yY]*) return 0 ;; *) return 1 ;; esac
}

uv_dirs_on_path() {
	# The installer's own order of places, which a fresh shell's PATH may not hold yet.
	for d in "$HOME/.local/bin" "${XDG_DATA_HOME:+$XDG_DATA_HOME/../bin}" "${XDG_BIN_HOME:-}" "${UV_INSTALL_DIR:-}"; do
		[ -n "$d" ] && [ -x "$d/uv" ] && PATH="$d:$PATH"
	done
	export PATH
}

ensure_uv() {
	command -v uv >/dev/null 2>&1 && return
	# Installed by an earlier run into a directory this shell's PATH lacks.
	uv_dirs_on_path
	command -v uv >/dev/null 2>&1 && return
	say "uv is not installed; installing it with astral's installer (no root needed)."
	if [ -n "${FLEET_TEST_UV_INSTALLER:-}" ]; then
		sh "$FLEET_TEST_UV_INSTALLER" </dev/null || die "the uv installer failed; its error is above"
	elif command -v curl >/dev/null 2>&1; then
		curl -LsSf https://astral.sh/uv/install.sh </dev/null | sh || die "the uv installer failed; its error is above"
	elif command -v wget >/dev/null 2>&1; then
		wget -qO- https://astral.sh/uv/install.sh </dev/null | sh || die "the uv installer failed; its error is above"
	else
		die "uv is not installed, and fetching its installer needs curl or wget. Install one, then run this again."
	fi
	uv_dirs_on_path
	command -v uv >/dev/null 2>&1 ||
		die "uv was installed but this shell cannot find it. Open a new shell and run this again."
}

# Sets LINE to the command that installs git here, or to nothing.
git_line() {
	sudo="sudo "
	[ "$(id -u 2>/dev/null)" = 0 ] && sudo=""
	UPDATE=""
	if command -v apt-get >/dev/null 2>&1; then
		# A fresh image ships with no package lists, and install then finds no git.
		UPDATE="${sudo}apt-get update"
		LINE="${sudo}apt-get install -y git"
	elif command -v dnf >/dev/null 2>&1; then
		LINE="${sudo}dnf install -y git"
	elif command -v pacman >/dev/null 2>&1; then
		LINE="${sudo}pacman -S --needed --noconfirm git"
	elif command -v brew >/dev/null 2>&1; then
		LINE="brew install git"
	else
		LINE=""
	fi
}

ensure_git() {
	command -v git >/dev/null 2>&1 && return
	git_line
	[ -n "$LINE" ] || die "git is not installed, and cloning fleet needs it. No package manager this knows
(apt-get, dnf, pacman, brew) is here: install git, then run this again."
	say "git is not installed, and cloning fleet needs it. This installs it:"
	if [ -n "$UPDATE" ]; then
		say "  $UPDATE"
	fi
	say "  $LINE"
	confirm "Install git now?"
	case $? in
	0) ;;
	2) die "there is no terminal to ask. Run this yourself, or run the installer again with --yes:
  $LINE" ;;
	*) die "git was not installed. Install it, then run this again." ;;
	esac
	# LINE is words on purpose: a command and its arguments, no globs.
	# shellcheck disable=SC2086
	if [ -n "$UPDATE" ]; then
		$UPDATE </dev/null || die "refreshing the package lists failed; its error is above"
	fi
	# shellcheck disable=SC2086
	$LINE </dev/null || die "installing git failed; its error is above"
	command -v git >/dev/null 2>&1 || die "git was installed but this shell cannot find it. Open a new shell and run this again."
}

# Every fleet checkout this machine's thurbox has a lead session for, one path
# per line. A fleet may have named itself (`<mark> Mission Control - acme`), so
# the name is matched the way the pane matches it: an optional one-codepoint
# mark, the words, and optionally the mark and the fleet's own name.
lead_cwds() {
	command -v thurbox-cli >/dev/null 2>&1 || return 0
	thurbox-cli session list --json </dev/null 2>/dev/null |
		uv run --no-project --quiet python -c 'import json, sys
try:
    sessions = json.load(sys.stdin)
except ValueError:
    sessions = []
LEAD = "Mission Control"
for s in sessions:
    if not isinstance(s, dict):
        continue
    name = str(s.get("name", ""))
    mark, _, rest = name.partition(" ")
    body = rest if rest and len(mark.encode()) <= 4 else name
    if body == LEAD or body.startswith(LEAD + " \u00b7 "):
        print(s.get("cwd") or "")' 2>/dev/null
}

# Sets DIR and WHY. The lead thurbox already runs decides before the default.
pick_dir() {
	# A NAMED run is a fleet of its own by construction, so it never lands on
	# the fleet already installed here, which is what asking thurbox would do.
	if [ -n "$NAME" ]; then
		DIR="$HOME/fleet-$NAME"
		WHY="the default for a fleet named $NAME; set FLEET_DIR or pass --dir to choose another"
		return
	fi

	found=""
	first=""
	count=0
	old_ifs="$IFS"
	IFS='
'
	for cwd in $(lead_cwds); do
		[ -n "$cwd" ] && [ -f "$cwd/extension.toml.in" ] || continue
		count=$((count + 1))
		[ -n "$first" ] || first="$cwd"
		found="$found  $cwd
"
	done
	IFS="$old_ifs"

	if [ "$count" -gt 1 ]; then
		die "this machine already runs $count fleets, and nothing here says which one this is:
$found
Name the checkout you mean with --dir DIR, or start another fleet with --name NAME."
	fi
	if [ "$count" -eq 1 ]; then
		DIR="$first"
		WHY="the checkout your Mission Control session already opens"
		return
	fi
	DIR="$HOME/fleet"
	WHY="the default; set FLEET_DIR or pass --dir to choose another"
}

# Write NAME into the clone's own orchestration/fleet.conf, which is what makes
# it a fleet of its own. Nothing here renames a fleet that already named itself.
#
# Shell builtins only, as everywhere in this file: the machine this runs on is
# the one that has the least, and `sed`, `grep` and `mv` are not things to
# require for writing one line.
name_the_fleet() {
	[ -n "$NAME" ] || return 0
	conf="$DIR/orchestration/fleet.conf"
	if [ ! -f "$conf" ]; then
		mkdir -p "$DIR/orchestration" || die "could not create $DIR/orchestration"
		printf 'NAME=%s\n' "$NAME" >"$conf" || die "could not write $conf"
		say "This fleet is named '$NAME' (orchestration/fleet.conf)."
		return 0
	fi

	body=""
	current=""
	wrote=0
	while IFS= read -r line || [ -n "$line" ]; do
		case "$line" in
		NAME=*)
			current="${line#NAME=}"
			if [ -n "$current" ] && [ "$current" != "$NAME" ]; then
				die "$DIR is the fleet '$current', and this run says '$NAME'.
Renaming a fleet that is already running spawns a second lead and orphans the
first, so this does not do it. extension.toml.in's RENAMING header holds the two
sequences - one keeps the lead's conversation, one discards it."
			fi
			line="NAME=$NAME"
			wrote=1
			;;
		esac
		body="$body$line
"
	done <"$conf"
	[ "$current" = "$NAME" ] && return 0
	if [ "$wrote" = 0 ]; then
		body="${body}NAME=$NAME
"
	fi
	printf '%s' "$body" >"$conf" || die "could not write $conf"
	say "This fleet is named '$NAME' (orchestration/fleet.conf)."
}

# Clone into DIR, or bring the clone already there up to date without ever
# overwriting it.
checkout() {
	if [ ! -e "$DIR" ] || { [ -d "$DIR" ] && [ -z "$(ls -A "$DIR" 2>/dev/null)" ]; }; then
		mkdir -p "$(dirname "$DIR")" || die "could not create $(dirname "$DIR")"
		git clone --quiet --branch "$BRANCH" "$REPO" "$DIR" </dev/null ||
			die "git clone of $REPO failed; its error is above"
		say "Cloned $REPO ($BRANCH)."
		return
	fi

	# --show-cdup prints nothing at a repository's top level, and needs no path
	# comparison that a symlinked temp directory would get wrong.
	if [ ! -f "$DIR/extension.toml.in" ] || [ ! -f "$DIR/scripts/lib/queue.py" ] ||
		! cdup="$(git -C "$DIR" rev-parse --show-cdup 2>/dev/null)" || [ -n "$cdup" ]; then
		die "$DIR exists and is not a fleet clone, so it was left alone.
Set FLEET_DIR to an empty or new directory and run this again."
	fi

	current="$(git -C "$DIR" symbolic-ref --quiet --short HEAD 2>/dev/null)"
	if [ "$current" != "$BRANCH" ]; then
		die "$DIR is on '${current:-a detached HEAD}', not '$BRANCH'; not switching it.
Check out $BRANCH there yourself, then run this again."
	fi

	if ! git -C "$DIR" fetch --quiet origin "$BRANCH" </dev/null; then
		say "Could not fetch origin; carrying on with the checkout as it is."
		return
	fi

	behind="$(git -C "$DIR" rev-list --count "HEAD..origin/$BRANCH")"
	ahead="$(git -C "$DIR" rev-list --count "origin/$BRANCH..HEAD")"
	dirty="$(git -C "$DIR" status --porcelain --untracked-files=no)"

	if [ "$behind" -gt 0 ] && [ "$ahead" -gt 0 ]; then
		die "$DIR has diverged from origin/$BRANCH ($ahead ahead, $behind behind); not rebasing or resetting it.
Reconcile it by hand, then run this again."
	fi
	if [ "$behind" -gt 0 ] && [ -n "$dirty" ]; then
		die "$DIR is $behind commit(s) behind origin/$BRANCH and has uncommitted changes to tracked files; not fast-forwarding over them.
Commit or stash them, then run this again:
$dirty"
	fi
	if [ "$behind" -gt 0 ]; then
		git -C "$DIR" merge --ff-only --quiet "origin/$BRANCH" </dev/null ||
			die "the fast-forward of $DIR failed; its error is above"
		say "Fast-forwarded $behind commit(s) to $(git -C "$DIR" rev-parse --short HEAD)."
	elif [ "$ahead" -gt 0 ]; then
		say "The checkout is $ahead commit(s) ahead of origin/$BRANCH; left as it is."
	else
		say "The checkout is already current with origin/$BRANCH."
	fi
}

main() {
	dir_arg=""
	NAME="${FLEET_NAME:-}"
	YES=0
	[ "${FLEET_YES:-}" = 1 ] && YES=1
	while [ $# -gt 0 ]; do
		case "$1" in
		--dir)
			[ $# -ge 2 ] || die "--dir takes a directory" 2
			dir_arg="$2"
			shift
			;;
		--dir=*) dir_arg="${1#--dir=}" ;;
		--name)
			[ $# -ge 2 ] || die "--name takes a name" 2
			NAME="$2"
			shift
			;;
		--name=*) NAME="${1#--name=}" ;;
		-y | --yes) YES=1 ;;
		-h | --help)
			say "usage: sh install.sh [--dir DIR] [--name NAME] [--yes]   (settings: FLEET_DIR, FLEET_NAME, FLEET_REPO, FLEET_BRANCH, FLEET_YES)"
			exit 0
			;;
		*) die "unknown argument: $1 (usage: sh install.sh [--dir DIR] [--name NAME] [--yes])" 2 ;;
		esac
		shift
	done

	# The same grammar the renderer holds a name to, refused here so it is
	# refused before a clone rather than after one. It becomes a directory under
	# thurbox's config and a path segment inside a session name.
	if [ -n "$NAME" ]; then
		case "$NAME" in
		*[!A-Za-z0-9_-]* | [!A-Za-z0-9]*)
			die "--name $NAME is not a name a fleet can carry: letters, digits, '_' and '-',
starting with a letter or a digit." 2
			;;
		*__*)
			die "--name $NAME carries a double underscore, which is how this repo spells an
unrendered placeholder: a lead whose name holds one is read as a manifest nobody
rendered, and the reconciler stops waking it. Use a single '_' or a '-'." 2
			;;
		esac
		[ "${#NAME}" -le 24 ] || die "--name $NAME is longer than 24 characters" 2
	fi

	[ -n "${HOME:-}" ] || die "HOME is not set"
	REPO="${FLEET_REPO:-https://github.com/Thurbeen/fleet.git}"
	BRANCH="${FLEET_BRANCH:-main}"

	ensure_uv
	ensure_git

	if [ -n "$dir_arg" ]; then
		DIR="$dir_arg"
		WHY="--dir"
	elif [ -n "${FLEET_DIR:-}" ]; then
		DIR="$FLEET_DIR"
		WHY="FLEET_DIR"
	else
		pick_dir
	fi
	case "$DIR" in /*) ;; *) DIR="$PWD/$DIR" ;; esac

	say "fleet: installing into $DIR"
	say "       ($WHY)"
	say ""
	checkout
	DIR="$(cd "$DIR" && pwd -P)"
	name_the_fleet
	say ""

	set -- run --project "$DIR" fleet install
	[ "$YES" = 1 ] && set -- "$@" --yes
	# Its question is read from the terminal; with none, it refuses unless --yes.
	if has_tty; then
		uv "$@" </dev/tty
	else
		uv "$@" </dev/null
	fi
	exit $?
}

main "$@"
