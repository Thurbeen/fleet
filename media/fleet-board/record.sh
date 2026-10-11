#!/usr/bin/env sh
# Records the README's Kanban demo, media/fleet-board.gif, from a REAL thurbox
# TUI running this checkout's plugins against a throwaway profile.
#
#     media/fleet-board/record.sh
#
# Linux only (GNU date, flock). Needs thurbox and thurbox-cli on PATH, plus
# uv, git, sqlite3, vhs (with ttyd) and ffmpeg. Run it from anywhere; it writes
# only media/fleet-board.gif in this checkout.
#
# NOTHING LIVE IS RECORDED. HOME, XDG_*, THURBOX_CONFIG_DIR, THURBOX_DATA_DIR
# and TMUX_TMPDIR all point into one scratch directory, so the
# recording starts its own tmux server and its own database and cannot see the
# operator's sessions, layout, theme or queue. Everything on screen is seeded
# below:
#
#   * a copy of this checkout's tracked files, whose orchestration/queue holds
#     three synthetic topics, as the Mission Control session's directory;
#   * three synthetic target repositories under ~/code, one per topic;
#   * sessions whose "agent" is demo-agent.sh: it prints a fixed transcript,
#     reports a status with `thurbox-cli session signal` and waits. No agent
#     CLI, account or network is involved.
#
# The board's queue probe is the real one (`scripts/lib/pane_probe.py`, run in
# the copy). The reconciler is NOT started, because a live one would dispatch the
# synthetic tasks; a held lock and a fresh heartbeat stand in for it, so the
# header reads as it does on a working fleet.
#
# The scratch directory is FLEET_DEMO_ROOT, default ~/.cache/fleet-board-demo,
# and it is deleted on exit unless FLEET_DEMO_KEEP=1. The raw capture and its
# frames are large, so they stay out of the system temp directory too.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
REAL_HOME=$HOME
ROOT=${FLEET_DEMO_ROOT:-${XDG_CACHE_HOME:-$HOME/.cache}/fleet-board-demo}
OUT="$REPO_ROOT/media/fleet-board.gif"

missing=
for tool in thurbox thurbox-cli uv git sqlite3 flock vhs ffmpeg ffprobe; do
    command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
if [ -n "$missing" ]; then
    echo "error: missing required tool(s):$missing" >&2
    exit 1
fi

# uv keeps the real cache: it holds packages, nothing about the operator.
UV_CACHE_DIR=$(uv cache dir)
export UV_CACHE_DIR

rm -rf "$ROOT"
mkdir -p "$ROOT"
ROOT=$(CDPATH= cd -- "$ROOT" && pwd)

# A short socket directory: AF_UNIX paths are length-limited.
SOCK_DIR=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/fleet-demo.XXXXXX")

