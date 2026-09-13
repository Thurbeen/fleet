#!/usr/bin/env bash
# Prove that the gate reads nothing a running fleet wrote.
#
# One commit must give the same `./scripts/check.sh` verdict wherever it runs:
# CI, a worker's worktree, and the operator's own control-plane checkout with
# its live queue, its gitignored settings and its owner's global git config.
# Only the last of those holds any of that, so it is the one place a leak shows
# — a record nobody touched turns a green commit red there, or a claim CI
# proved is quietly skipped there — and it is the one place nobody reviews a
# gate run. So this builds that worst case on purpose:
#
#   A POISONED COPY of the tree under test: a malformed queue record and an
#   OPERATOR.md, an auto-merge.conf naming a repository, publish, agent, glyph
#   and voice settings with odd values, a rendered extension.toml, a reconciler
#   runtime directory, and a registry map of the wrong shape. All of it is made
#   up here. Nothing is ever copied from a real control plane.
#
#   A HOSTILE HOST: a HOME, an XDG git config and a GIT_CONFIG_GLOBAL that sign
#   every commit with a gpg that fails, route every hook to one that fails, and
#   name `trunk` the default branch; a thurbox hosts.toml; forge credentials and
#   a THURBOX_SESSION in the environment; and tripwire `gh`, `glab`,
#   `thurbox-cli`, `quota-axi` and `ssh` first on PATH, each recording that it
#   was run.
#
# Then, in that copy and under that host, it runs every CHECK that reads
# settings or records, and every SELFTEST cheap enough to run twice. Each must
# pass exactly as it does on a clean runner. The two expensive selftests —
# queue and reconcile — are not run a second time: §3 holds every selftest to
# the one shared helper, `scripts/lib/selftest-env.sh`, and §2 proves that
# helper against the same hostile host.
#
# Every assertion names the leak it guards, so a FAIL line is the diagnosis.
#
# Usage: scripts/isolation-selftest.sh     (also: ./scripts/check.sh isolation)
#
# Requires: git, jq and python3 (with PyYAML) — the dependencies of the checks
# it re-runs.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

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
	if grep -qF -- "$2" <<<"$3"; then pass "$1"; else fail "$1" "wanted: $2${nl}--- got ---${nl}$3"; fi
}

refute() {
	if grep -qF -- "$2" <<<"$3"; then fail "$1" "did not want: $2${nl}--- got ---${nl}$3"; else pass "$1"; fi
}

# A check passed: exit 0. When it did not, its own FAIL lines are the message,
# and its last lines when it printed none.
expect_green() {
	[ "$2" -eq 0 ] && {
		pass "$1"
		return
	}
	local why
	why="$(grep -F -A6 FAIL <<<"$3" | head -60)"
	fail "$1" "exit $2${nl}${why:-$(tail -30 <<<"$3")}"
}

for tool in git jq python3; do
	command -v "$tool" >/dev/null || {
		echo "error: $tool not found" >&2
		exit 2
	}
done

tmp="$(mktemp -d)"
tmp="$(cd "$tmp" && pwd -P)"

# Where the TOOLS are installed is not operator state. A PyYAML installed with
# `pip --user` lives under the real HOME, and a hostile HOME must not make it
# vanish: that failure would be about this machine's packaging, not a leak.
PYBASE="${PYTHONUSERBASE:-$(python3 -m site --user-base 2>/dev/null)}"

# --- the poisoned copy --------------------------------------------------------

# The working tree as it stands, tracked and untracked-but-not-ignored, so this
# tests the code under review and never an operator file beside it.
copy="$tmp/checkout"
mkdir -p "$copy"
git ls-files -z --cached --others --exclude-standard |
	while IFS= read -r -d '' f; do [ -e "$f" ] && printf '%s\0' "$f"; done |
	tar --null -cf - -T - | tar -xf - -C "$copy"

# Its own history, built with every host config switched off by hand. This file
# constructs the hostile host below, so it cannot lean on the helper whose job is
# to defeat it.
cgit() {
	GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_COUNT=0 \
		git -c user.name=isolation -c user.email=isolation@example.invalid \
		-c commit.gpgsign=false -c core.hooksPath=/dev/null "$@"
}
cgit -C "$copy" init -q -b main
cgit -C "$copy" add -A
cgit -C "$copy" commit -qm "fixture: the tree under test"

# A queue record nobody touched, wrong in every way `queue.sh check` knows.
rec="$copy/orchestration/queue/poisoned"
mkdir -p "$rec/01-broken"
printf 'slug: poisoned\ntitle: A topic nobody touched\narchived: 42\n' >"$rec/topic.yaml"
printf 'the prompt\n' >"$rec/PROMPT.md"
cat >"$rec/01-broken/task.yaml" <<'YAML'
id: 99-not-this-directory
topic: poisoned
title: A broken record
state: half-done
repo: /nowhere
branch: poisoned/branch
publish: {method: carrier-pigeon, how: 7}
blocked_by:
  - {task: poisoned/42-gone, kind: invented, why: ""}
YAML
printf 'Always sign off as the private operator.\n' >"$copy/orchestration/queue/OPERATOR.md"

