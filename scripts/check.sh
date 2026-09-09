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
# Checks: shell, markdown, yaml, profiles, queue, webui, status, skills, pane. Only `markdown` has a
# fixer; `--fix` is a no-op for the rest, so `scripts/check.sh --fix` is
# always safe to run.
#
# Requires: shellcheck, rumdl, python3 (with PyYAML), curl. A missing tool fails the
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

# The task queue, in two halves. `queue.sh check` validates the local
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

# The monitor's lifecycle, which is the part of it that can break silently. It
# claims to adopt a running server rather than start a second one, and it
# claims that an explicit `stop` survives the next `ensure` — the call the
# onboarding skill makes. A regression in either is invisible until the day it
# costs something: a duplicate server over one queue, or a monitor the operator
# asked down that comes back up on its own. webui-selftest.sh binds a real
# socket on a port well away from the default and proves both.
check_webui() {
	need python3 webui || return
	need curl webui || return

	if ./scripts/webui-selftest.sh >/dev/null; then
		ok "webui: adopts rather than duplicates, and a stop stays stopped"
	else
		# Re-run visibly: a failing claim is the whole message.
		./scripts/webui-selftest.sh
		fail "webui: scripts/webui-selftest.sh"
	fi
}

# The status command's two promises, both invisible until they cost something.
# It must DEGRADE — a missing `gh`, a missing thurbox, a monitor that is down
# each cost exactly their own section and never the reading — and it must carry
# thurbox's state vocabulary through unflattened, because reporting `uncovered`
# or `unreported` as `idle` tells the lead a worker mid-turn has finished. Both
# are only observable with those things MISSING, which is never the state a
# gate run is in, so the selftest constructs it out of stubs on a sandboxed
# PATH. It also holds the third promise: the command reads and writes nothing.
check_status() {
	need python3 status || return
	need git status || return

	if ./scripts/fleet-status-selftest.sh >/dev/null; then
		ok "status: degrades a section at a time, keeps thurbox's words, writes nothing"
	else
		# Re-run visibly: a failing claim is the whole message.
		./scripts/fleet-status-selftest.sh
		fail "status: scripts/fleet-status-selftest.sh"
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
# names a slot, and `install-extension.sh`, the onboarding skill and the
# fleet-pane skill each print a `layout.lua` block naming that slot. If any of
# them drifts, the operator is handed a block that places a slot nothing
# fills: the pane loads, lists, and draws nothing, and every message they have
# says it should work.
check_pane() {
	local pane="interface/fleet_queue.lua"

	if [ ! -f "$pane" ]; then
		fail "pane: $pane is missing"
		return
	fi

	local slot
	slot="$(sed -n 's/^local SLOT = "\(.*\)"$/\1/p' "$pane" | head -1)"
	if [ -z "$slot" ]; then
		fail "pane: could not read the slot name from $pane"
		return
	fi

	# The files that DOCUMENT the placement block, which the README deliberately
	# does not: it is a setup step, two skills walk an operator through it, and
	# the installer prints the block at the moment it is needed.
	local f miss=0
	for f in scripts/install-extension.sh .agents/skills/fleet-onboarding/SKILL.md .agents/skills/fleet-pane/SKILL.md; do
		grep -q "slot = \"$slot\"" "$f" || {
			fail "pane: $f does not name slot \"$slot\" in a layout.lua line"
			miss=1
		}
		# The guard, and not only the slot. A placement block without
		# `panels.shown` is carved on every frame, so the pane's F-key flips a
		# panel state nothing reads and the column opens and never closes —
		# which `thurbox-cli plugin check` cannot catch, because the pane DOES
		# draw. That shipped once; this is what keeps it from shipping twice.
		grep -q "panels.shown(\"$slot\")" "$f" || {
			fail "pane: $f documents slot \"$slot\" without the panels.shown guard, so its F-key would not close the column"
			miss=1
		}
	done

	# The installed name, which the installer's own header documents as the
	# argument to `plugin remove`. It is the destination PATH and not its
	# basename — `plugin remove 91_fleet_queue.lua` answers "not listed in
	# plugins.toml" and removes nothing, which is how this check earned its
	# place: the docs said the basename until the command was actually run.
	local dest
	dest="$(sed -n 's/^PANE_DEST="\(.*\)"$/\1/p' scripts/install-extension.sh | head -1)"
	if [ -z "$dest" ]; then
		fail "pane: could not read PANE_DEST from scripts/install-extension.sh"
		miss=1
	elif ! grep -q "plugin remove $dest" scripts/install-extension.sh; then
		fail "pane: scripts/install-extension.sh does not document 'plugin remove $dest'"
		miss=1
	fi

	# The chord must not be one the KERNEL already owns. A plugin-scoped
	# binding does not outrank a kernel one, and the failure is silent in the
	# worst way: the pane still registers, `ui.chord` still finds it, the pane's
	# own title still advertises it — and the key never reaches the pane,
	# because thurbox's action band answers it first. F6 shipped exactly like
	# that, reading "F6 hides" in a title while F6 opened Settings.
	local reserved="f1 f4 f6 f10 f12"

	# The chord, which the docs promise and only the pane binds.
	local chord
	chord="$(sed -n 's/^      key = "\(f[0-9]*\)",$/\1/p' "$pane" | head -1)"
	if [ -z "$chord" ]; then
		fail "pane: could not read the F-key from $pane"
		miss=1
	else
		case " $reserved " in
		*" $chord "*)
			fail "pane: $chord is a kernel chord (help/theme/settings/reload/perf); a plugin binding loses to it and the key would never reach the pane"
			miss=1
			;;
		esac
		local upper
		upper="$(printf '%s' "$chord" | tr '[:lower:]' '[:upper:]')"
		for f in scripts/install-extension.sh README.md .agents/skills/fleet-pane/SKILL.md; do
			grep -q "$upper" "$f" || {
				fail "pane: $f does not mention the pane's $upper chord"
				miss=1
			}
		done
	fi

	# ONE SOURCE FOR THE FUEL READING. The pane draws the account's fuel, and
	# the only place that reading exists is `probe_fuel()` in
	# scripts/lib/fleet_status.py. A pane that ran `quota-axi` itself would be
	# a second parse of a document it does not own, disagreeing with the screen
	# the moment either side is touched — so the pane asks the flag, and the
	# flag has to still be there.
	if ! grep -q -- "fleet-status.sh --fuel" "$pane"; then
		fail "pane: $pane does not read fuel through 'fleet-status.sh --fuel'"
		miss=1
	fi
	if ! grep -q -- '"--fuel"' scripts/lib/fleet_status.py; then
		fail "pane: scripts/lib/fleet_status.py no longer offers --fuel, which the pane's probe calls"
		miss=1
	fi
	# Comment lines dropped first: the pane's header has to be able to EXPLAIN
	# that it does not read quota-axi. What is banned is code that does.
	if grep -v '^[[:space:]]*--' "$pane" | grep -q "quota-axi"; then
		fail "pane: $pane reads quota-axi itself; the reading comes from fleet-status.sh, never from a second parse"
		miss=1
	fi

	# AND ONE VOCABULARY. The record is `name<TAB>value` lines with a blank
	# line between providers, and the pane is the only reader of it — so a
	# field renamed on one side and not the other costs the pane exactly that
	# fact, silently, with both files still perfectly valid. Every `fields.x`
	# the pane reads has to be a name `RECORD_FIELDS` actually emits.
	local wire fields f
	wire="$(sed -n '/^RECORD_FIELDS = (/,/^)/p' scripts/lib/fleet_status.py |
		tr -d ' \t"' | tr ',' '\n' | grep -E '^[a-z_]+$')"
	fields="$(grep -oE 'fields\.[a-z_]+' "$pane" | sed 's/^fields\.//' | sort -u)"
	for f in $fields; do
		grep -qx "$f" <<<"$wire" || {
			fail "pane: $pane reads a '$f' field that scripts/lib/fleet_status.py's RECORD_FIELDS does not emit"
			miss=1
		}
	done

	# AND ONE THRESHOLD, WHICH THE PANE NEVER SPELLS. FLEET.md's `## Fuel`
	# section owns the reserve, `fleet_status.py` carries the same number, and
	# it travels down on every record — so the pane compares against what it
	# was handed and colours a bar by it. A literal here is a second copy of a
	# rule that would then move in one place and not the other.
	local reserve
	reserve="$(sed -n 's/^FUEL_RESERVE = \([0-9]*\)$/\1/p' scripts/lib/fleet_status.py | head -1)"
	if [ -z "$reserve" ]; then
		fail "pane: could not read FUEL_RESERVE from scripts/lib/fleet_status.py"
		miss=1
	elif grep -v '^[[:space:]]*--' "$pane" | grep -qE "(^|[^0-9])$reserve([^0-9]|\$)"; then
		fail "pane: $pane spells the reserve threshold ($reserve) itself; it arrives on the record, so the pane compares and never states it"
		miss=1
	fi

	# NO VARIATION SELECTOR, AND NOTHING BUILT OUT OF ONE. The pane's fuel
	# glyph is a bare codepoint on purpose: U+FE0F asks for an emoji
	# presentation the terminal may not have, adds a character some terminals
	# count as a column and others do not, and a zero-width joiner builds a
	# glyph whose width nothing agrees on. Every row here is budgeted in
	# cells, so a character the painter and the terminal measure differently
	# shears the whole column.
	if LC_ALL=C grep -qP '\xef\xb8\x8f|\xef\xb8\x8e|\xe2\x80\x8d' "$pane" 2>/dev/null; then
		fail "pane: $pane carries a variation selector or a zero-width joiner; the fuel glyph is a bare codepoint so its width is one both sides agree on"
		miss=1
	fi

	# AND ONE COST MODEL. `quota-axi` makes a network call, so the fuel probe
	# must not run at the queue probe's cadence — a pane that refetched it
	# every ten seconds would burn the fuel it is reporting.
	local ttl fuel_ttl
	ttl="$(sed -n 's/^local TTL = \([0-9]*\)$/\1/p' "$pane" | head -1)"
	fuel_ttl="$(sed -n 's/^local FUEL_TTL = \([0-9]*\)$/\1/p' "$pane" | head -1)"
	if [ -z "$ttl" ] || [ -z "$fuel_ttl" ]; then
		fail "pane: could not read TTL and FUEL_TTL from $pane"
		miss=1
	elif [ "$fuel_ttl" -le "$ttl" ]; then
		fail "pane: FUEL_TTL ($fuel_ttl s) is not longer than the queue's TTL ($ttl s); the fuel probe hits the network"
		miss=1
	fi

	[ "$miss" -eq 0 ] && ok "pane: slot \"$slot\", $dest and $chord agree across installer and docs; fuel is one record per provider at ${fuel_ttl}s"
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

checks=()
for arg in "$@"; do
	case "$arg" in
	--fix) fix=1 ;;
	*) checks+=("$arg") ;;
	esac
done

if [ ${#checks[@]} -eq 0 ]; then
	checks=(shell markdown yaml profiles queue webui status skills pane)
fi

for c in "${checks[@]}"; do
	case "$c" in
	shell) check_shell ;;
	markdown) check_markdown ;;
	yaml) check_yaml ;;
	profiles) check_profiles ;;
	queue) check_queue ;;
	webui) check_webui ;;
	status) check_status ;;
	skills) check_skills ;;
	pane) check_pane ;;
	*)
		printf 'error: unknown check %q (want: shell markdown yaml profiles queue webui status skills pane)\n' "$c" >&2
		exit 2
		;;
	esac
done

exit "$failed"
