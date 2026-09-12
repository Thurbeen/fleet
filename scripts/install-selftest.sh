#!/usr/bin/env bash
# Prove the one-line install and the first-run pane ask, end to end, offline.
#
# Both are run once per operator and never again, which is what makes a
# regression in either invisible to everyone already set up and total for the
# next person. So they are driven here the way an operator meets them:
#
#   1. THE ONE-LINER. `install.sh` piped into `sh` in a throwaway HOME, against
#      a local clone of this working tree: prerequisites are checked BEFORE the
#      extension is installed, a missing required one stops it, the pane is
#      installed and NOT placed, and a second run changes nothing. An existing
#      checkout is fast-forwarded and never overwritten — a dirty or diverged
#      one is refused, and so is a directory that is not a fleet clone.
#   2. THE FIRST-RUN ASK. `scripts/pane-ask.sh` says `ask` once, remembers a
#      yes and a no in gitignored state in the checkout, places the pane only on
#      the yes, and never asks an operator who already placed it. FLEET.md is
#      what tells the lead to run it, because a hook would be one agent's.
#
# WHY thurbox-cli IS A STUB HERE. A throwaway HOME does not isolate it: it
# still reaches the real thurbox configuration, so a real `extension install`
# from a test would register a Mission Control session on the operator's own
# machine. The stub records every call instead, and the layout.lua it names is
# a copy of scripts/fixtures/layout/stock.lua. The real-thurbox run is the
# fresh-machine one, and it is not this file's.
#
# Usage: scripts/install-selftest.sh   (also: ./scripts/check.sh install)
#
# Requires: bash, git, jq, and the coreutils the scripts under test use. No
# thurbox, no gh, no network.

# Every stub body below is source for ANOTHER shell.
# shellcheck disable=SC2016

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2
REPO="$PWD"

# The caller's git and gh environment decide nothing here; see
# onboarding-selftest.sh, which argues each of these.
unset GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GH_TOKEN GITHUB_TOKEN GH_HOST GITLAB_HOST
unset FLEET_DIR FLEET_REPO FLEET_BRANCH
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
	case "$3" in
	*"$2"*) pass "$1" ;;
	*) fail "$1" "wanted: $2${nl}got:${nl}$3" ;;
	esac
}

refute() {
	case "$3" in
	*"$2"*) fail "$1" "did not want: $2${nl}got:${nl}$3" ;;
	*) pass "$1" ;;
	esac
}

expect_exit() {
	if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "wanted exit $2, got $3"; fi
}

expect_nonzero() {
	if [ "$2" != 0 ]; then pass "$1"; else fail "$1" "wanted a non-zero exit, got 0"; fi
}

tmp="$(mktemp -d)"
# Resolved, so a path printed by a script compares equal to the one built here
# on a machine whose temp directory is a symlink.
tmp="$(cd "$tmp" && pwd -P)"

# --- the sandbox --------------------------------------------------------------

# A HOME whose git config commits without signing, since this machine's is not
# the test's.
home="$tmp/home"
mkdir -p "$home"
printf '[user]\n\tname = selftest\n\temail = selftest@example.invalid\n[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n' \
	>"$home/.gitconfig"

# A PATH built from nothing, so "missing" means missing even here.
BASE="$tmp/base"
mkdir -p "$BASE"
for t in bash sed grep sort head tail cut tr awk find cat date mktemp cp rm rmdir mv ls \
	dirname basename wc uname tar env chmod mkdir touch md5sum cmp git jq; do
	real="$(command -v "$t" 2>/dev/null)" && ln -sf "$real" "$BASE/$t"
done
# `| sh` runs whatever /bin/sh is. dash is the strict one, so prefer it: a
# bashism that slipped into the installer fails here rather than on Debian.
real_sh="$(command -v dash 2>/dev/null || command -v sh)"
ln -sf "$real_sh" "$BASE/sh"

stub() {
	local dir="$1" name="$2" body="$3"
	mkdir -p "$dir"
	rm -f "$dir/$name"
	printf '#!/usr/bin/env bash\n%s\n' "$body" >"$dir/$name"
	chmod +x "$dir/$name"
}

