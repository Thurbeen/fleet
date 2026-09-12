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
#   A BLOCKER NAMES A TASK OR A CONDITION. `--on <ref>` is the wait with an
#   end: it clears when that task LANDS. `--condition '<what>'` is the wait on
#   something the queue cannot observe — a credential, an approval, a window,
#   a machine somebody has to fix, a decision nobody has made. Nothing clears
#   one but `block --clear` naming it back: no timer, no `collect`, no `reap`,
#   and no inference from a later dispatch working. Before it existed, a task
#   held by an unauthenticated `az` had nowhere to be written down, so `plan`
#   called it ready and the reconciler woke the lead to dispatch it.
#
# A queue that runs one task at a time is slower than no queue at all.
#
# `dispatch` takes refs for the one case that is neither ready nor blocked —
# the operator has not authorized a task yet. That is a fact about this moment,
# so it records nothing: the task stays queued and the next bare `dispatch`
# sends it. Bare `dispatch` is still the norm and still sends everything.
#
# COMPLETION COMES FROM TWO PLACES, on purpose, and RELEASE FROM A THIRD:
#
#   `watch`    reads `thurbox-cli watch` — the event stream — and folds each
#              transition into the task's progress.jsonl. It closes NOTHING.
#              A turn ending is not a task finishing.
#   `collect`  reads the result.md a worker wrote when it knew what it had
#              concluded, and only that closes a task. It also CHECKS that
#              task's artifact against the PUBLISH METHOD the task declares —
#              `attested`, `pr` or `push`, which name what the work must
#              LEAVE BEHIND rather than which tool made it. So "publish the way
#              your brief says" stops being an unverifiable instruction about a
#              method: collect asks the forge for a change request — a pull
#              request on GitHub, a merge request on GitLab — from this task's
#              own branch (and, for `attested`, an attestation for the
#              commit that would merge), or asks git whether a `push` task's
#              commit reached the base branch. An artifact that is not there is
#              reported and the task is left OPEN; a check that could not run
#              (no forge CLI, no network, a base branch this machine cannot read)
#              says so and is never read as either verdict. The TOOL is
#              `--how`: free text rendered into the brief and never parsed,
#              which is what lets a task name a publisher fleet has never heard
#              of. `add` takes both, defaulting to POLICY.md's frontmatter.
#   `reap`     asks the FORGE whether each concluded task's change request has
#              merged, moves the ones that did to `landed`, and only then
#              deletes their sessions and worktrees. A `push` task has nothing
#              left to ask by this point — `collect` already confirmed its
#              commit reached the base branch before closing it — so it lands
#              in the same run reap follows. `collect` runs it, because
#              "delete each session as it closes out" was a documented MANUAL
#              step and twenty gigabytes sat in a worktree whose change request
#              had merged the day before. It never touches a session thurbox
#              says is working, and never one a worker gave up in — that
#              session is the evidence.
#
# Both are things the lead READS when it chooses. Neither pushes anything into
# its terminal, which is what `thurbox-cli message send` does and why the queue
# does not use it: an arriving worker message interrupts whoever is talking to
# the lead.
#
# AND THE OTHER DIRECTION IS `send`, WHICH IS NOT A SIXTH THING. It is how the
# lead course-corrects a worker mid-flight, and the only reason it belongs here
# rather than in `thurbox-cli session send` is that it WRITES THE INSTANT DOWN.
# The lead knows when it sent; nothing recorded it, and without that instant
# there is nothing to compare a later observation against. On 2026-09-09 a
# parked worker was sent new scope, `session send` reported success, and ten
# minutes later the session read `done` with an age of 3043s — a state from
# before the message. The worker had taken it, done the work and committed it,
# and the lead found that out by running `git log` in a foreign worktree.
#
# So `send` records the send plus a BASELINE (the branch head), and the
# read-only views compare it against two things a worker cannot fake: the head
# of its branch, read out of the task's own repo because a worktree shares that
# object store, and a transition in progress.jsonl DATED after the message.
# `list` says `messaged 12m ago · committed 4m ago` or `messaged 12m ago · no
# transition since, no commit since`; `show` says it source by source. Both are
# facts. Neither is ever "the worker is stuck", and a source that could not be
# read is `not checked` rather than a silent no. It adds no daemon, no poll and
# no state: `collect` is still the only thing that closes a task.
#
# AND THEN THE CHANGE REQUEST OUTLIVES THE TASK, which is what `shepherd` is for:
#
#   `shepherd` asks the FORGE for every open change request on the repos this
#              queue's tasks name, DISPATCHES A FIXER for one that conflicts,
#              fails a check, has a review asking for changes, or was declared
#              `attested` and carries no attestation — and squash-merges
#              one that clears every gate. It is a fourth thing, after both
#              halves of completion.
#
#              It reads the forge and not the task records because a task
#              records ONE artifact, the first change request its worker
#              reported: #25 was a SECOND pull request from a task still
#              pointing at the merged #23, and a PR opened outside the queue
#              was invisible the same way. One no task records is shepherded
#              like any other; it just has no session to send a fixer into,
#              and that is said out loud.
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
#                work in flight rather than a still-broken change request.
#                `--force` to mean it anyway.
#   NEVER GUESS  no forge, no network, no thurbox — it says what it could not
#                determine and carries on. A change request it could not read
#                is never called broken, and never called ready.
#   NEVER TOUCH  only artifacts recorded on this queue's own tasks, and it
#   A STRANGER   merges only in the repos the operator's own, gitignored
#                orchestration/auto-merge.conf names — nowhere at all until
#                that file exists — each of which names its forge
#                (`github.com/owner/repo`, `gitlab.example.com/group/project`).
#
# Usage:
#   scripts/queue.sh topic add <slug> --title T --prompt 'the ask'   # or --prompt-file F|-
#   scripts/queue.sh add <topic> <slug> --title T --repo P --branch B [--base main]
#                        [--host H] [--profile default] [--touches a,b] [--brief-file F]
#                        [--publish attested|pr|push] [--how 'run `/publish`']
#                        # --brief-file fills whichever of the brief's four
#                        # sections its own `## ` headings name; a body with no
#                        # headings all goes into `What to do`. A file that
#                        # leaves any section unwritten is refused HERE, naming
#                        # them, and nothing is created — as is a --branch no
#                        # worktree could be cut for, which includes --base,
#                        # and a --title thurbox could not make a session name
#                        # of: that name is the title wearing the worker's mark
#                        # and cut to thurbox's byte cap, and it carries no
#                        # `/`, no `\`, no `..` and no leading `.`
#   scripts/queue.sh block <ref> --on <ref> --kind KIND --why 'reason'   # or --clear,
#                        which names the blocker to remove, since a task can
#                        carry several; `block --help` lists the valid kinds
#   scripts/queue.sh block <ref> --condition 'what holds it' --kind KIND --why 'reason'
#                        # the second form: a wait on something OUTSIDE the
#                        # queue, which clears only when you run
#                        # `block <ref> --clear --condition 'what holds it'`.
#                        # Its kinds are their own closed set, also in --help
#   scripts/queue.sh plan [--json]        # what goes out now, what waits, and why
#   scripts/queue.sh dispatch [<ref>...] [--dry-run]  # the whole ready set at
#                        once with no ref, which is the norm; refs launch
#                        exactly those, refuse one that is not ready, and leave
#                        the rest queued with nothing recorded
#   scripts/queue.sh attach <ref> <uuid>  # record a session you spawned by hand
#   scripts/queue.sh send <ref> 'one line'  # message a task's worker, and
#                        RECORD that you did, so `list` and `show` can compare
#                        that instant against what moved after it
#   scripts/queue.sh watch [--for-secs N] # fold transitions in; close nothing
#   scripts/queue.sh collect [--allow-unverified] [--no-reap]  # read results,
#                        close what is done; --allow-unverified closes one whose
#                        artifact failed the publish check, after you have
#                        judged that artifact; --no-reap leaves every session
#                        alone
#   scripts/queue.sh reap [--dry-run]     # land what merged, release its session
#   scripts/queue.sh refuel [<ref>] [--dry-run]  # the account's fuel first, then
#                        restart the workers that ran dry against it
#   scripts/queue.sh shepherd [--dry-run] # every open change request on the repo: fix or merge
#                        [--json] [--topic T] [--ref R] [--no-merge] [--force]
#   scripts/queue.sh run [<topic>]        # refresh the run log(s) by hand
#   scripts/queue.sh list [--topic T] [--archived] [--all]  # the lead's view:
#                        a line per task; archived topics hidden by default
#   scripts/queue.sh archive <topic>      # hide a finished topic from every
#                        default view; refuses one with a live task
#   scripts/queue.sh unarchive <topic>    # put it back in every view
#   scripts/queue.sh show <ref>           # one task's whole record, archived or not
#   scripts/queue.sh check                # validate every record (./scripts/check.sh queue)
#   scripts/queue.sh root [--foreign]     # the resolved queue directory,
#                        absolute; --foreign instead names the control plane,
#                        and exits 0, only when this checkout is not it
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
# where it stands: the host answers ssh as a POSIX shell, the repo is a
# checkout at that path, and it has credentials OF ITS OWN for the forge that
# repo's `origin` names — GitHub or GitLab. The repo is asked about before its
# forge because which forge to prove a credential against is a fact about that
# checkout's origin, so it cannot be asked before the checkout is known to
# exist. Fleet never sends credentials anywhere. POSIX hosts only — a Windows
# host (hosts.toml spells one with a non-tmux `multiplexer`) is refused by
# name.
#
# TWO THINGS ABOUT A REMOTE SPAWN THAT ARE NOT OPTIONS. It gets no `--parent`:
# thurbox refuses a parent that lives on another host and the lead is local, so
# the link a local worker gets is one a remote worker cannot legally have.
# And every command fleet runs on a host goes through a LOGIN shell, because
# `ssh host 'cmd'` sources no profile and the binaries these probes ask about
# live in `~/.local/bin` — a bare shell answers "not installed" about a host
# where it is installed, which is exactly how a working host got written off.
# The two calls that only move bytes (the brief out, the result back) stay
# bare, so a profile that prints cannot land its banner inside them.
#
# THE RUN LOG IS PRODUCED, NOT REMEMBERED. `AGENTS.md` step 5 used to say
# "record the run in orchestration/runs/ as it happens", and two consecutive
# runs did not: one file existed only because its lead session was being
# migrated, the other was reconstructed from chat history at the end. Every
# other artefact of the loop is scaffolded without anyone choosing to make it,
# so this one is too:
#
#   `topic add` opens `orchestration/runs/<opened>-<topic>.md` from the tracked
#       _TEMPLATE.md — one log per topic, because a topic is one unit of intent
#       and its slug and date name the file with no pointer to keep in step.
#   `dispatch`, `collect` and `shepherd` REWRITE a fenced block inside it from
#       the records — never append, because collect runs many times over one
#       run and a line per pass is a timeline nobody reads. The block is a pure
#       function of the records, so a refresh that changes nothing says nothing.
#   EVERYTHING OUTSIDE THE FENCE IS THE LEAD'S — the goal in its own words, the
#       decisions, what went wrong, the outcome. None of that can come from a
#       record, and it is why the file exists. Remove the fence and the queue
#       reports the file and never writes it again.
#   `run` is that refresh made explicit, for a topic older than the feature and
#       for a lead that just wants the path.
#
# Run logs are gitignored working state, like the queue: they may hold machine
# paths and session ids freely, and _TEMPLATE.md is the one tracked file there.
#
# THE QUEUE BELONGS TO ONE CHECKOUT — the CONTROL PLANE's, the clone the
# Mission Control session opens. It is never resolved against the shell's cwd,
# so this script does the same thing from any directory. That matters because a
# second clone of this repo is the SUPPORTED shape here: a control plane with no
# `origin` of its own needs one that workers can branch and push from. Running
# `topic add` in that clone used to write a whole second queue in silence,
# with the TUI pane correctly showing nothing. Now:
#
#   `topic add` and `add` REFUSE outside the control plane. Creating a record
#       is the only act that can fork the queue, so that is where the hard stop
#       goes, and the refusal names both paths and the override.
#   Everything else WARNS — once this checkout actually holds records — and
#       carries on, because a second clone is a fine place to read a queue from
#       and taking that away helps nobody.
#   Neither fires when the control plane cannot be identified — no rendered
#       extension.toml and no live Mission Control session. A fleet used
#       without the thurbox extension is legitimate and must not be made
#       unusable by a guard that cannot tell whether it is warranted.
#
# `queue.sh root`, `list` and `check` all print the resolved directory, so
# "why is the pane empty?" is a question the tooling answers about itself.
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
#   FLEET_RUNS_DIR         where run logs are written (default: this
#                          checkout's orchestration/runs). The _TEMPLATE.md
#                          they are scaffolded from is always the checkout's.
#   FLEET_QUEUE_WATCH_CMD  the event source, for a replay or another transport
#                          (default: thurbox-cli watch --json)
#   THURBOX_SESSION        set inside a thurbox session; dispatch passes it as
#                          --parent so `session list --parent` enumerates
#                          workers, and reap refuses to delete it
#
# Requires: python3 (with PyYAML) — the same dependency the rest of the gate
# has. `dispatch`, `watch`, `reap` and `refuel` additionally need thurbox-cli,
# and `collect`, `reap` and `shepherd` ask the FORGE about a change request —
# whichever `scripts/lib/forge.py` has configured: `gh` for GitHub, `glab` for
# GitLab — and `shepherd` needs git as well. `refuel` reads the account's
# quota window with
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
