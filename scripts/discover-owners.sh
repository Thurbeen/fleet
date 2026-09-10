#!/usr/bin/env bash
# Who this operator's repos belong to, guessed from what is already on the
# machine — so onboarding asks one question instead of asking them to type a
# list they have written down nowhere.
#
# registry/owners.txt is the one input the repo map genuinely needs, and every
# part of it is already recorded somewhere: in the `gh` session, in the git
# configuration, and in the remotes of the clones the operator has been working
# in for years. This reads all three and prints candidates with the EVIDENCE for
# each, because a guess an operator cannot check is one they have to verify by
# hand anyway.
#
#   gh account     `gh api user` — the account the registry sync reads GitHub as
#   gh org         `gh api user/orgs` — orgs that session can see
#   git config     `github.user`, and a @users.noreply.github.com commit email
#   local clones   the `origin` of every git checkout under the roots scanned
#
# IT WRITES NOTHING. registry/owners.txt is the operator's file and stays
# theirs; this hands the candidates to whoever is about to ask them.
#
# GITHUB OWNERS ONLY, because that is what the file holds — the map is built
# from `gh`. GitLab clones found on the way are reported in their own section
# and belong in no owners file: a task targets a GitLab repo by path, through
# the forge seam in scripts/lib/forge.py.
#
# Usage:
#   scripts/discover-owners.sh                 # candidates with their evidence
#   scripts/discover-owners.sh --plain         # bare owner names, one per line
#   scripts/discover-owners.sh ~/work ~/oss    # scan these roots instead
#
# Exit: 0 when at least one candidate was found, 1 when none was — which on an
# authenticated machine means `gh auth status` is the thing to read first.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2
REPO_ROOT="$PWD"

PLAIN=0
ROOTS=()
while [ $# -gt 0 ]; do
	case "$1" in
	--plain) PLAIN=1 ;;
	-h | --help)
		sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
		exit 0
		;;
	-*)
		printf 'usage: %s [--plain] [root ...]\n' "$0" >&2
		exit 2
		;;
	*) ROOTS+=("$1") ;;
	esac
	shift
done

# Where clones live, when the caller named no root. The checkout's own parent
# first — a control plane is usually cloned beside the work it orchestrates —
# then the handful of directories people actually keep code in. A root that
# does not exist is skipped, so this list costs nothing on a machine that uses
# none of them.
if [ ${#ROOTS[@]} -eq 0 ]; then
	ROOTS=("$(dirname "$REPO_ROOT")" "$HOME/code" "$HOME/src" "$HOME/dev"
		"$HOME/projects" "$HOME/work" "$HOME/git" "$HOME/repos")
fi

declare -A SOURCES=() # owner -> "gh account, local clones (12)"
declare -A CLONES=()  # owner -> number of local checkouts found
declare -A GITLAB=()  # host/owner -> number of local checkouts found
ORDER=()              # first-seen order, so gh's answers lead

note() {
	local owner="$1" source="$2"
	if [ -z "${SOURCES[$owner]+x}" ]; then
		SOURCES["$owner"]="$source"
		ORDER+=("$owner")
	else
		case "${SOURCES[$owner]}" in
		*"$source"*) ;;
		*) SOURCES["$owner"]="${SOURCES[$owner]}, $source" ;;
		esac
	fi
}

# --- what gh already knows ----------------------------------------------------

gh_login=""
scope_hint=""
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
	gh_login="$(gh api user --jq .login 2>/dev/null)"
	[ -n "$gh_login" ] && note "$gh_login" "gh account"

	# An org list that comes back empty on an account with orgs is a SCOPE
	# problem and not an answer, and the two look identical from here — so the
	# remedy is printed rather than the emptiness being treated as fact.
	orgs="$(gh api user/orgs --jq '.[].login' 2>/dev/null)"
	if [ -n "$orgs" ]; then
		while IFS= read -r org; do
			[ -n "$org" ] && note "$org" "gh org"
		done <<<"$orgs"
	else
		scope_hint="gh listed no orgs. If you expect some, the token is missing a scope: gh auth refresh -s read:org"
	fi
else
	scope_hint="gh is not authenticated, so the account and its orgs could not be read: gh auth login"
fi

# --- what the git configuration remembers -------------------------------------
#
# Two fields carry a GitHub identity, and both are common on a machine whose
# `gh` was never logged in: `github.user`, which several tools set, and the
# noreply commit address, which is `12345+name@users.noreply.github.com`.

cfg_user="$(git config --get github.user 2>/dev/null)"
[ -n "$cfg_user" ] && note "$cfg_user" "git config github.user"

cfg_email="$(git config --get user.email 2>/dev/null)"
case "$cfg_email" in
*@users.noreply.github.com)
	handle="${cfg_email%@users.noreply.github.com}"
	handle="${handle##*+}"
	[ -n "$handle" ] && note "$handle" "git commit email"
	;;
