#!/usr/bin/env bash
# Everything fleet needs on this machine, in one pass, with the remedy for each.
#
# WHY THIS IS A SCRIPT AND NOT A CHECKLIST IN A SKILL. Onboarding used to probe
# these one at a time in prose, which has two failure modes and this repo has
# hit both: an operator learns about a missing tool one restart at a time, and
# the list drifts — `quota-axi` was load-bearing for `queue.sh refuel` and the
# fuel rows in the pane for months while no setup document mentioned it. One
# table, one owner.
#
# IT WRITES NOTHING AND INSTALLS NOTHING. It probes, and prints the command
# that would fix each gap. Installing is the operator's call — a package
# manager is the one part of this setup that touches the machine outside the
# checkout — so `--commands` exists to hand those lines to whoever said yes.
#
# THREE TIERS, because "missing" does not mean one thing:
#
#   required     fleet cannot run. Missing one is a non-zero exit.
#   recommended  a named capability degrades and the rest still works —
#                so it is reported, never fatal.
#   gate         only `./scripts/check.sh` needs it. A control plane that
#                never pushes a change never needs these.
#
# Usage:
#   scripts/preflight.sh                  # the table, grouped by tier
#   scripts/preflight.sh --commands       # just the install lines for what is missing
#   scripts/preflight.sh --tier required  # only that tier (repeatable)
#
# `--tier` is what makes "install the required ones only" a command rather than
# a judgement call about which lines to copy out of a longer list.
#
# Exit: 0 when every REQUIRED dependency is present and authenticated, 1 when
# one is not, 2 on a usage error. Recommended and gate gaps never fail it.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

# The two authentication rows are PER ACCOUNT and PER HOST, and both seams are
# shared with the scripts that do the real reading rather than reimplemented
# here. See each row below for what its CLI's bare status command gets wrong.
# shellcheck source=scripts/lib/gh-accounts.sh
. scripts/lib/gh-accounts.sh
# shellcheck source=scripts/lib/glab-hosts.sh
. scripts/lib/glab-hosts.sh

MODE="text"
TIERS=""
while [ $# -gt 0 ]; do
	case "$1" in
	--commands) MODE="commands" ;;
	--tier)
		case "${2:-}" in
		required | recommended | gate) TIERS="$TIERS ${2}" ;;
		*)
			printf 'usage: --tier required|recommended|gate\n' >&2
			exit 2
			;;
		esac
		shift
		;;
	-h | --help)
		sed -n '2,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
		exit 0
		;;
	*)
		printf 'usage: %s [--commands] [--tier required|recommended|gate]...\n' "$0" >&2
		exit 2
		;;
	esac
	shift
done

# --- how a package is installed HERE ------------------------------------------
#
# One detection, used by every row that has a distro package. A tool with no
# package on this platform falls back to its own installer, which is why each
# row carries a URL as well as a package name.

PKG=""
for candidate in apt-get dnf pacman zypper apk brew; do
	if command -v "$candidate" >/dev/null 2>&1; then
		PKG="$candidate"
		break
	fi
done

# pkg_cmd <apt-name> <dnf-name> <pacman-name> <brew-name>
# Empty answer means "no package on this platform" — the caller falls back.
pkg_cmd() {
	case "$PKG" in
	apt-get) [ -n "$1" ] && printf 'sudo apt-get install -y %s' "$1" ;;
	dnf) [ -n "$2" ] && printf 'sudo dnf install -y %s' "$2" ;;
	pacman) [ -n "$3" ] && printf 'sudo pacman -S --needed %s' "$3" ;;
	zypper) [ -n "$2" ] && printf 'sudo zypper install -y %s' "$2" ;;
	apk) [ -n "$1" ] && printf 'sudo apk add %s' "$1" ;;
	brew) [ -n "$4" ] && printf 'brew install %s' "$4" ;;
	esac
}

# --- the results table --------------------------------------------------------
#
# Rows accumulate as UNIT-SEPARATED records so every output mode reads the same
# data: tier, name, state, detail, why, remedy. The separator is US (0x1f) and
# not a tab on purpose: a tab is IFS whitespace, so `read` collapses two of
# them into one and an EMPTY field — a tool with no version, a row with no
# remedy — silently shifts every field after it by one.

US=$'\x1f'
ROWS=()
required_missing=0

record() {
	local tier="$1" name="$2" state="$3" detail="$4" why="$5" remedy="$6"
	ROWS+=("$tier$US$name$US$state$US$detail$US$why$US$remedy")
	if [ "$tier" = "required" ] && [ "$state" != "ok" ]; then
		required_missing=$((required_missing + 1))
	fi
}

