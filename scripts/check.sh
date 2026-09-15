#!/usr/bin/env bash
# The control plane's whole gate, in one place.
#
# CI runs this (one check per job, so a docs-only PR skips the shell job), the
# prek hooks run it, and `.publish.yaml` declares it as this repo's whole gate.
# One definition means a green local run and a green CI run mean the same
# thing — the failure this repo is most exposed to, because CI only fires on
# pull requests while routine control-plane changes go straight to `main`.
#
# Usage:
#   scripts/check.sh                     # every check
#   scripts/check.sh shell yaml          # only the named ones
#   scripts/check.sh --fix markdown      # apply the fixes a check can apply
#
# Checks: shell, markdown, docs, yaml, workflow, profiles, queue, cli, reconcile,
# status, skills, pane, voice, automerge, onboarding, install, sync, isolation. Only `markdown`
# has a fixer; `--fix` is a no-op for the rest, so `scripts/check.sh --fix` is
# always safe to run.
#
# IT READS NO OPERATOR STATE. One commit gets one verdict — on CI, in a worker's
# worktree, and in the control-plane checkout — so no check reads what a running
# fleet wrote into a checkout: the queue's records, the registry map, the
# gitignored `orchestration/*.conf`, the reconciler's runtime, or the caller's
# HOME and git config. Checks read tracked files; every selftest runs through
# `scripts/lib/selftest-env.sh`. The live half moved and did not vanish:
# `./scripts/fleet-status.sh --records` validates the operator's queue records
# and registry map, and `./scripts/queue.sh check` still validates the queue on
# its own. `isolation` holds the line, by re-running the
# checks that could leak in a poisoned copy of the tree under a hostile host.
#
# Requires: shellcheck, rumdl, uv, python3 (with PyYAML), lua. A missing tool
# fails the check rather than skipping it — a gate that silently passes when
# its linter is absent is worse than no gate.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

failed=0
fix=0

fail() {
	printf '\033[31mFAIL\033[0m  %s\n' "$1" >&2
	failed=1
}

ok() {
	printf '\033[32mok\033[0m    %s\n' "$1"
}

need() {
	command -v "$1" >/dev/null && return 0
	fail "$2: $1 not found"
	return 1
}

