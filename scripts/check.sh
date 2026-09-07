#!/usr/bin/env bash
# The control plane's whole gate, in one place.
#
# CI runs this (one check per job, so a docs-only PR skips the shell job), the
# prek hooks run it, and `.no-mistakes.yaml` points its `lint` command at it.
# One definition means a green local run and a green CI run mean the same
# thing — the failure this repo is most exposed to, because CI only fires on
# pull requests while routine control-plane changes go straight to `main`.
#
# Usage:
#   scripts/check.sh                     # every check
#   scripts/check.sh shell yaml          # only the named ones
#   scripts/check.sh --fix markdown      # apply the fixes a check can apply
#
# Checks: shell, markdown, yaml, profiles, queue, skills. Only `markdown` has a
# fixer; `--fix` is a no-op for the rest, so `scripts/check.sh --fix` is
# always safe to run.
#
# Requires: shellcheck, rumdl, python3 (with PyYAML). A missing tool fails the
# check rather than skipping it — a gate that silently passes when its linter
# is absent is worse than no gate.

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

check_shell() {
	need shellcheck shell || return
	if shellcheck scripts/*.sh; then
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
		ok "yaml: ${#files[@]} tracked files parse, registry shape holds"
	else
		fail "yaml"
	fi
}

# The session profiles render into `thurbox-cli session create` flags, so the
# two rules that make a profile safe — no `THURBOX_*` key thurbox would
# discard, no `command` without the `reports_as` that keeps the session
# reporting — are only worth anything if a profile that breaks one cannot be
# committed. scripts/session-flags.sh owns those assertions; this runs them
# over both profile layers so there is one implementation rather than two.
#
# It does NOT check for secrets, and the profiles file says so rather than
# claiming a guarantee this cannot give. That rule is a convention with a home
# — the gitignored `session-profiles.local.yaml` — not a gate.
check_profiles() {
	need python3 profiles || return

	local out
	if out="$(./scripts/session-flags.sh --check)"; then
		ok "profiles: $out"
	else
		fail "profiles: scripts/session-flags.sh --check"
	fi
}

# The task queue, in two halves. `queue.sh check` validates THIS instance's
# records — a blocker naming a task that no longer exists, a state word nobody
# defined — and says so and passes when the queue has never been used, the way
# check_yaml.py treats an unsynced registry.
#
# `queue-selftest.sh` is the other half and the more important one. The queue
# makes claims that are easy to invert by accident: that independent work goes
# out all at once, that file overlap does NOT serialize, that a turn ending is
# not a task finishing. Each is a test against a throwaway queue, so a change
# that quietly reverses one fails here rather than in a run six weeks later.
check_queue() {
	need python3 queue || return

	local out
	if ! out="$(./scripts/queue.sh check)"; then
		fail "queue: ./scripts/queue.sh check"
		return
	fi
	if ./scripts/queue-selftest.sh >/dev/null; then
		ok "queue: ${out#queue check: }, ordering and wake claims hold"
	else
		# Re-run visibly: a failing claim is the whole message.
		./scripts/queue-selftest.sh
		fail "queue: scripts/queue-selftest.sh"
	fi
}

# The agent-agnostic skills layout: `.agents/skills/` holds the real files and
# `.claude/skills` is a symlink to it, so one copy serves every CLI. Two ways
# that breaks silently and this catches both — a clone with `core.symlinks`
# off (Windows) materialises the link as a text file holding its target, and a
# hand-added skill can land under `.claude/` where only Claude Code sees it.
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

checks=()
for arg in "$@"; do
	case "$arg" in
	--fix) fix=1 ;;
	*) checks+=("$arg") ;;
	esac
done

if [ ${#checks[@]} -eq 0 ]; then
	checks=(shell markdown yaml profiles queue skills)
fi

for c in "${checks[@]}"; do
	case "$c" in
	shell) check_shell ;;
	markdown) check_markdown ;;
	yaml) check_yaml ;;
	profiles) check_profiles ;;
	queue) check_queue ;;
	skills) check_skills ;;
	*)
		printf 'error: unknown check %q (want: shell markdown yaml profiles queue skills)\n' "$c" >&2
		exit 2
		;;
	esac
done

exit "$failed"
