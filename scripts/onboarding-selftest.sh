#!/usr/bin/env bash
# Prove what onboarding's three scripts claim, offline.
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
#   6. EVERY `gh` ACCOUNT IS READ, in both places that ask GitHub who the
#      operator is. A machine with several logins reaches a different set of
#      repositories per login, so asking only the active one described half the
#      machine — and in the map's case it did so with the same warning a
#      MISTYPED owner produces. Nothing switches the active account, and the
#      fallback to that one account alone stays the floor.
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
# Requires: bash, and the coreutils the scripts under test use. No thurbox, no
# gh, no network.

# Every stub body below is source for ANOTHER shell, so `$1` in one has to
# survive into the file being written rather than expanding here.
# shellcheck disable=SC2016

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2
REPO="$PWD"

# EVERY git probe below is decided by the git ENVIRONMENT before any config
# file gets a say: GIT_CONFIG_GLOBAL replaces ~/.gitconfig outright, and
# GIT_CONFIG_COUNT/KEY_n/VALUE_n layer on top of everything. A caller that sets
# either — `GIT_CONFIG_GLOBAL=/tmp/nosign ./scripts/check.sh onboarding` is how
# this repo is gated on a machine whose signing is misconfigured — would
# otherwise decide this script's answers for it: §1f's signing case AND §2's
# fixture ~/.gitconfig both. Cleared ONCE, here, at the boundary they share.
# §1f sets GIT_CONFIG_GLOBAL as a per-command prefix, which this does not touch.
unset GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM
for ((_i = 0; _i <= ${GIT_CONFIG_COUNT:-0}; _i++)); do
	unset "GIT_CONFIG_KEY_$_i" "GIT_CONFIG_VALUE_$_i"
done
unset GIT_CONFIG_COUNT _i

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

# A PATH built from nothing, so "missing" means missing even here. Only the
# utilities the scripts under test actually call are linked in; a tool a test
# is about gets stubbed on top of that, and one it says is absent is simply
# never linked.
BASE="$tmp/base"
mkdir -p "$BASE"
for t in bash sed grep sort head tail cut tr find cat date mktemp cp rm rmdir mv ls dirname basename wc uname; do
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

printf '\n\033[1m§1 preflight — the table is honest, and every gap carries a remedy\033[0m\n'

floor="$(sed -n 's/^min_thurbox_version *= *"\(.*\)"/\1/p' extension.toml.in | head -1)"
[ -n "$floor" ] || fail "the manifest declares a thurbox floor" "extension.toml.in has no min_thurbox_version"