# `-x` because scripts/lib/*.sh holds sourced libraries rather than commands:
# without it every `.` of one is an SC1091 "not following", and with it the
# library is checked in the context of the script that sources it as well as
# on its own.
check_shell() {
	need shellcheck shell || return
	if shellcheck -x install.sh scripts/*.sh scripts/lib/*.sh; then
		ok "shell: shellcheck clean"
	else
		fail "shell: shellcheck"
	fi
}

check_markdown() {
	need rumdl markdown || return
	need git markdown || return

	# An explicit file list, not `rumdl check .`. rumdl's directory walk finds
	# nothing inside a LINKED GIT WORKTREE — where `.git` is a file, not a
	# directory — and every thurbox worker runs in one of those, so a walk
	# would report this check green by checking no files at all. `git
	# ls-files` answers the same in a worktree and in a fresh clone.
	#
	# .rumdl.toml's excludes still apply to files named on the command line.
	local files=()
	while IFS= read -r -d '' f; do files+=("$f"); done < <(git ls-files -z -- '*.md')

	if [ ${#files[@]} -eq 0 ]; then
		fail "markdown: no tracked *.md files found"
		return
	fi

	local args=(check)
	[ "$fix" -eq 1 ] && args+=(--fix)

	if rumdl "${args[@]}" "${files[@]}"; then
		ok "markdown: rumdl clean (${#files[@]} tracked files)"
	else
		fail "markdown: rumdl"
	fi
}

# The README's links and the diagram it opens with. rumdl lints the prose and
# never where it points, and both failures here are silent on GitHub: a moved
# file is a link that 404s, and an SVG that does not parse is a broken-image
# icon. scripts/lib/check_docs.py's header names every rule.
check_docs() {
	need python3 docs || return
	need git docs || return

	if python3 scripts/lib/check_docs.py README.md; then
		ok "docs: README's relative links resolve to tracked files; its SVG diagram parses, is real text, loads nothing and has a dark palette"
	else
		fail "docs: scripts/lib/check_docs.py README.md"
	fi
}

check_yaml() {
	need python3 yaml || return
	need git yaml || return

	local files=()
	while IFS= read -r -d '' f; do files+=("$f"); done < <(git ls-files -z -- '*.yml' '*.yaml')

	if [ ${#files[@]} -eq 0 ]; then
		fail "yaml: no tracked *.yml/*.yaml files found"
		return
	fi

	if python3 scripts/lib/check_yaml.py "${files[@]}"; then
		ok "yaml: ${#files[@]} tracked files parse"
	else
		fail "yaml"
	fi
}

# CI's single required status is `All Checks`, so a job it does not need can
# fail while the pull request reports green. check_workflow.py holds that, a
# timeout on every job, and a job on windows-latest.
check_workflow() {
	need python3 workflow || return

	if python3 scripts/lib/check_workflow.py; then
		ok "workflow: All Checks needs every job, every job has a timeout, one runs on Windows"
	else
		fail "workflow: scripts/lib/check_workflow.py"
	fi
}

# The session profiles render into `thurbox-cli session create` flags, so the
# two rules that make a profile safe — no `THURBOX_*` key thurbox would
# discard, no `command` without the `reports_as` that keeps the session
# reporting — are only worth anything if a profile that breaks one cannot be
# committed. scripts/session-flags.sh owns those assertions; this runs them so
# there is one implementation rather than two.
#
# It does NOT check for secrets, and the profiles file says so rather than
# claiming a guarantee this cannot give. That one is a convention — the file is
# committed to a public repo, so nothing environment-specific goes in it — and
# not a gate.
check_profiles() {
	need python3 profiles || return

	local out
	if out="$(./scripts/session-flags.sh --check)"; then
		ok "profiles: $out"
	else
		fail "profiles: scripts/session-flags.sh --check"
	fi
}

# The task queue's claims. The queue makes claims that are easy to invert by
# accident: that independent work goes out all at once, that file overlap does
# NOT serialize, that a turn ending is not a task finishing. Each is a test
# against a throwaway queue, so a change that quietly reverses one fails here
# rather than in a run six weeks later.
#
# NOT this checkout's own records. `queue.sh check` over them used to run here,
# which validated 72 live topics in the control-plane checkout and an empty
# directory everywhere else — so a record nobody touched could fail a commit
# there that CI passed. Those records are the operator's, and
# `fleet-status.sh --records` is where they are validated now.
check_queue() {
	need python3 queue || return
	need uv queue || return

	if ./scripts/queue-selftest.sh >/dev/null; then
		ok "queue: ordering and wake claims hold"
	else
		# Re-run visibly: a failing claim is the whole message.
		./scripts/queue-selftest.sh
		fail "queue: scripts/queue-selftest.sh"
	fi

	# The claims already ported to pytest, with the harness they stand on. These
	# run natively on Windows as well; the bash above does not.
	if uv run --frozen --quiet pytest -q tests/test_harness.py tests/queue >/dev/null 2>&1; then
		ok "queue: the ported claims hold (tests/queue)"
	else
		uv run --frozen --quiet pytest -q tests/test_harness.py tests/queue
		fail "queue: tests/queue"
	fi
}

# The reconciler's lifecycle, the part of it that can break silently: it runs
# `collect`, so a second instance or a stop that does not stop costs closed
# tasks and reaped sessions.
# tests/reconcile proves adoption, a durable stop, and the three claims that
# are specific to it — that the four cadences are four separate clocks, that
# the ONLY things it ever asks the queue to do are watch, collect, shepherd,
# refuel and the read-only plan, and that the one line it sends the lead goes
# out on a transition rather than on every pass. It stubs the queue command and
# thurbox-cli, so it needs no thurbox, no `gh` and no network.
check_reconcile() {
	need uv reconcile || return
	if uv run --frozen --quiet pytest -q tests/reconcile >/dev/null 2>&1; then
		ok "reconcile: adopts rather than duplicates, a stop stays stopped, it writes no record, and it wakes the lead once per transition"
	else
		# Re-run visibly: a failing claim is the whole message.
		uv run --frozen --quiet pytest -q tests/reconcile
		fail "reconcile: tests/reconcile"
	fi
}

# The SessionStart hook, and the one script whose output is its only evidence.
# It runs before anyone is watching, so a refusal it reports that did not
# actually happen is believed: "offline" reads as a network blip, nobody looks,
# and every session inherits the stale `main` the script exists to prevent.
# That is how `timeout 15 git fetch` shipped — `timeout` is GNU coreutils and is
# absent on a stock macOS, so it exited 127 and every Mac session reported an
# unreachable origin while the network was fine. tests/sync holds the fetch to
# the child's own timeout with tripwires standing in for `timeout` and
# `gtimeout`, and the rest of the contract around it: a refusal changes no
# tracked state, a real outage is still reported, a fast-forward that brings
# instructions raises restart-lead, and the hook is one command every shell
# parses the same. The registry and add-owner claims run with it.
check_sync() {
	need git sync || return
	need uv sync || return

	if uv run --frozen --quiet pytest -q tests/sync >/dev/null 2>&1; then
		ok "sync: bounds the fetch without coreutils, refuses without touching the tree, and raises the hand-over"
	else
		# Re-run visibly: a failing claim is the whole message.
		uv run --frozen --quiet pytest -q tests/sync
		fail "sync: tests/sync"
	fi
}

# The status command's two promises, both invisible until they cost something.
# It must DEGRADE — a missing `gh`, a missing thurbox, a missing queue each
# cost exactly their own section and never the reading — and it must carry
# thurbox's state vocabulary through unflattened, because reporting `uncovered`
# or `unreported` as `idle` tells the lead a worker mid-turn has finished. Both
# are only observable with those things MISSING, which is never the state a
# gate run is in, so tests/status constructs it out of stand-ins on a PATH
# holding no tool at all. It also holds the third promise: the command reads
# and writes nothing.
check_status() {
	need uv status || return
	need git status || return

	if uv run --frozen --quiet pytest -q tests/status >/dev/null 2>&1; then
		ok "status: degrades a section at a time, keeps thurbox's words, writes nothing"
	else
		# Re-run visibly: a failing claim is the whole message.
		uv run --frozen --quiet pytest -q tests/status
		fail "status: tests/status"
	fi
}

# The `fleet` command, and the two scripts that forward to it: queue.sh and
# fleet-status.sh give exactly what `fleet` gives, and what it writes stays
# UTF-8 on a cp1252 console. The lock comes first because the forwarders run
# `--frozen`, which never notices a uv.lock that no longer matches
# pyproject.toml. tests/test_cli.py is stdlib unittest, so it runs natively on
# Windows too, where it skips the bash half.
check_cli() {
	need uv cli || return

	if ! uv lock --check >/dev/null 2>&1; then
		uv lock --check
		fail "cli: uv.lock does not match pyproject.toml — run \`uv lock\`"
		return
	fi
	if uv run --frozen --quiet python -m unittest discover -s tests >/dev/null 2>&1; then
		ok "cli: the forwarders give what fleet gives, and fleet writes UTF-8 on any console"
	else
		# Re-run visibly: a failing claim is the whole message.
		uv run --frozen --quiet python -m unittest discover -s tests
		fail "cli: tests/test_cli.py"
	fi
}

# The agent-agnostic skills layout: `.agents/skills/` holds the real files and
# `.claude/skills` is a symlink to it, so one copy serves every CLI. Two ways
# that breaks silently and this catches both — a clone with `core.symlinks`
# off (Windows) materialises the link as a text file holding its target, and a
# hand-added skill can land under `.claude/` where only Claude Code sees it.
# The pane, its installer and the three documents that tell an operator how to
# place it, held to one spelling of the two strings that must agree.
#
# NOT A LUA LINTER, and deliberately not. `selene` and `stylua` are the tools
# that would lint this file, and neither is on the CI runners or on a fresh
# clone — and this gate FAILS on a missing tool rather than skipping, on purpose
# (see the header), so requiring one would make a green run impossible for
# anyone who has not installed a Rust toolchain. The pane's own gate is
# `thurbox-cli plugin check`, which loads it the way thurbox does; that needs a
# thurbox install, so it belongs at install time and not here.
#
# What DOES belong here is the failure this repo can cause on its own. The pane
# names a slot, and `fleet install-extension`, the onboarding skill and the
# fleet-pane skill each print a `layout.lua` block naming that slot. If any of
# them drifts, the operator is handed a block that places a slot nothing
# fills: the pane loads, lists, and draws nothing, and every message they have
# says it should work.
check_pane() {
	need uv pane || return
	# tests/pane renders the pane with lua and skips that without one; this
	# gate fails on a missing tool rather than skipping, so it asks first.
	need lua pane || return

	if uv run --frozen --quiet pytest -q tests/pane >/dev/null 2>&1; then
		ok "pane: slot, chord, fuel source and lead name agree, and the render holds at 44 and 30 columns (tests/pane)"
	else
		uv run --frozen --quiet pytest -q tests/pane
		fail "pane: tests/pane"
	fi
}

check_skills() {
	local link=".claude/skills"

	if [ ! -L "$link" ]; then
		fail "skills: $link is not a symlink (git config core.symlinks=true, then re-checkout)"
		return
	fi

	local target
	target="$(readlink "$link")"
	if [ "$target" != "../.agents/skills" ]; then
		fail "skills: $link points at '$target', expected '../.agents/skills'"
		return
	fi

	if [ ! -d "$link/" ]; then
		fail "skills: $link does not resolve to a directory"
		return
	fi

	local missing=0 d
	for d in .agents/skills/*/; do
		[ -d "$d" ] || continue
		if [ ! -f "$d/SKILL.md" ]; then
			fail "skills: ${d}SKILL.md is missing"
			missing=1
		fi
	done
	[ "$missing" -eq 0 ] && ok "skills: $link -> $target resolves; every skill has a SKILL.md"
}

