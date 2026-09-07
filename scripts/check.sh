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
# Checks: shell, markdown, yaml, skills. Only `markdown` has a fixer; `--fix`
# is a no-op for the rest, so `scripts/check.sh --fix` is always safe to run.
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
	checks=(shell markdown yaml skills)
fi

for c in "${checks[@]}"; do
	case "$c" in
	shell) check_shell ;;
	markdown) check_markdown ;;
	yaml) check_yaml ;;
	skills) check_skills ;;
	*)
		printf 'error: unknown check %q (want: shell markdown yaml skills)\n' "$c" >&2
		exit 2
		;;
	esac
done

exit "$failed"
