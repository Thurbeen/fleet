# shellcheck shell=bash
# The one environment every selftest runs in: nothing the caller's machine or
# the checkout's operator configured reaches it.
#
#   . scripts/lib/selftest-env.sh
#   tmp="$(mktemp -d)"
#   selftest_isolate "$tmp/env"     # a directory the selftest owns and removes
#
# WHY ONE HELPER. A selftest runs in three places — CI, a worker's worktree,
# and the operator's own control-plane checkout — and only the last carries a
# global git config that signs every commit or routes every hook, a HOME
# holding thurbox's hosts.toml and every forge CLI's login, a THURBOX_SESSION
# when a worker runs the gate, a GIT_INDEX_FILE when a git hook does, and
# gitignored settings files in the checkout itself. Seven selftests each
# clearing some of that was how most of them cleared none of it.
# scripts/isolation-selftest.sh runs this against a hostile host, and fails any
# selftest that does not call it.
#
# WHAT IT PINS
#   git       no system config, no caller GIT_CONFIG_* or GIT_DIR-family
#             variable, and a HOME whose only config is an identity, no
#             signing and `main`. A section that wants a different config still
#             sets HOME or GIT_CONFIG_GLOBAL on its own command, and that wins.
#   HOME      a throwaway one, with every XDG base inside it, so what `gh`,
#             `glab`, thurbox or quota-axi would read there is not there.
#   env       forge credentials and host overrides, and THURBOX_SESSION.
#   settings  FLEET_{AUTO_MERGE,PUBLISH,AGENT,GLYPH}_ROOT and FLEET_VOICE_CONF
#             at a copy of the TRACKED *.example.conf only; FLEET_QUEUE_DIR,
#             FLEET_RUNS_DIR and FLEET_RECONCILE_DIR at empty directories; and
#             FLEET_REGISTRY_FILE at a registry map that does not exist. A
#             selftest that forgets to relocate one reads a fresh clone's
#             answer, never the operator's; one that wants its own still
#             exports it afterwards.
#
# WHAT IT KEEPS: PATH, PYTHONUSERBASE, and uv's cache and Python directories.
# Where the tools are installed is not operator state: a PyYAML installed with
# `pip --user` lives under the real HOME this replaces, and so do the PyYAML and
# the Python every `uv run` behind scripts/queue.sh would otherwise fetch again
# for each copy of the tree a selftest builds.

selftest_isolate() {
	local dir="$1" repo i
	repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

	if [ -z "${PYTHONUSERBASE:-}" ] && command -v python3 >/dev/null; then
		PYTHONUSERBASE="$(python3 -m site --user-base 2>/dev/null)" && export PYTHONUSERBASE
	fi
	if command -v uv >/dev/null; then
		if [ -z "${UV_CACHE_DIR:-}" ]; then
			UV_CACHE_DIR="$(uv cache dir 2>/dev/null)" && export UV_CACHE_DIR
		fi
		if [ -z "${UV_PYTHON_INSTALL_DIR:-}" ]; then
			UV_PYTHON_INSTALL_DIR="$(uv python dir 2>/dev/null)" && export UV_PYTHON_INSTALL_DIR
		fi
	fi

	mkdir -p "$dir/home/.config" "$dir/home/.cache" "$dir/home/.local/share" \
		"$dir/home/.local/state" "$dir/settings/orchestration" "$dir/queue" "$dir/runs" "$dir/reconcile"

	export HOME="$dir/home"
	export XDG_CONFIG_HOME="$HOME/.config" XDG_CACHE_HOME="$HOME/.cache"
	export XDG_DATA_HOME="$HOME/.local/share" XDG_STATE_HOME="$HOME/.local/state"

	for ((i = 0; i <= ${GIT_CONFIG_COUNT:-0}; i++)); do
		unset "GIT_CONFIG_KEY_$i" "GIT_CONFIG_VALUE_$i"
	done
	unset GIT_CONFIG_COUNT GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GIT_CONFIG_PARAMETERS \
		GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY \
		GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_NAMESPACE GIT_PREFIX
	export GIT_CONFIG_NOSYSTEM=1
	export GIT_AUTHOR_NAME=selftest GIT_AUTHOR_EMAIL=selftest@example.invalid
	export GIT_COMMITTER_NAME=selftest GIT_COMMITTER_EMAIL=selftest@example.invalid
	printf '[user]\n\tname = selftest\n\temail = selftest@example.invalid\n[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n' \
		>"$HOME/.gitconfig"

	unset GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN GH_HOST GH_CONFIG_DIR \
		GITLAB_TOKEN GITLAB_HOST GLAB_CONFIG_DIR THURBOX_SESSION

	cp "$repo"/orchestration/*.example.conf "$dir/settings/orchestration/"
	export FLEET_AUTO_MERGE_ROOT="$dir/settings" FLEET_PUBLISH_ROOT="$dir/settings"
	export FLEET_AGENT_ROOT="$dir/settings" FLEET_GLYPH_ROOT="$dir/settings"
	export FLEET_VOICE_CONF="$dir/settings/orchestration/voice.example.conf"
	export FLEET_QUEUE_DIR="$dir/queue" FLEET_RUNS_DIR="$dir/runs" FLEET_RECONCILE_DIR="$dir/reconcile"
	export FLEET_REGISTRY_FILE="$dir/registry/repos.generated.yaml"
}