o="$copy/orchestration"
printf 'github.com/operator-private/secret-repo\n' >"$o/auto-merge.conf"
printf 'METHOD=carrier-pigeon\nHOW=run operator-private-pipeline --ship\nATTESTATION_MARKER=operator-private-mark\n' >"$o/publish.conf"
printf 'AGENT=operator-private-agent\nFUEL_PROVIDER=operator-private-vendor\nLIMIT_BANNER=you are out\n' >"$o/agent.conf"
printf 'GLYPHS=sideways\nLEAD_GLYPH_ON=@@\n' >"$o/session-glyphs.conf"
printf 'OPERATOR_NAME=Operator Private\nASSISTANT_NAME=Private Lead\n' >"$o/voice.conf"
mkdir -p "$o/reconcile" "$o/first-run"
printf '1\n' >"$o/reconcile/pid"
printf 'asked down by the operator\n' >"$o/reconcile/down"
printf 'no\n' >"$o/first-run/pane"
printf '[[sessions]]\nname = "Poisoned Lead"\nrepo_path = "/nowhere"\n' >"$copy/extension.toml"
printf 'owners: not-a-list\n' >"$copy/registry/repos.generated.yaml"
printf 'operator-private-org\n' >"$copy/registry/owners.txt"

# --- the hostile host ---------------------------------------------------------

hostile="$tmp/hostile"
TRIP="$tmp/tripwire.log"
: >"$TRIP"
mkdir -p "$hostile/home/.config/git" "$hostile/home/.config/thurbox" "$hostile/hooks" "$hostile/bin"

for h in pre-commit prepare-commit-msg commit-msg post-commit post-checkout pre-push reference-transaction; do
	printf '#!/bin/sh\necho "hostile %s hook ran" >&2\nexit 1\n' "$h" >"$hostile/hooks/$h"
	chmod +x "$hostile/hooks/$h"
done
printf '#!/bin/sh\necho "hostile gpg refused to sign" >&2\nexit 1\n' >"$hostile/nogpg"
chmod +x "$hostile/nogpg"

cat >"$hostile/gitconfig" <<EOF
[user]
	name = Operator Private
	email = operator@private.invalid
	signingkey = DEADBEEF
[commit]
	gpgsign = true
[tag]
	gpgsign = true
[gpg]
	program = $hostile/nogpg
[init]
	defaultBranch = trunk
[core]
	hooksPath = $hostile/hooks
EOF
cp "$hostile/gitconfig" "$hostile/home/.gitconfig"
cp "$hostile/gitconfig" "$hostile/home/.config/git/config"
printf '[[hosts]]\nname = "operator-private-box"\n' >"$hostile/home/.config/thurbox/hosts.toml"

for t in gh glab thurbox-cli quota-axi ssh; do
	printf '#!/bin/sh\nprintf "%%s %%s\\n" "%s" "$*" >>"%s"\nexit 97\n' "$t" "$TRIP" >"$hostile/bin/$t"
	chmod +x "$hostile/bin/$t"
done

# Run a command in the poisoned copy, under the hostile host.
under_hostile() {
	(cd "$copy" && env \
		HOME="$hostile/home" XDG_CONFIG_HOME="$hostile/home/.config" \
		GIT_CONFIG_GLOBAL="$hostile/gitconfig" \
		PATH="$hostile/bin:$PATH" PYTHONUSERBASE="$PYBASE" \
		GH_TOKEN=operator-private-token GITHUB_TOKEN=operator-private-token \
		GH_HOST=github.private.invalid GITLAB_HOST=gitlab.private.invalid \
		THURBOX_SESSION=00000000-0000-0000-0000-operatorlead \
		"$@" 2>&1)
}

# The poison has to be poison, or every pass below is vacuous.
if out="$(cd "$copy" && FLEET_QUEUE_DIR="$copy/orchestration/queue" ./scripts/queue.sh check 2>&1)"; then
	fail "the poisoned queue record really fails queue.sh check" "$out"
else
	pass "the poisoned queue record really fails queue.sh check"
fi
mkdir -p "$tmp/hostile-probe"
printf x >"$tmp/hostile-probe/f"
if out="$(under_hostile git -C "$tmp/hostile-probe" init -q &&
	under_hostile git -C "$tmp/hostile-probe" add f &&
	under_hostile git -C "$tmp/hostile-probe" commit -qm x)"; then
	fail "the hostile git config really breaks a commit" "$out"
else
	pass "the hostile git config really breaks a commit"
fi
: >"$TRIP"

# --- §1 the gate's checks read no record and no setting of the checkout -------

printf '\n§1 the checks, in a poisoned checkout\n'

# `check.sh queue` runs queue-selftest.sh, whose own isolation is §3's. Swapped
# for a marker here so what is under test is what check_queue ITSELF reads, at
# a cost of milliseconds rather than a second full queue selftest.
printf '#!/usr/bin/env bash\ntouch "%s"\n' "$tmp/queue-selftest-ran" >"$copy/scripts/queue-selftest.sh"

