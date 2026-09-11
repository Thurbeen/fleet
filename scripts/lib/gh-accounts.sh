# shellcheck shell=bash
# Every `gh` login this machine holds, not just the active one.
#
# SOURCED, never executed. Four callers share it and want the same three
# things — the account list, one account's token, one `gh api` call as that
# account — because each of them asks GitHub who the operator is:
#
#   scripts/sync-registry.sh    what the repo map is built from
#   scripts/discover-owners.sh  the one question onboarding asks the operator
#   scripts/add-owner.sh        what a newly authenticated login reaches
#   scripts/preflight.sh        the `gh auth` row, which is decided per ACCOUNT
#
# WHY THIS EXISTS. `gh api user/repos` and `gh api user/orgs` answer for
# whichever account is ACTIVE. A machine with a personal login and an
# employer's reaches two disjoint sets, so asking once described half the
# machine — and in the map's case the symptom was the `no accessible repos`
# warning a mistyped owner produces, which made the two indistinguishable.
#
# NOTHING HERE SWITCHES THE ACTIVE ACCOUNT. Tokens are read BY NAME with
# `gh auth token --user`, which hands one over without touching which account
# `gh` is pointing at — and `gh` is a tool the operator uses for everything
# else, so a sync that left it pointing somewhere new would be a side effect
# on their shell, not a read.

# gh_accounts <host>
#
# The login of every account `gh` holds for <host> whose credential still
# works, one per line, active account first as `gh` lists them.
#
# PRINTS NOTHING, successfully, when the list cannot be read. That is the
# documented fallback and not a failure: an older `gh` has no
# `auth status --json`, and a `GH_TOKEN`/`GITHUB_TOKEN` already in the
# environment overrides every stored account anyway, so enumerating them would
# describe repos the caller cannot reach. A caller that gets no lines asks the
# ACTIVE session — which is exactly what both callers did before they asked
# more than one.
#
# An account whose state is not `success` is named on stderr and skipped: one
# expired login must cost its own repos and never the caller's whole answer.
gh_accounts() {
	local host="$1" json login state
	command -v gh >/dev/null 2>&1 || return 0
	command -v jq >/dev/null 2>&1 || return 0
	[ -z "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ] || return 0
	json="$(gh auth status --json hosts 2>/dev/null)" || return 0
	[ -n "$json" ] || return 0

	while IFS=$'\t' read -r login state; do
		[ -n "$login" ] || continue
		if [ "$state" = "success" ]; then
			printf '%s\n' "$login"
		else
			printf "warning: gh account '%s' on %s is %s — skipped\n" \
				"$login" "$host" "$state" >&2
		fi
	done < <(jq -r --arg h "$host" \
		'.hosts[$h] // [] | .[] | [.login, .state] | @tsv' <<<"$json" 2>/dev/null)
}

# gh_account_token <host> <login>
#
# That account's token, or nothing. Reading it is not switching to it.
gh_account_token() {
	gh auth token --hostname "$1" --user "$2" 2>/dev/null
}

# gh_api_as <token> <gh api argument ...>
#
# One `gh api` call as one account. An EMPTY token means the active session,
# which is the fallback path above.
#
# The token is a prefix on `gh` ITSELF — an external command, so the
# assignment lives exactly as long as that one call and nothing is exported
# into the calling shell. Two shapes that look equivalent are not:
# `GH_TOKEN=x some_shell_function` leaves the assignment SET in the shell
# after the function returns, so the last account's token would still be in
# force for whatever ran next; and wrapping the call in `( export GH_TOKEN=x;
# ... )` scopes it correctly but throws away anything the callee collected in
# a variable, which is fatal for the caller that folds each account's answer
# into an array it keeps.
gh_api_as() {
	local token="$1"
	shift
	if [ -n "$token" ]; then
		GH_TOKEN="$token" gh api "$@"
	else
		gh api "$@"
	fi
}