floor="$(sed -n 's/^min_thurbox_version *= *"\(.*\)"/\1/p' extension.toml.in | head -1)"

UI="$tmp/ui"
mkdir -p "$UI"
cp scripts/fixtures/layout/stock.lua "$UI/layout.lua"
slot="$(sed -n 's/^local SLOT = "\(.*\)"$/\1/p' interface/fleet_queue.lua | head -1)"

LOG="$tmp/thurbox-calls.log"
: >"$LOG"

bin="$tmp/bin"
mkdir -p "$bin"
ln -sf "$BASE"/* "$bin/"
stub "$bin" gh 'case "$1" in
  --version) echo "gh version 2.100.0" ;;
  auth) exit 0 ;;
  api) echo "octo" ;;
esac'
stub "$bin" python3 'case "$1" in
  --version) echo "Python 3.13.0" ;;
  -c) case "$2" in *yaml*) echo "6.0.1" ;; esac ;;
esac'
stub "$bin" apt-get 'exit 0'
# `extension install` drains stdin on purpose. Under `curl | sh` the script IS
# stdin, and an installer that lets a child read it loses every line after that
# child — which here is the closing next step, asserted in 1a.
stub "$bin" thurbox-cli 'printf "%s\n" "$*" >>"$THURBOX_LOG"
case "$1 $2" in
"--version ") echo "thurbox-cli $THURBOX_FLOOR" ;;
"extension install") cat >/dev/null; echo "installed fleet" ;;
"session list") echo "${THURBOX_SESSIONS:-[]}" ;;
"plugin install") echo "installed $3" ;;
"plugin dir") echo "$THURBOX_UI" ;;
"plugin check")
  if grep -v "^[[:space:]]*--" "$THURBOX_UI/layout.lua" | grep -q "slot = \"fleetqueue\""; then
    echo "✓ loads — fleetqueue"
  else
    echo "✗ nothing places slot \"fleetqueue\""; exit 1
  fi ;;
*) exit 0 ;;
esac'

export THURBOX_LOG="$LOG" THURBOX_FLOOR="$floor" THURBOX_UI="$UI"

# The upstream: this working tree as it stands, committed into a repo of its
# own, so the test installs the code under test and not the last commit.
src="$tmp/upstream"
mkdir -p "$src"
git ls-files -z --cached --others --exclude-standard |
	while IFS= read -r -d '' f; do [ -e "$f" ] && printf '%s\0' "$f"; done |
	tar --null -cf - -T - | tar -xf - -C "$src"
HOME="$home" git -C "$src" init -q
HOME="$home" git -C "$src" add -A
HOME="$home" git -C "$src" commit -qm "fixture: the tree under test"

# install <PATH> [VAR=value...] -- runs the one-liner exactly as the README
# prints it: the script on stdin of `sh`.
install_piped() {
	local path="$1"
	shift
	env -i HOME="$home" PATH="$path" FLEET_REPO="$src" THURBOX_LOG="$LOG" \
		THURBOX_FLOOR="$floor" THURBOX_UI="$UI" "$@" sh <"$REPO/install.sh" 2>&1
}

tree_sum() {
	(cd "$1" && find . -type f -not -path './.git/*' | sort | while IFS= read -r f; do md5sum "$f"; done)
}

printf '\n\033[1m§1 install.sh — prerequisites first, the pane installed and not placed, idempotent\033[0m\n'

if [ ! -f "$REPO/install.sh" ]; then
	fail "1 install.sh exists at the repo root" "no $REPO/install.sh"
fi

clone="$home/fleet"

# --- 1a. a fresh machine ------------------------------------------------------
out="$(install_piped "$bin")"
code=$?
expect_exit "1a the one-liner exits 0 on a machine that has everything" 0 "$code"
expect "1a it clones into ~/fleet by default" "$clone" "$out"
if [ -f "$clone/scripts/install-extension.sh" ]; then
	pass "1a the clone is there"
else
	fail "1a the clone is there" "$out"
fi
expect "1a the prerequisites were checked" "REQUIRED" "$out"
first_probe="$(grep -n '^--version' "$LOG" | head -1 | cut -d: -f1)"
first_install="$(grep -n "^extension install $clone\$" "$LOG" | head -1 | cut -d: -f1)"
if [ -n "$first_install" ]; then
	pass "1a the extension was installed from the clone"
else
	fail "1a the extension was installed from the clone" "thurbox-cli calls:${nl}$(cat "$LOG")"
fi
if [ -n "$first_probe" ] && [ -n "$first_install" ] && [ "$first_probe" -lt "$first_install" ]; then
	pass "1a and only after the prerequisites were probed"
else
	fail "1a and only after the prerequisites were probed" "thurbox-cli calls:${nl}$(cat "$LOG")"
fi
expect "1a the pane plugin was installed" "plugin install" "$(cat "$LOG")"
if cmp -s scripts/fixtures/layout/stock.lua "$UI/layout.lua" && ! ls "$UI"/layout.lua.bak-* >/dev/null 2>&1; then
	pass "1a the pane was NOT placed: layout.lua untouched, no backup"
else
	fail "1a the pane was NOT placed: layout.lua untouched, no backup"
fi
expect "1a it names the next step" "Mission Control" "$out"
expect "1a and says the first session asks about the pane" "pane" "$out"
expect "1a the last line survived a child draining stdin" "fleet-onboarding" "$out"

# --- 1b. the second run changes nothing ---------------------------------------
head_before="$(git -C "$clone" rev-parse HEAD 2>/dev/null)"
sum_before="$(tree_sum "$clone")"
layout_before="$(cat "$UI/layout.lua")"
# The inspectable form this time: downloaded, read, run as a file.
out="$(env -i HOME="$home" PATH="$bin" FLEET_REPO="$src" THURBOX_LOG="$LOG" \
	THURBOX_FLOOR="$floor" THURBOX_UI="$UI" sh "$REPO/install.sh" 2>&1)"
expect_exit "1b a second run exits 0" 0 $?
expect "1b and says the checkout is already current" "already current" "$out"
if [ "$head_before" = "$(git -C "$clone" rev-parse HEAD)" ]; then pass "1b HEAD did not move"; else fail "1b HEAD did not move"; fi
if [ "$sum_before" = "$(tree_sum "$clone")" ]; then
	pass "1b no file in the checkout changed"
else
	fail "1b no file in the checkout changed" "$(diff <(printf '%s\n' "$sum_before") <(tree_sum "$clone"))"
fi
if [ -z "$(git -C "$clone" status --porcelain)" ]; then
	pass "1b the tree is clean — everything the install rendered is gitignored"
else
	fail "1b the tree is clean — everything the install rendered is gitignored" "$(git -C "$clone" status --porcelain)"
fi
if [ "$layout_before" = "$(cat "$UI/layout.lua")" ]; then pass "1b layout.lua still untouched"; else fail "1b layout.lua still untouched"; fi

# --- 1c. upstream moved: fast-forward, never re-clone -------------------------
printf '\nmoved\n' >>"$src/README.md"
HOME="$home" git -C "$src" commit -qam "upstream moves"
out="$(install_piped "$bin")"
expect_exit "1c an existing clone behind upstream exits 0" 0 $?
expect "1c it is fast-forwarded" "Fast-forwarded" "$out"
if [ "$(git -C "$src" rev-parse HEAD)" = "$(git -C "$clone" rev-parse HEAD)" ]; then
	pass "1c to upstream's HEAD"
else
	fail "1c to upstream's HEAD" "$out"
fi

# --- 1d. a dirty checkout is refused, and kept --------------------------------
printf 'operator edit\n' >>"$clone/AGENTS.md"
dirty_before="$(cat "$clone/AGENTS.md")"
head_before="$(git -C "$clone" rev-parse HEAD)"
printf '\nagain\n' >>"$src/README.md"
HOME="$home" git -C "$src" commit -qam "upstream moves again"
: >"$LOG"
out="$(install_piped "$bin")"
expect_nonzero "1d a dirty checkout is refused" $?
expect "1d and it says why" "uncommitted" "$out"
if [ "$dirty_before" = "$(cat "$clone/AGENTS.md")" ] && [ "$head_before" = "$(git -C "$clone" rev-parse HEAD)" ]; then
	pass "1d the edit and HEAD are both kept"
else
	fail "1d the edit and HEAD are both kept"
fi
refute "1d nothing was installed" "extension install" "$(cat "$LOG")"
git -C "$clone" checkout -q -- AGENTS.md

# --- 1e. a diverged checkout is refused, never reset --------------------------
printf 'local\n' >"$clone/local-note.md"
HOME="$home" git -C "$clone" add local-note.md
HOME="$home" git -C "$clone" commit -qm "a local commit"
head_before="$(git -C "$clone" rev-parse HEAD)"
: >"$LOG"
out="$(install_piped "$bin")"
expect_nonzero "1e a diverged checkout is refused" $?
expect "1e and it says so" "diverged" "$out"
if [ "$head_before" = "$(git -C "$clone" rev-parse HEAD)" ]; then pass "1e the local commit is kept"; else fail "1e the local commit is kept"; fi
refute "1e nothing was installed" "extension install" "$(cat "$LOG")"

# --- 1f. a directory that is not a fleet clone is left alone ------------------
other="$tmp/not-fleet"
mkdir -p "$other"
printf 'mine\n' >"$other/notes.txt"
out="$(install_piped "$bin" FLEET_DIR="$other")"
expect_nonzero "1f a non-empty directory that is not a fleet clone is refused" $?
if [ "$(ls -A "$other")" = "notes.txt" ] && [ "$(cat "$other/notes.txt")" = "mine" ]; then
	pass "1f and nothing in it was touched"
else
	fail "1f and nothing in it was touched" "$(ls -A "$other")"
fi

# --- 1g. FLEET_DIR is honoured, and printed -----------------------------------
elsewhere="$tmp/elsewhere/fleet"
: >"$LOG"
out="$(install_piped "$bin" FLEET_DIR="$elsewhere")"
expect_exit "1g FLEET_DIR installs somewhere else" 0 $?
expect "1g and says where it went" "$elsewhere" "$out"
expect "1g the extension points at that clone" "extension install $elsewhere" "$(cat "$LOG")"

# --- 1h. the location is sticky: an installed lead decides the default --------
# A second clone would get a manifest thurbox never applies — it reuses the
# lead by name and never moves it — so with no FLEET_DIR the checkout the live
# Mission Control opens is the default, not ~/fleet.
home2="$tmp/home2"
mkdir -p "$home2"
cp "$home/.gitconfig" "$home2/"
out="$(env -i HOME="$home2" PATH="$bin" FLEET_REPO="$src" THURBOX_LOG="$LOG" \
	THURBOX_FLOOR="$floor" THURBOX_UI="$UI" \
	THURBOX_SESSIONS="[{\"name\":\"X Mission Control\",\"cwd\":\"$elsewhere\"}]" \
	sh <"$REPO/install.sh" 2>&1)"
expect_exit "1h an installed lead's checkout is reused" 0 $?
expect "1h and named as the reason" "$elsewhere" "$out"
if [ ! -e "$home2/fleet" ]; then pass "1h no second clone was made"; else fail "1h no second clone was made" "$out"; fi

# --- 1i. a missing required dependency stops it before the extension ----------
nogh="$tmp/bin-nogh"
mkdir -p "$nogh"
for f in "$bin"/*; do
	[ "$(basename "$f")" = gh ] && continue
	ln -sf "$f" "$nogh/$(basename "$f")"
done
fresh="$tmp/fresh/fleet"
: >"$LOG"
out="$(install_piped "$nogh" FLEET_DIR="$fresh")"
expect_nonzero "1i a missing required dependency stops the install" $?
expect "1i it names what to install" "sudo apt-get install -y gh" "$out"
refute "1i and the extension was never installed" "extension install" "$(cat "$LOG")"

# --- 1j. no git: the bootstrap cannot clone, and says so ----------------------
nogit="$tmp/bin-nogit"
mkdir -p "$nogit"
for f in "$bin"/*; do
	[ "$(basename "$f")" = git ] && continue
	ln -sf "$f" "$nogit/$(basename "$f")"
done
out="$(install_piped "$nogit" FLEET_DIR="$tmp/nogit/fleet")"
expect_nonzero "1j no git is a refusal" $?
expect "1j that names git" "git" "$out"

printf '\n\033[1m§2 pane-ask.sh — asked once, both answers remembered, a placed pane never asked about\033[0m\n'

ask="$clone/scripts/pane-ask.sh"
state="$clone/orchestration/first-run/pane"
git -C "$clone" reset -q --hard HEAD~1 # drop 1e's local commit; this is a test clone
cp scripts/fixtures/layout/stock.lua "$UI/layout.lua"
rm -f "$UI"/layout.lua.bak-*

run_ask() { env -i HOME="$home" PATH="$bin" THURBOX_LOG="$LOG" THURBOX_UI="$UI" "$ask" "$@" 2>&1; }

if [ ! -x "$ask" ]; then
	fail "2 scripts/pane-ask.sh exists and is executable" "no $ask"
fi

# --- 2a. first session: ask ---------------------------------------------------
out="$(run_ask)"
expect_exit "2a a first session exits 0" 0 $?
expect "2a and says to ask" "ask" "$out"
expect "2a naming the answer that places it" "pane-ask.sh yes" "$out"
if [ ! -e "$state" ]; then pass "2a asking records nothing"; else fail "2a asking records nothing"; fi

# --- 2b. no is remembered, and the way back printed once ----------------------
out="$(run_ask no)"
expect_exit "2b a no exits 0" 0 $?
expect "2b it prints how to place it later" "./scripts/place-pane.sh" "$out"
expect "2b the no is recorded" "no" "$(cat "$state" 2>/dev/null)"
if cmp -s scripts/fixtures/layout/stock.lua "$UI/layout.lua"; then pass "2b a no places nothing"; else fail "2b a no places nothing"; fi

out="$(run_ask)"
expect "2c the next session does not ask" "skip" "$out"
refute "2c and does not print the way back a second time" "place-pane.sh" "$out"
if [ -z "$(git -C "$clone" status --porcelain)" ]; then
	pass "2d the answer is gitignored"
else
	fail "2d the answer is gitignored" "$(git -C "$clone" status --porcelain)"
fi

# --- 2e. yes places it, and is remembered -------------------------------------
rm -f "$state"
out="$(run_ask yes)"
expect_exit "2e a yes exits 0" 0 $?
expect "2e the pane is placed" "slot = \"$slot\"" "$(cat "$UI/layout.lua")"
expect "2e the yes is recorded" "yes" "$(cat "$state" 2>/dev/null)"
out="$(run_ask)"
expect "2e the next session does not ask" "skip" "$out"

# --- 2f. an operator who placed it already is never asked ---------------------
# 2e's layout carries the block; forget the recorded answer, as a machine that
# placed it by hand or through onboarding never had one.
rm -f "$state"
out="$(run_ask)"
expect "2f a layout that already places the pane is not asked about" "skip" "$out"
refute "2f not even once" "ask" "$(printf '%s\n' "$out" | head -1)"
expect "2f and that is recorded too" "placed" "$(cat "$state" 2>/dev/null)"

# --- 2g. no thurbox to ask: nothing asked, nothing recorded -------------------
rm -f "$state"
nothurbox="$tmp/bin-nothurbox"
mkdir -p "$nothurbox"
for f in "$bin"/*; do
	[ "$(basename "$f")" = thurbox-cli ] && continue
	ln -sf "$f" "$nothurbox/$(basename "$f")"
done
out="$(env -i HOME="$home" PATH="$nothurbox" "$ask" 2>&1)"
expect "2g with no layout to read it does not ask" "skip" "$out"
if [ ! -e "$state" ]; then pass "2g and records nothing, so a later session can"; else fail "2g and records nothing, so a later session can"; fi

printf '\n'
if [ "$failed" -eq 0 ]; then
	printf '\033[32minstall selftest: everything passed\033[0m\n'
else
	printf '\033[31minstall selftest: failures above\033[0m\n' >&2
fi
exit "$failed"
