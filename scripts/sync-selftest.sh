#!/usr/bin/env bash
# Prove that `scripts/sync-checkout.sh` reports what actually happened.
#
# This script is the SessionStart hook, so it runs before anyone is watching and
# its output is the only evidence it produces. That makes one failure mode worse
# than all the others: reporting a refusal that did not happen. A sync that says
# "offline" on a machine that is online looks exactly like a network blip, so
# nobody investigates, and every session silently inherits a stale `main` — the
# one outcome the script exists to prevent.
#
# That is not hypothetical. `timeout` is GNU coreutils and is NOT present on a
# stock macOS, so `timeout 15 git fetch` exited 127 there, the guard read it as
# a failed fetch, and every session on that machine reported "could not reach
# origin (offline?)" while GitHub answered in under a second. §1 is the
# regression test for it; the rest hold the promises around it:
#
#   - a refusal (dirty, feature branch, diverged) changes no tracked state;
#   - a genuinely unreachable origin is still reported as unreachable;
#   - a fast-forward that moves an INSTRUCTION_PATH says restart-lead.
#
# Every case runs against a throwaway origin on disk, under a PATH that holds
# only the tools the script may use — so the run is hermetic, needs no network,
# and §1 can assert the macOS condition on any platform by simply leaving
# `timeout` and `gtimeout` out of that PATH.
#
# Usage: scripts/sync-selftest.sh     (also: ./scripts/check.sh sync)
#
# Requires: git and jq — the script's own dependencies.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

SYNC="$PWD/scripts/sync-checkout.sh"
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

# Herestrings rather than pipelines: this file runs under `set -o pipefail`,
# where a pipeline reports the whole pipeline's status and a helper built on one
# can report FAIL for input that plainly matched.
expect() {
	local label="$1" want="$2" out="$3"
	if grep -qF -- "$want" <<<"$out"; then
		pass "$label"
	else
		fail "$label" "expected to find: $want${nl}--- got ---${nl}$out"
	fi
}

refute() {
	local label="$1" unwanted="$2" out="$3"
	if grep -qF -- "$unwanted" <<<"$out"; then
		fail "$label" "should NOT contain: $unwanted${nl}--- got ---${nl}$out"
	else
		pass "$label"
	fi
}

# --- the sandbox ------------------------------------------------------------
#
# A PATH built by hand, holding links to exactly the tools sync-checkout.sh is
# allowed to reach. Two things follow, and both are the point: `timeout` and
# `gtimeout` are absent unless a case adds them, which is what lets §1 assert
# the stock-macOS condition from anywhere; and a tool the script starts using
# without declaring shows up here as a failure rather than as a silent
# dependency on whatever the developer happened to have installed.
SANDBOX_TOOLS=(git jq sed head tr grep cat env bash sh date basename dirname rm mkdir touch printf test expr sleep uname wc sort)

build_path() {
	local bin="$1" t src
	mkdir -p "$bin"
	for t in "${SANDBOX_TOOLS[@]}"; do
		src="$(command -v "$t" 2>/dev/null)" || continue
		ln -sf "$src" "$bin/$t" 2>/dev/null || true
	done
}

# A fresh origin + clone per case. Cases mutate their own copy and nothing else.
new_repo() {
	local root="$1"
	local origin="$root/origin.git"
	local work="$root/work"

	git init --quiet --bare --initial-branch=main "$origin"
	git clone --quiet "$origin" "$work" 2>/dev/null

	git -C "$work" config user.email selftest@example.invalid
	git -C "$work" config user.name 'sync selftest'
	git -C "$work" config commit.gpgsign false

	printf 'seed\n' >"$work/README.md"
	git -C "$work" add README.md
	git -C "$work" commit --quiet -m 'seed'
	git -C "$work" push --quiet origin main 2>/dev/null

	# `git clone` records origin/HEAD only for a non-empty remote, and the
	# script falls back to `main` without it. Set it so the fallback is not
	# what is under test here.
	git -C "$work" remote set-head origin main 2>/dev/null
}

