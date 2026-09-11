#!/usr/bin/env bash
# Regenerate registry/repos.generated.yaml from live GitHub metadata.
#
# Enumerates every repository the authenticated user can reach — owned, via org
# membership, or as a collaborator — then keeps the ones belonging to an owner
# listed in registry/owners.txt, in that file's order. Add an org to owners.txt
# and its repos appear on the next sync; nothing else is hardcoded.
#
# EVERY `gh` ACCOUNT IS ASKED, not just the active one. A machine with two
# logins reaches two disjoint sets of repositories — one sees the personal org,
# another the employer's — and `user/repos` answers only for whichever account
# is active, so one pass wrote a map silently missing every owner the other
# logins reach. Worse, it was indistinguishable from a typo: both produce the
# `no accessible repos` warning below. Tokens are read BY NAME and never by
# switching — scripts/lib/gh-accounts.sh is the seam and argues the mechanism.
#
# This file is GENERATED — do not hand-edit it. It is also gitignored, like
# registry/owners.txt and everything else a running fleet writes; .gitignore
# says why. Curated, human-owned context lives in registry/context/<repo>.md,
# which this script never touches.
#
# Runs locally. Requires: gh (authenticated), jq. The listing uses the
# `user/repos` endpoint authenticated by your `gh auth` sessions — no PAT and
# no CI secret. The file it writes stays local: it is gitignored, so there is
# nothing to commit and nothing to push after a sync.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OWNERS_FILE="$REPO_ROOT/registry/owners.txt"
OUT="$REPO_ROOT/registry/repos.generated.yaml"

# shellcheck source=scripts/lib/gh-accounts.sh
. "$REPO_ROOT/scripts/lib/gh-accounts.sh"

die() {
	printf 'error: %s\n' "$1" >&2
	exit 1
}

command -v gh >/dev/null || die "gh not found"
command -v jq >/dev/null || die "jq not found"
[ -f "$OWNERS_FILE" ] || die "missing $OWNERS_FILE — it is yours and gitignored.
       Copy the tracked example and fill it in:
         cp registry/owners.example.txt registry/owners.txt"

# One owner per line; `#` starts a comment, blank lines are ignored.
owners=()
while IFS= read -r line || [ -n "$line" ]; do
	line="${line%%#*}"
	line="$(printf '%s' "$line" | tr -d '[:space:]')"
	if [ -n "$line" ]; then
		owners+=("$line")
	fi
done <"$OWNERS_FILE"

if [ "${#owners[@]}" -eq 0 ]; then
	die "no owners configured. Edit $OWNERS_FILE and uncomment (or add) your
       GitHub username and any orgs you belong to, one per line."
fi

# The host the map is built against. GitHub by definition — owners.txt is a
# list of GitHub owners, and a GitLab repo is targeted per task through the
# forge seam instead — but `GH_HOST` is what `gh api` itself would obey, so
# enumerating accounts for any other host would name logins whose tokens the
# listing below never uses.
MAP_HOST="${GH_HOST:-github.com}"
nl=$'\n'
ENDPOINT='user/repos?per_page=100&affiliation=owner,collaborator,organization_member'

# Every account `gh` holds for that host. An empty list is the documented
# fallback and not a failure: the active session is asked alone, which is what
# this script did before it asked more than one.
accounts=()
while IFS= read -r acct; do
	[ -n "$acct" ] && accounts+=("$acct")
done < <(gh_accounts "$MAP_HOST")

echo "fetching every accessible repository ..." >&2

# One listing per account, concatenated. An account whose token cannot be read
# or whose listing fails is NAMED and skipped — one expired login must cost its
# own repos and never the whole map, and a map that got thinner without saying
# why is the failure this whole file is about.
#
# EVERY account failing is a different thing, and it is not a thin map — it is
# no answer at all: an offline laptop, an outage, an SSO session that lapsed on
# all of them. Publishing that would write `owners: []` over the operator's
# only index. The single pass this replaced aborted there under `set -e`; the
# refusal below is that same floor, kept now that each listing is survivable.
raw=""
if [ "${#accounts[@]}" -eq 0 ]; then
	raw="$(gh_api_as "" --paginate "$ENDPOINT" --jq '.[]')"
else
	listed=0
	for acct in "${accounts[@]}"; do
		tok="$(gh_account_token "$MAP_HOST" "$acct")" || tok=""
		if [ -z "$tok" ]; then
			echo "warning: no usable token for gh account '$acct' — its repositories are not in this map" >&2
			continue
		fi
		if one="$(gh_api_as "$tok" --paginate "$ENDPOINT" --jq '.[]')"; then
			listed=$((listed + 1))
			raw="$raw$one$nl"
		else
			echo "warning: could not list repositories for gh account '$acct' — its repositories are not in this map" >&2
		fi
	done
	[ "$listed" -gt 0 ] || die "not one of the ${#accounts[@]} gh accounts above could be listed.
       $OUT is left exactly as it was rather than
       overwritten with an empty map."
fi

# One combined, de-duplicated array of all accessible repos, newest push first.
# A repository two accounts both reach is ONE repository: `full_name` is the
# identity, because the same repo listed twice would double every count below.
ALL="$(jq -s 'unique_by(.full_name) | sort_by(.pushed_at) | reverse' <<<"$raw")"

owners_json="$(printf '%s\n' "${owners[@]}" | jq -R . | jq -s .)"
total="$(jq --argjson o "$owners_json" \
	'[.[] | select(.owner.login as $l | $o | index($l))] | length' <<<"$ALL")"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

{
	echo "# GENERATED by scripts/sync-registry.sh — do not edit by hand."
	echo "# Every accessible repository under the owners in registry/owners.txt."
	echo "# Curated context lives in registry/context/<repo>.md."
	echo "# Regenerate: ./scripts/sync-registry.sh"
} >"$tmp"

if [ "$total" -eq 0 ]; then
	echo "owners: []" >>"$tmp"
else
	echo "owners:" >>"$tmp"
fi

for owner in "${owners[@]}"; do
	count="$(jq --arg o "$owner" '[.[] | select(.owner.login==$o)] | length' <<<"$ALL")"
	if [ "$count" -eq 0 ]; then
		echo "warning: no accessible repos for owner '$owner'" >&2
		continue
	fi

	{
		echo "  - name: $owner"
		echo "    repo_count: $count"
		echo "    repos:"
	} >>"$tmp"

	jq -r --arg o "$owner" '
    [.[] | select(.owner.login==$o)][] |
    "    - name: \(.name)\n" +
    "      url: \(.html_url)\n" +
    "      visibility: \(if .private then "private" else "public" end)\n" +
    "      role: \((.role_name // "-") | @json)\n" +
    "      archived: \(.archived)\n" +
    "      fork: \(.fork)\n" +
    "      language: \((.language // "-") | @json)\n" +
    "      default_branch: \((.default_branch // "-") | @json)\n" +
    "      pushed_at: \(.pushed_at)\n" +
    "      topics: [\((.topics // []) | join(", "))]\n" +
    "      description: \((.description // "") | @json)"
  ' <<<"$ALL" >>"$tmp"
done

# Owners that resolved to at least one repo — the ones actually emitted above.
emitted="$(jq --argjson o "$owners_json" \
	'[.[] | .owner.login] | unique | map(select(. as $l | $o | index($l))) | length' <<<"$ALL")"

{
	echo "totals:"
	echo "  repos: $total"
	echo "  owners: $emitted"
} >>"$tmp"

mv "$tmp" "$OUT"
trap - EXIT
echo "wrote $OUT ($total repos across $emitted owners)" >&2