# --- 1a. everything present ---------------------------------------------------
full="$tmp/bin-full"
mkdir -p "$full"
ln -sf "$BASE"/* "$full/" 2>/dev/null
stub "$full" gh 'case "$1" in
  --version) echo "gh version 2.100.0" ;;
  auth) exit 0 ;;
  api) case "$2" in user) echo "octo" ;; user/orgs) echo "acme" ;; esac ;;
esac'
# `--version` answered WITHOUT reading stdin: a stub that cats first blocks
# forever when the probe asks it for a version.
stub "$full" jq 'case "$1" in --version) echo "jq-1.7" ;; *) cat >/dev/null; echo "[]" ;; esac'
stub "$full" python3 'case "$1" in
  --version) echo "Python 3.13.0" ;;
  -c) case "$2" in *yaml*) echo "6.0.1" ;; esac ;;
esac'
stub "$full" thurbox-cli "echo 'thurbox-cli $floor'"
stub "$full" git 'echo "git version 2.43.0"'
stub "$full" quota-axi 'echo "0.1.41"'
stub "$full" lua 'echo "Lua 5.4.4"'
stub "$full" shellcheck 'echo "version: 0.10.0"'
stub "$full" rumdl 'echo "rumdl 0.2.0"'
stub "$full" prek 'echo "prek 0.5.0"'
stub "$full" glab 'case "$1" in auth) exit 0 ;; *) echo "glab 1.60.0" ;; esac'
# A package manager, so the remedies this machine prints are the runnable kind
# rather than a URL. Which manager is not the point; that a gap turns into a
# command is.
stub "$full" apt-get 'exit 0'

out="$(PATH="$full" "$REPO/scripts/preflight.sh" 2>&1)"
code=$?
expect_exit "1a a fully equipped machine exits 0" 0 "$code"
expect "1a it says so in one line" "Every required dependency is present" "$out"

out="$(PATH="$full" "$REPO/scripts/preflight.sh" --commands 2>&1)"
if [ -z "$out" ]; then
	pass "1a --commands prints nothing when there is nothing to install"
else
	fail "1a --commands prints nothing when there is nothing to install" "$out"
fi

# --- 1b. two required tools missing -------------------------------------------
# gh and jq are simply not linked in. Everything else stays, so the report has
# to name BOTH and stop — not the first one it tripped over.
part="$tmp/bin-part"
mkdir -p "$part"
for f in "$full"/*; do
	case "$(basename "$f")" in gh | jq) continue ;; esac
	ln -sf "$f" "$part/$(basename "$f")"
done

out="$(PATH="$part" "$REPO/scripts/preflight.sh" 2>&1)"
code=$?
expect_exit "1b a missing required tool is a non-zero exit" 1 "$code"
expect "1b it names gh" "missing" "$out"
expect "1b and jq in the same pass" "jq" "$out"
expect "1b gh is named too" "gh" "$out"
expect "1b it says how many are missing" "2 required dependencies missing" "$out"
expect "1b every gap carries an install line" "install:" "$out"

cmds="$(PATH="$part" "$REPO/scripts/preflight.sh" --commands 2>&1)"
if [ -n "$cmds" ]; then
	pass "1b --commands hands over the lines to run"
else
	fail "1b --commands hands over the lines to run" "printed nothing"
fi
refute "1b --commands never prints a 'see <url>' as if it were a command" "see http" "$cmds"

# 1e. "install the required ones only" has to be a command, not a judgement
# about which lines to copy out of a longer list. The exit code stays honest
# under a filter: a required gap is one whatever the caller asked to see.
thin="$tmp/bin-thin"
mkdir -p "$thin"
for f in "$full"/*; do
	case "$(basename "$f")" in gh | quota-axi) continue ;; esac
	ln -sf "$f" "$thin/$(basename "$f")"
done
cmds="$(PATH="$thin" "$REPO/scripts/preflight.sh" --commands --tier required 2>&1)"
refute "1e --tier required leaves the recommended lines out" "quota-axi" "$cmds"
out="$(PATH="$thin" "$REPO/scripts/preflight.sh" --tier gate 2>&1)"
expect_exit "1e and a filtered view still fails on a required gap" 1 $?
refute "1e --tier gate shows only that tier" "RECOMMENDED" "$out"

# --- 1c. thurbox below the floor ----------------------------------------------
old="$tmp/bin-old"
mkdir -p "$old"
ln -sf "$full"/* "$old/" 2>/dev/null
stub "$old" thurbox-cli 'echo "thurbox-cli 0.0.1"'

out="$(PATH="$old" "$REPO/scripts/preflight.sh" 2>&1)"
code=$?
expect_exit "1c a thurbox below the floor fails preflight" 1 "$code"
expect "1c it is reported stale, not missing" "stale" "$out"
expect "1c and the remedy names the floor from the manifest" "$floor" "$out"

# --- 1d. recommended and gate gaps are not fatal ------------------------------
lean="$tmp/bin-lean"
mkdir -p "$lean"
for f in "$full"/*; do
	case "$(basename "$f")" in quota-axi | glab | rumdl | shellcheck | prek | lua) continue ;; esac
	ln -sf "$f" "$lean/$(basename "$f")"
done

out="$(PATH="$lean" "$REPO/scripts/preflight.sh" 2>&1)"
code=$?
expect_exit "1d a missing recommended or gate tool is reported, never fatal" 0 "$code"
expect "1d quota-axi is named" "quota-axi" "$out"
expect "1d and it says what degrades without it" "refuel" "$out"
expect "1d the gate tools are their own tier" "GATE" "$out"

# --- 1f. the config that fails the gate for reasons the gate never mentions ---
# Signing is not a tool, and the trap is that it can be configured for the
# operator's code tree and nowhere else: committing in this checkout works,
# committing in the sandbox a selftest builds does not. So the probe must ask
# git from OUTSIDE the checkout, and this proves it does by answering only
# through GIT_CONFIG_GLOBAL.
sign="$tmp/bin-sign"
mkdir -p "$sign"
ln -sf "$full"/* "$sign/" 2>/dev/null
rm -f "$sign/git"
ln -sf "$(command -v git)" "$sign/git"

printf '[commit]\n\tgpgsign = true\n' >"$tmp/gitconfig-nokey"
out="$(GIT_CONFIG_GLOBAL="$tmp/gitconfig-nokey" PATH="$sign" "$REPO/scripts/preflight.sh" 2>&1)"
expect "1f signing on with no key is reported" "commit signing" "$out"
expect "1f and it names what it costs" "sandbox" "$out"
expect "1f with a remedy that is either half of the fix" "commit.gpgsign false" "$out"

# The colour codes come OUT before the refute: the table writes
# `missing\e[0m  commit signing`, so a refute against the plain words could
# never fire and the case it names would be untested.
printf '[commit]\n\tgpgsign = true\n[user]\n\tsigningkey = ~/.ssh/k.pub\n' >"$tmp/gitconfig-key"
esc=$'\033'
out="$(GIT_CONFIG_GLOBAL="$tmp/gitconfig-key" PATH="$sign" "$REPO/scripts/preflight.sh" --tier gate 2>&1 |
	sed "s/${esc}\\[[0-9;]*m//g")"
expect "1f the signing row is reported at all" "commit signing" "$out"
refute "1f signing with a key is not reported as a gap" "missing  commit signing" "$out"

printf '\n\033[1m§2 discover-owners — three sources, and GitLab is not one of them\033[0m\n'

# A HOME of its own: the git config probes read the operator's real one
# otherwise, and this machine's answer is not a test.
home="$tmp/home"
mkdir -p "$home"
printf '[user]\n\temail = 4242+octo@users.noreply.github.com\n' >"$home/.gitconfig"

# A clone tree with every remote shape that matters: an ssh host ALIAS, a plain
# https GitHub remote, a GitLab one, an ssh:// URL carrying a PORT, a fork with
# a second remote, and two checkouts nobody works in — a vim plugin and an npm
# package — that a home-directory scan walks straight into.
tree="$tmp/clones"
mkdir -p "$tree/a/.git" "$tree/b/.git" "$tree/c/.git" "$tree/d/.git" "$tree/e/.git" \
	"$tree/f/.git" "$tree/.vim/plugged/vim-thing/.git" "$tree/g/node_modules/pkg/.git"
printf '[remote "origin"]\n\turl = git@github-perso:aliased-owner/thing.git\n' >"$tree/a/.git/config"
printf '[remote "origin"]\n\turl = https://github.com/plain-owner/thing.git\n' >"$tree/b/.git/config"
printf '[remote "origin"]\n\turl = git@gitlab.example.com:group/thing.git\n' >"$tree/c/.git/config"
printf '[remote "origin"]\n\turl = git@github-perso:aliased-owner/other.git\n' >"$tree/d/.git/config"
# A fork: origin is the operator's, upstream is a project they have no repos
# under. Only origin names an owner.
printf '[remote "origin"]\n\turl = git@github.com:fork-owner/linux.git\n[remote "upstream"]\n\turl = https://github.com/upstream-owner/linux.git\n' \
	>"$tree/e/.git/config"
# GitHub's own SSH-over-HTTPS workaround for a firewalled network. The `:443`
# is a PORT, and a parser that splits the host from the path on `:` makes it
# an owner.
printf '[remote "origin"]\n\turl = ssh://git@ssh.github.com:443/porty-owner/thing.git\n' \
	>"$tree/f/.git/config"
printf '[remote "origin"]\n\turl = https://github.com/plugin-author/vim-thing.git\n' \
	>"$tree/.vim/plugged/vim-thing/.git/config"
printf '[remote "origin"]\n\turl = https://github.com/npm-author/pkg.git\n' \
	>"$tree/g/node_modules/pkg/.git/config"

disc="$tmp/bin-disc"
mkdir -p "$disc"
ln -sf "$BASE"/* "$disc/" 2>/dev/null
ln -sf "$(command -v git)" "$disc/git"
stub "$disc" gh 'case "$1" in
  auth) exit 0 ;;
  api) case "$2" in user) echo "octo" ;; user/orgs) printf "acme\nbeta\n" ;; esac ;;
esac'

out="$(cd "$tmp" && HOME="$home" PATH="$disc" "$REPO/scripts/discover-owners.sh" "$tree" 2>&1)"
code=$?
expect_exit "2a discovery exits 0 when it found something" 0 "$code"
expect "2a the gh account is a candidate" "octo" "$out"
expect "2a so are its orgs" "acme" "$out"
expect "2a each candidate carries its evidence" "gh account" "$out"
expect "2b an ssh host ALIAS clone is found" "aliased-owner" "$out"
expect "2b with its clone count" "local clones (2)" "$out"
expect "2b a plain https remote too" "plain-owner" "$out"
expect "2b an ssh:// URL with a port names the owner, not the port" "porty-owner" "$out"
expect "2b a fork's origin names its owner" "fork-owner" "$out"
expect "2d a GitLab remote is reported as evidence" "GITLAB CHECKOUTS" "$out"
expect "2d under the host it lives on" "gitlab.example.com/group" "$out"

# The candidate table is what an operator copies into registry/owners.txt, so
# what must NOT be in it is asserted against that section alone — the GitLab
# evidence below it names the same strings on purpose.
candidates="$(printf '%s\n' "$out" | sed -n '/^CANDIDATE OWNERS/,/^GITLAB CHECKOUTS/p')"
refute "2b a port is never an owner" "443" "$candidates"
refute "2b a fork's upstream is not an owner the operator has repos under" "upstream-owner" "$candidates"
refute "2b a vendored vim plugin's author is not an owner" "plugin-author" "$candidates"
refute "2b nor is an npm package's" "npm-author" "$candidates"
refute "2d a GitLab namespace never reaches the candidate list" "group" "$candidates"

# --- 2e. no gh at all: the git config still answers ---------------------------
nogh="$tmp/bin-nogh"
mkdir -p "$nogh"
ln -sf "$BASE"/* "$nogh/" 2>/dev/null
ln -sf "$(command -v git)" "$nogh/git"

out="$(cd "$tmp" && HOME="$home" PATH="$nogh" "$REPO/scripts/discover-owners.sh" "$tree" 2>&1)"
code=$?
expect_exit "2e a machine with no gh session still discovers owners" 0 "$code"
expect "2e the noreply commit email names the account" "octo" "$out"
expect "2e and the missing gh session is said out loud" "gh auth login" "$out"

# --- 2f. nothing anywhere -----------------------------------------------------
bare="$tmp/emptyhome"
mkdir -p "$bare" "$tmp/noclones"
out="$(cd "$tmp" && HOME="$bare" PATH="$nogh" "$REPO/scripts/discover-owners.sh" "$tmp/noclones" 2>&1)"
code=$?
expect_exit "2f a machine that says nothing exits 1 rather than inventing an owner" 1 "$code"
expect "2f and points at the file to write by hand" "owners.example.txt" "$out"

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

printf '\n\033[1m§4 sync-registry — the map covers every gh account, not just the active one\033[0m\n'

# §4 AND §5 ARE APPENDED, not filed beside the section about the same script.
# New sections go at the END of this file so that two people adding cases at
# the same time do not interleave in the diff.
#
# A machine with more than one `gh` login reaches a DIFFERENT set of
# repositories per login: one sees the personal org, another the employer's, a
# third a client's. `user/repos` answers for whichever account is ACTIVE, so a
# single pass wrote a map missing every owner the other logins reach — and its
# only symptom was the `no accessible repos` warning that a MISTYPED owner
# produces. The two were indistinguishable, which is why the fix has to keep
# that warning meaning exactly one thing: §4b holds both halves of it.
#
# Run against a COPY of the script in a sandbox repo root. `sync-registry.sh`
# resolves its paths from its own location and OVERWRITES
# registry/repos.generated.yaml, so driving the real one here would rewrite
# the operator's map with fixture data.
sandbox="$tmp/registry-repo"
mkdir -p "$sandbox/scripts/lib" "$sandbox/registry"
cp scripts/sync-registry.sh "$sandbox/scripts/"
cp scripts/lib/gh-accounts.sh "$sandbox/scripts/lib/"

# `typo-owner` is in the list and no account reaches it — the typo signal,
# which must survive a change whose whole point is that it now means only that.
printf '%s\n' '# fixture' octo acme-org client-org employer-org typo-owner \
	>"$sandbox/registry/owners.txt"

# One repo object per line, keyed by the token the caller presents. Each
# account reaches a disjoint set except `acme-org/shared`, which two of them
# see — the dedup case.
repo_json() {
	printf '{"full_name":"%s/%s","name":"%s","html_url":"https://github.com/%s/%s",' \
		"$1" "$2" "$2" "$1" "$2"
	printf '"owner":{"login":"%s"},"private":false,"role_name":"admin","archived":false,' "$1"
	printf '"fork":false,"language":"Rust","default_branch":"main","pushed_at":"2026-09-01T00:00:00Z",'
	printf '"topics":[],"description":"fixture"}\n'
}
{
	repo_json octo own-repo
	repo_json acme-org tool
	repo_json acme-org shared
} >"$tmp/repos-octo.json"
{
	repo_json client-org client-thing
	repo_json acme-org shared
} >"$tmp/repos-client.json"
repo_json employer-org work-thing >"$tmp/repos-worky.json"

# The account list `gh auth status --json hosts` answers with. Five logins and
# three shapes: three that work, one whose credential has expired, and one
# that reports success but whose token cannot be read back.
cat >"$tmp/hosts.json" <<'JSON'
{"hosts":{"github.com":[
 {"state":"success","active":true,"host":"github.com","login":"octo"},
 {"state":"success","active":false,"host":"github.com","login":"client"},
 {"state":"success","active":false,"host":"github.com","login":"worky"},
 {"state":"success","active":false,"host":"github.com","login":"tokenless"},
 {"state":"timeout","active":false,"host":"github.com","login":"expired"}
]}}
JSON

regbin="$tmp/bin-registry"
mkdir -p "$regbin"
ln -sf "$BASE"/* "$regbin/" 2>/dev/null
real_jq="$(command -v jq 2>/dev/null)" && ln -sf "$real_jq" "$regbin/jq"

# `gh auth status --json hosts` is the account list, and `gh auth token --user`
# hands over one account's token WITHOUT switching the active one — which is
# the whole mechanism: the sync can ask every account and still leave the
# operator's `gh` pointing exactly where it found it.
stub "$regbin" gh 'case "$1 $2" in
"auth status")
  case "$*" in
  *--json*) cat "$FIXTURES/hosts.json" ;;
  *) exit 0 ;;
  esac
  ;;
"auth token")
  for a in "$@"; do
    case "$a" in
    octo | client | worky) echo "tok-$a"; exit 0 ;;
    esac
  done
  exit 1
  ;;
"api --paginate")
  case "${GH_TOKEN:-tok-octo}" in
  tok-octo) cat "$FIXTURES/repos-octo.json" ;;
  tok-client) cat "$FIXTURES/repos-client.json" ;;
  tok-worky) cat "$FIXTURES/repos-worky.json" ;;
  *) exit 1 ;;
  esac
  ;;
*) echo "unexpected gh call: $*" >&2; exit 9 ;;
esac'

out="$(FIXTURES="$tmp" PATH="$regbin" "$sandbox/scripts/sync-registry.sh" 2>&1)"
code=$?
map="$sandbox/registry/repos.generated.yaml"
expect_exit "4a a sync across five accounts exits 0" 0 "$code"

# 4a. Every owner ANY account reaches is in the map — the whole point.
for owner in octo acme-org client-org employer-org; do
	if grep -q "^  - name: $owner\$" "$map" 2>/dev/null; then
		pass "4a the map carries owner '$owner', reached by one of the accounts"
	else
		fail "4a the map carries owner '$owner', reached by one of the accounts" \
			"sync said:${nl}$out"
	fi
done

# 4b. The `no accessible repos` warning now means ONE thing. It is still
# printed for an owner nothing reaches — that is the typo signal and the only
# way a mistyped owner is ever noticed — and printed for no owner that some
# account can see, which is what it used to do for every owner outside the
# active login.
expect "4b an owner no account reaches is still reported" \
	"no accessible repos for owner 'typo-owner'" "$out"
for owner in octo acme-org client-org employer-org; do
	refute "4b '$owner' is not reported unreachable — some account reaches it" \
		"no accessible repos for owner '$owner'" "$out"
done

# 4c. A repo two accounts both reach is one repo.
shared="$(grep -c '^    - name: shared$' "$map" 2>/dev/null || true)"
if [ "$shared" = "1" ]; then
	pass "4c a repo two accounts both reach appears once"
else
	fail "4c a repo two accounts both reach appears once" "found $shared"
fi

# 4d. The totals count the merged set, not one account's slice.
expect "4d the totals count every merged repo" "repos: 5" "$(cat "$map")"
expect "4d across every owner that resolved" "owners: 4" "$(cat "$map")"

# 4e. Asking every account must not change which one is ACTIVE. `gh` is the
# tool the operator uses for everything else, so the read is by NAME and
# `auth switch` appears in neither script nor in the seam they share.
refute "4e the sync never switches the operator's active account" "auth switch" \
	"$(cat scripts/sync-registry.sh scripts/discover-owners.sh scripts/lib/gh-accounts.sh)"

# 4f. A login that no longer works costs its own repos and never the map: it is
# named — an unexplained thinner map is the failure this whole section is
# about — and the sync carries on.
expect "4f an expired credential is named" "expired" "$out"
expect "4f a login whose token cannot be read is named too" "tokenless" "$out"
expect "4f and the map was still written" "wrote $map" "$out"

# 4g. A `gh` too old for `auth status --json` still syncs, from the active
# account alone. Same for a GH_TOKEN already in the environment, which
# overrides every stored account anyway. That fallback is what keeps the floor
# where it was: "gh exists and is authenticated".
oldbin="$tmp/bin-registry-old"
mkdir -p "$oldbin"
ln -sf "$regbin"/* "$oldbin/" 2>/dev/null
stub "$oldbin" gh 'case "$1 $2" in
"auth status")
  case "$*" in
  *--json*) echo "unknown flag: --json" >&2; exit 1 ;;
  *) exit 0 ;;
  esac
  ;;
"api --paginate") cat "$FIXTURES/repos-octo.json" ;;
*) echo "unexpected gh call: $*" >&2; exit 9 ;;
esac'
out_old="$(FIXTURES="$tmp" PATH="$oldbin" "$sandbox/scripts/sync-registry.sh" 2>&1)"
expect_exit "4g a gh without --json still writes a map" 0 $?
expect "4g from the active account alone" "repos: 3" "$(cat "$map")"
expect "4g and the owners it cannot reach are back to being warned about" \
	"no accessible repos for owner 'employer-org'" "$out_old"

printf '\n\033[1m§5 discover-owners — every login is asked, not only the active one\033[0m\n'

# The same blindness one level earlier, and it costs more there. Discovery
# prints the candidate list that is the SINGLE question onboarding asks the
# operator, so an org only the second login can see is an owner never offered —
# and an owner never offered never reaches the map at all.
multi="$tmp/bin-multi"
mkdir -p "$multi"
ln -sf "$BASE"/* "$multi/" 2>/dev/null
ln -sf "$(command -v git)" "$multi/git"
real_jq="$(command -v jq 2>/dev/null)" && ln -sf "$real_jq" "$multi/jq"
stub "$multi" gh 'case "$1 $2" in
"auth status")
  case "$*" in
  *--json*) cat "$FIXTURES/hosts.json" ;;
  *) exit 0 ;;
  esac
  ;;
"auth token")
  for a in "$@"; do
    case "$a" in
    octo | client | worky) echo "tok-$a"; exit 0 ;;
    esac
  done
  exit 1
  ;;
"api user")
  case "${GH_TOKEN:-tok-octo}" in
  tok-octo) echo octo ;;
  tok-client) echo client ;;
  tok-worky) echo worky ;;
  esac
  ;;
"api user/orgs")
  case "${GH_TOKEN:-tok-octo}" in
  tok-octo) printf "acme-org\n" ;;
  tok-client) printf "client-org\n" ;;
  tok-worky) printf "employer-org\n" ;;
  esac
  ;;
*) echo "unexpected gh call: $*" >&2; exit 9 ;;
esac'

mkdir -p "$tmp/noclones-5"
out="$(cd "$tmp" && FIXTURES="$tmp" HOME="$home" PATH="$multi" \
	"$REPO/scripts/discover-owners.sh" "$tmp/noclones-5" 2>&1)"
code=$?
expect_exit "5a discovery across several logins exits 0" 0 "$code"
candidates="$(printf '%s\n' "$out" | sed -n '/^CANDIDATE OWNERS/,$p')"
expect "5a the active account is a candidate" "octo" "$candidates"
expect "5a so is a login that is not active" "worky" "$candidates"
expect "5a and so is the third" "client" "$candidates"
expect "5b the active login's orgs are there" "acme-org" "$candidates"
expect "5b and the orgs only another login can see" "employer-org" "$candidates"
expect "5b including the third login's" "client-org" "$candidates"

# 5c. A login that no longer works is reported and skipped — never offered as
# a candidate the operator would then put in owners.txt, and never fatal.
refute "5c an expired login is not offered as a candidate" "expired" "$candidates"
expect "5c but it is reported" "expired" "$out"

# 5d. The fallback, which is also every machine with one login: no account
# list means the active session is asked, exactly as it was before.
oldgh="$tmp/bin-oldgh"
mkdir -p "$oldgh"
ln -sf "$multi"/* "$oldgh/" 2>/dev/null
stub "$oldgh" gh 'case "$1 $2" in
"auth status")
  case "$*" in
  *--json*) echo "unknown flag: --json" >&2; exit 1 ;;
  *) exit 0 ;;
  esac
  ;;
"api user") echo octo ;;
"api user/orgs") printf "acme-org\n" ;;
*) echo "unexpected gh call: $*" >&2; exit 9 ;;
esac'
out="$(cd "$tmp" && HOME="$home" PATH="$oldgh" \
	"$REPO/scripts/discover-owners.sh" "$tmp/noclones-5" 2>&1)"
expect_exit "5d a gh without --json still discovers" 0 $?
expect "5d from the active session alone" "octo" "$out"
expect "5d with its orgs" "acme-org" "$out"

printf '\n'
if [ "$failed" -eq 0 ]; then
	printf '\033[32monboarding selftest: everything passed\033[0m\n'
else
	printf '\033[31monboarding selftest: failures above\033[0m\n' >&2
fi
exit "$failed"