out="$(under_hostile ./scripts/check.sh queue)"
expect_green "queue: check.sh queue reads no queue record the checkout holds (leak: a live record nobody touched failed the gate)" $? "$out"
if [ -e "$tmp/queue-selftest-ran" ]; then
	pass "queue: and check.sh queue still runs queue-selftest.sh"
else
	fail "queue: and check.sh queue still runs queue-selftest.sh" "$out"
fi

out="$(under_hostile ./scripts/check.sh automerge)"
expect_green "automerge: check.sh automerge passes beside an operator's auto-merge.conf" $? "$out"
expect "automerge: and still proves the fresh-clone reading there (leak: it answered skip whenever auto-merge.conf existed)" \
	"a fresh clone merges nowhere" "$out"
refute "automerge: and never repeats the operator's list" "operator-private" "$out"

out="$(under_hostile ./scripts/check.sh yaml)"
expect_green "yaml: check.sh yaml reads no registry map the checkout holds (leak: the gitignored repos.generated.yaml was validated)" $? "$out"

out="$(under_hostile ./scripts/check.sh voice)"
expect_green "voice: check.sh voice renders from the tracked defaults, not voice.conf or session-glyphs.conf" $? "$out"
refute "voice: and never renders the operator's names" "Operator Private" "$out"

# --- §2 the helper defeats the hostile host -----------------------------------

printf '\n§2 scripts/lib/selftest-env.sh, under a hostile host\n'

helper="scripts/lib/selftest-env.sh"
if [ ! -f "$copy/$helper" ]; then
	fail "helper: $helper exists (leak: every selftest pins host git config itself, or does not)"
else
	# shellcheck disable=SC2016 # expanded by the inner shell, on purpose
	out="$(under_hostile bash -c '
		. scripts/lib/selftest-env.sh
		selftest_isolate "$1"
		cd "$1" && git init -q probe && cd probe && printf x >f && git add f &&
			git commit -qm probe && echo "committed"
		echo "branch=$(git rev-parse --abbrev-ref HEAD)"
		echo "hooks=$(git config --get core.hooksPath)"
		echo "home=$HOME"
		echo "hosts=$(cat "$HOME/.config/thurbox/hosts.toml" 2>/dev/null)"
		echo "session=${THURBOX_SESSION:-}"
		echo "token=${GH_TOKEN:-}${GITHUB_TOKEN:-}"
		echo "forge=${GH_HOST:-}${GITLAB_HOST:-}"
		python3 -c "import yaml" && echo "yaml imports"
	' _ "$tmp/helper-run")"
	expect "helper: a selftest commits despite a global config that signs and hooks every commit (leak: host git config)" "committed" "$out"
	expect "helper: the host's init.defaultBranch does not name a selftest's branch" "branch=main" "$out"
	expect "helper: no host hooks path reaches a selftest's repos" "hooks=${nl}" "$out"
	refute "helper: a selftest runs with a throwaway HOME, not the caller's (leak: real HOME)" "home=$hostile/home" "$out"
	refute "helper: no thurbox hosts.toml from the caller's HOME is readable" "operator-private-box" "$out"
	expect "helper: a worker's THURBOX_SESSION does not reach a selftest" "session=${nl}" "$out"
	expect "helper: no forge credential reaches a selftest" "token=${nl}" "$out"
	expect "helper: no forge host override reaches a selftest" "forge=${nl}" "$out"
	expect "helper: a PyYAML installed under the real HOME still imports" "yaml imports" "$out"
fi

# --- §3 every selftest isolates itself through that helper --------------------

printf '\n§3 every selftest goes through the helper\n'

# The tree under test, not the copy: §1 swapped the copy's queue-selftest.sh
# for a marker.
for s in scripts/*-selftest.sh; do
	name="$(basename "$s")"
	# This file builds the hostile host the helper exists to defeat.
	[ "$name" = isolation-selftest.sh ] && continue
	if grep -qE '^[[:space:]]*(\.|source)[[:space:]].*scripts/lib/selftest-env\.sh' "$s" &&
		grep -qE '^[[:space:]]*selftest_isolate[[:space:]]' "$s"; then
		pass "$name isolates itself through $helper"
	else
		fail "$name isolates itself through $helper (leak: host git config, HOME, forge credentials and THURBOX_SESSION reach it)"
	fi
done

# --- §4 the selftests cheap enough to run twice, run poisoned -----------------

printf '\n§4 the cheap selftests, in a poisoned checkout under a hostile host\n'

for s in fleet-status sync onboarding install; do
	out="$(under_hostile "./scripts/$s-selftest.sh")"
	expect_green "$s-selftest.sh passes here exactly as on a clean runner" $? "$out"
done

# --- §5 nothing reached a real tool -------------------------------------------

printf '\n§5 the tripwires\n'

if [ -s "$TRIP" ]; then
	fail "no check or selftest ran a real gh, glab, thurbox-cli, quota-axi or ssh off PATH" "$(sort -u "$TRIP")"
else
	pass "no check or selftest ran a real gh, glab, thurbox-cli, quota-axi or ssh off PATH"
fi

if [ "$failed" -eq 0 ]; then
	echo "isolation selftest: the gate reads no operator state"
fi
exit "$failed"
