#!/bin/sh
# Install fleet: clone the control plane, check what it needs, install its
# thurbox extension.
#
#     curl -fsSL https://raw.githubusercontent.com/Thurbeen/fleet/main/install.sh | sh
#
# Or read it before running it, which is what keeping it short is for:
#
#     curl -fsSLo install.sh https://raw.githubusercontent.com/Thurbeen/fleet/main/install.sh
#     less install.sh
#     sh install.sh
#
# THREE STEPS, AND EACH ONE IS A SCRIPT THAT ALREADY EXISTS. This file is a
# bootstrap and owns no logic a clone does not already have:
#
#   1. the checkout     clone it, or fast-forward the one already there
#   2. prerequisites    scripts/preflight.sh — a missing REQUIRED one stops here
#   3. the extension    scripts/install-extension.sh, which also installs the
#                       queue pane PLUGIN
#
# Owners, the repo map and the reconciler stay with /fleet-onboarding: each is
# a question for the operator, and a pipe into `sh` is no place to ask one.
#
# IT INSTALLS NO DEPENDENCY. A package manager touches the machine outside the
# checkout, so preflight's install lines are printed and never run.
#
# IT PLACES NO PANE. The plugin install stays an unconditional side effect of
# step 3 because an installed, unplaced pane draws nothing and costs nothing,
# and because it is what makes a later yes one command. Putting it ON SCREEN
# is an edit to the operator's own layout.lua, and the first Mission Control
# session asks about that, once (FLEET.md, scripts/pane-ask.sh).
#
# WHERE THE CLONE GOES IS STICKY. The extension bakes this path in, and thurbox
# reuses the lead session by name without ever moving it, so a clone that
# moves later costs the lead its conversation. The directory is, in order:
#
#   --dir DIR, or FLEET_DIR     what the operator said
#   the lead's own checkout     a Mission Control session already opens one, so
#                               a second clone would be one thurbox never uses
#   ~/fleet                     a plain directory the operator can find and
#                               back up — the queue and the map live in it
#
# NOTHING EXISTING IS OVERWRITTEN. A directory that is not a fleet clone is
# refused. An existing clone is fast-forwarded and only fast-forwarded: on
# another branch, diverged from origin, or behind with uncommitted changes to
# tracked files, it is refused and left exactly as it was.
#
# POSIX sh, because `| sh` runs whatever /bin/sh is; the scripts it hands off
# to are bash. The body is one function called on the last line, so the shell
# has read all of it before any child runs — under `| sh` stdin IS the rest of
# this file, and a child that read it would swallow every line after itself.
#
# Settings, from the environment:
#   FLEET_DIR     where the checkout goes (see above)
#   FLEET_REPO    what to clone (default: https://github.com/Thurbeen/fleet.git)
#   FLEET_BRANCH  the branch to track (default: main)
#
# Usage: sh install.sh [--dir DIR]
# Exit: 0 installed, 1 refused or a step failed, 2 usage.

say() { printf '%s\n' "$*"; }

die() {
	printf '\nfleet install: %s\n' "$1" >&2
	exit "${2:-1}"
}

# Sets DIR and WHY. The lead thurbox already runs decides before the default
# does; both tools are optional here, since preflight has not run yet.
pick_dir() {
	if command -v thurbox-cli >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
		lead_cwd="$(thurbox-cli session list --json </dev/null 2>/dev/null |
			jq -r '[.[] | select(.name | endswith(" Mission Control")) | .cwd][0] // empty' 2>/dev/null)"
		if [ -n "$lead_cwd" ] && [ -f "$lead_cwd/scripts/install-extension.sh" ]; then
			DIR="$lead_cwd"
			WHY="the checkout your Mission Control session already opens"
			return
		fi
	fi
	DIR="$HOME/fleet"
	WHY="the default; set FLEET_DIR or pass --dir to choose another"
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

	if [ ! -f "$DIR/scripts/install-extension.sh" ] || [ ! -f "$DIR/extension.toml.in" ] ||
		[ "$(git -C "$DIR" rev-parse --show-toplevel 2>/dev/null)" != "$(cd "$DIR" && pwd -P)" ]; then
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
	while [ $# -gt 0 ]; do
		case "$1" in
		--dir)
			[ $# -ge 2 ] || die "--dir takes a directory" 2
			dir_arg="$2"
			shift
			;;
		--dir=*) dir_arg="${1#--dir=}" ;;
		-h | --help)
			say "usage: sh install.sh [--dir DIR]   (settings: FLEET_DIR, FLEET_REPO, FLEET_BRANCH)"
			exit 0
			;;
		*) die "unknown argument: $1 (usage: sh install.sh [--dir DIR])" 2 ;;
		esac
		shift
	done

	[ -n "${HOME:-}" ] || die "HOME is not set"
	command -v git >/dev/null 2>&1 ||
		die "git is not installed, and cloning fleet needs it. Install git with your package manager, then run this again."
	command -v bash >/dev/null 2>&1 ||
		die "bash is not installed, and every fleet script is bash. Install it with your package manager, then run this again."

	REPO="${FLEET_REPO:-https://github.com/Thurbeen/fleet.git}"
	BRANCH="${FLEET_BRANCH:-main}"

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
	say "Step 1/3  Checkout"
	checkout
	DIR="$(cd "$DIR" && pwd -P)"

	say ""
	say "Step 2/3  Prerequisites"
	if ! (cd "$DIR" && bash scripts/preflight.sh </dev/null); then
		say ""
		say "Stopped before the extension: a required dependency is missing, and"
		say "nothing was installed for you. The table above names each one; the"
		say "lines that install them here are:"
		say ""
		(cd "$DIR" && bash scripts/preflight.sh --commands --tier required </dev/null 2>/dev/null) |
			sed 's/^/  /'
		say ""
		say "Then run this again. The checkout is already in place:"
		say ""
		say "  sh $DIR/install.sh --dir $DIR"
		exit 1
	fi

	say ""
	say "Step 3/3  Thurbox extension"
	(cd "$DIR" && bash scripts/install-extension.sh </dev/null) ||
		die "the extension did not install; the output above says why. Nothing else was changed."

	cat <<EOF

fleet is installed in $DIR.

Next: open thurbox and start the Mission Control session. On its first
session it asks you, once, whether to put the queue pane on your screen.

  thurbox

Then run /fleet-onboarding in it for what this did not do: the GitHub owners
your map covers, the map itself, and the reconciler.
EOF
}

main "$@"
