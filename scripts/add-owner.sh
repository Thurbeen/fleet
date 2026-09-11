#!/usr/bin/env bash
# What this machine can reach that the map does not cover yet — and the one
# command that fixes it.
#
# WHY THIS EXISTS. Onboarding is a seven-step FIRST RUN. Every step of it
# converges on a re-run, but the thing that actually happens later is not a
# re-run: the operator gains an owner, a repository, or a whole `gh` account,
# and the map has to catch up. That meant hand-editing registry/owners.txt and
# remembering which script to run afterwards — and nothing anywhere told them
# what a newly authenticated account even reaches.
#
# So this is the incremental half, and it answers ONE question in two moods:
#
#   scripts/add-owner.sh                  what is new, and nothing is written
#   scripts/add-owner.sh <owner> ...      add those, then sync, then say what moved
#   scripts/add-owner.sh --all            add every owner above that is new
#
# The report groups owners BY THE ACCOUNT THAT REACHES THEM, because after a
# `gh auth login` that is the shape of the question: this account is now
# readable, it reaches these owners, this many are not in your map.
#
# IT LOGS NOBODY IN. `gh auth login` and `glab auth login` are interactive and
# the operator's. This reads what they have already done, offers, and syncs.
#
# GITLAB IS EVIDENCE, NEVER AN OWNER. An authenticated GitLab host changes what
# preflight reports and what a task can target through the forge seam in
# scripts/lib/forge.py — and contributes nothing to registry/owners.txt, which
# is a list of GITHUB owners read by `gh`. It is reported for exactly that
# reason: an operator who just authenticated one should be told what it did and
# did not change.
#
# THE FILE IS THE OPERATOR'S. Its comment header documents the format for
# whoever edits it by hand, and its ORDER is the order the generated map is
# emitted in — so a new owner is APPENDED and nothing is ever reshuffled.
#
# Options:
#   --all         add every owner the report marks new
#   --no-sync     write registry/owners.txt and stop, leaving the sync for later
#
# Exit: 0 when the report was printed or the owners were added, 1 when there was
# nothing this script could do — no owners file, a duplicate, a name that is not
# a GitHub owner — and 2 on a usage error.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2
REPO_ROOT="$PWD"
OWNERS_FILE="$REPO_ROOT/registry/owners.txt"
MAP="$REPO_ROOT/registry/repos.generated.yaml"

# The same two seams preflight reads, and for the same reason: every `gh`
# account rather than the active one, and GitLab per host rather than
# all-or-nothing. Each file's header owns its mechanism.
# shellcheck source=scripts/lib/gh-accounts.sh
. "$REPO_ROOT/scripts/lib/gh-accounts.sh"
# shellcheck source=scripts/lib/glab-hosts.sh
. "$REPO_ROOT/scripts/lib/glab-hosts.sh"

ADD_ALL=0
DO_SYNC=1
WANTED=()
while [ $# -gt 0 ]; do
	case "$1" in
	--all) ADD_ALL=1 ;;
	--no-sync) DO_SYNC=0 ;;
	-h | --help)
		sed -n '2,42p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
		exit 0
		;;
	-*)
		printf 'usage: %s [--all] [--no-sync] [owner ...]\n' "$0" >&2
		exit 2
		;;
	*) WANTED+=("$1") ;;
	esac
	shift
done

die() {
	printf 'error: %s\n' "$1" >&2
	exit 1
}

# THE MACHINE THIS IS NOT FOR. A clone with no owners file has not been
# onboarded, and writing one from here would be a first run done badly — the
# candidates come from three sources, not one, and which of them the map should
# cover is a question somebody has to be asked.
[ -f "$OWNERS_FILE" ] || die "no $OWNERS_FILE yet — this is the path for a map that already exists.
       A first run belongs to the fleet-onboarding skill, which asks:
         ./scripts/discover-owners.sh
         cp registry/owners.example.txt registry/owners.txt"