# THE VOICE, WHICH IS A SETTING AND NOT A LITERAL. `FLEET.md` is the lead's
# standing context and the source of the extension's payload, and the two names
# in it — what the lead calls the operator and what it answers to — are the
# operator's choice, exactly as the session glyph is. So `orchestration/voice.example.conf`
# is the one place they are spelled, `fleet install-extension` renders them
# into the gitignored `FLEET.rendered.md` the manifest actually ships, and
# `FLEET.md` carries placeholders. A name written into FLEET.md would be a
# second copy of the setting that no `voice.conf` could move.
check_voice() {
	need uv voice || return

	if uv run --frozen --quiet pytest -q tests/extension >/dev/null 2>&1; then
		ok "voice: orchestration/voice.example.conf renders into FLEET.md's placeholders; the extension and both asks hold (tests/extension)"
	else
		uv run --frozen --quiet pytest -q tests/extension
		fail "voice: tests/extension"
	fi
}

# WHERE FLEET MAY MERGE, WHICH IS THE OPERATOR'S AND NOT THIS REPO'S. The
# allowlist used to be a literal in scripts/lib/queue.py, so naming a
# repository meant committing it to a PUBLIC repo, and every clone inherited
# the last operator's merge rights. It is orchestration/auto-merge.conf now —
# the operator's, gitignored — and this gate is what keeps it from drifting
# back: the tracked copy must name NOTHING, and the set a fresh clone would
# read must come out empty. Textual, because the failure mode is: somebody adds
# "just one" entry to the shipped file and it ships to everybody.
check_automerge() {
	need python3 automerge || return

	local example="orchestration/auto-merge.example.conf" miss=0
	if [ ! -f "$example" ]; then
		fail "automerge: $example is missing; a fresh clone would document no format"
		return
	fi

	# Every line with its comment cut off. Anything left is an entry, and an
	# entry here is one operator's repository published in everybody's copy.
	local live
	live="$(sed 's/#.*//' "$example" | grep -E '[^[:space:]]')"
	if [ -n "$live" ]; then
		fail "automerge: $example names a repository; the tracked copy must name none"
		printf '%s\n' "$live" | sed 's/^/          /' >&2
		miss=1
	fi

	# And the set itself, read the way `shepherd` reads it — from a root that
	# holds the tracked example and nothing else, which is exactly what a fresh
	# clone holds. Never this checkout's root: an operator's auto-merge.conf
	# there would answer instead, and the gate used to answer `skip` rather than
	# judge somebody's private list, so the claim went unproven in the one
	# checkout that has one.
	local shipped fresh
	fresh="$(mktemp -d)" || {
		fail "automerge: could not make a temp directory for the fresh-clone reading"
		return
	}
	mkdir -p "$fresh/orchestration"
	cp "$example" "$fresh/orchestration/"
	shipped="$(env -u FLEET_AUTO_MERGE_ROOT FLEET_AUTO_MERGE_REPOS='' python3 - "$fresh" <<'PY' 2>&1
import sys
sys.path.insert(0, "scripts/lib")
import queue as q
print("entries=" + (" ".join(sorted(q.auto_merge_repos(sys.argv[1]))) or "none"))
PY
)"
	rm -rf "$fresh"
	if [ "$shipped" != "entries=none" ]; then
		fail "automerge: a fresh clone would inherit a merge allowlist: $shipped"
		miss=1
	fi

	# THE SAME RULE FOR EVERY TRACKED SETTING. An owner, a repository, a
	# publishing tool or an agent written into a file this repo SHIPS is one
	# operator's setup handed to every clone. The example files carry defaults;
	# none of them may carry a name.
	local pub="orchestration/publish.example.conf"
	local ag="orchestration/agent.example.conf"
	local f val
	for f in "$pub" "$ag"; do
		if [ ! -f "$f" ]; then
			fail "automerge: $f is missing; a fresh clone would document no format"
			miss=1
		fi
	done
	# A tool name reaches a worker only through HOW, which nothing parses. The
	# tracked copy must leave it empty, and the method must be the one shape
	# that needs no tool at all.
	# EVERY occurrence, not the first: a leak appended below a correct line is
	# exactly the edit a first-match read would wave through.
	if [ -f "$pub" ]; then
		val="$(sed -n 's/^METHOD=//p' "$pub" | tr -d '[:space:]')"
		[ "$val" = pr ] ||
			{ fail "automerge: $pub ships METHOD=$val; the tracked default must be pr"; miss=1; }
		val="$(sed -n 's/^HOW=//p' "$pub" | tr -d '[:space:]')"
		[ -z "$val" ] ||
			{ fail "automerge: $pub names a tool in HOW ($val); that is the operator's"; miss=1; }
	fi
	# An agent or a provider here would gate every operator's fleet on one
	# operator's vendor. Empty means "thurbox's own" and "derive it".
	if [ -f "$ag" ]; then
		for key in AGENT FUEL_PROVIDER LIMIT_BANNER TRANSCRIPT_DIR \
			AGENT_PROVIDERS TRUST_SIGNATURE TRUST_KEYS; do
			val="$(sed -n "s/^$key=//p" "$ag" | tr -d '[:space:]')"
			[ -z "$val" ] ||
				{ fail "automerge: $ag ships $key=$val; that is the operator's"; miss=1; }
		done
	fi
	# And no method may be a tool name again: the five are artifact shapes.
	local shapes
	shapes="$(python3 -c '