esac

# --- what the clones on this disk say -----------------------------------------
#
# The strongest evidence there is: an owner the operator has actually been
# working in. Remotes are read from each checkout's config file rather than by
# running git in it, so a scan over hundreds of directories costs no processes.

scan_root() {
	local root="$1" cfg url rest host owner
	[ -d "$root" ] || return 0
	while IFS= read -r cfg; do
		[ -f "$cfg" ] || continue
		while IFS= read -r url; do
			# Host and owner out of any remote shape, INCLUDING an ssh host
			# ALIAS. `git@github-perso:Thurbeen/fleet.git` is what a machine
			# with two GitHub accounts looks like, and matching on the literal
			# `github.com` finds none of those clones — which on this very
			# checkout was every one of them.
			rest="${url#*://}"
			rest="${rest#*@}"
			host="${rest%%[:/]*}"
			owner="${rest#*[:/]}"
			owner="${owner%%/*}"
			[ -n "$owner" ] || continue
			case "$host" in
			*github*) CLONES["$owner"]=$((${CLONES[$owner]:-0} + 1)) ;;
			*gitlab*) GITLAB["$host/$owner"]=$((${GITLAB[$host/$owner]:-0} + 1)) ;;
			esac
		done < <(sed -n 's/^[[:space:]]*url[[:space:]]*=[[:space:]]*//p' "$cfg" 2>/dev/null)
	done < <(find "$root" -maxdepth 5 -type d -name .git -prune -print 2>/dev/null |
		sed 's|$|/config|')
}

seen_roots=""
for root in "${ROOTS[@]}"; do
	[ -d "$root" ] || continue
	real="$(cd "$root" && pwd -P)" || continue
	case "$seen_roots" in *"|$real|"*) continue ;; esac
	seen_roots="$seen_roots|$real|"
	scan_root "$real"
done

# Clone counts join the candidate list AFTER gh's answers, biggest first, so
# the order an operator reads is the order they would pick in.
if [ ${#CLONES[@]} -gt 0 ]; then
	while IFS= read -r line; do
		count="${line%% *}"
		owner="${line#* }"
		note "$owner" "local clones ($count)"
	done < <(for owner in "${!CLONES[@]}"; do printf '%s %s\n' "${CLONES[$owner]}" "$owner"; done | sort -rn)
fi

# --- output -------------------------------------------------------------------

if [ ${#ORDER[@]} -eq 0 ]; then
	if [ "$PLAIN" -eq 0 ]; then
		printf 'No candidate owners found.\n\n' >&2
		[ -n "$scope_hint" ] && printf '  %s\n' "$scope_hint" >&2
		printf '  Nothing on this machine names a GitHub owner: no gh session, no\n' >&2
		printf '  github.user, and no clone with a github.com remote under the roots\n' >&2
		printf '  scanned. Name a root to scan, or write registry/owners.txt by hand\n' >&2
		printf '  from registry/owners.example.txt.\n' >&2
	fi
	exit 1
fi

if [ "$PLAIN" -eq 1 ]; then
	printf '%s\n' "${ORDER[@]}"
	exit 0
fi

# Which candidates the operator has already committed to, so a re-run says
# "already there" instead of proposing the same list twice.
declare -A CONFIGURED=()
if [ -f registry/owners.txt ]; then
	while IFS= read -r line; do
		line="${line%%#*}"
		line="$(printf '%s' "$line" | tr -d '[:space:]')"
		[ -n "$line" ] && CONFIGURED["$line"]=1
	done <registry/owners.txt
fi

printf 'CANDIDATE OWNERS — for registry/owners.txt, which is a list of GITHUB owners\n\n'
for owner in "${ORDER[@]}"; do
	mark="  "
	[ -n "${CONFIGURED[$owner]+x}" ] && mark="* "
	printf '  %s%-22s %s\n' "$mark" "$owner" "${SOURCES[$owner]}"
done
[ ${#CONFIGURED[@]} -gt 0 ] && printf '\n  * already in registry/owners.txt\n'
[ -n "$scope_hint" ] && printf '\n  note: %s\n' "$scope_hint"

if [ ${#GITLAB[@]} -gt 0 ]; then
	printf '\nGITLAB CHECKOUTS — evidence, not owners\n\n'
	for key in "${!GITLAB[@]}"; do
		printf '  %-30s %s local clone(s)\n' "$key" "${GITLAB[$key]}"
	done
	printf '\n  These belong in no owners file: the map is built with gh. A task targets a\n'
	printf '  GitLab repo by host and path, through the seam in scripts/lib/forge.py, and\n'
	printf '  needs glab authenticated for that host.\n'
fi

exit 0