# One owner per line; `#` starts a comment, blank lines are ignored. The same
# parse sync-registry.sh does, because these two read the same file.
declare -A CONFIGURED=()
while IFS= read -r line || [ -n "$line" ]; do
	line="${line%%#*}"
	line="$(printf '%s' "$line" | tr -d '[:space:]')"
	[ -n "$line" ] && CONFIGURED["$line"]=1
done <"$OWNERS_FILE"

# --- what the accounts reach --------------------------------------------------

# GitHub, unless `GH_HOST` says otherwise — the variable `gh api` itself obeys,
# so enumerating logins for any other host would name tokens the calls below
# never use. scripts/sync-registry.sh and scripts/discover-owners.sh pick the
# host the same way.
GH_MAP_HOST="${GH_HOST:-github.com}"

ACCOUNT_ORDER=()
declare -A ACCOUNT_OWNERS=() # login -> "owner owner owner"
NEW_ORDER=()
declare -A NEW_SEEN=()

# One account's own login and its orgs, in that order. An EMPTY token means the
# active session, which is the fallback the seam documents.
ask_account() {
	local token="$1" login orgs org owners=""
	login="$(gh_api_as "$token" user --jq .login 2>/dev/null)" || login=""
	[ -n "$login" ] || return 1
	owners="$login"
	orgs="$(gh_api_as "$token" user/orgs --jq '.[].login' 2>/dev/null)" || orgs=""
	if [ -n "$orgs" ]; then
		while IFS= read -r org; do
			[ -n "$org" ] && owners="$owners $org"
		done <<<"$orgs"
	fi

	ACCOUNT_ORDER+=("$login")
	ACCOUNT_OWNERS["$login"]="$owners"
	for org in $owners; do
		if [ -z "${CONFIGURED[$org]+x}" ] && [ -z "${NEW_SEEN[$org]+x}" ]; then
			NEW_SEEN["$org"]=1
			NEW_ORDER+=("$org")
		fi
	done
	return 0
}

# `command -v gh` AND NOTHING MORE, for the reason discover-owners.sh argues:
# `gh auth status` exits 1 when an account on ANY host has trouble, so gating
# on it throws away every healthy login the moment one has lapsed — which is
# exactly the machine that gains accounts over time.
if command -v gh >/dev/null 2>&1; then
	accounts=()
	while IFS= read -r acct; do
		[ -n "$acct" ] && accounts+=("$acct")
	done < <(gh_accounts "$GH_MAP_HOST")

	if [ "${#accounts[@]}" -eq 0 ]; then
		ask_account "" || true
	else
		for acct in "${accounts[@]}"; do
			tok="$(gh_account_token "$GH_MAP_HOST" "$acct")" || tok=""
			if [ -z "$tok" ]; then
				printf "warning: no usable token for gh account '%s' — what it reaches is not below\n" "$acct" >&2
				continue
			fi
			ask_account "$tok" ||
				printf "warning: gh account '%s' could not say who it is — what it reaches is not below\n" "$acct" >&2
		done
	fi
fi

# --- the map, before and after ------------------------------------------------

# `owner/repo` for every repository in the generated map, in the map's own
# order. This is the only thing read out of it: the report is about what MOVED,
# and printing the map back is what this replaces.
map_pairs() {
	[ -f "$MAP" ] || return 0
	awk '/^  - name: / { owner = $3; next }
	     /^    - name: / { if (owner != "") print owner "/" $3 }' "$MAP"
}

# The two numbers under the map's `totals:` key, as one phrase. Four-space
# indents belong to a repository entry, so the two-space match reaches only the
# totals block.
map_totals() {
	local repos owners
	[ -f "$MAP" ] || {
		printf 'no map'
		return 0
	}
	repos="$(sed -n 's/^  repos: \(.*\)$/\1/p' "$MAP" | tail -1)"
	owners="$(sed -n 's/^  owners: \(.*\)$/\1/p' "$MAP" | tail -1)"
	printf '%s repos across %s owners' "${repos:-?}" "${owners:-?}"
}

