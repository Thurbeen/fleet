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
# COMPLETION COMES FROM TWO PLACES, on purpose, and RELEASE FROM A THIRD:
#
#   `watch`    reads `thurbox-cli watch` — the event stream — and folds each
#              transition into the task's progress.jsonl. It closes NOTHING.
#              A turn ending is not a task finishing.
#   `collect`  reads the result.md a worker wrote when it knew what it had
#              concluded, and only that closes a task. It also CHECKS that
#              task's artifact: a `no-mistakes` pull request body carries five
#              headings, so "open the PR through the pipeline" stops being an
#              unverifiable instruction about a method. A pull request without
#              them is reported and the task is left OPEN; a check that could
#              not run (no `gh`, no network) says so and is never read as
#              either verdict.
#   `reap`     asks the FORGE whether each concluded task's pull request has
#              merged, moves the ones that did to `landed`, and only then
#              deletes their sessions and worktrees. `collect` runs it, because
#              "delete each session as it closes out" was a documented MANUAL
#              step and twenty gigabytes sat in a worktree whose pull request
#              had merged the day before. It never touches a session thurbox
#              says is working, and never one a worker gave up in — that
#              session is the evidence.
#
# Both are things the lead READS when it chooses. Neither pushes anything into
# its terminal, which is what `thurbox-cli message send` does and why the queue
# does not use it: an arriving worker message interrupts whoever is talking to
# the lead.
#
# AND THEN THE PULL REQUEST OUTLIVES THE TASK, which is what `shepherd` is for:
#
#   `shepherd` asks the FORGE for every open PR on the repos this queue's
#              tasks name, DISPATCHES A FIXER for one that conflicts, fails a
#              check, has a review asking for changes, or carries no pipeline
#              attestation — and squash-merges one that clears every gate.
#              It is a fourth thing, after both halves of completion.
#
#              It reads the forge and not the task records because a task
#              records ONE artifact, the first PR its worker reported: #25 was
#              a SECOND pull request from a task still pointing at the merged
#              #23, and a PR opened outside the queue was invisible the same
#              way. A PR no task records is shepherded like any other; it just
#              has no session to send a fixer into, and that is said out loud.
#
# AND A FIFTH THING, WHICH IS FUEL. A worker that hits its agent's token limit
# does not fail — it SITS. The hook that would have said `idle` never fires, so
# thurbox goes on reporting `working` and nothing in the loop above ever touches
# it. `refuel` finds those and restarts them, and its first move is the one that
# matters:
#
#   `refuel`   asks the ACCOUNT's quota window BEFORE it looks at any session.
#              That window — `quota-axi`, reading the credentials already on
#              this machine — is the operator's subscription, shared by the lead
#              and every worker. While it is spent, every session is stuck for
#              the same reason and a restart is worse than useless: the worker
#              resumes, hits the same wall, and burns the reset it was waiting
#              for. So a spent account restarts NOTHING and prints `resetsAt`,
#              and a quota that could not be read restarts nothing either.
#
#              With fuel in the account, one wedged session is a conjunction:
#              a `working` hook state older than any real turn, AND the agent's
#              own limit banner on its pane or the rate-limit record in its
#              transcript. Stale alone is a SLOW worker, and slow is not dry.
#              The restart re-sends the brief through dispatch's own trusted
#              handoff, is recorded on the task, and is capped.
#
# It exists because noticing was never the expensive part. In one day: #14 went
# CONFLICTING when #13 merged and nothing saw it; #11 and #12 were opened with
# a bare `gh pr create` and nobody knew for hours; a review finding sat in a PR
# body until a human read it out. A status report would have saved none of it.
#
# Three rules make it safe to run, and `--dry-run` shows all of them:
#   IDEMPOTENT   the fixer it sent is recorded on the task; a second pass sees
#                work in flight rather than a still-broken PR. `--force` to mean
#                it anyway.
#   NEVER GUESS  no gh, no network, no thurbox — it says what it could not
#                determine and carries on. A PR it could not read is never
#                called broken, and never called ready.
#   NEVER TOUCH  only artifacts recorded on this queue's own tasks, and it
#   A STRANGER   merges only in the repos AUTO_MERGE_REPOS names.
#
# Usage:
#   scripts/queue.sh topic add <slug> --title T --prompt 'the ask'   # or --prompt-file F|-
#   scripts/queue.sh add <topic> <slug> --title T --repo P --branch B [--base main]
#                        [--host H] [--profile default] [--touches a,b] [--brief-file F]
#   scripts/queue.sh block <ref> --on <ref> --kind KIND --why 'reason'   # or --clear
#   scripts/queue.sh plan [--json]        # what goes out now, what waits, and why
#   scripts/queue.sh dispatch [--dry-run] # launch the whole ready set at once
#   scripts/queue.sh attach <ref> <uuid>  # record a session you spawned by hand
#   scripts/queue.sh watch [--for-secs N] # fold transitions in; close nothing
#   scripts/queue.sh collect [--allow-unverified] [--no-reap]  # read results,
#                        close what is done; --allow-unverified closes one whose
#                        PR failed the pipeline check, after you have judged
#                        that PR; --no-reap leaves every session alone
#   scripts/queue.sh reap [--dry-run]     # land what merged, release its session
#   scripts/queue.sh refuel [<ref>] [--dry-run]  # the account's fuel first, then
#                        restart the workers that ran dry against it
#   scripts/queue.sh shepherd [--dry-run] # every open PR on the repo: fix or merge
#                        [--json] [--topic T] [--ref R] [--no-merge] [--force]
#   scripts/queue.sh list [--topic T]     # the lead's view: a line per task
#   scripts/queue.sh show <ref>           # one task's whole record
#   scripts/queue.sh check                # validate every record (./scripts/check.sh queue)
#   scripts/queue.sh root                 # the resolved queue directory, absolute
#
# A ref is `<topic>/<task>`, or a bare task id when only one topic has it.
#
# A TASK CAN NAME A HOST, and then its worker runs there instead of here:
# `add --host <name>` takes a name from thurbox's hosts.toml, `dispatch` passes
# it to `session create`, and the agent, its tmux window and its worktree all
# live on that machine. `--repo` is then a path ON THAT HOST and nothing local
# validates it. No `--host` means no change: a local task takes every path
# through this file that it took before the flag existed.
#
# The transport is ssh and the model is unchanged. A remote worker cannot write
# into this queue's directory, so `dispatch` copies the brief into the worktree
# thurbox made on the host, and `collect` fetches the worker's result.md back
# into the task's own before it reads it. Completion is still a FILE the lead
# reads — `message send` would have needed no plumbing at all and was refused,
# because it injects into the lead's terminal whatever machine it comes from.
#
# Three probes run before anything is spawned, and one failure stops that task
# where it stands: the host answers ssh as a POSIX shell, it has GitHub
# credentials OF ITS OWN, and the repo is a checkout at that path. Fleet never
# sends credentials anywhere. POSIX hosts only — a Windows host (hosts.toml
# spells one with a non-tmux `multiplexer`) is refused by name.
#
# THE QUEUE BELONGS TO ONE CHECKOUT — the CONTROL PLANE's, the clone the
# `mission control` session opens. It is never resolved against the shell's cwd,
# so this script does the same thing from any directory. That matters because a
# second clone of this repo is the SUPPORTED shape here: a control plane with no
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
#       extension.toml and no live `mission control` session. A fleet used
#       without the thurbox extension is legitimate and must not be made
#       unusable by a guard that cannot tell whether it is warranted.
#
# `queue.sh root`, `list` and `check` all print the resolved directory, and
# `webui.sh status` prints the same one, so "why is the dashboard empty?" is a
# question the tooling answers about itself.
#
# Everything under orchestration/queue/ is WORKING STATE and gitignored — this
# repo is public and your prompts are not — except README.md, POLICY.md and
# OPERATOR.example.md, which stay tracked: the layout, the standing policy every
# brief points a worker at instead of restating it, and the form of OPERATOR.md.
# That last one is the OPERATOR's counterpart to the policy — their own standing
# instructions to every worker, ignored like the rest of the queue, and pointed
# at by every brief scaffolded while it exists and is not empty.
# .gitignore's header owns the reason for each.
#
# Environment:
#   FLEET_QUEUE_DIR        where the queue lives (default: this checkout's
#                          orchestration/queue). Honoured VERBATIM and never
#                          guarded — someone who set it meant it.
#   FLEET_QUEUE_WATCH_CMD  the event source, for a replay or another transport
#                          (default: thurbox-cli watch --json)
#   THURBOX_SESSION        set inside a thurbox session; dispatch passes it as
#                          --parent so `session list --parent` enumerates
#                          workers, and reap refuses to delete it
#
# Requires: python3 (with PyYAML) — the same dependency the rest of the gate
# has. `dispatch`, `watch`, `reap` and `refuel` additionally need thurbox-cli,
# and `collect`, `reap` and `shepherd` ask `gh` about a pull request, and
# `shepherd` needs git as well. `refuel` reads the account's quota window with
# `quota-axi` (https://github.com/kunchenguid/quota-axi), which fleet neither
# installs nor sends any credential to. A task that names a `--host`
# additionally needs `ssh`. Every one of those degrades to "could not check"
# rather than to a guess.

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
