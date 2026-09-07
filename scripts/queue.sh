#!/usr/bin/env bash
# The fleet task queue — one prompt becomes a topic, a topic becomes tasks, and
# every task carries its own instructions in its own file.
#
# WHY THIS EXISTS. A control plane running several projects at once has three
# problems a conversation cannot solve. A prompt that lives in a turn is gone
# after a context reset. Work that is planned in an agent's head is planned
# again, differently, on the next pass. And an agent holding every task's
# detail at once mixes them up. So the queue puts all three on disk:
#
#   orchestration/queue/<topic>/PROMPT.md       the prompt, verbatim
#   orchestration/queue/<topic>/<task>/BRIEF.md the instructions for ONE task
#   orchestration/queue/<topic>/<task>/task.yaml   its record
#
# The lead reads `list` and `plan` — a line per task — and never the briefs.
# Each worker reads one brief and nothing else. That is the context hygiene:
# structural, not a matter of the agent being careful.
#
# THE ORDERING RULE, which is the counterintuitive part and the whole value:
#
#   Isolated work dispatches IMMEDIATELY, all of it, with no concurrency cap.
#   File or subsystem overlap is a RISK SIGNAL, reported beside the ready set,
#   and it holds nothing up — an ordinary rebase reconciles it.
#   Only a recorded blocker serializes: a true semantic dependency, shared
#   mutable external state, an incompatible concurrent migration, or another
#   concrete condition that makes independent progress unsafe. Every one names
#   a kind and a reason, so it survives the next planning pass instead of
#   being re-derived. `block` refuses one that does not.
#
# A queue that runs one task at a time is slower than no queue at all.
#
# COMPLETION COMES FROM TWO PLACES, on purpose:
#
#   `watch`    reads `thurbox-cli watch` — the event stream — and folds each
#              transition into the task's progress.jsonl. It closes NOTHING.
#              A turn ending is not a task finishing.
#   `collect`  reads the result.md a worker wrote when it knew what it had
#              concluded, and only that closes a task.
#
# Both are things the lead READS when it chooses. Neither pushes anything into
# its terminal, which is what `thurbox-cli message send` does and why the queue
# does not use it: an arriving worker message interrupts whoever is talking to
# the lead.
#
# Usage:
#   scripts/queue.sh topic add <slug> --title T --prompt 'the ask'   # or --prompt-file F|-
#   scripts/queue.sh add <topic> <slug> --title T --repo P --branch B [--base main]
#                        [--profile default] [--touches a,b] [--brief-file F]
#   scripts/queue.sh block <ref> --on <ref> --kind KIND --why 'reason'   # or --clear
#   scripts/queue.sh plan [--json]        # what goes out now, what waits, and why
#   scripts/queue.sh dispatch [--dry-run] # launch the whole ready set at once
#   scripts/queue.sh attach <ref> <uuid>  # record a session you spawned by hand
#   scripts/queue.sh watch [--for-secs N] # fold transitions in; close nothing
#   scripts/queue.sh collect              # read the results; close what is done
#   scripts/queue.sh list [--topic T]     # the lead's view: a line per task
#   scripts/queue.sh show <ref>           # one task's whole record
#   scripts/queue.sh check                # validate every record (./scripts/check.sh queue)
#   scripts/queue.sh root                 # the resolved queue directory, absolute
#
# A ref is `<topic>/<task>`, or a bare task id when only one topic has it.
#
# THE QUEUE BELONGS TO ONE CHECKOUT — the CONTROL PLANE's, the clone the
# `fleet` session opens. It is never resolved against the shell's cwd, so this
# script does the same thing from any directory. That matters because a second
# clone of this repo is the SUPPORTED shape here: a control plane with no
# `origin` of its own needs one that workers can branch and push from. Running
# `topic add` in that clone used to write a whole second queue in silence,
# with the monitor correctly showing nothing. Now:
#
#   `topic add` and `add` REFUSE outside the control plane. Creating a record
#       is the only act that can fork the queue, so that is where the hard stop
#       goes, and the refusal names both paths and the override.
#   Everything else WARNS — once this checkout actually holds records — and
#       carries on, because a second clone is a fine place to read a queue from
#       and taking that away helps nobody.
#   Neither fires when the control plane cannot be identified — no rendered
#       extension.toml and no live `fleet` session. A fleet used without the
#       thurbox extension is legitimate and must not be made unusable by a
#       guard that cannot tell whether it is warranted.
#
# `queue.sh root`, `list` and `check` all print the resolved directory, and
# `webui.sh status` prints the same one, so "why is the dashboard empty?" is a
# question the tooling answers about itself.
#
# Everything under orchestration/queue/ is INSTANCE DATA and gitignored — your
# queue, not the template's. `_TEMPLATE/` beside it is the shipped form and
# stays tracked, the same split registry/context/ and orchestration/runs/ use.
# .gitignore's header owns the reason.
#
# Environment:
#   FLEET_QUEUE_DIR        where the queue lives (default: this checkout's
#                          orchestration/queue). Honoured VERBATIM and never
#                          guarded — someone who set it meant it.
#   FLEET_QUEUE_WATCH_CMD  the event source, for a replay or another transport
#                          (default: thurbox-cli watch --json)
#   THURBOX_SESSION        set inside a thurbox session; dispatch passes it as
#                          --parent so `session list --parent` enumerates workers
#
# Requires: python3 (with PyYAML) — the same dependency the rest of the gate
# has. `dispatch` and `watch` additionally need thurbox-cli.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

case "${1:-}" in
-h | --help | "")
	awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
	exit 0
	;;
esac

if ! command -v python3 >/dev/null; then
	echo "error: python3 not found" >&2
	exit 2
fi

exec python3 scripts/lib/queue.py "$@"