# First version-shaped token anything prints, which is enough for a report and
# is never parsed for a comparison except by the one row that has a floor.
version_of() {
	local tool="$1" out="" flag
	# `--version` first, `-v` after it: lua answers only the short one, and a
	# tool that answers neither gets an empty column rather than a stall.
	for flag in --version -v; do
		out="$("$tool" "$flag" 2>&1 | head -3 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)"
		[ -n "$out" ] && break
	done
	printf '%s' "$out"
}

# have <tier> <tool> <why> <remedy>
have() {
	local tier="$1" tool="$2" why="$3" remedy="$4"
	if command -v "$tool" >/dev/null 2>&1; then
		record "$tier" "$tool" ok "$(version_of "$tool")" "$why" ""
		return 0
	fi
	record "$tier" "$tool" missing "" "$why" "$remedy"
	return 1
}

# --- required -----------------------------------------------------------------

git_install="$(pkg_cmd git git git git)"
have required git \
	"every checkout, and the worktree each worker gets" \
	"${git_install:-see https://git-scm.com/downloads}"

gh_install="$(pkg_cmd gh gh github-cli gh)"
if have required gh \
	"builds the repo map from registry/owners.txt, and is fleet's GitHub forge adapter" \
	"${gh_install:-see https://cli.github.com}"; then
	# Authentication is a second, separate fact about the same tool: an
	# installed `gh` that cannot answer `user/repos` fails the registry sync
	# with an error that reads like a network problem.
	#
	# ASKED PER ACCOUNT, because `gh auth status` is all-or-nothing across
	# every login on every host: one lapsed token among three made this row
	# report `missing` while two working accounts sat there — and `gh auth`
	# is REQUIRED, so that is a whole setup called unrunnable because one
	# credential of three had expired. The per-account answer already exists
	# and the map and discovery both read it; this asks that same seam rather
	# than writing a second enumeration.
	gh_why="the registry sync reads GitHub as you — EVERY account, no PAT, no CI secret"
	gh_warn="$(mktemp)"
	gh_logins=()
	while IFS= read -r acct; do
		[ -n "$acct" ] && gh_logins+=("$acct")
	done < <(gh_accounts "${GH_HOST:-github.com}" 2>"$gh_warn")

	# The seam's own words about a login it skipped, passed through verbatim.
	# Counting them here and swallowing the lines would report how many
	# accounts are broken without naming WHICH, and an account that goes
	# missing in silence is the failure that whole file is about.
	gh_skipped=0
	if [ -s "$gh_warn" ]; then
		gh_skipped="$(grep -c '' "$gh_warn")"
		cat "$gh_warn" >&2
	fi
	rm -f "$gh_warn"

	if [ "${#gh_logins[@]}" -gt 0 ]; then
		gh_detail=""
		for login in "${gh_logins[@]}"; do
			gh_detail="${gh_detail:+$gh_detail, }$login"
		done
		gh_total=$((${#gh_logins[@]} + gh_skipped))
		if [ "$gh_total" -gt 1 ]; then
			gh_detail="${#gh_logins[@]} of $gh_total accounts — $gh_detail"
		fi
		record required "gh auth" ok "$gh_detail" "$gh_why" ""
	elif gh auth status >/dev/null 2>&1; then
		# The fallback the seam documents, which is also every machine with
		# one login: no account list — an older `gh`, or a `GH_TOKEN` in the
		# environment that overrides the stored ones anyway — means the ACTIVE
		# session is asked alone, exactly as this row did before it asked more
		# than one. That keeps the floor where it was.
		record required "gh auth" ok "$(gh api user --jq .login 2>/dev/null)" "$gh_why" ""
	else
		record required "gh auth" missing "" "$gh_why" "gh auth login"
	fi
fi

jq_install="$(pkg_cmd jq jq jq jq)"
have required jq \
	"scripts/sync-registry.sh and scripts/install-extension.sh read JSON with it" \
	"${jq_install:-see https://jqlang.github.io/jq/download/}"

yaml_install="$(pkg_cmd python3-yaml python3-pyyaml python-yaml '')"
py_install="$(pkg_cmd python3 python3 python python3)"
if have required python3 \
	"the queue, the forge seam and the status screen are Python" \
	"${py_install:-see https://www.python.org/downloads/}"; then
	if python3 -c 'import yaml' >/dev/null 2>&1; then
		record required PyYAML ok "$(python3 -c 'import yaml; print(yaml.__version__)' 2>/dev/null)" \
			"every record fleet writes is YAML — task.yaml, topic.yaml, the profiles" ""
	else
		record required PyYAML missing "" \
			"every record fleet writes is YAML — task.yaml, topic.yaml, the profiles" \
			"${yaml_install:-python3 -m pip install --user PyYAML}"
	fi
fi

# The version floor has ONE owner and it is the manifest, which records why the
# number sits where it does. Reading it from there means this file never has to
# be edited when the floor moves.
floor="$(sed -n 's/^min_thurbox_version *= *"\(.*\)"/\1/p' extension.toml.in | head -1)"
if have required thurbox-cli \
	"the sessions fleet spawns, the extension, and the queue pane all live in it" \
	"see https://github.com/Thurbeen/thurbox"; then
	have_ver="$(version_of thurbox-cli)"
	if [ -n "$floor" ] && [ -n "$have_ver" ] &&
		[ "$(printf '%s\n%s\n' "$floor" "$have_ver" | sort -V | head -1)" != "$floor" ]; then
		# Replace the ok row rather than adding a second one about the same tool.
		unset 'ROWS[${#ROWS[@]}-1]'
		record required thurbox-cli stale "$have_ver" \
			"extension.toml.in sets the floor at $floor and says why" \
			"upgrade thurbox-cli to $floor or newer"
	fi
fi

# --- recommended --------------------------------------------------------------

quota_why="the fuel the pane and ./scripts/fleet-status.sh draw, and the"
quota_why="$quota_why account window queue.sh refuel checks before it restarts a worker"
have recommended quota-axi "$quota_why" "npm install -g quota-axi"

glab_install="$(pkg_cmd glab glab glab glab)"
if have recommended glab \
	"fleet's GitLab forge adapter; nothing needs it until a task's repo lives on GitLab" \
	"${glab_install:-see https://gitlab.com/gitlab-org/cli}"; then
	# ASKED PER HOST, for the reason scripts/lib/glab-hosts.sh argues at
	# length: `glab auth status` is all-or-nothing across every instance glab
	# has configured, so an operator authenticated to their company's GitLab
	# — where their repositories actually live — and not to gitlab.com, which
	# they have never used, read `missing` beside a remedy they had already
	# run. A self-hosted instance is the ORDINARY case for this row, not a
	# footnote on the end of it.
	glab_why="a merge request is read with a credential for ITS host, not for every host glab knows"
	if [ -n "${GITLAB_HOST:-}" ]; then
		# `GITLAB_HOST` is glab's own variable for which instance to talk to,
		# so a host named there is the one that must work: it is where every
		# glab call fleet makes would go, and a credential for some other
		# instance is not one the forge seam would ever reach for.
		if glab_host_ok "$GITLAB_HOST"; then
			record recommended "glab auth" ok "$GITLAB_HOST" "$glab_why" ""
		else
			record recommended "glab auth" missing "" "$glab_why" \
				"glab auth login --hostname $GITLAB_HOST"
		fi
	else
		glab_seen=0
		glab_ok=""
		while IFS= read -r host; do
			[ -n "$host" ] || continue
			glab_seen=1
			glab_host_ok "$host" && glab_ok="${glab_ok:+$glab_ok, }$host"
		done < <(glab_hosts)

		if [ -n "$glab_ok" ]; then
			# One working credential is the whole question — fleet reaches a
			# GitLab repository by HOST plus path, so an instance it will
			# never be pointed at cannot make the ones it will unusable.
			record recommended "glab auth" ok "$glab_ok" "$glab_why" ""
		elif [ "$glab_seen" -eq 0 ] && glab auth status >/dev/null 2>&1; then
			# The same fallback the gh row has, and for the same reason: an
			# older `glab` with no `--all` enumerates nothing, and the bare
			# status command is then the only answer there is.
			record recommended "glab auth" ok "" "$glab_why" ""
		else
			record recommended "glab auth" missing "" "$glab_why" \
				"glab auth login   # it asks which instance"
		fi
	fi
fi

# --- gate ---------------------------------------------------------------------

lua_install="$(pkg_cmd lua5.4 lua lua lua)"
have gate lua \
	"./scripts/check.sh pane renders the queue pane offline with it" \
	"${lua_install:-see https://www.lua.org/download.html}"

sc_install="$(pkg_cmd shellcheck ShellCheck shellcheck shellcheck)"
have gate shellcheck \
	"./scripts/check.sh shell" \
	"${sc_install:-see https://github.com/koalaman/shellcheck#installing}"

have gate rumdl \
	"./scripts/check.sh markdown" \
	"uv tool install rumdl   # or: cargo install rumdl"

have gate prek \
	"the pre-commit hooks in .pre-commit-config.yaml, which run the same gate" \
	"uv tool install prek"

# NOT A TOOL — a configuration, and the one whose failures name anything but
# themselves. `commit.gpgsign = true` with no key makes every `git commit` in a
# repo that key does not cover fail: a throwaway sandbox, or a worktree
# somewhere no `includeIf` names. `scripts/queue-selftest.sh` settles it for the
# repos it builds — its own header says how, and it had to, because the run
# reported a dozen unrelated queue failures and never mentioned signing — so
# what is left is the machine's own gap, reported here once and by name.
#
# READ FROM A DIRECTORY OUTSIDE THIS CHECKOUT, which is the whole subtlety. A
# `includeIf gitdir:` block can set the key for the operator's code tree and
# nowhere else — so committing here works, committing in /tmp does not, and
# asking git from inside this repo answers about the wrong place. The probe
# directory is the kind of place a sandbox repo is built in, which is the place
# this row is about.
sign_why="git commit signing is on with no key outside this checkout, so every"
sign_why="$sign_why commit in a repo that key does not cover fails — any sandbox or worktree"
if command -v git >/dev/null 2>&1; then
	probe_dir="$(mktemp -d)"
	sign_on="$(git -C "$probe_dir" config --get commit.gpgsign 2>/dev/null)"
	sign_key="$(git -C "$probe_dir" config --get user.signingkey 2>/dev/null)"
	sign_cmd="$(git -C "$probe_dir" config --get gpg.ssh.defaultKeyCommand 2>/dev/null)"
	rmdir "$probe_dir" 2>/dev/null
	if [ "$sign_on" = "true" ] && [ -z "$sign_key" ] && [ -z "$sign_cmd" ]; then
		record gate "commit signing" missing "" "$sign_why" \
			"git config --global user.signingkey <key>   # or: commit.gpgsign false"
	else
		record gate "commit signing" ok "" "$sign_why" ""
	fi
fi

# --- output -------------------------------------------------------------------

# No --tier means every tier. The exit code is NOT filtered: a required gap is
# still a required gap when the caller only asked to see the gate tools, and a
# preflight that reported success because of how it was queried would be worse
# than no preflight.
wanted() {
	[ -z "$TIERS" ] && return 0
	case " $TIERS " in *" $1 "*) return 0 ;; esac
	return 1
}

case "$MODE" in
commands)
	# Only the lines that would change something, in table order, deduplicated.
	# This is what an operator who said "yes, install them" gets handed.
	printed=""
	for row in "${ROWS[@]}"; do
		IFS="$US" read -r tier _name state _detail _why remedy <<<"$row"
		[ "$state" = "ok" ] && continue
		wanted "$tier" || continue
		[ -n "$remedy" ] || continue
		case "$remedy" in see\ *) continue ;; esac
		case "$printed" in *"|$remedy|"*) continue ;; esac
		printed="$printed|$remedy|"
		printf '%s\n' "$remedy"
	done
	;;