# Add a commit on origin that the clone does not have yet.
advance_origin() {
	local root="$1"
	local file="${2:-CHANGELOG.md}"
	local up="$root/upstream"

	rm -rf "$up"
	git clone --quiet "$root/origin.git" "$up" 2>/dev/null
	git -C "$up" config user.email selftest@example.invalid
	git -C "$up" config user.name 'sync selftest'
	git -C "$up" config commit.gpgsign false

	mkdir -p "$(dirname "$up/$file")"
	printf 'incoming\n' >>"$up/$file"
	git -C "$up" add "$file"
	git -C "$up" commit --quiet -m "incoming: $file"
	git -C "$up" push --quiet origin main 2>/dev/null
}

# Run the script the way the hook does: from inside the checkout, with only the
# sandboxed PATH. $2.. are extra tools to admit for this case.
run_sync() {
	local work="$1"
	shift
	local bin="$work/../sandbox-bin"
	local t src

	build_path "$bin"
	for t in "$@"; do
		src="$(command -v "$t" 2>/dev/null)" && ln -sf "$src" "$bin/$t" 2>/dev/null
	done

	(cd "$work" && PATH="$bin" HOME="$work/../home" "$SYNC" 2>&1)
}

head_of() { git -C "$1" rev-parse HEAD 2>/dev/null; }

# --- §1 the regression: no `timeout` is not "offline" ------------------------
#
# The whole reason this file exists. On a stock macOS neither `timeout` nor
# `gtimeout` is on PATH, and the sandbox reproduces that everywhere.

printf '\n§1 a reachable origin is never reported as unreachable\n'

tmp="$(mktemp -d)"
r1="$tmp/case1"
mkdir -p "$r1"
new_repo "$r1"
advance_origin "$r1"
out="$(run_sync "$r1/work")"

refute "no 'timeout' on PATH does not become 'could not reach origin'" \
	"could not reach origin" "$out"
expect "fast-forwards instead" "fast-forwarded 'main' 1 commit(s)" "$out"

if [ "$(head_of "$r1/work")" = "$(git -C "$r1/origin.git" rev-parse main)" ]; then
	pass "the checkout actually moved to origin/main"
else
	fail "the checkout actually moved to origin/main" \
		"work HEAD $(head_of "$r1/work") != origin main $(git -C "$r1/origin.git" rev-parse main)"
fi

# The two paths must agree: with coreutils present the result has to be
# identical, or the portable fallback and the `timeout` branch have drifted.
# Skipped rather than failed on a host with neither binary — which is every
# stock macOS, and exactly the host this bug came from.
if command -v timeout >/dev/null 2>&1 || command -v gtimeout >/dev/null 2>&1; then
	r1b="$tmp/case1b"
	mkdir -p "$r1b"
	new_repo "$r1b"
	advance_origin "$r1b"
	out="$(run_sync "$r1b/work" timeout gtimeout)"
	expect "identical result when coreutils' timeout IS available" \
		"fast-forwarded 'main' 1 commit(s)" "$out"
else
	printf '  \033[33mskip\033[0m  coreutils timeout/gtimeout not on this host\n'
fi

# --- §2 an unreachable origin is still reported ------------------------------
#
# The fix must not buy §1 by dropping the report altogether.

printf '\n§2 an origin that really is unreachable still says so\n'

r2="$tmp/case2"
mkdir -p "$r2"
new_repo "$r2"
git -C "$r2/work" remote set-url origin "$r2/does-not-exist.git"
before="$(head_of "$r2/work")"
out="$(run_sync "$r2/work")"

expect "reports it" "could not reach origin" "$out"
if [ "$(head_of "$r2/work")" = "$before" ]; then
	pass "changes nothing"
else
	fail "changes nothing" "HEAD moved from $before to $(head_of "$r2/work")"
fi

# --- §3 already current is silent -------------------------------------------

printf '\n§3 nothing to do says nothing\n'

r3="$tmp/case3"
mkdir -p "$r3"
new_repo "$r3"
out="$(run_sync "$r3/work")"

if [ -z "${out//[[:space:]]/}" ]; then
	pass "no message when already current"
else
	fail "no message when already current" "$out"