import sys
sys.path.insert(0, "scripts/lib")
import queue as q
print(" ".join(sorted(q.PUBLISH_METHODS)))
' 2>&1)"
	[ "$shapes" = "attested none note pr push" ] ||
		{ fail "automerge: the publish methods are '''$shapes''', and must be artifact shapes"; miss=1; }

	[ "$miss" -eq 0 ] &&
		ok "automerge: no tracked setting names a repository, a tool or an agent, and a fresh clone merges nowhere"
}

# THE SETUP NOBODY RE-RUNS. Onboarding's scripts — preflight, discover-owners,
# place-pane — are the ones every operator runs once and never again, so a
# regression in them is invisible to everyone who is already set up and total
# for everyone who is not. `onboarding-selftest.sh` drives those three offline,
# and sync-registry.sh and add-owner.sh with them: those two are re-run rather
# than run once, but each reads across every `gh` account, which needs a second
# login to exercise and so is unreachable on a machine with one. All of it runs
# against stubs on a PATH built from scratch and a copy of a stock layout, and
# its header argues each claim — including the two seams a grep here could
# only assert about source text: the pane's
# slot has ONE spelling (§3c places a RENAMED pane and reads the slot back out
# of the block) and the thurbox floor has one owner (§1c reads it from the
# manifest and expects it in the remedy).
check_onboarding() {
	if ./scripts/onboarding-selftest.sh >/dev/null 2>&1; then
		ok "onboarding: scripts/onboarding-selftest.sh"
	else
		./scripts/onboarding-selftest.sh
		fail "onboarding: scripts/onboarding-selftest.sh"
	fi
}

