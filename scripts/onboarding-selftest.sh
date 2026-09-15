#!/usr/bin/env bash
# Prove what onboarding's scripts claim, offline.
#
# Onboarding is the one part of this repo whose failures are invisible to
# everyone who already ran it. A dependency table that forgets a tool, an owner
# discovery that misses every clone on the disk, a pane placed into a layout
# that no longer parses — each is fine on the machine it was written on, and
# each costs a new operator the whole setup. None of it is reachable by the
# greps in `check.sh`, so it is driven here instead:
#
#   1. THE TABLE IS HONEST. `preflight.sh` names every missing REQUIRED tool in
#      one pass and exits non-zero; a missing recommended or gate tool is
#      reported and never fatal. A thurbox below the manifest's floor is
#      `stale`, not `ok` — the floor has one owner and this reads it from there.
#   2. EVERY GAP CARRIES A REMEDY. A row an operator cannot act on is the
#      restart-at-a-time setup this script exists to replace, and `--commands`
#      hands exactly the actionable ones to whoever said "yes, install them".
#   3. DISCOVERY READS THE MACHINE, NOT ONE SHAPE OF IT. `gh`, the git config
#      and the clones on disk are three sources, and the clone scan matches an
#      ssh host ALIAS — `git@github-perso:owner/repo` — because matching the
#      literal `github.com` found none of the clones on the machine this was
#      written on, including this checkout.
#   4. GITLAB IS EVIDENCE, NEVER AN OWNER. registry/owners.txt is read by `gh`;
#      a GitLab remote that reached it would make the map silently thinner.
#   5. PLACING THE PANE IS SAFE OR IT DOES NOT HAPPEN. Idempotent, backed up,
#      refused outright on a layout it cannot recognise, and the block it
#      writes carries the `panels.shown` guard and the slot the PANE declares.
#   6. EVERY `gh` ACCOUNT IS READ, everywhere that asks GitHub who the operator
#      is. A machine with several logins reaches a different set of
#      repositories per login, so asking only the active one described half the
#      machine — and in the map's case it did so with the same warning a
#      MISTYPED owner produces. Nothing switches the active account, and the
#      fallback to that one account alone stays the floor.
#   7. NEITHER AUTHENTICATION ROW IS AN EXIT CODE. `gh auth status` and
#      `glab auth status` are all-or-nothing, so one lapsed login among three,
#      and a gitlab.com the operator has never used, each reported a working
#      setup as broken. The `gh auth` row is decided per ACCOUNT and the
#      `glab auth` row per HOST, each naming what answered and each keeping the
#      bare status command as the fallback its seam documents.
#   8. THE MAP CATCHES UP AFTER THE FIRST RUN. `add-owner.sh` names what the
#      current accounts reach that the map does not, appends only what was
#      asked for — the file's comment header and its ORDER kept, a duplicate
#      refused — and then reports what MOVED rather than the whole map.
#
# HOW IT RUNS OFFLINE. Every probe is a stub on a sandboxed PATH — `gh`,
# `thurbox-cli`, `quota-axi` — and the PATH is built from scratch so that a
# tool the test says is missing really is missing, on a machine that has it
# installed. Nothing here reaches the network, the operator's thurbox, or the
# operator's layout.lua: the fixture at scripts/fixtures/layout/stock.lua is a
# copy of a stock arrangement and every edit happens to a copy of it.
#
# Usage: scripts/onboarding-selftest.sh   (also: ./scripts/check.sh onboarding)
#
# Requires: bash, python3 (with PyYAML) for §4a's map shape, and the coreutils
# the scripts under test use. No thurbox, no gh, no network.

# Every stub body below is source for ANOTHER shell, so `$1` in one has to
# survive into the file being written rather than expanding here.
# shellcheck disable=SC2016

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2
REPO="$PWD"