fi

# --- §4 the three refusals change no tracked state ---------------------------

printf '\n§4 a refusal reports, and touches nothing\n'

# dirty tree
r4="$tmp/case4"
mkdir -p "$r4"
new_repo "$r4"
advance_origin "$r4"
printf 'local edit\n' >>"$r4/work/README.md"
before="$(head_of "$r4/work")"
out="$(run_sync "$r4/work")"
expect "dirty tree is reported" "the tree is dirty. Not fast-forwarding." "$out"
if [ "$(head_of "$r4/work")" = "$before" ]; then
	pass "dirty tree: HEAD unchanged"
else
	fail "dirty tree: HEAD unchanged" "moved to $(head_of "$r4/work")"
fi

# feature branch
r5="$tmp/case5"
mkdir -p "$r5"
new_repo "$r5"
advance_origin "$r5"
git -C "$r5/work" checkout --quiet -b feature/x
before="$(head_of "$r5/work")"
out="$(run_sync "$r5/work")"
expect "a feature branch is reported, not merged" "on 'feature/x'" "$out"
if [ "$(head_of "$r5/work")" = "$before" ]; then
	pass "feature branch: HEAD unchanged"
else
	fail "feature branch: HEAD unchanged" "moved to $(head_of "$r5/work")"
fi

# diverged
r6="$tmp/case6"
mkdir -p "$r6"
new_repo "$r6"
advance_origin "$r6"
printf 'local commit\n' >"$r6/work/LOCAL.md"
git -C "$r6/work" add LOCAL.md
git -C "$r6/work" commit --quiet -m 'local work'
before="$(head_of "$r6/work")"
out="$(run_sync "$r6/work")"
expect "divergence is reported, never reconciled" "has diverged" "$out"
if [ "$(head_of "$r6/work")" = "$before" ]; then
	pass "diverged: HEAD unchanged"
else
	fail "diverged: HEAD unchanged" "moved to $(head_of "$r6/work")"
fi

# --- §5 a fast-forward that brings instructions says restart-lead ------------
#
# The hand-over is the half no command can perform, so it has to be said. If
# this line stops appearing, a lead runs on stale instructions and reports the
# update as applied.

printf '\n§5 incoming instructions raise the hand-over\n'

r7="$tmp/case7"
mkdir -p "$r7"
new_repo "$r7"
advance_origin "$r7" AGENTS.md
out="$(run_sync "$r7/work")"
expect "restart-lead is raised" "restart-lead:" "$out"
expect "and names the path that moved" "AGENTS.md" "$out"

# A fast-forward that touches nothing instructional must NOT raise it.
r8="$tmp/case8"
mkdir -p "$r8"
new_repo "$r8"
advance_origin "$r8" docs/unrelated.md
out="$(run_sync "$r8/work")"
refute "an unrelated path does not raise it" "restart-lead:" "$out"

# --- §6 the contract with the hook ------------------------------------------
#
# Claude Code parses stdout as one JSON object and a sync problem must never
# block a session, so the exit code is always 0 and the payload always parses.

printf '\n§6 the hook contract: one JSON object, always exit 0\n'

r9="$tmp/case9"
mkdir -p "$r9"
new_repo "$r9"
advance_origin "$r9"
out="$(run_sync "$r9/work")"
rc=$?

if [ "$rc" -eq 0 ]; then
	pass "exits 0"
else
	fail "exits 0" "exit was $rc"
fi

if jq -e . >/dev/null 2>&1 <<<"$out"; then
	pass "stdout parses as JSON"
else
	fail "stdout parses as JSON" "$out"
fi

if [ "$(jq -r 'has("systemMessage") and has("suppressOutput")' <<<"$out" 2>/dev/null)" = true ]; then
	pass "carries systemMessage and suppressOutput"
else
	fail "carries systemMessage and suppressOutput" "$out"
fi

printf '\n'
if [ "$failed" -eq 0 ]; then
	printf '  \033[32mall sync checks passed\033[0m\n'
else
	printf '  \033[31msync selftest FAILED\033[0m\n' >&2
fi
exit "$failed"