# At most this many names, then a count. A hundred repositories arriving with a
# new employer is a line the operator scrolls past, not one they read.
SHOW=8
list_some() {
	local n=0 item out=""
	while IFS= read -r item; do
		[ -n "$item" ] || continue
		n=$((n + 1))
		[ "$n" -le "$SHOW" ] && out="${out:+$out, }$item"
	done
	[ "$n" -eq 0 ] && return 0
	[ "$n" -gt "$SHOW" ] && out="$out, +$((n - SHOW)) more"
	printf '%s' "$out"
}

# --- report mode --------------------------------------------------------------

report() {
	local login owners owner mark new_here line

	if [ "${#ACCOUNT_ORDER[@]}" -eq 0 ]; then
		printf 'No gh account answered, so there is nothing to compare your map against.\n\n'
		printf '  gh auth login          then run this again\n'
		printf '  ./scripts/preflight.sh reads the same accounts and says which are broken\n'
		return
	fi

	# The column is as wide as the widest login rather than a guessed number:
	# a `gh` login can be long, and one that overflows a fixed column takes the
	# alignment of every row with it.
	local width=8
	for login in "${ACCOUNT_ORDER[@]}"; do
		[ "${#login}" -gt "$width" ] && width="${#login}"
	done

	printf 'ACCOUNTS gh HOLDS — and the GitHub owners each one reaches\n\n'
	for login in "${ACCOUNT_ORDER[@]}"; do
		owners="${ACCOUNT_OWNERS[$login]}"
		line=""
		new_here=0
		for owner in $owners; do
			mark="*"
			if [ -z "${CONFIGURED[$owner]+x}" ]; then
				mark="+"
				new_here=$((new_here + 1))
			fi
			line="${line:+$line, }$mark $owner"
		done
		if [ "$new_here" -gt 0 ]; then
			printf '  %-*s  %s   (%d new)\n' "$width" "$login" "$line" "$new_here"
		else
			printf '  %-*s  %s\n' "$width" "$login" "$line"
		fi
	done
	printf '\n  * already in registry/owners.txt      + not in it yet\n'

	# An authenticated GitLab instance is the other thing an operator just did,
	# and the report has to say what it changed — which is what preflight
	# reports and what a task can target, and NOT this file.
	local hosts="" host
	while IFS= read -r host; do
		[ -n "$host" ] || continue
		glab_host_ok "$host" && hosts="${hosts:+$hosts, }$host"
	done < <(glab_hosts)
	if [ -n "$hosts" ]; then
		printf '\nGITLAB — evidence, never an owner\n\n'
		printf '  authenticated: %s\n\n' "$hosts"
		printf '  That changes what ./scripts/preflight.sh reports and what a task can target\n'
		printf '  through the forge seam in scripts/lib/forge.py. It adds no owner here:\n'
		printf '  registry/owners.txt is a list of GITHUB owners, read by gh.\n'
	fi

	printf '\n'
	if [ "${#NEW_ORDER[@]}" -eq 0 ]; then
		printf 'Nothing new: every owner these accounts reach is already in registry/owners.txt.\n'
		return
	fi

	local plural="s"
	[ "${#NEW_ORDER[@]}" -eq 1 ] && plural=""
	printf '%d owner%s not in registry/owners.txt: %s\n\n' \
		"${#NEW_ORDER[@]}" "$plural" "$(printf '%s\n' "${NEW_ORDER[@]}" | list_some)"
	printf '  ./scripts/add-owner.sh --all       add every one of them, then sync\n'
	printf '  ./scripts/add-owner.sh %-10s  or name the ones you want\n' "${NEW_ORDER[0]}"
}

if [ "$ADD_ALL" -eq 0 ] && [ "${#WANTED[@]}" -eq 0 ]; then
	report
	exit 0
fi

# --- add mode -----------------------------------------------------------------

if [ "$ADD_ALL" -eq 1 ]; then
	if [ "${#NEW_ORDER[@]}" -eq 0 ]; then
		printf 'Nothing new: every owner these accounts reach is already in registry/owners.txt.\n'
		exit 0
	fi
	WANTED+=("${NEW_ORDER[@]}")