# EVERY git probe below is decided by the git ENVIRONMENT before any config
# file gets a say: GIT_CONFIG_GLOBAL replaces ~/.gitconfig outright, and
# GIT_CONFIG_COUNT/KEY_n/VALUE_n layer on top of everything, so a caller that
# sets either would answer §1f's signing case AND §2's fixture ~/.gitconfig.
# The same thing one seam over: §4 and §5 stub `gh` and decide which account
# answers by the token the stub is handed. `GH_TOKEN` or `GITHUB_TOKEN`
# short-circuits `gh_accounts` — both scripts then take the active-session path
# and the stub matches the operator's REAL token against fixture names — and
# `GH_HOST` moves which host's logins are enumerated, so the fixture list under
# `github.com` comes back empty. `GITLAB_HOST` answers §6a and §6d.
#
# scripts/lib/selftest-env.sh clears all of them ONCE, as soon as the temp
# directory exists, at the boundary every section shares. §1f's
# GIT_CONFIG_GLOBAL and §6b/§6c's GITLAB_HOST are per-command prefixes, which it
# does not touch.
# shellcheck source=scripts/lib/selftest-env.sh
. scripts/lib/selftest-env.sh

nl=$'\n'
failed=0
tmp=""
trap '[ -n "$tmp" ] && rm -rf "$tmp"' EXIT

pass() { printf '  \033[32mok\033[0m    %s\n' "$1"; }

fail() {
	printf '  \033[31mFAIL\033[0m  %s\n' "$1" >&2
	if [ $# -gt 1 ]; then printf '%s\n' "$2" | sed 's/^/          /' >&2; fi
	failed=1
}

expect() {
	local label="$1" want="$2" out="$3"
	case "$out" in
	*"$want"*) pass "$label" ;;
	*) fail "$label" "wanted: $want${nl}got:${nl}$out" ;;
	esac
}

refute() {
	local label="$1" unwanted="$2" out="$3"
	case "$out" in
	*"$unwanted"*) fail "$label" "did not want: $unwanted${nl}got:${nl}$out" ;;
	*) pass "$label" ;;
	esac
}

expect_exit() {
	local label="$1" want="$2" got="$3"
	if [ "$want" = "$got" ]; then pass "$label"; else fail "$label" "wanted exit $want, got $got"; fi
}

tmp="$(mktemp -d)"
selftest_isolate "$tmp/env"

# A PATH built from nothing, so "missing" means missing even here. Only the
# utilities the scripts under test actually call are linked in; a tool a test
# is about gets stubbed on top of that, and one it says is absent is simply
# never linked.
BASE="$tmp/base"
mkdir -p "$BASE"
for t in bash sed grep sort head tail cut tr awk find cat date mktemp cp rm rmdir mv ls dirname basename wc uname; do
	real="$(command -v "$t" 2>/dev/null)" && ln -sf "$real" "$BASE/$t"
done

# stub <dir> <name> <body>
stub() {
	local dir="$1" name="$2" body="$3"
	mkdir -p "$dir"
	# Unlink first. These directories are built by symlinking a previous one
	# in wholesale, so writing THROUGH a symlink would rewrite the stub in the
	# directory it points at — which is how the "thurbox below the floor" case
	# silently rewrote the "everything present" one.
	rm -f "$dir/$name"
	printf '#!/usr/bin/env bash\n%s\n' "$body" >"$dir/$name"
	chmod +x "$dir/$name"
}

printf '\n\033[1m§3 place-pane — safe, idempotent, or refused\033[0m\n'

FIXTURE="scripts/fixtures/layout/stock.lua"
[ -f "$FIXTURE" ] || fail "the stock layout fixture exists" "$FIXTURE is missing"

slot="$(sed -n 's/^local SLOT = "\(.*\)"$/\1/p' interface/fleet_queue.lua | head -1)"
[ -n "$slot" ] || fail "the pane declares a slot" "no SLOT in interface/fleet_queue.lua"