# THE ONE-LINER AND THE FIRST-RUN ASK, driven as an operator meets them.
# `install-selftest.sh` pipes install.sh into `sh` in a throwaway HOME against
# a copy of this tree: prerequisites before the extension, the pane installed
# and not placed, a second run that changes nothing, and a checkout that is
# fast-forwarded or refused but never overwritten. Then `pane-ask.sh`: asked
# once, both answers remembered, a placed pane never asked about. Then
# `voice-ask.sh`: the two names recorded and rendered, a refused one writing
# nothing, an answered voice.conf never overwritten unasked.
check_install() {
	need git install || return
	need jq install || return

	if ./scripts/install-selftest.sh >/dev/null 2>&1; then
		ok "install: install.sh converges and places no pane; pane-ask.sh and voice-ask.sh ask once"
	else
		./scripts/install-selftest.sh
		fail "install: scripts/install-selftest.sh"
	fi
}

# ONE COMMIT, ONE VERDICT. The header's promise that no check reads operator
# state, held by building the worst case: a poisoned copy of the tree under a
# hostile host, with every check that reads settings or records and every
# selftest cheap enough to run twice re-run inside it. Seconds, because the two
# slow selftests are held to the shared helper rather than re-run.
check_isolation() {
	need git isolation || return
	need jq isolation || return
	need python3 isolation || return
	need uv isolation || return

	if ./scripts/isolation-selftest.sh >/dev/null 2>&1; then
		ok "isolation: a poisoned checkout under a hostile host gets the same verdict"
	else
		./scripts/isolation-selftest.sh
		fail "isolation: scripts/isolation-selftest.sh"
	fi
}

checks=()
for arg in "$@"; do
	case "$arg" in
	--fix) fix=1 ;;
	*) checks+=("$arg") ;;
	esac
done

if [ ${#checks[@]} -eq 0 ]; then
	checks=(shell markdown docs yaml workflow profiles queue cli reconcile status skills pane voice automerge onboarding install sync isolation)
fi

for c in "${checks[@]}"; do
	case "$c" in
	shell) check_shell ;;
	markdown) check_markdown ;;
	docs) check_docs ;;
	yaml) check_yaml ;;
	workflow) check_workflow ;;
	profiles) check_profiles ;;
	queue) check_queue ;;
	cli) check_cli ;;
	reconcile) check_reconcile ;;
	status) check_status ;;
	sync) check_sync ;;
	skills) check_skills ;;
	pane) check_pane ;;
	voice) check_voice ;;
	automerge) check_automerge ;;
	onboarding) check_onboarding ;;
	install) check_install ;;
	isolation) check_isolation ;;
	*)
		printf 'error: unknown check %q (want: shell markdown docs yaml workflow profiles queue cli reconcile status skills pane voice automerge onboarding install sync isolation)\n' "$c" >&2
		exit 2
		;;
	esac
done

exit "$failed"