cleanup() {
    # Only servers whose socket is in the private directory: never the default.
    for sock in "$SOCK_DIR"/tmux-*/*; do
        [ -S "$sock" ] && tmux -S "$sock" kill-server >/dev/null 2>&1 || true
    done
    [ -n "${LOCK_PID:-}" ] && kill "$LOCK_PID" >/dev/null 2>&1 || true
    rm -rf "$SOCK_DIR"
    [ "${FLEET_DEMO_KEEP:-0}" = 1 ] || rm -rf "$ROOT"
}
trap cleanup EXIT INT TERM

export HOME="$ROOT/home"
export XDG_CONFIG_HOME="$HOME/.config" XDG_DATA_HOME="$HOME/.local/share"
export XDG_STATE_HOME="$HOME/.local/state" XDG_CACHE_HOME="$HOME/.cache"
export THURBOX_CONFIG_DIR="$XDG_CONFIG_HOME/thurbox"
export THURBOX_DATA_DIR="$XDG_DATA_HOME/thurbox"
export TMUX_TMPDIR="$SOCK_DIR"
export FLEET_DEMO_TRANSCRIPTS="$SCRIPT_DIR/transcripts"
mkdir -p "$THURBOX_CONFIG_DIR" "$THURBOX_DATA_DIR" "$XDG_STATE_HOME" "$XDG_CACHE_HOME"

git_init() {
    git -C "$1" init -q -b main
    git -C "$1" -c user.email=demo@example.org -c user.name=demo add -A
    git -C "$1" -c user.email=demo@example.org -c user.name=demo commit -q -m "$2"
}

# --- The control plane: this checkout's tracked files, and a synthetic queue --
FLEET="$HOME/fleet"
mkdir -p "$FLEET"
git -C "$REPO_ROOT" ls-files -z | (cd "$REPO_ROOT" && xargs -0 tar -cf - --) | tar -xf - -C "$FLEET"
git_init "$FLEET" "chore: import fleet"
(cd "$FLEET" && uv sync --frozen --quiet)

for repo in atlas beacon cedar; do
    mkdir -p "$HOME/code/$repo"
    printf '# %s\n' "$repo" >"$HOME/code/$repo/README.md"
    git_init "$HOME/code/$repo" "init $repo"
done

# --- Sessions -------------------------------------------------------------------
cat >"$THURBOX_CONFIG_DIR/agents.toml" <<EOF
default = "claude"

[[agents]]
name = "claude"
command = "$SCRIPT_DIR/demo-agent.sh"

[[agents]]
name = "codex"
command = "$SCRIPT_DIR/demo-agent.sh"
EOF

thurbox-cli extension deactivate hooks >/dev/null 2>&1 || true
thurbox-cli config accept-interface >/dev/null 2>&1 || true

session() { # name repo agent -> id
    thurbox-cli session create --json --name "$1" --repo-path "$2" --agent "$3" |
        uv run --project "$FLEET" --frozen --quiet python -c 'import json,sys; print(json.load(sys.stdin)["id"])'
}
LEAD=$(session "📡 Mission Control" "$FLEET" claude)
EXPORT=$(session "🚀 Export worker" "$HOME/code/atlas" codex)
SEARCH=$(session "🚀 Search service" "$HOME/code/beacon" claude)
ADAPTERS=$(session "🚀 Update adapters" "$HOME/code/cedar" codex)

# --- Queue records ----------------------------------------------------------------
QUEUE="$FLEET/orchestration/queue"
NOW=$(date -u +%s)
stamp() { date -u -d "@$((NOW - $1))" +%Y-%m-%dT%H:%M:%S+00:00; }

topic() { # slug title
    mkdir -p "$QUEUE/$1"
    printf 'slug: %s\ntitle: %s\ncreated: %s\n' "$1" "$2" "$(stamp 7200)" >"$QUEUE/$1/topic.yaml"
}

# task topic id state title agent repo [session] [method] [publish-state] [artifact]
#      [more publish: lines] [more top-level lines]
task() {
    dir="$QUEUE/$1/$2"
    mkdir -p "$dir"
    printf '# %s\n' "$4" >"$dir/BRIEF.md"
    {
        printf "id: '%s'\ntopic: %s\ntitle: %s\nstate: %s\n" "$2" "$1" "$4" "$3"
        printf 'repo: %s\nbranch: demo/%s-%s\nbase: origin/main\nagent: %s\n' "$6" "$1" "$2" "$5"
        printf 'session: %s\n' "${7:-}"
        printf 'publish:\n  method: %s\n' "${8:-pr}"
        [ -n "${9:-}" ] && printf '  state: %s\n  at: %s\n' "$9" "$(stamp 600)"
        [ -n "${11:-}" ] && printf '%s\n' "${11}"
        [ -n "${10:-}" ] && printf 'artifact: %s\nreview: https://example.org/reviews/%s-%s\n' "${10}" "$1" "$2"
        [ -n "${12:-}" ] && printf '%s\n' "${12}"
        printf 'created: %s\n' "$(stamp 5400)"
        case "$3" in queued) ;; *) printf 'dispatched_at: %s\n' "$(stamp 2400)" ;; esac
        case "$3" in done | landed | stuck | abandoned) printf 'concluded_at: %s\n' "$(stamp 900)" ;; esac
    } >"$dir/task.yaml"
    case "$3" in done | landed | stuck) printf -- '---\noutcome: shipped\n---\n' >"$dir/result.md" ;; esac
}

A="$HOME/code/atlas" B="$HOME/code/beacon" C="$HOME/code/cedar"
topic atlas "Audit exports"
task atlas 00 landed "Audit schema" codex "$A" "" pr merged https://github.com/example/atlas/pull/201
task atlas 01 queued "Add audit export" codex "$A"
task atlas 02 dispatched "Export worker" codex "$A" "$EXPORT"
task atlas 03 done "Validate every exported record" codex "$A" "" pr checks-failed \
    https://github.com/example/atlas/pull/204 "$(printf '  detail: "failed checks: unit"\n  unresolved_threads: 2')"
task atlas 04 stuck "Recover test database" codex "$A"
topic beacon "Search index"
task beacon 01 done "Schema migration" claude "$B" "" pr checks-passed https://github.com/example/beacon/pull/81
task beacon 02 dispatched "Search service" claude "$B" "$SEARCH"
task beacon 03 queued "Wire search index" claude "$B" "" pr "" "" "" \
    "$(printf 'blocked_by:\n- task: beacon/02\n  kind: consumes')"
task beacon 04 done "Search architecture" claude "$B" "" served served
topic cedar "Platform adapters"
task cedar 01 dispatched "Update adapters" codex "$C" "$ADAPTERS"
task cedar 02 queued "Stage release" codex "$C" "" pr "" "" "" \
    "$(printf 'blocked_by:\n- condition: release sign-off\n  kind: awaiting-approval')"
task cedar 03 done "Document adapters" codex "$C" "" pr ready https://github.com/example/cedar/pull/206

# What each session reports, as an agent's hooks would.
thurbox-cli session signal --session "$LEAD" --state idle >/dev/null
for id in "$EXPORT" "$SEARCH" "$ADAPTERS"; do
    thurbox-cli session signal --session "$id" --state working >/dev/null
done

# One fleet: peer discovery off, as orchestration/peers.example.conf documents.
printf 'DISCOVER=off\n' >"$FLEET/orchestration/peers.conf"

# A stand-in for the reconciler: its lock held and a fresh heartbeat.
RT="$FLEET/orchestration/reconcile"
mkdir -p "$RT"
printf '%s\n' "$NOW" >"$RT/heartbeat"
(exec 9>"$RT/lock" && flock 9 && exec sleep 3600) </dev/null >/dev/null 2>&1 &
LOCK_PID=$!

# --- Interface: this checkout's plugins, trusted, in the Doom theme -------------
for lib in fleet_reader fleet_board; do
    thurbox-cli plugin install "$REPO_ROOT/interface/$lib.lua" --as "lib/$lib.lua" --text >/dev/null
done
printf 'return { checkout = "%s" }\n' "$FLEET" >"$ROOT/fleet_home.lua"
thurbox-cli plugin install "$ROOT/fleet_home.lua" --as lib/fleet_home.lua --text >/dev/null
thurbox-cli plugin install "$REPO_ROOT/interface/fleet_kanban.lua" --as plugins/91_fleet_queue.lua --text >/dev/null
thurbox-cli plugin install "$REPO_ROOT/interface/fleet_lead.lua" --as plugins/93_fleet_lead.lua --text >/dev/null

UI_DIR=$(thurbox-cli plugin dir --text | head -n1)
uv run --project "$FLEET" --frozen --quiet python - "$THURBOX_CONFIG_DIR/ui.json" "$UI_DIR/plugins/91_fleet_queue.lua" <<'PY'
import json, os, sys
path, board = sys.argv[1], sys.argv[2]
digest = 0xCBF29CE484222325  # FNV-1a 64, thurbox's plugin trust digest
for byte in open(board, "rb").read():
    digest = ((digest ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
ui = json.load(open(path)) if os.path.exists(path) else {}
ui.setdefault("trusted", {})[board] = f"{digest:016x}"
json.dump(ui, open(path, "w"), indent=2)
PY
thurbox-cli plugin check --text >/dev/null

sqlite3 "$THURBOX_DATA_DIR/thurbox.db" "INSERT INTO metadata (key, value) VALUES ('active_theme', 'doom')
    ON CONFLICT(key) DO UPDATE SET value = excluded.value;
    UPDATE sessions SET display_order = CASE id WHEN '$LEAD' THEN 0 WHEN '$EXPORT' THEN 1
        WHEN '$SEARCH' THEN 2 ELSE 3 END;"
thurbox-cli session focus "$LEAD" >/dev/null 2>&1 || true

if [ "${FLEET_DEMO_SETUP_ONLY:-0}" = 1 ]; then
    # Leave the profile running for a manual look; `. $ROOT/env.sh` enters it,
    # and deleting $ROOT after killing its tmux server undoes it.
    printf 'export HOME=%s XDG_CONFIG_HOME=%s XDG_DATA_HOME=%s XDG_STATE_HOME=%s XDG_CACHE_HOME=%s\n' \
        "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME" "$XDG_CACHE_HOME" >"$ROOT/env.sh"
    printf 'export THURBOX_CONFIG_DIR=%s THURBOX_DATA_DIR=%s TMUX_TMPDIR=%s\n' \
        "$THURBOX_CONFIG_DIR" "$THURBOX_DATA_DIR" "$TMUX_TMPDIR" >>"$ROOT/env.sh"
    printf 'export UV_CACHE_DIR=%s FLEET_DEMO_TRANSCRIPTS=%s\n' "$UV_CACHE_DIR" "$FLEET_DEMO_TRANSCRIPTS" >>"$ROOT/env.sh"
    trap - EXIT INT TERM
    echo "profile ready: . $ROOT/env.sh"
    exit 0
fi

# --- Record -----------------------------------------------------------------------
mkdir -p "$ROOT/out"
# vhs drives a headless browser it caches under the REAL home; the tape puts
# the throwaway HOME back before it starts thurbox.
export FLEET_DEMO_HOME="$HOME"
# Its frames go to TMPDIR, so that is the scratch directory too.
mkdir -p "$ROOT/tmp"
(cd "$ROOT/out" && HOME=$REAL_HOME TMPDIR="$ROOT/tmp" vhs "$SCRIPT_DIR/fleet-board.tape")
# vhs drops frames it cannot capture in time and plays the rest at a fixed
# rate, so the raw take runs fast. Stretch it back to the tape's own length:
# every Sleep after Show, plus the typing.
RAW=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$ROOT/out/fleet-board.gif")
STRETCH=$(uv run --project "$FLEET" --frozen --quiet python - "$SCRIPT_DIR/fleet-board.tape" "$RAW" <<'PY'
import re, sys
whole = open(sys.argv[1], encoding="utf-8").read()
shown = whole.split("\nShow\n", 1)[1]
secs = lambda v: float(v[:-2]) / 1000 if v.endswith("ms") else float(v[:-1])
wanted = sum(secs(v) for v in re.findall(r"^Sleep (\S+)", shown, re.M))
typing = secs(re.search(r"^Set TypingSpeed (\S+)", whole, re.M).group(1))
wanted += typing * sum(len(t) for t in re.findall(r'^Type "([^"]*)"', shown, re.M))
print(f"{wanted / float(sys.argv[2]):.3f}")
PY
)
ffmpeg -loglevel error -i "$ROOT/out/fleet-board.gif" \
    -vf "setpts=$STRETCH*PTS,fps=8,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" \
    -loop 0 -y "$ROOT/out/small.gif"
mv "$ROOT/out/small.gif" "$OUT"
echo "wrote $OUT ($(wc -c <"$OUT") bytes)"