fi

# EVERY name is checked before ANY is written. A run that appended two owners
# and then refused the third would leave the operator's file in a state they
# did not ask for and did not see.
declare -A ASKED=()
for owner in "${WANTED[@]}"; do
	# A name given twice on one command line is the same duplicate as one
	# already in the file, and appending it twice would put it in the map's
	# order twice.
	[ -z "${ASKED[$owner]+x}" ] ||
		die "'$owner' was named twice. Nothing was written."
	ASKED["$owner"]=1
	# What a GitHub owner is, and nothing wider. A GitLab group path, a
	# host-qualified name or a URL is not one, and a file that accepted one
	# would produce `no accessible repos for owner '<x>'` on every sync
	# forever — the warning that means a typo, made to mean two things again.
	case "$owner" in
	[A-Za-z0-9]*) ;;
	*) die "'$owner' is not a GitHub owner. This file holds GitHub usernames and orgs." ;;
	esac
	case "$owner" in
	*[!A-Za-z0-9-]*)
		die "'$owner' is not a GitHub owner — it holds usernames and orgs, one per line.
       A GitLab group is not one: a task targets a GitLab repository by host
       and path, through the seam in scripts/lib/forge.py."
		;;
	esac
	[ -z "${CONFIGURED[$owner]+x}" ] ||
		die "'$owner' is already in registry/owners.txt. Nothing was written."
done

# Appended, never inserted and never sorted: this file's order is the order
# sync-registry.sh emits owners in, so reshuffling it churns the generated map
# for nothing. A file whose last line has no newline would otherwise get the
# first new owner glued onto it.
[ -s "$OWNERS_FILE" ] && [ -n "$(tail -c 1 "$OWNERS_FILE")" ] && printf '\n' >>"$OWNERS_FILE"
printf '%s\n' "${WANTED[@]}" >>"$OWNERS_FILE"
printf 'added to registry/owners.txt: %s\n' "$(printf '%s\n' "${WANTED[@]}" | list_some)"

if [ "$DO_SYNC" -eq 0 ]; then
	printf '\nNot synced, as asked. The map is stale until:\n\n  ./scripts/sync-registry.sh\n'
	exit 0
fi

# --- sync, and report what MOVED ----------------------------------------------

before_pairs="$(mktemp)"
after_pairs="$(mktemp)"
trap 'rm -f "$before_pairs" "$after_pairs"' EXIT
had_map=0
[ -f "$MAP" ] && had_map=1
map_pairs >"$before_pairs"
before_totals="$(map_totals)"

printf '\n'
"$REPO_ROOT/scripts/sync-registry.sh" || die "the sync failed — registry/owners.txt keeps the owners just added,
       so re-running ./scripts/sync-registry.sh is all that is left to do."

map_pairs >"$after_pairs"

printf '\nMAP CHANGED\n'
printf '  owners added        %s\n' "$(printf '%s\n' "${WANTED[@]}" | list_some)"

# `grep -Fxv -f` and not `comm`, because these are in the map's own order and
# sorting them to compare would cost that order for nothing.
gained="$(grep -Fxv -f "$before_pairs" "$after_pairs" | list_some)"
lost="$(grep -Fxv -f "$after_pairs" "$before_pairs" | list_some)"
[ -n "$gained" ] && printf '  repositories gained %s\n' "$gained"
[ -n "$lost" ] && printf '  repositories lost   %s\n' "$lost"
[ -n "$gained" ] || [ -n "$lost" ] || printf '  repositories        none gained, none lost\n'

after_totals="$(map_totals)"
if [ "$had_map" -eq 1 ]; then
	printf '  totals              %s (was %s)\n' "$after_totals" "$before_totals"
else
	printf '  totals              %s (the map did not exist before this)\n' "$after_totals"
fi
printf '\nA repository that is in the map and still unexplained belongs in\nregistry/context/<repo>.md, which is where the judgement about a project lives.\n'