# No thurbox on this PATH: placement must work from --layout alone, which is
# also what proves it never needs the operator's real interface directory.
place="$tmp/bin-place"
mkdir -p "$place"
ln -sf "$BASE"/* "$place/" 2>/dev/null
real_lua="$(command -v lua 2>/dev/null)" && ln -sf "$real_lua" "$place/lua"

lay="$tmp/right/layout.lua"
mkdir -p "$(dirname "$lay")"
cp "$FIXTURE" "$lay"

out="$(PATH="$place" "$REPO/scripts/place-pane.sh" --check --layout "$lay" 2>&1)"
expect_exit "3a an unplaced pane is exit 1 from --check" 1 $?
expect "3a and it says what that costs" "draws nothing" "$out"

out="$(PATH="$place" "$REPO/scripts/place-pane.sh" --dry-run --layout "$lay" 2>&1)"
expect_exit "3a --dry-run exits 0" 0 $?
expect "3a --dry-run names the file it would edit" "$lay" "$out"
if cmp -s "$FIXTURE" "$lay"; then pass "3a --dry-run changed nothing"; else fail "3a --dry-run changed nothing"; fi

out="$(PATH="$place" "$REPO/scripts/place-pane.sh" --layout "$lay" 2>&1)"
expect_exit "3a placing it exits 0" 0 $?
expect "3a it says which side it chose" "right of the terminal" "$out"

out="$(PATH="$place" "$REPO/scripts/place-pane.sh" --check --layout "$lay" 2>&1)"
expect_exit "3a and --check now agrees" 0 $?

# 3b. RIGHT means after the centre column, which is the whole recommendation.
after="$(grep -n "columns\[#columns + 1\] = { slot = \"center\" }" "$lay" | head -1 | cut -d: -f1)"
here="$(grep -n "slot = \"$slot\"" "$lay" | head -1 | cut -d: -f1)"
if [ -n "$after" ] && [ -n "$here" ] && [ "$here" -gt "$after" ]; then
	pass "3b the default places the column to the RIGHT of the terminal"
else
	fail "3b the default places the column to the RIGHT of the terminal" "center at ${after:-?}, pane at ${here:-?}"
fi

# 3c. The guard, and the slot the pane itself declares.
expect "3c the block carries the panels.shown guard" "panels.shown(\"$slot\")" "$(cat "$lay")"
expect "3c and the slot the pane declares" "slot = \"$slot\"" "$(cat "$lay")"

# The slot has ONE spelling and it is the PANE's. Proved by renaming it in a
# copy of the pane and placing that: a writer carrying a slot name of its own
# would carve a column the renamed pane never fills, which is the rename that
# half-lands and looks installed.
renamed_pane="$tmp/renamed_queue.lua"
sed 's/^local SLOT = ".*"$/local SLOT = "renamedqueue"/' interface/fleet_queue.lua >"$renamed_pane"
renamed_lay="$tmp/renamed/layout.lua"
mkdir -p "$(dirname "$renamed_lay")"
cp "$FIXTURE" "$renamed_lay"
out="$(PATH="$place" "$REPO/scripts/place-pane.sh" --pane "$renamed_pane" --layout "$renamed_lay" 2>&1)"
expect_exit "3c a pane declaring another slot places exit 0" 0 $?
expect "3c the block carries the slot the PANE declares" 'slot = "renamedqueue"' "$(cat "$renamed_lay")"
refute "3c and never one the writer spells itself" "slot = \"$slot\"" "$(cat "$renamed_lay")"

# 3d. Idempotent, byte for byte.
before="$(cat "$lay")"
out="$(PATH="$place" "$REPO/scripts/place-pane.sh" --layout "$lay" 2>&1)"
expect_exit "3d a second run exits 0" 0 $?
expect "3d and says it changed nothing" "Already placed" "$out"
if [ "$before" = "$(cat "$lay")" ]; then pass "3d the file is untouched"; else fail "3d the file is untouched"; fi

# 3e. A backup, beside the original.
if ls "$(dirname "$lay")"/layout.lua.bak-* >/dev/null 2>&1; then
	pass "3e the edit left a backup beside the original"
else
	fail "3e the edit left a backup beside the original" "no layout.lua.bak-* in $(dirname "$lay")"
fi

# 3f. --left puts it before the centre column instead.
left="$tmp/left/layout.lua"
mkdir -p "$(dirname "$left")"
cp "$FIXTURE" "$left"
out="$(PATH="$place" "$REPO/scripts/place-pane.sh" --left --layout "$left" 2>&1)"
expect_exit "3f --left exits 0" 0 $?
after="$(grep -n "columns\[#columns + 1\] = { slot = \"center\" }" "$left" | head -1 | cut -d: -f1)"
here="$(grep -n "slot = \"$slot\"" "$left" | head -1 | cut -d: -f1)"
if [ -n "$after" ] && [ -n "$here" ] && [ "$here" -lt "$after" ]; then
	pass "3f --left places the column before the terminal"
else
	fail "3f --left places the column before the terminal" "center at ${after:-?}, pane at ${here:-?}"
fi

# 3g. A layout it cannot recognise is REFUSED, not rewritten.
odd="$tmp/odd/layout.lua"
mkdir -p "$(dirname "$odd")"
printf 'return function(ctx)\n  return { children = { { slot = "center" } } }\nend\n' >"$odd"
sum_before="$(cat "$odd")"
out="$(PATH="$place" "$REPO/scripts/place-pane.sh" --layout "$odd" 2>&1)"
expect_exit "3g an unrecognised arrangement is refused" 3 $?
expect "3g and the block is printed for the operator instead" "panels.shown(\"$slot\")" "$out"
if [ "$sum_before" = "$(cat "$odd")" ]; then pass "3g the file was not touched"; else fail "3g the file was not touched"; fi

# 3h. A layout that is not there at all.
out="$(PATH="$place" "$REPO/scripts/place-pane.sh" --layout "$tmp/nope/layout.lua" 2>&1)"
expect_exit "3h a missing layout.lua exits 2 and says thurbox writes one" 2 $?
expect "3h with the reason" "no layout.lua" "$out"

# 3j. A layout that carries the anchor but NOT the helpers the block calls.
# Lua resolves globals at CALL time, so the edited file would parse cleanly,
# survive the re-read, and take the whole interface down at the next launch.
trimmed="$tmp/trimmed/layout.lua"
mkdir -p "$(dirname "$trimmed")"
printf 'return function(ctx)\n  local columns = {}\n  columns[#columns + 1] = { slot = "center" }\n  return { columns = columns }\nend\n' >"$trimmed"
before="$(cat "$trimmed")"
out="$(PATH="$place" "$REPO/scripts/place-pane.sh" --layout "$trimmed" 2>&1)"
expect_exit "3j a layout missing the helpers the block calls is refused" 3 $?
expect "3j and it names the one it could not find" "panels.shown()" "$out"
if [ "$before" = "$(cat "$trimmed")" ]; then pass "3j the file was not touched"; else fail "3j the file was not touched"; fi

# 3k. A block the operator COMMENTED OUT — to find out whether it is what broke
# their interface — is not a placement. Reporting "already placed" there leaves
# the pane invisible and calls it success, which is the outcome this exists to
# prevent.
commented="$tmp/commented/layout.lua"
mkdir -p "$(dirname "$commented")"
sed "s|^\(.*slot = \"$slot\".*\)$|-- \1|" "$lay" >"$commented"
out="$(PATH="$place" "$REPO/scripts/place-pane.sh" --check --layout "$commented" 2>&1)"
expect_exit "3k a commented-out block is not a placement" 1 $?
expect "3k and --check says what that costs" "draws nothing" "$out"
out="$(PATH="$place" "$REPO/scripts/place-pane.sh" --layout "$commented" 2>&1)"
expect_exit "3k placing it over the comment exits 0" 0 $?
live="$(grep -v '^[[:space:]]*--' "$commented" | grep -c "slot = \"$slot\"")"
if [ "$live" = "1" ]; then
	pass "3k the file now carves exactly one live column for the slot"
else
	fail "3k the file now carves exactly one live column for the slot" "found $live"
fi

# 3i. The result still parses as Lua — the failure this script must never cause.
if command -v lua >/dev/null 2>&1; then
	if LAYOUT_PATH="$lay" lua -e 'assert(loadfile(os.getenv("LAYOUT_PATH")))' >/dev/null 2>&1; then
		pass "3i the edited layout still parses as Lua"
	else
		fail "3i the edited layout still parses as Lua" \
			"$(LAYOUT_PATH="$lay" lua -e 'print(select(2, loadfile(os.getenv("LAYOUT_PATH"))))' 2>&1)"
	fi
fi


printf '\n'
if [ "$failed" -eq 0 ]; then
	printf '\033[32monboarding selftest: everything passed\033[0m\n'
else
	printf '\033[31monboarding selftest: failures above\033[0m\n' >&2
fi
exit "$failed"