text)
	heading() {
		case "$1" in
		required) printf '\nREQUIRED — fleet cannot run without these\n' ;;
		recommended) printf '\nRECOMMENDED — each one names what degrades without it\n' ;;
		gate) printf '\nGATE — only ./scripts/check.sh needs these\n' ;;
		esac
	}
	for tier in required recommended gate; do
		wanted "$tier" || continue
		heading "$tier"
		for row in "${ROWS[@]}"; do
			IFS="$US" read -r rtier name state detail why remedy <<<"$row"
			[ "$rtier" = "$tier" ] || continue
			case "$state" in
			ok) printf '  \033[32mok\033[0m       %-14s %s\n' "$name" "$detail" ;;
			stale) printf '  \033[33mstale\033[0m    %-14s %s — %s\n' "$name" "$detail" "$why" ;;
			*) printf '  \033[31mmissing\033[0m  %-14s %s\n' "$name" "$why" ;;
			esac
			[ "$state" = "ok" ] || [ -z "$remedy" ] || printf '           %-14s install: %s\n' "" "$remedy"
		done
	done
	printf '\n'
	if [ "$required_missing" -eq 0 ]; then
		printf 'Every required dependency is present. "--commands" lists the\n'
		printf 'install lines for anything above that is not.\n'
	else
		plural=""
		[ "$required_missing" -gt 1 ] && plural="ies"
		printf '%d required dependenc%s missing. Install before onboarding\n' \
			"$required_missing" "${plural:-y}"
		printf 'writes anything: a half-onboarded clone is worse than one that\n'
		printf 'never started.\n\n  ./scripts/preflight.sh --commands\n'
	fi
	;;
esac

[ "$required_missing" -eq 0 ] || exit 1
exit 0
