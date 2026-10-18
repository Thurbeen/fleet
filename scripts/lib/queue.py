#!/usr/bin/env python3
"""The fleet task queue: durable records on disk, ordered by recorded blockers.

Run as `uv run fleet queue <verb>`. This docstring owns the model and, at its
end, the usage. Three ideas, and the whole thing follows from them:

1. A PROMPT BECOMES A RECORD. Not a turn in a conversation that a context reset
   loses. A prompt opens a TOPIC — the unit of intent, which is usually several
   units of work across several repos — and the topic decomposes into TASKS.
   The topic keeps the prompt verbatim in PROMPT.md; nobody has to remember
   what was actually asked.

2. INSTRUCTIONS LIVE IN FILES. Each task carries its own BRIEF.md and nothing
   else reads it. The lead holds the index — `list`, `plan` — and never every
   task's detail at once, so a queue of twenty tasks costs the lead twenty
   lines rather than twenty briefs. That is structural: it does not depend on
   the agent being disciplined about what it reads.

3. ORDER COMES FROM RECORDED BLOCKERS, NOT FROM CAUTION. `ready` is every
   queued task whose recorded blockers are all genuinely done, and it is
   dispatched at once with no cap. File overlap is reported beside the ready
   set as a RISK NOTE and never removes anything from it. A controller that
   serializes by default is slower than no controller at all.

WHAT A COMPLETION IS. Two halves, deliberately from two sources:

    the WHEN   `thurbox-cli watch` — an event stream the lead reads on its own
               cadence. `watch` folds transitions into progress.jsonl and
               closes nothing, because a turn ending is not a task finishing.
    the WHAT   result.md, which the worker writes when it knows what it
               concluded. `collect` reads it and only then does a task close.

And a THIRD thing, after both: a task that is `done` has an OPEN pull request,
not a merged one, and its session is still the cheap way to fix what review
finds. When the artifact actually lands — which only the forge knows, usually
long after the worker is gone — the task moves to `landed` and its session and
worktree are released. `reap` below owns that, and argues it.

The pair replaces `thurbox-cli message send`, which is exact but WAKES the
recipient: an arriving worker message pushes into the lead's conversation and
interrupts whoever is talking to it. A stream that is read and a file that is
read interrupt nobody. `--no-wake` would not interrupt either, and it is still
not the completion: `watch` already says WHEN with nothing sent, `collect`
verifies the WHAT, and a third signal is one more thing to drain. The one
fleet use of the mailbox is the reconciler's own notices, in notify_conductor.py
and notify_origin.py.

WHICH CHECKOUT. The queue belongs to the CONTROL PLANE's clone — the one the
Mission Control session opens — and never to the process cwd. A second clone
of this repo is supported and common (a control plane with no `origin` needs one
that workers push from), and resolving the queue against the cwd meant working
in that clone silently forked it. `queue_root()` and `guard_creating()` below
own that; the refuse-vs-warn split is argued at `guard_creating`.

Layout under $FLEET_QUEUE_DIR (default: this checkout's orchestration/queue,
gitignored):

    <topic>/topic.yaml            the topic record
    <topic>/PROMPT.md             the prompt, verbatim
    <topic>/<NN>-<slug>/task.yaml       intent and current state  (the PLAN)
    <topic>/<NN>-<slug>/BRIEF.md        what the worker reads     (the PLAN)
    <topic>/<NN>-<slug>/progress.jsonl  transitions, appended     (the PROGRESS)
    <topic>/<NN>-<slug>/result.md       the worker's conclusion   (the RESULT)

There is no queue-wide cursor. Each task resumes the stream from its OWN
progress.jsonl, so one task's events can never consume another's.

Those four files are why a topic view needs no field this file does not
already have: intent, progress and outcome are separate artifacts rather than
one status word.

WHERE A TASK RUNS. A task may name a `host` from thurbox's hosts.toml, and then
its worker — agent, tmux window and worktree — lives on that machine while the
queue stays here. That changes the transport and NOTHING else: the brief is
copied to the host, into `<worktree>.fleet` beside the worktree and never into
it (COMPANION_SUFFIX), before the worker is prompted, and the result is fetched back
into this same result.md before it is read. See the remote-hosts section below
for why the alternative (a worker reporting through `message send`) was refused.
A task with no host is untouched by any of it.

A BLOCKER NAMES A TASK OR A CONDITION. `--on <ref>` is the wait with an end: it
clears when that task LANDS. `--condition '<what>'` is the wait on something the
queue cannot observe — a credential, an approval, a window, a machine somebody
has to fix, a decision nobody has made. Nothing clears one but `block --clear`
naming it back: no timer, no `collect`, no `reap`, and no inference from a later
dispatch working. Every blocker names a kind and a reason, so it survives the
next planning pass instead of being re-derived; `block` refuses one that does
not.

`dispatch` takes refs for the one case that is neither ready nor blocked — the
operator has not authorized a task yet. That is a fact about this moment, so it
records nothing: the task stays queued and the next bare `dispatch` sends it.
`--origin ID` and `--topic SLUG` scope a bare `dispatch` to one origin's or one
topic's ready tasks, so an origin sends its own work and nobody else's.

ONE DISPATCHER PER TASK. `dispatch` holds the task's `dispatch.lock` from its
readiness re-check to the save of `dispatched`; a second dispatcher prints
`being dispatched by another process` and writes nothing for that task. The OS
releases the lock however its holder dies, and the task stays `queued` until
the save. A dispatcher that died after `session create` and before that save
left a live session no record names: the next `dispatch` adopts it — this
machine's session under the task's name, on its branch in every repository the
task spans — the way `attach` does, instead of failing on the duplicate name.

WHAT `collect` CHECKS. A task declares a PUBLISH METHOD — `attested`, `pr`,
`push`, `note`, `served` or `none` — which names what the work must LEAVE
BEHIND rather than which tool made it. `collect` asks the forge for a change
request from the task's own branch or its recorded `--target` (and, for
`attested`, an attestation for the commit that would merge, unless the forge
already reports it merged), the forge again for a `note` task's review or
comment on its `--target`, or git whether a `push` task's commit reached the
base branch. An artifact that is not there leaves the task OPEN; a check that
could not run says so and is never read as either verdict. The last two are
checked nowhere, and they differ in what happens AFTER: a `none` task is
finished, and a `served` one is a document waiting on a READER, so it holds
its session until a person runs `fleet queue reviewed <ref>`. The TOOL is
`--how`: free text rendered into the brief and never parsed, which is what lets
a task name a publisher fleet has never heard of; a `served` task with none
takes the operator's `SERVE` line from publish.conf, which says where pages
are hosted. A `stuck` or `failed` task is read again, and acted on only when
the outcome in its result.md CHANGED.

`abandon` IS THE ONE HAND-MADE WAY INTO A TERMINAL STATE. A task that will
never run — superseded, or held by a condition nobody will clear — would
otherwise read `waiting` forever and hold its topic open. It reaches the same
`abandoned` two ends nobody declares reach on their own: a change request
closed unmerged, and a dispatched task whose session thurbox stopped listing
with no result written (`sweep_vanished`). One state, so nothing downstream
learns a new word; `abandoned.how` keeps the three apart in every view —
`closed unmerged`, `session gone`, `abandoned by hand`. A blocker naming any
of them stays blocked, and the verb names those dependants for the lead to
decide.

`send` IS NOT A SIXTH THING. It is how the lead course-corrects a worker
mid-flight, and it belongs here rather than in `thurbox-cli session send`
because it WRITES THE INSTANT DOWN, plus a BASELINE (the branch head). `list`
and `show` compare that against two things a worker cannot fake — the head of
its branch and a transition in progress.jsonl dated after the message — and a
source that could not be read is `not checked` rather than a silent no.

`shepherd` and `refuel` are the fourth and fifth things, each argued at its own
section below: the change request outlives the task, and a worker that hits its
agent's token limit SITS rather than failing.

An independent review can be required separately from publication, through
publish.conf's opt-in contract. `review_lifecycle.py` owns it: pending heads
reach the configured reviewer, findings reach the existing fixer, and only a
linked assessment recorded from the independent reviewer session discharges
review. `plan` exposes obligations to the lead; merging still uses shepherd's
single gate. The configured reviewer is held for future heads and PRs even
after its result is collected. Empty configuration preserves existing behavior.

A DRAFT PHASE is the other opt-in there (publish.conf's DRAFT_REPOS): a task's
pull request opens as a draft, bots review it, and `shepherd` marks it ready
for review once it would otherwise be `ready` — once, never back, never merged
in the same pass. `draft_repos` and the comment above it own the rule.

THE RUN LOG IS PRODUCED, NOT REMEMBERED. `topic add` opens
`orchestration/runs/<opened>-<topic>.md` from the tracked _TEMPLATE.md, once;
`dispatch`, `collect` and `shepherd` rewrite the facts in the
`<opened>-<topic>.facts.md` beside it from the records, and the log itself is
the lead's and never written again. `run` is that refresh made explicit, and
`run --all` reaches archived topics too.

`find` IS THE STABLE READ FOR ANOTHER PROGRAM, so its shape is a contract:

  uv run fleet queue find --origin STR --json
      # a JSON list, one object per task whose `origin` equals STR exactly and
      # whose `session` is recorded and not terminal (not landed or abandoned,
      # not reaped): [{"ref": "topic/01-slug", "state": "dispatched",
      # "session": "<thurbox session id>", "host": null | "<host name>"}],
      # sorted by ref. `[]` and exit 0 when there are none. Read-only; whether
      # thurbox still lists the session is the caller's to ask.

Usage:
  uv run fleet queue topic add <slug> --title T --prompt 'the ask'   # or --prompt-file F|-
                       [--by PERSON]  # who asked, as free text: written to
                       # topic.yaml's `requested_by`, shown, and acted on by
                       # nothing; its tasks inherit it
  uv run fleet queue add <topic> <slug> --title T --repo P --branch B [--base main]
                       [--by PERSON]  # overrides the topic's requester; the
                       # task.yaml stores the RESOLVED name, so editing the
                       # topic later rewrites no history. None anywhere is
                       # valid and means "not recorded"
                       [--add-dir P] [--add-repo P[@BASE]] [--host H]
                       [--profile default] [--touches a,b] [--brief-file F]
                       # --add-dir, repeatable, attaches another directory to
                       # the worker's session exactly as it is: no worktree, no
                       # branch, nothing to publish. It is how a worker reads a
                       # sibling repository or a docs tree while it works.
                       # --add-repo, repeatable, is a second repository the
                       # task also COMMITS in — its own worktree, the same
                       # --branch, off BASE or off --base. The task then leaves
                       # one artifact PER REPOSITORY: `collect` holds it open
                       # unless every one verifies, `reap` lands it only once
                       # every one has landed, and `shepherd` watches them all.
                       [--publish attested|pr|push|note|served|none]
                       [--target U]
                       [--how 'run `/publish`']
                       [--origin STR] [--reply CMD]
                       # --origin says who OWNS the task's conversation and
                       # --reply is the command a worker runs to speak to them:
                       # two opaque strings, stored as `origin` and `reply`,
                       # shown by `show`, checked as text by `check`, and
                       # parsed by nothing. Without --origin, inside thurbox,
                       # the origin is the calling session, stored as
                       # `thurbox:<session id>` — the one form fleet reads —
                       # and without --reply a session origin gets
                       # `thurbox-cli message send --to <id> --kind reply
                       # --body` (none on a --host task). Outside thurbox both
                       # stay empty. `reply` also adds one fixed section to
                       # the brief (verdict, PR URL, every question BEFORE
                       # `declare needs-input`, and `needs-input` in its own
                       # pane when the command fails); with none the brief is
                       # unchanged. `owners.conf` routes a matching origin to
                       # a session or a `!command` relay, and `origin_target`
                       # resolves an origin to one of them, or to `gone`.
                       # --brief-file fills whichever of the brief's four
                       # sections its own `## ` headings name; a body with no
                       # headings all goes into `What to do`. A file that
                       # leaves any section unwritten is refused HERE, naming
                       # them, and nothing is created — as is a --branch no
                       # worktree could be cut for, which includes --base,
                       # and a --title thurbox could not make a session name
                       # of: that name is the title wearing the worker's mark
                       # and cut to thurbox's byte cap, and it carries no
                       # `/`, no `\\`, no `..` and no leading `.`.
                       # A leading `NN-` on the slug is dropped, since add
                       # numbers the task itself; stdout is the final ref.
  uv run fleet queue block <ref> --on <ref> --kind KIND --why 'reason'   # or --clear,
                       which names the blocker to remove, since a task can
                       carry several — as written, so it clears one naming a
                       task whose record is gone; `block --help` lists the
                       valid kinds
  uv run fleet queue block <ref> --condition 'what holds it' --kind KIND --why 'reason'
                       # the second form; clears only with
                       # `block <ref> --clear --condition 'what holds it'`.
                       # Its kinds are their own closed set, also in --help
  uv run fleet queue plan [--json]        # what goes out now, what waits, and why;
                       and which dispatched workers are STALLED — at rest past
                       half an hour with no result.md and no new commit,
                       unless the worker declared a wait, which it lists as
                       `paused` or `needs_input` instead — or UNPROMPTED,
                       dispatched minutes ago and never briefed
  uv run fleet queue declare <ref> paused --until TIME --why W  # a worker's
                       own word: waiting by design until TIME (ISO-8601 with
                       an offset, or 45m / 2h; at most 6h away), so not
                       stalled meanwhile
  uv run fleet queue declare <ref> needs-input --why W  # or: a decision is
                       needed, told to the lead once. Either counts only until
                       the next dispatch, restart or delivered `send`
  uv run fleet queue dispatch [<ref>...] [--origin ID | --topic SLUG] [--dry-run]
                       # the whole ready set at once with no ref, which is the
                       norm; refs launch exactly those, refuse one that is not
                       ready, and leave the rest queued with nothing recorded;
                       --origin / --topic restrict the ready set instead, and
                       are refused beside refs
  uv run fleet queue attach <ref> <uuid> [--prompted | --unprompted]
                       # record a session you spawned by hand; working, blocked
                       or done implies prompted; idle needs an explicit override
  uv run fleet queue prompt [<ref>]       # retry the handoff to a session that
                       was created but not prompted; every such one by default
  uv run fleet queue send <ref> 'one line'  # message a task's worker, and
                       RECORD that you did
  uv run fleet queue watch [--for-secs N] # fold transitions in; close nothing
  uv run fleet queue collect [--allow-unverified <ref>...] [--no-reap]  # read
                       results, close what is done; --allow-unverified closes
                       exactly the tasks it names whose artifact failed the
                       publish check, after you have judged each artifact;
                       --no-reap leaves every session alone
  uv run fleet queue reap [--dry-run]     # land what merged, release its session;
                       retire what closed unmerged or whose session vanished,
                       and free the build output a vanished session left
  uv run fleet queue reviewed <ref> [--why W]  # a `served` task's document is no
                       longer waiting on a reader: the next reap lands it and
                       releases the session kept to answer them
  uv run fleet queue review record <PR-URL> --head SHA --score N --findings N
                       --evidence URL  # assigned independent reviewer only
  uv run fleet queue abandon <ref>... --why W [--force]  # retire tasks that
                       will never run; or --topic T for every task in it not
                       landed or abandoned. Refuses `landed`, and a dispatched
                       task whose session is listed unless --force; names every
                       task still blocked on one; never touches a session
  uv run fleet queue refuel [<ref>] [--dry-run]  # the account's fuel first, then
                       restart the workers that ran dry against it
  uv run fleet queue shepherd [--dry-run] # every open change request on the repo: fix or merge
                       [--json] [--topic T] [--ref R] [--no-merge] [--force];
                       also adopts sessions fleet did not spawn, and writes
                       the PR-state store (shepherd_store.py)
  uv run fleet queue prs [--json]         # every watched session's PR state,
                       as the last shepherd pass recorded it
  uv run fleet queue run [<topic>] [--all]  # refresh the run facts by hand
  uv run fleet queue list [--topic T] [--archived] [--all] [--live]  # the
                       lead's view: a line per task; archived topics hidden by
                       default; --live re-reads each open task's change
                       requests from the forge and names where the record lags
  uv run fleet queue archive <topic>      # hide a finished topic from every
                       default view; refuses one with a live task
  uv run fleet queue unarchive <topic>    # put it back in every view
  uv run fleet queue show <ref>           # one task's whole record, archived or not
  uv run fleet queue check                # validate every record (`fleet status --records`)
  uv run fleet queue root [--foreign]     # the resolved queue directory,
                       absolute; --foreign instead names the control plane,
                       and exits 0, only when this checkout is not it

A ref is `<topic>/<task>`, or a bare task id when only one topic has it.
`uv run fleet queue <verb> --help` gives each verb's flags.

Environment:
  FLEET_QUEUE_DIR        where the queue lives (default: this checkout's
                         orchestration/queue). Honoured VERBATIM and never
                         guarded — someone who set it meant it.
  FLEET_RUNS_DIR         where run logs are written (default: this
                         checkout's orchestration/runs). The _TEMPLATE.md
                         they are scaffolded from is always the checkout's.
  FLEET_QUEUE_WATCH_CMD  the event source, for a replay or another transport
                         (default: thurbox-cli watch --json); one command,
                         split with shell quoting (on Windows a backslash is
                         an ordinary character) and run with no shell, so
                         not a pipeline
  THURBOX_SESSION        set inside a thurbox session; add records it as the
                         task's origin when none is given, dispatch passes it
                         as --parent so `session list --parent` enumerates
                         workers, and reap refuses to delete it

Requires: uv, which brings the Python and the PyYAML pinned in uv.lock.
`dispatch`, `watch`, `reap` and `refuel` additionally need thurbox-cli, and
`collect`, `reap` and `shepherd` ask the FORGE about a change request —
whichever `scripts/lib/forge.py` has configured: `gh` for GitHub, `glab` for
GitLab — and `shepherd` needs git as well. `refuel` reads the account's quota
window with `quota-axi` (https://github.com/kunchenguid/quota-axi), which fleet
neither installs nor sends any credential to. A task that names a `--host`
additionally needs `ssh`. Every one of those degrades to "could not check"
rather than to a guess.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import difflib
import fnmatch
import glob
import importlib.util
import json
import ntpath
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
import tomllib
import unicodedata
from pathlib import Path
from typing import NamedTuple
from datetime import datetime, timezone

import yaml


def _load_sibling(name: str, filename: str):
    """Load a module from scripts/lib beside this file, under a name of its own.

    Not a plain `import forge`: this file is loaded three ways — as `queue` off
    `scripts/lib` on sys.path, and by `fleet_status.py` and the pane harness
    through importlib with no path change at all — and only the first of those
    would find a sibling module. Keyed in sys.modules so every one of them
    shares ONE registry, and therefore one answer about which forges are
    configured.
    """
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# WHICH FORGE. Everything fleet knows about a change request — a pull request
# on GitHub, a merge request on GitLab — it asks this module for. Nothing in
# this file runs a forge CLI or builds a forge URL; the one exception is
# FORGE_PROBE_TEMPLATE's ssh check below, which is a different coupling (git
# hosting, not the forge API) and reads the repository's own `origin` rather
# than naming a forge.
forge = _load_sibling("fleet_forge", "forge.py")

# WHICH OS. Every place this file behaves differently on POSIX and Windows —
# where thurbox keeps its config, how a record reaches the disk — it asks this
# module, and nothing here reads `os.name`.
fleet_platform = _load_sibling("fleet_platform", "fleet_platform.py")

# WHICH AGENT. Everything fleet knows about one agent — its trust dialog, its
# limit signal, where its transcripts are, which account it draws on — is a
# fact about that agent and not about this checkout. This module owns
# `orchestration/agent.conf` and the rule that resolves a setting for one
# agent; nothing here parses that file itself.
agent_settings = _load_sibling("fleet_agent_settings", "agent_settings.py")

# So that a sibling loading THIS file back — `fleet_status.py`, which the fuel
# gauge below loads lazily — finds this copy under the key every loader uses,
# whichever name this one was run under: `queue` off sys.path, `__main__` from
# the command line. The same line `forge.py` ends with, for the same reason:
# one module object, one `_STREAM_HIGH`, one forge registry.
sys.modules.setdefault("fleet_queue", sys.modules[__name__])

# The four conditions that justify making one task wait for another. They are
# firstmate's, and they are a closed set on purpose: "these edit the same file"
# is not among them and cannot be spelled here.
BLOCKER_KINDS = {
    "semantic-dependency": "this task consumes something the other one introduces",
    "shared-external-state": "both mutate the same external state",
    "incompatible-migration": "the two migrations cannot be in flight together",
    "other": "another concrete condition that makes independent progress unsafe",
}

# THE SECOND FORM OF BLOCKER: a task held by something that is not a task.
#
# WHY IT EXISTS, measured. On 2026-09-11
# `vending-machine-egress-resume/01-vm-identity-reconciliation` was ready by
# every record fleet keeps and unrunnable in fact — its brief's first
# instruction reads Azure and `az` was not authenticated. `block` took only
# `--on <ref>`, so there was nothing to write down: `plan` listed the task as
# ready and `notify_conductor.py` correctly woke the lead to dispatch it. The only
# honest answer was to refuse in conversation and leave the record silent,
# which is the one thing this queue exists not to do.
#
# A condition is a durable, nameable reason a task is not ready that no task
# will ever satisfy: a credential, an approval, a window, a machine somebody
# has to fix, a decision nobody has made. Its kinds are their own closed set
# rather than a reuse of the four above, because those four all describe a
# relationship BETWEEN TASKS — every condition would land on `other` and the
# set would stop saying anything.
#
# AND NOTHING CLEARS ONE BUT A HAND. A task blocker clears when the task it
# names LANDS, which is an event the forge reports. A condition has nothing to
# observe, so `blocker_cleared` answers False for it forever and `block
# <ref> --clear --condition ...` is the only way out. A condition that expired
# on a timer, or on a later dispatch appearing to work, would put back exactly
# the silence it was recorded to break.
CONDITION_KINDS = {
    "missing-credential": "a login, secret or session the work needs is not present",
    "awaiting-approval": "a person or a process has to say yes before this can run",
    "closed-window": "it may only run inside a window that is not open",
    "broken-dependency": "something outside the queue is broken and has to be fixed",
    "undecided": "the operator has not made a decision this task turns on",
    "other": "another durable thing outside the queue — name it in --why",
}


def blocker_condition(blocker: dict) -> str:
    """The condition an entry names, or "" when it names a task instead.

    The ONE test for which form a `blocked_by` entry is, so the eight readers
    of that list cannot come to eight opinions about it. An entry carries
    `task:` or `condition:` and never both — `cmd_block` writes exactly one.
    """
    return str(blocker.get("condition") or "").strip()


# `stuck` and `failed` are the worker's own verdicts, not an observation of its
# session. Nothing here derives a state from a transition.
#
# `landed` is the one state a WORKER never produces. It comes after `done` and
# it is the forge's answer, not anyone's claim — see the landing section below
# for why the two have to be different words.
STATES = ("queued", "dispatched", "done", "landed", "stuck", "failed", "abandoned")
OUTCOMES = {
    "shipped": "done",
    "not-applicable": "done",
    "stuck": "stuck",
    "failed": "failed",
}

# A task in one of these has concluded. It is not waiting for anything, so a
# blocker still recorded against it holds nothing — `landed` printed with a
# "held by" line under it is what made this a named set rather than a
# comparison written out three times.
CONCLUDED_STATES = ("done", "landed", "stuck", "failed", "abandoned")

# Concluded AND never going to be on main: the forge closed it unmerged, or a
# person gave it up. A blocker naming one does not clear, and calling it "held
# by" describes a wait with no end. `stuck` and `failed` are not here: a worker
# can give up on work whose change request then merges anyway, and
# `sweep_landings` lands that task the moment every artifact it named has.
UNLANDABLE_STATES = ("abandoned",)

# The two a WORKER's own verdict put there, so the same worker's file can take
# them back. `collect` keeps reading their result.md and acts only when the
# outcome in it CHANGED: a worker whose shell died wrote `stuck`, recovered,
# and rewrote it `shipped` with a pull request that had merged all along. That
# rewrite is new evidence, and a `shipped` still has to pass the publish check,
# so the task stays where a human decides until something proves otherwise.
# `abandoned` is not here: the forge said that, not a worker.
REREAD_STATES = ("stuck", "failed")

# Where each outcome a worker may write says the task should stand once the
# forge has answered. `landed` is the merge; `abandoned` is the other answer to
# that same question, so it agrees with no outcome at all — `abandoned` beside
# `shipped` is two recorded facts that contradict, and the display says so
# instead of printing them side by side as though they agreed.
OUTCOME_STATES = {
    "shipped": ("done", "landed"),
    "not-applicable": ("done", "landed"),
    "stuck": ("stuck",),
    "failed": ("failed",),
}

# The task states a topic can be archived out from under, and the ONE place
# the predicate is written down: the automatic sweep, the manual `archive` and
# the clear `add` does all call `unfinished()` below rather than spelling this
# again. A view that hides live work is worse than the clutter archiving
# removes, so the three must never be able to drift apart.
#
# `stuck` and `failed` are deliberately absent. They are the WORKER's own
# verdicts, the sessions behind them are kept as evidence rather than reaped
# (see HOLDING_STATES and the reap gate below), and the whole point of keeping
# a session is that a human comes and looks at it. A topic holding either has
# to stay in front of the operator, however finished the rest of it is.
TERMINAL_STATES = ("landed", "abandoned")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
# The ordinal `add` prefixes, which a slug must not carry a second time.
ORDINAL_RE = re.compile(r"^(?:\d{2}-)+", re.ASCII)
BRIEF_PLACEHOLDER = "<!-- WRITE THE INSTRUCTIONS HERE -->"

# The sections every brief has, in order. The lead was designing a document per
# brief instead of filling one in: across seven hand-written briefs, 1191 lines,
# the scaffold's four headings were joined by 20 invented ones. A fixed skeleton
# costs the lead nothing and removes that whole decision.
#
# Every section starts as the SAME placeholder `dispatch` refuses, so the
# refusal now covers a half-written brief and not only a blank one. A section
# with nothing to say is filled in with "None" -- which is a claim the worker
# can rely on, unlike an absent heading.
BRIEF_SECTIONS = (
    "What to do",
    "Hard constraints",
    "Coordination",
    "Done means",
)


def split_brief_body(body: str) -> dict:
    """A `--brief-file`'s own `## ` headings, mapped onto BRIEF_SECTIONS.

    The whole file used to go into section 0, so a body that already carried
    the four standard headings produced a brief with `## What to do` twice and
    three untouched placeholders -- which `dispatch` then refused. Five briefs
    were hand-repaired that way in one session.

    This relaxes nothing. BRIEF_SECTIONS is still every heading a brief has and
    still the order they come in; a file only fills them. A heading that is NOT
    one of the four is content and is kept verbatim, in place, under whichever
    section it appeared in: dropping it loses what the lead wrote, and
    promoting it is the 20 invented headings the skeleton exists to stop. Text
    before the first heading is section 0's, so a body with no headings at all
    lands exactly where it landed before.

    A `## ` inside a fenced block is a brief quoting markdown, not opening a
    section, and stays where it was written.
    """
    parts: dict[str, list] = {}
    current = BRIEF_SECTIONS[0]
    fence = None
    for line in body.splitlines():
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
        elif stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
        elif line.startswith("## ") and line[3:].strip() in BRIEF_SECTIONS:
            current = line[3:].strip()
            parts.setdefault(current, [])
            continue
        parts.setdefault(current, []).append(line)
    filled = {h: "\n".join(v).strip() for h, v in parts.items()}
    return {h: v for h, v in filled.items() if v}


def brief_sections(text: str) -> dict:
    """A RENDERED brief's four sections, mapped to the body written under each.

    `split_brief_body` above reads what the lead HANDS IN; this reads what the
    file on disk ended up saying, which is a different question with a
    different rule. Here a `## ` heading of any kind ENDS the section it
    follows -- the scaffold puts `## Reporting back` after the last one, and a
    body that carried its own headings keeps them in place -- while only one of
    BRIEF_SECTIONS opens a new one. Text before the first is the preamble the
    scaffold wrote and belongs to nobody.

    A `## ` inside a fence is quoted markdown, exactly as it is on the way in.
    """
    out: dict[str, list] = {}
    current = None
    fence = None
    for line in text.splitlines():
        stripped = line.strip()
        if fence is not None or stripped.startswith(("```", "~~~")):
            fence = _next_fence(fence, stripped)
        elif line.startswith("## "):
            heading = line[3:].strip()
            current = heading if heading in BRIEF_SECTIONS else None
            if current:
                out.setdefault(current, [])
            continue
        if current is not None:
            out.setdefault(current, []).append(line)
    return {h: "\n".join(v).strip() for h, v in out.items()}


def _next_fence(fence: str | None, stripped: str) -> str | None:
    """The open fence after one line: closed by its own marker, or opened by one."""
    if fence is not None:
        return None if stripped.startswith(fence) else fence
    return stripped[:3]


def unfilled_sections(text: str) -> list:

    """Which sections still hold the scaffold's own text, and nothing else.

    The check this replaces was a substring grep for BRIEF_PLACEHOLDER over the
    whole file, which answers a different question: does this brief MENTION the
    placeholder. A brief about the scaffold mentions it, so the queue could not
    carry a task about its own scaffold -- the brief for the task that fixed
    this had the quotation cut out of it to get dispatched.

    So compare each section against what the scaffold wrote there. A section
    saying anything else is written, including one that quotes the placeholder
    while describing it. This weakens nothing: the placeholder standing alone
    is still exactly what it always was, and every section is still checked.

    A section whose heading is gone is not reported. The scaffold writes all
    four, so a missing one is a lead who restructured the file deliberately,
    and this is a check on the scaffold's text and not on the lead's shape.
    """
    body = brief_sections(text)
    return [h for h in BRIEF_SECTIONS if body.get(h, "").strip() == BRIEF_PLACEHOLDER]


def brief_shortfall(path: str) -> str:
    """Why this BRIEF.md is not something to send a worker, or "" if it is."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return "never written"
    missing = unfilled_sections(text)
    if missing:
        return "still the scaffold's own text under " + ", ".join(missing)
    return ""


# HOW A TASK PUBLISHES, as one word about the ARTIFACT it leaves behind —
# and the ONE place each is written down. `render_brief` writes `brief` into
# the worker's instructions, `publish_verdict` goes and looks for `artifact`,
# and `report_unverified` and `cmd_show` quote `proof` when it does not hold.
#
# WHY A CHECK AT ALL. "Open the pull request by running the pipeline" is an
# instruction about a METHOD, and a method leaves no trace: a worker that
# produced a good-looking PR with a bare `gh pr create` satisfied every visible
# requirement. Two tasks were once collected `shipped` that way and nothing
# noticed until the operator read the bodies himself. Naming the ARTIFACT is
# that requirement restated as something a reader can verify.
#
# WHY ARTIFACT SHAPES AND NOT TOOL NAMES. A tool fleet has never heard of — an
# operator's own `/publish` skill, a repo's `make release` — still ends in a
# pull request or a commit on the base branch, so three of these words cover every
# tool there will ever be while naming none of them. The tool itself rides on
# the record as `publish.how`: free text, rendered into the brief, never
# parsed. That is the whole of fleet's agnosticism, and it lasts exactly as
# long as nothing branches on it.
#
# IT DID NOT LAST. The third shape was called `no-mistakes` — one operator's
# pipeline — for as long as this table has existed, so the code branched on a
# tool name, this repo shipped that tool as its tracked default, and every
# clone inherited a pipeline most of them do not run. It is `attested` now: a
# pull request whose body carries an attestation for the commit that would
# merge. WHAT an attestation looks like is `ATTESTATION_MARKER` in
# `orchestration/publish.conf`, so fleet reads the operator's format rather
# than asking them to emit fleet's.
#
# TWO SHAPES THAT ARE NOT CODE. Ten tasks once posted a review or a comment and
# were declared `push`, "nothing to commit", because nothing else existed. No
# commit URL could ever prove them, so every one was closed by
# `--allow-unverified`. `note` is that deliverable, named: a review or comment
# on the change request or issue the task TARGETS, proven when the forge says
# who wrote it and what it sits on. `none` is the honest answer for what no
# forge holds — a document on somebody's own server, a commit in a repository
# with no remote. It records the URL and checks nothing, and says so; a word
# that claimed to verify a link to somebody's laptop would be a verdict on
# nothing.
#
# THE SIXTH IS A WAIT, NOT A CHECK. `none` also closed five tasks whose
# deliverable was a document SERVED TO A READER, and closed each one the
# instant it was served: nothing was left to wait on, so `reap` deleted the
# session, and every reader who annotated one of those documents and sent it
# back was answered by "No agent is listening right now". The document
# existed; the REVIEW had not started. `served` is `none` plus the single fact
# `none` cannot hold — somebody is expected to ANSWER this — and it verifies
# no more than `none` does, because fleet cannot ask a server it did not start
# whether a reader is finished. What it changes is the LANDING: a served
# document stands `open`, which is the state that already keeps a session, and
# only `fleet queue reviewed` moves it. A person clears it the way a person
# clears a condition blocker, and for the same reason — nothing here can
# observe the event.
#
# IT IS A SHAPE LIKE THE OTHER FIVE. A document served by a review tool, by a
# preview server, by `python -m http.server` in a worktree: one artifact, one
# wait, no tool named.
# New records default to a plain PR. Existing records with an explicit
# `attested` method keep their body check through task_publish and collect.
PUBLISH_DEFAULT = "pr"

# The methods whose artifact is the task's OWN change request. A `note` task's
# URL names a change request too — the one the note sits on — and reading it as
# the task's would make somebody else's pull request look like this task's
# work: to the landing check, and to the shepherd's merge gate.
CHANGE_METHODS = ("attested", "pr")

# The methods whose artifact belongs to a REPOSITORY, and so goes plural when a
# task spans several. `note`, `none` and `served` are not here and that is
# deliberate: a note sits on the one `--target` a task names, `none` names
# nothing fleet checks, and a served document is ONE document however many
# repositories it was written from — none becomes one-per-repository however
# many repositories the worker had open, and inventing a second target for one
# would be fleet making up a deliverable the operator never asked for.
PER_REPO_METHODS = ("attested", "pr", "push")

# Accepted wherever a method is read, so a record written before the rename
# still loads and `--publish no-mistakes` still works. New records say
# `attested`; nothing writes the old word.
PUBLISH_ALIASES = {"no-mistakes": "attested"}


def publish_method(word: str | None) -> str | None:
    """One method word, with the retired spelling folded into its shape."""
    if not word:
        return word
    return PUBLISH_ALIASES.get(word, word)


PUBLISH_METHODS = {
    "attested": {
        "brief": (
            "open a pull request from this task's branch whose body carries an "
            "attestation for the commit that would merge — the Publish line "
            "below names the command that produces one"
        ),
        "artifact": "that pull request's URL",
        "proof": (
            "the pull request is from this task's branch and its body carries "
            "an attestation for the commit that would merge, or it has merged "
            "— so if that command writes no attestation, still report "
            "`shipped` with the pull request, not `stuck`"
        ),
    },
    "pr": {
        "brief": (
            "open a pull request from this task's branch onto its base, by "
            "whatever means this repo uses"
        ),
        "artifact": "that pull request's URL",
        "proof": (
            "the pull request is open or merged and is from this task's branch"
        ),
    },
    "push": {
        "brief": (
            "commit onto the base branch and push it; there is no pull request"
        ),
        "artifact": "that commit's URL, or its full sha when `origin` is on no forge",
        "proof": "the commit is an ancestor of the base branch on `origin`",
    },
    "note": {
        "brief": (
            "post a note — a review or a comment — on this task's Target, as the "
            "account this machine's forge CLI is logged in as; there is nothing "
            "to commit and no pull request to open"
        ),
        "artifact": "that note's URL",
        "proof": (
            "the forge says that note exists, was written by the account fleet "
            "runs as, and sits on this task's target"
        ),
    },
    "served": {
        "brief": (
            "serve the document at a URL its reader can open from their own "
            "machine, and leave it served — a page dies with the machine "
            "serving it. There is nothing to commit and no pull request to open. "
            "Your session is kept up so the reader has somebody to answer them"
        ),
        "artifact": "the URL the document is served at",
        # Quoted in TWO frames — the brief's "`collect` closes this task only
        # once …" and the refusal's "A `served` task is proven when …" — so it
        # is the provable half and nothing else. The WAIT that follows closing
        # is the shape's, not `collect`'s, and `brief` above is where a worker
        # reads it; a `proof` that described the wait told every served worker
        # its task would not close until a person acted.
        "proof": (
            "a URL for the reader to open was recorded, the document behind it "
            "being off any forge and never checked"
        ),
    },
    "none": {
        "brief": (
            "nothing fleet can verify is expected — a document off the forge, "
            "or work with nowhere to publish it"
        ),
        "artifact": "the URL of what you produced, if there is one",
        "proof": (
            "nothing: a `none` task closes on the worker's word, and its URL is "
            "recorded unchecked"
        ),
    },
}

# A pull request URL, and nothing else — `forge.change_url` returns the
# canonical form, so a link someone pasted with `/files` or a `#comment` on the
# end still resolves to the change request it names, and a `/-/merge_requests/`
# one resolves as readily as a `/pull/` one. `not-applicable` and `stuck`
# produce no artifact at all, and an artifact that is not a change request (an
# issue, a doc, a commit) is not a pipeline claim — neither is a failure, and
# neither is checked.
#
# Which forge OWNS a URL, and whether that forge is configured here, are
# separate questions with separate answers — so an artifact on a forge nobody
# configured reads as "could not be checked" and never as "shipped nothing".

# The `push` method's artifact, in the same forge-agnostic shape. Group 1 is
# the canonical link and group 2 the sha, which is the half git is asked about.
COMMIT_URL_RE = re.compile(
    r"^(https?://[^/\s]+/[^/\s]+/[^/\s]+/commit/([0-9a-f]{7,40}))(?:[/?#].*)?$", re.I
)
# The same artifact with no forge to link it on: a local-only fleet pushes to a
# bare repository or a remote no forge owns, and git proves the sha exactly as
# it proves one read out of a URL. Same groups as COMMIT_URL_RE.
COMMIT_SHA_RE = re.compile(r"^(([0-9a-f]{7,40}))$", re.I)

# Standing policy for every worker, tracked beside the otherwise-gitignored
# queue. Anchored to the CHECKOUT, not to FLEET_QUEUE_DIR: it lives with the
# repo and does not move when the queue does.
POLICY_FILE = os.path.join("orchestration", "queue", "POLICY.md")

# The OPERATOR's own standing instructions: the same delivery mechanism as the
# policy and the opposite ownership. Fleet writes POLICY.md and tracks it; the
# operator writes this one and it is gitignored, so a fresh clone has none and
# a brief must then say nothing about it.
#
# Not named CONSTITUTION.md, which is the operator's word for it: thurbox
# already ships a docs/CONSTITUTION.md meaning something else entirely, and a
# worker that reads both repos would have to guess which was meant. Named for
# whose file it is instead, which is the one thing that distinguishes it from
# POLICY.md sitting beside it.
OPERATOR_FILE = "OPERATOR.md"


class QueueError(Exception):
    """Something the operator can fix, reported without a traceback."""


# --- paths -------------------------------------------------------------------


def checkout_root() -> str:
    """The checkout this script belongs to — never the caller's cwd.

    Anchoring on `__file__` rather than on `git rev-parse --show-toplevel` is
    deliberate: rev-parse answers "which checkout is the SHELL in", which is
    the question that produced two queues in the first place. The script's own
    path answers "which checkout is this code in", which is the one that has a
    single right answer no matter where it is invoked from. A git worktree of
    this repo is a different checkout by this rule, and that is correct — it
    has its own orchestration/queue/.
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def is_this_checkout(path) -> bool:
    """Whether `path`, on THIS machine, is this checkout — False when it cannot tell.

    `samefile` and not a string compare: a symlinked home, a trailing slash, a
    drive letter's case or a Windows short name all spell one directory two
    ways. The caller says the path is local first (`session_place`); a path
    from another machine that happens to exist here is not this checkout.
    """
    if not isinstance(path, str) or not path:
        return False
    try:
        return os.path.samefile(path, checkout_root())
    except (OSError, ValueError):
        return False


# The conductor's own directory, inside this checkout: `fleet install-extension`
# renders it for a fleet that runs a conductor, and the session opens it rather
# than the checkout so that it loads its own standing context and not the
# lead's. `extension.toml.in`'s conductor block owns the why.
CONDUCTOR_DIR = os.path.join("orchestration", "conductor")


def is_conductor_dir(path) -> bool:
    """Whether `path`, on THIS machine, is this checkout's conductor directory.

    `is_this_checkout`'s rule, one directory down: the conductor's cwd is the
    one place it differs from the lead's.
    """
    if not isinstance(path, str) or not path:
        return False
    try:
        return os.path.samefile(path, os.path.join(checkout_root(), CONDUCTOR_DIR))
    except (OSError, ValueError):
        return False


# --- the session glyph, which is one setting and one name per KIND ------------
#
# thurbox has no per-session icon field, so a session that wears a mark wears it
# in its NAME. `orchestration/session-glyphs.example.conf` is the one place the
# mark is chosen, `session-glyphs.conf` beside it is the operator's gitignored
# override, and the two modules that read it are this file (every session fleet
# spawns that is not the lead) and `fleet install-extension` (the lead). No line
# of CODE here spells a glyph — one would be a second copy of a setting this
# file does not own — and the comment below spells one only to do the arithmetic
# it is about.
#
# `GLYPHS=off` leaves every name exactly what it was before glyphs existed,
# which is what makes the setting a way back rather than a different mode.

GLYPH_CONF = "orchestration/session-glyphs.conf"
GLYPH_CONF_DEFAULTS = "orchestration/session-glyphs.example.conf"

# thurbox's own cap, and it is BYTES despite what the error says. `session
# create` refuses with "Name too long (max 64 characters)" at 65 bytes, so a
# 61-character title wearing a 5-byte "🚀 " is already over — measured against
# thurbox 2.19.5 rather than assumed. A name that fails at spawn fails the whole
# dispatch, so the truncation below counts the same units thurbox does.
SESSION_NAME_BYTES = 64


def glyph_conf(root: str | None = None) -> dict[str, str]:
    """The glyph setting, from the operator's copy or the tracked defaults.

    Read as DATA — `KEY=value`, no quoting, no continuation — and never
    executed. A setting that can run is a different kind of file.

    `FLEET_GLYPH_ROOT` overrides where that setting is read from, the same way
    `FLEET_QUEUE_DIR` relocates queue state — so a test can dispatch a real
    task without inheriting whatever the developer's own gitignored
    session-glyphs.conf says.
    """
    root = root or os.environ.get("FLEET_GLYPH_ROOT") or checkout_root()
    return read_kv_conf(conf_path(GLYPH_CONF, GLYPH_CONF_DEFAULTS, root))


# --- the operator's settings files, which are all read the same way ----------
#
# One parse for every `orchestration/*.conf`: the operator's copy when it is
# there, the tracked `*.example.conf` beside it when it is not. Read as DATA —
# `KEY=value`, no quoting, no continuation — and none of these files is ever
# executed. One VALUE is a command line, which the module that reads it runs on
# purpose (`reconcile.conf`'s `WORKTREE_RELEASED_CMD`, split and run with no
# shell); this parser gives no meaning to anything in a value and takes the
# rest of the line verbatim, quotes and all.
#
# The tracked copy is where the DEFAULT lives, so every one of these ships a
# working answer and a fresh clone needs no configuration at all. What none of
# them ships is a name: an owner, a repository, a tool or an agent written into
# a tracked file is one operator's setup published in everybody's machinery.


def conf_path(name: str, defaults: str, root: str) -> str:
    """The operator's copy of one conf, or the tracked example beside it."""
    path = os.path.join(root, name)
    return path if os.path.exists(path) else os.path.join(root, defaults)


def read_kv_conf(path: str) -> dict[str, str]:
    """`KEY=value` lines, as data. An unreadable file is no settings at all.

    ONE GRAMMAR for every one of these files, which is why the parse itself is
    `agent_settings.read_conf` — that module reads `agent.conf` before this one
    is called, and a second implementation of the same five lines is how the
    two would come to disagree about a comment or a blank value.
    """
    return agent_settings.read_conf(path)


PUBLISH_CONF = "orchestration/publish.conf"
PUBLISH_CONF_DEFAULTS = "orchestration/publish.example.conf"
AGENT_CONF = agent_settings.AGENT_CONF
AGENT_CONF_DEFAULTS = agent_settings.AGENT_CONF_DEFAULTS


def publish_conf(root: str | None = None) -> dict[str, str]:
    """The publish settings in force. `FLEET_PUBLISH_ROOT` relocates them."""
    root = root or os.environ.get("FLEET_PUBLISH_ROOT") or checkout_root()
    return read_kv_conf(conf_path(PUBLISH_CONF, PUBLISH_CONF_DEFAULTS, root))


RECONCILE_CONF = "orchestration/reconcile.conf"
RECONCILE_CONF_DEFAULTS = "orchestration/reconcile.example.conf"


def reconcile_conf(root: str | None = None) -> dict[str, str]:
    """The reconciler's settings in force. `FLEET_RECONCILE_CONF_ROOT` relocates them.

    The one reader of the file: `reconcile.py` asks this for its clocks, and
    `reap` for what a landing does to a session.
    """
    root = root or os.environ.get("FLEET_RECONCILE_CONF_ROOT") or checkout_root()
    return read_kv_conf(conf_path(RECONCILE_CONF, RECONCILE_CONF_DEFAULTS, root))


# What `reap` does with a finished task's session, per ending. `delete` is what
# it always did. `notify` keeps the session and tells it why, once.
SESSION_ENDINGS = ("delete", "notify")


def session_ending(closed: bool) -> str:
    """`ON_LANDED`, or `ON_CLOSED` for a change request closed unmerged; `delete` unset.

    An unrecognised value comes back as itself, and `reap` keeps the session
    rather than guess: a typo of `notify` is an operator who wanted it kept,
    and the delete cannot be taken back.
    """
    return reconcile_conf().get("ON_CLOSED" if closed else "ON_LANDED", "").strip().lower() or "delete"


def agent_conf(root: str | None = None) -> dict[str, str]:
    """The agent settings in force. `FLEET_AGENT_ROOT` relocates them.

    The dotted `<agent>.KEY` lines come back with everything else; reading one
    OUT of this dict is `agent_settings`' job and never a `.get()` here.
    """
    return agent_settings.conf(root)


def configured_agent() -> str | None:
    """The agent every spawn names, or None to leave thurbox its own default.

    Empty is the shipped answer and is not a gap: `agents.toml` already records
    which agent the operator runs and `session create` already honours it, so a
    name here would be a second copy of that answer.
    """
    return agent_conf().get("AGENT", "").strip() or None


# A KIND of session fleet spawns, and the setting key naming its mark. Three
# kinds and one switch: `dispatch` renders a worker's name in-process, and
# `fleet session-name` (scripts/lib/session_name.py) renders the other two for
# the skills whose `session create` line spawns them — prose, which can read no
# setting of its own. Adding a kind is a row here and a word in the setting;
# it is never a second switch, because the reason to turn a mark off is the
# terminal and the terminal has no opinion about which session it belongs to.
GLYPH_KEYS = {
    "worker": "WORKER_GLYPH_ON",
    "diagnose": "DIAGNOSE_GLYPH_ON",
    "review": "REVIEW_GLYPH_ON",
    # Not spawned by a dispatch or a skill: `fleet install-extension` renders
    # it into the manifest's second `[[sessions]]`, through this map.
    "conductor": "CONDUCTOR_GLYPH_ON",
}


def session_glyph(kind: str, root: str | None = None) -> str:
    """The mark one kind of fleet-spawned session wears, or "" when glyphs are off."""
    if kind not in GLYPH_KEYS:
        raise QueueError(f"no session kind {kind!r} (have: {', '.join(GLYPH_KEYS)})")
    conf = glyph_conf(root)
    setting = conf.get("GLYPHS", "on")
    if setting not in ("on", "off", ""):
        raise QueueError(f"GLYPHS in {GLYPH_CONF} is neither 'on' nor 'off'")
    if setting == "off":
        return ""
    return conf.get(GLYPH_KEYS[kind], "")


def worker_glyph(root: str | None = None) -> str:
    """The mark every worker fleet spawns wears, or "" when glyphs are off."""
    return session_glyph("worker", root)


def missing_glyph_word(kind: str, root: str | None = None) -> str:
    """The setting key this kind's mark needs and the conf in force lacks, or "".

    `conf_path` picks the operator's own `session-glyphs.conf` INSTEAD of the
    tracked example, never merging the two, so a copy made before a kind
    existed answers for that kind with silence: `GLYPHS=on` and no mark, which
    is the one state a reader cannot tell from `off`.

    Asked HERE rather than by the caller so that "are glyphs on" keeps one
    answer. `session_glyph` has already validated `GLYPHS` and already returned
    "" for `off`, so a caller re-deciding it would be a second rule that
    disagrees on a value neither of them expected.
    """
    if session_glyph(kind, root):
        return ""
    return "" if glyph_conf(root).get("GLYPHS", "on") == "off" else GLYPH_KEYS[kind]


def rendered_name(title: str, glyph: str) -> str:
    """The whole name a title renders to, mark and all, before the cap.

    One spelling of "the mark goes in front, with a space": `session_name` cuts
    this, and the callers that must judge what the cut would hide ask it
    directly rather than laying the name out a second time.
    """
    return f"{glyph} {title}" if glyph else title


def session_name(title: str, glyph: str) -> str:
    """A spawned session's name: its title, wearing the mark, within the cap.

    The glyph goes in FRONT and the title is what gets cut, so a run of workers
    is a column of marks with the work beside it. Truncation is by byte and on a
    codepoint boundary — thurbox counts bytes (see SESSION_NAME_BYTES) and a
    name cut through the middle of a character is not a name it accepts either.
    """
    encoded = rendered_name(title, glyph).encode()
    return encoded[:SESSION_NAME_BYTES].decode(errors="ignore")


def unsafe_name(name: str) -> str:
    """Why thurbox would refuse this name, as a clause, or "".

    thurbox's rule MIRRORED, never re-invented and never tightened: a session
    name becomes a path segment there, so `paths::validate_safe_name` refuses
    an empty name, one starting `.`, and one holding `/`, `\\` or `..` — the
    shapes its own `unsafe_names_are_rejected` enumerates, the byte cap aside.
    Everything else it accepts, and so does this: a title is human-facing text
    and narrowing it further would be a defect of its own.

    Takes a NAME rather than a title so that a caller may ask about one the
    cap has not been applied to. `session_name_refusal` below asks about the
    cut name, because that is what `dispatch` hands over; `fleet session-name`
    asks about the whole one, because it refuses the cut instead of making it.
    """
    if not name:
        return "and an empty name is not one it accepts"
    if name.startswith("."):
        return "and a name beginning with '.' is not one it accepts"
    for bad in ("/", "\\", ".."):
        if bad in name:
            return f"and it contains {bad!r}, which thurbox refuses"
    return ""


def safe_name_title(title: str, glyph: str) -> str:
    """The nearest title to this one thurbox WOULD accept, or "".

    A refusal that only says no leaves the operator inventing the working name
    themselves, which is what happened on 2026-09-18: a reviewer titled the way
    this repo identifies a repository everywhere else — host plus path — was
    refused by thurbox, and the name it ran under was typed by hand.

    IT ONLY EVER REMOVES WHAT THURBOX REFUSES — a `/`, a `\\`, the extra dots of
    a `..`, a leading `.` where no mark precedes it — plus redundant
    whitespace, which a removal leaves behind and which costs bytes of its own.
    NO WORD IS EVER DROPPED, and a separator becomes a space rather than
    taking what stands beside it: `CI/CD` is not a path, and a repair that read
    it as one would suggest a title saying something else, while one that kept
    only the last segment of `github.com/owner/repo` would hand two
    repositories of the same name one suggestion — and `--on-existing adopt`
    matches on the name.

    NO WORD IS DROPPED TO FIT THE CAP either, for that same reason. A title
    runs over in what comes LAST, which is where the identity sits —
    `review-prs` titles a reviewer `... on <project>` — so shortening it from
    the end is the collision this whole command exists to prevent. Removing
    what thurbox refuses can bring a title under the cap, and collapsing a run
    of spaces can too; when neither does, there is no suggestion, and the
    person who knows what distinguishes their session shortens it themselves.

    It is a SUGGESTION and not a rewrite: nothing renders it, and the caller
    prints it for a person to accept, edit or ignore. Returns "" rather than a
    guess when what falls out is empty, unchanged, over the cap, or still
    refused — the last asked of `unsafe_name`, so that the suggestion and the
    refusal cannot disagree about thurbox's rule.

    AND "" FOR ANYTHING NOT PRINTABLE, because a suggestion is text somebody
    types back. A title carrying an escape sequence, a zero-width space or a
    control byte cannot be handed over under any quoting: printed as itself it
    colours the terminal or vanishes, and escaped it grows a `\\`, which is one
    of the characters thurbox refuses — so the title typed back off the screen
    is refused in its turn, and suggests the same thing again.
    """
    candidate = re.sub(r"[/\\]", " ", title)
    candidate = re.sub(r"\.{2,}", ".", candidate)
    candidate = " ".join(candidate.split())
    if not glyph:
        # Only where the name starts with it: the mark is what makes a title
        # beginning `.` safe, and dropping the dot under a mark would edit a
        # title thurbox never objected to.
        candidate = candidate.lstrip(".").strip()
    whole = rendered_name(candidate, glyph)
    if (not candidate or candidate == title or not candidate.isprintable()
            or len(whole.encode()) > SESSION_NAME_BYTES or unsafe_name(whole)):
        return ""
    return candidate


def session_name_refusal(title: str, glyph: str) -> str:
    """Why `session create` would refuse this title, asked at `add` time.

    Asked about the RENDERED name and not the raw title, because the rendered
    name is what `dispatch` hands to `session create`: the glyph goes in front,
    so a title starting `.` is unsafe exactly when no mark precedes it, and the
    title is cut to the cap, so one that is merely long never reaches thurbox
    long. `add` took `Rust crate, CI/CD and the profile model`; `dispatch` died
    on it with thurbox's bare exit status, and the repair was a hand-edit of
    `title` in task.yaml, because there is no retitle verb.
    """
    name = session_name(title, glyph)
    why = unsafe_name(name)
    if not why:
        return ""
    return (
        f"--title {title!r} cannot become a session name:\n"
        f"thurbox is asked to create {name!r}, {why}.\n"
        "That name becomes a path there, so it carries no '/', no '\\', no "
        "'..' and no\nleading '.'. The spawn fails with thurbox's own refusal "
        "and the task stays\nqueued.\n"
        "Pass a --title without them and run this again. Nothing was created."
    )


def queue_root() -> str:
    """Where the queue lives, as an absolute path.

    FLEET_QUEUE_DIR is honoured verbatim and never second-guessed — the
    tests and anyone pointing a harness at a temp directory rely on that.
    Otherwise the queue belongs to this CHECKOUT, not to the process cwd.
    """
    return os.environ.get("FLEET_QUEUE_DIR") or os.path.join(
        checkout_root(), "orchestration", "queue"
    )


def policy_path() -> str:
    """The standing worker policy every brief points at, absolute.

    Deliberately not under queue_root(): a queue may be anywhere
    FLEET_QUEUE_DIR names, while the policy is tracked and lives in the
    checkout that ships it.
    """
    return os.path.join(checkout_root(), POLICY_FILE)


_POLICY_PUBLISH_WARNED = False


def policy_publish_block() -> dict | None:
    """POLICY.md's retired `publish:` frontmatter, or None when it has none.

    Tracked file, so a block here is one operator's tool in everybody's copy —
    `publish.conf` is where it belongs. Honoured while it exists because
    dropping it would downgrade a live fleet's verification in silence, and
    said out loud once per process so the move is noticed.
    """
    global _POLICY_PUBLISH_WARNED
    try:
        with open(policy_path(), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) != 3:
        return None
    try:
        doc = load_yaml(parts[1])
    except yaml.YAMLError as exc:
        raise QueueError(f"{policy_path()}: its frontmatter is not YAML: {exc}")
    if not isinstance(doc, dict) or "publish" not in doc:
        return None
    pub = doc["publish"]
    if not isinstance(pub, dict):
        raise QueueError(
            f"{policy_path()}: publish is {pub!r}, and must be a mapping with "
            "method/how, not a bare value"
        )
    if not _POLICY_PUBLISH_WARNED:
        _POLICY_PUBLISH_WARNED = True
        print(
            f"{POLICY_FILE}: its `publish:` frontmatter still decides the default. "
            f"That file is TRACKED, so it ships your tool to every clone — move "
            f"the two values to {PUBLISH_CONF} (see {PUBLISH_CONF_DEFAULTS}) and "
            "delete the block.",
            file=sys.stderr,
        )
    return pub


def policy_publish_default() -> tuple[str, str | None]:
    """The operator's own publish default, from `orchestration/publish.conf`.

        METHOD=attested
        HOW=run `/your-pipeline --yes`

    IT MOVED OUT OF POLICY.md, which is TRACKED. Putting it there made the
    default reviewable in a diff, which was the argument for it — and made this
    repo ship one operator's pipeline as every clone's default, which is the
    argument against and the larger one. `publish.example.conf` is the tracked
    half now: it documents the five artifact shapes, ships `pr`, and names no
    tool. POLICY.md still SAYS how a worker publishes, because every brief
    points at it; it no longer decides for somebody else's fleet.

    A `publish:` block still in POLICY.md's frontmatter is honoured and warned
    about once, so an operator who syncs across the move keeps working while
    they are told where it went. Ignoring it would silently downgrade every
    task's verification, which is the failure this whole subsystem prevents.

    The shipped answer is `pr` with no `how`: a fresh clone needs no
    configuration at all. A word that is not a method is refused rather than
    ignored, for the same reason.
    """
    block = policy_publish_block()
    where = policy_path()
    if block is None:
        conf = publish_conf()
        block = {"method": conf.get("METHOD", ""), "how": conf.get("HOW", "")}
        where = PUBLISH_CONF

    method = publish_method(str(block.get("method") or "").strip()) or PUBLISH_DEFAULT
    if method not in PUBLISH_METHODS:
        raise QueueError(
            f"{where}: publish method is {method!r}, and the methods are "
            + ", ".join(sorted(PUBLISH_METHODS))
        )
    how = str(block.get("how") or "").strip()
    return method, how or None


def operator_path() -> str:
    """The operator's standing instructions, absolute — beside their queue.

    Anchored to queue_root() and deliberately NOT to the checkout, the opposite
    choice from policy_path(). The policy is tracked and ships with the code;
    this is the operator's own working state, gitignored like everything else
    the queue holds, so it belongs wherever their queue is. The tracked
    OPERATOR.example.md that documents the format stays in the checkout, beside
    the default location.
    """
    return os.path.join(queue_root(), OPERATOR_FILE)


def operator_instructions() -> str:
    """What the operator wrote, or "" — absent, empty and unreadable are one.

    A fresh clone has no such file, and a brief that pointed a worker at one
    that is not there would be worse than saying nothing, so "" is the answer
    the scaffold checks and every non-answer collapses into it. It is prose for
    a worker to read, never configuration: nothing here parses it, and the only
    question asked of it is whether there is any.
    """
    try:
        with open(operator_path(), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


# --- which checkout owns the queue -------------------------------------------
#
# A queue belongs to ONE checkout, and this is the question "is that checkout
# this one". A second clone of this repo is the SUPPORTED shape — the control
# plane may have no `origin` of its own, so workers branch and push from a clone
# that does — and that is exactly how the queue silently forked: a `topic add`
# run with the shell in the second clone wrote records the TUI pane was right to
# not show, and nothing said a word.
#
# SEVERAL FLEETS ON ONE MACHINE CHANGE NOTHING HERE, and that is the point.
# Each fleet is a checkout with a Mission Control of its own
# (`orchestration/fleet.example.conf`), and the answer below is read out of THIS
# checkout's own rendered manifest — so fleet B's clone is fleet B's control
# plane and fleet A is not consulted, while a copy of either is still refused.
# This guard is what keeps two fleets from ever writing one queue.
#
# Two ways to recognise the control plane, cheapest first, and both are things
# the install already produced rather than new state this file invents:
#
#   extension.toml   rendered into the control-plane clone by
#                    `fleet install-extension`, with `repo_path` naming it.
#                    Gitignored, so its presence IS the claim. No thurbox needed.
#   the live session thurbox-cli reports the lead session's real `cwd`, which
#                    is the only authority when the clone has moved. Its NAME
#                    comes from the manifest, never from a constant here.
#
# When neither answers — no rendered manifest, no thurbox-cli, no such session —
# the answer is "unknown", and unknown must stay SILENT. A fleet used without
# the extension installed is a legitimate setup and may not be made unusable by
# a guard that cannot tell whether it is even warranted.

def rendered_manifest() -> str:
    """The rendered extension.toml: FLEET_MANIFEST_ROOT's, else this checkout's.

    Gitignored, and the operator's — it names their lead and their conductor —
    so the gate points every reader of it elsewhere, the way it does each
    gitignored *.conf.
    """
    return os.path.join(os.environ.get("FLEET_MANIFEST_ROOT") or checkout_root(), "extension.toml")


def manifest_session(path: str) -> tuple[str | None, str | None]:
    """(session name, repo_path) from the first [[sessions]] table of a manifest.

    Decoded as TOML by `manifest.py`, the one reader of that table: the
    rendered `repo_path` escapes every backslash, so a native Windows path
    matched as text never equalled the checkout it names.
    """
    return _load_sibling("fleet_manifest", "manifest.py").read_lead_session(path)


def manifest_conductor(path: str) -> tuple[str | None, str | None]:
    """(session name, repo_path) of the conductor a manifest declares; (None, None) when none."""
    return _load_sibling("fleet_manifest", "manifest.py").read_conductor_session(path)


def live_session_cwd(name: str) -> str | None:
    """The directory the named thurbox session actually opens, if it is running."""
    try:
        out = subprocess.run(
            ["thurbox-cli", "session", "list", "--json"],
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        sessions = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(sessions, list):
        return None
    for s in sessions:
        if isinstance(s, dict) and s.get("name") == name and s.get("cwd"):
            return str(s["cwd"])
    return None


def control_plane() -> str | None:
    """The checkout that owns the queue, or None when it cannot be told.

    WITH MANY ORIGINS AND ONE CONDUCTOR it is still the LEAD's checkout, and
    still one: the lead is the manifest's first `[[sessions]]`, and it opens
    the checkout itself. The conductor opens `orchestration/conductor` INSIDE
    that checkout, so it names no second one, and its code runs from the same
    files — the guard below agrees with it by construction. An origin is any
    conversation in any directory; it reaches the queue through
    `uv run --project <checkout>`, and this guard is what tells it which
    checkout that is. None of them is asked here: one answer, the lead's.
    """
    here = checkout_root()
    # The tracked extension.toml.in is the fallback source for the session's
    # NAME, so a rename (README's "Renaming" section) reaches this without a
    # second edit.
    name, repo_path = manifest_session(rendered_manifest())
    if repo_path:
        return repo_path.rstrip("/")
    if not name:
        name, _ = manifest_session(os.path.join(here, "extension.toml.in"))
    if not name or "__" in name:
        return None
    cwd = live_session_cwd(name)
    return cwd.rstrip("/") if cwd else None


def foreign_checkout() -> str | None:
    """The control plane's path when THIS checkout is provably not it.

    None means either "this is the control plane" or "nothing here can tell",
    and the two are deliberately indistinguishable to callers: both must be
    silent. Only a positive identification of a different checkout is loud.
    """
    if os.environ.get("FLEET_QUEUE_DIR"):
        return None  # someone named the directory they meant. Honour it, verbatim.
    owner = control_plane()
    if owner and not same_path(owner, checkout_root()):
        return owner
    return None


def guard_creating() -> None:
    """Refuse to open a topic or a task in a checkout that is not the control plane.

    REFUSE here, WARN elsewhere, and the split is the whole policy. Creating a
    record is the only act that can bring a second queue into existence; every
    other command reads or edits records that already exist, and a foreign
    checkout is still a legitimate place to run those from. So the loudest
    response goes exactly where the damage is, and nothing else is taken away.
    """
    owner = foreign_checkout()
    if not owner:
        return
    here = checkout_root()
    raise QueueError(
        "refusing to create queue records outside the control plane.\n"
        f"    this checkout: {here}\n"
        f"    control plane: {owner}\n"
        "  Records written here are invisible to the pane and to the lead.\n"
        "  Open the topic where the queue lives:\n"
        f"      uv run --project {owner} fleet queue ...\n"
        "  or, if you really mean this checkout's queue, name it:\n"
        f"      FLEET_QUEUE_DIR={os.path.join(here, 'orchestration', 'queue')}"
    )


def has_records(root: str) -> bool:
    """Does this directory hold a queue, as opposed to just existing?

    `orchestration/queue/` is present in every checkout — its README is the one
    tracked file in it — so the directory existing proves nothing.
    """
    try:
        names = os.listdir(root)
    except OSError:
        return False
    return any(is_record_dir(n) and os.path.isdir(os.path.join(root, n)) for n in names)


def warn_foreign(root: str) -> None:
    """Say, on stderr, that the queue being read is not the control plane's.

    Only when this checkout already HOLDS records: with none here there is no
    second queue for anything to be confused by, and a warning on every
    `fleet check` run in every worktree is how a warning stops being read.
    """
    owner = foreign_checkout()
    if not owner or not has_records(root):
        return
    print(
        f"queue: warning — {root}\n"
        f"       is not the control plane's queue. The control plane is {owner};\n"
        "       records here are invisible to its pane and to its lead.",
        file=sys.stderr,
    )


def is_record_dir(name: str) -> bool:
    """`_TEMPLATE` and dotfiles are the shipped form and leftovers, not records."""
    return not name.startswith(("_", "."))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp_datetime(stamp) -> datetime | None:
    """A timestamp as a datetime, or None for anything unreadable.

    Two spellings reach the records: this file's own ISO-8601 text, and the
    `at` of a thurbox event, which `watch` copies verbatim and thurbox emits as
    epoch MILLISECONDS — read as text, every folded transition was time 0
    (#251). A bare number above 1e11 is milliseconds, below it seconds.
    Naive text comes back naive, so each caller keeps its own reading of it.
    """
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
        secs = stamp / 1000 if stamp > 1e11 else stamp
        try:
            return datetime.fromtimestamp(secs, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(stamp))
    except ValueError:
        return None


def age_of(stamp) -> str:
    """How long ago, as one short token — or "" for anything unreadable.

    Freshness is part of the fact. `collect` and `shepherd` run on the lead's
    cadence, so a state observed forty minutes ago has to look forty minutes
    old wherever it is drawn, rather than reading as what is true now.
    """
    then = stamp_datetime(stamp)
    if then is None:
        return ""
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    secs = max(0, int((datetime.now(timezone.utc) - then).total_seconds()))
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


# THE LOADER EVERY RECORD IS READ WITH. PyYAML's pure-Python parser took 93% of
# a `fleet queue list` — 1.1 ms per record against 0.13 ms through libyaml,
# measured over 150 records — and the pane probe pays that on every redraw. The
# C loader is the same SafeConstructor over the same resolver, so it answers
# the same document, and a YAMLError with the same `problem` and mark; the
# pure one is the fallback for a Python built without it. Dumping stays with
# `safe_dump`: what reaches the disk must not change by a byte.
YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def load_yaml(stream):
    """`yaml.safe_load`, through the fastest safe loader this interpreter has."""
    return yaml.load(stream, Loader=YAML_LOADER)


def one_line(exc: BaseException) -> str:
    """An exception's message on one line.

    PyYAML's carry the file name twice, a caret and the offending line; the
    caller already names the file, so a marked error is reduced to what was
    wrong and where.
    """
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None)
    if problem and mark is not None:
        return f"{problem} (line {mark.line + 1}, column {mark.column + 1})"
    return " ".join(str(exc).split())


def read_yaml(path: str) -> dict:
    """One record, as a mapping. A file that is not one NAMES ITSELF and stops.

    A QueueError rather than the parser's own exception: every reader opens
    every record, so a task.yaml saved mid-edit ended `list`, `plan` and
    `check` — the command whose whole job is to name a broken record — with a
    traceback that named the file three lines from the bottom.
    """
    return read_record(path)[0]


def read_record(path: str) -> tuple[dict, str]:
    """`read_yaml`, plus the text it was parsed from — what a save compares with."""
    text = fleet_platform.read_record(path)
    return parse_record(path, text), text


def parse_record(path: str, text: str) -> dict:
    try:
        doc = load_yaml(text)
    except yaml.YAMLError as exc:
        raise QueueError(f"{path} is not a record fleet can read: {one_line(exc)}") from exc
    if not isinstance(doc, dict):
        raise QueueError(f"{path}: expected a mapping")
    return doc


def write_yaml(path: str, doc: dict, header: str) -> str:
    """Write one record; returns the text written."""
    text = header.rstrip() + "\n" + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
    fleet_platform.write_record(path, text)
    return text


TASK_HEADER = """\
# A fleet task record. `fleet queue` owns this file's shape and rewrites it,
# so comments you add here do not survive. The instructions belong in BRIEF.md,
# which is yours; this is the index entry.
#
# blocked_by is the ONLY thing that makes this task wait, and every entry names
# a kind from a closed set plus a reason. An entry names EITHER a `task:`,
# which clears when that task lands, OR a `condition:` outside the queue, which
# clears only when somebody runs `block --clear`. Overlapping files are recorded
# under `touches` instead, where they are reported as a risk and hold nothing up.
"""

TOPIC_HEADER = """\
# A fleet topic: one unit of intent, usually several tasks across several
# repos. PROMPT.md beside this file holds the prompt that opened it, verbatim.
"""


# --- records -----------------------------------------------------------------

# The files a topic directory and a task directory hold, each named once.
TOPIC_RECORD = "topic.yaml"
TASK_RECORD = "task.yaml"
PROMPT_FILE = "PROMPT.md"
BRIEF_FILE = "BRIEF.md"
RESULT_FILE = "result.md"
PREVIOUS_RESULT_FILE = "result.previous.md"
PROGRESS_FILE = "progress.jsonl"
# The lock beside a record is the record's own path plus this.
LOCK_SUFFIX = ".lock"

# What a failed command that printed nothing is said to have printed.
NO_OUTPUT = "no output"
NO_THURBOX_CLI = "thurbox-cli not found on PATH"


class Task:
    def __init__(self, topic: str, tid: str, path: str, doc: dict, loaded: str | None = None):
        self.topic = topic
        self.id = tid
        self.path = path
        self.doc = doc
        # The text `doc` was parsed from, or None for a record this view made.
        # A save compares the disk with it to tell whether anybody else wrote.
        self.loaded = loaded
        # A `--host` task's repositories as read ON ITS HOST, once per view —
        # `host_origins` fills it. A reading, never written to the record.
        self.host_origins: dict | None = None

    @property
    def ref(self) -> str:
        return f"{self.topic}/{self.id}"

    @property
    def state(self) -> str:
        return self.doc.get("state", "queued")

    @property
    def touches(self) -> list:
        return self.doc.get("touches") or []

    @property
    def blockers(self) -> list:
        return self.doc.get("blocked_by") or []

    def file(self, name: str) -> str:
        return os.path.join(self.path, name)

    def save(self) -> None:
        """Write this task back, keeping whatever another writer saved since it loaded.

        Every command loads a view, changes a task and saves the whole record,
        and the loop's `collect` runs beside the lead's commands and its own
        `shepherd`. A save of the view as it was loaded wrote the other's
        change away: a `shepherd` that loaded a task while `dispatched` put it
        back to `dispatched` after `collect` had closed it. So the save is a
        transaction under the record's lock — re-read, merge, write — and the
        merge is per top-level field: a field this view changed is this view's,
        and every other field is what the disk holds now. The lock is held
        for those few milliseconds and not across the command, whose forge
        calls can take minutes.
        """
        path = self.file(TASK_RECORD)
        with fleet_platform.exclusive_lock(path + LOCK_SUFFIX, wait=RECORD_LOCK_WAIT):
            doc = self.doc
            if self.loaded is not None:
                try:
                    now_text = fleet_platform.read_record(path)
                except FileNotFoundError:
                    now_text = self.loaded
                if now_text != self.loaded:
                    # A disk copy that no longer parses has nothing to keep.
                    with contextlib.suppress(QueueError):
                        doc = merge_fields(parse_record(path, self.loaded),
                                           parse_record(path, now_text), self.doc)
            text = write_yaml(path, doc, TASK_HEADER)
        self.doc = doc
        self.loaded = text


# A save holds a record's lock for the milliseconds of one read and one write,
# so a writer that waits this long is waiting on a holder that hung.
RECORD_LOCK_WAIT = 30.0


def merge_fields(base: dict, theirs: dict, mine: dict) -> dict:
    """`theirs` with every top-level field `mine` changed from `base` applied over it."""
    out = dict(theirs)
    gone = object()
    for key in [*base, *(k for k in mine if k not in base)]:
        was, now = base.get(key, gone), mine.get(key, gone)
        if was == now:
            continue
        if now is gone:
            out.pop(key, None)
        else:
            out[key] = now
    return out




# --- a task that spans several repositories ----------------------------------
#
# thurbox's `session create` has always taken `--add-dir` (a directory attached
# exactly as it is) and `--add-repo PATH[@BASE]` (a second repository, its own
# worktree, on the SAME `--worktree-branch`). The queue reached for neither, so
# a task could name one repository and `.agents/skills/thurbox-session/` taught
# the lead a capability the queue could not express.
#
# THE TWO ARE DIFFERENT WEIGHTS, and that is the whole shape of this. An
# `--add-dir` is READ: it stops at `spawn_commands` and nothing below it —
# verification, landing, reaping, the shepherd — has anything to say about one.
# An `--add-repo` is COMMITTED IN, and a commit fleet does not verify is the
# exact failure verification exists to stop. So the artifact model goes plural
# with it: one artifact per repository, each with its own verdict.
#
# ONE PUBLISH METHOD FOR THE WHOLE TASK. A task is one unit of intent, and
# `--publish attested` means every repository it touches leaves an attested
# change request behind. There are no per-repository methods.
#
# BOTH SHAPES OF `artifact:` LOAD, the way the retired `no-mistakes` method is
# still read as `attested`. A scalar is ONE artifact, the primary repository's
# — which is what every record written before this carries, and what a
# single-repo task still writes, so those records load, list, verify and reap
# unchanged. A list is the plural shape and nothing writes one for a task with
# one repository.


def split_add_repo(spec: str) -> tuple[str, str]:
    """(path, base) for one `--add-repo` value — `PATH` or `PATH@BASE`.

    thurbox owns that syntax and gets the operator's string VERBATIM but for
    its base, which a dispatch fetches and `spawn_commands` spells out as
    `origin/<base>` — the local one may be days stale. This split is fleet's
    own reading of it, for the two questions fleet has to answer about
    the repository itself: which checkout is it, and what does its worktree
    branch off. No `@` means the task's own `--base`, which is thurbox's
    default too.
    """
    path, sep, base = spec.rpartition("@")
    return (path, base) if sep and path else (spec, "")


def task_repos(task) -> list[dict]:
    """Every repository this task spans: {path, base, spec, primary}.

    The primary first, then each `--add-repo` in the order the operator gave
    it. A task that names none returns exactly ONE entry, which is what keeps
    every reader below a single code path rather than two.
    """
    d = task.doc
    primary = {
        "path": str(d.get("repo") or ""),
        "base": str(d.get("base") or "main"),
        "spec": str(d.get("repo") or ""),
        "primary": True,
    }
    repos = [primary]
    for spec in d.get("add_repos") or []:
        path, base = split_add_repo(str(spec))
        repos.append(
            {"path": path, "base": base or primary["base"], "spec": str(spec), "primary": False}
        )
    return repos


def same_path(a: str, b: str) -> bool:
    """Whether two written paths name the same checkout — as text, and no more.

    Nothing here touches the filesystem: a task's repositories may be on
    another machine (`--host`), and a check that resolved them would be
    answering about this one. `os.path.normcase` is the standard library's own
    answer to "is this OS case-sensitive", so this grows no second branch of
    fleet's own about which OS it is on.
    """
    return os.path.normcase(os.path.normpath(a or ".")) == os.path.normcase(
        os.path.normpath(b or ".")
    )


def artifact_entries(value, primary: str) -> list[dict]:
    """`artifact:` in EITHER shape, as [{repo, url}].

    A scalar — a URL, or nothing at all — is one artifact, the primary
    repository's. A list is the plural shape, one mapping per repository.
    Reading both is what lets a record written before a task could span
    repositories keep loading, showing, verifying and reaping as it did.
    """
    if not isinstance(value, list):
        return [{"repo": primary, "url": value}]
    out = []
    for item in value:
        if isinstance(item, dict):
            out.append({"repo": str(item.get("repo") or primary), "url": item.get("url")})
        else:
            out.append({"repo": primary, "url": item})
    return out


def recorded_artifacts(task) -> list[dict]:
    """What this task's RECORD claims it left behind, one entry per repository."""
    return artifact_entries(task.doc.get("artifact"), str(task.doc.get("repo") or ""))


def artifact_repos(task) -> list[dict]:
    """The repositories an artifact is expected FOR.

    Every repository the task spans for the methods whose artifact belongs to
    one (PER_REPO_METHODS); the primary alone for `note`, `none` and `served`,
    which are left single deliberately and said out loud rather than faked.
    """
    repos = task_repos(task)
    return repos if task_publish(task)[0] in PER_REPO_METHODS else repos[:1]


def artifact_text(task) -> str:
    """Every artifact this task recorded, as ONE line for a log or a table cell.

    A task with one repository is exactly its URL, so nothing a reader has ever
    seen changes; a task that spans them names each, space-separated, rather
    than printing the list the record holds.
    """
    return " ".join(str(a["url"]) for a in recorded_artifacts(task) if a["url"])


def artifact_value(rows: list[dict]):
    """What goes onto the record as `artifact:`.

    A SCALAR for a task with one repository — the shape every reader of a
    record already speaks, from the TUI pane to `fleet status` to every record
    written before this existed. A list of {repo, url} only for a task that
    actually spans repositories, which is the one case anything has to learn a
    second shape for.
    """
    if len(rows) == 1:
        return rows[0]["url"]
    return [{"repo": r["repo"], "url": r["url"]} for r in rows]


# What a Queue loads. The default is the operator's default view, and it is
# the reason `archived` is worth having at all: a topic marked archived costs
# ONE read of its topic.yaml and its task directories are never opened, so
# twenty-two finished topics stop being twenty-seven file reads and twenty-two
# screenfuls on every list, every status line and every pane refresh.
SCOPES = ("live", "archived", "all")


class Queue:
    def __init__(self, root: str, scope: str = "live"):
        self.root = root
        self.scope = scope
        self.topics: dict[str, dict] = {}
        self.tasks: dict[str, Task] = {}
        # Topics this view deliberately did not open, so every reader can say
        # how many it is hiding. A queue that merely LOOKS small is worse than
        # one that looks long: the operator has to be able to tell the
        # difference between "nothing is queued" and "you cannot see it".
        self.archived_hidden: list[str] = []
        # Tasks fetched by ref out of a topic this view skipped — `show` and a
        # blocker that names an archived task. Kept apart from `self.tasks` so
        # a targeted fetch can never leak into a listing.
        self._fetched: dict[str, Task] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.isdir(self.root):
            return
        for topic in sorted(os.listdir(self.root)):
            tpath = os.path.join(self.root, topic)
            if not os.path.isdir(tpath) or not is_record_dir(topic):
                continue
            meta = os.path.join(tpath, TOPIC_RECORD)
            if not os.path.exists(meta):
                continue
            doc = read_yaml(meta)
            archived = bool(doc.get("archived"))
            if archived and self.scope == "live":
                self.archived_hidden.append(topic)
                continue
            if not archived and self.scope == "archived":
                continue
            self.topics[topic] = doc
            self._load_tasks(topic, tpath)

    def _load_tasks(self, topic: str, tpath: str) -> None:
        """Every task record under one loaded topic."""
        for tid in sorted(os.listdir(tpath)):
            dpath = os.path.join(tpath, tid)
            if not os.path.isdir(dpath) or not is_record_dir(tid):
                continue
            rec = os.path.join(dpath, TASK_RECORD)
            if os.path.exists(rec):
                t = Task(topic, tid, dpath, *read_record(rec))
                self.tasks[t.ref] = t

    def get(self, ref: str) -> Task:
        # A blocker entry a hand left without its `task:` key asks for None.
        # That is "no such task", and it has to read as one: a TypeError out of
        # `_fetch` below was the whole of `list` and `plan` for that queue.
        if not isinstance(ref, str) or not ref:
            raise QueueError(f"no such task: {ref!r}")
        if ref in self.tasks:
            return self.tasks[ref]
        # A bare task id is unambiguous when only one topic carries it.
        hits = [t for t in self.tasks.values() if t.id == ref]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise QueueError(
                f"{ref} names {len(hits)} tasks across topics; use <topic>/<task>"
            )
        fetched = self._fetch(ref)
        if fetched:
            return fetched
        # The ref the lead meant is usually one typo or one doubled ordinal
        # away, so the miss names it rather than leaving it to `list`.
        # A bare id is compared with ids and a full ref with refs, so the
        # topic's own length never decides how close a typo is.
        key = (lambda r, t: r) if "/" in ref else (lambda r, t: t.id)
        hits = difflib.get_close_matches(
            ref, {key(r, t) for r, t in self.tasks.items()}, n=3, cutoff=0.6
        )
        close = [r for h in hits for r, t in sorted(self.tasks.items()) if key(r, t) == h]
        raise QueueError(
            f"no such task: {ref}" + (f" (did you mean {', '.join(close)}?)" if close else "")
        )

    def _fetch(self, ref: str) -> Task | None:
        """One task out of a topic this view skipped, read on demand.

        This is the "fetch if really useful" half of archiving, and it is what
        keeps the filter from being a lie in two places. `show <ref>` reaches
        an archived task without unarchiving anything; and a live task BLOCKED
        on one whose topic has since been archived still sees that it landed —
        without it, that blocker would read as uncleared forever and the
        dependent task would wait on work that is already on main.

        At most one directory listing and one task.yaml per miss, and only on a
        ref the loaded set could not answer.
        """
        if ref in self._fetched:
            return self._fetched[ref]
        found = []
        for topic, tid in self._fetch_candidates(ref):
            if not (is_record_dir(topic) and is_record_dir(tid)):
                continue
            dpath = os.path.join(self.root, topic, tid)
            rec = os.path.join(dpath, TASK_RECORD)
            if not os.path.exists(rec):
                continue
            found.append(Task(topic, tid, dpath, *read_record(rec)))
        if len(found) > 1:
            raise QueueError(
                f"{ref} names {len(found)} tasks across topics; use <topic>/<task>"
            )
        if found:
            task = found[0]
            self._fetched[ref] = task
            return task
        return None

    def _fetch_candidates(self, ref: str) -> list:
        """(topic, id) pairs `_fetch` may find `ref` under."""
        candidates = []
        if "/" in ref:
            candidates.append(tuple(ref.split("/", 1)))
        else:
            for topic in self.archived_hidden:
                tpath = os.path.join(self.root, topic)
                if os.path.isdir(os.path.join(tpath, ref)):
                    candidates.append((topic, ref))
        return candidates

    def by_topic(self) -> dict:
        out: dict[str, list] = {t: [] for t in self.topics}
        for t in self.tasks.values():
            out.setdefault(t.topic, []).append(t)
        for v in out.values():
            v.sort(key=lambda t: t.id)
        return out

    # The whole ordering rule, in one method. A task waits only for a recorded
    # blocker, and only until that blocker's task has LANDED — concluded AND
    # its artifact on main. A session that stopped does not release it, an
    # abandoned task does not, and neither does `done`: a task collected
    # `shipped` once cleared its dependents while its pull request was still
    # open and unreviewed, and the lead had to hold them by hand.
    def is_ready(self, task: Task) -> bool:
        if task.state != "queued":
            return False
        return all(self.blocker_cleared(b) for b in task.blockers)

    def blocker_cleared(self, blocker: dict) -> bool:
        # A CONDITION NEVER CLEARS ITSELF. There is no upstream to observe, so
        # there is no event this could read, and inventing one — a timer, a
        # later dispatch that happened to work — would release a task on a
        # guess. `block --clear` is the only release, and it is a person
        # saying the thing is no longer true.
        if blocker_condition(blocker):
            return False
        try:
            return self.get(blocker.get("task")).state == "landed"
        except QueueError:
            return False

    def ready(self) -> list:
        return [t for t in sorted(self.tasks.values(), key=lambda t: t.ref) if self.is_ready(t)]

    def waiting(self) -> list:
        return [
            t
            for t in sorted(self.tasks.values(), key=lambda t: t.ref)
            if t.state == "queued" and not self.is_ready(t)
        ]

    # Overlap between tasks that are ALL going out together. Reported so the
    # operator can see the risk they are accepting, and never acted on.
    def overlaps(self, tasks: list) -> list:
        seen: dict[str, list] = {}
        for t in tasks:
            for path in t.touches:
                seen.setdefault(path, []).append(t.ref)
        return [
            {"touches": p, "tasks": refs} for p, refs in sorted(seen.items()) if len(refs) > 1
        ]


# --- what a record MEANS, in one place ---------------------------------------
#
# `fleet queue list`, `fleet queue plan`, `fleet status` and the TUI pane all show
# these three readings, and each used to derive its own. That is how the queue
# came to print `landed` with a "held by" line under it, a blocker on an
# `abandoned` upstream as though a merge were still coming, and `abandoned`
# beside `shipped` as though the state and the outcome agreed: four surfaces,
# four answers, none of them obviously wrong on its own.
#
# Deriving here means the four can differ in LAYOUT and never in what they
# claim. Nothing below writes: a contradiction in the records is history, and
# the fix for one is always in what is said about it.


def blocker_view(q: Queue, task: Task, blocker: dict) -> dict:
    """One blocker, plus the one word for what it is doing to this task.

        moot         the blocked task itself concluded; this holds nothing
        cleared      the upstream landed
        unclearable  the upstream can never land, so there is no release path
        unknown      the upstream is not in this queue at all
        holding      the ordinary case — a merge that can still come
        outside      a CONDITION, which no event releases — only `block --clear`

    `cleared` is kept as a field of its own because `is_ready` asks exactly
    that question and nothing else. `outside` is the one status that is never
    `cleared`: the record exists for exactly as long as the condition is true,
    and removing it is how it stops being true.
    """
    condition = blocker_condition(blocker)
    ref = None if condition else blocker.get("task")
    try:
        upstream = None if condition else q.get(ref).state
    except QueueError:
        upstream = None

    if task.state in CONCLUDED_STATES:
        status = "moot"
    elif condition:
        status = "outside"
    elif upstream is None:
        status = "unknown"
    elif upstream == "landed":
        status = "cleared"
    elif upstream in UNLANDABLE_STATES:
        status = "unclearable"
    else:
        status = "holding"

    view = {
        "task": ref,
        "condition": condition or None,
        "kind": blocker.get("kind"),
        "why": blocker.get("why"),
        "upstream_state": upstream,
        "status": status,
        "cleared": status == "cleared",
    }
    view["line"] = blocker_line(view)
    return view


def blocker_line(view: dict) -> str:
    """The one sentence every surface prints for a blocker.

    The upstream's STATE rides along in all but the moot line, because "held by
    X" and "held by X, which is abandoned" are the difference between a wait
    and a dead end, and only the second one tells the reader to go and do
    something about it.

    A CONDITION'S LINE SAYS WHO RELEASES IT, for the same reason. There is no
    upstream state to carry and no merge coming, so the fact a reader needs in
    its place is that nothing here is going to change on its own.
    """
    why = view["why"] or "no reason recorded"
    condition = view.get("condition")
    if condition:
        if view["status"] == "moot":
            return (
                f"was held by {view['kind']} outside the queue ({condition}); "
                "this task concluded, so it holds nothing"
            )
        return (
            f"held by {view['kind']} outside the queue ({condition}) — only "
            f"`block --clear` releases it: {why}"
        )
    what = f"{view['kind']} on {view['task']}"
    if view["status"] == "moot":
        return f"was held by {what}; this task concluded, so it holds nothing"
    if view["status"] == "cleared":
        return f"cleared: {what} has landed"
    if view["status"] == "unknown" and not view["task"]:
        # An entry that names NEITHER — a hand edit that lost the key. Nothing
        # can ever clear it, and `check` is what names it as the defect it is.
        return (
            f"UNCLEARABLE: a blocker ({view['kind']}) that names neither a task nor "
            f"a condition: {why}"
        )
    if view["status"] == "unknown":
        return f"UNCLEARABLE: {what}, which is not in this queue: {why}"
    if view["status"] == "unclearable":
        return (
            f"UNCLEARABLE: {what}, which is {view['upstream_state']} and can "
            f"never land: {why}"
        )
    return f"held by {what} ({view['upstream_state']}): {why}"


def state_conflict(task: Task) -> str | None:
    """The task's own two recorded facts, when they disagree — else None.

    Both are facts and neither is edited away: `abandoned` is the forge saying
    the pull request was closed unmerged, `shipped` is the worker saying it
    opened one. The line names the disagreement and, when `reap` recorded why,
    what the forge actually answered.
    """
    outcome = task.doc.get("outcome")
    if not outcome:
        return None
    # Given up on — by hand, or retired in the forge's or thurbox's own words —
    # the recorded reason IS the explanation for the two disagreeing, so there
    # is nothing contradictory left to flag.
    if task.state == "abandoned" and task.doc.get("abandoned"):
        return None
    agree = OUTCOME_STATES.get(outcome)
    if agree is None:
        return f"outcome {outcome!r} is not one of: {', '.join(sorted(OUTCOMES))}"
    if task.state in agree:
        return None
    note = f"state {task.state} disagrees with outcome {outcome}"
    detail = (task.doc.get("landing") or {}).get("detail")
    return f"{note} — {detail}" if detail else note


def dispatch_gap(q: Queue, task: Task) -> str | None:
    """A task nothing is holding and nothing is running.

    A FACT and not a countdown: this says a session was never attached, never
    how long ago one should have been. `fleet queue` has no clock in its output
    and this does not give it one.
    """
    if task.state != "queued" or task.doc.get("session"):
        return None
    return "no session dispatched" if q.is_ready(task) else None


def task_notes(q: Queue, task: Task) -> list:
    """Everything a one-line task row cannot say, in the order it matters.

    The blockers here are the ACTIVE ones only: a moot or cleared blocker is
    part of the record and not part of what is happening, and printing it under
    a task that concluded is the contradiction this whole section exists for.
    """
    notes = []
    given_up = task.doc.get("abandoned") or {}
    if task.state == "abandoned" and given_up.get("why"):
        notes.append(f"{ABANDON_HOW.get(abandon_how(task), abandon_how(task))}: {given_up['why']}")
    conflict = state_conflict(task)
    if conflict:
        notes.append(f"! {conflict}")
    gap = dispatch_gap(q, task)
    if gap:
        notes.append(gap)
    notes += [
        v["line"]
        for v in (blocker_view(q, task, b) for b in task.blockers)
        if v["status"] not in ("moot", "cleared")
    ]
    return notes


# --- archiving: a flag on the topic, and a filter in every reader ------------
#
# The queue reached 24 topics with 27 of its 30 tasks `landed`, and the two
# topics with live work were buried under twenty-two finished ones in both
# readers. Archiving is the operator's "only fetch if really useful", spelled
# as a flag and a filter and NOTHING else: no record is moved, deleted or
# rewritten. This queue is gitignored and the repo does not back it up, so
# anything archiving moved would be gone.
#
# WHY THE FLAG LIVES ON topic.yaml. It is the file that already holds `slug`,
# `title` and `created`, and it is the one file every reader opens per topic
# anyway — the TUI pane's shell probe already `sed`s it for the title and
# never opens a task directory. So skipping an archived topic costs one more
# `sed` there and one more key lookup everywhere else. A design that had to
# read every `task.yaml` to decide what to hide would have made the default
# view more expensive, not less, which is the opposite of the ask.
#
# WHY IT IS DERIVED BUT STORED. Everything else a reader shows about a topic is
# derived on the spot (the pane's `classify`), and this could have been too.
# It is not, because deriving it is exactly the read the flag exists to avoid.
# The cost of storing it is that it can go stale — which is what `add` clearing
# it below is for, and what makes `unfinished()` the single predicate.


def topic_meta_path(root: str, slug: str) -> str:
    return os.path.join(root, slug, TOPIC_RECORD)


def unfinished(tasks: list, state_of=None) -> Task | None:
    """The first task that is not finished, or None when every one of them is.

    THE one predicate. The automatic sweep, `cmd_archive`, `cmd_check` and
    `collect`'s reopening of an archived topic all ask this and nothing else,
    because four implementations of "is this topic done" is four chances to
    hide a topic somebody is still working in.
    Returns the task rather than a bool so a refusal can name it.

    `state_of` is how the sweep asks the question during a dry run, where the
    state on disk is still the one the landing sweep did not write. It is the
    only thing that varies between the callers, so it is the only thing passed
    in — the rule itself has one spelling.
    """
    read = state_of or (lambda t: t.state)
    for task in sorted(tasks, key=lambda t: t.ref):
        if read(task) not in TERMINAL_STATES:
            return task
    return None


def set_archived(root: str, slug: str, at: str | None) -> None:
    """Write or clear `archived` on one topic, leaving the rest of it alone."""
    path = topic_meta_path(root, slug)
    with fleet_platform.exclusive_lock(path + LOCK_SUFFIX, wait=RECORD_LOCK_WAIT):
        doc = read_yaml(path)
        if at is None:
            doc.pop("archived", None)
        else:
            doc["archived"] = at
        write_yaml(path, doc, TOPIC_HEADER)


def sweep_archives(q: Queue, state_of, dry: bool) -> int:
    """Archive every loaded topic whose tasks have all reached a terminal state.

    Run by the pass that MOVES a task into one — `reap`'s landing sweep, which
    `collect` ends by running — because that is the only moment a topic can
    become finished. Nothing else has to remember to do it, which is the same
    reason `reap` itself is wired into `collect`: a step only a human runs is a
    step that does not run.

    `state_of` projects a dry run's answer forward the way `reap` does, so
    `reap --dry-run` reports the archive it would write instead of reporting
    the topic as still live.
    """
    archived = 0
    for slug, tasks in sorted(q.by_topic().items()):
        if not tasks or q.topics.get(slug, {}).get("archived"):
            continue
        if unfinished(tasks, state_of):
            continue
        archived += 1
        word = "would archive" if dry else "archived"
        print(f"    {slug:<46} {word:<10} {len(tasks)} task(s) landed or abandoned")
        if not dry:
            set_archived(q.root, slug, now())
    if archived and not dry:
        print("      `fleet queue list --archived` still shows them; `show <ref>` still reaches them.")
    return archived


def cmd_archive(args) -> int:
    # `all`, because the topic being archived is by definition one the default
    # view is about to stop loading — and because `archive` on an
    # already-archived topic should say so rather than say it does not exist.
    root = queue_root()
    q = Queue(root, scope="all")
    if args.topic not in q.topics:
        raise QueueError(f"no such topic: {args.topic}")
    if q.topics[args.topic].get("archived"):
        print(f"{args.topic} is already archived")
        return 0
    held = unfinished(q.by_topic().get(args.topic) or [])
    if held:
        raise QueueError(
            f"{args.topic} is not finished: {held.ref} is `{held.state}`.\n"
            "       Archiving hides a topic from every default view, so a topic "
            "holding live\n"
            "       work is never archived — `stuck` and `failed` included: those "
            "sessions are\n"
            "       kept as evidence and somebody has to see them."
        )
    set_archived(root, args.topic, now())
    print(f"{args.topic} archived")
    return 0


def cmd_unarchive(args) -> int:
    root = queue_root()
    q = Queue(root, scope="all")
    if args.topic not in q.topics:
        raise QueueError(f"no such topic: {args.topic}")
    if not q.topics[args.topic].get("archived"):
        print(f"{args.topic} is not archived")
        return 0
    set_archived(root, args.topic, None)
    print(f"{args.topic} unarchived")
    return 0


# --- commands ----------------------------------------------------------------


# WHO ASKED. Free text — a name, a chat handle — because fleet has no identity
# of its own to check it against, and recorded so that a Mission Control fronted
# by a chat channel can start work "under this person". It is recorded and
# shown, and NOTHING keys on it: no permission, no routing of a notice. A value
# is one line of display text, so anything a line-per-record reader splits on,
# or a terminal acts on, is refused rather than flattened into somebody's name.
REQUESTER_MAX = 80


def unprintable(value: str) -> bool:
    """A control character or a line or paragraph separator: what no one-line view can draw.

    Wider than a newline and a tab because every reader here splits on lines,
    and Python's `splitlines()` splits on `\\v`, `\\x85` and `\\u2028` too, while
    an escape sequence is printed raw into whoever's terminal lists the queue.
    The bidi marks and overrides draw as nothing and make the rest of a line
    read as other text — the same set context.py's CONTROL_RE strips from a peer.
    """
    return any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") or ch in BIDI_CONTROLS for ch in value)


BIDI_CONTROLS = frozenset("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def requester_value(text: str | None) -> str | None:
    """`--by`, trimmed; None when it names nobody. Refuses a value no view could draw."""
    if text is None:
        return None
    value = text.strip()
    if not value:
        return None
    if unprintable(value):
        raise QueueError("--by is one line of text: it may not hold a newline, a tab or any other control character")
    if len(value) > REQUESTER_MAX:
        raise QueueError(f"--by is {len(value)} characters; a requester is at most {REQUESTER_MAX}")
    return value


def requester_problem(value) -> str:
    """Why a recorded `requested_by` is not one, or "" — absent is valid and means not recorded."""
    if value is None:
        return ""
    if not isinstance(value, str) or not value.strip():
        return f"requested_by {value!r} is not a name"
    if unprintable(value) or len(value.strip()) > REQUESTER_MAX:
        return f"requested_by {value!r} is not one short line"
    return ""


def requester(value) -> str | None:
    """A record's `requested_by` as every view shows it: trimmed, or None.

    The one reader, so a hand-edited value no check would pass — a list, a
    date YAML parsed, an escape sequence — is "not recorded" everywhere rather
    than drawn, compared or serialized as it came; `check` is what names it.
    """
    return None if value is None or requester_problem(value) else value.strip()


def cmd_topic_add(args) -> int:
    root = queue_root()
    if not SLUG_RE.match(args.slug):
        raise QueueError(f"{args.slug!r} is not a slug (lowercase, digits, hyphens)")
    path = os.path.join(root, args.slug)
    if os.path.exists(path):
        raise QueueError(f"topic {args.slug} already exists at {path}")

    prompt = args.prompt
    if args.prompt_file:
        prompt = sys.stdin.read() if args.prompt_file == "-" else open(args.prompt_file, encoding="utf-8").read()
    if not prompt:
        raise QueueError("a topic needs the prompt that opened it: --prompt or --prompt-file")
    by = requester_value(args.by)

    meta = {
        "slug": args.slug,
        "title": args.title or args.slug.replace("-", " "),
        "created": now(),
    }
    # Written only when somebody was named, so a topic nobody attributed has
    # exactly the shape every topic had before the field existed.
    if by:
        meta["requested_by"] = by
    os.makedirs(path)
    write_yaml(os.path.join(path, TOPIC_RECORD), meta, TOPIC_HEADER)
    fleet_platform.write_record(os.path.join(path, PROMPT_FILE), prompt.rstrip() + "\n")

    # Opening a topic is where a run begins, so it is where its log begins —
    # nobody has to decide to make one. On stderr because stdout is the VALUE
    # here: `topic="$(uv run fleet queue topic add ...)"` still gets a bare slug.
    log, note = refresh_run_log(Queue(root), args.slug)
    print(f"run log {note or 'ready'}: {log}", file=sys.stderr)

    print(args.slug)
    return 0


def next_number(tpath: str) -> str:
    used = [
        int(n[:2])
        for n in os.listdir(tpath)
        if os.path.isdir(os.path.join(tpath, n)) and is_record_dir(n) and n[:2].isdigit()
    ]
    return f"{max(used) + 1 if used else 1:02d}"


def branch_refusal(repo: str, branch: str, base: str, host: str | None) -> str:
    """Why `session create` would fail on this branch, asked at `add` time.

    `--worktree-branch X` only ever CREATES X (see `branch_checkout` below,
    which exists for the same reason), so a branch that is already there fails
    the spawn with thurbox's own non-zero exit. The task then stays `queued`
    and the operator hand-edits task.yaml and dispatches again -- the whole
    cost of learning at dispatch what `add` was told.

    branch == base is the instance the operator hit, and it is answered from
    the arguments alone: a base branch exists by definition, so no worktree can
    ever be cut for a task whose branch IS it. Every other existing branch --
    a re-used name, one left behind by an earlier run, one carrying commits
    base has not got -- is the same failure and needs the repo to see. A repo
    this machine cannot read, which is every `--host` task's, is left to
    dispatch exactly as before.
    """
    if branch == base:
        return (
            f"--branch and --base are both {branch!r}, and no worktree can be "
            "cut there:\n"
            "thurbox's --worktree-branch only ever CREATES a branch, and the "
            "base of a\n"
            "task exists by definition. The spawn fails and the task stays "
            "queued.\n"
            "Name the branch the work goes ON, off the branch it starts from."
        )
    if host or not os.path.isdir(repo):
        return ""
    if not git_out(repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]):
        return ""
    return (
        f"--branch {branch} already exists in {repo}, so no worktree can be "
        "cut for it:\n"
        "thurbox's --worktree-branch only ever CREATES a branch. The spawn "
        "fails with\n"
        f"`a branch named '{branch}' already exists` and the task stays "
        "queued.\n"
        "Name a branch that is not there yet, or delete that one first."
    )


def target_refusal(method: str, target: str | None) -> str:
    """Why `add` refuses this method with this target, or "" when it does not.

    REFUSED, NOT WARNED, AND ON STRUCTURE, NOT ON `--how`. Ten tasks were once
    declared `push` with a `how` saying "nothing to commit", and a check that
    read that sentence would be fleet parsing the one field whose whole contract
    is that nothing parses it. What CAN be read is the shape: a note with
    nothing to sit on, a push with a change request to work on, a pull request
    aimed at an issue. Each is a task that cannot be verified as declared, and
    a warning would be scrolled past exactly the way the workaround was.
    """
    if not target:
        if method == "note":
            return (
                "--publish note needs --target: a note is proven by sitting on the "
                "change request or issue it was asked for, and a task that names "
                "none leaves nothing to check it against. Add `--target <its URL>`."
            )
        return ""
    if not forge.TARGET_URL_RE.match(target):
        return (
            f"--target {target!r} is not a change request or issue URL "
            "(…/pull/<n>, …/-/merge_requests/<n>, …/issues/<n>)"
        )
    if method in ("push", "none", "served"):
        return (
            f"--publish {method} has no use for --target {target}: nothing a "
            f"`{method}` task leaves behind is checked against one. A task that "
            "reviews or comments on it is `--publish note`; one that pushes to its "
            "branch is `--publish pr` or `attested`."
        )
    if method in CHANGE_METHODS and not forge.change_url(target):
        return (
            f"--publish {method} is proven by a pull request, and {target} is an "
            "issue. A task whose deliverable is a comment on it is `--publish note`."
        )
    return ""


def cmd_add(args) -> int:
    root = queue_root()
    tpath = os.path.join(root, args.topic)
    if not os.path.isdir(tpath):
        raise QueueError(f"no such topic: {args.topic} (open one with `topic add`)")
    slug, tid, path = _new_task_path(args, tpath)

    add_dirs = [d.strip() for d in (args.add_dir or []) if d.strip()]
    add_repos = [r.strip() for r in (args.add_repo or []) if r.strip()]
    _refuse_unplaceable(args, add_repos)

    # And one layer down again: the title becomes the worker's session NAME,
    # and thurbox refuses a name it could not make a path segment of. Asked
    # here for the branch's own reason, only harder — a title that gets past
    # `add` is repaired by hand-editing task.yaml and the brief's H1, because
    # nothing here retitles a task.
    title = _task_title(args, slug)

    # Refuse an explicit --agent that contradicts the operator's policy before
    # the task record exists. The same check runs again at dispatch, because a
    # rule may appear between add and dispatch.
    if args.agent:
        _refuse_agent(args, add_repos)

    method, how, target = _add_publish(args)
    # The task's own `--by`, else its topic's — RESOLVED HERE and stored, so a
    # later edit to topic.yaml does not rewrite who asked for work already
    # under way. None is "not recorded", which every older record also reads as.
    topic_meta = read_yaml(os.path.join(tpath, TOPIC_RECORD))
    requested_by = requester_value(args.by) or requester(topic_meta.get("requested_by"))

    doc = {
        "id": tid,
        "topic": args.topic,
        "title": title,
        "state": "queued",
        "repo": args.repo,
        # None is a local task, and every path below treats it as today's
        # behaviour verbatim. A name here moves the worker — agent, tmux window
        # and worktree — to that machine, and `repo` above is then a path there.
        "host": args.host,
        "branch": args.branch,
        "base": args.base,
        # The change request or issue this task works ON, when it is not one
        # its own branch opens — a review's pull request, a contributor's fork.
        # Verification compares against this instead of `branch`.
        "target": target,
        "profile": args.profile,
        "agent": args.agent,
        # Directories attached to the worker's session as they are — no
        # worktree, no branch, nothing to publish. `spawn_commands` passes each
        # to thurbox verbatim; everything else here ignores them, which is the
        # whole of what `--add-dir` means.
        "add_dirs": add_dirs,
        # Repositories this task also COMMITS in, each in its own worktree on
        # `branch` above. `PATH` or `PATH@BASE`, kept as the operator wrote it
        # because thurbox owns that syntax. Everything that verifies, lands or
        # shepherds this task reads these too — one artifact per repository.
        "add_repos": add_repos,
        "touches": [s.strip() for s in (args.touches or "").split(",") if s.strip()],
        # Who asked for this work, as free text; displayed and never acted on.
        "requested_by": requested_by,
        # Who OWNS the conversation, and how a worker speaks to them. Opaque
        # strings: fleet matches `origin` against `owners.conf` and `find`, and
        # renders `reply` into the brief, and parses neither — except the one
        # form fleet writes itself, a session origin (`add_origin`).
        **dict(zip(("origin", "reply"), add_origin(args))),
        # What this task must PRODUCE, and — as free text nothing ever parses —
        # what the operator calls the tool that produces it.
        "publish": {"method": method, "how": how},
        "blocked_by": [],
        "session": None,
        "prompted": False,
        # The stream sequence this task's history starts at, stamped by
        # `attach`. Its floor thereafter comes from progress.jsonl itself.
        "watch_from": None,
        "created": now(),
        "dispatched_at": None,
        "outcome": None,
        "artifact": None,
        "concluded_at": None,
    }
    task = Task(args.topic, tid, path, doc)

    # Rendered BEFORE anything exists on disk, so that the one thing `add` can
    # be wrong about costs nothing to be wrong about. `--brief-file` is a claim
    # to have written the brief; a file that names three of the four sections
    # leaves the fourth holding the scaffold's placeholder, which `dispatch`
    # then refuses as "unwritten" -- about a brief the lead did write, without
    # saying which heading it means. That round-trip ran three times in one
    # session before the operator started patching the rendered file by hand.
    #
    # `add` with no --brief-file is untouched. That is the deliberate "scaffold
    # it, I will write it" path, and dispatch stays its backstop.
    brief = open(args.brief_file, encoding="utf-8").read() if args.brief_file else None
    text = render_brief(task, topic_meta, brief)
    if args.brief_file:
        _refuse_partial_brief(args.brief_file, text, brief)

    # A topic that grows a new task is live again, whatever it was. Without
    # this an operator who did not notice the flag would add and dispatch into
    # a topic no default view draws — and would then be watching for progress
    # on a screen that had already decided not to show it. Last of all, so a
    # refusal above has written nothing.
    _unarchive_for_add(root, tpath, args.topic)

    os.makedirs(path)
    task.save()
    fleet_platform.write_record(task.file(BRIEF_FILE), text)

    if slug != args.slug:
        print(f"{args.slug}: add numbers the task itself, so the ref is {task.ref}",
              file=sys.stderr)
    print(task.ref)
    return 0


def _task_title(args, slug: str) -> str:
    """The new task's title, refused when thurbox could not name a session after it."""
    title = args.title or slug.replace("-", " ")
    refusal = session_name_refusal(title, worker_glyph())
    if refusal:
        raise QueueError(refusal)
    return title


def _unarchive_for_add(root: str, tpath: str, topic: str) -> None:
    """Clear a topic's archived flag because a task is being added to it."""
    if read_yaml(os.path.join(tpath, TOPIC_RECORD)).get("archived"):
        set_archived(root, topic, None)
        print(f"{topic} was archived; a new task un-archives it", file=sys.stderr)


def _new_task_path(args, tpath: str) -> tuple[str, str, str]:
    """(slug, id, directory) for the task `add` is about to create, refusing a clash."""
    if not SLUG_RE.match(args.slug):
        raise QueueError(f"{args.slug!r} is not a slug (lowercase, digits, hyphens)")

    # `add` numbers the task itself, so an ordinal the lead typed into the slug
    # was a second one: `01-design` became `01-01-design`, and every ref typed
    # from memory afterwards missed. It is dropped, and the ref is said.
    slug = ORDINAL_RE.sub("", args.slug) or args.slug
    number = args.number or next_number(tpath)
    tid = f"{number}-{slug}"
    path = os.path.join(tpath, tid)
    if os.path.exists(path):
        raise QueueError(f"task {args.topic}/{tid} already exists")
    return slug, tid, path


def _refuse_unplaceable(args, add_repos: list) -> None:

    """Refuse an `add` whose profile, host or branch no spawn could use."""
    # Refused here, before a directory exists and long before a brief is
    # written: a host thurbox does not know, or one fleet cannot drive, is a
    # typo or a decision to make, and both are cheapest at `add` time. So is a
    # profile nobody defined, which dispatch would otherwise render as no
    # flags at all.
    unknown = unknown_profile(args.profile)
    if unknown:
        raise QueueError(f"--profile {args.profile}: {unknown}. Nothing was created.")
    if args.host:
        entry, why = host_entry(args.host)
        if not entry:
            raise QueueError(
                f"--host {args.host}: {why}\n"
                "A host is a name from thurbox's hosts.toml, and `--repo` is then "
                "a path on THAT machine."
            )

    # Same argument, one layer down: a branch no worktree can be made on is a
    # spawn failure `add` can see coming, and refusing it here costs the
    # operator one re-run instead of a dead dispatch and a hand-edited
    # task.yaml.
    # A base dispatch will fetch is a branch name and nothing else, refused
    # before any record holds it (`base_problem` says why).
    for flag, base in [("--base", args.base)] + [(f"--add-repo {r}", split_add_repo(r)[1]) for r in add_repos]:
        if base and (why := base_problem(base)):
            raise QueueError(f"{flag}: {why}. Nothing was created.")
    refusal = branch_refusal(args.repo, args.branch, args.base, args.host)
    if refusal:
        raise QueueError(refusal)

    # And once per --add-repo, for exactly the same reason: thurbox cuts a
    # worktree on the same branch in every one of them, so a branch that
    # already exists in the SECOND repository fails the same spawn just as
    # dead. Each is asked about its own base, which `PATH@BASE` may name.
    for spec in add_repos:
        also, also_base = split_add_repo(spec)
        refusal = branch_refusal(also, args.branch, also_base or args.base, args.host)
        if refusal:
            raise QueueError(f"--add-repo {spec}: {refusal}")


def _refuse_agent(args, add_repos: list) -> None:
    """Refuse an `--agent` the policy covering this task's repositories does not allow."""
    policy = agent_policy()
    if not policy:
        return
    paths = [] if args.host else [args.repo, *(split_add_repo(r)[0] for r in add_repos)]
    matched = repos_policy(paths, policy)
    if not matched:
        return
    allowed, prefix = matched
    if not allowed:
        raise QueueError(
            f"agent policy covers this task's repositories with "
            f"{prefix!r} and no one agent is allowed in all of them. "
            "A task that spans repositories runs one agent in every "
            "one of them."
        )
    if args.agent not in allowed:
        raise QueueError(
            f"--agent {args.agent!r} contradicts policy for "
            f"{prefix!r}, which allows only {', '.join(allowed)}."
        )


def _add_publish(args) -> tuple:
    """(method, how, target) for a new task, refusing a target its method cannot use."""
    # A stated method keeps the default command when it names the same shape.
    # Another shape drops it: an attesting command is wrong for a `push` task.
    # A stated command always wins, with or without a stated method.
    method, how = policy_publish_default()
    if args.publish and args.publish != method:
        method, how = args.publish, None
    if args.how is not None:
        how = args.how
    # A served document's HOW is where it is hosted, which is the operator's
    # `SERVE` line — the publishing HOW names a tool for another shape.
    if method == "served" and not how:
        how = publish_conf().get("SERVE", "").strip() or None
    target = (args.target or "").strip() or None
    refusal = target_refusal(method, target)
    if refusal:
        raise QueueError(refusal)
    return method, how, target


def _refuse_partial_brief(brief_file: str, text: str, body: str) -> None:
    """Refuse a `--brief-file` that left one of the brief's sections unwritten.

    A section heading is its name and nothing else on the line, and that stays
    the contract: a heading with a gloss after it is content, kept verbatim
    under the section before it. What this adds is saying so. A lead who wrote
    `## Hard constraints — read this carefully` and the whole section under it
    was told only that `Hard constraints` was unwritten and to "add the
    heading" -- the one thing they had done. So a section the file came CLOSE
    to opening names the line that came close, and why it did not.
    """
    missing = unfilled_sections(text)
    if not missing:
        return
    near = {h: _near_miss_headings(body, h) for h in missing}
    close = any(near.values())
    rows = []
    for h in missing:
        rows.append(f"    {h}")
        if close and not near[h]:
            rows.append(f"        no line comes close: add `## {h}`.")
        for n, line, why in near[h]:
            rows.append(f"        line {n}: `{line}`")
            if why is None:
                rows.append(
                    "        opens it, but nothing is written under it."
                )
                continue
            rows.append(
                f"        does not open it: {why}.\n"
                f"        A section heading is `## {h}`, at the start of its line\n"
                "        and with nothing else on it, so this one was read as text\n"
                "        of the section before it."
            )
    raise QueueError(
        f"{brief_file} leaves {len(missing)} of the brief's "
        "sections unwritten:\n"
        + "\n".join(rows)
        + "\nA worker gets all four whatever the file says, so one the "
        "file does not\nname stays the scaffold's placeholder and "
        "`dispatch` refuses it. "
        + ("Fix what is named\nabove" if close else "Add the\nheading")
        + " — `None.` is a complete answer — and run this again. "
        "Nothing was created."
    )


def _near_miss_headings(body: str, section: str) -> list:
    """The `--brief-file`'s heading lines that look like `section`, and why each failed.

    A markdown heading of any level, outside a fence, whose text is the
    section's name in any case, ending there or at anything but a letter or
    digit -- `## Hard constraints — why`, `### Hard constraints`, `  ## hard
    constraints`, but not `### What to double-check` for `What to do`. Each
    comes with what kept it from opening the section, in words, or None for
    the exact heading of a section left empty: `split_brief_body` drops an
    empty section, so that one is unwritten under a correct heading.

    Only ever read to explain a refusal: `split_brief_body` alone decides what
    opens a section, and nothing here changes which briefs are accepted.
    """
    out = []
    fence = None
    for n, line in enumerate(body.splitlines(), 1):
        stripped = line.strip()
        if fence is not None or stripped.startswith(("```", "~~~")):
            fence = _next_fence(fence, stripped)
            continue
        hashes = len(stripped) - len(stripped.lstrip("#"))
        if not 1 <= hashes <= 6 or stripped[hashes:hashes + 1] not in (" ", "\t"):
            continue
        text = stripped[hashes:].strip()
        name, rest = text[:len(section)], text[len(section):]
        if name.casefold() != section.casefold() or rest[:1].isalnum():
            continue
        if line.startswith("## ") and line[3:].strip() == section:
            out.append((n, line.rstrip(), None))
            continue
        why = []
        if line != line.lstrip():
            why.append("it is indented")
        if hashes != 2:
            why.append(f"it is a level-{hashes} heading, `{'#' * hashes}`")
        elif stripped[2] != " ":
            why.append("a tab, not a space, follows the `##`")
        if name != section:
            why.append(f"it spells the name `{name}`")
        if rest.strip():
            why.append(f"`{rest.strip()}` follows the name")
        out.append((n, line.rstrip(), "; ".join(why)))
    return out


def _draft_brief_line(task: Task, method: str) -> str:
    """The brief's draft-phase line, or "" where no repository of this task has one.

    Read off each checkout's `origin` locally, and off a host task's origins
    only when dispatch already read them: rendering a brief never opens ssh.
    """
    if method not in CHANGE_METHODS or not draft_repos():
        return ""
    if task.doc.get("host"):
        origins = [repo for repo, _ in (task.host_origins or {}).values()]
    else:
        origins = [repo_from_checkout(u["path"]) for u in task_repos(task)]
    if not any(draft_listed(repo) for repo in origins):
        return ""
    return "\n" + textwrap.fill(
        "- **Draft.** Open the pull request as a DRAFT (`gh pr create --draft`,"
        " `glab mr create --draft`) and never mark it ready yourself: CI and the"
        " review bots go first, and `shepherd` marks it ready for review once"
        " every check passed, nothing is left to fix and no review thread is"
        " open.",
        width=78,
        subsequent_indent="  ",
    )


def render_brief(task: Task, topic: dict, body: str | None) -> str:

    """The worker's whole world: this file, and the one policy file it names.

    What is TASK-SPECIFIC is written here — the goal, the repo, the branch, and
    the absolute path of the result file that closes it. What is true of EVERY
    task is not: it lives in POLICY.md and is pointed at, because policy the
    lead retypes per brief is policy that drifts. It measurably did — `squash
    merge` made it into one hand-written brief in five.

    The result contract stays here even so, because the path is this task's
    alone, and because it is the half of completion the event stream cannot
    supply: a transition says a turn ended, and only this file says what the
    worker concluded.

    Between the two comes BRIEF_SECTIONS, unwritten: the lead supplies content,
    not structure. `--brief-file` fills whichever of those four sections its own
    `## ` headings name (`split_brief_body`), so a body handed in on the command
    line gets the same skeleton -- and `cmd_add` refuses it on the spot if any
    section came out of this still holding the placeholder.

    The operator's own standing instructions ride the same pointer when there
    are any, and NOTHING when there are not — a fresh clone has no such file,
    and its briefs must not name one that does not exist.

    A REMOTE task's brief differs in more than the result contract. An absolute
    control-plane path — the prompt, the standing policy, the operator's
    instructions — is not on the worker's filesystem either, so `settle_remote`
    copies each of those, and a rendered context bundle, alongside the brief in
    `<worktree>.fleet` BESIDE the worktree thurbox mints there, never in it, and
    every pointer below is stated RELATIVE to the brief itself for a host task,
    the one thing a remote worker can always resolve. `collect` fetches the
    result over ssh into this task's own result.md, so the closing claim is the
    same file either way.
    """
    d = task.doc
    host = d.get("host")
    method, how = task_publish(task)
    spec = PUBLISH_METHODS[method]
    publish_line = textwrap.fill(
        f"- **Publish.** `{method}` — {spec['brief']}."
        + (f" Here that means: {how}." if how else "")
        + f" Your result's `artifact:` is {spec['artifact']}, and `collect`"
        f" closes this task only once {spec['proof']}.",
        width=78,
        subsequent_indent="  ",
    )
    publish_line += _draft_brief_line(task, method)
    result = os.path.abspath(task.file(RESULT_FILE))
    prompt = os.path.abspath(os.path.join(os.path.dirname(task.path), PROMPT_FILE))
    has_operator = bool(operator_instructions())
    prompt_ref, policy_ref, operator_ref = _brief_refs(host, prompt)
    operator_line = f"\n- **Operator's standing instructions.** {operator_ref}" if has_operator else ""
    memory_line = remote_context_line() if host else context_lines(task) + declare_lines(task)
    reply_section = reply_lines(task)
    operator_note = (
        "\n\nRead the operator's file too. It is how this operator wants work done\n"
        "across every task, and it ADDS to this brief without replacing anything in\n"
        "it: where the two disagree the brief wins, and where it disagrees with the\n"
        "standing policy the policy wins."
        if has_operator
        else ""
    )
    filled = split_brief_body(body) if body and body.strip() else {}
    sections = "\n\n".join(
        f"## {h}\n\n{filled.get(h, BRIEF_PLACEHOLDER)}" for h in BRIEF_SECTIONS
    )
    where = f" on host `{host}`" if host else ""
    target_line = f"\n- **Target.** {d['target']}" if d.get("target") else ""
    # Whose request this is, so a worker who has to ask knows whom it is for.
    asked = requester(d.get("requested_by"))
    requester_line = f"\n- **Requested by.** {asked}" if asked else ""
    spans = task_repos(task)[1:]
    spans_line = (
        "\n"
        + textwrap.fill(
            "- **Also on this branch.** "
            + ", ".join(f"`{u['path']}` off `{u['base']}`" for u in spans)
            + f" — each in its own worktree on `{d['branch']}`. Your session"
            " opens in a workspace holding one symlink per repository, so each"
            " is a subdirectory there and the absolute paths above resolve"
            " too. What you commit in each is published and verified exactly"
            " as this repository's is, so your result must name one artifact"
            " PER REPOSITORY (see Reporting back).",
            width=78,
            subsequent_indent="  ",
        )
        if spans
        else ""
    )
    attached = d.get("add_dirs") or []
    attached_line = (
        "\n"
        + textwrap.fill(
            "- **Also attached.** "
            + ", ".join(f"`{p}`" for p in attached)
            + " — on whatever branch each was already on. Nothing cut a"
            " worktree there and nothing publishes or verifies them: read"
            " them, and do not commit in them.",
            width=78,
            subsequent_indent="  ",
        )
        if attached
        else ""
    )
    artifact_contract = _artifact_contract(task, spans, method, spec)
    result_target, result_note = _result_target(host, result)
    return f"""# {d["title"]}

Task `{task.ref}` of topic **{topic.get("title", task.topic)}**.
The prompt this came from is at {prompt_ref}; read it if the goal here is unclear.

- **Repo.** `{d["repo"]}`{where}{requester_line}
- **Branch.** `{d["branch"]}` off `{d["base"]}`{spans_line}{attached_line}{target_line}
{publish_line}
- **Expected to touch.** {", ".join(f"`{p}`" for p in d["touches"]) or "not recorded"}
- **Standing policy.** {policy_ref}{operator_line}{memory_line}

**Read that policy file before you start.** It is the rest of your
instructions and it is not repeated here: how to verify your own publish, who
merges it, the gate to run before you push, and what the other workers running
beside you mean for you. This brief does not override it.{operator_note}

{sections}
{reply_section}
## Reporting back — write a file, do not send mail

When you are finished, or when you have concluded you cannot finish, write:

{result_target}

with exactly this shape:

```markdown
---
outcome: shipped | stuck | failed | not-applicable
{artifact_contract}
learned:   # optional, at most 5 — what the next worker on this repository needs
  - repo: {context_repo(task)}
    fact: "<one or two sentences>"
---
A short paragraph: what you actually did, and anything the lead must know.
```

That file is what closes this task, and the policy's last section says why it,
and not a message, is what does it.
{result_note}"""


def _brief_refs(host, prompt: str) -> tuple[str, str, str]:
    """How the brief names the prompt, the policy and the operator's file.

    Relative to the brief itself for a host task, whose filesystem is not this one.
    """
    if host:
        return (
            "`PROMPT.md`, alongside this file",
            "`POLICY.md`, alongside this file",
            "`OPERATOR.md`, alongside this file",
        )
    return f"`{prompt}`", f"`{policy_path()}`", f"`{operator_path()}`"


def _artifact_contract(task: Task, spans: list, method: str, spec: dict) -> str:
    """The brief's `artifact:` line, or the `artifacts:` block a spanning task writes."""
    # The `artifact:` line, or the `artifacts:` block a task that spans
    # repositories writes instead. POLICY.md carries the same pair, and the two
    # have to move together: every brief points its worker there rather than
    # restating the contract, so a change in one place and not the other is how
    # they drift.
    if spans and method in PER_REPO_METHODS:
        return "artifacts:\n" + "\n".join(
            f"  {u['path']}: <{spec['artifact']}>" for u in task_repos(task)
        ) + "\n  # a repository that needed nothing: no change needed — <why>"
    return (
        "artifact: <PR URL, commit URL for a `push` task, note URL for a "
        "`note` task, the document's URL for a `served` task, or omit>"
    )


def _result_target(host, result: str) -> tuple[str, str]:
    """Where the brief tells its worker to write result.md, and the note a host task adds."""
    if host:
        result_target = (
            "    result.md — beside the BRIEF.md you are reading now, in\n"
            f"    `<worktree>{COMPANION_SUFFIX}`, and not inside your worktree"
        )
        result_note = f"""
You are running on the remote host `{host}`, so the control plane's own
directories are not on this filesystem and an absolute path to one would
resolve to nothing here. `fleet queue collect` fetches that file over ssh, and it
closes this task exactly as it would locally.

This brief and every file beside it sit in `<worktree>{COMPANION_SUFFIX}`, next to
your worktree and outside it, so no commit can carry them. Do not copy them
into the worktree. Write `result.md` there after the pull request is open.
"""
        return result_target, result_note
    return f"    {result}", ""


def reply_lines(task: Task) -> str:
    """The brief's section for a task with a reply command, or nothing at all.

    Fixed text: the command is the operator's, and fleet runs and parses none of
    it. A task without one renders no section, so its brief is what it was. The
    one command fleet wrote itself, a session origin's default, takes no
    `--file`, so that hint is left off it.

    A reply that fails means the origin is gone — a deleted session, a relay
    that is down — and nothing routes the question anywhere else: the worker
    asks in its own pane, and a local one declares `needs-input` so the board
    shows that it is.
    """
    d = task.doc
    cmd = str(d.get("reply") or "").strip()
    if not cmd:
        return ""
    sid = origin_session(d.get("origin"))
    file_hint = "" if sid and cmd == session_reply(sid) else " (or `--file <path>`)"
    if d.get("host"):
        fallback = textwrap.fill(
            "If that command fails, the requester cannot be reached: ask your"
            " question here, in your own session, and wait for the answer.",
            width=78,
        )
    else:
        fallback = textwrap.fill(
            "If that command fails, the requester cannot be reached: ask the same"
            " question here, in your own session, where the operator will answer"
            " it, and say that you are waiting:",
            width=78,
        ) + (
            f"\n\n    uv run --project {checkout_root()} fleet queue declare {task.ref}"
            " needs-input --why '<your question>'"
        )
    return f"""
## Replying to the requester

Post with `{cmd} '<text>'`{file_hint}:

- your verdict, once `result.md` is written;
- the pull request URL, as soon as one is open and ready for review;
- every question, BEFORE you run `fleet queue declare ... needs-input`. A
  person answers in the thread and the answer reaches this session.

{fallback}
"""


def _context_module():
    """`fleet context`'s own module (`context.py`), loaded once on first use."""
    return _load_sibling("fleet_context", "context.py")


def context_repo(task: Task) -> str:
    """The name `fleet context` knows this task's repository by, or its path when it cannot tell here."""
    path = str(task.doc.get("repo") or "")
    if task.doc.get("host"):
        return "<repo>"
    return _context_module().path_identity(path) or path


def remote_context_line() -> str:
    """A remote brief's pointer into memory: the bundle beside it, since that host has no `fleet context`."""
    return "\n" + textwrap.fill(
        "- **Project memory.** `CONTEXT.md`, alongside this file: this"
        " repository's live facts and the result of every task this one waits"
        " on, rendered when it was dispatched. Its `repo:` line is the name to"
        " put in `learned:` (see Reporting back).",
        width=78,
        subsequent_indent="  ",
    )


def declare_lines(task: Task) -> str:
    """A local brief's way to say it is waiting on purpose, with its checkout and ref filled in.

    Local only: a worker on a host cannot reach this checkout's records.
    """
    cmd = f"uv run --project {checkout_root()} fleet queue declare {task.ref}"
    return (
        "\n" + textwrap.fill(
            "- **Waiting on purpose.** If you stop to wait for CI or a reviewer, or"
            " need a decision before you can go on, say so before your turn ends"
            " (the standing policy says when):",
            width=78,
            subsequent_indent="  ",
        )
        + f"\n\n      {cmd} paused --until 2h --why '...'"
        + f"\n      {cmd} needs-input --why '...'\n"
    )


def context_lines(task: Task) -> str:
    """The brief's pointers into memory: a command read when the worker starts, never a pasted copy.

    A pointer is current when it is read, which a copy frozen at `add` is not —
    and blockers are usually recorded AFTER `add`, so `--task` has the command
    list the results of whatever this task waits on by then. Results already
    known at `add` are named too, as plain paths.
    """
    prior = [
        os.path.join(queue_root(), b["task"], RESULT_FILE)
        for b in task.blockers
        if not blocker_condition(b) and isinstance(b.get("task"), str)
    ]
    line = "\n" + textwrap.fill(
        "- **Project memory.** Run this before you start: this repository's live"
        " facts, its recent results, and the result of every task this one waits"
        " on. Put what you learn in `learned:` (see Reporting back).",
        width=78,
        subsequent_indent="  ",
    )
    line += (
        f"\n\n      uv run --project {checkout_root()} fleet context repo"
        f" {context_repo(task)} --task {task.ref}\n"
    )
    if prior:
        line += "\n" + textwrap.fill(
            "- **Prior results.** " + ", ".join(f"`{p}`" for p in prior),
            width=78,
            subsequent_indent="  ",
        )
    return line


def blocker_kind_refusal(condition: bool = False) -> str:
    """The paragraph a missed `--kind` or `--why` gets, in one place.

    The half that matters is the last sentence: overlapping files are the
    commonest thing someone reaches for `block` to express, and they are not a
    blocker. `--touches` records them and `plan` reports them as a risk beside
    the ready set, holding nothing up.

    The two forms get the two closed sets, not the union. A `--condition` that
    was answered with the four task kinds would be answered with four
    relationships between tasks, none of which it can be.
    """
    kinds = "\n".join(f"    {k:<24} {v}" for k, v in BLOCKER_KINDS.items())
    conditions = "\n".join(f"    {k:<24} {v}" for k, v in CONDITION_KINDS.items())
    head = (
        "a blocker needs --kind and --why, because it is the one thing that\n"
        "makes work wait and it has to survive the next planning pass.\n"
    )
    if condition:
        return (
            head
            + f"--kind, for a --condition, is one of:\n{conditions}\n"
            "A condition is something OUTSIDE the queue — a credential, an approval,\n"
            "a window, a machine somebody has to fix. Nothing clears one but\n"
            "`block <ref> --clear --condition ...`."
        )
    return (
        head
        + f"--kind is one of:\n{kinds}\n"
        "Overlapping files are not on that list. Record them with `add --touches`;\n"
        "they are reported as a risk beside the ready set and hold nothing up.\n"
        f"Waiting on something that is not a task at all? `--condition` instead\n"
        "of `--on`, with one of:\n" + conditions
    )


def blocker_kind(value: str) -> str:
    """`--kind`'s validator, so a wrong one still gets the whole paragraph.

    `choices` beside it is what puts the values in `--help` — they used to
    appear only in the refusal you got after guessing wrong, and the lead
    guessed twice in one session. `choices` alone would then answer `invalid
    choice` and lose the sentence about `--touches`, so the message stays here
    and argparse never reaches its own.

    It accepts the UNION of the two sets, because which set applies depends on
    whether `--on` or `--condition` came with it and argparse has neither yet.
    `cmd_block` is what refuses a kind from the wrong set, where it can say so.
    """
    if value in BLOCKER_KINDS or value in CONDITION_KINDS:
        return value
    raise argparse.ArgumentTypeError("\n" + blocker_kind_refusal())


def cmd_block(args) -> int:
    """Record — or remove — one of the two things that make a task wait.

    ONE ENTRY NAMES A TASK OR A CONDITION, NEVER BOTH. `--on` and `--condition`
    are mutually exclusive in the parser above, and everything downstream tests
    which by asking `blocker_condition`.
    """
    q = Queue(queue_root())
    task = q.get(args.ref)

    if args.condition is not None:
        return block_on_condition(task, args.condition.strip(), args)

    if args.clear:
        # The ref is matched as written, and as the task it resolves to when
        # it still does. A blocker naming a task whose record is gone never
        # clears itself — `blocker_cleared` reads it as not landed — so this
        # is its only release, and refusing it here as `no such task` left
        # hand-editing task.yaml as the way out.
        try:
            names = {args.on, q.get(args.on).ref}
        except QueueError:
            names = {args.on}
        before = len(task.blockers)
        task.doc["blocked_by"] = [
            b for b in task.blockers if b.get("task") not in names
        ]
        task.save()
        print(f"{task.ref}: {before - len(task.doc['blocked_by'])} blocker(s) cleared")
        return 0

    target = q.get(args.on)
    if args.kind not in BLOCKER_KINDS or not (args.why or "").strip():
        raise QueueError(blocker_kind_refusal())
    if target.ref == task.ref:
        raise QueueError(f"{task.ref} cannot block itself")

    task.doc.setdefault("blocked_by", [])
    task.doc["blocked_by"] = [b for b in task.blockers if b.get("task") != target.ref]
    task.doc["blocked_by"].append(
        {"task": target.ref, "kind": args.kind, "why": args.why.strip(), "recorded": now()}
    )
    task.save()

    cycle = find_cycle(Queue(queue_root()))
    if cycle:
        task.doc["blocked_by"] = [b for b in task.blockers if b.get("task") != target.ref]
        task.save()
        raise QueueError("that blocker closes a cycle: " + " -> ".join(cycle))

    print(f"{task.ref} waits on {target.ref} ({args.kind})")
    return 0


def block_on_condition(task: Task, condition: str, args) -> int:
    """The condition form: a wait on something the queue cannot observe.

    NO CYCLE CHECK, because a condition is not an edge — `find_cycle` walks
    `task:` entries and this adds none. And no landing to wait for: the record
    stands until `--clear` names the same condition back.
    """
    if not condition:
        raise QueueError(
            "--condition is the wait itself, in words: --condition 'az is\n"
            "authenticated for the billing tenant'. Nothing removes one but\n"
            "`block --clear --condition` naming it back, so a blank one is a\n"
            "wait nobody could name and nobody could release."
        )

    if args.clear:
        before = len(task.blockers)
        task.doc["blocked_by"] = [
            b for b in task.blockers if blocker_condition(b) != condition
        ]
        task.save()
        gone = before - len(task.doc["blocked_by"])
        if not gone:
            held = [blocker_condition(b) for b in task.blockers if blocker_condition(b)]
            raise QueueError(
                f"{task.ref} records no condition {condition!r}. It holds: "
                + (", ".join(repr(h) for h in held) if held else "no condition at all")
            )
        print(f"{task.ref}: {gone} blocker(s) cleared")
        return 0

    if args.kind not in CONDITION_KINDS or not (args.why or "").strip():
        raise QueueError(blocker_kind_refusal(condition=True))

    task.doc.setdefault("blocked_by", [])
    task.doc["blocked_by"] = [
        b for b in task.blockers if blocker_condition(b) != condition
    ]
    task.doc["blocked_by"].append(
        {
            "condition": condition,
            "kind": args.kind,
            "why": args.why.strip(),
            "recorded": now(),
        }
    )
    task.save()

    print(
        f"{task.ref} waits on a condition outside the queue ({args.kind}): "
        f"{condition}\n"
        f"    Nothing clears this but you: uv run fleet queue block {task.ref} "
        f"--clear --condition {shlex.quote(condition)}"
    )
    return 0


def find_cycle(q: Queue) -> list | None:
    colour: dict[str, int] = {}
    stack: list[str] = []
    for ref in sorted(q.tasks):
        if colour.get(ref, 0) == 0:
            found = _walk_blockers(q, ref, colour, stack)
            if found:
                return found
    return None


def _walk_blockers(q: Queue, ref: str, colour: dict, stack: list) -> list | None:
    """`find_cycle`'s depth-first walk from `ref`: the cycle it closes, or None."""
    colour[ref] = 1
    stack.append(ref)
    for b in q.tasks[ref].blockers:
        # A condition names no task, so it is not an edge in this graph and
        # cannot be part of a cycle.
        nxt = b.get("task")
        if nxt is None or nxt not in q.tasks:
            continue
        if colour.get(nxt) == 1:
            return stack[stack.index(nxt):] + [nxt]
        if colour.get(nxt, 0) == 0:
            found = _walk_blockers(q, nxt, colour, stack)
            if found:
                return found
    stack.pop()
    colour[ref] = 2
    return None


# How long a dispatched worker may sit at rest, with no result and no commit,
# before `plan` calls it stalled. The same half hour refuel waits on a stale
# `working`, and for the same measured reason: past 95% of real turns. A worker
# at rest that long without a result ended its turn early, asked a question
# nobody saw, or never got its brief — each of which only the lead can answer.
STALLED_IDLE_SECS = 30 * 60

# The session states that are the agent saying it is at rest; `notify_delivery.py`
# wakes into exactly these, and for the same reason reads nothing else.
AT_REST_STATES = ("idle", "done")


# --- declarations: the worker's own word about a wait ------------------------
#
# `stalled` is an INFERENCE, and it cannot tell a worker waiting on CI or a
# reviewer by design from one that ended its turn early; and a worker that
# needed a decision had no way to say so except ending its turn, which reads as
# exactly that stall. So a worker may SAY which it is, in one line appended to
# its own progress.jsonl — the contract, which `fleet queue declare` writes so
# nobody hand-writes JSON:
#
#     {"declare": {"state": "paused", "until": "<ISO-8601 UTC>", "why": "..."},
#      "observed": "<ISO-8601 UTC>"}
#     {"declare": {"state": "needs-input", "why": "..."}, "observed": "..."}
#
# Keyed `declare` the way `shepherd`'s and `publish`'s entries are keyed by
# their own word, and carrying neither `seq` (watch's cursor) nor `at` (what
# `last_transition` reads as the session moving), so no other reader of the
# file can mistake it for one of its own.
#
# IT IS EVIDENCE `plan` READS AND NOTHING ELSE. No state moves, nothing is
# dispatched or cancelled, and the reconciler still decides nothing: `paused`
# keeps a task out of `stalled` until its time, and `needs-input` names it in a
# set of its own that reaches the lead the way a stall does.
#
# ONLY THE LATEST, AND ONLY ONE NEWER THAN THE TASK'S LAST DISPATCH, RESTART OR
# DELIVERED MESSAGE. A declaration is about the conversation that wrote it: a
# `refuel` restart or a fresh dispatch is a new turn the old word says nothing
# about, and a message from the lead is the answer a `needs-input` was waiting
# for. Keep fractional seconds in queue timestamps so a declaration and its
# answer in the same second remain ordered. Exact ties are ambiguous and
# invalidate the declaration rather than hiding a possible stall.

DECLARE_STATES = ("paused", "needs-input")

# The longest a `paused` keeps a worker out of `stalled`, counted from when it
# was declared, whatever `until` says. A CI run or a review round comes back
# well inside six hours; a wait longer than that is not the worker's to
# declare but a CONDITION the lead records (`block --condition`), which only a
# person clears. Without a cap, one line naming next year would hide a dead
# worker for good — the very silence `stalled` exists to end.
PAUSE_CAP_SECS = 6 * 3600


def declaration_floor(task: Task) -> float:
    """When the conversation a declaration must be newer than began."""
    marks = [task.doc.get("dispatched_at")]
    refuels = task.doc.get("refuels") or []
    if refuels and isinstance(refuels[-1], dict):
        marks.append(refuels[-1].get("at"))
    marks += [s.get("at") for s in task.doc.get("sends") or []
              if isinstance(s, dict) and s.get("delivered")]
    return max((record_time(m) for m in marks if m), default=0.0)


def declaration(task: Task) -> dict | None:
    """The task's standing declaration, or None. See the section above."""
    try:
        rows = open(task.file(PROGRESS_FILE), encoding="utf-8").read().splitlines()
    except OSError:
        return None
    latest = _latest_declaration(rows, declaration_floor(task))
    if latest is None:
        return None
    when, said = latest
    out = {"state": said["state"], "why": str(said.get("why") or ""),
           "declared": datetime.fromtimestamp(when, timezone.utc).isoformat()}
    if said["state"] == "paused":
        until = min(aware_time(said.get("until")), when + PAUSE_CAP_SECS)
        if until <= time.time():
            return None
        out["until"] = datetime.fromtimestamp(until, timezone.utc).isoformat()
    return out


def _latest_declaration(rows: list, floor: float) -> tuple | None:
    """(when, what) of the newest declaration in progress.jsonl newer than `floor`."""
    latest = None
    for row in rows:
        try:
            doc = json.loads(row)
        except ValueError:
            continue
        said = doc.get("declare") if isinstance(doc, dict) else None
        if not isinstance(said, dict) or said.get("state") not in DECLARE_STATES:
            continue
        when = record_time(doc.get("observed"))
        if when and when > floor and (latest is None or when >= latest[0]):
            latest = (when, said)
    return latest


def aware_time(stamp) -> float:
    """An ISO-8601 time WITH an offset as an epoch second; 0 for anything else.

    A naive time would be read in whatever zone the reading machine is in, so
    it means nothing here and counts as already past.
    """
    try:
        then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    return then.timestamp() if then.tzinfo else 0.0


def open_tasks(q: Queue) -> list:
    """Dispatched, holding a session, with no result.md yet."""
    return [
        t for t in sorted(q.tasks.values(), key=lambda t: t.ref)
        if t.state == "dispatched" and t.doc.get("session")
        and not os.path.exists(t.file(RESULT_FILE))
    ]


def declared_tasks(q: Queue) -> dict:
    """{ref: row} for every open task with a standing declaration."""
    out = {}
    for task in open_tasks(q):
        said = declaration(task)
        if said:
            out[task.ref] = {"task": task.ref, "session": task.doc["session"], **said}
    return out


def stalled_tasks(q: Queue, declared: dict | None = None) -> list[dict]:
    """Dispatched, at rest past STALLED_IDLE_SECS, no result.md, no commit in that window.

    A JUDGEMENT AND NOT A STATE. Nothing is written and nothing moves: it is
    what `plan` reports, and the reconciler carries it to the lead once, like
    the ready set. A session list that cannot be read reports nothing. A task
    whose worker has declared a wait (`declared_tasks`) is not judged at all:
    its own word is the better evidence, for as long as it stands.
    """
    if declared is None:
        declared = declared_tasks(q)
    todo = [t for t in open_tasks(q) if t.ref not in declared]
    if not todo:
        return []
    sessions, _why = session_snapshot()
    if sessions is None:
        return []
    out = []
    for task in todo:
        row = sessions.get(task.doc["session"]) or {}
        age = row.get("hook_state_age_secs")
        if row.get("state") not in AT_REST_STATES or isinstance(age, bool):
            continue
        if not isinstance(age, (int, float)) or age < STALLED_IDLE_SECS:
            continue
        _sha, at, _note = branch_head(task)
        if at and time.time() - record_time(at) < STALLED_IDLE_SECS:
            continue
        out.append({"task": task.ref, "session": task.doc["session"], "idle_secs": int(age)})
    return out


# How long a dispatched task may read `prompted: false` before `plan` names it.
# Dispatch prompts in the same command that attaches, and its retry budget is
# seconds, so a task still unprompted minutes later is one whose handoff failed
# and nobody retried — or one attached by hand with no brief. Five minutes is
# well past any handoff in flight and far short of the half hour a stall needs,
# because an unprompted worker is not slow: it has nothing to do.
UNPROMPTED_GRACE_SECS = 5 * 60


def unprompted_tasks(q: Queue) -> list[dict]:
    """Dispatched, `prompted` false, dispatched more than UNPROMPTED_GRACE_SECS ago.

    The stalled set reads only sessions at rest, so a worker that never got
    its brief — at a trust dialog, or reporting nothing — reached no standing
    surface. A judgement like `stalled_tasks`, read off the records alone.
    """
    out = []
    for task in sorted(q.tasks.values(), key=lambda t: t.ref):
        if task.state != "dispatched" or task.doc.get("prompted"):
            continue
        if not task.doc.get("session") or os.path.exists(task.file(RESULT_FILE)):
            continue
        waited = time.time() - record_time(task.doc.get("dispatched_at"))
        if waited < UNPROMPTED_GRACE_SECS:
            continue
        out.append({"task": task.ref, "session": task.doc["session"], "waited_secs": int(waited)})
    return out


def commits_ahead(task: Task) -> bool:
    """Whether any of this task's repositories holds a commit on its branch that
    its base does not. Asked of the task's own `repo`, as `branch_head` asks, so
    the worktree's path is never needed; against `origin/<base>` when the clone
    has one, because a local base that fell behind would count origin's commits
    as the worker's. Unreadable is False: this only ever adds a question."""
    branch = str(task.doc.get("branch") or "")
    if task.doc.get("host") or not branch:
        return False
    return any(
        _unit_ahead(unit, branch) for unit in task_repos(task) if os.path.isdir(unit["path"])
    )


def _unit_ahead(unit: dict, branch: str) -> bool:
    """Whether one repository holds a commit on `branch` its base does not."""
    for base in (f"origin/{unit['base']}", unit["base"]):
        if git_out(unit["path"], ["rev-parse", "--verify", "--quiet", base + "^{commit}"]).strip():
            count = git_out(unit["path"], ["rev-list", "--count", f"{base}..{branch}", "--"]).strip()
            return count.isdigit() and int(count) > 0
    return False


def missing_result(sid: str) -> str:
    """What a worker's Stop hook should ask for before the worker stops, or "".

    THE FAILURE IT ANSWERS. A worker publishes — commits pushed, pull request
    open — and ends its turn without the result.md its brief names, and its
    task sits `dispatched` until a person notices, because only that file
    closes a task. So when the stopping session is a DISPATCHED task's, that
    task has no result.md and its branch has commits ahead of its base, this
    returns the one sentence that asks for it, naming the path the brief names.
    `reconcile nudge` hands it to the agent as a Stop-hook block; every other
    session, a lead's above all, gets "" and stops exactly as before. A `--host`
    task's result is written on that machine, so it is never asked about here.
    """
    if not sid:
        return ""
    for task in Queue(queue_root()).tasks.values():
        if task.doc.get("session") != sid:
            continue
        if task.state != "dispatched" or task.doc.get("host"):
            return ""
        result = os.path.abspath(task.file(RESULT_FILE))
        if os.path.exists(result) or not commits_ahead(task):
            return ""
        return (
            f"fleet task {task.ref} has commits on {task.doc['branch']} but no result.md, "
            f"and only that file closes it. If your work is published, write it now at "
            f"{result} as your brief's Reporting back says, then stop. If you are stopping "
            "to wait on a person instead, say so in one line and stop."
        )
    return ""


def cmd_plan(args) -> int:
    q = Queue(queue_root())
    ready, waiting = q.ready(), q.waiting()
    overlaps = q.overlaps(ready)
    declared = declared_tasks(q)
    stalled = stalled_tasks(q, declared)
    paused = [r for r in declared.values() if r["state"] == "paused"]
    needs_input = [r for r in declared.values() if r["state"] == "needs-input"]
    unprompted = unprompted_tasks(q)
    reviews = review_lifecycle().pending(sys.modules[__name__])

    if args.json:
        print(
            json.dumps(
                {
                    "ready": [t.ref for t in ready],
                    "waiting": [
                        {"task": t.ref, "blocked_by": t.blockers} for t in waiting
                    ],
                    "overlaps": overlaps,
                    "stalled": stalled,
                    "unprompted": unprompted,
                    "paused": paused,
                    "needs_input": needs_input,
                    "reviews": reviews,
                },
                indent=2,
            )
        )
        return 0

    _print_plan_ready(ready, overlaps)
    _print_plan_waiting(q, waiting)
    _print_plan_stalled(stalled, unprompted)
    _print_plan_declared(needs_input, paused)
    for row in reviews:
        print(f"review-{row['state']}: {row['url']} at {row['head']} — {row.get('detail', '')}")
    return 0


def _print_plan_ready(ready: list, overlaps: list) -> None:
    """`plan`'s ready set, and the overlaps it goes out with anyway."""
    print(
        f"ready: {len(ready)} task(s) — every one of them goes out now, "
        "there is no concurrency cap"
    )
    for t in ready:
        print(f"    {t.ref:<52} {where_it_runs(t)}  {t.doc['branch']}")
    for o in overlaps:
        print(f"    risk: {', '.join(o['tasks'])} all touch {o['touches']}")
        print(
            "          Overlap is a risk signal, not a reason to wait — dispatch\n"
            "          them together and let the delivery path reconcile a rebase."
        )


def _print_plan_waiting(q: Queue, waiting: list) -> None:
    """`plan`'s waiting set, each with the blockers still holding it."""
    print()
    print(f"waiting: {len(waiting)} task(s) — each held by a durable, recorded blocker")
    for t in waiting:
        print(f"    {t.ref}")
        for b in t.blockers:
            view = blocker_view(q, t, b)
            if view["status"] == "cleared":
                continue
            print(f"        {view['line']}")


def _print_plan_stalled(stalled: list, unprompted: list) -> None:
    """`plan`'s stalled and unprompted sets, when there are any."""
    if stalled:
        print()
        print(
            f"stalled: {len(stalled)} task(s) — at rest for over {STALLED_IDLE_SECS // 60}m "
            "with no result.md and no new commit"
        )
        for row in stalled:
            print(f"    {row['task']:<52} idle {row['idle_secs'] // 60}m  session {row['session']}")
        print("          `fleet queue show <ref>` reads it; `fleet queue send <ref> '...'` nudges it.")
    if unprompted:
        print()
        print(
            f"unprompted: {len(unprompted)} task(s) — dispatched over "
            f"{UNPROMPTED_GRACE_SECS // 60}m ago and never handed the brief"
        )
        for row in unprompted:
            print(f"    {row['task']:<52} {row['waited_secs'] // 60}m  session {row['session']}")
            print(f"          uv run fleet queue prompt {row['task']}")


def _print_plan_declared(needs_input: list, paused: list) -> None:
    """`plan`'s declared waits: what needs input, and what is paused."""
    if needs_input:
        print()
        print(f"needs input: {len(needs_input)} task(s) — the worker said it needs a decision")
        for row in needs_input:
            print(f"    {row['task']:<52} {row['why']}")
        print("          `fleet queue send <ref> '...'` answers it.")
    if paused:
        print()
        print(f"paused: {len(paused)} task(s) — the worker said it is waiting, so not judged stalled")
        for row in paused:
            print(f"    {row['task']:<52} until {row['until']}  {row['why']}")


DURATION = re.compile(r"(\d+)([mh])")


def cmd_declare(args) -> int:
    """Append the worker's own word about a wait to its task's progress.jsonl.

    A CONVENIENCE OVER THE CONTRACT, which is the JSON line the declarations
    section above spells: this writes it so no worker hand-writes JSON. It
    reaches a LOCAL worker, whose brief names the checkout; a worker on a
    `--host` has no control-plane filesystem to write to, so it cannot declare,
    and its stalls stay the inference they always were.
    """
    q = Queue(queue_root())
    task = q.get(args.ref)
    if task.state != "dispatched" or os.path.exists(task.file(RESULT_FILE)):
        raise QueueError(
            f"{task.ref} is {task.state}, or has its result written; only a dispatched "
            "task that has not reported can declare a wait"
        )
    why = args.why.strip()
    if not why:
        raise QueueError("--why is required: the lead reads it instead of your pane")
    said = {"state": args.state}
    if args.state == "paused":
        until = _pause_until(args.until)
        said["until"] = datetime.fromtimestamp(int(until), timezone.utc).isoformat()
    elif args.until:
        raise QueueError("--until is for paused only")
    said["why"] = why
    fleet_platform.append_record(
        task.file(PROGRESS_FILE), json.dumps({"declare": said, "observed": now()}) + "\n"
    )
    print(f"{task.ref}: declared {args.state}" + (f" until {said['until']}" if "until" in said else ""))
    return 0


def _pause_until(text: str | None) -> float:
    """`declare paused --until`, as an epoch second inside the pause cap, or refused."""
    if not text:
        raise QueueError("paused needs --until: an ISO-8601 time with an offset, or 45m / 2h")
    rel = DURATION.fullmatch(text.strip())
    if rel:
        until = time.time() + int(rel.group(1)) * (60 if rel.group(2) == "m" else 3600)
    else:
        until = aware_time(text)
        if not until:
            raise QueueError(
                f"--until {text!r} is not an ISO-8601 time with an offset "
                "(...Z or ...+00:00), nor a duration like 45m or 2h"
            )
    if until <= time.time():
        raise QueueError(f"--until {text} is already past")
    if until > time.time() + PAUSE_CAP_SECS:
        raise QueueError(
            f"a pause counts for at most {PAUSE_CAP_SECS // 3600}h. A longer wait is a "
            "condition the lead records with `fleet queue block --condition`, so "
            "declare needs-input and say what you are waiting on"
        )
    return until


# --- remote hosts ------------------------------------------------------------

#
# A task may name a HOST from thurbox's hosts.toml, and `session create --host`
# then puts the agent, its tmux window and its git worktrees on that machine.
# Only the TUI stays here. Everything in this section exists because the queue's
# model assumes a shared filesystem and a remote worker has none.
#
# THE DECISION THAT SHAPED IT. `collect` closes a task by reading the result.md
# a worker wrote into that task's directory — which is HERE, on the control
# plane. A worker on another machine cannot write to it. Two ways out, and they
# are not equivalent:
#
#   ssh transport   the brief is pushed to the host before the worker is
#                   prompted, and the result is pulled back INTO this same
#                   result.md before `collect` reads it. One completion model,
#                   at the cost of the plumbing below.
#   message send    the worker reports through thurbox's own database, which
#                   needs no shared filesystem and almost no code here.
#
# The second was refused. `message send` INJECTS into the lead's conversation and
# interrupts whoever is talking to it — that is why this file's header says
# workers write files and do not send mail, and it is as true of a remote worker
# as of a local one. It would also make completion arrive by two mechanisms
# depending on where a task happened to run, so `collect`, `reap`, `shepherd`
# and the pane would each have to learn the difference. ssh confines that
# difference to `push_brief` and `fetch_result`. By the time anything else reads
# a task, its result.md is a local file that says nothing about where it came
# from.
#
# TWO SHELLS, ONE LINE PROTOCOL. Every remote command here is written twice:
# once for a POSIX shell and once for Windows PowerShell 5, which is what sshd
# on a native-Windows host hands a command to. Which one is the host's
# `platform` in hosts.toml (`posix` or `windows`), independent of its
# multiplexer, exactly as in thurbox: psmux runs on a POSIX host and tmux on a
# Windows one. Only an entry that sets no `platform` falls back to the older
# proxy, `psmux` meaning Windows and `tmux` POSIX; any other multiplexer, or a
# platform fleet cannot name, is refused by name. `HostShell` below is the
# seam, and each pair of scripts prints the same words, so every caller reads
# one answer and never learns which machine gave it.
#
# Probe 1 still asks the host to print its shell's sentinel, and a host that
# does not is refused BY NAME, before any session exists — a hosts.toml entry
# that says `tmux` about a Windows box is caught there, not at a worker's first
# command.
#
# CREDENTIALS ARE NEVER MOVED. The host needs its OWN forge credentials to
# clone, fetch and push; ours are not inherited and nothing here sends them.
# Probe 2 asks whether the host has any, and refuses the dispatch when it does
# not — that is the whole of fleet's involvement. Forwarding an SSH agent would
# also fix it and would forward every key that agent holds; that is the
# operator's call to make on their own machine, not something a dispatch makes
# for them.

# What probe 1 asks the host to print, one per shell. A POSIX shell echoes its
# own; a PowerShell host has no `printf` and answers that one with an error, and
# a POSIX host handed `-EncodedCommand` has no `powershell` — so each shell can
# only ever print the sentinel of the shell it really is.
POSIX_SENTINEL = "fleet-posix-ok"
POWERSHELL_SENTINEL = "fleet-powershell-ok"

# ssh's own reserved exit code: the connection itself failed, as opposed to the
# remote command running and exiting non-zero. It is what tells "unreachable"
# apart from "reachable, and that command did not work there".
SSH_CONNECTION_FAILED = 255

# Every ssh here is bounded twice: BatchMode, so a host that wants a password
# fails instead of waiting for one nobody is there to type, and a hard timeout
# on the process. A probe that hangs is worse than a probe that fails — the
# dispatch it guards is holding every other task in the wave behind it.
#
# Appended AFTER the operator's own ssh_opts on purpose: ssh takes the FIRST
# value it obtains for an option, so anything they set in hosts.toml still wins.
SSH_GUARD_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
SSH_TIMEOUT = 60

# THE SHELL EVERY REMOTE COMMAND RUNS IN, and the reason it is not the default
# one. `ssh host 'cmd'` gets a shell that is neither a login shell nor an
# interactive one: none of the account's profile has run, so `PATH` is the bare
# system default. Agent and thurbox binaries live in `~/.local/bin`, which is
# exactly what a profile puts on `PATH` — so `command -v thurbox-cli` answered
# "not found" on a Debian test host where thurbox-cli 2.20.0 is installed, and
# the lead read that as an unprovisioned host.
#
# This is thurbox pull request #1100's bug in fleet's own code, and `/bin/sh
# -lc` is thurbox's own remedy for it (`login_wrap_for_remote`). The host's
# login shell is the host's business and nothing here may hard-code one:
# measured on a Debian test host whose login shell IS zsh, `/bin/sh -lc` finds
# the binary through `~/.profile` while `zsh -lc` does not, because a
# non-interactive zsh reads no `~/.zshrc`. So the POSIX login shell is both the
# simpler answer and the better one.
LOGIN_SHELL = "/bin/sh"


def login_wrap(script: str) -> str:
    """One command, run by the host's POSIX login shell."""
    return f"{LOGIN_SHELL} -lc {shlex.quote(script)}"


def reportable(stream: str) -> str:
    """A stream as a person would read it, the way thurbox's
    `reportable_stderr` cleans one. Three layers nobody here owns can put their
    own lines where the one worth reporting should be:

    - OpenSSH 10's post-quantum advisory, which is informational and on stderr
      for every connection to an older server — a POSIX host's as much as a
      Windows one's — so a failure used to report the advisory instead;
    - PowerShell's CLIXML envelope (`#< CLIXML`, then `<Objs …>`), which is how
      it writes an error when stderr is not a console, with the message inside;
    - PowerShell's plain error trailer (`At line:`, `+ CategoryInfo`, `+
      FullyQualifiedErrorId`), which puts the error's position after its text.
    """
    text = stream or ""
    if "#< CLIXML" in text:
        text = "".join(re.findall(r'<S S="Error">(.*?)</S>', text, re.S))
        text = re.sub(r"_x([0-9A-Fa-f]{4})_", lambda m: chr(int(m.group(1), 16)), text)
        for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                             ("&apos;", "'"), ("&amp;", "&")):
            text = text.replace(entity, char)
    lines = [ln for ln in text.splitlines()
             if not re.match(r"\s*\*\* .*(post-quantum|store now, decrypt later|openssh\.com/pq)", ln)]
    if "FullyQualifiedErrorId" in text:
        # What is left is one message, wrapped at the console's width, so its
        # last line alone is a fragment of a path. Rejoin it.
        kept = [ln.strip() for ln in lines
                if ln.strip() and not re.match(r"\s*(At line:|At char:|\+|~)", ln)]
        lines = [" ".join(kept)]
    return "\n".join(lines)


def first_line(proc) -> str:
    """The one line of an ssh failure worth reporting, stderr before stdout."""
    for stream in (reportable(proc.stderr), proc.stdout):
        lines = [ln.strip() for ln in (stream or "").splitlines() if ln.strip()]
        if lines:
            return lines[-1]
    return ""


def last_out(proc) -> str:
    """The last non-empty line the host printed on stdout.

    THE LAST, and not the whole stream: a login shell sources the account's
    profile before it runs anything, and a profile that prints a banner would
    otherwise be glued to the front of the one line a probe meant to say.
    """
    return last_out_of(proc.stdout)


def last_out_of(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def thurbox_config() -> dict:
    """`thurbox-cli config show --json`, or {} when it cannot be asked.

    Only ever used to LOCATE hosts.toml and to name the hosts thurbox knows, so
    an empty answer degrades to the default path and a vaguer refusal — never
    to a guess about whether a host exists.
    """
    if not shutil.which("thurbox-cli"):
        return {}
    try:
        proc = subprocess.run(
            ["thurbox-cli", "config", "show", "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        return json.loads(proc.stdout) if proc.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def hosts_file() -> str:
    """Where thurbox reads hosts.toml — asked of thurbox rather than assumed."""
    path = ((thurbox_config().get("paths") or {}).get("hosts_toml")) or ""
    return str(path) or os.path.join(fleet_platform.thurbox_config_dir(), "hosts.toml")


def host_entry(name: str) -> tuple[dict | None, str]:
    """The hosts.toml entry for `name`, or None and the reason there is not one.

    Read out of hosts.toml and not out of `config show`, because a dispatch
    needs the `destination` and the `ssh_opts` to talk to the host at all and
    `config show` reports only the names. A name thurbox does not know is
    refused here, at `add` time, where it costs nothing.
    """
    path = hosts_file()
    try:
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
    except OSError as exc:
        return None, f"{path} could not be read: {exc}"
    except tomllib.TOMLDecodeError as exc:
        return None, f"{path} is not valid TOML: {exc}"

    hosts = [h for h in (doc.get("hosts") or []) if isinstance(h, dict)]
    entry = next((h for h in hosts if h.get("name") == name), None)
    if entry is None:
        known = ", ".join(str(h.get("name")) for h in hosts if h.get("name"))
        return None, f"no host named {name!r} in {path} (it knows: {known or 'none'})"
    if not entry.get("destination"):
        return None, f"host {name!r} in {path} has no `destination` to ssh to"

    # The platform is which shell a host speaks — see this section's header.
    # One fleet has no shell for is refused by name here rather than
    # discovered by a worker that cannot run its first command.
    if host_platform(entry) is None:
        if entry.get("platform") is not None:
            return None, (
                f"host {name!r} sets platform {entry.get('platform')!r}, which fleet "
                "has no shell for: it knows `posix` and `windows`."
            )
        mux = str(entry.get("multiplexer") or "tmux")
        return None, (
            f"host {name!r} runs the {mux!r} multiplexer and sets no `platform`, so "
            "fleet has no shell for it: without one it reads a `tmux` host as POSIX "
            "and a `psmux` one as Windows. Set `platform` on that host, run this "
            "task locally, or name a host fleet can speak to."
        )

    # THE TRUST DIALOG, decided here. `session capture`, `key` and `send` all
    # work against a remote session — thurbox delegates each verb to the
    # thurbox-cli on the host — so `session_trust.py` answers a remote dialog
    # exactly as it answers a local one. That delegation is switched off
    # wholesale by `share_sessions = false`, and then nothing can see the pane:
    # the worker would sit on its dialog with the brief unread, which is the
    # silent stall this whole mechanism exists to prevent. So it is refused,
    # rather than dispatched and hoped for.
    if entry.get("share_sessions") is False:
        return None, (
            f"host {name!r} sets `share_sessions = false`, which switches off the "
            "delegation that lets `session capture` and `session key` see a pane on "
            "that machine. Fleet could spawn the worker but could not get it past "
            "its agent's trust dialog, and it would sit there with the brief unread. "
            "Drop that setting (it defaults to true), or run this task locally."
        )
    return entry, ""


def ssh_argv(entry: dict) -> list:
    opts = [str(o) for o in (entry.get("ssh_opts") or [])]
    return ["ssh", *opts, *SSH_GUARD_OPTS, str(entry["destination"])]


# --- the host's shell: the seam every remote command goes through -----------
#
# THE WHOLE INTERFACE, and each method is something a caller above actually
# asks a host:
#
#     command(script, login)  the one string ssh is handed
#     reach()                 prints this shell's sentinel
#     repo_check(repo)        prints `ok`, `no-dir` or `no-git`
#     fetch_base(repo, b, n)  fetches `origin/<n>` and prints a FETCH_* word,
#                             git's own error on stderr
#     drop_upstream(worktree) unsets its branch's upstream, prints UPSTREAM_GONE
#     forge_probe(repo)       prints `<host> with <credential>`, or
#                             `no forge: <why>`, or `<host>|<tools found>`
#     write(dest, text)       (script, stdin) that puts `text` at `dest`
#     read(src) / decode(out) prints a file, and turns that back into its text
#     join(worktree, name)    a path beside another, in the host's own spelling
#     thurbox_cli(args)       runs the host's own thurbox-cli (HOST_CLI_POSIX)
#
# Every exit status means only zero or not: Windows' outer shell folds a
# PowerShell script's `exit 3` into 1, so nothing here reads a code beyond
# ssh's own 255, and every answer that matters is a WORD on stdout.


# WHERE A REMOTE WORKER'S PRIVATE FILES GO: a directory BESIDE its worktree,
# `<worktree>.fleet`, and never inside it. The brief, the prompt, the policy,
# the operator's file and the context bundle are the control plane's, and a
# copy in the worktree is one `git add -A` from a public pull request — which
# only the worker's own diligence used to prevent. Beside it, no commit can
# reach them, and `reap` removes exactly the files fleet put there.
COMPANION_SUFFIX = ".fleet"


# THE HOST'S OWN thurbox-cli, WHERE THURBOX LOOKS FOR IT. thurbox provisions
# one on every host it shares sessions with, under its data directory and
# DELIBERATELY NOT ON `PATH` (`session_ops/host_cli.rs`, `HOST_BIN_DIR`), and
# drives every remote session through that copy. A host fleet's probes passed
# therefore has a CLI whether or not anybody installed one, and asking for it
# by bare name answered "not found" on exactly those hosts — `reap` kept every
# session there for good. So the same places, in thurbox's own order: its
# provisioned copy, then `PATH`, then the installer's defaults. The copy is
# THIS thurbox's FLAVOUR's (`host_cli_flavour`): a dev build provisions under
# `thurbox-dev`, and the release copy beside it reads another database, whose
# empty list would pass every session as unoccupied. None found is an answer on
# stderr that every caller keeps on, never an empty list.
NO_HOST_CLI = (
    "no thurbox-cli on the host: not where thurbox provisions it "
    "(its data directory's bin/), and not on PATH"
)
HOST_CLI_POSIX = (
    'for c in "$HOME/.local/share/{flavour}/bin/thurbox-cli" thurbox-cli '
    '"$HOME/.local/bin/thurbox-cli" /usr/local/bin/thurbox-cli; do '
    'p=$(command -v "$c" 2>/dev/null) && [ -x "$p" ] && exec "$p" {args}; done; '
    "echo " + shlex.quote(NO_HOST_CLI) + " >&2; exit 127"
)
HOST_CLI_POWERSHELL = (
    "$c = @(\"$env:LOCALAPPDATA\\{flavour}\\bin\\thurbox-cli.exe\", 'thurbox-cli', "
    "\"$env:LOCALAPPDATA\\Programs\\thurbox\\thurbox-cli.exe\")\n"
    "foreach ($p in $c) { $g = Get-Command $p -ErrorAction SilentlyContinue; "
    "if ($g) { & $g.Source {args}; exit $LASTEXITCODE } }\n"
    "[Console]::Error.WriteLine(__NO_CLI__)\nexit 127\n"
)


HOST_CLI_FLAVOURS = ("thurbox", "thurbox-dev")
_host_cli_flavour: list = []


def host_cli_flavour() -> str:
    """`thurbox` or `thurbox-dev`: the data directory this machine's thurbox-cli
    names, whose last part is what thurbox provisions a host's copy under.
    Asked once per process; a CLI that does not say is a release one, which is
    every thurbox before `version --json` and the only kind fleet installs."""
    if not _host_cli_flavour:
        flavour = "thurbox"
        try:
            proc = subprocess.run(
                ["thurbox-cli", "version", "--json"],
                capture_output=True, text=True, encoding="utf-8", timeout=10,
            )
            data_dir = json.loads(proc.stdout).get("data_dir") if proc.returncode == 0 else None
            name = os.path.basename(str(data_dir).rstrip("/\\")) if data_dir else ""
            flavour = name if name in HOST_CLI_FLAVOURS else flavour
        except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
            pass
        _host_cli_flavour.append(flavour)
    return _host_cli_flavour[0]


class PosixShell:
    """A POSIX host — every remote command exactly as it was first written."""

    name = "posix shell"
    label = "POSIX shell"
    sentinel = POSIX_SENTINEL

    def command(self, script: str, login: bool) -> str:
        return login_wrap(script) if login else script

    def reach(self) -> str:
        return f"printf %s {POSIX_SENTINEL}"

    def repo_check(self, repo: str) -> str:
        quoted = shlex.quote(repo)
        return (
            f"if [ ! -d {quoted} ]; then printf no-dir; exit 1; fi\n"
            f"if [ ! -e {quoted}/.git ]; then printf no-git; exit 1; fi\n"
            "printf ok\n"
        )

    def forge_probe(self, repo: str) -> str:
        return forge_probe(repo)

    def write(self, dest: str, text: str) -> tuple[str, str]:
        return f"cat > {shlex.quote(dest)}", text

    def read(self, src: str) -> str:
        return f"cat {shlex.quote(src)}"

    def decode(self, stdout: str) -> str | None:
        return stdout

    def join(self, worktree: str, name: str) -> str:
        return f"{worktree.rstrip('/')}/{name}"

    def beside(self, worktree: str) -> str:
        return f"{worktree.rstrip('/')}{COMPANION_SUFFIX}"

    def private_dir(self, path: str) -> str:
        quoted = shlex.quote(path)
        return f"mkdir -p -- {quoted} && chmod 700 -- {quoted}"

    def set_aside(self, src: str, dst: str) -> str:
        quoted = shlex.quote(src)
        return f"mv -f -- {quoted} {shlex.quote(dst)} 2>/dev/null; [ ! -e {quoted} ]"

    def remove(self, path: str, names: list) -> str:
        files = " ".join(shlex.quote(self.join(path, n)) for n in names)
        return f"rm -f -- {files} && rmdir -- {shlex.quote(path)}"

    def origin(self, repo: str) -> str:
        return f"git -C {shlex.quote(repo)} remote get-url origin"

    def fetch_base(self, repo: str, base: str, name: str) -> str:
        quoted = shlex.quote(repo)
        return (
            "command -v git >/dev/null 2>&1 || "
            f"{{ echo 'git is not on PATH' >&2; printf {FETCH_FAILED}; exit 1; }}\n"
            f"git -C {quoted} remote get-url origin >/dev/null 2>&1 || {{ printf {FETCH_AS_WRITTEN}; exit 0; }}\n"
            f"git -C {quoted} rev-parse --verify --quiet {shlex.quote('refs/tags/' + base)} >/dev/null 2>&1"
            f" && {{ printf {FETCH_AS_WRITTEN}; exit 0; }}\n"
            f"err=$(LC_ALL=C GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never git -C {quoted}"
            f" {shell_quote(fetch_config('/dev/null'))} fetch --quiet --no-recurse-submodules origin"
            f" {shlex.quote(fetch_refspec(name))} 2>&1 </dev/null) && {{ printf {FETCH_DONE}; exit 0; }}\n"
            f"case \"$err\" in *{shlex.quote(NO_REMOTE_REF)}*) printf {FETCH_AS_WRITTEN}; exit 0 ;; esac\n"
            f"printf '%s\\n' \"$err\" >&2; printf {FETCH_FAILED}; exit 1\n"
        )

    def drop_upstream(self, worktree: str) -> str:
        quoted = shlex.quote(worktree)
        return (
            f"if git -C {quoted} rev-parse --verify --quiet '@{{upstream}}' >/dev/null 2>&1; then\n"
            f"  git -C {quoted} branch --unset-upstream </dev/null || exit 1\n"
            "fi\n"
            f"printf {UPSTREAM_GONE}\n"
        )

    def thurbox_cli(self, args: str) -> str:
        return HOST_CLI_POSIX.replace("{flavour}", host_cli_flavour()).replace("{args}", args)


def ps_quote(s: str) -> str:
    """A PowerShell single-quoted literal. Only a single quote is special in
    one, doubled — and PowerShell reads the typographic ones (U+2018 to
    U+201B) as that quote too. `\\`, `$` and the backtick are literal, which is
    what a Windows path needs."""
    return "'" + re.sub("['\u2018\u2019\u201a\u201b]", lambda m: m.group(0) * 2, str(s)) + "'"


# THE POWERSHELL CREDENTIAL PROBE: FORGE_PROBE_TEMPLATE's questions, in the same
# order and printing the same words, so `probe_host` reads one protocol. The
# backticks are inside single quotes, where PowerShell takes them literally.
#
# THE KEY CHECK IS `Start-Process`, not `& ssh`, and bounded. ssh.exe run inline
# inside an ssh session on Windows never returned: measured on a Windows 11
# host, `& ssh -T git@github.com`, the same with `-n`, and `cmd /c "... <NUL"`
# each hung past 45s, and the whole probe past SSH_TIMEOUT. Started as its own
# process with its stdio on files it answered in 1.6s. The 20s bound is so a
# host where it does hang reads as "no key" and goes on to ask `gh` and `glab`.
POWERSHELL_FORGE_PROBE_TEMPLATE = r"""$repo = __REPO__
$url = ''
if (Get-Command git -ErrorAction SilentlyContinue) {
    $url = "$(& git -C $repo remote get-url origin 2>$null)".Trim()
}
$hostport = ''
if ($url -match '^[^:/]+://([^/]*)') {
    $hostport = $Matches[1] -replace '^.*@', ''
} elseif ($url -match '^[^@/]+@([^:/]+):') {
    $hostport = $Matches[1]
}
$h = ($hostport -split ':')[0]
$port = ''
if ($hostport.Contains(':')) { $port = $hostport.Substring($hostport.IndexOf(':') + 1) }
if (-not $h) {
    Write-Output ('no forge: ' + $repo + ' has no readable `origin`, so there is no host to prove a credential against')
    exit 1
}
$banner = ''
if (Get-Command ssh -ErrorAction SilentlyContinue) {
    $sshArgs = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10')
    if ($port) { $sshArgs += @('-p', $port) }
    $sshArgs += @('-T', ('git@' + $h))
    $in = [IO.Path]::GetTempFileName()
    $out = [IO.Path]::GetTempFileName()
    $err = [IO.Path]::GetTempFileName()
    try {
        $p = Start-Process -FilePath 'ssh' -ArgumentList $sshArgs -NoNewWindow -PassThru `
            -RedirectStandardInput $in -RedirectStandardOutput $out -RedirectStandardError $err
        if (-not $p.WaitForExit(20000)) { $p.Kill() }
        $banner = (Get-Content -LiteralPath $out, $err -Raw) -join ' '
    } finally {
        Remove-Item -LiteralPath $in, $out, $err -ErrorAction SilentlyContinue
    }
}
if ($banner -like '*successfully authenticated*' -or $banner -like '*Welcome to GitLab*') {
    Write-Output ($h + ' with an ssh key')
    exit 0
}
$tools = ''
if (Get-Command gh -ErrorAction SilentlyContinue) {
    & gh auth status --hostname $h *> $null
    if ($LASTEXITCODE -eq 0) { Write-Output ($h + ' with a gh token'); exit 0 }
    $tools = '`gh`'
}
if (Get-Command glab -ErrorAction SilentlyContinue) {
    & glab auth status --hostname $h *> $null
    if ($LASTEXITCODE -eq 0) { Write-Output ($h + ' with a glab token'); exit 0 }
    if ($tools) { $tools += ' and ' }
    $tools += '`glab`'
}
Write-Output ($h + '|' + $tools)
exit 1
"""


class PowerShell:
    """A native-Windows host, whose sshd hands every command to PowerShell 5.

    `-EncodedCommand`, and never `-Command "..."`, for thurbox's reason
    (`host_powershell_c`): the string crosses the host's default shell first,
    which is PowerShell itself and expands `$…` inside it. Base64 is
    `[A-Za-z0-9+/=]`, so nothing on the way finds anything to interpret.

    NO LOGIN SHELL, because Windows has no such thing to miss: an ssh session
    gets the account's `PATH` from the registry, which is how `claude.exe` in
    `~\\.local\\bin` resolved on a Windows 11 host with nothing sourced.

    BYTES TRAVEL AS BASE64 IN BOTH DIRECTIONS. PowerShell 5 decodes stdin and
    encodes stdout through the console code page (`ibm850` on a Windows 11
    host), so a brief with one non-ASCII character in it would not arrive as
    written, and `>` would write it as UTF-16. ASCII survives every code page.
    """

    name = "powershell"
    label = "PowerShell"
    sentinel = POWERSHELL_SENTINEL

    # Progress records otherwise reach stderr as CLIXML ("Preparing modules for
    # first use") on a command that succeeded.
    PREAMBLE = "$ProgressPreference = 'SilentlyContinue'\n"

    def command(self, script: str, login: bool) -> str:
        encoded = base64.b64encode((self.PREAMBLE + script).encode("utf-16-le"))
        return "powershell -NoProfile -NonInteractive -EncodedCommand " + encoded.decode("ascii")

    def reach(self) -> str:
        return f"Write-Output '{POWERSHELL_SENTINEL}'"

    def repo_check(self, repo: str) -> str:
        quoted = ps_quote(repo)
        return (
            f"if (-not (Test-Path -LiteralPath {quoted} -PathType Container)) "
            "{ Write-Output 'no-dir'; exit 1 }\n"
            f"if (-not (Test-Path -LiteralPath (Join-Path {quoted} '.git'))) "
            "{ Write-Output 'no-git'; exit 1 }\n"
            "Write-Output 'ok'\n"
        )

    def forge_probe(self, repo: str) -> str:
        return POWERSHELL_FORGE_PROBE_TEMPLATE.replace("__REPO__", ps_quote(repo))

    def write(self, dest: str, text: str) -> tuple[str, str]:
        script = (
            "$ErrorActionPreference = 'Stop'\n"
            "$bytes = [Convert]::FromBase64String([Console]::In.ReadToEnd().Trim())\n"
            f"[IO.File]::WriteAllBytes({ps_quote(dest)}, $bytes)\n"
            "Write-Output 'fleet-wrote'\n"
        )
        return script, base64.b64encode(text.encode("utf-8")).decode("ascii")

    def read(self, src: str) -> str:
        quoted = ps_quote(src)
        return (
            "$ErrorActionPreference = 'Stop'\n"
            f"if (-not (Test-Path -LiteralPath {quoted} -PathType Leaf)) "
            "{ Write-Output 'fleet-no-file'; exit 1 }\n"
            f"[Console]::Out.Write([Convert]::ToBase64String([IO.File]::ReadAllBytes({quoted})))\n"
        )

    def decode(self, stdout: str) -> str | None:
        try:
            return base64.b64decode(last_out_of(stdout), validate=True).decode("utf-8")
        except ValueError:
            return None

    def join(self, worktree: str, name: str) -> str:
        return f"{worktree.rstrip(chr(92) + '/')}\\{name}"

    def beside(self, worktree: str) -> str:
        return f"{worktree.rstrip(chr(92) + '/')}{COMPANION_SUFFIX}"

    # A new directory inherits the ACL of the one it is made in, which on a
    # Windows host is the account's own tree; POSIX gets an explicit 0700.
    def private_dir(self, path: str) -> str:
        return (
            "$ErrorActionPreference = 'Stop'\n"
            f"New-Item -ItemType Directory -Force -Path {ps_quote(path)} | Out-Null\n"
            "Write-Output 'fleet-dir'\n"
        )

    def set_aside(self, src: str, dst: str) -> str:
        quoted = ps_quote(src)
        return (
            "$ErrorActionPreference = 'Stop'\n"
            f"if (Test-Path -LiteralPath {quoted} -PathType Leaf) "
            f"{{ Move-Item -LiteralPath {quoted} -Destination {ps_quote(dst)} -Force }}\n"
            "Write-Output 'fleet-set-aside'\n"
        )

    def remove(self, path: str, names: list) -> str:
        files = ",".join(ps_quote(self.join(path, n)) for n in names)
        return (
            "$ErrorActionPreference = 'Stop'\n"
            f"Remove-Item -LiteralPath {files} -Force -ErrorAction SilentlyContinue\n"
            f"Remove-Item -LiteralPath {ps_quote(path)}\n"
            "Write-Output 'fleet-removed'\n"
        )

    def origin(self, repo: str) -> str:
        return f"& git -C {ps_quote(repo)} remote get-url origin\n"

    def fetch_base(self, repo: str, base: str, name: str) -> str:
        quoted = ps_quote(repo)
        return (
            "if (-not (Get-Command git -ErrorAction SilentlyContinue)) "
            f"{{ [Console]::Error.WriteLine('git is not on PATH'); Write-Output '{FETCH_FAILED}'; exit 1 }}\n"
            f"& git -C {quoted} remote get-url origin *> $null\n"
            f"if ($LASTEXITCODE -ne 0) {{ Write-Output '{FETCH_AS_WRITTEN}'; exit 0 }}\n"
            f"& git -C {quoted} rev-parse --verify --quiet {ps_quote('refs/tags/' + base)} *> $null\n"
            f"if ($LASTEXITCODE -eq 0) {{ Write-Output '{FETCH_AS_WRITTEN}'; exit 0 }}\n"
            "$env:LC_ALL = 'C'\n"
            "$env:GIT_TERMINAL_PROMPT = '0'\n"
            "$env:GCM_INTERACTIVE = 'never'\n"
            # Each stderr record as its own text, so git's `fatal:` line is not
            # wrapped in PowerShell's error decoration.
            f"$err = (& git -C {quoted} {' '.join(ps_quote(a) for a in fetch_config('NUL'))}"
            f" fetch --quiet --no-recurse-submodules origin {ps_quote(fetch_refspec(name))}"
            " 2>&1 | ForEach-Object { \"$_\" }) -join \"`n\"\n"
            f"if ($LASTEXITCODE -eq 0) {{ Write-Output '{FETCH_DONE}'; exit 0 }}\n"
            f"if ($err -like {ps_quote('*' + NO_REMOTE_REF + '*')}) {{ Write-Output '{FETCH_AS_WRITTEN}'; exit 0 }}\n"
            f"[Console]::Error.WriteLine($err); Write-Output '{FETCH_FAILED}'; exit 1\n"
        )

    def drop_upstream(self, worktree: str) -> str:
        quoted = ps_quote(worktree)
        return (
            f"& git -C {quoted} rev-parse --verify --quiet '@{{upstream}}' *> $null\n"
            "if ($LASTEXITCODE -eq 0) {\n"
            f"  & git -C {quoted} branch --unset-upstream\n"
            "  if ($LASTEXITCODE -ne 0) { exit 1 }\n"
            "}\n"
            f"Write-Output '{UPSTREAM_GONE}'\n"
        )

    def thurbox_cli(self, args: str) -> str:
        return (HOST_CLI_POWERSHELL.replace("{flavour}", host_cli_flavour()).replace("{args}", args)
                .replace("__NO_CLI__", ps_quote(NO_HOST_CLI)))


POSIX = PosixShell()
POWERSHELL = PowerShell()

# Which shell a platform means, and the platform an entry that names none
# has always meant by its multiplexer. Anything else is refused by
# `host_entry` rather than guessed at.
PLATFORM_SHELLS = {"posix": POSIX, "windows": POWERSHELL}
MULTIPLEXER_PLATFORMS = {"tmux": "posix", "psmux": "windows"}


def host_platform(entry: dict) -> str | None:
    """`posix` or `windows` for a hosts.toml entry, or None when it says neither."""
    platform = entry.get("platform")
    if platform is not None:
        return platform if isinstance(platform, str) and platform in PLATFORM_SHELLS else None
    return MULTIPLEXER_PLATFORMS.get(str(entry.get("multiplexer") or "tmux"))


def host_shell(entry: dict):
    return PLATFORM_SHELLS.get(host_platform(entry) or "posix")


def ssh_text(data: bytes) -> str:
    """What text mode would have handed back: UTF-8, with universal newlines."""
    return data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def ssh_run(entry: dict, script: str, stdin: str | None = None, login: bool = True,
            timeout: int = SSH_TIMEOUT):
    """One command on the host, in the shell it speaks. Never raises; the
    caller reads it.

    A LOGIN SHELL BY DEFAULT on a POSIX host (see LOGIN_SHELL), because what
    almost every caller asks is "what does this host have", and only the
    profile's `PATH` can answer it. The exception is the two calls that MOVE
    BYTES — the brief push and the result fetch — which need no binary beyond
    `cat` and which a profile that prints would corrupt in the middle of. They
    pass `login=False`, and that is the whole rule: a login shell to FIND
    something, a bare one to CARRY something. A PowerShell host has no login
    shell and ignores it.
    """
    try:
        # Bytes both ways: text-mode stdin on a Windows lead turns every LF it
        # sends into CRLF, and a brief would reach a POSIX host that way.
        done = subprocess.run(
            ssh_argv(entry) + [host_shell(entry).command(script, login)],
            input=None if stdin is None else stdin.encode("utf-8"), capture_output=True, timeout=timeout,
        )
        return subprocess.CompletedProcess(done.args, done.returncode, ssh_text(done.stdout), ssh_text(done.stderr))
    except subprocess.TimeoutExpired:
        # Not `str(exc)`: that is the whole argv, and a PowerShell command's
        # argv is kilobytes of base64 that say nothing about what went wrong.
        return subprocess.CompletedProcess(
            args=[], returncode=SSH_CONNECTION_FAILED, stdout="",
            stderr=f"ssh to {entry.get('destination')} gave no answer in {timeout}s",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(
            args=[], returncode=SSH_CONNECTION_FAILED, stdout="", stderr=str(exc)
        )


# GIT HOSTING, NOT THE FORGE API. `scripts/lib/forge.py` answers "what is this
# change request"; this answers "can this machine clone, fetch and push". They
# are different couplings and this is the one that lives here.
#
# IT ASKS THE REPOSITORY'S OWN HOST, and that is the whole point. §1a used to
# name `ssh -T git@github.com` flatly, which proves nothing about a checkout
# whose `origin` is a GitLab instance — a remote GitLab task would pass the
# probe and then fail at its first `git push`. So the script reads the repo's
# `origin` on the host and probes THAT, port and all.
#
# TWO QUESTIONS AND NOT ONE. The ssh probe proves a key. A host that clones
# over HTTPS with a CLI token has no key and is perfectly able to push, so
# testing only the key would refuse a working host. Either credential passes;
# neither is read, moved, or reported beyond the word that says which was
# found. The banners are GitHub's and GitLab's own — the two forges fleet
# ships adapters for — because `ssh -T` exits non-zero on a successful GitHub
# authentication, so the exit status cannot be the test.
#
# ITS FAILURE SAYS WHICH FORGE CLIs IT FOUND, after the `host|tools` separator,
# because "installed and not logged in" and "not installed at all" are
# different problems with different remedies — and because the second is what a
# non-login `PATH` used to manufacture about a host that had both.
FORGE_PROBE_TEMPLATE = """\
url=$(git -C __REPO__ remote get-url origin 2>/dev/null || printf '')
case "$url" in
*://*) rest=${url#*://}; rest=${rest#*@}; hostport=${rest%%/*} ;;
*@*:*) rest=${url#*@}; hostport=${rest%%:*} ;;
*) hostport='' ;;
esac
host=${hostport%%:*}
port=''
case "$hostport" in *:*) port=${hostport#*:} ;; esac
if [ -z "$host" ]; then
	printf 'no forge: %s has no readable `origin`, so there is no host to prove a credential against' __REPO__
	exit 1
fi
if [ -n "$port" ]; then
	banner=$(ssh -o BatchMode=yes -p "$port" -T "git@$host" 2>&1)
else
	banner=$(ssh -o BatchMode=yes -T "git@$host" 2>&1)
fi
case "$banner" in
*'successfully authenticated'*|*'Welcome to GitLab'*)
	printf '%s with an ssh key' "$host"
	exit 0
	;;
esac
tools=''
if command -v gh >/dev/null 2>&1; then
	if gh auth status --hostname "$host" >/dev/null 2>&1; then
		printf '%s with a gh token' "$host"
		exit 0
	fi
	tools='`gh`'
fi
if command -v glab >/dev/null 2>&1; then
	if glab auth status --hostname "$host" >/dev/null 2>&1; then
		printf '%s with a glab token' "$host"
		exit 0
	fi
	tools="${tools:+$tools and }\\`glab\\`"
fi
printf '%s|%s' "$host" "$tools"
exit 1
"""


def forge_probe(repo: str) -> str:
    """The credential probe, for one repository path on the host."""
    return FORGE_PROBE_TEMPLATE.replace("__REPO__", shlex.quote(repo))


def went_away(proc, out: list) -> bool:
    """Did ssh itself fail on a probe after the first? Records it if so.

    A PROBE'S ANSWER AND A LOST CONNECTION ARE DIFFERENT SENTENCES, and reading
    the second as the first is how a reachable host gets written off. Every
    probe below the first can fail because the machine went away mid-sweep, and
    ssh's own reserved 255 is the only thing that tells that apart from the
    question actually being answered `no`.
    """
    if proc.returncode != SSH_CONNECTION_FAILED:
        return False
    out.append({"check": "reachable", "ok": False, "detail": (
        "the host answered the first probe and then stopped answering ssh "
        f"({first_line(proc) or 'ssh could not connect'}). Nothing was learned "
        "about the repo or its credentials; this is the CONNECTION, not an answer.")})
    return True


def probe_host(entry: dict, repo: str) -> list:
    """§1a's questions, stopping at the first NO.

    The repository is asked about BEFORE its forge, because which forge to
    prove a credential against is a fact about that checkout's `origin` — a
    remote task on a GitLab repository needs a GitLab credential, and asking
    github.com about it is how one used to pass the probe and then fail at its
    first `git push`.

    Returns one {check, ok, detail} per probe run. Nothing is spawned until
    every one of them passes: a remote worker that starts and then fails at its
    first `git` call looks exactly like an agent bug and is not one, and the
    lead pays for the difference in reading a pane on another machine.
    """
    out: list = []
    shell = host_shell(entry)

    reach = ssh_run(entry, shell.reach())
    if reach.returncode == SSH_CONNECTION_FAILED and shell.sentinel not in reach.stdout:
        out.append({"check": "reachable", "ok": False,
                    "detail": first_line(reach) or "ssh could not connect"})
        return out
    if shell.sentinel not in reach.stdout:
        out.append({"check": shell.name, "ok": False, "detail": (
            f"the host answered ssh but did not print the {shell.label} sentinel "
            f"({first_line(reach) or 'no output'}). hosts.toml gives it the "
            f"`{entry.get('multiplexer') or 'tmux'}` multiplexer, so fleet spoke "
            f"{shell.label} to it; if its sshd runs another shell, that entry is wrong.")})
        return out
    out.append({"check": "reachable", "ok": True, "detail": f"answers ssh, {shell.label}"})

    seen = ssh_run(entry, shell.repo_check(repo))
    if went_away(seen, out):
        return out
    if seen.returncode != 0:
        why = {
            "no-dir": f"{repo} does not exist on that host",
            "no-git": f"{repo} exists on that host but is not a git checkout",
        }.get(last_out(seen), first_line(seen) or "could not be checked")
        out.append({"check": "repo", "ok": False, "detail": (
            f"{why}. `--repo` is a path on the HOST for a remote task, and "
            "nothing local validates it.")})
        return out
    out.append({"check": "repo", "ok": True, "detail": f"{repo} is a git checkout there"})

    creds = ssh_run(entry, shell.forge_probe(repo))
    if went_away(creds, out):
        return out
    if creds.returncode != 0:
        # The probe names the host it tried, so a machine with a key for one
        # forge and none for the other says WHICH — which is the whole reason
        # this asks the repository rather than a constant. It also names which
        # forge CLIs it FOUND, so "installed and not logged in" reads
        # differently from "not installed at all" — the second is what a
        # non-login `PATH` used to manufacture, and both are answers from a
        # host that is up, which is the sentence the lead needed and did not
        # have.
        out.append({"check": "forge", "ok": False, "detail": _no_forge_credential(creds, shell)})
        return out
    out.append({"check": "forge", "ok": True,
                "detail": f"reaches {last_out(creds) or 'its forge with a credential'}"})
    return out


def _no_forge_credential(creds, shell) -> str:
    """What a failed forge probe says, in words that name the host's own gap."""
    said = last_out(creds)
    if said.startswith("no forge: "):
        return said[len("no forge: "):]
    tried, _, tools = said.partition("|")
    tried = tried or "its forge"
    login = "login shell's " if shell is POSIX else ""
    found = (
        f"{tools} is installed there and not logged in to it" if tools
        else f"neither `gh` nor `glab` is on its {login}PATH"
    )

    return (
        f"the host ANSWERED, and it has no credentials of its own for "
        f"{tried}: no ssh key, and {found}. It cannot clone, fetch or push. "
        "Give that MACHINE its own credentials; nothing here sends yours."
    )


def host_reachable(name: str) -> tuple[bool, str]:
    """Is this host answering right now? (ok, the reason it is not).

    THE FACT THAT MAKES THIS NECESSARY. thurbox has an `unreachable` state and
    its CLI never produces it — that word reaches the interface's session rows
    and nothing else. `session get --json` on a session whose host is down
    answers with the LATCHED hook state instead, so a worker that last reported
    `idle` before its machine went away still reads `idle` hours later. Nothing
    downstream can tell that apart from an agent at rest, and `reap` deletes
    sessions that are at rest. So a remote session is asked about its HOST, not
    only about its state, before anything is deleted — and `unreachable` is a
    word this file derives rather than one it waits to be told.
    """
    entry, why = host_entry(name)
    if not entry:
        return False, why
    shell = host_shell(entry)
    probe = ssh_run(entry, shell.reach())
    if shell.sentinel in probe.stdout:
        return True, ""
    return False, first_line(probe) or "ssh could not connect"


def session_worktree(sid: str) -> tuple[str, str]:
    """The worktree thurbox made for this session. (path, reason it could not say).

    For a remote session this is a path on the HOST — thurbox mints it there —
    which is exactly what the brief copy and the result fetch need.
    """
    doc, why = session_doc(sid)
    if doc is None:
        return "", why
    trees = doc.get("worktrees") or []
    path = str((trees[0] or {}).get("worktree_path") or "") if trees else ""
    return (path, "") if path else ("", "thurbox reported no worktree for that session")


def push_brief(entry: dict, dest: str, text: str) -> str:
    """Write text to a path on the host over ssh. '' on success.

    Used for the brief itself and its companions (PROMPT.md, POLICY.md,
    OPERATOR.md): the canonical copy of each stays HERE — the brief is what the
    lead wrote, what `check` validates and what `dispatch` refuses when it is
    unwritten. What lands on the host is a copy, made after that refusal has
    already had its say.
    """
    script, payload = host_shell(entry).write(dest, text)
    proc = ssh_run(entry, script, stdin=payload, login=False)
    if proc.returncode != 0:
        return first_line(proc) or f"could not write {dest} on the host"
    return ""


def fetch_result(entry: dict, src: str) -> tuple[str | None, str]:
    """The worker's result.md, read off the host. (text, reason).

    None is 'not there yet, or could not be read' — the same answer a local task
    gives when the file does not exist, and it closes nothing either way. A
    result that cannot be fetched must never be able to look like a task that
    concluded, for the same reason a timeout cannot manufacture a merge.
    """
    shell = host_shell(entry)
    proc = ssh_run(entry, shell.read(src), login=False)
    if proc.returncode != 0:
        return None, first_line(proc) or "no result.md on the host yet"
    text = shell.decode(proc.stdout)
    if text is None:
        return None, "the host answered, but not with the file's bytes"
    return text, ""


def settle_remote(task: Task, entry: dict) -> tuple[bool, str]:
    """Resolve the host-side worktree and put the brief beside it. (ok, what happened).

    Runs between `session create` and the first `session send`, because the
    worker is about to be told to read a file that does not exist yet.

    Everything goes in `<worktree>.fleet/` (COMPANION_SUFFIX), private to the
    account, and the brief names its companions as siblings of itself — see
    render_brief: PROMPT.md, POLICY.md, OPERATOR.md when the operator has one,
    and CONTEXT.md, the project memory rendered HERE because the host has no
    `fleet context` to run. The worker writes result.md there too, so its
    worktree holds nothing but its own work.
    """
    wt, why = session_worktree(task.doc["session"])
    if not wt:
        return False, why
    shell = host_shell(entry)
    beside = shell.beside(wt)
    proc = ssh_run(entry, shell.private_dir(beside), login=False)
    if proc.returncode != 0:
        return False, (f"could not make {beside} on {entry['destination']}: "
                       f"{first_line(proc) or 'no output'}")
    # The host's half of what `attach` does here: a re-dispatch on the same
    # branch gets the same worktree, and the previous attempt's result.md
    # beside it would be fetched as this attempt's verdict (#258).
    proc = ssh_run(entry, shell.set_aside(shell.join(beside, RESULT_FILE),
                                          shell.join(beside, PREVIOUS_RESULT_FILE)), login=False)
    if proc.returncode != 0:
        return False, (f"could not move the previous result.md aside in {beside} on "
                       f"{entry['destination']}: {first_line(proc) or 'no output'}")
    repo = remote_identity(entry, str(task.doc["repo"]))
    files = [
        (BRIEF_FILE, read_text(task.file(BRIEF_FILE))),
        (PROMPT_FILE, read_text(os.path.join(os.path.dirname(task.path), PROMPT_FILE))),
        ("POLICY.md", read_text(policy_path())),
    ]
    if operator_instructions():
        files.append(("OPERATOR.md", read_text(operator_path())))
    files.append(("CONTEXT.md", _context_module().worker_bundle(repo, task.ref)))
    for name, text in files:
        why = push_brief(entry, shell.join(beside, name), text)
        if why:
            return False, f"could not copy {name} to {entry['destination']}: {why}"
    brief = shell.join(beside, BRIEF_FILE)
    task.doc["remote"] = {
        "host": task.doc["host"],
        "destination": str(entry["destination"]),
        "worktree": wt,
        "companions": beside,
        "files": [name for name, _ in files] + [RESULT_FILE],
        "repo": repo,
        "brief": brief,
        "result": shell.join(beside, RESULT_FILE),
        "at": now(),
    }
    task.save()
    return True, f"brief copied to {entry['destination']}:{brief}"


def remote_identity(entry: dict, repo: str) -> str | None:
    """The name `fleet context` knows a host's checkout by: its `origin`'s, asked of the host.

    None when the host cannot say or its origin is on no forge — a local path
    there means nothing here, so no store on this machine is that repository's.
    """
    proc = ssh_run(entry, host_shell(entry).origin(repo))
    url = last_out_of(proc.stdout) if proc.returncode == 0 else ""
    return _context_module().remote_identity(url) if url else None


def release_companions(task: Task, dry: bool) -> None:
    """Remove the files `settle_remote` put beside a remote worktree, once its session is gone.

    ONLY THOSE FILES, by the names the record lists, and then the directory
    only if that left it empty: anything else in it is somebody else's, and
    `rmdir` refusing is how it is kept. The worktree itself is thurbox's. The
    caller has already found the host reachable; a failure is said once and
    the record dropped, since the session it served no longer exists.
    """
    rec = task.doc.get("remote") or {}
    beside, names = rec.get("companions"), rec.get("files") or []
    if not task.doc.get("host") or not beside:
        return
    if dry:
        print(f"    {task.ref:<46} would remove {beside}")
        return
    entry, why = host_entry(str(task.doc["host"]))
    if entry:
        proc = ssh_run(entry, host_shell(entry).remove(str(beside), [str(n) for n in names]), login=False)
        why = "" if proc.returncode == 0 else first_line(proc) or "it is not empty"
    if why:
        print(f"    {task.ref:<46} left       {beside}: {why}")
    else:
        print(f"    {task.ref:<46} removed    {beside}")
    rec.pop("companions", None)
    task.save()


def pull_remote_result(task: Task) -> str:
    """Fetch a remote worker's result.md into this task's own. '' when there is
    nothing to say.

    This is the whole of the remote completion model: after it runs, the task
    has a local result.md and everything downstream reads it without knowing or
    caring which machine wrote it.
    """
    rec = task.doc.get("remote") or {}
    src = rec.get("result")
    if not src:
        return "dispatched to a host but no remote worktree was ever recorded"
    entry, why = host_entry(str(task.doc.get("host") or ""))
    if not entry:
        return f"host {task.doc.get('host')}: {why}"
    text, why = fetch_result(entry, str(src))
    if text is None:
        return ""  # not there yet is the normal state of a working task
    fleet_platform.write_record(task.file(RESULT_FILE), text)
    return f"result fetched from {rec.get('destination')}:{src}"


# --- dispatch ----------------------------------------------------------------


def read_text(path: str) -> str:
    """A file's content, best effort. A missing one reads as the placeholder.

    What the REMOTE push makes of that: a brief that is not there lands on the
    host as a scaffold, and the worker has nothing to do. `brief_shortfall`
    above is what stops that reaching a host at all, at dispatch.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return BRIEF_PLACEHOLDER


def _profiles_module():
    """Session profiles (`session_profiles.py`), loaded once on first use."""
    return _load_sibling("fleet_session_profiles", "session_profiles.py")


def profile_flags(profile: str) -> list:
    """The agent settings for this task: session-profiles.yaml, and the overlay.

    In-process, and not through a child process: on a machine with no bash
    the old shell call failed, the failure was swallowed here, and the worker
    started without its profile with nothing said
    (tests/queue/test_dialogs.py). A
    profile that is missing or breaks a rule still renders no flags.
    """
    profiles_mod = _profiles_module()
    errors: list[str] = []
    profiles = profiles_mod.load_layers(errors)
    if profiles is None or errors or profile not in profiles:
        return []
    return profiles_mod.render(profiles[profile])


def unknown_profile(profile: str) -> str | None:
    """Why `profile` names no profile, or None — when the profiles load at all.

    `profile_flags` renders nothing for a name it does not know, so a typo at
    `add`, or a profile removed from the overlay since, spawned a worker with
    no profile and said nothing. Profiles that do not load are
    `profile_problem`'s to report, not this.
    """
    profiles_mod = _profiles_module()
    profiles = profiles_mod.load_layers([])
    if profiles is None or profile in profiles:
        return None
    return f"no session profile {profile!r} — known: {', '.join(sorted(profiles))}"


def profile_problem(profile: str | None = None) -> str | None:
    """Why this task's profile cannot be rendered right now, or None.

    The gate validates the tracked file, so on a checkout without the
    operator's gitignored overlay this is always None. The overlay is read by
    no gate, and a rule it breaks would otherwise make `profile_flags` render
    nothing — a `cursor-trusted` task spawned as the default agent — so the
    spawn refuses instead and names the file. A `profile` that is not there at
    all is the same nothing, and is refused the same way.
    """
    profiles_mod = _profiles_module()
    errors: list[str] = []
    if profiles_mod.load_layers(errors) is None:
        return "the session profiles do not load — run `uv run fleet session-flags --check`"
    return "; ".join(errors) or (unknown_profile(profile) if profile else None)


def spawn_uncovered_notice(create: list) -> str | None:
    """What dispatch prints when this spawn has no hook family.

    Same words `session-flags` writes on stderr, derived from the argv that
    actually reaches `session create`. This is the DECLARATION's own sentence,
    and it is what a dry run prints, because a dry run has no session to ask.
    """
    profiles_mod = _profiles_module()
    return profiles_mod.uncovered_notice(create)


def session_reports(doc: dict) -> bool:
    """Is this session's state the agent's own hook talking?

    THE ONE MEASUREMENT THAT OUTRANKS `uncovered: true`. A profile's
    declaration is written when the profile is authored and says what fleet
    expected of an agent; this reads what thurbox is publishing about the
    session that actually started. Where they disagree the live document is
    the fact, because `watch`, `refuel` and `reap` each read that document and
    never the profile — so a session thurbox reports on is one they see,
    whatever the profile declared.

    Measured 2026-09-20 on a cursor-agent worker (2026.09.18-9a7762b) spawned
    by this fleet's own `cursor-trusted` profile:

        hook_reported: true   hook_coverage: none   hook_state: done
        state: done           state_source: hook

    `hook_coverage` is still `none` — thurbox reads coverage against the
    `--command` file stem and knows no `cursor` hook family — and it is
    deliberately NOT read here. Coverage is what thurbox SHIPS; these two
    fields are what the session SAID. An agent whose hooks the operator wired
    themselves is exactly the case where those come apart.

    BOTH fields, and neither alone — but NOT because thurbox routinely
    disagrees with itself. It does not: `hook_reported` is "a hook state was
    stored" and the `hook` source is chosen only when one was, so `true` beside
    `state_source: process` is not a shape thurbox produces. The one place they
    part is a PARKED session, which clears the source and leaves the flag
    standing — and a parked session is not one to announce as reporting. The
    conjunction is also the cheap guard on a document fleet does not control:
    this is another program's JSON, and a field that stops being written must
    fail towards the declaration rather than away from it.
    """
    if not isinstance(doc, dict):
        return False
    return doc.get("hook_reported") is True and str(doc.get("state_source") or "") == "hook"


def spawn_coverage_notice(create: list, session: str) -> str | None:
    """What dispatch prints about coverage once the session EXISTS.

    None when the spawn declared no `uncovered: true` — there is nothing to
    say about a session that named its hook family.

    Otherwise the declaration is checked against the session document, and
    there are THREE answers rather than two, because "fleet could not ask"
    must never read as "fleet measured coverage" and neither may "it has said
    nothing yet" read as "it never will".

    The declaration's own sentence is what an operator acts on — it is why
    they reap by hand — so the one thing it may not do is state as settled
    something fleet has only observed for an instant. A session is asked
    AFTER its brief went out, which is the latest point dispatch holds it and
    later than every hook cursor fires at session start; but an agent whose
    first hook is its first turn would still be silent here, and the sentence
    says which of the two fleet saw.
    """
    declared = spawn_uncovered_notice(create)
    if not declared:
        return None
    doc, why = session_doc(session) if session else (None, "it has no session id")
    if doc is None:
        return f"{declared} — though fleet could not read the session to check: {why}"
    if not session_reports(doc):
        return f"{declared}; it had reported nothing by the time its brief went out"
    return (
        "declared uncovered, but this session REPORTS: thurbox says "
        f"state_source=hook with hook_reported=true (state `{doc.get('state') or '-'}`), "
        "so watch, refuel and reap read it like any covered session"
    )


def brief_target(task: Task) -> str:
    """The path the worker is told to read.

    This queue's own file for a local task. For a remote one, the copy
    `settle_remote` put in the host's worktree — the control plane's path is
    not on that machine and would resolve to nothing there. Before that copy
    exists the answer is a description and not a path, which is what a dry run
    should print and what `prompt` refuses to send.
    """
    rec = task.doc.get("remote") or {}
    if task.doc.get("host"):
        return rec.get("brief") or f"<{task.doc['host']}>:<worktree>{COMPANION_SUFFIX}/BRIEF.md"
    return os.path.abspath(task.file(BRIEF_FILE))


def spawn_field_problems(d: dict) -> list:
    """What in a record `spawn_commands` would trip over, one sentence each.

    The one list both sides read: `check` reports it, and `spawn_commands`
    refuses on it, so a record `check` calls ok is one dispatch can spawn and a
    hand-edited one is a named refusal of that task rather than a traceback
    that ends the whole wave (#254).
    """
    problems = [f"missing {key}" for key in ("title", "repo", "branch", "base") if not d.get(key)]
    for key in ("agent", "profile"):
        if d.get(key) is not None and not isinstance(d[key], str):
            problems.append(f"{key} {d[key]!r} is not text")
    bases = [d.get("base")] + [split_add_repo(str(spec))[1] for spec in d.get("add_repos") or []]
    problems += [why for base in bases if base and (why := base_problem(str(base)))]
    return problems


# THE BASE A WORKTREE BRANCHES OFF IS FETCHED, NOT TRUSTED. thurbox runs `git
# worktree add -b <branch> <path> <base>` with no fetch of its own, and its
# orchestration guide puts that fetch on the driver — this. A checkout's local
# `main` is whatever it was the last time somebody pulled, which on a machine
# that only ever dispatches is days old. So in a checkout with an `origin`, a
# base that could be a branch there is fetched first, and thurbox is handed
# `origin/<base>`, a ref that fetch just wrote — whether or not this checkout
# had ever seen that branch, which a single-branch clone, or a base pushed
# from elsewhere, has not. A commit, a tag this checkout has, a branch
# `origin` answers it does not have, and a checkout with no `origin` are
# handed over as written: there is no origin branch to be stale against.
#
# ANY OTHER FAILED FETCH REFUSES THE TASK, and never falls back to the local
# base: that fallback is the stale tree this exists to stop, chosen silently.
#
# NO UPSTREAM. With `origin/<base>` as its start point, git's default
# `branch.autoSetupMerge` makes the worker's new branch track `origin/<base>`,
# and a worker's plain `git push` then either fails or, under
# `push.default=upstream`, lands on the base itself. A local start point set
# none. So dispatch unsets it in every worktree the spawn reports
# (`drop_upstreams`); thurbox's sync and review read the session's recorded
# base, never the branch's upstream.
#
# The refspec is explicit because `git fetch origin <base>` only updates
# `origin/<base>` where the remote's configured refspec covers that branch; a
# single-branch clone's does not, and would leave the ref there as stale as
# the local branch while the fetch reported success. Written that way, git
# either writes the ref or fails on it.
#
# The fetch runs in a checkout fleet did not write, so it runs none of that
# checkout's hooks, fsmonitor or auto-gc, and recurses into no submodule.
#
# Only what thurbox is handed changes. The record's own `base` is the change
# request's base, and `collect` and the forge keep reading it as written.
FETCH_TIMEOUT = 120
ORIGIN_REFS = "refs/remotes/origin/"
FETCH_DONE = "fleet-fetched"
FETCH_AS_WRITTEN = "fleet-as-written"
FETCH_FAILED = "fleet-fetch-failed"
UPSTREAM_GONE = "fleet-no-upstream"
# What git says when `origin` has no such branch: the one fetch failure that
# means "not a branch there", and not "could not ask".
NO_REMOTE_REF = "couldn't find remote ref"
COMMIT_RE = re.compile(r"[0-9a-f]{7,40}")
# A base reaches a refspec, a POSIX command line and, on a Windows host, a
# git.exe command line PowerShell 5 builds without escaping an embedded `"` —
# so it is a plain ref name or nothing, checked before any of those exists.
BASE_RE = re.compile(r"[A-Za-z0-9._/-]+")


def base_problem(base: str) -> str:
    """Why a written base is not a branch name a fetch may carry, or ""."""
    if base in ("HEAD", "origin/HEAD"):
        return f"base {base!r} names whatever happens to be checked out; name the branch"
    if not BASE_RE.fullmatch(base):
        return f"base {base!r} is not a branch, tag or commit name (letters, digits, `.`, `_`, `/`, `-`)"
    if base.startswith("-") or base.startswith("/") or base.endswith("/") or base.endswith(".lock"):
        return f"base {base!r} cannot start with `-` or `/`, or end with `/` or `.lock`"
    if ".." in base or "//" in base or any(part.startswith(".") for part in base.split("/")):
        return f"base {base!r} has `..`, `//` or a part starting with `.`"
    return ""


def fetch_config(null: str) -> list:
    """The `-c` options every base fetch runs under; `null` is the null device."""
    return ["-c", f"core.hooksPath={null}", "-c", "gc.auto=0", "-c", "core.fsmonitor=false"]


def origin_branch(base: str) -> str:
    """The branch on `origin` a base names: `main`, `origin/main` and either
    spelled as a full ref are all `main`."""
    for prefix in (ORIGIN_REFS, "refs/heads/", "origin/"):
        if base.startswith(prefix):
            return base[len(prefix):]
    return base


def fetch_refspec(name: str) -> str:
    return f"+refs/heads/{name}:{ORIGIN_REFS}{name}"


def git_error(stderr: str) -> str:
    """git's first `fatal:` or `error:` line, else the first line it wrote at all."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    return next((ln for ln in lines if ln.startswith(("fatal:", "error:"))), lines[0] if lines else "")


def fetch_ssh_env(repo: str) -> dict:
    """ssh that cannot ask for a passphrase, unless the operator chose an ssh."""
    if any(os.environ.get(k) for k in ("GIT_SSH_COMMAND", "GIT_SSH")) or git_out(
        repo, ["config", "--get", "core.sshCommand"]
    ).strip():
        return {}
    return {"GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}


def fetch_local_base(repo: str, base: str, name: str) -> tuple[str, str]:
    """(the base to branch off, why `name` could not be fetched) for a checkout here."""
    if not git_out(repo, ["remote", "get-url", "origin"]).strip():
        return base, ""
    if git_out(repo, ["rev-parse", "--verify", "--quiet", f"refs/tags/{base}"]):
        return base, ""
    try:
        done = subprocess.run(
            ["git", "-C", repo, *fetch_config(os.devnull), "fetch", "--quiet", "--no-recurse-submodules",
             "origin", fetch_refspec(name)],
            capture_output=True, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
            timeout=FETCH_TIMEOUT,
            # Nobody is at this terminal to type a password, and a prompt
            # would hold the whole wave until the timeout.
            env={**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
                 **fetch_ssh_env(repo)},
        )
    except subprocess.TimeoutExpired:
        return "", f"no answer in {FETCH_TIMEOUT}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return "", str(exc)
    if done.returncode != 0:
        if NO_REMOTE_REF in done.stderr:
            return base, ""
        return "", git_error(done.stderr) or f"git exited {done.returncode}"
    return f"origin/{name}", ""


def fetch_remote_base(entry: dict, repo: str, base: str, name: str) -> tuple[str, str]:
    """`fetch_local_base`, asked of the host the task runs on."""
    done = ssh_run(entry, host_shell(entry).fetch_base(repo, base, name), timeout=FETCH_TIMEOUT)
    word = last_out(done)
    if word == FETCH_DONE:
        return f"origin/{name}", ""
    if word == FETCH_AS_WRITTEN:
        return base, ""
    return "", git_error(reportable(done.stderr)) or "the host did not say why"


def fetched_bases(task: Task, entry: dict | None = None) -> tuple[list, str]:
    """`task_repos`, each base replaced by the one fetched for it — or the refusal.

    `entry` is the task's host, where its checkouts are; None is this machine.
    """
    problems = spawn_field_problems(task.doc)
    if problems:
        return [], "; ".join(problems) + " — see `fleet queue check`"
    repos = []
    for r in task_repos(task):
        name = origin_branch(r["base"])
        if COMMIT_RE.fullmatch(r["base"]):
            repos.append(r)
            continue
        if entry:
            base, why = fetch_remote_base(entry, r["path"], r["base"], name)
        else:
            base, why = fetch_local_base(r["path"], r["base"], name)
        if why:
            return [], f"{r['path']}: could not fetch origin/{name}: {why}"
        repos.append({**r, "base": base})
    return repos, ""


def fetched_create(create: list, repos: list) -> list:
    """`spawn_commands`' argv with every base `fetched_bases` resolved.

    Each `--add-repo` is spelled `PATH@BASE` then, because inheriting
    `--base-branch` could name an `origin/` a checkout that was not fetched
    lacks, or a written base where one was fetched.
    """
    out = list(create)
    out[out.index("--base-branch") + 1] = repos[0]["base"]
    values = [i + 1 for i, arg in enumerate(out) if arg == "--add-repo"]
    for i, r in zip(values, repos[1:], strict=True):
        out[i] = f"{r['path']}@{r['base']}"
    return out


def drop_upstreams(worktrees, entry: dict | None, required: bool = True) -> str:
    """Unset the upstream of every worktree thurbox reports, or say why not.

    `required` is for an answer that must name the worktrees it cut: none there
    means nobody knows whether a branch tracks the base, which is a refusal.
    """
    paths = [str(w["worktree_path"]) for w in worktrees if isinstance(w, dict) and w.get("worktree_path")] \
        if isinstance(worktrees, list) else []
    if required and not paths:
        return "thurbox reported no worktree, so nothing shows its branch does not track the base"
    for path in paths:
        if entry:
            done = ssh_run(entry, host_shell(entry).drop_upstream(path))
            if last_out(done) != UPSTREAM_GONE:
                return f"{path}: {git_error(reportable(done.stderr)) or 'the host did not say why'}"
            continue
        if not git_out(path, ["rev-parse", "--verify", "--quiet", "@{upstream}"]):
            continue
        try:
            done = subprocess.run(["git", "-C", path, "branch", "--unset-upstream"], capture_output=True,
                                  stdin=subprocess.DEVNULL, text=True, encoding="utf-8", timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"{path}: {exc}"
        if done.returncode != 0:
            return f"{path}: {git_error(done.stderr) or f'git exited {done.returncode}'}"
    return ""


UPSTREAM_KEPT = "its branch may still track the base, where a plain push could land"


def untrack_session(task: Task) -> str:
    """`drop_upstreams` for the worktrees thurbox lists for the task's session now."""
    doc, why = session_doc(task.doc["session"])
    if doc is None:
        return f"could not read the session to check its branch: {why}"
    entry = None
    if task.doc.get("host"):
        entry, why = host_entry(task.doc["host"])
        if not entry:
            return f"host {task.doc['host']}: {why}"
    return drop_upstreams(doc.get("worktrees"), entry, required=False)


def spawn_commands(task: Task) -> tuple[list, str, str]:
    """The `session create` argv, the prompt to send, and THE AGENT IT NAMES.

    The third value is the whole of what `dispatch` records, and it is returned
    rather than re-derived because the two are not the same answer. A profile
    carrying `command` names no agent at all, so the honest record for it is
    `""` and not whatever `task_agent` would have said; and even where they
    agree, a second resolution reads `agent-policy.conf` off the disk and runs
    `git remote get-url origin` again, on the far side of a `session create`
    that takes seconds — so it is a second measurement of a moving thing, not
    the same pure answer a moment later.

    The bases are the record's as written; the spawn that actually runs swaps
    in the fetched ones (`fetched_create`).
    """
    d = task.doc
    problems = spawn_field_problems(d)
    if problems:
        raise QueueError("; ".join(problems) + " — see `fleet queue check`")
    create = [
        "thurbox-cli", "session", "create",
        "--name", session_name(d["title"], worker_glyph()),
        "--repo-path", d["repo"],
        "--worktree-branch", d["branch"],
        "--base-branch", d["base"],
        # `fail` and not the default `allow`: a name already in use here means
        # the lead has lost track of a task, and a twin breaks by-name
        # addressing for both of them permanently.
        "--on-existing", "fail",
    ]
    # Attached as they are, in the order the operator named them. thurbox
    # cuts no worktree and creates no branch for these, so nothing downstream
    # — verification, landing, reaping — has anything to say about them.
    for extra in d.get("add_dirs") or []:
        create += ["--add-dir", extra]
    # The operator's string, `PATH@BASE` included: thurbox owns that syntax,
    # and fleet reinterpreting it is how a second repository ends up branched
    # off something nobody asked for. Only `fetched_create` spells its base out.
    for extra in d.get("add_repos") or []:
        create += ["--add-repo", extra]
    # The whole of what moves a worker to another machine. `--repo-path` above
    # is then a path on THAT machine, which is why nothing here looks for it
    # locally — see `probe_host`, which asks the host instead.
    if d.get("host"):
        create += ["--host", d["host"]]
    problem = profile_problem(d.get("profile") or "default")
    if problem:
        raise QueueError(problem)
    flags = profile_flags(d.get("profile") or "default")
    refusal = agent_policy_refusal(task)
    if refusal:
        # No ref prefix here: every caller already names the task itself
        # (dispatch's "NOT SPAWNED", prompt's own per-task line), and a
        # prefix here duplicated it in the printed message.
        raise QueueError(refusal)
    # A profile carrying `command` replaces `--agent`; thurbox refuses both.
    # `named` stays "" in that case, and a "" is what the record then gets:
    # fleet cannot tell which agent a free command launches, which is the same
    # thing `agent_policy_refusal` says just above.
    named = ""
    if "--command" not in flags:
        # `task_agent` and nothing else: the answer this names is the one
        # `refuel` later judges the worker by, and the two coming apart is the
        # defect this call closes (#117).
        named = task_agent(task)
        if named:
            create += ["--agent", named]
    # NOT ON A REMOTE SPAWN, and there is no way to ask for it there. thurbox
    # validates a parent against the HOST's own backend and refuses one that
    # lives anywhere else ("a session's parent must be on the same host",
    # `spawn_delegated` in thurbox's `src/session_ops/spawn.rs`); the lead is
    # local by construction, so passing it made every `--host` dispatch
    # impossible — the three probes ran, passed, and the spawn then died on a
    # flag that had nothing to do with them. The link is a nicety in the
    # session list and the task record is what actually ties a worker to its
    # lead, so a remote worker simply does not ask for a parent it could not
    # legally have.
    parent = os.environ.get("THURBOX_SESSION")
    if parent and not d.get("host"):
        create += ["--parent", parent]
    create += flags + ["--json"]
    send = f"Read {brief_target(task)} and do what it says."
    return create, send, named


def spawn_failure(exc, proc=None) -> str:
    """What thurbox actually said when `session create` failed.

    NOT just stderr. `thurbox-cli` prints its structured failure on STDOUT —
    `{"error": ...}` under `--json` — and exits non-zero, so a dispatch reading
    only stderr reported `returned non-zero exit status 1` about a session name
    thurbox had already named the fault in. That cost an operator a hand-run of
    the printed `session create` to find out what it meant. Read both streams,
    unwrap thurbox's own `error` field, and fall back to the exception only
    when neither stream said anything — a spawn that failed for a reason
    nobody here anticipated is exactly the case this is for.

    `proc` carries the streams for the failures that are not a non-zero exit:
    a success whose JSON does not parse, or carries no `id`, is thurbox saying
    something unexpected on stdout, and that something is the whole answer.
    """
    source = exc if isinstance(exc, subprocess.CalledProcessError) else proc
    said = []
    for raw in (getattr(source, "stdout", None), getattr(source, "stderr", None)):
        text = raw.decode(errors="replace") if isinstance(raw, bytes) else (raw or "")
        text = text.strip()
        if not text:
            continue
        try:
            loaded = json.loads(text)
        except ValueError:
            loaded = None
        if isinstance(loaded, dict) and loaded.get("error"):
            text = str(loaded["error"])
        said.append(text)
    return "; ".join(said) or str(exc)


def shell_quote(argv: list) -> str:
    return " ".join(shlex.quote(a) for a in argv)


def select_for_dispatch(q: Queue, refs: list, origin: str | None = None,
                        topic: str | None = None) -> list:
    """What this dispatch launches: the tasks named, or the whole ready set.

    NO REF IS THE NORM, and it stays the default: `dispatch` alone sends
    everything nothing is holding, because a queue that runs one task at a time
    is slower than no queue at all. Refs exist for the one thing that had no
    honest spelling. `dispatch` was all-or-nothing, so holding three of five
    ready tasks back could only be done by inventing blockers for the other
    two, and one was recorded with the reason "Operator has not been asked
    whether to run it at all" -- which is not a dependency, does not survive a
    planning pass, and holds that task until someone deletes it by hand.

    Refs record NOTHING. A task left out is still `queued` and still in the
    ready set, so the next bare `dispatch` sends it; "the operator has not said
    yes yet" is a fact about this moment and not a property of the task.

    A named task that is not ready is refused BY NAME, with whatever is
    actually holding it, rather than quietly dropped from the wave.

    `origin` and `topic` SCOPE a bare dispatch, and that is all they do: the
    ready set restricted to tasks recording exactly that `origin` — the same
    comparison `find --origin` makes — or filed under that topic. A bare
    `dispatch` is right for the one actor that keeps the whole queue moving
    and wrong for an origin, which would send every other origin's ready work
    with its own (#227). Refs already name exactly what goes, so a scope beside
    them is two answers to one question and is refused rather than combined.
    """
    if refs and (origin is not None or topic is not None):
        raise QueueError(
            "name tasks OR scope the ready set with --origin / --topic, not both: "
            "refs already say exactly what goes"
        )
    if not refs:
        return [
            t for t in q.ready()
            if (origin is None or t.doc.get("origin") == origin)
            and (topic is None or t.topic == topic)
        ]
    picked, seen = [], set()
    for ref in refs:
        task = q.get(ref)
        if task.ref not in seen:
            seen.add(task.ref)
            picked.append(task)
    refused = []
    for task in picked:
        if q.is_ready(task):
            continue
        held = [
            blocker_view(q, task, b)["line"]
            for b in task.blockers
            if not q.blocker_cleared(b)
        ]
        refused += [f"    {task.ref}: {line}" for line in held] or [
            f"    {task.ref}: {task.state}, and only a queued task is dispatched"
        ]
    if refused:
        raise QueueError(
            "these tasks were named and are not ready to go out:\n"
            + "\n".join(refused)
            + "\nClear what is holding them, or leave them out of the dispatch."
        )
    return sorted(picked, key=lambda t: t.ref)


# Held by the one process dispatching a task, from its readiness re-check to
# the save of `dispatched`. See `cmd_dispatch`.
DISPATCH_LOCK = "dispatch.lock"


def stranded_session(task: Task, named: str, sessions: dict | None) -> tuple[str, dict] | None:
    """The live LOCAL session a dispatch that died left for this task, if any.

    A dispatcher that dies between `session create` and the save of
    `dispatched` leaves a session no record names, and a plain retry then
    failed on `--on-existing fail`'s duplicate name, holding the task where
    only a hand could free it. Everything that makes it THIS task's has to
    match: the name it is spawned under, and a worktree on its branch in EVERY
    repository it spans — the primary and each `--add-repo`. A name alone is a
    title another task may share, and a title and branch alone can belong to a
    session in another repository, which adopting would mark prompted without
    it ever being handed this brief.

    Only ever this machine's sessions: a `--host` task is never adopted, and a
    row thurbox places anywhere else is not one this dispatch could have made.
    `sessions` is the dispatch's one `session_snapshot`, and None — a list
    that could not be read — adopts nothing, so `session create` is then the
    same refusal it always was.
    """
    if task.doc.get("host"):
        return None
    branch = task.doc.get("branch")
    for sid, row in (sessions or {}).items():
        if row.get("name") != named:
            continue
        if session_place(row.get("backend_type") or "") != ("local", ""):
            continue
        trees = [w for w in row.get("worktrees") or []
                 if isinstance(w, dict) and w.get("branch") == branch]
        if all(any(same_checkout(str(w.get("repo_path") or ""), unit["path"]) for w in trees)
               for unit in task_repos(task)):
            return sid, row
    return None


def same_checkout(a: str, b: str) -> bool:
    """Whether two paths on THIS machine name one checkout: as written, or on disk."""
    if not a or not b:
        return False
    if same_path(a, b):
        return True
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def cmd_dispatch(args) -> int:
    q = Queue(queue_root())
    origin = args.origin.strip() if args.origin is not None else None
    ready = select_for_dispatch(q, args.ref, origin, args.topic)

    # The backstop for `add`'s own check, and it says the same thing: WHICH
    # sections, so the answer is in the refusal and not in a file the lead has
    # to go and grep. It fires on a task scaffolded and never written, and on
    # one hand-edited back into a placeholder after `add` accepted it.
    unfilled = [(t, why) for t in ready if (why := brief_shortfall(t.file("BRIEF.md")))]
    if unfilled:
        raise QueueError(
            "these tasks carry a BRIEF.md a worker would have nothing to do "
            "with:\n"
            + "\n".join(
                f"    {t.ref}: {why}\n        {t.file('BRIEF.md')}"
                for t, why in unfilled
            )
            + "\nWrite those sections, or hand the whole brief to `add "
            "--brief-file`."
        )
    if not ready:
        print("dispatch: nothing ready")
        return 0

    if args.ref:
        rest = len(q.ready()) - len(ready)
        print(
            f"dispatch: {len(ready)} named task(s) ready, launching together — "
            "no concurrency cap."
            + (
                f"\n          {rest} other ready task(s) stay queued, and nothing "
                "records that:\n          no ref is the norm, and the next bare "
                "`dispatch` sends them."
                if rest > 0
                else ""
            )
        )
    else:
        scope = " ".join(
            f"{flag} {value}" for flag, value in (("--origin", origin), ("--topic", args.topic))
            if value is not None
        )
        print(
            f"dispatch: {len(ready)} task(s) ready"
            + (f" in scope {scope}" if scope else "")
            + ", launching together — no "
            "concurrency cap,\n          because every one of them has no "
            "recorded blocker left."
        )

    if args.dry_run:
        print("    each base is fetched first, and handed over as origin/<base> wherever"
              " the checkout has an origin")
        dry_failures = 0
        for t in ready:
            try:
                create, send, _agent = spawn_commands(t)
            except QueueError as exc:
                print(f"    {t.ref}: NOT SPAWNED — {exc}", file=sys.stderr)
                dry_failures += 1
                continue
            print(f"    {t.ref}")
            shell = host_shell(host_entry(t.doc["host"])[0] or {}) if t.doc.get("host") else None
            if shell:
                print(f"      on host {t.doc['host']} — probed first, and not spawned"
                      " until all three pass:")
                print(f"        reachable and speaking {shell.label} / the repo is there"
                      " / it has its own credentials for that repo's forge")
            print(f"      {shell_quote(create)}")
            if notice := spawn_uncovered_notice(create):
                print(f"      {notice}")
            if shell is POSIX:
                print(f"      ssh <host> 'cat > <worktree>{COMPANION_SUFFIX}/BRIEF.md'   # the worker's"
                      " filesystem is not this one; its companions go beside it")
            elif shell:
                print("      ssh <host> powershell -EncodedCommand <BRIEF.md, as base64,"
                      f" into <worktree>{COMPANION_SUFFIX}\\BRIEF.md>   # the worker's filesystem is not this one")
            print("      uv run fleet session-trust <uuid>   # answer the trust dialog first")
            print(f"      thurbox-cli session send <uuid> {shell_quote([send])}")
        return 1 if dry_failures else 0

    # Phase 1: create every session back to back, before any of them is kept
    # waiting on a trust dialog. That is what makes "launched together" true —
    # a whole wave against one repo draws its dialogs simultaneously only if
    # session creation for task 2 does not wait on task 1's trust confirmation.
    attached: list[Task] = []
    # Refs whose worktrees this dispatch has already left tracking nothing, so
    # phase 2 does not ask the host about them a second time.
    untracked: set = set()
    # Every task that HAS a session, whether or not it was prompted: coverage
    # is reported off these at the end of phase 2 rather than here, because
    # `session create` returns before the agent's own first hook has fired and
    # a document read now would call a reporting session silent.
    spawned: list[tuple[Task, list]] = []
    failures = 0
    listed: list | None = None
    for t in ready:
        # ONE DISPATCHER PER TASK, from the readiness re-check to the save of
        # `dispatched`. `session create` takes seconds, so two actors that both
        # read `queued` both spawned, and the second was stopped only by
        # `--on-existing fail` refusing a duplicate name. The lock is a file
        # the OS releases however its holder dies, so it is never stale and
        # trusts no pid — the reconciler supervisor's own argument — and the
        # task stays `queued` throughout: no state word, no timeout. It is
        # taken BEFORE the record's own lock (`Task.save`, milliseconds) and
        # never after it, which is the one order both can be held in.
        with contextlib.ExitStack() as held:
            try:
                held.enter_context(
                    fleet_platform.exclusive_lock(t.file(DISPATCH_LOCK), wait=0)
                )
            except fleet_platform.LockHeld:
                print(f"    {t.ref}: being dispatched by another process")
                continue
            # The view was read before the lock, and a dispatcher that held it
            # since may have saved `dispatched`. Only the disk says.
            t = Task(t.topic, t.id, t.path, *read_record(t.file("task.yaml")))
            if not q.is_ready(t):
                print(f"    {t.ref}: {t.state} now, and only a queued task is dispatched — skipped")
                continue
            # A remote task is probed BEFORE it is spawned, in §1a's order, and one
            # failed probe stops it there. A remote worker that starts and then
            # fails at its first `git` call looks exactly like an agent bug and is
            # not one — and the lead pays for that difference by reading a pane on
            # another machine. The task stays `queued`, so fixing the host and
            # re-running `dispatch` sends it.
            entry = None
            if t.doc.get("host"):
                entry, why = host_entry(t.doc["host"])
                if not entry:
                    print(f"    {t.ref}: NOT SPAWNED — host {t.doc['host']}: {why}",
                          file=sys.stderr)
                    failures += 1
                    continue
                probes = probe_host(entry, t.doc["repo"])
                for p in probes:
                    mark = "ok  " if p["ok"] else "FAIL"
                    print(f"    {t.ref}  probe {mark} {p['check']}: {p['detail']}",
                          file=None if p["ok"] else sys.stderr)
                if not probes[-1]["ok"]:
                    print(f"    {t.ref}: NOT SPAWNED — the `{probes[-1]['check']}` probe "
                          f"failed on host {t.doc['host']}", file=sys.stderr)
                    failures += 1
                    continue

            try:
                create, _send, spawned_as = spawn_commands(t)
            except QueueError as exc:
                print(f"    {t.ref}: NOT SPAWNED — {exc}", file=sys.stderr)
                failures += 1
                continue
            # A session already spawned for this task and never recorded is
            # bound the way `attach` binds one, not spawned a twin: busy or
            # done means it was briefed, and idle stays unprompted for phase 2.
            # One `session list` per dispatch, read at the first local task:
            # a session a dispatcher that died left is there before this run
            # began, and one that dies mid-run is repaired by the next dispatch.
            if listed is None and not t.doc.get("host"):
                listed = [session_snapshot()[0]]
            stranded = stranded_session(t, create[create.index("--name") + 1],
                                        listed[0] if listed else None)
            if stranded:
                sid, row = stranded
                state, _why = session_state(sid)
                prompted = state in SESSION_BUSY | {"done"}
                attach(t, sid, spawned_as if row.get("agent") == spawned_as else "",
                       prompted=prompted)
                print(f"    {t.ref}: adopted {sid}, which a dispatch that did not "
                      "finish left behind" + ("" if prompted else "  (unprompted)"))
                why = drop_upstreams(row.get("worktrees"), None)
                if why:
                    print(f"    {t.ref}: {UPSTREAM_KEPT}: {why}", file=sys.stderr)
                    failures += 1
                    continue
                untracked.add(t.ref)
                if not prompted:
                    attached.append(t)
                continue
            # Per task and as late as its spawn — after an adoption, which needs
            # no network, and never once for the whole wave, whose last task
            # would branch off a fetch minutes old.
            repos, why = fetched_bases(t, entry)
            if why:
                print(f"    {t.ref}: NOT SPAWNED — {why}", file=sys.stderr)
                failures += 1
                continue
            create = fetched_create(create, repos)
            proc = None
            try:
                proc = subprocess.run(create, capture_output=True, check=True)
                created = json.loads(proc.stdout)
                session = created["id"]
            except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError) as exc:
                print(f"    {t.ref}: spawn failed: {spawn_failure(exc, proc)}", file=sys.stderr)
                failures += 1
                continue
            # What the spawn ACTUALLY named, carried out of `spawn_commands` and
            # never resolved a second time: the record is a claim about a session
            # that now exists, and a second reading of the policy and of `origin`
            # on the far side of `session create` can answer differently.
            attach(t, session, spawned_as)
            spawned.append((t, create))
            why = drop_upstreams(created.get("worktrees"), entry)
            if why:
                print(f"    {t.ref}: session exists but was NOT prompted — {UPSTREAM_KEPT}: {why}",
                      file=sys.stderr)
                failures += 1
                continue
            untracked.add(t.ref)

            # The remote worker is about to be told to read a file that is not on
            # its filesystem. Put it there first, and record where — `collect`
            # fetches the result back from beside it.
            if entry:
                ok, note = settle_remote(t, entry)
                print(f"    {t.ref}  {note}", file=None if ok else sys.stderr)
                if not ok:
                    print(f"    {t.ref}: session exists but was NOT prompted — the brief "
                          "never reached the host", file=sys.stderr)
                    failures += 1
                    continue
            attached.append(t)

    # Phase 2: the session exists, but the agent may be sitting on a trust
    # dialog: thurbox mints a fresh worktree path per session and most agents
    # ask about a directory they have not seen. Sending the brief now would
    # type it INTO that dialog, which is how every fleet-spawned worker used
    # to break.
    unprompted: list = []
    for t in attached:
        ok, report = prompt_session(t, untracked=t.ref in untracked)
        print(f"    {t.ref}  -> {t.doc['session']}{'' if ok else '  NOT PROMPTED'}")
        if report:
            print(f"        {report}", file=None if ok else sys.stderr)
        if not ok:
            unprompted.append(t.ref)

    # Coverage, last: the declaration is only half the answer, and the other
    # half is a session that has now had its trust dialog answered and its
    # brief typed — which is the latest point dispatch holds it.
    for t, create in spawned:
        if notice := spawn_coverage_notice(create, t.doc.get("session") or ""):
            print(f"    {t.ref}: {notice}")

    if unprompted:
        failures += len(unprompted)
        print(
            f"\n{len(unprompted)} session(s) exist but were NOT prompted. Look at the\n"
            "pane, then retry the handoff — nothing was typed into them:\n"
            "    uv run fleet queue prompt",
            file=sys.stderr,
        )
    refresh_run_logs(q)
    return 1 if failures else 0


def attach(task: Task, session: str, agent: str = "", prompted: bool = False) -> None:
    """Bind a session to a task, and record the agent that session runs.

    `prompted` is whether that session already has its work. Dispatch binds a
    session it has just created and not yet briefed, so false; `fleet queue
    attach` binds one somebody else started and says what thurbox reports of
    it (`cmd_attach`), because the bare `prompt` sweep types the ORIGINAL brief
    into every dispatched task that reads unprompted.

    `agent` is the string that actually reached `session create`'s `--agent`,
    carried out of `spawn_commands` rather than resolved again here. It is
    EMPTY in two cases and neither is an omission: `fleet queue attach` binds a
    session fleet did not create and has nothing to say about one, and a
    profile carrying `command` names no agent at all — fleet cannot tell what a
    free command launches, so "" is the honest record and a guess would be a
    claim about a process nobody identified.

    Writing it at all is what makes the record the one answer both sides read
    (#117): before it, `add` recorded only an agent the operator had NAMED, and
    a task that named none left every later reader to re-derive its own.
    """
    # A NEW ATTEMPT STARTS WITH NO RESULT. A task put back to `queued` by hand
    # keeps the previous attempt's result.md, and once this marks it
    # dispatched `collect` would read that file as this attempt's verdict
    # (#258). It is moved aside, not deleted: it is still the record of what
    # the last attempt said.
    stale = task.file(RESULT_FILE)
    if task.state == "queued":
        if os.path.exists(stale):
            os.replace(stale, task.file(PREVIOUS_RESULT_FILE))
        # And where the last attempt's result was fetched from: `settle_remote`
        # records this attempt's, so nothing pulls the old one in between.
        task.doc.pop("remote", None)
    if agent:
        task.doc["agent"] = agent
    task.doc["session"] = session
    task.doc["state"] = "dispatched"
    task.doc["dispatched_at"] = now()
    task.doc["prompted"] = prompted
    # Where the stream resumes FOR THIS TASK, fixed at the instant it attaches
    # so the first `watch` after a dispatch starts from the dispatch and not
    # from 0 (replays the whole backlog) or from "now" (drops the gap). Set
    # once: a task re-dispatched into a fresh session keeps the older floor,
    # which replays harmlessly and can never skip.
    if task.doc.get("watch_from") is None:
        high = stream_high_water()
        if high is not None:
            task.doc["watch_from"] = high
    task.save()


def prompt_session(task: Task, timeout: int = 20, untracked: bool = False) -> tuple[bool, str]:
    """Get the session past its trust dialog, then send it its brief.

    Split out of dispatch because it is the retry path too. A session that
    could not be confirmed past the dialog is left recorded and UNPROMPTED
    rather than silently sent a brief that would land in the dialog — the
    record says `prompted: false` and `fleet queue prompt` picks it up again.

    `untracked` is a caller that has just left the session's worktrees
    tracking nothing; every other caller has that done here first, so no
    route briefs a worker whose branch still tracks the base.
    """
    session = task.doc.get("session")
    if not session:
        return False, "no session attached"
    if not untracked and (why := untrack_session(task)):
        return False, f"{UPSTREAM_KEPT}: {why}"

    # A remote task whose brief never reached the host has nothing to point a
    # worker at. Sending the control plane's own path would have it read a file
    # that is not on its filesystem, which is a worker that stalls with no
    # visible reason — the one outcome this whole path exists to avoid.
    if task.doc.get("host") and not (task.doc.get("remote") or {}).get("brief"):
        entry, why = host_entry(task.doc["host"])
        if not entry:
            return False, f"host {task.doc['host']}: {why}"
        ok, note = settle_remote(task, entry)
        if not ok:
            return False, f"the brief is not on the host: {note}"

    try:
        _, send, _agent = spawn_commands(task)
    except QueueError as exc:
        # A policy refusal here is not a spawn failure to crash the whole
        # retry batch over: the task stays `dispatched, prompted: false`, and
        # `fleet queue prompt` picks it up again once the policy or the
        # task's recorded agent changes.
        return False, str(exc)
    ok, report = trust_and_send(
        session, send, timeout, verify=needs_verified_submit(task)
    )
    if not ok:
        if task.doc.get("host"):
            report += (
                f"\n(this worker is on host {task.doc['host']}: "
                "`fleet trust-thurbox-dir` seeds THIS machine's ~/.claude.json "
                "and would do nothing for it.)"
            )
        return False, report
    task.doc["prompted"] = True
    task.save()
    return True, report


def cmd_prompt(args) -> int:
    q = Queue(queue_root())
    if args.ref:
        targets = [q.get(args.ref)]
    else:
        targets = [
            t
            for t in sorted(q.tasks.values(), key=lambda t: t.ref)
            if t.state == "dispatched" and not t.doc.get("prompted")
        ]
    if not targets:
        print("prompt: every dispatched task has already been prompted")
        return 0
    failures = 0
    for t in targets:
        ok, report = prompt_session(t, args.timeout)
        print(f"    {t.ref}  {'prompted' if ok else 'NOT PROMPTED'}")
        if report:
            print(f"        {report}", file=None if ok else sys.stderr)
        failures += 0 if ok else 1
    return 1 if failures else 0


def cmd_attach(args) -> int:
    """Record a session somebody spawned, and whether it already has its work.

    Working, blocked and done imply work has begun, so the bare `prompt` sweep
    must not re-brief them. Idle is ambiguous: a startup hook can report it
    before any work is delivered. It stays unprompted unless `--prompted`
    confirms it already has its work; `--unprompted` forces the reverse.

    It never re-opens a task (#252). Writing `dispatched` over a concluded one
    made the next `collect` close it again on the same result.md and tell its
    origin a second time, where the transitions beside `reap` say nothing
    re-opens. So a `landed` or `abandoned` task is refused, naming its state:
    nothing more will be asked of a session there, and reap would delete it.
    A `done`, `stuck` or `failed` one keeps its state and only takes the new
    session — the one shepherd and a reviewer reach for review fixes.
    `attach()` itself stays unguarded: it is also dispatch's binder, which only
    ever hands it a `queued` task.
    """
    q = Queue(queue_root())
    task = q.get(args.ref)
    if task.state in TERMINAL_STATES:
        raise QueueError(
            f"{task.ref} is {task.state}: attach never re-opens a finished task"
        )
    prompted = args.prompted
    if not args.unprompted and not args.prompted:
        state, _why = session_state(args.session)
        prompted = state in SESSION_BUSY | {"done"}
    if task.state in CONCLUDED_STATES:
        task.doc["session"] = args.session
        task.doc["prompted"] = prompted
        task.save()
        print(f"{task.ref} -> {args.session}  (kept {task.state})")
        return 0
    attach(task, args.session, prompted=prompted)
    print(f"{task.ref} -> {args.session}{'' if prompted else '  (unprompted)'}")
    return 0


# --- watch: the WHEN ---------------------------------------------------------


def read_cursor(root: str) -> int | None:
    """The retired queue-wide `.cursor`, read for one purpose: a task attached
    before the floor moved onto the task has no `watch_from`, and this is the
    only honest lower bound left for it. None means there is none to read,
    distinct from a written 0. Nothing writes this file any more.
    """
    path = os.path.join(root, ".cursor")
    try:
        return int(open(path, encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return None


def watch_command(extra: list) -> list:
    """The stream command, real or a test's recorded-stream override."""
    override = os.environ.get("FLEET_QUEUE_WATCH_CMD")
    if override:
        # Split into argv with the OS's own quoting and nothing else of a shell:
        # there is no `sh` to hand a line to on native Windows.
        try:
            return fleet_platform.split_command(override)
        except ValueError as exc:
            raise QueueError(f"FLEET_QUEUE_WATCH_CMD: {exc}") from exc
    return ["thurbox-cli", "watch", "--json"] + extra


_STREAM_HIGH: list = []


def stream_high_water() -> int | None:
    """The stream's current high-water mark, asked once per process.

    None means the stream could not be READ, which is never the same answer as
    a genuine 0 (a brand-new thurbox instance): a task seeded at 0 replays from
    the beginning and loses nothing, and a task left unseeded falls back
    further still. Collapsing the two would seed a task at "now" on a machine
    with no thurbox-cli and drop everything before its first watch.
    """
    if _STREAM_HIGH:
        return _STREAM_HIGH[0]
    try:
        proc = subprocess.run(
            watch_command(["--initial", "--for-secs", "0"]),
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    high = 0
    for ev in stream_events(proc.stdout):
        high = max(high, int(ev.get("seq") or 0))
    _STREAM_HIGH.append(high)
    return high


def stream_events(blob: bytes):
    """The JSON objects in a stream's output, skipping whatever else it said."""
    for line in blob.decode(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        yield ev


def folded_through(task: Task) -> int | None:
    """The highest sequence number already in this task's own progress.jsonl.

    The record IS the cursor. That is the whole fix: a floor derived from what
    was actually appended cannot run ahead of what was appended, so a run that
    dies part-way through a batch neither skips the events it had not written
    nor re-appends the ones it had.
    """
    high = None
    try:
        fh = open(task.file(PROGRESS_FILE), encoding="utf-8")
    except OSError:
        return None
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                seq = json.loads(line).get("seq")
            except ValueError:
                continue
            # `shepherd` appends to this file too, and its entries carry no
            # sequence number. They are not stream events and never move a
            # floor.
            if isinstance(seq, int):
                high = seq if high is None else max(high, seq)
    return high


def task_floor(task: Task, legacy: int | None) -> int:
    """Where the stream resumes for ONE task. Never shared with another.

    A queue-wide cursor was the bug: `watch` advanced a single number over
    every event it read, folded or not, having decided what to fold from a
    session map snapshotted before the stream was opened. A task dispatched
    inside that window was not in the map, so its transitions were skipped and
    the shared number was written past them — consumed, for a task that was
    never updated. Across 24 topics that emptied 19 of 20 timelines.
    """
    marks = [
        m for m in (folded_through(task), task.doc.get("watch_from"))
        if isinstance(m, int)
    ]
    if marks:
        return max(marks)
    return legacy if legacy is not None else 0


def cmd_watch(args) -> int:
    """Fold thurbox's event stream into the records. Close nothing.

    This is the non-disruptive half of the wake: the lead runs it when it
    chooses, for as long as it chooses, and reads the result. Compare
    `thurbox-cli message send`, which pushes into the recipient's conversation
    the moment a worker calls it.
    """
    root = queue_root()
    q = Queue(root)
    by_session = {t.doc["session"]: t for t in q.tasks.values() if t.doc.get("session")}
    if not by_session:
        print("watch: no task has a session attached yet")
        return 0

    # One floor per task, and the request resumes from the LOWEST of them, so a
    # task nobody has folded yet drags the whole read back far enough to reach
    # its events. Each task then ignores whatever is already below its own
    # floor, which is how the same batch can be a replay for one task and news
    # for another.
    legacy = read_cursor(root)
    floors = {t.ref: task_floor(t, legacy) for t in by_session.values()}
    since = min(floors.values())
    cmd = watch_command(["--for-secs", str(args.for_secs), "--since", str(since)])

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=args.for_secs + 30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QueueError(f"could not read the event stream: {exc}") from exc

    high = since
    touched: dict[str, dict] = {}
    for ev in stream_events(proc.stdout):
        seq = int(ev.get("seq") or 0)
        high = max(high, seq)
        task = by_session.get(ev.get("session"))
        # An event belonging to no task of this queue — the lead's own session,
        # a worker from another clone — moves NOTHING. It used to move the
        # shared cursor, and so did an event whose task was attached after this
        # map was built.
        if task is None or seq <= floors[task.ref]:
            continue
        record_event(task, ev)
        floors[task.ref] = seq
        touched[task.ref] = ev
        print(
            f"    seq {seq}  {task.ref}  "
            f"{ev.get('from_state') or '-'} -> {ev.get('to_state') or ev.get('state') or '-'}"
        )

    print(f"watch: {len(touched)} task(s) moved, stream at seq {high}")
    _print_turn_ends(q, touched)
    return 0


def _print_turn_ends(q: Queue, touched: dict) -> None:
    """Point at the result of every task whose turn just ended — or say there is none."""
    for ref, ev in sorted(touched.items()):
        # A turn ending is the ONLY thing worth a second look, and it is still
        # not a completion — the worker's own file is.
        if (ev.get("to_state") or ev.get("state")) not in ("done", "idle"):
            continue
        task = q.tasks[ref]
        if os.path.exists(task.file(RESULT_FILE)):
            print(f"    {ref}: a result is waiting — read it with `fleet queue collect`")
        else:
            print(
                f"    {ref}: turn ended with no result file yet. That is not a\n"
                "        finished task; leave it be or go and look at the pane."
            )


def record_event(task: Task, ev: dict) -> None:
    """Append one transition. The task's floor is read back out of this file,
    so a write that fails must STOP the run rather than be swallowed: a fold
    that silently did nothing would leave the stream to be replayed, which is
    right, but a caller that kept going would report the task as moved.
    """
    entry = {
        "seq": ev.get("seq"),
        "at": ev.get("at"),
        "from": ev.get("from_state"),
        "to": ev.get("to_state") or ev.get("state"),
        "reason": ev.get("reason"),
        "observed": now(),
    }
    try:
        fleet_platform.append_record(task.file(PROGRESS_FILE), json.dumps(entry) + "\n")
    except OSError as exc:
        raise QueueError(
            f"{task.ref}: could not append to progress.jsonl: {exc}\n"
            "Nothing was lost — the stream is replayed from each task's own\n"
            "record, so fix the path and run `fleet queue watch` again."
        ) from exc


# --- collect: the WHAT -------------------------------------------------------


def parse_result(text: str) -> tuple[dict, str]:
    meta: dict = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            meta = load_yaml(parts[1]) or {}
            body = parts[2]
    if not isinstance(meta, dict):
        meta = {}
    return meta, body.strip()


def record_learned(task: Task, meta: dict) -> None:
    """Record the result's `learned:` entries as fact files, and say so in a line.

    At the moment a task concludes, so a task held open records nothing until
    it closes, and a second pass over the same result makes nothing new:
    `context.py` derives each fact's id from the task and the entry's place,
    and never overwrites one. A bad entry is a line, never a held task.
    """
    if meta.get("learned") is None:
        return
    for line in _context_module().capture_learned(task, meta["learned"]):
        print(f"        {line}")


def task_publish(task: Task) -> tuple[str, str | None]:
    """(method, how) for one task — the resolved default when it declares none.

    A record written before this field existed has no `publish` block, and it
    must keep being verified exactly as it was dispatched. So the fallback is
    the OPERATOR's default from POLICY.md and never the bare `pr` that ships as
    fleet's: an operator whose default is `attested`, so every task already
    in its queue keeps the check it was opened under.

    A record that DOES declare a method declares its `how` with it, absent
    included — the operator's global tool is the wrong sentence to append to a
    task that was deliberately given another method.
    """
    method, how = policy_publish_default()
    block = task.doc.get("publish") or {}
    declared = publish_method(block.get("method"))
    if declared in PUBLISH_METHODS:
        method, how = declared, block.get("how")
    text = str(how).strip() if how else ""
    return method, text or None


NO_CHANGE_RE = re.compile(r"^\s*no change needed\b\s*[:\u2014\u2013-]?\s*(.*)$", re.I | re.S)


def no_change_reason(value) -> str | None:
    """The reason a repository needed nothing, "" for none given, None for not a no-op.

    The per-repository word in `artifacts:` for a task that spans
    repositories: `no change needed — <why>` (a dash, or a quoted colon, since
    an unquoted second colon is not YAML). It verifies on its own, and landing
    never waits on it, since it is no change request.
    """
    match = NO_CHANGE_RE.match(value) if isinstance(value, str) else None
    return match.group(1).strip() if match else None


def publish_verdict(task: Task, outcome, url, unit: dict | None = None) -> tuple[str, str, dict]:
    """Does this artifact prove this REPOSITORY published? Four answers, per method.

        skipped   nothing to check — the outcome does not require an artifact
                  (`not-applicable` or `stuck`), and none, or one of the wrong
                  shape, was given. Or the method is `none`, which names
                  nothing to check.
        passed    the forge, or git, says the artifact this task's method names
                  is there, from this task's branch, in the state claimed.
        missing   `shipped` with no such artifact, or one that does not hold up.
        unknown   the check could not run — no forge configured for that
                  host, no network, no such change request, a base branch this
                  machine cannot read.

    `unknown` is a fourth word on purpose and never collapses into `passed` or
    `missing`. An offline machine and a CI runner with no forge CLI must both
    still be able to collect, and "could not check" must never be reported as either
    verdict — that is how a trusted claim gets manufactured out of a timeout.
    Every git call below goes through `git_out`, whose empty answer IS a
    failure, so `push` keeps that rule as strictly as the two forge methods do.

    A worker that writes `outcome: shipped` is claiming an artifact, so a
    missing or malformed one is not the same silence as `not-applicable` and
    `stuck` legitimately producing none — it is `missing`, held open like any
    other unproven `shipped` claim.

    The third value is fields `collect` writes onto the publish block beside
    the verdict. Only a pull request ever has any; see `pull_request_verdict`.

    `unit` is the repository being asked about — one of `task_repos`. It
    defaults to the primary, so every caller that has only ever had one
    repository to ask about reads exactly as it did.
    """
    unit = unit or task_repos(task)[0]
    method, _how = task_publish(task)
    reason = no_change_reason(url) if len(task_repos(task)) > 1 else None
    if reason is not None and method in PER_REPO_METHODS:
        if not reason:
            return "missing", (
                "`no change needed` must give the reason: `no change needed — <why>`"
            ), {}
        return "passed", f"no change needed in this repository: {reason}", {}
    if method == "push":
        return (*commit_verdict(task, outcome, url, unit), {})
    if method == "note":
        return (*note_verdict(task, outcome, url), {})
    if method == "served":
        # Checked no more than `none` is — and the ONE thing that can be
        # checked without asking anybody is whether a reader was handed an
        # address at all. `shipped` claims a document somebody is expected to
        # answer; without a URL there is nothing to open, and closing it would
        # leave a session held for a reader who was never given the document.
        if not url and outcome == "shipped":
            return "missing", (
                "shipped with no URL for the reader to open; a `served` task's "
                "artifact is where its document is being served"
            ), {}
        # The publish block's own word, and only for a task that recorded an
        # address. It exists to overwrite the `unverified` a held-open pass
        # left behind — which is reachable only WITH a URL — so a task that
        # served nothing writes nothing here and draws no artifact row, exactly
        # as a `none` one does.
        return "skipped", (
            "a `served` task names nothing fleet can check; its artifact is "
            "recorded as given"
        ), ({"state": "served"} if url else {})
    if method == "none":
        return "skipped", (
            "a `none` task names nothing fleet can check; its artifact is "
            "recorded as given"
        ), {}
    return pull_request_verdict(task, method, outcome, url, unit)


def pull_request_verdict(
    task: Task, method: str, outcome, url, unit: dict | None = None
) -> tuple[str, str, dict]:
    """The forge as witness, for the two methods that end in a pull request.

    THE HEAD BRANCH IS CHECKED FOR BOTH, and it costs nothing — the field
    arrives in the same `forge.get`. It closes the one hole no body check ever
    closed: a worker pasting somebody ELSE's good pull request. "This pull
    request is from this task's branch" is the one claim about it that a worker
    cannot write into its own result.md.

    `attested` then asks for the attestation rather than for headings in the
    prose. Same class of evidence — the tool's own trace in the body — and
    strictly stronger, because it names the head commit the pipeline ran on and
    a stale one is refused. It is also the check the shepherd already makes, so
    the two commands now agree about what proves a pipeline ran.

    The commit list comes back in the same call and is read only to WORD the
    refusal — `pipeline_moved_the_head` tells the one stale attestation the
    pipeline caused itself apart from every other. It cannot change a verdict.

    A TARGET REPLACES THE BRANCH. A task that works on a pull request it did not
    open — a contributor's, from a fork — cannot share that pull request's head
    branch, because thurbox will not cut a worktree on a branch already checked
    out. So `add --target` records WHICH change request, and that is compared
    instead. It is still nothing a worker can write for itself: the lead typed
    it at intake, before any worker existed.

    A MERGE ENDS THE ATTESTATION'S QUESTION. An attestation answers "may this
    merge", and whoever merged a pull request the forge reports merged has
    answered that. Holding the task open on a stale one held it open for good,
    because landing only ever sweeps a task that concluded. So it concludes,
    and the attestation that did not hold is kept on the publish block as
    `attestation` — a note on the record, never a hold. Who MAY merge an
    unattested pull request is the shepherd's gate, and nothing here touches it.

    A BRANCH NAME IS NOT A REPOSITORY. `fix/x` exists in any number of them, and
    every repository a task spans shares the task's branch — so the head branch
    alone let a pull request in some other repository verify, and let one URL
    written for every `--add-repo` verify all of them. So the change request's
    repository is compared with the one this repository's checkout names
    (`unit_repo`), and, for the task's own branch, its base with the base the
    worktree was cut from — or, in a repository `flow.conf` gives an
    integration branch, onto that branch, which is where its feat→develop
    change request goes by design. A change request FROM the integration branch
    targets the next branch along, so its base is `integration_verdict`'s
    business and not compared here. Where the checkout's repository cannot be
    read — no `origin`, a local-only fleet, another machine — nothing is
    guessed and the branch check stands alone, exactly as it always did.

    The third value is those fields for the publish block: `state`, when this
    check knows better than `collect_publish_state` does, and that note.
    """
    if not forge.change_url(url):
        if outcome == "shipped":
            return "missing", "shipped with no pull request to check", {}
        return "skipped", "no pull request to check", {}
    which, ref = forge.for_url(url)
    if which is None:
        return "unknown", ref, {}
    cr, why = which.get(ref)
    if why:
        return "unknown", why, {}

    # A `--target` names ONE change request, and it is the primary
    # repository's by construction: a task that works on somebody else's pull
    # request works on that one. Every other repository a task spans opens its
    # own change request from the task's branch and is compared against it,
    # exactly as a task with no target always was.
    named, target, why = task_target(task) if (unit or {}).get("primary", True) else ("", None, "")
    if named:
        stop, whose, own = _target_match(cr, named, target, why)
    else:
        stop, whose, own = _branch_match(task, cr, unit or task_repos(task)[0])
    if stop is not None:
        return stop
    return _merge_state_verdict(cr, method, whose, own)


def _target_match(cr: forge.ChangeRequest, named: str, target, why: str) -> tuple:
    """`pull_request_verdict` for a task with a `--target`: (refusal or None, whose, own)."""
    if target is None:
        return ("unknown", why, {}), "", True
    if not target.same(forge.Target(cr.repo, cr.number, "change")):
        return ("missing", (
            f"the pull request is {cr.url}, and this task's target is {named}"
        ), {}), "", True
    return None, "is this task's target", True


def _branch_match(task: Task, cr: forge.ChangeRequest, unit: dict) -> tuple:
    """`pull_request_verdict` for a task's own branch: (refusal or None, whose, own)."""
    mine = unit_repo(task, unit)
    if mine is not None and not same_repo(mine, cr.repo):
        return ("missing", (
            f"the pull request is in {cr.repo.qualified}, and this repository's "
            f"checkout {unit['path']} is {mine.qualified}"
        ), {}), "", True
    branch = str(task.doc.get("branch") or "")
    if not cr.head_branch:
        return ("unknown", "the forge did not say which branch this pull request is from", {}), "", True
    whose = f"is from {branch}"
    integration = longest_prefix_rule(cr.repo, integration_branches())
    # In a repository with an integration branch, the task's own change
    # request goes feat→develop by design, whatever base it was cut from.
    bases = {forge_base(task, unit)} | ({integration[0]} if integration else set())
    if cr.head_branch == branch and cr.base_branch and cr.base_branch not in bases:
        return ("missing", (
            f"the pull request is from {branch} onto {cr.base_branch}, and this "
            f"repository's work is cut from and lands on {unit['base']}"
        ), {}), "", True
    if cr.head_branch == branch:
        return None, whose, True
    if integration is None or integration[0] != cr.head_branch:
        return ("missing", (
            f"the pull request is from branch {cr.head_branch}, and this task's "
            f"is {branch}; a task that works on a change request it did not "
            "open names it with `add --target`"
        ), {}), "", True
    verdict, whose = integration_verdict(task, cr, branch, unit)
    if verdict != "passed":
        return (verdict, whose, {}), "", False
    return None, whose, False


def _merge_state_verdict(cr: forge.ChangeRequest, method: str, whose: str,
                         own: bool) -> tuple[str, str, dict]:
    """`pull_request_verdict` once the change request is this task's: its state."""
    # CLOSED UNMERGED IS AN END, NOT A GAP. Nothing more will happen on that
    # change request, so holding the task open for a publish that can never
    # verify left it `dispatched` until somebody abandoned it by hand. The
    # `closed` state is what `collect` reads to retire it in its own words.
    # Only the task's OWN change request — its branch's, or its `--target`. A
    # closed integration one (develop→main) carries work already on develop,
    # so it keeps the plain refusal below rather than ending the task.
    if cr.state == "closed" and own:
        return "missing", f"{cr.url} was closed without merging", {"state": "closed"}

    if method == "attested":
        attested, why = attestation_verdict(cr.body, cr.head_sha)
        if attested:
            return "passed", why, {}
        why += pipeline_moved_the_head(cr)
        if cr.state == "merged":
            return "passed", (
                "the pull request is merged, so nothing is left for an attestation "
                f"to authorise; its attestation did not hold: {why}"
            ), {"state": "merged", "attestation": why}
        return "missing", why, {}

    if cr.state not in ("open", "merged"):
        return "missing", (
            f"the pull request is {cr.state or 'in no state the forge named'}"
        ), {}
    return "passed", f"the pull request is {cr.state} and {whose}", {}


def forge_base(task: Task, unit: dict) -> str:
    """Translate a verified origin tracking ref to the branch the forge names.

    The checkout's identity comes from origin, so another remote cannot prove
    its landing branch. Git must resolve the ref unambiguously, and origin's
    fetch mapping must confirm its heads name; a slash alone proves neither.
    Unreadable checkouts, including remote tasks, retain the recorded base.
    """
    base = unit["base"]
    if task.doc.get("host"):
        return base
    repo = unit["path"]
    ref = git_out(repo, ["rev-parse", "--symbolic-full-name", "--verify", "--quiet", base]).strip()
    prefix = "refs/remotes/origin/"
    if not ref.startswith(prefix):
        return base
    branch = ref[len(prefix):]
    mappings = git_out(repo, ["config", "--get-all", "remote.origin.fetch"]).splitlines()
    allowed = {"refs/heads/*:refs/remotes/origin/*", f"refs/heads/{branch}:{ref}"}
    if any(mapping.removeprefix("+") in allowed for mapping in mappings):
        return branch
    return base


def unit_repo(task: Task, unit: dict) -> forge.RepoId | None:
    """The forge repository one of this task's checkouts is, or None for "cannot tell".

    `repo_from_checkout` — the checkout's `origin` — and nothing cleverer. A
    `--host` task's paths are on another machine, and reading a directory that
    happens to share the path here would answer about the wrong checkout, so
    that is None too rather than a guess.
    """
    if task.doc.get("host"):
        return None
    return repo_from_checkout(unit["path"])


def same_repo(a: forge.RepoId, b: forge.RepoId) -> bool:
    """One repository, however the two spellings cased it — both forges fleet
    ships an adapter for resolve a repository path without regard to case."""
    return (a.host, a.path.lower()) == (b.host, b.path.lower())


# The pipeline's own commits, which are the ONE way an `attested` branch grows
# a new head without anybody having touched it. The attestation is written
# during the `pr` step and CI fixes are pushed on top of it, so the body ends up
# attesting a commit that is now an ancestor of the head.
#
# Refusing that is right and stays right — an attestation for an ancestor
# describes code that is not what would merge. But "somebody pushed over the
# pipeline" and "the pipeline did this to itself" are the same refusal with
# different remedies, and only the second is answered by running the tool
# again. Reading #48 today, a lead cannot tell which one it is looking at.
#
# NONE IS THE SHIPPED ANSWER, because the subject a pipeline writes is that
# pipeline's convention. With `PIPELINE_COMMIT_PREFIX` unset fleet never claims
# to know which of the two it is looking at, which is honest rather than thin.


def pipeline_commit_re() -> "re.Pattern | None":
    """The subject this fleet's pipeline writes, or None when none is set."""
    prefix = publish_conf().get("PIPELINE_COMMIT_PREFIX", "").strip()
    if not prefix:
        return None
    return re.compile(r"^(?:chore:\s*)?" + re.escape(prefix) + r"[:\s]", re.I)


def pipeline_moved_the_head(cr: forge.ChangeRequest) -> str:
    """The clause naming the pipeline's own commits, or "" for every other case.

    It only ever ADDS to `attestation_verdict`'s line. The verdict itself is
    not consulted and cannot be changed from here: this says why a refusal
    happened, never whether it should have.

    "" is also what a case that cannot be TOLD APART reads as — the forge
    answering with no commit list, or with one whose last commit is not the head. An
    absent list is silence, and silence must not become a claim about who
    pushed what.
    """
    raw = attestation_json(cr.body or "")
    if raw is None:
        return ""
    try:
        doc = json.loads(raw)
    except ValueError:
        return ""
    attested = str(doc.get("head_sha") or "").lower() if isinstance(doc, dict) else ""
    own = pipeline_commit_re()
    head = (cr.head_sha or "").lower()
    commits = cr.commits
    if own is None or not attested or not head or not commits:
        return ""
    oids = [c.sha.lower() for c in commits]
    if oids[-1] != head or attested not in oids:
        return ""

    after = commits[oids.index(attested) + 1:]
    if not after or not all(own.match(c.headline) for c in after):
        return ""
    named = ", ".join(f"{c.sha[:8]} “{c.headline}”" for c in after[:2])
    return (
        f"; the pipeline pushed that head itself ({named}) after it attested, so "
        "nothing else has moved this branch — run this task's publish command "
        "again and it will attest the commit that would merge"
    )


def task_target(task: Task) -> tuple[str, "forge.Target | None", str]:
    """(the URL `add --target` recorded, what it names, why that could not be read).

    ("", None, "") is a task that names no target. A URL with no Target beside
    it is one no forge configured HERE recognises — which every caller reads as
    "could not check" and never as "names no target", or a check would quietly
    fall back to the branch the target was recorded to replace.
    """
    named = str(task.doc.get("target") or "").strip()
    if not named:
        return "", None, ""
    which, target = forge.for_target(named)
    if which is None:
        return named, None, target
    return named, target, ""


def note_verdict(task: Task, outcome, url) -> tuple[str, str]:
    """The forge as witness, for a task whose deliverable is a note — a review
    or a comment on the change request or issue the task targets.

    Three facts, each asked of the forge and none read off the URL: the note
    EXISTS; it was WRITTEN by the account this machine's forge CLI runs as,
    which is the account every worker here posts as; and it SITS ON this task's
    target, as the forge says and not as the URL says. A URL is text a worker
    typed. `pull/1107#pullrequestreview-…` can name any review at all,
    somebody else's included, and a check that trusted the path would let a
    pasted link prove itself.

    "No such note" is `unknown`, exactly as "no such change request" is: a 404
    and an unreachable forge are both a question nobody could put. A note by
    another account, on anything but the target, or a URL its own forge does
    not read as a note at all, is `missing` — those are answers.
    """
    text = str(url or "").strip()
    if not forge.NOTE_URL_RE.match(text):
        if outcome == "shipped":
            return "missing", "shipped with no note URL to check"
        return "skipped", "no note to check"
    named, target, why = task_target(task)
    if not named:
        return "missing", (
            "this `note` task names no target, so nothing can show the note sits "
            "where it was asked for — record one with `add --target`"
        )
    if target is None:
        return "unknown", why
    which, ref = forge.for_note(text)
    if which is None:
        return ("missing" if forge.owner(text) else "unknown"), ref
    note, why = which.note(ref)
    if why:
        return "unknown", why
    host = ref.target.repo.host
    me, why = which.whoami(host)
    if why:
        return "unknown", why
    if not note.author:
        return "unknown", "the forge did not say who wrote the note"
    if note.target is None:
        return "unknown", "the forge did not say what the note sits on"
    if note.author.lower() != me.lower():
        return "missing", (
            f"the note was written by {note.author}, and fleet runs as {me} on {host}"
        )
    if not note.target.same(target):
        return "missing", (
            f"the note sits on {note.target}, and this task's target is {named}"
        )
    return "passed", f"{me} wrote it, on {named}"


def commit_verdict(task: Task, outcome, url, unit: dict | None = None) -> tuple[str, str]:
    """git as witness, for the method that ends on the base branch and not in a PR.

    The ancestry question is asked in two halves rather than one, because
    `git_out` answers "" both for a command that failed and for one that
    printed nothing — and `merge-base --is-ancestor` prints nothing either way.
    So: read the two commits first, where an empty answer is `unknown` exactly
    as an unreachable `gh` is; then ask for their merge base, where the answer
    IS the ancestry and an empty one means histories that do not meet.
    """
    text = str(url or "").strip()
    match = COMMIT_URL_RE.match(text) or COMMIT_SHA_RE.match(text)
    if not match:
        if outcome == "shipped" and forge.NOTE_URL_RE.match(str(url or "").strip()):
            # The workaround this used to be, said out loud where it is seen.
            return "missing", (
                "shipped with no commit URL to check: that is a note on a change "
                "request or issue, and a task whose deliverable is one is added "
                "`--publish note --target <what it sits on>`"
            )
        if outcome == "shipped":
            return "missing", "shipped with no commit URL to check"
        return "skipped", "no commit to check"
    sha = match.group(2)

    host = task.doc.get("host")
    if host:
        return "unknown", f"the base branch is on host {host}; not checked from here"
    unit = unit or task_repos(task)[0]
    repo, base = unit["path"], unit["base"]

    git_out(repo, ["fetch", "--quiet", "origin", base], timeout=60)
    head = git_out(repo, ["rev-parse", "--verify", "--quiet", f"origin/{base}^{{commit}}"]).strip()
    if not head:
        return "unknown", f"origin/{base} could not be read in {repo}"
    commit = git_out(repo, ["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"]).strip()
    if not commit:
        return "unknown", f"{sha[:8]} is not a commit {repo} holds; it cannot be checked here"
    mb = git_out(repo, ["merge-base", commit, head]).strip()
    if not mb:
        return "unknown", f"{sha[:8]} and origin/{base} have no common history to compare"
    if mb == commit:
        return "passed", f"{sha[:8]} is on origin/{base}"
    return "missing", f"{sha[:8]} is not on origin/{base} — it never reached the base branch"


def reported_artifacts(task: Task, meta: dict) -> list[dict]:
    """What result.md claims, lined up against the repositories fleet expects.

    `artifacts:` is a mapping from repository path to URL — the shape a task
    that spans repositories writes. `artifact:` is the scalar every result has
    always written, and it is read as the PRIMARY repository's, so a
    single-repository result is read by the same two lines it always was.

    A repository the file names nothing for gets None, which for a `shipped`
    task is `missing` exactly as an absent scalar has always been: the worker
    claimed an artifact and there is none to check. Keys are matched as PATHS,
    and the `PATH@BASE` the operator typed is accepted as itself.
    """
    plural = meta.get("artifacts")
    rows = []
    for unit in artifact_repos(task):
        url = None
        if isinstance(plural, dict):
            for key, value in plural.items():
                if str(key) == unit["spec"] or same_path(str(key), unit["path"]):
                    url = value
                    break
        if url is None and unit["primary"]:
            url = meta.get("artifact")
        rows.append({"repo": unit["path"], "url": url, "unit": unit})
    return rows


def fold_verdicts(rows: list[dict]) -> tuple[str, str]:
    """One verdict for the task, out of one per repository.

    A single-repository task folds to exactly its own row — same word, same
    sentence — which is what keeps every message and every field `collect`
    writes byte-identical to what it wrote before.

    `missing` wins, because ONE UNVERIFIED REPOSITORY HOLDS THE WHOLE TASK
    OPEN: a task is one unit of intent, and half of it published is not it.
    `unknown` comes next and stays the fourth word it has always been — it
    holds nothing open and never collapses into either verdict. Then `passed`,
    and `skipped` is what is left when nothing was asked at all.
    """
    if len(rows) == 1:
        return rows[0]["verdict"], rows[0]["detail"]
    joined = "; ".join(f"{r['repo']}: {r['detail']}" for r in rows)
    for word in ("missing", "unknown", "passed"):
        if any(r["verdict"] == word for r in rows):
            return word, joined
    return "skipped", joined


def fold_seen(rows: list[dict]) -> dict:
    """The publish-block fields `collect` writes beside the verdict.

    `pull_request_verdict` produces most of them, and `publish_verdict`'s own
    `served` branch produces `{"state": "served"}` — which never meets the
    multi-row branch below, because `artifact_repos` pins a served task to its
    primary repository the way it pins a `note`. The only field that is a claim
    about the TASK is `state`. So it survives a fold only when every repository
    said the same thing: a task with one change request merged and one still
    open has not merged, and writing `merged` onto its publish block would say
    it had.
    """
    if len(rows) == 1:
        return dict(rows[0].get("seen") or {})
    states = {(r.get("seen") or {}).get("state") for r in rows}
    return {"state": states.pop()} if len(states) == 1 and None not in states else {}


def collect_publish_state(verdict: str, method: str) -> str:
    """What `collect` writes into `publish.state`, or "" for nothing to record.

    `skipped` is the "" — a task that legitimately produced no artifact has no
    publish to observe, and an absent state is what says "nothing has looked".
    `pushed` is terminal: the code is on the base branch, so the task lands in
    this same run and there is nothing left for the shepherd to watch. `posted`
    is terminal for the same reason: the note is on the forge, and nothing about
    it waits for a merge.

    `served` IS THE ONE `skipped` THAT STILL WRITES, and it writes from
    `publish_verdict` rather than from here. Every other method that can come
    back `missing` overwrites that `unverified` on its next clean pass, because
    its clean verdict is `passed` and `passed` always writes. `served`'s clean
    verdict is `skipped`, so a task held open once for a missing URL and then
    collected properly kept `unverified` for good — on the record, in
    progress.jsonl, and drawn `UNVERIFIED` in the pane over a document served
    exactly as asked. The word rides on the verdict because only the verdict
    knows whether there is an address to say it about.
    """
    if verdict == "passed":
        return {"push": "pushed", "note": "posted"}.get(method, "open")
    return {"missing": "unverified", "unknown": "unknown"}.get(verdict, "")


def record_publish(task: Task, state: str, detail: str, by: str, extra: dict | None = None) -> None:
    """The publish block, stamped by whoever LOOKED — never by a worker.

    Merged into whatever is already there, so the method and the `how` the lead
    declared at intake survive an observation being written over them.

    Appended to progress.jsonl the way `record_shepherd` appends, because the
    block holds only the LATEST look: "checks-running, then checks-failed, then
    ready" is the story of a pull request, and every retelling of it would
    otherwise be overwritten by the next pass. The entry carries no `seq`, so
    it is not a stream event and never moves a watch floor (`folded_through`).

    AN UNCHANGED LOOK WRITES NOTHING. The shepherd calls this for every linked
    task on every pass, and the reconciler comes round every few minutes: the
    same state and the same detail is not news, and writing it grew
    progress.jsonl without bound and rewrote task.yaml each time. Compared on
    everything except `at`, the rule `record_shepherd`'s caller already keeps,
    so a changed state, detail or observer still appends.
    """
    old = dict(task.doc.get("publish") or {})
    block = {**old, "state": state, "detail": detail, "at": now(), "by": by, **(extra or {})}
    if {k: v for k, v in old.items() if k != "at"} == {k: v for k, v in block.items() if k != "at"}:
        return
    task.doc["publish"] = block
    task.save()
    fleet_platform.append_record(
        task.file(PROGRESS_FILE), json.dumps({"publish": dict(block), "observed": now()}) + "\n"
    )


def report_unverified(task: Task, rows: list[dict]) -> None:
    """The loud half of the check: the lead sees this AT COLLECT TIME.

    ONE MESSAGE, not two. A task that spans repositories names each of them and
    marks the ones that did not hold up, between the same header and the same
    remedy a single-repository task has always got — because "which repository
    is unverified" is a detail of this refusal and not a different refusal.
    """
    method, how = task_publish(task)
    spec = PUBLISH_METHODS[method]
    told = f"\n        Its brief said: {how}." if how else ""
    if len(rows) == 1:
        middle = (
            f"        {rows[0]['url'] or '(no artifact given)'}\n"
            f"        {rows[0]['detail']}\n"
        )
        every = ""
    else:
        held = sum(1 for r in rows if r["verdict"] == "missing")
        middle = f"        {held} of {len(rows)} repositories did not verify:\n"
        for r in rows:
            mark = "NOT VERIFIED" if r["verdict"] == "missing" else r["verdict"]
            middle += (
                f"          {r['repo']}  {r['url'] or '(no artifact given)'}\n"
                f"            {mark}: {r['detail']}\n"
            )
        every = " in EVERY repository it spans"
    print(
        f"    {task.ref}: NOT CLOSED — nothing proves this task published\n"
        f"{middle}"
        f"        A `{method}` task is proven when {spec['proof']}{every}.{told}\n"
        "        Send the worker back to publish again, then collect again.\n"
        "        If you have read the artifact yourself and judged it good as\n"
        "        it stands, close it deliberately with\n"
        f"        `fleet queue collect --allow-unverified {task.ref}`.",
        file=sys.stderr,
    )


def reopen_unfinished_archives(q: Queue) -> None:
    """Clear `archived` on every topic that holds a task that is not finished.

    An archived topic claims every task in it is finished, and the live view
    trusts that claim so completely that it never opens the task directories.
    A record written back over a landing breaks it: a `shepherd` that loaded a
    task while `dispatched` and saved it after `collect` had closed it and
    `reap` had archived its topic left a `dispatched` task no live reader
    could see. `collect` read `0 result(s)` and never named it again.

    So `collect`, the command that closes tasks, reads every archived record
    and not only the live ones. One `task.yaml` per archived task per pass is
    the price, and `list` and the pane still skip them. The topic comes back
    rather than being read around, so every other reader sees the task too,
    and `reap` archives it again once the task lands.

    It reads them out of `collect`'s own `all` view rather than a second load:
    archived topics are most of a long-running queue, and a view of its own
    parsed every one of their records twice a pass. The flag it clears on disk
    is cleared in that view too, so the rest of the pass reads the topic as
    live, as a fresh load would.
    """
    for slug, tasks in sorted(q.by_topic().items()):
        if not q.topics.get(slug, {}).get("archived"):
            continue
        task = unfinished(tasks)
        if task is None:
            continue
        set_archived(q.root, slug, None)
        q.topics[slug].pop("archived", None)
        print(
            f"    {slug:<46} unarchived {task.ref} is `{task.state}`, and an "
            "archived topic holds only finished tasks"
        )


def cmd_collect(args) -> int:
    root = queue_root()
    # `all`, because this command ends by running `reap`, whose view must be —
    # see `reap` below. ONE view for the whole pass rather than a second one
    # built halfway through it, so the run log refreshed at the end renders the
    # records `reap` just wrote and not the ones this loop read. Reopening an
    # archived topic reads the same view, so each record is parsed once a
    # pass, and every task left in an archived topic is terminal, so the loop
    # below skips all of them exactly as it skips a concluded live one.
    q = Queue(root, scope="all")
    before = {ref: t.state for ref, t in q.tasks.items()}
    # ONE TASK AT A TIME, BY NAME. The override used to be a bare flag that
    # closed every held task in the pass, so judging one artifact good closed
    # every other one nobody had read. A ref that names no task fails here,
    # before anything is written.
    allowed = {q.get(ref).ref for ref in args.allow_unverified}
    reopen_unfinished_archives(q)
    concluded = 0
    held = 0
    artifacts = 0
    for task in sorted(q.tasks.values(), key=lambda t: t.ref):
        c, h, a = _collect_task(task, allowed)
        concluded, held, artifacts = concluded + c, held + h, artifacts + a

    _print_collect_summary(concluded, held, artifacts)

    # The third thing, wired into the command the lead already runs rather than
    # left as a chore for it to remember — a step only a human remembers is the
    # step that let twenty gigabytes of merged-and-forgotten worktree pile up.
    # It is safe to run here because its gate is not this command's: it acts on
    # a task whose artifact the FORGE says has landed, which is never true of a
    # result collected a moment ago. Everything else is reported and left be.
    reap(q, dry=False, release=not args.no_reap)
    tell_origins(q, before)

    # The run log's facts move when the records do, and this is the command
    # that moves them most. It prints only when a file actually changed.
    refresh_run_logs(q)
    return 0


def _read_result(task: Task) -> tuple | None:
    """(meta, body, outcome) of a task's new result.md, or None when there is none to act on.

    Every reason it is None that a person should see is printed.
    """
    path = task.file(RESULT_FILE)
    # Only a task with a worker has a result to read. A `queued` one has none
    # yet, so a result.md beside it is a previous attempt's, left there when
    # the task was put back by hand: reading it would close work that never
    # ran, and land it once that old pull request merged.
    if task.state not in ("dispatched", *REREAD_STATES):
        if task.state == "queued" and os.path.exists(path):
            print(
                f"    {task.ref}: is queued, so its result.md is not read — no worker has"
                f" run since it was written; dispatch moves it to {PREVIOUS_RESULT_FILE}",
                file=sys.stderr,
            )
        return None

    # The remote transport, and the whole of it. A worker on another machine
    # wrote its result into its own worktree; this pulls that file into the
    # task's own result.md, after which every line below reads a local file
    # and neither knows nor cares which machine wrote it. Nothing is closed
    # here: a result that could not be fetched leaves the task exactly as a
    # missing local one does.
    if task.doc.get("host") and task.state in ("dispatched", *REREAD_STATES):
        note = pull_remote_result(task)
        if note:
            print(f"    {task.ref}  {note}")

    if not os.path.exists(path):
        return None
    # ONE WORKER'S FILE HOLDS ONE TASK. A frontmatter the worker mangled
    # used to end this whole pass with the parser's traceback, and the
    # reconciler ran that pass every two minutes: nothing else was
    # collected until somebody found the file. Bytes that are not UTF-8 —
    # a Windows console writing in its own code page — are read with a
    # replacement character rather than refused, since the frontmatter is
    # ASCII and the body is only ever quoted.
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            meta, body = parse_result(fh.read())
    except (OSError, yaml.YAMLError) as exc:
        why = (
            f"its frontmatter is not YAML: {one_line(exc)}"
            if isinstance(exc, yaml.YAMLError) else str(exc)
        )
        print(
            f"    {task.ref}: result.md could not be read — {why}; the task is left as it is",
            file=sys.stderr,
        )
        return None
    outcome = str(meta.get("outcome", "")).strip()
    if task.state in REREAD_STATES and outcome == task.doc.get("outcome"):
        return None  # the verdict it concluded on; nothing new to read
    if outcome not in OUTCOMES:
        print(
            f"    {task.ref}: result.md has outcome {outcome!r}; expected one of "
            + ", ".join(sorted(OUTCOMES)),
            file=sys.stderr,
        )
        return None
    return meta, body, outcome


def _collect_task(task: Task, allowed: set) -> tuple[int, int, int]:
    """Read one task's result and close it, hold it, or retire it.

    (concluded, held, artifacts) — what it adds to `collect`'s counts.
    """
    read = _read_result(task)
    if read is None:
        return 0, 0, 0
    meta, body, outcome = read
    method, _how = task_publish(task)
    # ONE ROW PER REPOSITORY THIS TASK SPANS, and exactly one for the task
    # that spans none — which is why everything below reads the same for a
    # single-repository task as it did before a task could span any.
    rows = reported_artifacts(task, meta)
    verdict, detail, seen = _check_artifacts(task, outcome, method, rows)
    artifact = artifact_value(rows)

    # A change request closed unmerged is the forge ending the task, and
    # every repository that held it open is one: retired, never held.
    closed = [r for r in rows if (r.get("seen") or {}).get("state") == "closed"]
    # `unknown` still never collapses into another word: a repository whose
    # forge could not be asked leaves the task where it is, as at landing.
    if verdict == "missing" and closed and all(
        r in closed for r in rows if r["verdict"] in ("missing", "unknown")
    ):
        task.doc["outcome"] = outcome
        task.doc["artifact"] = artifact
        task.doc["concluded_at"] = now()
        retire(task, "closed-unmerged", "; ".join(r["detail"] for r in closed))
        print(f"    {task.ref}  {ABANDON_HOW['closed-unmerged']}  {closed[0]['detail']}")
        record_learned(task, meta)
        return 1, 0, 0

    if verdict == "missing" and task.ref not in allowed:
        task.save()
        report_unverified(task, rows)
        return 0, 1, 0

    task.doc["outcome"] = outcome
    task.doc["artifact"] = artifact
    task.doc["state"] = OUTCOMES[outcome]
    task.doc["concluded_at"] = now()
    task.save()
    artifacts = (
        1 if method in CHANGE_METHODS and any(pr_ref(r["url"]) for r in rows) else 0
    )
    print(_concluded_line(task, outcome, rows, artifact, verdict, detail, method, seen))
    first = body.splitlines()[0] if body.splitlines() else ""
    if first:
        print(f"        {first}")
    record_learned(task, meta)
    return 1, 0, artifacts


def _check_artifacts(task: Task, outcome: str, method: str, rows: list) -> tuple:
    """Verify each reported artifact and record the verdict. (verdict, detail, seen)."""
    for row in rows:
        row["verdict"], row["detail"], row["seen"] = publish_verdict(
            task, outcome, row["url"], row["unit"]
        )
    verdict, detail = fold_verdicts(rows)
    # Recorded before the branch below, so a held-back task carries the
    # reason in its record and not only in the terminal that saw it. The
    # METHOD is recorded with it because a verdict is only readable beside
    # what it was asked to prove.
    task.doc["artifact_check"] = {
        "verdict": verdict, "detail": detail, "at": now(), "method": method,
    }
    if len(rows) > 1:
        # The per-repository verdicts, kept beside the folded one: the fold
        # says the task is held and this says which repository held it,
        # after the terminal that printed it has scrolled away.
        task.doc["artifact_check"]["repos"] = [
            {"repo": r["repo"], "url": r["url"], "verdict": r["verdict"],
             "detail": r["detail"]}
            for r in rows
        ]
    seen = fold_seen(rows)
    state = seen.pop("state", "") or collect_publish_state(verdict, method)
    if state:
        record_publish(task, state, detail, "collect", seen)
    elif (task.doc.get("publish") or {}).get("state") == "unverified":
        # A WORKER THAT WITHDREW ITS CLAIM LEAVES THE REFUSAL BEHIND IT. A
        # held-open task whose worker comes back with a result claiming no
        # artifact at all is not the task that was refused: `unverified` is
        # a verdict about an artifact this pass was not given, and left
        # standing it is drawn red in the pane over a task that landed
        # having correctly produced nothing. Cleared rather than
        # overwritten with a fresh word, because "nothing has looked" is
        # exactly where such a record stands.
        record_publish(task, "", "this result claims no artifact to check", "collect")
    return verdict, detail, seen


def _concluded_line(task: Task, outcome: str, rows: list, artifact, verdict: str,
                    detail: str, method: str, seen: dict) -> str:
    """`collect`'s line for a task it closed."""
    line = f"    {task.ref}  {outcome}"
    if len(rows) == 1:
        if artifact:
            line += f"  {artifact}"
    else:
        named = ", ".join(str(r["url"]) for r in rows if r["url"])
        line += f"  {len(rows)} repositories: {named or '(none reported)'}"
    if verdict == "passed":
        line += f"  [publish verified: {method}]"
        if seen.get("attestation"):
            line += "  [merged; its attestation did not hold — noted, not held]"
    elif verdict == "skipped" and method in ("none", "served"):
        line += f"  [publish not checked: {method}]"
    elif verdict == "missing":
        line += "  [publish NOT verified — closed by --allow-unverified]"
    elif verdict == "unknown":
        line += f"  [publish unchecked: could not check — {detail}]"
    return line


def _print_collect_summary(concluded: int, held: int, artifacts: int) -> None:
    """`collect`'s closing counts, and the nudge toward `shepherd`."""
    print(f"collect: {concluded} result(s) read")
    if held:
        print(
            f"         {held} task(s) HELD OPEN — nothing proves they published; "
            "see above.",
            file=sys.stderr,
        )
    if artifacts:
        # The bug the shepherd fixes is "a step only a human remembers", so
        # the command that closes a task names the one that watches what it
        # left behind. A sibling nobody runs reproduces the bug exactly.
        print(
            f"         {artifacts} of them left an open pull request. Run\n"
            "         `fleet queue shepherd --dry-run` — a PR can go bad long\n"
            "         after the worker that wrote it stopped."
        )


# --- land and reap: the third thing, after both halves of completion ---------
#
# A task that finishes leaves a thurbox session and a git worktree running
# forever, and nothing used to reap them. Four had accumulated on one machine;
# the oldest held twenty gigabytes and its pull request had merged the day
# before. `AGENTS.md` already said "delete each session as it closes out" — it
# was documented, it was manual, and a step only a human remembers is a step
# that does not run.
#
# THE TRAP THAT DECIDES THE GATE. The obvious design is "reap when `collect`
# closes the task". It is wrong, and it was learned expensively. `collect`
# closes a task on the worker's result.md, and `outcome: shipped` there means A
# PULL REQUEST IS OPEN — not merged. Hours after two tasks were collected
# `shipped`, both pull requests turned out to have been opened by hand instead
# of through the pipeline. The fix was a follow-up message to each still-living
# session. Had collect reaped them, the same fix would have cost a full
# re-spawn: a new worktree, a cold agent, and the brief read again from
# nothing.
#
# So the gate is not "the task is done". It is "the artifact LANDED", which
# only the forge knows and usually long after the worker is gone:
#
#     done       the worker concluded. Its pull request is open, under review,
#                and its session is the cheap way to fix whatever review finds.
#     landed     ... and the artifact reached main, or there was never one to
#                reach it. Nothing more will be asked of that session.
#     abandoned  ... and the pull request was closed unmerged. Nothing more
#                will be asked of the session either — but the work is NOT on
#                main, so a task that waited on this one still waits.
#
# `landed` is a state the record keeps and everything reads: reaping, blocker
# clearing, and `list`. Nothing re-opens — this is a LATER transition out of
# `done`, discovered by asking the forge, never by a worker claiming it. Out of
# `stuck` or `failed` too, on the narrower answer that EVERY artifact the
# worker named merged: the worker gave up, somebody finished the job on the
# forge, and the work is on main either way (`sweep_landings`).

# The only session states a reap will act on. `working` and `blocked` are the
# ones that must never be touched, but they are not the whole exclusion:
# `running` is an agent holding the pane with nothing signalled, `uncovered` is
# an agent wired to report nothing, and `unreported` is one that can report and
# has not yet. None of those three is the agent saying it is at rest, and
# treating them as `idle` is how live work gets killed. Read the word, never
# the absence of one. (`.agents/skills/thurbox-session/SKILL.md` §4a.)
#
# `unreachable` — a remote session whose host cannot be reached — is absent for
# the same reason, and it is the one word on that list the CLI does not say out
# loud: it reaches the interface's session rows and nothing else, so `session
# get --json` on a session whose host went away answers with the state that was
# LATCHED before it did. A worker that last reported `idle` therefore still
# reads `idle`, and `idle` is reapable. `host_reachable` is what closes that:
# a remote session is asked about its host before anything is deleted, and this
# file derives the word rather than waiting to be told it.
#
# Even an idle session is kept when deleting it would take down a worktree
# another live session is sitting in. `session delete --force` removes the
# worktrees thurbox created for that session; a session made by hand is not in
# the queue, so the live `session list` is the only set that can answer. If
# that list cannot be read, or a path on it cannot be resolved, the session
# stays — a guess here is someone else's uncommitted work. A remote session
# is asked about its HOST first (`host_reachable`) and then for that host's
# own `session list` over the same ssh path; an occupant there keeps the
# session and the next pass retries, and a host that does not answer is the
# existing unreachable keep.
REAPABLE_SESSION_STATES = ("idle", "done", "stopped")

# The task states that still hold a session worth reporting on. `queued` never
# had one and `dispatched` is a worker mid-flight.
HOLDING_STATES = ("done", "landed", "abandoned", "stuck", "failed")

# What a landing promotes a `done` task to. `open` and `unknown` are absent on
# purpose: a task waits rather than move on a fact nobody could establish.
LANDED_STATE = {"merged": "landed", "none": "landed", "closed": "abandoned"}


def artifact_landing(
    artifact, method: str | None = None, reviewed: bool = False,
    task_ref: str = "<ref>",
) -> tuple[str, str]:
    """Has this task's artifact reached main? Asked of the forge, never of a worker.

        none      nothing to wait for — `not-applicable` produced no artifact,
                  or the artifact is not a pull request, or the task is a
                  `note` or `none` one, whose URL may NAME a pull request that
                  is not its own. Such a task skips straight through rather
                  than waiting for a merge that is not its to wait for.
                  A `push` task is never asked here: its commit is git's to
                  vouch for, and `push_landing` asks git.
        merged   the pull request is on main.
        closed    it was closed unmerged: nothing more will happen on that
                  branch, and the work did not land either.
        open      the normal state of work awaiting review. Not an error and
                  not a warning.
        unknown   the forge could not be asked — none is configured for that
                  host, no network, no such change request. It never collapses into any of the others, for
                  the same reason `collect`'s pipeline check has a fourth word:
                  a timeout must not be able to manufacture a merge, and a
                  merge is what authorises a deletion.
    """
    if method == "served":
        # The one wait no forge and no git can answer, so it is read off the
        # record a PERSON writes (`reviewed` below) and nothing else. `open`
        # rather than a word of its own: "work awaiting review" is exactly
        # what this is, and every reader of a landing — the reap gate, the
        # blockers, the pane — already knows that word.
        #
        # NO DOCUMENT IS NO WAIT. A `not-applicable` task concludes having
        # produced nothing, and a wait on a reader who was handed no address
        # is one nothing could ever end: the task would sit `done` for good,
        # holding a session and holding every task blocked on it, until
        # somebody closed a review of a document that was never served.
        if not artifact:
            return "none", "no document was served, so there is no reader to wait for"
        if reviewed:
            return "none", "the reader's review was recorded closed"
        return "open", (
            "a served document is awaiting its reader; nothing but "
            f"`fleet queue reviewed {task_ref}` closes that"
        )
    if method in ("note", "none"):
        return "none", f"a `{method}` task has no pull request of its own to wait for"
    url = forge.change_url(artifact)
    if not url:
        return "none", "no pull request to wait for"
    which, ref = forge.for_url(url)
    if which is None:
        return "unknown", ref
    state, why = which.state(ref)
    if why:
        return "unknown", why
    if state == "merged":
        return "merged", f"{url} is merged"
    if state == "closed":
        return "closed", f"{url} was closed without merging"
    return "open", f"{url} is still open — work awaiting review"


def push_landing(task: Task, entry: dict) -> tuple[str, str]:
    """Has a `push` task's commit reached the base branch? Asked of git, as at collect.

    A `push` artifact is a commit and not a change request, so the forge has
    nothing to say about it, and `artifact_landing` used to answer `none`
    for it — "nothing to wait for". That was the hole `unknown` must never
    open: a sha the repository does not hold is `unknown` at collect, which
    closes the task, and the same pass then LANDED it on no evidence at all
    and released every task blocked on it. So landing asks exactly the
    question collect asked, through `commit_verdict` itself, and only its
    `passed` is a landing:

        passed    -> merged   the commit is on origin/<base>.
        unknown   -> unknown  git could not answer: no such commit here, a
                              base branch that cannot be read.
        missing   -> open     the commit has not reached the base branch,
                              or a `shipped` result named none. A definite
                              answer, so not `unknown`,
                              which every landing reader keeps for "could not
                              be asked"; `open` is the word they already read
                              as "not yet", and the next pass asks again, so a
                              commit that reaches the base branch later lands
                              then. Only a task closed `--allow-unverified`
                              reaches here, since collect holds `missing`.
        skipped   -> none     no commit was claimed — `not-applicable` — so
                              there is nothing to wait for.

    A repository recorded as `no change needed — <why>` in a multi-repo
    task has no commit to wait for, just as at collect.

    A REMOTE TASK (`--host`) keeps the answer it always had, `none`, and on
    purpose: its repository is on that host, `commit_verdict` cannot look
    from here, and collect already recorded that as `unknown` in the
    publish block. Answering `unknown` again would leave every remote push
    task `done` for good — nothing this machine runs could ever end it.
    """
    reason = no_change_reason(entry["url"]) if len(task_repos(task)) > 1 else None
    if reason:
        return "none", f"no change needed in this repository: {reason}"
    if task.doc.get("host"):
        return "none", (
            f"the base branch is on host {task.doc['host']}; a remote push lands "
            "on what collect recorded, not on a check from here"
        )
    unit = next(
        (u for u in task_repos(task) if same_path(u["path"], entry["repo"])), None
    )
    verdict, detail = commit_verdict(task, task.doc.get("outcome"), entry["url"], unit)
    if verdict == "passed":
        return "merged", detail
    if verdict == "skipped":
        return "none", detail
    if verdict == "missing":
        return "open", detail
    return "unknown", detail


# The order a task's own landing folds in, most-blocking first. `unknown` is
# still the word that never collapses into another, so a forge nobody could ask
# leaves the task where it is. `open` holds it for the ordinary reason. `closed`
# is the forge saying THAT change request will never land, and a task holding
# one can never reach `landed` whatever the others do. `merged` and `none` are
# what is left.
LANDING_ORDER = ("unknown", "open", "closed", "merged", "none")


def task_landing(task: Task) -> tuple[str, str]:
    """Has EVERY one of this task's artifacts reached main?

    THE REASON IT IS ASKED PER TASK AND NOT PER ARTIFACT. `landed` is what
    clears a blocker, so a task promoted on half its repositories would release
    its dependents while the other half sat unmerged — which is the bug
    AGENTS.md already records in its single-repository form ("a task collected
    `shipped` once released its dependents while its change request sat
    unreviewed"), one size larger. A task with one repository merged and one
    open is not landed.

    A task with one repository is exactly `artifact_landing` of its one
    artifact: same word, same sentence, same reap — or `push_landing` of it,
    for the one method whose witness is git and not the forge.
    """
    method = task_publish(task)[0]
    closed = review_closed(task)
    rows = []
    for entry in recorded_artifacts(task):
        if method == "push":
            kind, detail = push_landing(task, entry)
        else:
            kind, detail = artifact_landing(entry["url"], method, closed, task.ref)
        rows.append({"repo": entry["repo"], "kind": kind, "detail": detail})
    if len(rows) == 1:
        return rows[0]["kind"], rows[0]["detail"]
    kinds = {r["kind"] for r in rows}
    kind = next((w for w in LANDING_ORDER if w in kinds), "unknown")
    return kind, "; ".join(f"{r['repo']}: {r['detail']}" for r in rows)


def review_closed(task: Task) -> bool:
    """Has a person said the reader is done with this task's served document?

    THE ONLY WITNESS IS A PERSON. Fleet cannot poll a server it did not start,
    a reader who closed the browser tab leaves no trace anywhere fleet can
    read, and an idle session proves nothing — the agent being at rest is what
    waiting for feedback LOOKS like. So this reads a record `cmd_reviewed`
    writes and derives nothing, the way a condition blocker is cleared by the
    hand that recorded it and by nothing else.
    """
    return bool((task.doc.get("review") or {}).get("closed"))


def sweep_landings(q: Queue, dry: bool) -> dict:
    """Ask the forge about every `done` task and promote the ones that landed.

    Works from the RECORD and the forge alone. That is a requirement, not an
    accident: a merge normally happens after the session that produced it is
    gone, so nothing here may depend on a worker being alive to say so.

    A `stuck` or `failed` task is asked too, and moves on ONE answer only:
    every artifact it named merged. Its worker gave up, but a person can
    still take the pull request the rest of the way — a decision made on it,
    an attestation the publisher would not write — and a task left `stuck`
    over work that is on main holds its dependents and its topic on the board
    until somebody rewrites its result by hand. Still open, closed, merged in
    one repository of two, or no artifact named at all, and it stays exactly
    where its worker put it: that verdict is still the truth, and the kept
    session is still its evidence.

    Returns {ref: (kind, detail)} for every task it asked about, so the caller
    can say why a task it did not promote is being kept.
    """
    seen: dict = {}
    for task in sorted(q.tasks.values(), key=lambda t: t.ref):
        if task.state != "done" and not (
            task.state in REREAD_STATES and all(a["url"] for a in recorded_artifacts(task))
        ):
            continue
        kind, detail = task_landing(task)
        if task.state != "done" and kind != "merged":
            continue
        seen[task.ref] = (kind, detail)
        nxt = LANDED_STATE.get(kind)
        if dry:
            if nxt:
                print(f"    {task.ref:<46} would be {nxt:<10} {detail}")
            continue
        _record_landing(task, kind, detail, nxt)
    return seen


def _record_landing(task: Task, kind: str, detail: str, nxt: str | None) -> None:
    """Write what the forge said of one task, promoting it if it landed."""
    was = task.state
    task.doc["landing"] = {"state": kind, "detail": detail, "at": now()}
    if kind in ("merged", "closed"):
        # The same fact in the block that carries every other observation
        # of this task's publish, so a reader of that block alone is never
        # left at the last thing the shepherd saw while the pull request
        # has since merged. `open`, `none` and `unknown` write nothing:
        # they say the sweep looked and learned nothing new. A `push`
        # task is skipped: its block already says `pushed`, the same fact
        # in the word its own method uses, and git is the witness to both.
        if task_publish(task)[0] != "push":
            record_publish(task, kind, detail, "reap")
    if nxt == "abandoned":
        retire(task, "closed-unmerged", detail)
        print(f"    {task.ref:<46} {nxt:<10} {ABANDON_HOW['closed-unmerged']}: {detail}")
    elif nxt:
        task.doc["state"] = nxt
        print(f"    {task.ref:<46} {nxt:<10} {detail}")
    task.save()
    if was in REREAD_STATES:
        fleet_platform.append_record(task.file(PROGRESS_FILE), json.dumps({
            "landed": f"the worker said {was}; every artifact merged — {detail}",
            "observed": now(),
        }) + "\n")


# How long a dispatched task's session has to stay missing from thurbox's
# list before the task is retired as `session gone`. Two looks rather than one,
# because one `session list` that answered without the id is a single reading,
# and retiring live work on a hiccup is worse than a pass of delay. Ten minutes
# is five collect passes of the reconciler: long past a blip, and still an end
# the same afternoon for a worker a crashed multiplexer took with it.
VANISHED_GRACE_SECS = 10 * 60

# How many tracked sessions may go missing from one machine in ONE look before
# that look is read as an outage of the machine rather than as that many
# separate endings. A thurbox restart, a multiplexer crash or a host whose
# mirror stopped answering still yields a list that reads cleanly — it just
# lacks most of the sessions — and without this every task on that machine is
# retired as `session gone` one grace period later, in words that claim each
# worker ended. Five, and more than half of that machine's dispatched sessions,
# is the line Agent Orchestrator's reaper draws for the same question: below
# five, a handful of real deaths on a small fleet is still believed; at five or
# more, losing the majority at once is a fact about the machine, which is for a
# person to look at and not for this loop to act on. Counted PER HOST, because
# an outage is: devbox dropping off ends nothing here, and must not hold back a
# local session that really is gone.
MASS_VANISH_MIN = 5


def sweep_vanished(q: Queue, dry: bool, snapshot=None, decided: set | None = None) -> int:
    """Retire every dispatched task whose session thurbox no longer has.

    A worker whose multiplexer crashed, or whose session somebody deleted by
    hand, leaves a task `dispatched` with nobody behind it, and nothing else in
    the loop will ever end it: no result.md is coming and `collect` waits for
    one forever. So the absence is recorded on the first pass (`vanished`) and
    the task is retired as `session gone` once it has lasted
    VANISHED_GRACE_SECS. A session that comes back clears the first look.

    It retires nothing on a guess. A session list that could not be read, a
    result.md waiting for `collect`, the lead's own session, and a task whose
    host cannot be reached all leave the task exactly where it is. So does a
    look in which one machine lost MASS_VANISH_MIN or more of its sessions, and
    most of them: that is an outage, and it is named for a person instead.
    """
    todo = [
        t for t in sorted(q.tasks.values(), key=lambda t: t.ref)
        if t.state == "dispatched" and t.doc.get("session")
    ]
    if not todo:
        return 0
    live, _why = (snapshot or live_sessions)()
    if live is None:
        return 0
    lead = os.environ.get("THURBOX_SESSION")
    reachable: dict = {}

    def missing(task) -> bool:
        sid = task.doc["session"]
        return not (sid in live or sid == lead or os.path.exists(task.file(RESULT_FILE)))

    def unreachable(host) -> bool:
        if host and host not in reachable:
            reachable[host] = host_reachable(host)[0]
        return bool(host) and not reachable[host]

    outage = _outage_hosts(todo, missing, unreachable)

    acted = 0
    for task in todo:
        if not _vanish_held(task, missing, unreachable, outage, dry):
            acted += _vanished_look(task, dry, decided)
    return acted


def _vanish_held(task: Task, missing, unreachable, outage: set, dry: bool) -> bool:
    """Whether this look leaves the task alone; a session listed again clears its first look."""
    if not missing(task):
        seen = task.doc.get("vanished") or {}
        if seen and not dry:
            task.doc.pop("vanished", None)
            task.save()
        return True
    if unreachable(task.doc.get("host")):
        return True
    # Neither a first look nor a retirement. A first look started here
    # would count the outage itself toward the grace, so a session
    # still missing when the machine recovers would be retired on its
    # very next pass, with ten minutes of "evidence" that were really
    # the outage's. Leaving it unrecorded starts the clock only once a
    # look can be believed again. An earlier first look is left as it
    # is: it predates the outage and stays honest on its own.
    return (task.doc.get("host") or "") in outage


def _outage_hosts(todo: list, missing, unreachable) -> set:
    """The hosts that lost most of their sessions in one look, each named out loud."""
    # One machine at a time: how many of its tracked sessions this look lost.
    per_host: dict = {}
    for task in todo:
        host = task.doc.get("host") or ""
        tracked, absent = per_host.get(host, (0, 0))
        per_host[host] = (tracked + 1, absent + (missing(task) and not unreachable(host)))
    outage = set()
    for host, (tracked, absent) in sorted(per_host.items()):
        if absent >= MASS_VANISH_MIN and absent * 2 > tracked:
            outage.add(host)
            where = f"on {host}" if host else "here"
            print(
                f"    thurbox lists {absent} of {tracked} dispatched sessions {where} as gone in one look; "
                "read as an outage, nothing retired — a person should look"
            )
    return outage


def _vanished_look(task: Task, dry: bool, decided: set | None) -> int:
    """Record a missing session's first look, or retire it once the grace is up. 1 if acted."""
    sid = task.doc["session"]
    seen = task.doc.get("vanished") or {}
    since = record_time(seen.get("since")) if seen.get("session") == sid else 0.0
    if not since:
        if dry:
            print(f"    {task.ref:<46} not listed {sid}  thurbox does not list its session")
            return 1
        task.doc["vanished"] = {"session": sid, "since": now()}
        task.save()
        print(
            f"    {task.ref:<46} not listed {sid}  thurbox no longer lists its session; "
            f"`{ABANDON_HOW['session-gone']}` if it is still absent in "
            f"{VANISHED_GRACE_SECS // 60}m"
        )
        return 1
    if time.time() - since < VANISHED_GRACE_SECS:
        return 0
    why = f"thurbox has not listed session {sid} since {seen['since']}, and no result.md was written"
    if dry:
        print(f"    {task.ref:<46} would be abandoned  {ABANDON_HOW['session-gone']}: {why}")
        return 1
    retire(task, "session-gone", why)
    record_reaped(task, sid, "already gone")
    print(f"    {task.ref:<46} abandoned  {ABANDON_HOW['session-gone']}: {why}")
    trees = task_worktrees(task)
    free_build_output(task.ref, trees, dry)
    run_worktree_command(task.ref, handover_worktrees(task), dry, decided=decided)
    release_companions(task, dry)
    return 1


def session_snapshot() -> tuple[dict | None, str]:
    """Every active session document, or None when thurbox cannot be asked.

    None is emphatically not an empty snapshot. "thurbox did not answer" must never
    read as "every session is already gone" — that would drop the id of a
    session still holding a worktree, and the worktree with it.
    """
    if not shutil.which("thurbox-cli"):
        return None, NO_THURBOX_CLI
    try:
        proc = subprocess.run(
            ["thurbox-cli", "session", "list", "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"thurbox-cli session list could not run: {exc}"
    if proc.returncode != 0:
        return None, "thurbox-cli session list failed"
    return sessions_from_json_text(proc.stdout, "thurbox-cli session list")


def live_sessions() -> tuple[set | None, str]:
    sessions, why = session_snapshot()
    return (set(sessions) if sessions is not None else None), why


def sessions_from_rows(doc, source: str) -> tuple[dict | None, str]:
    if not isinstance(doc, list):
        return None, f"{source} did not answer a list"
    sessions = {}
    for row in doc:
        sid = row.get("id") if isinstance(row, dict) else None
        if not isinstance(sid, str) or not sid or sid in sessions:
            return None, f"{source} answered invalid or duplicate session ids"
        sessions[sid] = row
    return sessions, ""


def sessions_from_json_text(text: str, source: str) -> tuple[dict | None, str]:
    """A session list, including one a login-shell banner glued itself in front of.

    Seeking the first `[` after a failed parse is a heuristic: a banner that
    contains `[` can make the slice unparseable, and that is a keep, not a
    delete. Fail-closed is what makes the guess acceptable on a path that
    decides deletions.
    """
    raw = text or ""
    try:
        doc = json.loads(raw)
    except ValueError:
        start = raw.find("[")
        if start < 0:
            return None, f"{source} did not answer JSON"
        try:
            doc = json.loads(raw[start:])
        except ValueError:
            return None, f"{source} did not answer JSON"
    return sessions_from_rows(doc, source)


def host_session_snapshot(name: str) -> tuple[dict | None, dict | None, str]:
    """The host's own `session list --json`, over the same ssh path `host_reachable` uses."""
    entry, why = host_entry(name)
    if not entry:
        return None, None, why
    proc = ssh_run(entry, host_shell(entry).thurbox_cli("session list --json"))
    if proc.returncode != 0:
        return None, entry, first_line(proc) or f"thurbox-cli session list on host {name} failed"
    sessions, why = sessions_from_json_text(
        proc.stdout, f"thurbox-cli session list on host {name}",
    )
    return sessions, entry, why


def owned_worktree_paths(target) -> tuple[list | None, str]:
    worktrees = target.get("worktrees")
    if not isinstance(worktrees, list):
        return None, "cannot read session worktrees"
    owned = []
    for tree in worktrees:
        if not isinstance(tree, dict):
            return None, "cannot read worktree ownership"
        flag = tree.get("created_by_thurbox")
        if flag is None:
            owned.append(tree.get("worktree_path"))
        elif type(flag) is not bool:
            return None, "cannot read worktree ownership"
        elif flag:
            owned.append(tree.get("worktree_path"))
    return owned, ""


# Why a path a session reports cannot be placed at all.
NOT_ABSOLUTE = "missing or non-absolute path"


def local_resolved_path(value) -> str:

    # Path.resolve(strict=True), not os.path.realpath(..., strict=True):
    # the latter's strict= is 3.13 and this file's floor is 3.11.
    if not isinstance(value, str) or not value or "\0" in value or not os.path.isabs(value):
        raise ValueError(NOT_ABSOLUTE)
    return os.path.normcase(str(Path(value).resolve(strict=True)))


def local_occupant_path(value) -> str:
    """Another session's cwd, judged even when its directory is gone.

    A deleted directory holds nothing a deletion could take away, but a
    session whose cwd was deleted still names where it stood — and one stale
    row must not stop every release on the machine. So a path that no longer
    exists is placed lexically, with the symlinks of the part that does still
    exist resolved: one still spelled inside a worktree keeps it. Anything
    else unresolvable is still a refusal to judge.
    """
    try:
        return local_resolved_path(value)
    except (FileNotFoundError, NotADirectoryError):
        return os.path.normcase(str(Path(value).resolve(strict=False)))


def documented_path(value, posix: bool) -> str:
    """A path as the multiplexer spells it, not as this machine would resolve it."""
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError(NOT_ABSOLUTE)
    mod = posixpath if posix else ntpath
    if not mod.isabs(value):
        raise ValueError(NOT_ABSOLUTE)
    return mod.normcase(mod.normpath(value))


# thurbox's `backend_type` grammar, as `src/session/route.rs` there parses it:
# a PLACE and a MULTIPLEXER, independent, and neither says what OS the machine
# runs. Old rows are unqualified (`""`, `tmux`, `local-tmux`, `local-<mux>`,
# `ssh:<host>`, `wsl:<host>`); new ones are `local:<mux>`, `ssh:<host>:<mux>`,
# `wsl:<host>:<mux>`. Mirrored and not imported, and a key it cannot place is
# None — refused, never guessed local.
ROUTE_MULTIPLEXERS = ("tmux", "psmux", "rmux", "herdr")


def session_place(key) -> tuple[str, str] | None:
    """Where a route runs: ("local", ""), ("ssh", host) or ("wsl", host)."""
    if not isinstance(key, str):
        return None
    for via in ("ssh", "wsl"):
        rest = key.removeprefix(via + ":")
        if rest == key:
            continue
        # A host name holds no `:`, so a known multiplexer after the last one
        # is the route's; anything else is kept as the host, as thurbox does.
        host, sep, mux = rest.rpartition(":")
        if not sep or mux not in ROUTE_MULTIPLEXERS:
            host = rest
        return (via, host) if host else None
    if key in ("", "tmux", "local-tmux"):
        return ("local", "")
    for prefix in ("local:", "local-"):
        if key.startswith(prefix) and key[len(prefix):] in ROUTE_MULTIPLEXERS:
            return ("local", "")
    return None


def occupant_in_worktrees(sessions, sid, roots, path_of, common, skip_offbox=False):
    for other_id, other in sessions.items():
        if other_id == sid:
            continue
        why = _occupant(other_id, other, roots, path_of, common, skip_offbox)
        if why:
            return why
    return ""


def _occupant(other_id, other: dict, roots, path_of, common, skip_offbox: bool) -> str:
    """Why one other session occupies one of `roots`, or "" when it does not."""
    backend = other.get("backend_type")
    if not isinstance(backend, str):
        return f"cannot read backend of session {other_id} for worktree check"
    place = session_place(backend)
    if place is None:
        return f"cannot judge backend {backend!r} of session {other_id} for worktree check"
    off_box = place[0] != "local"
    try:
        cwd = path_of(other.get("cwd"))
    except (OSError, ValueError, RuntimeError) as exc:
        # An ssh/wsl cwd that does not resolve here is a path on another
        # machine, not a refusal to judge this one. A wsl row whose cwd
        # *does* resolve is on this filesystem and is checked below.
        if skip_offbox and off_box:
            return ""
        return f"cannot resolve cwd of session {other_id}: {exc}"
    for path, root in roots:
        # commonpath compares components, unlike a prefix check which
        # confuses worktree and worktree-other. Separate Windows drives
        # cannot contain one another.
        try:
            inside = common((root, cwd)) == root
        except ValueError:
            inside = False
        if inside:
            return f"session {other_id} uses worktree {path} (cwd {other['cwd']})"
    return ""


def worktree_release_blocker(sid: str) -> str:
    """Refuse deletion when another active session sits in a worktree this would take down.

    Thurbox's teardown uses `worktrees[].created_by_thurbox`, not cwd or a
    directory naming convention. The flag is absent-means-true on the wire —
    the same rule thurbox uses when an older host omits it — so a missing key
    is treated as owned. Read a fresh list before each deletion: a session
    made by hand is not in the queue, and even an idle or stopped session can
    still hold work worth keeping. Missing data is not permission to destroy
    it. A remote target is judged on the host's own session list, over the
    same ssh path `host_reachable` uses; a backend this file has no occupancy
    rule for is the operator's to delete.
    """
    sessions, why = session_snapshot()
    if sessions is None:
        return why
    target = sessions.get(sid)
    if target is None:
        return "session disappeared during the worktree check; retry next pass"
    owned, why = owned_worktree_paths(target)
    if why:
        return why
    if not owned:
        return ""
    backend = target.get("backend_type")
    place = session_place(backend)
    if place is None:
        return f"cannot judge backend {backend!r} of session {sid}"
    via, host = place
    if via == "local":
        try:
            roots = [(path, local_resolved_path(path)) for path in owned]
        except (OSError, ValueError, RuntimeError) as exc:
            return f"cannot resolve session worktree: {exc}"
        return occupant_in_worktrees(
            sessions, sid, roots, local_occupant_path, os.path.commonpath, skip_offbox=True,
        )
    if via == "ssh":
        return _host_release_blocker(sid, host, owned)

    # A WSL distro: on this filesystem, spelled as POSIX paths.
    def wsl_path(value):
        return documented_path(value, True)

    try:
        roots = [(path, wsl_path(path)) for path in owned]
    except (OSError, ValueError, RuntimeError) as exc:
        return f"cannot resolve session worktree: {exc}"
    return occupant_in_worktrees(
        sessions, sid, roots, wsl_path, posixpath.commonpath,
    )


def _host_release_blocker(sid: str, host: str, owned: list) -> str:
    """`worktree_release_blocker` for a session on an ssh host, judged on its own list."""
    remote, entry, why = host_session_snapshot(host)
    if remote is None:
        return why
    # Occupancy skips the target by id. That only holds when the host
    # lists this session under the same id the local mirror does. A
    # non-empty list without that row cannot tell an occupant from the
    # target itself, so this is a named keep rather than a silent one.
    if remote and sid not in remote:
        return (
            f"host session list has no row {sid}; "
            "cannot tell an occupant from this session"
        )
    # The host's platform, never the route's multiplexer: thurbox serves
    # psmux on a POSIX host and tmux on a Windows one.
    posix = host_platform(entry) == "posix"
    common = posixpath.commonpath if posix else ntpath.commonpath

    def host_path(value):
        return documented_path(value, posix)

    try:
        roots = [(path, host_path(path)) for path in owned]
    except (OSError, ValueError, RuntimeError) as exc:
        return f"cannot resolve session worktree: {exc}"
    return occupant_in_worktrees(remote, sid, roots, host_path, common)


def session_state(sid: str) -> tuple[str | None, str]:
    """What thurbox says the session is doing, or None with the reason it cannot say.

    `session get` and not `session list`: only `get` probes the pane, and that
    probe is the whole difference between `uncovered` — this agent is wired to
    report nothing, so its silence means nothing — and `running`, an agent
    holding the pane right now. A reap that read the cheaper answer would
    delete both.
    """
    doc, why = session_doc(sid)
    if doc is None:
        return None, why
    state = doc.get("state")
    if not state:
        return None, "thurbox-cli session get answered no state"
    return str(state), ""


def session_status(sid: str, live: set | None) -> str:
    """Where a session stands against a `session list` snapshot: 'gone', a
    thurbox-reported state, or 'unknown'.

    'gone' only when the snapshot itself proves the id absent. A snapshot
    that could not be taken, or a `session get` that could not read the pane
    despite the id being listed, both answer 'unknown' — and unknown must
    never be read as gone, or a probe hiccup looks exactly like a session
    that finished.
    """
    if live is None:
        return "unknown"
    if sid not in live:
        return "gone"
    state, _why = session_state(sid)
    return state or "unknown"


def record_reaped(task: Task, sid: str, how: str) -> None:
    """The receipt.

    `session` is cleared so `list` and `show` stop pointing at an id that no
    longer resolves; the id itself moves into `reaped`, because "which session
    did this task have" stays a real question after the session is gone.
    """
    task.doc["reaped"] = {"session": sid, "how": how, "at": now()}
    task.doc["session"] = None
    task.save()


# What a build makes and the next build makes again, freed from a worktree the
# queue lets go of. Rust's `target/` alone held 25-30 GB per worktree on one
# machine, and a session that was already gone took none of it with it.
BUILD_OUTPUT = ("target", "node_modules", ".venv")

# How `git worktree list --porcelain` opens each worktree's block.
WORKTREE_LINE = "worktree "


def task_worktrees(task: Task) -> list[str]:
    """Where this task's branch is checked out on this machine, one per repository.

    Asked of git and not of thurbox, because the case this serves is a session
    thurbox no longer has. A `--host` task's worktrees are on that machine.
    """
    if task.doc.get("host") or not task.doc.get("branch"):
        return []
    want = f"branch refs/heads/{task.doc['branch']}"
    found = []
    for unit in task_repos(task):
        here = None
        for line in git_out(unit["path"], ["worktree", "list", "--porcelain"]).splitlines():
            if line.startswith(WORKTREE_LINE):
                here = line[len(WORKTREE_LINE):]
            elif line == want and here and not same_path(here, unit["path"]):
                found.append(here)
    return found


def build_output(tree: str) -> list[str]:
    """Every BUILD_OUTPUT directory under `tree` that is safe to delete.

    SAFE MEANS THREE THINGS, and each is a way a shared directory gets lost. It
    is a real directory and not a link (or a junction) to a store something
    else uses; nothing on the way to it is a link either, so it is inside this
    worktree; and git says it is IGNORED with nothing under it TRACKED — a
    directory that happens to be called `target` and holds source is source.
    Anything git cannot answer about is kept.
    """
    found = _named_build_dirs(tree)
    if not found:
        return []
    rel = [os.path.relpath(f, tree).replace(os.sep, "/") for f in found]
    try:
        ignored = subprocess.run(
            ["git", "-C", tree, "check-ignore", "--", *(r + "/" for r in rel)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        tracked = subprocess.run(
            ["git", "-C", tree, "ls-files", "--", *rel],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if ignored.returncode not in (0, 1) or tracked.returncode != 0:
        return []
    said = {line.rstrip("/") for line in ignored.stdout.splitlines()}
    held = tracked.stdout.splitlines()
    return [
        full for full, r in zip(found, rel)
        if r in said and not any(t == r or t.startswith(r + "/") for t in held)
    ]


def _named_build_dirs(tree: str) -> list[str]:
    """Every real directory under `tree` named in BUILD_OUTPUT, not descending into one."""
    found = []
    for here, dirs, _files in os.walk(tree):
        keep = []
        for name in dirs:
            full = os.path.join(here, name)
            if name == ".git" or fleet_platform.is_dir_link(full):
                continue
            if name in BUILD_OUTPUT:
                found.append(full)
            else:
                keep.append(name)
        dirs[:] = keep
    return found


def worktree_occupant(sessions, tree: str, sid: str | None = None) -> str:
    """Why reap must leave this worktree alone, or "" when it may act on it.

    ONE GATE FOR BOTH THINGS reap does to a worktree it lets go of: free the
    build output under it, and hand it to the operator's own command
    (`WORKTREE_CMD` below). Somebody carrying the work on may be building in
    it right now, or running whatever that command would take down — so a
    worktree another session sits in is left whole, and so is one whose
    occupancy could not be judged, because a guess here is someone else's
    work. Two actions, one answer, rather than two that could disagree.

    `sid` is the session being released, which is never its own occupant: the
    deletion path asks this BEFORE `session delete --force` runs, while that
    session is still listed sitting in the worktree it is about to take down.

    THE PATH IS PLACED EVEN WHEN THE DIRECTORY IS ALREADY GONE, exactly as
    another session's cwd is (`local_occupant_path`), because `session delete
    --force` takes the worktree with the session and the operator's command
    still has something to say about it afterwards. Nothing is freed on the
    strength of that: a directory that is not there holds no build output.
    """
    try:
        roots = [(tree, local_occupant_path(tree))]
    except (OSError, ValueError, RuntimeError) as exc:
        return f"cannot place worktree {tree}: {exc}"
    return occupant_in_worktrees(
        sessions, sid, roots, local_occupant_path, os.path.commonpath, skip_offbox=True,
    )


def free_build_output(ref: str, trees: list[str], dry: bool) -> int:
    """Delete the build output in each worktree, saying what went. Returns how many.

    A worktree another session sits in is kept whole: somebody carrying the
    work on may be building in it right now. The same occupancy rule `reap`
    applies before a release, and the same refusal when it cannot be judged.
    """
    freed = 0
    sessions = None
    for tree in trees:
        if sessions is None:
            sessions, why = session_snapshot()
            if sessions is None:
                print(f"    {ref:<46} kept       build output in {tree}: {why}")
                return freed
        # ASKED STRICTLY HERE, and not through the gate: `worktree_occupant`
        # places a path whose directory is already gone, which is what the
        # command below needs and what a deletion must never be run on the
        # strength of. Nothing changed for this caller when that gate was
        # extracted, and this line is why.
        try:
            local_resolved_path(tree)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"    {ref:<46} kept       build output in {tree}: {exc}")
            continue
        busy = worktree_occupant(sessions, tree)
        if busy:
            print(f"    {ref:<46} kept       build output: {busy}")
            continue
        freed += _free_paths(ref, build_output(tree), dry)
    return freed


def _free_paths(ref: str, paths: list[str], dry: bool) -> int:
    """Delete each build-output path, saying what went. Returns how many."""
    freed = 0
    for path in paths:
        if dry:
            print(f"    {ref:<46} would free {path}")
            freed += 1
            continue
        try:
            fleet_platform.remove_tree(path)
        except OSError as exc:
            print(f"    {ref:<46} kept       {path}: {exc}", file=sys.stderr)
            continue
        print(f"    {ref:<46} freed      {path}")
        freed += 1
    return freed


# --- the operator's own command for a worktree reap lets go of ---------------
#
# WHAT FLEET CANNOT KNOW ABOUT A WORKTREE. `BUILD_OUTPUT` above is everything
# reap can name for itself: directories a build makes and the next build makes
# again. A worker also starts things OUTSIDE the worktree that are keyed by it
# — a container stack, a database, a daemon, a tunnel — and every one of those
# belongs to a tool fleet has never heard of. Measured on one machine on
# 2026-10-13: eleven such stacks whose recorded working directory pointed at a
# worktree reap had already removed, holding 55 containers, 14 images, 38
# named volumes (3.0 GB) and 127 anonymous volumes with no container left
# (23.0 GB) — on a non-sparse virtual disk, which grows to its peak and never
# shrinks, so each remnant cost that space for good.
#
# SO IT IS A SEAM AND NOT A CLEANUP, for the same reason `publish.conf` holds
# no tool name and `forge.py` takes plugins: the one command that can take
# those down is the operator's, and naming theirs here would ship one
# operator's machine to every clone. Fleet contributes the three things only
# it knows — WHICH worktree, WHOSE task, and WHEN it is nobody's any more —
# and runs what it is given.
#
# WHAT THE COMMAND IS GIVEN, and why each: the worktree's ABSOLUTE PATH, as
# the first argument, because that path is how a tool outside fleet recorded
# what it made (and its last component is how such a tool names a project by
# default), and the TASK REF as the second, so a command can log or decide per
# task. Nothing is passed in the environment: an operator reads argv in any
# language, and one record of the answer beats two.
#
# WHEN IT RUNS. Wherever reap LETS A WORKTREE GO, which is four places and not
# one: a vanished session's task retiring, an id reap drops because thurbox no
# longer has it, a session reap deletes with `--force` — which takes the
# worktree with it, so the paths are read from git BEFORE the deletion and the
# command runs after it, on a directory that is gone and a stack that is not —
# and a fixer's checkout `git worktree remove` takes back.
#
# WHAT IT MAY NOT DO: hold up the reap. A failure, a timeout, a line that is
# not a command — each is printed and the task still lands, because the
# artifact merged whatever the operator's machine thinks, and a task held open
# over a leftover stack would hold its dependents too. It is bounded and
# killed with everything it started, exactly as a reconciler child is
# (`fleet_platform.run_bounded`): a command that hangs costs one pass.
# NOT `ON_WORKTREE_RELEASED`, and neither half of that name is an accident.
# `ON_LANDED` / `ON_CLOSED` above hold a WORD fleet knows (`delete`, `notify`)
# and this holds a command line fleet never reads, which `_CMD` is how the
# rest of the settings say; and `<NAME>_SECS` is the reconciler's family of
# clocks, read once when the loop starts and overridable by
# `FLEET_RECONCILE_<NAME>_SECS`, while this bound is read on every reap, by
# hand as much as by the loop, and has no environment override.
WORKTREE_CMD = "WORKTREE_RELEASED_CMD"
WORKTREE_CMD_SECS = "WORKTREE_RELEASED_CMD_SECS"

#
# WHAT ITS BOUND IS AND IS NOT: it is PER COMMAND, not per pass. A reap that
# releases many worktrees at once spends it once for each, and the reconciler
# bounds the whole `collect` it runs this inside (`BOUND_SECS`, 600s by
# default) — so an operator whose teardown is slow AND whose passes release
# several worktrees sets this lower rather than higher, or that one pass is
# killed and its remaining worktrees wait for the next.
WORKTREE_CMD_DEFAULT_SECS = 60

# A bound this long is not a bound: an hour is already longer than any
# teardown, and `collect` is bounded at 600s above it, so every value past
# this is a typo. A CEILING AND NOT A LIMIT OF THE MACHINE — measured here,
# `communicate(timeout=…)` takes 4000 and 86400 happily and only raises
# `OverflowError: timeout is too large` around 3e6 — but a refusal here is
# cheap, says which setting, and keeps the absurd end of the range away from a
# call whose only guard is `except OSError`. Whole seconds, `int` like every
# other clock in this file, which is also what keeps `inf` and `nan` out: read
# as floats they pass every "is it positive" test there is, and `inf` reaches
# that same raise out of the pass.
WORKTREE_CMD_MAX_SECS = 3600


def worktree_command() -> tuple[list[str], int, str]:
    """The operator's command for a released worktree: (argv, its bound, why not).

    `([], 0, "")` is a fleet that configured none, which is every fresh clone
    and the tracked example — and then reap does exactly what it always did.
    `([], 0, why)` is a setting that cannot be read as a command or a bound:
    the line is named and nothing runs, never raised, because a quote left
    open must not take down a reap pass that had nothing to do with it.
    """
    conf = reconcile_conf()
    line = conf.get(WORKTREE_CMD, "").strip()
    if not line:
        return [], 0, ""
    try:
        argv = fleet_platform.split_command(line)
    except ValueError as exc:
        return [], 0, f"{WORKTREE_CMD}: {exc}"
    if not argv:
        return [], 0, ""
    raw = conf.get(WORKTREE_CMD_SECS, "").strip()
    secs = WORKTREE_CMD_DEFAULT_SECS
    if raw:
        try:
            secs = int(raw)
        except ValueError:
            secs = 0
    if not 0 < secs <= WORKTREE_CMD_MAX_SECS:
        return [], 0, (
            f"{WORKTREE_CMD_SECS}={raw} is not a whole number of seconds between 1 and "
            f"{WORKTREE_CMD_MAX_SECS}; {WORKTREE_CMD} was not run"
        )
    return argv, secs, ""


def command_worktrees(task: Task) -> list[str]:
    """This task's worktrees, asked only when the operator has a command for them.

    WITH NO SETTING, NOTHING ABOUT A REAP CHANGES — not even one more `git
    worktree list` per task. The deletion path needs the paths before it
    deletes the session, which is the one place nothing else was asking for
    them, so the question is asked there only when there is something to do
    with the answer. A setting that cannot be read still counts as configured:
    `run_worktree_command` is what names it, wherever it was reached.
    """
    argv, _secs, why = worktree_command()
    return handover_worktrees(task) if argv or why else []


def worktree_key(tree: str) -> str:
    """One key per worktree for a pass's `decided` set, however it was spelled.

    The placed path, because the four release points reach the same directory
    by different routes — git lists a task's worktrees, a fixer's checkout is
    assembled from the queue root — and two spellings of one worktree must
    count as one. A path that cannot be placed is keyed by itself: the gate
    refuses it anyway, and a key only has to be stable.
    """
    try:
        return local_occupant_path(tree)
    except (OSError, ValueError, RuntimeError):
        return tree


def fixer_checkout_paths(task: Task) -> list[str]:
    """Where this task's fixer checkout would be — now, and before the move."""
    slug = f"{task.topic}__{task.id}"
    return [
        os.path.join(fixer_worktrees_root(), slug),
        os.path.join(queue_root(), ".worktrees", slug),
    ]


def handover_worktrees(task: Task) -> list[str]:
    """The task's worktrees a GENERIC release point may hand to the command.

    NOT THE FIXER'S CHECKOUT, which git lists among them whenever
    `branch_checkout` cut it on the task's own branch — and it usually does.
    That checkout has a release point of its own, and that one is the only
    place that knows whether `git worktree remove` actually took it back: a
    removal git REFUSES means files it does not track are in there, which is
    work somebody has not finished. The vanished sweep runs BEFORE that point,
    so without this it handed over a directory nothing had even tried to
    release yet, and the same pass then printed `kept fixer checkout`.

    The build output is a different question and keeps asking git's whole
    answer: `release_fixer_checkouts` frees it on purpose before the removal,
    because untracked build output is exactly what the removal would refuse
    over.
    """
    skip = {worktree_key(path) for path in fixer_checkout_paths(task)}
    return [tree for tree in task_worktrees(task) if worktree_key(tree) not in skip]


def run_worktree_command(ref: str, trees: list[str], dry: bool, sid: str | None = None,
                         decided: set | None = None) -> int:
    """Hand each released worktree to the operator's command. Returns how many ran.

    The same refusals as `free_build_output`: a worktree another session sits
    in is not handed over, and neither is one whose occupancy cannot be
    judged (`worktree_occupant` owns both, and `sid` is why the session being
    deleted is not read as its own occupant).

    ONCE PER WORKTREE PER PASS, which is what `decided` carries. Two tasks on
    one branch share one worktree, so two release points can reach it in a
    single pass, and a teardown is not promised to be idempotent — the second
    call is at best noise and at worst takes down what the first one's
    replacement raised. ONLY RUNNING COUNTS as having answered for a worktree.
    A refusal does not, and that distinction is the whole point: the reason a
    worktree is refused can be a session THIS PASS is about to delete, and
    marking that refusal down would lose the one hand-over that mattered.
    """
    argv, secs, why = worktree_command()
    if why:
        print(f"    {ref:<46} NOT RUN    {why}", file=sys.stderr)
        return 0
    if not argv or not trees:
        return 0
    ran = 0
    sessions = None
    for tree in trees:
        # ONE SPELLING, whichever route the path arrived by. `git worktree
        # list` answers in forward slashes on Windows and fleet's own
        # `os.path.join` in backslashes, so without this an operator's command
        # would be handed two spellings of one path depending on which release
        # point reached it — and a teardown that compares the path against
        # what some other tool recorded compares strings.
        tree = os.path.normpath(tree)
        key = worktree_key(tree)
        if decided is not None and key in decided:
            continue
        if sessions is None:
            sessions, snap_why = session_snapshot()
            if sessions is None:
                print(f"    {ref:<46} kept       {WORKTREE_CMD} for {tree}: {snap_why}")
                return ran
        busy = worktree_occupant(sessions, tree, sid)
        if busy:
            print(f"    {ref:<46} kept       {WORKTREE_CMD}: {busy}")
            continue
        ran += _hand_over(ref, [*argv, tree, ref], secs, tree, dry, decided, key)
    return ran


def _hand_over(ref: str, argv_here: list, secs, tree: str, dry: bool,
               decided: set | None, key: str) -> int:
    """Run (or, dry, name) the operator's command on one worktree. 1 when it ran."""
    # Asked for, so nothing asks again. Set before the run and not after
    # it: a command that fails has still been pointed at this worktree,
    # and pointing a second one at it is not the retry to make.
    if decided is not None:
        decided.add(key)
    if dry:

        print(f"    {ref:<46} would run  {shell_quote(argv_here)}")
        return 1
    return 1 if _run_worktree_argv(ref, argv_here, secs, tree) else 0


def _run_worktree_argv(ref: str, argv_here: list, secs, tree: str) -> bool:
    """Run the operator's command on one worktree, bounded. True when it succeeded."""
    try:
        done = fleet_platform.run_bounded(
            argv_here, secs, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        print(f"    {ref:<46} FAILED     {WORKTREE_CMD}: {exc}; {tree} may still be "
              "held outside fleet", file=sys.stderr)
        return False
    if done is None:
        print(f"    {ref:<46} FAILED     {WORKTREE_CMD} killed after {secs}s, its bound "
              f"({WORKTREE_CMD_SECS}); {tree} may still be held outside fleet",
              file=sys.stderr)
        return False
    if done.returncode != 0:
        said = (done.stdout or "").strip().splitlines()
        print(f"    {ref:<46} FAILED     {WORKTREE_CMD} exit {done.returncode}: "
              + (said[-1] if said else NO_OUTPUT), file=sys.stderr)
        return False
    print(f"    {ref:<46} released   {tree}  ({WORKTREE_CMD})")
    return True


def release_fixer_checkouts(q: Queue, state_of, dry: bool, decided: set | None = None) -> int:
    """Take back the checkout `branch_checkout` cut for a finished task's fixer.

    Nothing else does. git cut it, not thurbox, so `session delete` leaves it:
    on 2026-09-13 one reported `removed_worktrees: []` and the lead removed the
    checkout by hand. Released on the same states `reap` releases a session on,
    and looked for where fixers go now and where they went before the move.

    `git worktree remove` without `--force`, so a checkout holding uncommitted
    work is kept and said out loud rather than thrown away.
    """
    acted = 0
    for task in sorted(q.tasks.values(), key=lambda t: t.ref):
        if state_of(task) not in ("landed", "abandoned") or task.doc.get("host"):
            continue
        slug = f"{task.topic}__{task.id}"
        for path in (
            os.path.join(fixer_worktrees_root(), slug),
            os.path.join(queue_root(), ".worktrees", slug),
        ):
            if not os.path.isdir(path):
                continue
            acted += 1
            _release_fixer_checkout(task, path, dry, decided)
    return acted


def _release_fixer_checkout(task: Task, path: str, dry: bool, decided: set | None) -> None:
    """Free, remove and hand over one fixer checkout that is still on disk."""
    # Before the remove, which refuses a checkout holding files git
    # does not track, and build output is exactly that.
    free_build_output(task.ref, [path], dry)
    if dry:
        # NO `would run` HERE. Whether this checkout is handed over
        # depends on a `git worktree remove` that has not been tried,
        # and a dry run that printed the command would promise exactly
        # what a refused removal cancels.
        argv, _secs, why = worktree_command()
        after = ", then hand it over" if argv or why else ""
        print(f"    {task.ref:<46} would remove fixer checkout {path}{after}")
        return
    proc = _remove_fixer_worktree(task, path)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip().splitlines()
        print(
            f"    {task.ref:<46} kept       fixer checkout {path}: "
            + (err[-1] if err else "git worktree remove failed"),
            file=sys.stderr,
        )
        # HANDED OVER TO NOBODY. git refused this checkout because it
        # holds files it does not track, which is work somebody has
        # not finished, and no other release point reaches it
        # (`handover_worktrees`) — a teardown aimed at a worktree
        # still being worked in is the one mistake here that costs
        # more than the leak it was added to stop.
        return
    print(f"    {task.ref:<46} removed    fixer checkout {path}")
    # A fixer's checkout is a worktree an agent worked in too, so what
    # it started outside it is the operator's to take down as well.
    run_worktree_command(task.ref, [path], dry, decided=decided)


def _remove_fixer_worktree(task: Task, path: str) -> subprocess.CompletedProcess:
    """`git worktree remove` of a fixer checkout; the last attempt's result."""
    # Asked of each repository the task spans, because `shepherd` cuts
    # the checkout off whichever one the change request was in and only
    # that one's git knows about the worktree. A task with one
    # repository asks exactly the one it always asked.
    for unit in task_repos(task):
        proc = subprocess.run(
            ["git", "-C", unit["path"], "worktree", "remove", path],
            capture_output=True, text=True, encoding="utf-8",
        )
        if proc.returncode == 0:
            break
    return proc


# THE SESSIONS NO VERB HERE MAY TAKE AS A WORKER'S. The session the command
# runs in has always been refused by id, from THURBOX_SESSION; with a conductor
# that is no longer the lead's alone, since the lead, the conductor and any
# origin may each run `reap` or `refuel`, and the reconciler runs both from no
# session at all. So this checkout's lead and conductor are refused BY NAME as
# well — `notify_conductor.control_sessions`, the one reading of who they are —
# whoever runs the command. Asked only when there is a session to judge.
# `reap` hands in its pass's one `session list` (see `reap`): a second list
# taken here for the lead would be a second answer the pass could disagree
# with, and one that fails after the first answered keeps every holder.
CONTROL_WHY = {
    "self": "that is the lead's own session, not a worker's",
    "lead": "that is the lead's own session, not a worker's",
    "conductor": "that is the conductor's own session, not a worker's",
}


def control_session_ids(rows: list | None = None) -> dict[str, str]:
    """{session id: "self" | "lead" | "conductor"}: the sessions reap and refuel refuse.

    `rows` is a `session list` already taken; None asks thurbox for one.
    """
    found: dict[str, str] = {}
    try:
        notify = _load_sibling("fleet_notify_conductor", "notify_conductor.py")
        found.update(notify.control_sessions(rows))
    except Exception:  # noqa: BLE001 — unknown refuses nothing more than before
        pass
    inside = os.environ.get("THURBOX_SESSION")
    if inside:
        found[inside] = "self"
    return found


def reap(q: Queue, dry: bool = False, release: bool = True) -> int:
    """Land what has landed, then release the session of every task that is finished.

    Only ever acts on a session THIS QUEUE recorded. The lead's own session and
    anything made by hand are not in the records and so are never candidates;
    the lead's is additionally named and refused, because a task attached to it
    by mistake would otherwise be a deletion — and so is the conductor's
    (`control_session_ids`).

    ITS VIEW IS `all`, AND BOTH CALLERS BUILD IT THAT WAY. A task that lands
    while its worker is not yet at rest is KEPT — correctly — and a keep is a
    promise to look again on a later pass. In the SAME pass the topic has
    nothing unfinished left in it and archives, also correctly, and archived
    topics leave the default view: there is no later pass, and the session and
    the git worktree under it are stranded for good. Three of them had
    accumulated on one machine, every one `idle` or `done` and reapable for
    hours while `reap --dry-run` reported `would release 0 session(s)`;
    releasing them took that disk from 83% to 51%. The window is the common
    case rather than an edge one, because a worker that has just written
    `result.md` is exactly a worker that is `working` for a moment longer.

    So the view built to hide finished topics from a PERSON is the wrong input
    for the one command whose whole job is work that is already terminal —
    the same reading `cmd_shepherd` makes, and for the same reason. `list`,
    `list --archived`, `fleet status` and the pane go on hiding them: nothing
    here reads the flag, and the `sweep_archives` call below still skips a
    topic that already carries it.

    WIDENING WHAT IT SEES DOES NOT WIDEN WHAT IT ACTS ON. The gate is
    untouched and is the only thing that decides a deletion:
    REAPABLE_SESSION_STATES only, a remote session's host probed first, and the
    lead's own session refused by name. It costs no extra forge call either —
    a topic archives only once every task in it is terminal, so an archived
    task is never `done` and `sweep_landings` asks about none of them — and it
    stays idempotent, because the pass that releases a session clears the id
    off the record and finds no holder on the next one.

    Returns how many tasks it had something to say about, so a caller can stay
    silent when there was nothing.
    """
    landings = sweep_landings(q, dry)
    acted = sum(1 for kind, _ in landings.values() if kind in LANDED_STATE)
    if acted and not dry:
        print("      Run `fleet queue plan` — a blocker clears when the task it names LANDS.")

    # A dry run promotes nothing, so the state on disk still says `done` for a
    # task that just landed. Project the sweep's answer forward instead, or a
    # dry run would report every reap it is about to do as a task it is keeping.
    def state_of(task: Task) -> str:
        kind = (landings.get(task.ref) or (None,))[0]
        return LANDED_STATE.get(kind, task.state) if dry else task.state

    # ONE `session list` for the pass, taken when something first needs it:
    # the vanished sweep and the holders below read the same answer.
    taken: list = []

    # Which worktrees this pass has already answered the operator's command
    # for (`run_worktree_command`'s docstring owns the rule). Pass-scoped, so
    # the next reap asks again about anything that is still there.
    decided: set[str] = set()

    def rows() -> tuple[dict | None, str]:
        if not taken:
            taken.append(session_snapshot())
        return taken[0]

    def snapshot() -> tuple[set | None, str]:
        sessions, why = rows()
        return (set(sessions) if sessions is not None else None), why

    # Before the archive sweep, because a retired task can be the last thing
    # holding its topic open. Not under `--no-reap`: retiring a vanished
    # session drops its id and frees its worktree's build output, which is
    # exactly the session work that flag promises to leave alone.
    if release:
        acted += sweep_vanished(q, dry, snapshot, decided)

    # A topic can only become finished when one of its tasks moves into a
    # terminal state, and this is the pass that moves them — so the flag is
    # written here rather than left as something the lead has to remember.
    # Before the `--no-reap` return: archiving is a flag on a topic and has
    # nothing to do with whether a session is released.
    acted += sweep_archives(q, state_of, dry)

    if not release:
        print("      Sessions left alone (--no-reap); `fleet queue reap` releases them.")
        return acted

    # Before the holders: a task whose own session is long gone can still have
    # a fixer checkout on disk.
    acted += release_fixer_checkouts(q, state_of, dry, decided)
    return acted + _reap_holders(q, state_of, landings, rows, dry, decided)


def _reap_holders(q: Queue, state_of, landings: dict, rows, dry: bool,
                  decided: set) -> int:
    """`reap`'s last step: every finished task still holding a session. Returns how many."""
    acted = 0
    holders = [

        t
        for t in sorted(q.tasks.values(), key=lambda t: t.ref)
        if t.doc.get("session") and state_of(t) in HOLDING_STATES
    ]
    if not holders:
        return acted

    # The lead and the conductor are found in the pass's one list. When that
    # list could not be read none is found, and none needs to be: without it
    # every holder is kept below, so nothing is deleted on the gap.
    sessions, live_detail = rows()
    lead = control_session_ids(list(sessions.values()) if sessions is not None else [])
    live: set | None = set(sessions) if sessions is not None else None

    reaped = kept = dropped = 0
    for task in holders:
        acted += 1
        r, k, d = _reap_holder(task, state_of(task), lead, landings, live, live_detail,
                               dry, decided)
        reaped, kept, dropped = reaped + r, kept + k, dropped + d

    if reaped or kept or dropped:
        line = (
            f"reap: {'would release' if dry else 'released'} {reaped} session(s), "
            f"kept {kept}"
        )
        if dropped:
            line += f", dropped {dropped} stale id(s)"
        print(line)
    return acted


def _reap_hold_reason(task: Task, sid: str, state: str, lead: dict, landings: dict,
                      live: set | None, live_detail: str) -> str:
    """Why `reap` keeps this holder's session before asking thurbox, or ""."""
    if sid in lead:
        return CONTROL_WHY[lead[sid]]
    if review_lifecycle().holds_reviewer(sys.modules[__name__], task):
        return "configured reviewer remains responsible for current and future heads"
    if state in ("stuck", "failed"):
        return (
            f"the worker's own verdict is `{state}` — its session is the "
            "evidence, and a human decides"
        )
    if state == "done":
        # `done` is not landed whatever the check said, and an empty reason —
        # a record with no artifacts gives one — must still keep it.
        return landings.get(task.ref, ("", ""))[1] or "its artifact has not landed"
    if live is None:
        return live_detail
    return ""


def _reap_holder(task: Task, state: str, lead: dict, landings: dict, live: set | None,
                 live_detail: str, dry: bool, decided: set) -> tuple[int, int, int]:
    """Release, keep or drop one holder's session. (reaped, kept, dropped)."""
    sid = task.doc["session"]
    why = _reap_hold_reason(task, sid, state, lead, landings, live, live_detail)
    if why:
        print(f"    {task.ref:<46} kept       {why}")
        return 0, 1, 0

    # A remote session is asked about its HOST first. Both answers below
        # would otherwise be wrong for a host that is merely down: an absent id
        # would read as a session that finished, and a latched `idle` would
        # read as an agent at rest. See REAPABLE_SESSION_STATES.
    if task.doc.get("host"):
        up, unreachable_why = host_reachable(task.doc["host"])
        if not up:
            print(f"    {task.ref:<46} kept       unreachable: host "
                  f"{task.doc['host']} — {unreachable_why}")
            return 0, 1, 0

    # What this ending does to a live session. A `closed` landing is the
    # change request closed unmerged; an abandon by hand keeps today's
    # delete whatever the setting, because nothing merged or closed.
    closed = (landings.get(task.ref) or (None,))[0] == "closed" or (
        state == "abandoned" and abandon_how(task) == "closed-unmerged")
    ending = session_ending(closed) if state in ("landed", "abandoned") else "delete"
    if ending != "delete" and sid in live:
        return 0, keep_session(task, sid, ending, closed, dry), 0

    if sid not in live:
        return 0, 0, _drop_gone(task, sid, dry, decided)

    live_state, detail = session_state(sid)
    if live_state not in REAPABLE_SESSION_STATES:
        print(
            f"    {task.ref:<46} kept       thurbox says `{live_state or detail}`; "
            "only " + ", ".join(REAPABLE_SESSION_STATES) + " are reaped"
        )
        return 0, 1, 0

    blocker = worktree_release_blocker(sid)
    if blocker:
        print(f"    {task.ref:<46} kept       {sid}: {blocker}")
        return 0, 1, 0
    return _reap_delete(task, sid, live_state, dry, decided)


def _drop_gone(task: Task, sid: str, dry: bool, decided: set) -> int:
    """A holder thurbox no longer lists: drop its id and free its worktree. Returns 1 if dropped."""
    dropped = 0
    if dry:
        print(f"    {task.ref:<46} would drop {sid}  thurbox no longer has it")
    else:
        record_reaped(task, sid, "already gone")
        print(f"    {task.ref:<46} gone       {sid}  thurbox no longer had it")
        dropped = 1
    # A session that went without `delete --force` left its worktree,
    # and the worktree its build output.
    trees = task_worktrees(task)
    free_build_output(task.ref, trees, dry)
    run_worktree_command(task.ref, handover_worktrees(task), dry, decided=decided)
    release_companions(task, dry)
    return dropped


def _reap_delete(task: Task, sid: str, live_state: str, dry: bool,
                 decided: set) -> tuple[int, int, int]:
    """Delete a session at rest, then hand its worktrees over. (reaped, kept, dropped)."""
    # READ BEFORE THE DELETION, which takes the worktree with the session:
    # after it git lists none for this branch and the path the operator's
    # command needs is unrecoverable. Asked only when there is a command
    # (`command_worktrees`), so an unconfigured fleet runs nothing extra.
    released = command_worktrees(task)

    if dry:
        print(f"    {task.ref:<46} would reap {sid}  ({live_state})")
        run_worktree_command(task.ref, released, dry, sid, decided)
        release_companions(task, dry)
        return 1, 0, 0

    # `--force`, never a plain `delete`. Plain delete soft-deletes the row
    # and leaves the TUI to reap the tmux window and the worktrees on its
    # next sync; headless — which this is — that never happens and the disk
    # is never actually freed, which is the entire point.
    proc = subprocess.run(
        ["thurbox-cli", "session", "delete", sid, "--force"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip().splitlines()
        print(
            f"    {task.ref:<46} NOT REAPED  session delete failed: "
            + (err[-1] if err else NO_OUTPUT),
            file=sys.stderr,
        )
        return 0, 1, 0
    record_reaped(task, sid, "deleted")
    print(f"    {task.ref:<46} reaped     {sid}  ({live_state})")
    # After the deletion, on a worktree that is gone and whatever it
    # started that is not. `sid` is already deleted, so it cannot be read
    # as its own occupant whichever list answers.
    run_worktree_command(task.ref, released, dry, sid, decided)
    release_companions(task, dry)
    return 1, 0, 0


def keep_session(task: Task, sid: str, ending: str, closed: bool, dry: bool) -> int:
    """`ON_LANDED`/`ON_CLOSED=notify`: keep the session, and tell it once. Returns 1.

    The task has already moved — `landed` or `abandoned`, blockers cleared,
    topic archived — exactly as under `delete`. Only the deletion and the
    build-output cleanup are skipped. Once the line is typed the hold is
    released as `kept`, so no later pass sends it again and `fleet sessions
    orphans` lists the session for the operator to delete when done with it.
    """
    key = "ON_CLOSED" if closed else "ON_LANDED"
    if ending not in SESSION_ENDINGS:
        print(f"    {task.ref:<46} kept       {key}={ending} is neither "
              f"{' nor '.join(SESSION_ENDINGS)}; the session is left as it is")
        return 1
    live_state, detail = session_state(sid)
    if live_state == "stopped":
        # No agent is running to read a line, and nothing is lost by keeping it.
        if dry:
            print(f"    {task.ref:<46} would keep {sid}  ({key}=notify; stopped, nothing to tell)")
        else:
            record_reaped(task, sid, "kept")
            print(f"    {task.ref:<46} kept       {sid}  ({key}=notify; stopped, nothing to tell)")
        return 1
    if live_state not in SESSION_AT_REST:
        print(f"    {task.ref:<46} kept       {key}=notify; thurbox says "
              f"`{live_state or detail}` — told once it is at rest")
        return 1
    if dry:
        print(f"    {task.ref:<46} would tell {sid}  ({key}=notify)")
        return 1
    what = (task.doc.get("landing") or {}).get("detail") or (
        "its change request was closed unmerged" if closed else "it landed")
    line = (f"fleet: task {task.ref} is {'abandoned' if closed else 'landed'} — {what}. "
            f"This session is kept for follow-ups ({key}=notify); nothing is needed "
            "unless somebody asks. `fleet sessions orphans` lists it for deletion.")
    ok, report = trust_and_send(sid, " ".join(line.split()))
    if not ok:
        print(f"    {task.ref:<46} kept       {key}=notify; not told yet ({report}) — again next pass")
        return 1
    record_reaped(task, sid, "kept")
    print(f"    {task.ref:<46} told       {sid}  ({key}=notify; kept for follow-ups)")
    return 1


def cmd_reap(args) -> int:
    # `all`, and deliberately not the default view — see `reap` above.
    q = Queue(queue_root(), scope="all")
    before = {ref: t.state for ref, t in q.tasks.items()}
    if reap(q, dry=args.dry_run) == 0:
        print("reap: nothing has landed and no finished task is still holding a session")
    if not args.dry_run:
        tell_origins(q, before)
    return 0


def origin_outcomes(q: Queue, before: dict) -> list[tuple[Task, str]]:
    """[(task, word)] for every task this pass moved to an end its origin is told of.

    TRANSITIONS, NEVER STANDING STATES: `before` is each task's state when the
    pass began, so a first run after an upgrade tells nobody about every task
    that landed last month. `shipped` is collect closing a task on a shipped
    result; `landed` and `abandoned` are reap's — or collect's own, for a change
    request closed unmerged. A task collected and landed in one pass is told
    both, in that order.
    """
    out = []
    for ref, task in sorted(q.tasks.items()):
        was, state = before.get(ref), task.state
        if was == state or not task.doc.get("origin"):
            continue
        if (task.doc.get("outcome") == "shipped" and state in ("done", "landed")
                and was not in ("done", "landed")):
            out.append((task, "shipped"))
        if state in ("landed", "abandoned"):
            out.append((task, state))
    return out


def tell_origins(q: Queue, before: dict) -> None:
    """Tell each origin how its task ended, through `notify_origin.py`, and say so.

    Run on every `collect` and `reap` even with nothing new, which is what
    retries an outcome still waiting for its origin to come to rest. NEVER A
    FAILURE OF THE PASS: a relay that fails, a thurbox that does not answer or
    a notifier that raises costs a line here and nothing else, because a
    message is not worth the records this pass just wrote.
    """
    events = origin_outcomes(q, before)
    try:
        lines = _load_sibling("fleet_notify_origin", "notify_origin.py").outcomes(events)
    except Exception as exc:  # noqa: BLE001 — telling may not fail what it tells of
        lines = [f"origin: outcomes could not be told: {one_line(exc)}"]
    for line in lines:
        print(f"    {line}")


# Why a task in each state is not one waiting on a reader — and, where there is
# one, the command that moves it on. A sentence that named `collect` for every
# state sent the operator to a command that skips `landed` and `abandoned`
# entirely, which is worse than saying nothing.
REVIEWED_REFUSALS = {
    "queued": "Nothing is waiting yet: it has not been dispatched.",
    "dispatched": (
        "Nothing is waiting yet: its worker has not finished. "
        "`fleet queue collect` is what concludes it."
    ),
    "landed": "It has already landed, so there is nothing left for a reader to hold.",
    "abandoned": "Its artifact was given up on, so nothing is waiting on a reader.",
    "stuck": (
        "Its worker gave up: that session is the evidence and a human decides, "
        "which is a different keep from a reader's."
    ),
    "failed": (
        "Its worker gave up: that session is the evidence and a human decides, "
        "which is a different keep from a reader's."
    ),
}


def cmd_reviewed(args) -> int:
    """Record that the reader is done with a served document, and release it.

    THE HONEST HALF OF `served`. A kept session is a held machine, so a shape
    that keeps one needs an END, and a document served off any forge has no
    event fleet can observe: no merge, no comment API, no server fleet started.
    So this is a person, saying so, exactly as `block --clear --condition` is.
    It is the sentence `reap` prints under every task it keeps for this reason,
    so the operator never has to remember the command — only to decide.

    IT WRITES THE READER'S ANSWER AND NOTHING ELSE — no `state`, no `outcome`.
    The landing sweep still asks the same question on the next pass and still
    promotes the task itself, which keeps one answer to "has this landed"
    instead of two that can disagree.
    """
    q = Queue(queue_root())
    task = q.get(args.ref)
    method = task_publish(task)[0]
    if method != "served":
        raise QueueError(
            f"{task.ref} publishes `{method}`, and only a `served` task waits on a "
            "reader. Nothing else here is held for one."
        )
    if review_closed(task):
        was = (task.doc.get("review") or {}).get("closed")
        raise QueueError(f"{task.ref}: its review was already recorded closed at {was}")
    # ONLY A TASK THAT HAS CONCLUDED. Recorded on one still running, the answer
    # sits there until `collect` reads its result — and that same pass lands it
    # and reaps the session, so the document is served and its worker is gone in
    # one command. That is the failure `served` exists to prevent, arrived at
    # from the other end.
    if task.state != "done":
        raise QueueError(
            f"{task.ref} is `{task.state}`, and a review is closed on a task "
            "that has concluded and is waiting on its reader. "
            + REVIEWED_REFUSALS.get(
                task.state, "Nothing about this task is waiting on a reader."
            )
        )
    why = (args.why or "").strip()
    task.doc["review"] = {"closed": now(), **({"why": why} if why else {})}
    task.save()
    print(
        f"{task.ref}: review closed — the document is no longer waiting on a reader.\n"
        "    `fleet queue reap` lands it and releases the session that was "
        "kept to answer them."
    )
    return 0


# HOW A TASK CAME TO BE `abandoned`, in the words every view prints. One state,
# because everything downstream — blockers that never clear, topics that
# archive, the reap that releases a session — is the same for all three; three
# WORDS, because "abandoned by hand" on work the forge or the machine ended is a
# claim about a person that nobody made. A record from before `how` existed
# was always a hand's, and reads as one.
ABANDON_HOW = {
    "hand": "abandoned by hand",
    "closed-unmerged": "closed unmerged",
    "session-gone": "session gone",
}


def abandon_how(task: Task) -> str:
    return str((task.doc.get("abandoned") or {}).get("how") or "hand")


def retire(task: Task, how: str, why: str, **extra) -> dict:
    """Move a task to `abandoned`, recording how and why, in the record and its progress."""
    entry = {"at": now(), "why": why, "was": task.state, "how": how, **extra}
    task.doc["abandoned"] = entry
    task.doc["state"] = "abandoned"
    task.doc.pop("vanished", None)
    task.save()
    fleet_platform.append_record(
        task.file(PROGRESS_FILE), json.dumps({"abandoned": entry, "observed": now()}) + "\n"
    )
    return entry


def abandon_refusal(task: Task, live: set | None, live_why: str, force: bool) -> str:
    """Why this task may not be abandoned, or "" when it may.

    `landed` is refused whatever the flags: the work is on main, and calling it
    given up would be a false record. A DISPATCHED task whose session thurbox
    still lists is a worker mid-flight, so giving up on it is the lead's call
    to make out loud with `--force`. A session that could not be looked up
    counts as live — a probe hiccup must not read as a worker that is gone.
    """
    if task.state == "landed":
        return f"{task.ref} has landed; its work is on main and cannot be given up"
    sid = task.doc.get("session")
    if force or task.state != "dispatched" or not sid:
        return ""
    if task.doc.get("host"):
        status = "on host " + task.doc["host"] + ", which this command does not ask"
    elif live is None:
        status = f"not checked — {live_why}"
    elif sid not in live:
        return ""
    else:
        status, _ = session_state(sid)
        status = status or "listed by thurbox"
    return (
        f"{task.ref} is dispatched to session {sid} ({status}). Its worker may "
        "still be running;\n       `--force` abandons it anyway, and leaves the "
        "session to `fleet queue reap`"
    )


def cmd_abandon(args) -> int:
    """Retire tasks that will never run — the one hand-made way into `abandoned`.

    THE SAME TERMINAL STATE THE FORGE ALREADY WRITES for a pull request closed
    unmerged, so nothing downstream learns a new word: `unfinished()` stops
    counting the task and the topic archives, `collect` skips it, a blocker
    naming it reads UNCLEARABLE, and `reap` releases its session once thurbox
    says the agent is at rest. This verb never touches a session itself.

    ALL OR NOTHING. Every task is checked before any is written, so one refusal
    leaves every record as it was.
    """
    why = (args.why or "").strip()
    if not why:
        raise QueueError("--why is required: the reason is the whole record of an abandon")
    if bool(args.refs) == bool(args.topic):
        raise QueueError("name the tasks to abandon, or --topic T — one or the other")
    q = Queue(queue_root(), scope="all")
    tasks = _abandon_targets(q, args)

    todo = [t for t in tasks if t.state != "abandoned"]
    live, live_why = (None, "")
    if not args.force and any(t.state == "dispatched" and t.doc.get("session") for t in todo):
        live, live_why = live_sessions()
    refusals = [r for r in (abandon_refusal(t, live, live_why, args.force) for t in todo) if r]
    if refusals:
        raise QueueError("nothing abandoned:\n       " + "\n       ".join(refusals))

    for task in tasks:
        if task.state == "abandoned":
            print(f"    {task.ref:<46} already abandoned")
            continue
        entry = retire(task, "hand", why, forced=bool(args.force))
        held = " — its session is `reap`'s to release" if task.doc.get("session") else ""
        print(f"    {task.ref:<46} abandoned  (was {entry['was']}){held}")
    if not todo:
        print("abandon: nothing to do")
        return 0

    # A blocker on an abandoned task never clears — only `landed` releases one
    # — so every task still waiting on these is named, with both ways out.
    # Which one is right is the lead's decision, not this command's.
    _print_stranded(q, {t.ref for t in todo})

    touched = {t.topic for t in todo}
    for slug in sorted(touched):
        refresh_run_logs(q, only=slug)
    sweep_archives(Queue(queue_root(), scope="live"), lambda t: t.state, dry=False)
    return 0


def _abandon_targets(q: Queue, args) -> list:
    """The tasks `abandon` names: a topic's unfinished ones, or the refs given."""
    if args.topic:
        if args.topic not in q.topics:
            raise QueueError(f"no such topic: {args.topic}")
        return [t for t in q.by_topic().get(args.topic, []) if t.state not in TERMINAL_STATES]
    return list({t.ref: t for t in (q.get(r) for r in args.refs)}.values())


def _print_stranded(q: Queue, gone: set) -> None:
    """Name every task still waiting on one of the abandoned `gone`, with both ways out."""
    for dep in sorted(q.tasks.values(), key=lambda t: t.ref):
        if dep.state in CONCLUDED_STATES or dep.ref in gone:
            continue
        for b in dep.blockers:
            if b.get("task") in gone:
                print(
                    f"    {dep.ref} waits on {b['task']} and stays blocked: an abandoned "
                    "task never lands.\n"
                    f"      uv run fleet queue block {dep.ref} --clear --on {b['task']}"
                    "   # it can run without it\n"
                    f"      uv run fleet queue abandon {dep.ref} --why '...'"
                    "   # it cannot"
                )


# --- refuel: the fuel, and the sessions that ran dry -------------------------
#
# WHY THIS EXISTS. A worker that hits its agent's token limit does not fail —
# it SITS. thurbox reports the last state its hook saw, and the hook that would
# have said `idle` never fires, so a session that ran dry mid-turn reads
# `working` for as long as it is left there. `watch` folds no transition,
# `collect` finds no result, `reap` sees a task that is not finished. Nothing
# in the loop notices, and the operator finds it hours later.
#
# THE THING THAT DECIDES THE WHOLE DESIGN: the limit is not the session's, it
# is the ACCOUNT's. `quota-axi` reads the vendor's own quota windows off the
# credentials already on this machine, and what it reports is the operator's
# subscription window — the one the lead and every worker draw on together. So
# while that window is spent, every session is stuck for the same reason and
# restarting them is worse than useless: each one resumes, hits the same wall
# within seconds, and burns the reset it was waiting for. That is not a
# hypothetical — three concurrent pipeline runs did exactly it on 2026-08-29
# and lost every step in flight.
#
# Hence the order, and it is the point of this section:
#
#   1. ask the ACCOUNT. Spent -> restart NOTHING, print `resetsAt`, and say the
#      fleet is waiting on the window rather than on any session. Unreadable ->
#      `undetermined`, which is never a pass and never a failure.
#   2. only with fuel in the account, look for the individual session that is
#      wedged anyway — which is the only case a restart recovers anything.
#
# A dead pane (`hook_corroboration=dead`) needs recovery regardless of hooks.
# For a live pane, detection is a conjunction, because either half
# alone is wrong:
#
#   the state is STALE   `hook_state` says `working` and its age has grown past
#                        anything a turn takes (STALE_WORKING_SECS below).
#                        On its own this is a SLOW worker, and slow is not dry.
#   the agent SAYS SO    its own limit banner on the pane, or — more precisely —
#                        the rate-limit record in its transcript, which names
#                        the window that rejected the turn.
#
# A restart is neither a completion nor a failure: nothing here writes `state`
# or `outcome`. `collect` stays the only thing that closes a task.

# How long a `working` hook state has to have stood before it is worth looking
# at the pane at all. MEASURED, not guessed: 306 fleet-worker turns from this
# machine's Claude Code transcripts (~/.claude/projects, the sessions whose
# first prompt is this queue's own "Read <BRIEF.md> and do what it says") that
# carry NO rate-limit record ran p50 3.7 min, p90 19.1 min, p95 25.1 min. Half
# an hour is past 95% of real work, and the eight turns that ran longer are the
# multi-hour ones a whole brief occasionally takes — which is exactly why age
# alone never restarts anything here.
STALE_WORKING_SECS = 30 * 60

# How many times one task's session may be restarted before it is left to a
# human. A session that runs dry, is restarted, and runs dry again is a task
# too big for the window it is drawing on; a fourth restart is a loop, not a
# recovery.
REFUEL_CAP = 3

# How long to wait, after parking, for the old conversation holder to exit
# before `session start` resumes. The pane disappearing is not that proof:
# `session restart` used to spawn into an id Claude Code still held, and the
# new process exited 1 (`Session ID … is already in use`). Ten seconds is
# longer than a killed agent takes to drop the lock; past that the session
# stays parked for a human rather than launching on top of a survivor.
HOLDER_WAIT_SECS = 10

# HOW EACH AGENT SAYS IT RAN OUT, one entry per agent fleet has actually
# WATCHED do it — the same shape as `scripts/lib/session_trust.py`'s per-agent
# table, and for the same reason: fleet drives several agents, so knowing one
# of them is a fact about that agent and not a assumption about all of them.
#
# `banner` is the sentence on the pane. `transcript` is the directory of
# per-session records, with `home_env` the agent's own knob for moving it; a
# transcript is read only if `jsonl` says its records are one JSON object per
# line with a `rate_limit` error on the rejected turn.
#
# AN AGENT THAT IS NOT HERE IS NOT GUESSED AT. It answers `undetermined`, which
# restarts nothing, and `refuel` says which setting would teach fleet its
# signal. Inventing a pattern for an agent nobody has watched hit its limit is
# how a live worker gets restarted mid-turn, so nothing is matched here that
# was not observed.
AGENT_LIMIT_SIGNALS = {
    # Observed 2026-09-08, on the pane and in the transcripts:
    #     You've hit your session limit · resets 11:30pm (Europe/Paris)
    # Matched on the sentence, so the window's name and reset time can vary.
    "claude": {
        "banner": r"you'?ve hit your \w+ limit",
        "home_env": "CLAUDE_CONFIG_DIR",
        "home": "~/.claude",
        "transcript": "projects",
        "jsonl": True,
    },
}


def limit_signal(agent: str | None) -> dict:
    """How this agent reports exhaustion — the operator's answer outranks ours.

    `LIMIT_BANNER` and `TRANSCRIPT_DIR` in `orchestration/agent.conf` teach
    fleet an agent it has never watched, without a code change and without
    fleet claiming to know a sentence nobody observed. Either can be said
    ABOUT ONE AGENT (`<agent>.LIMIT_BANNER=`), and that outranks the
    checkout-wide line, which keeps the position it has always had: ahead of
    the table below, because a sentence the operator watched beats one this
    repo wrote down.

    A second account of a watched agent has no row of its own and gets the
    watched one through `<agent>.LIKE=`, which is the whole point: fleet
    invents nothing here.
    """
    conf = agent_conf()
    sig = dict(agent_settings.row(AGENT_LIMIT_SIGNALS, agent, conf) or {})
    banner = agent_settings.value("LIMIT_BANNER", agent, conf)
    tdir = agent_settings.value("TRANSCRIPT_DIR", agent, conf)
    if banner:
        sig["banner"] = banner
    if tdir:
        sig.update({"home": tdir, "home_env": "", "transcript": "", "jsonl": True})
    return sig


def limit_banner_re(agent: str | None) -> "re.Pattern | None":
    """The sentence that agent prints at its limit, or None to not guess."""
    pattern = limit_signal(agent).get("banner")
    return re.compile(pattern, re.I) if pattern else None

# How much pane to ask for, and how much of it the banner has to be in. The
# banner sits in the scrollback of a session that already recovered from an
# earlier limit, so only the tail counts as evidence about NOW.
CAPTURE_LINES = 200
PANE_TAIL_LINES = 40

# The tail of a transcript is all that matters, and a long one is megabytes.
TRANSCRIPT_TAIL_BYTES = 512 * 1024

# The account's own quota window, and the ONE thing that decides whether any
# restart is worth making. It is read through `fleet_status.probe_fuel()` —
# `refuel/02-fuel-gauge` put that there for the lead's screen — rather than
# parsed a second time here: one reader means quota-axi's schema moving costs
# one edit. `probe_fuel()` takes ONE provider and this command names which,
# because the screen reads every authenticated provider through
# `probe_fuel_all()` and the two may legitimately show different pictures: a
# spent window on a provider the fleet never dispatches through is not a reason
# to leave a worker sitting at its limit.
#
# Loaded by path and LAZILY, because fleet_status.py imports this file: at
# import time that is a cycle, and inside the one function that needs it, it is
# not.
QUOTA_CMD = "quota-axi"

# The provider that gauge reads, and so the agent whose account it speaks for.
# A task whose account cannot be worked out is not refused — its window is
# undetermined, and undetermined restarts nothing.
#
# NO NAME IS WRITTEN HERE. A literal would gate every operator's fleet on one
# operator's vendor, and reading the WRONG provider is worse than reading none:
# a spent window somewhere the fleet never dispatches would strand a worker at
# its limit. `FUEL_PROVIDER` in `orchestration/agent.conf` sets it outright;
# with that empty this reads the provider off the task in hand.
#
# A PROVIDER IS NOT AN ACCOUNT, and this is the distinction the per-agent
# settings put in. Two workers on the same provider and different accounts
# have different windows, so a reading is keyed by BOTH: the provider names
# the vendor, and the agent's `ENV` record names which of that vendor's
# accounts quota-axi is to read. One pass can hold several, and each task is
# judged against its own rather than against one reading for the whole sweep.


def agent_providers() -> dict[str, str]:
    """`agent=provider` pairs from agent.conf, for a fleet whose names differ.

    Empty is the shipped answer and means IDENTITY: quota-axi names most
    providers after the agent that draws on them, so a map is only needed where
    they come apart. Nothing here lists the providers quota-axi supports —
    `fleet_status.authenticated_providers()` asks it, so the set grows with the
    tool and never with a table in this file.
    """
    raw = agent_conf().get("AGENT_PROVIDERS", "").strip()
    out: dict[str, str] = {}
    for pair in re.split(r"[,\s]+", raw):
        agent, _, provider = pair.partition("=")
        if agent.strip() and provider.strip():
            out[agent.strip()] = provider.strip()
    return out


def task_agent_reason(task) -> tuple[str, str]:
    """The agent a task runs, and why it is NOT KNOWABLE when it is not.

    Exactly one of the two is ever non-empty. The second is what keeps this
    honest: a caller that cannot be told "fleet does not know" gets told a
    guess instead, and a guess here is another account's quota window.

    The record first: `dispatch` writes the agent it actually spawned the
    worker as, so what a later pass reads is EVIDENCE about a running session
    rather than a second derivation of it — which is what keeps the answer
    right after the operator edits the policy under a worker already at work.

    A record with no agent is a task dispatched before that writing existed,
    or one not dispatched yet, so the resolution below is the spawn's own, in
    the spawn's order: the policy covering the repository, then the checkout's
    `AGENT`. It used to stop at the last of those, and `refuel` then judged
    every policy-covered task by an agent it was not spawned as.

    THE MIDDLE STEP CAN FAIL, and failing it is not the same as no rule
    covering the repository. A policy is in force and the repository behind
    the checkout cannot be read — the checkout was moved or deleted, its
    `origin` names a forge no adapter claims, the task runs on another machine
    and its `origin` has not been read THERE (`host_origins`) or could not be —
    and then which agent the policy would have named is unknown. Falling
    through to the checkout's `AGENT` there is precisely the silent wrong
    answer this function exists to stop, and it is what `dispatch` already
    refuses to do: `agent_policy_refusal` fails closed on this same state.

    EVERY REPOSITORY THE TASK SPANS, since one session runs one agent in all of
    them: the middle step is the INTERSECTION of what each allows, in the first
    matching repository's order so the default is still its first agent. An
    empty intersection is not knowable either — there is no agent to name — and
    a task with one repository asks exactly what it always asked.
    """
    recorded = (task.doc.get("agent") or "").strip()
    if recorded:
        return recorded, ""
    policy = agent_policy()
    if policy:
        named = _policy_agent(task, policy)
        if named is not None:
            return named
    return configured_agent() or "", ""


def _policy_agent(task, policy: dict) -> tuple[str, str] | None:
    """`task_agent_reason` under a policy in force; None when no rule covers the task."""
    if task.doc.get("host"):
        # Only what `host_origins` already READ on the host: this answer is also
        # asked by listings and `refuel`, which must not ssh per task to give
        # it. `dispatch` reads first (`agent_policy_refusal`), so the spawn
        # finds the read here; anything else is told it was not made.
        read = task.host_origins
        if read is None:
            return "", (f"agent policy is in force and this task's checkout is on "
                        f"{task.doc['host']}, whose `origin` has not been read there, "
                        "so which agent it runs is not knowable here")
        for repo, why in read.values():
            if repo is None:
                return "", f"agent policy is in force and {why}, so which agent it runs is not knowable"
    for unit in [] if task.doc.get("host") else task_repos(task):
        if repo_from_checkout(unit["path"]) is None:
            return "", (f"agent policy is in force and no repository can be read from "
                        f"`origin` in {unit['path']!r}, so which agent it runs "
                        "is not knowable")
    matched = task_policy(task, policy)
    if not matched:
        return None
    allowed, prefix = matched
    if not allowed:
        return "", (f"agent policy covers this task's repositories with {prefix!r} "
                    "and no one agent is allowed in all of them, so which agent "
                    "it runs is not knowable")
    return allowed[0], ""


def task_agent(task) -> str:
    """The agent a task runs, or "" when fleet cannot tell — see `task_agent_reason`."""
    return task_agent_reason(task)[0]


def fuel_agent(agent: str | None = None) -> str | None:
    """The provider this agent draws on, or None to not guess.

    `FUEL_PROVIDER` pins one outright. Otherwise `AGENT_PROVIDERS` is asked —
    for the agent, then for whatever it is `LIKE`, since a second account of an
    agent draws on that agent's provider — and failing that the agent is used
    as its own provider name, which is the identity quota-axi's naming makes
    right most of the time.
    """
    conf = agent_conf()
    pinned = conf.get("FUEL_PROVIDER", "").strip()
    if pinned:
        return pinned
    named = agent_providers()
    chain = agent_settings.chain(agent, conf)
    for name in chain:
        if name in named:
            return named[name]
    return chain[-1] if chain else None


def provider_is_known(provider: str, env: dict | None = None) -> tuple[bool, str]:
    """Does quota-axi hold a credential for this provider?

    Asked rather than assumed, because quota-axi supports many providers and a
    list in this file would be stale the day it gained another. A provider it
    does not name is reported, never guessed past: reading the WRONG window is
    worse than reading none.
    """
    try:
        names, why = fuel_gauge().authenticated_providers(env=env)
    except (OSError, ImportError, AttributeError, SyntaxError) as exc:
        return True, f"the provider list could not be read ({exc})"
    if why:
        return True, ""
    if provider in names:
        return True, ""
    return False, (
        f"{QUOTA_CMD} holds no credential for `{provider}` — it names "
        + (", ".join(names) if names else "none")
        + f". Map the agent to its provider with AGENT_PROVIDERS in {AGENT_CONF}"
    )


def fuel_gauge():
    """The `fleet_status` module, loaded from beside this file — once.

    Through `_load_sibling`, under the key `fleet/cli.py` gives it, so a
    process that already holds it — `fleet status` itself — hands back that
    copy. It used to execute the file on every call, and fleet_status.py loads
    this file back, so each reading of the fuel ran a second queue.py too.
    """
    return _load_sibling("fleet_status", "fleet_status.py")


def account_fuel(provider: str, env: dict | None = None) -> tuple[str, str]:
    """('fuel' | 'spent' | 'unknown', detail) for ONE account's window.

    `effectivePercentRemaining` is a subscription window that every session
    drawing on that account spends at once — six workers dispatched together
    spend one window six ways — so this is ONE reading per ACCOUNT and never a
    per-session one. There is no per-session number anywhere: `session get
    --json` carries no token, usage, cost or limit field at all.

    `env` is the agent's own `ENV` record, and it is what makes this a reading
    of an account rather than of a vendor: quota-axi picks its credentials out
    of the environment it runs under, so two accounts of one provider are two
    calls under two environments.
    """
    try:
        sec = fuel_gauge().probe_fuel(provider, env=env)
    except (OSError, ImportError, AttributeError, SyntaxError) as exc:
        return "unknown", f"the fuel gauge could not be loaded: {exc}"
    if sec.get("unavailable"):
        return "unknown", str(sec["unavailable"])
    remaining = sec.get("remaining")
    detail = f"{remaining}% remaining"
    if sec.get("limited_by"):
        detail += f" — limited by {', '.join(sec['limited_by'])}"
    if sec.get("resets_at"):
        detail += f", resets {sec['resets_at']}"
    if sec.get("stale"):
        # quota-axi's own word for a reading it could not refresh. Reported,
        # not acted on differently: it still carries a number it measured.
        detail += "  (quota-axi calls this reading stale)"
    return ("spent" if remaining <= 0 else "fuel"), detail


def transcript_root(agent: str | None = None) -> str:
    """Where this agent keeps its transcripts, or "" when fleet does not know.

    Each agent's own env knob is read rather than a home being assumed, and an
    agent with no entry and no `TRANSCRIPT_DIR` returns "" so every caller
    degrades to `undetermined` instead of globbing somebody else's directory.

    IT READS THE AGENT'S ENVIRONMENT, NEVER THIS PROCESS'S. `home_env` names
    the variable the agent moves its home with, and the value comes from that
    agent's own `ENV` record. It used to come from `os.environ`, which is the
    LEAD's — a Mission Control session on the operator's main account,
    answering a question about a worker on a different one. The answer was the
    lead's own directory every time, so a worker on a second account had no
    transcript fleet would ever read and half of `refuel`'s detection was
    silently dead.
    """
    sig = limit_signal(agent)
    home = sig.get("home")
    if not home:
        return ""
    env = sig.get("home_env")
    account = agent_settings.account_env(agent, agent_conf())
    home = (env and account.get(env)) or os.path.expanduser(home)
    return os.path.join(home, sig["transcript"]) if sig.get("transcript") else home


def transcript_file(agent_sid: str, agent: str | None = None) -> str:
    """The agent's own transcript, named by the session id thurbox records."""
    root = transcript_root(agent)
    if not root:
        return ""
    hits = glob.glob(os.path.join(root, "*", f"{agent_sid}.jsonl"))
    if not hits:
        hits = glob.glob(os.path.join(root, "**", f"{agent_sid}.jsonl"), recursive=True)
    return hits[0] if hits else ""


def _last_turn(tail: str) -> dict | None:
    """The last conversational entry in a transcript's tail, or None."""
    last = None
    for line in tail.splitlines():
        # A tail read can start mid-line; an unparseable line is that, or a
        # record shape this does not know. Either way it is not evidence.
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("isSidechain"):
            continue
        if row.get("type") in ("user", "assistant"):
            last = row
    return last


def transcript_exhaustion(agent_sid: str, agent: str | None = None) -> tuple[str, str]:

    """Did the agent's LAST turn end on the quota rejecting it?

    The precise source, and the one this section derives its number from: the
    record carries `error: "rate_limit"`, `apiErrorStatus: 429` and the
    `quotaLimits` window that rejected the request, `resetsAt` included. The
    pane only renders the sentence.

    It has to be the last CONVERSATIONAL entry. What follows a rejection in a
    wedged session is bookkeeping — `system`, `file-history-snapshot`,
    `last-prompt` — and a session that came back has an ordinary turn after it.
    """
    if not agent_sid:
        return "unknown", "the session records no agent_session_id"
    root = transcript_root(agent)
    if not root:
        return "unknown", (
            f"fleet has not watched `{agent or 'this agent'}` hit a limit, so it "
            f"has no transcript to read — name one with TRANSCRIPT_DIR in "
            f"{AGENT_CONF}"
        )
    path = transcript_file(agent_sid, agent)
    if not path:
        return "unknown", f"no transcript for {agent_sid} under {root}"
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TRANSCRIPT_TAIL_BYTES))
            tail = fh.read().decode("utf-8", "replace")
    except OSError as exc:
        return "unknown", f"could not read {path}: {exc}"

    last = _last_turn(tail)
    name = os.path.basename(path)
    if last is None:
        return "unknown", f"transcript {name} records no turn yet"
    if last.get("error") != "rate_limit":
        return "quiet", f"transcript {name}: its last turn is an ordinary one"
    quota = last.get("quotaLimits") or {}
    resets = quota.get("resetsAt")
    if isinstance(resets, (int, float)) and not isinstance(resets, bool):
        resets = datetime.fromtimestamp(resets, timezone.utc).isoformat()
    return "exhausted", (
        f"transcript {name}: the last turn was rejected "
        f"{last.get('apiErrorStatus', '')} rate_limit on the "
        f"{quota.get('rateLimitType', 'unknown')} window, resets {resets}"
    )


def pane_exhaustion(sid: str, agent: str | None = None) -> tuple[str, str]:
    """Does the agent's own limit banner stand at the bottom of its pane?

    The tail only. The banner stays in the scrollback of a session that already
    came back from an earlier limit, and that is history, not a verdict.
    """
    if not shutil.which("thurbox-cli"):
        return "unknown", NO_THURBOX_CLI
    try:
        proc = subprocess.run(
            ["thurbox-cli", "session", "capture", sid, "--lines", str(CAPTURE_LINES), "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "unknown", f"session capture could not run: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return "unknown", "session capture failed: " + (detail[-1] if detail else NO_OUTPUT)
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        return "unknown", "session capture did not answer JSON"
    banner = limit_banner_re(agent)
    if banner is None:
        return "unknown", (
            f"fleet has not watched `{agent or 'this agent'}` hit a limit, so it "
            f"does not know its banner — set LIMIT_BANNER in {AGENT_CONF} to "
            "teach it one"
        )
    lines = [ln.strip() for ln in str(doc.get("output") or "").splitlines() if ln.strip()]
    for line in lines[-PANE_TAIL_LINES:]:
        if banner.search(line):
            return "exhausted", f"the pane ends on the agent's own banner: {line}"
    return "quiet", f"no limit banner in the pane's last {PANE_TAIL_LINES} lines"


def exhaustion(doc: dict) -> tuple[str, str]:
    """('exhausted' | 'quiet' | 'undetermined', detail) for one live session.

    The transcript outranks the pane wherever it can be read: it is the same
    event, recorded rather than rendered, and it says which window rejected the
    turn. The pane is what answers for an agent that keeps no transcript here.

    THE AGENT HERE IS A DIFFERENT QUESTION from `account_key`'s, which is why
    it is read from a different place. That one asks which agent fleet SPAWNED
    the task as, and answers from the task's record; this one asks what the
    LIVE session is running, and the session document is the direct evidence
    for it — a profile's `command` launches something no record could name.
    For a dispatched worker the two agree, and they agree because `dispatch`
    now records the agent it actually named (#117). Where they can still part
    is a session bound by hand with `fleet queue attach`: there the session
    document is the fact and the record holds no answer at all.
    """
    agent = doc.get("detected_agent") or doc.get("reports_as") or doc.get("agent")
    seen, detail = transcript_exhaustion(doc.get("agent_session_id") or "", agent)
    if seen != "unknown":
        return seen, detail
    pane, pane_detail = pane_exhaustion(str(doc.get("id") or ""), agent)
    if pane != "unknown":
        return pane, pane_detail
    return "undetermined", f"{detail}; {pane_detail}"


def session_doc(sid: str) -> tuple[dict | None, str]:
    """`session get --json` in full — the hook fields are the whole point here.

    THE ONE READER of `session get`. `session_state` and `session_worktree`
    each take one field out of this answer; they used to run the command
    themselves, three copies of the same four failure branches.
    """
    if not shutil.which("thurbox-cli"):
        return None, NO_THURBOX_CLI
    try:
        proc = subprocess.run(
            ["thurbox-cli", "session", "get", sid, "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"thurbox-cli session get could not run: {exc}"
    if proc.returncode != 0:
        return None, "thurbox-cli session get failed"
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        return None, "thurbox-cli session get did not answer JSON"
    if not isinstance(doc, dict):
        return None, "thurbox-cli session get did not answer an object"
    return doc, ""


def record_refuel(task: Task, sid: str, why: str, prompted: bool,
                  park: str | None = None) -> None:
    """The receipt, and the cap's only memory.

    Appended, never replaced: a session that keeps running dry is a fact about
    the task, and one that is invisible if each pass overwrites the last.
    `park` is why a stop that ran did not start — the next pass reads it
    instead of calling that a person parked the session.
    """
    rec = {"at": now(), "session": sid, "why": why, "prompted": prompted}
    if park:
        rec["park"] = park
    task.doc.setdefault("refuels", []).append(rec)
    task.save()


def restart_session(sid: str, doc: dict) -> tuple[bool, str, bool]:
    """Park, wait for the old conversation holder to exit, then resume.

    Returns (ok, note, issued_stop). `issued_stop` is whether `session stop`
    actually ran: a census that cannot be read, or a session with nothing to
    wait for, never touches the pane and must not spend the restart cap.

    thurbox's in-place restart kills a window and immediately spawns its
    replacement: the old process may still hold the conversation id. A sleep
    guesses when that lock is free; a fresh conversation loses the worker's
    context. Instead keep the row, worktree, launch environment and conversation
    with stop/start, and wait for the id's holders to exit. If a live agent's
    command carries no id, pin its observed command to a unique pid BEFORE
    stopping; a generic command after the stop could belong to another worker.
    Text selects only what we WAIT for, never what we kill.
    A surviving orphan or an unreadable census after stop leaves the session
    parked and reported for a human, rather than launching into an occupied
    conversation.
    """
    ident = str(doc.get("agent_session_id") or "")
    command = str(doc.get("foreground_command") or "").strip()
    if not ident and not command:
        return False, "no conversation id or foreground command to wait for", False
    commands = fleet_platform.process_commands()
    if commands is None:
        return False, "cannot read processes before stopping the session", False
    holders = {pid for pid, row in commands.items() if ident and ident in row}
    if not holders and command and doc.get("hook_corroboration") != "dead":
        holders = {pid for pid, row in commands.items() if command == row.strip()}
        if len(holders) > 1:
            return False, "foreground command belongs to several processes; cannot identify the old holder", False
    issued_stop, why = _session_verb("stop", sid)
    if why:
        return False, why, issued_stop
    why = _await_holders(holders, ident)
    if why:
        return False, why, True
    _ran, why = _session_verb("start", sid)
    if why:
        return False, why, True
    return True, "", True


def _session_verb(verb: str, sid: str) -> tuple[bool, str]:
    """`thurbox-cli session <verb> <sid>`: (whether it ran, why it failed or "")."""
    try:
        proc = subprocess.run(
            ["thurbox-cli", "session", verb, sid],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"session {verb} could not run: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return True, f"session {verb} failed: " + (detail[-1] if detail else NO_OUTPUT)
    return True, ""


def _await_holders(holders: set, ident: str) -> str:
    """Wait for the old conversation's holders to exit: "" once they have, else why not."""
    deadline = time.monotonic() + HOLDER_WAIT_SECS
    while True:
        commands = fleet_platform.process_commands()
        if commands is None:
            return "session parked: cannot confirm the old process exited; inspect before starting"
        if not any(pid in holders or (ident and ident in row) for pid, row in commands.items()):
            return ""
        if time.monotonic() >= deadline:
            return "session parked: the old conversation is still in use; inspect before starting"
        time.sleep(0.2)


def stale_since(age: float) -> float:
    """When the state this age describes was reported, as an epoch second."""
    return datetime.now(timezone.utc).timestamp() - age


def record_time(stamp) -> float:
    """A record's `at` — ISO text or a thurbox epoch, as `stamp_datetime`
    reads it — as an epoch second.

    Unreadable reads as the beginning of time, which makes an unparseable
    receipt fall through to the ordinary path rather than block it.
    """
    then = stamp_datetime(stamp)
    return then.timestamp() if then else 0.0


# --- the accounts one pass reads ---------------------------------------------
#
# ONE READING PER ACCOUNT, and an account is a provider plus the environment
# quota-axi is asked under. A pass used to take one provider for every task it
# held and report `undetermined` for the rest; two workers on one vendor and
# two logins were then one window read twice under the same credentials, which
# is one window read once and attributed to both.


class Account:
    """One window, read once, and the tasks judged against it."""

    def __init__(self, label: str, provider: str, env: dict | None):
        self.label, self.provider, self.env = label, provider, env
        self.verdict, self.detail = "unknown", "not read"

    def read(self) -> "Account":
        known, why = provider_is_known(self.provider, env=self.env)
        self.verdict, self.detail = ("unknown", why) if not known else account_fuel(
            self.provider, env=self.env
        )
        return self


def account_key(task) -> tuple | None:
    """What identifies the window this task spends, or None when fleet cannot tell.

    The provider AND the account: a pass may hold two agents that are the same
    vendor under different logins, and their windows are not each other's.
    Tasks whose agents resolve to the same pair share one reading.

    A task whose agent is not knowable keys nothing, and that is the whole
    point of asking for the reason: `FUEL_PROVIDER` pins a provider whatever
    the agent is, so reading the agent alone would have grouped such a task
    onto somebody else's window and judged it there.
    """
    agent, why = task_agent_reason(task)
    if why:
        return None
    provider = fuel_agent(agent)
    if not provider:
        return None
    env = agent_settings.account_env(agent, agent_conf())
    return (provider, tuple(sorted(env.items())))


def account_label(tasks, provider: str, env: dict) -> str:
    """How the account line names itself: the provider, and whose account when
    more than one agent's is being read, since the provider alone is ambiguous then."""
    who = sorted({task_agent(t) for t in tasks if task_agent(t)})
    return f"{provider} ({', '.join(who)})" if env and who else provider


def read_accounts(tasks) -> dict:
    """Every distinct account this pass touches, read once each, in task order.

    `None` keys nothing: a task whose provider cannot be worked out has no
    window, says so in its own line, and never borrows another task's.
    """
    grouped: dict = {}
    for task in tasks:
        key = account_key(task)
        if key is not None:
            grouped.setdefault(key, []).append(task)
    accounts = {}
    for key, held in grouped.items():
        provider, pairs = key
        env = dict(pairs)
        accounts[key] = Account(
            account_label(held, provider, env), provider, {**os.environ, **env} if env else None
        ).read()
    return accounts


def refuel(q: Queue, ref: str | None = None, dry: bool = False) -> int:
    """Restart the workers that ran dry — and only once the account can pay.

    Returns how many sessions it had something to say about.
    """
    tasks = [q.get(ref)] if ref else sorted(q.tasks.values(), key=lambda t: t.ref)
    holders = [t for t in tasks if t.doc.get("session")]
    if not holders:
        return 0

    print(
        f"refuel: {len(holders)} recorded session(s); the cap is {REFUEL_CAP} "
        "restart(s) per task"
    )
    accounts = read_accounts(holders)
    for account in accounts.values():
        _print_account(account)

    lead = control_session_ids()
    acted = fired = kept = 0
    for task in holders:
        acted += 1
        if _refuel_task(task, accounts, lead, dry):
            fired += 1
        else:
            kept += 1

    if fired or kept:
        print(
            f"refuel: {fired} restart(s){' (dry run)' if dry else ''}, "
            f"{kept} session(s) left alone"
        )
    return acted


def _print_account(account: "Account") -> None:
    """One account's line in `refuel`, and what a spent or unread one means."""
    print(f"    account {account.label:<26} "
          f"{'undetermined' if account.verdict == 'unknown' else account.verdict:<12} "
          f"{account.detail}")
    if account.verdict == "spent":
        print(
            "      That window is SPENT, and it is a subscription every "
            "session on the\n"
            "      account draws on. The fleet is waiting on the window, not "
            "on any session:\n"
            "      resuming a worker now would hit the same wall and burn the "
            "reset. Nothing\n"
            "      on that account is touched until it comes back."
        )
    if account.verdict == "unknown":
        print(
            "      That account's own quota could not be read, and "
            "undetermined is never a pass\n"
            f"      and never a failure — so nothing is acted on. {QUOTA_CMD}: "
            "https://github.com/kunchenguid/quota-axi"
        )


def _refuel_task(task: Task, accounts: dict, lead: dict, dry: bool) -> bool:
    """Judge one session holder, and restart it when it ran dry. True when it fired."""
    sid = task.doc["session"]
    if _refuel_held(task, sid, accounts, lead):
        return False

    doc, why = session_doc(sid)
    if doc is None:
        print(f"    {task.ref:<46} undetermined  {why}")
        return False

    history = task.doc.get("refuels") or []
    if doc.get("stopped") or doc.get("state") == "stopped":
        last = history[-1] if history else {}
        park = last.get("park") if last.get("session") == sid else None
        print(f"    {task.ref:<46} kept          "
              f"{park or 'the session is deliberately stopped'}")
        return False
    # `get` probes the multiplexer. Its corroboration is the pane's actual
    # liveness even when the last hook was fresh, absent, or contradicted.
    # It is independent of terminal width, locale and old scrollback text.
    dead = doc.get("hook_corroboration") == "dead"
    if dead:
        detail = "dead pane: thurbox reports hook_corroboration=dead"
    else:
        detail = _ran_dry(task, doc, history)
        if detail is None:
            return False
    return _refuel_restart(task, sid, doc, detail, dead, len(history), dry)


def _refuel_restart(task: Task, sid: str, doc: dict, detail: str, dead: bool,
                    already: int, dry: bool) -> bool:
    """Restart a session that ran dry, inside the cap. True when it fired."""
    if already >= REFUEL_CAP:
        print(f"    {task.ref:<46} kept          {'dead pane' if dead else 'ran dry again'} after {already} restart(s); "
              f"the cap is {REFUEL_CAP} — a human decides now")
        return False

    if dry:
        print(f"    {task.ref:<46} would restart {sid}  {detail}")
        return True

    # A stop that ran — even one that then failed to start — spends the
    # cap, so a pane that dies on every launch cannot loop. A census that
    # could not be read never issued stop, and must not.
    ok, note, issued_stop = restart_session(sid, doc)
    if issued_stop:
        record_refuel(task, sid, detail, False, park=None if ok else note)
    if not ok:
        print(f"    {task.ref:<46} NOT RESTARTED {note}", file=sys.stderr)
        return False

    _brief_refuelled(task, sid, detail)
    return True


def _refuel_held(task: Task, sid: str, accounts: dict, lead: dict) -> bool:
    """Whether `refuel` leaves this session alone before reading it. True has said why."""
    if sid in lead:
        # `reap`'s guard, for the same reason and a worse consequence: a
        # restart of the lead kills the operator's own conversation, and one
        # of the conductor the work that conversation was keeping moving.
        whose = {"self": "the session running this command — the lead's own",
                 "lead": "the lead's own session", "conductor": "the conductor's own session"}[lead[sid]]
        print(f"    {task.ref:<46} kept          that is {whose}, not a worker's")
        return True
    reviewing = (task.state in ("done", "landed")
                 and review_lifecycle().holds_reviewer(sys.modules[__name__], task))
    if task.state != "dispatched" and not reviewing:
        print(f"    {task.ref:<46} kept          its task is `{task.state}`; only a "
              "worker still in flight is refuelled")
        return True
    account = accounts.get(account_key(task))
    if account is None:
        _print_no_account(task)
        return True
    if account.verdict != "fuel":
        # The reason is the account line above, printed once: repeating a
        # hundred characters of it per task buries the one thing a reader
        # is looking for, which is which tasks it applies to.
        word = "kept" if account.verdict == "spent" else "undetermined"
        print(f"    {task.ref:<46} {word:<13} the {account.label} window is "
              f"{'spent' if account.verdict == 'spent' else 'unreadable'} — see above")
        return True
    if task.doc.get("host"):
        # `session capture` is local-only and the transcript is on that
        # machine, so neither half of the detection can be established from
        # here. Undetermined, and never a guess.
        print(f"    {task.ref:<46} undetermined  runs on host {task.doc['host']}: its "
              "pane and its transcript are there, not here")
        return True
    return False


def _print_no_account(task: Task) -> None:
    """Why a task has no account window for `refuel` to read."""
    runs, why = task_agent_reason(task)
    if why:
        # Which AGENT is unknown, which is a different sentence from
        # which PROVIDER — and saying the provider one here sent a
        # reader to `agent.conf` to fix something that was never wrong.
        print(f"    {task.ref:<46} undetermined  {why}")
    else:
        runs = runs or "thurbox's own default"
        print(f"    {task.ref:<46} undetermined  no provider to read for `{runs}`: "
              f"name one with FUEL_PROVIDER or AGENT_PROVIDERS in {AGENT_CONF}")


def _ran_dry(task: Task, doc: dict, history: list) -> str | None:
    """The evidence a live pane ran dry, or None when `refuel` keeps it (and says why)."""
    # The agent's own word, and only that. `running`, `uncovered` and
    # `unreported` are observations about a pane, not a claim by the agent
    # about itself (`thurbox-session` §4a), and a worker that is genuinely
    # at rest is `collect`'s business, not this command's.
    hook = doc.get("hook_state")
    age = doc.get("hook_state_age_secs")
    if hook != "working":
        print(f"    {task.ref:<46} kept          its hook says `{hook or doc.get('state')}`, "
              "which is not a stale working state")
        return None
    if not isinstance(age, (int, float)) or isinstance(age, bool):
        print(f"    {task.ref:<46} undetermined  thurbox reports no age for that "
              "`working` state")
        return None
    if age < STALE_WORKING_SECS:
        print(f"    {task.ref:<46} kept          working for {age / 60:.0f}m, under the "
              f"{STALE_WORKING_SECS // 60}m staleness threshold")
        return None

    seen, detail = exhaustion(doc)
    if seen == "undetermined":
        print(f"    {task.ref:<46} undetermined  {detail}")
        return None
    if seen != "exhausted":
        # The whole reason detection is a conjunction: this worker has been
        # in one turn for a long time and its agent has said nothing about
        # a limit. Slow is not dry.
        print(f"    {task.ref:<46} kept          working for {age / 60:.0f}m and quiet "
              f"about it — slow, not dry ({detail})")
        return None

    # A `working` state REPORTED BEFORE the last restart is evidence from
    # before that restart: the re-spawned agent has simply not reported yet.
    # Without this, two passes a minute apart spend the cap on one wedge and
    # kill a window that was coming back up.
    if history and stale_since(age) < record_time(history[-1].get("at")):
        print(f"    {task.ref:<46} kept          restarted at "
              f"{history[-1].get('at')}, and this `working` was reported before "
              "that — give it a moment")
        return None
    return detail


def _brief_refuelled(task: Task, sid: str, detail: str) -> None:
    """Hand a restarted session its brief again, and record whether it landed."""
    # The dispatch path's handoff, in dispatch's order and for its reason:
    # a re-spawned agent in a worktree can ask the trust question again, and
    # `session send` into that dialog types the prompt INTO it.
    send = (
        f"Read {brief_target(task)} and do what it says. Your session was "
        "restarted to recover a dead pane or a quota limit mid-task, so the "
        "conversation above is yours: continue from where you stopped rather "
        "than starting over."
    )
    prompted, report = trust_and_send(
        sid, send, verify=needs_verified_submit(task)
    )
    task.doc["refuels"][-1]["prompted"] = prompted
    # The task's own flag follows a delivery, never a failed one: a
    # restart that could not be briefed leaves it as it was, and the
    # receipt above is what names the miss.
    if prompted:
        task.doc["prompted"] = True
    task.save()
    print(f"    {task.ref:<46} restarted     {sid}"
          f"{'' if prompted else '  NOT PROMPTED'}")
    print(f"        {detail}")
    if not prompted:
        print(f"        the session is up but was NOT prompted: {report}\n"
              f"        nothing was typed into it — retry with `fleet queue prompt {task.ref}`",
              file=sys.stderr)


def cmd_refuel(args) -> int:
    q = Queue(queue_root())
    reviewer = publish_conf().get("REVIEWER_TASK", "").strip()
    if reviewer and reviewer not in q.tasks:
        try:
            assigned = Queue(queue_root(), scope="all").get(reviewer)
        except QueueError:
            pass
        else:
            q.tasks[assigned.ref] = assigned
    if refuel(q, ref=args.ref, dry=args.dry_run) == 0:
        print("refuel: no task here is holding a session")
    return 0


# --- shepherd: the pull request, after the worker stopped ---------------------
#
# WHY THIS EXISTS. A task closes when its worker writes result.md. The pull
# request it named goes on living, and in one day this control plane lost three
# round trips to that gap: #14 went CONFLICTING the moment #13 merged and
# nothing noticed; #11 and #12 were opened outside the pipeline and nobody saw
# for hours; a pipeline review finding sat in a PR body until a human read it
# out. Every one was a person noticing something a machine could have.
#
# So this is a FOURTH thing, after `watch` and `collect` and deliberately not
# folded into either. It asks the forge for every open PR on the repos the
# queue's tasks name — not just the ones recorded as a task's `artifact`,
# because a task records exactly one and #25 was a second pull request from a
# task whose artifact still pointed at the already-merged #23 — classifies
# each one, DISPATCHES A FIXER for the ones that need work, and merges the
# ones that have earned it. Noticing was never the expensive part, which is why
# a status report would have saved none of those three round trips.
#
# WHY NOT INSIDE `collect`. `collect` reads local files, closes tasks, and
# works with the network down; shepherding calls out to GitHub and spawns
# sessions. Folding a session-spawning side effect into the command whose whole
# contract is "read a file, close a task" makes `collect` fail when the forge
# is unreachable,
# for a reason unrelated to what it was asked to do. It is a sibling — and
# because a command nobody remembers to run reproduces the bug this fixes,
# `collect` ends by naming it whenever it closed a task carrying an artifact,
# and `--json` is the seam `fleet status` reads it through.
#
# THE RULES THAT KEEP IT FROM BEING WORSE THAN NOTHING:
#
#   Idempotent.  Every fix sent is recorded on the task under `shepherd`, as
#                one (condition, head) entry in its `sent` list. A second pass
#                over the same condition on the same head sends nothing; a
#                different condition, or a new head, goes to the session
#                already on the PR once it is at rest by its own word.
#   Ping first.  A session that is still there is typed into (`pinged`) and
#                never doubled; a fresh one is `spawned` on the branch only
#                when the recorded fixer and the task's own worker are gone.
#   Never guess. "Could not check" is its own outcome and is never `broken`.
#                No forge, no network, no thurbox: say what could not be
#                determined and carry on. Spawning a fixer for a healthy PR is
#                the one failure that costs more than the bug.
#   Never touch  A pull request whose head branch lives in someone else's
#   a stranger.  fork is reported and left alone — never merged, never handed
#                a fixer — because a stranger cannot create a branch inside
#                this repository, so that is the one claim a pull request
#                cannot make for itself.
#   One agent    A fixer is a worker fleet puts to work, so the operator's
#   policy.      per-repository agent policy governs it exactly as it governs
#                `dispatch` — same question, same function, no second reading
#                of the rules. A repository whose policy no longer clears the
#                task's agent, or one a policy covers and nothing can read,
#                gets a `policy-refused` row and no fixer; it is still
#                classified and still merged like any other.

# §4a of .agents/skills/thurbox-session/SKILL.md, as code. These two groups are
# the AGENT SPEAKING about itself. Every other word in that table is an
# observation — `running`, `uncovered`, `unreported` — and an observation is
# not permission to type into somebody's pane.
SESSION_BUSY = {"working", "blocked"}
SESSION_AT_REST = {"idle", "done"}

# Conditions that get a fixer, worst first: one pass sends ONE fix per PR, for
# the thing that has to be fixed before any of the others can even be judged.
FIXABLE = ("conflicting", "checks-failed", "changes-requested", "policy")

# WHERE FLEET IS ALLOWED TO MERGE. An explicit allowlist and not a flag,
# because the blast radius of getting this wrong is somebody else's repository.
# A repo that is not named is reported `ready to merge` and left for a human,
# which is what every repo did before this list existed.
#
# THE LIST IS NOT HERE, AND THAT IS THE POINT. It lives in
# `orchestration/auto-merge.conf` — the operator's, gitignored, read on every
# pass — with `orchestration/auto-merge.example.conf` as the tracked copy that
# documents the format and names NOTHING. `Thurbeen/fleet` is public and
# agnostic, so a repository literal in this file is one operator's merge rights
# published in somebody else's machinery: a fresh clone would inherit them, and
# every change to them would be a commit here naming that operator's projects.
# Four such commits is what it took to notice. The file's own header owns the
# format and the test for adding an entry; the gates below are unchanged and
# are what putting a repository there actually means.
#
# The gates are the operator's, and all must hold: the head branch lives in
# this repository (`classify`'s `foreign` check — a fork is never merged), the
# body carries an attestation naming the pull request's CURRENT
# head commit (so a stale attestation from an earlier push can never authorise
# the push that replaced it), every check has CONCLUDED and passed, the forge
# itself calls it mergeable, no review thread is unresolved (`threads_gate`,
# asked only once the rest hold), and whoever opened it can push to this repo
# (`author_can_push` — the last thing checked, because it is the one claim the
# pull request body cannot make for itself).
#
# HOST-QUALIFIED, and an entry that names no host is refused rather than
# guessed at (`forge.RepoId.parse`). `Thurbeen/fleet` is a different repository
# on github.com and on a self-hosted instance, and this is the one list where
# matching the wrong one means acting on somebody else's code. EVERY source is
# parsed the same way — the conf file as well as the environment — which is the
# bug the literal carried: a literal was never parsed, so a bare slug written
# into it would have matched nothing, refused nothing, and failed no test.
#
# AN EMPTY SET IS A VALID ANSWER and the default one. A fleet nobody has told
# where it may merge merges nowhere and says so; it does not guess.

AUTO_MERGE_CONF = "orchestration/auto-merge.conf"
AUTO_MERGE_CONF_DEFAULTS = "orchestration/auto-merge.example.conf"

# The one way to say it somewhere other than the conf file, and it REPLACES the
# set rather than adding to it: a fleet driving somebody else's repositories is
# a different fleet, not this one plus an extra. The test suite is the
# second fleet it was written for.
AUTO_MERGE_ENV = "FLEET_AUTO_MERGE_REPOS"

AGENT_POLICY_CONF = "orchestration/agent-policy.conf"
AGENT_POLICY_CONF_DEFAULTS = "orchestration/agent-policy.example.conf"
AGENT_POLICY_ENV = "FLEET_AGENT_POLICY"
AGENT_POLICY_ROOT_ENV = "FLEET_AGENT_POLICY_ROOT"

# Which repositories reach `main` through an integration branch. Read from the
# same root as publish.conf, because it is a fact about how work publishes.
FLOW_CONF = "orchestration/flow.conf"
FLOW_CONF_DEFAULTS = "orchestration/flow.example.conf"
# The smallest page either forge answers a change request's commits in.
INTEGRATION_LISTED_PAGE = 100

# Squash because it is the only method fleet's own remotes allow, so the pull
# request title becomes the commit on `main`; CONTRIBUTING.md owns that. A
# forge that cannot perform it says so BEFORE anything is merged rather than
# after something was merged another way — `Forge.merge_methods`.
MERGE_METHOD = "squash"
DELETE_MERGED_BRANCH = True


def parse_auto_merge(entries, source: str) -> set:
    """Host-qualified repositories out of raw entries, refusing the rest.

    One parser for both sources, so the conf file can no more carry a bare
    slug than the environment can. A refusal is LOUD — a line on stderr naming
    the entry — because silence here reads exactly like a repository fleet
    declined to merge in for one of the five good reasons.
    """
    out = set()
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        repo = forge.RepoId.parse(entry)
        if repo is None:
            print(
                f"{source}: ignoring {entry!r} — an entry must "
                "name its forge, as in github.com/owner/repo",
                file=sys.stderr,
            )
            continue
        out.add(repo.qualified)
    return out


def auto_merge_conf_path(root: str | None = None) -> str:
    """The auto-merge list in force: the operator's copy, or the tracked one.

    `FLEET_AUTO_MERGE_ROOT` overrides where it is read from, the same way
    `FLEET_GLYPH_ROOT` relocates the glyph setting — so a test can exercise
    the file itself without inheriting whatever the developer's own gitignored
    auto-merge.conf says.
    """
    root = root or os.environ.get("FLEET_AUTO_MERGE_ROOT") or checkout_root()
    return conf_path(AUTO_MERGE_CONF, AUTO_MERGE_CONF_DEFAULTS, root)


def auto_merge_repos(root: str | None = None) -> set:
    """The repositories fleet may merge in, host-qualified, every time.

    Read as DATA — one repository per line, `#` starts a comment — and never
    executed. The environment REPLACES the file rather than adding to it, and
    a missing file is an empty set: a fleet nobody told merges nowhere.
    """
    raw = os.environ.get(AUTO_MERGE_ENV, "").strip()
    if raw:
        return parse_auto_merge(re.split(r"[,\s]+", raw), AUTO_MERGE_ENV)
    path = auto_merge_conf_path(root)
    # A line that carries rules names its repository first; a line that is
    # ONLY rules names none and allows nothing.
    return parse_auto_merge(
        [NEEDS_HUMAN_RE.split(line, 1)[0] for line in auto_merge_lines(path)], path
    )


def may_auto_merge(repo: forge.RepoId) -> bool:
    """Whether the allowlist names this repository, in whatever case either spells it.

    Both forges fleet ships an adapter for resolve a path without regard to
    case — see `same_repo` — so `github.com/thurbeen/fleet` in the file is
    GitHub's `Thurbeen/fleet`, exactly as the agent policy already reads it.
    """
    return repo.qualified.casefold() in {r.casefold() for r in auto_merge_repos()}


# THE DRAFT PHASE. In a repository publish.conf's DRAFT_REPOS names, a task's
# pull request is opened as a DRAFT, and a draft means "bots only": CI runs,
# review bots comment, fixers and relayed comments go to the worker. Shepherd
# takes it out of draft — the forge's own "ready for review", which is what
# downstream jobs and people listen for — once it would otherwise be `ready`:
# every check passed on its head, no fixer condition, no unresolved thread.
#
# ONLY A TASK'S OWN DRAFT. A draft nobody's task records is a person's work in
# progress, and "still a draft" is still the whole answer about it.
#
# ONE WAY. A comment that arrives after the flip is relayed and its thread
# holds the MERGE, as on any pull request; the pull request does not go back to
# draft, because "ready for review" already fired whatever listens for it. And
# a person who puts it back in draft by hand is never overruled: the flip is
# recorded per pull request on the task (`shepherd_ready`) and done once.


_DRAFT_REPOS_READ: dict = {}


def draft_repos() -> set:
    """The repositories whose task pull requests go through the draft phase.

    publish.conf's DRAFT_REPOS, comma separated and host-qualified, refused
    loudly otherwise by the parser the auto-merge list uses — once per value
    per process, since a pass asks for every pull request. Empty: no draft
    phase anywhere, which is the default.
    """
    raw = publish_conf().get("DRAFT_REPOS", "")
    if raw not in _DRAFT_REPOS_READ:
        _DRAFT_REPOS_READ[raw] = parse_auto_merge(raw.split(","), "DRAFT_REPOS")
    return _DRAFT_REPOS_READ[raw]


def draft_listed(repo: forge.RepoId | None) -> bool:
    """Whether DRAFT_REPOS names this repository, in whatever case either spells it."""
    return repo is not None and repo.qualified.casefold() in {
        r.casefold() for r in draft_repos()
    }


def draft_phase(cr: forge.ChangeRequest, task) -> bool:
    """Is this pull request a task's draft that shepherd takes out of draft itself?"""
    return bool(task) and cr.state == "open" and cr.draft and draft_listed(cr.repo)


def draft_flipped(task, url: str) -> dict:
    """The record of shepherd having marked this pull request ready, or {}."""
    every = task.doc.get("shepherd_ready") if task else None
    rec = every.get(url) if isinstance(every, dict) else None
    return rec if isinstance(rec, dict) else {}


def auto_merge_lines(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as fh:
            return [line.partition("#")[0] for line in fh]
    except OSError:
        return []


# WHAT STILL NEEDS A HUMAN IN A REPOSITORY FLEET MERGES IN. Every gate above
# held for a UI change that was merged unattended and shipped broken in a
# release, because a gate asks whether the change is VETTED and not whether a
# person has to look at it. So a line in the same file can say so:
#
#   github.com/owner/repo  needs-human  path:src/ui/*  label:ui
#   needs-human  label:needs-human
#
# The first allows the repository AND holds a change there that touches a
# path matching the glob, or carries the label; the second names no
# repository and holds every one. A held change is reported `needs-human`
# naming the rule, never merged, and never sent a fixer — nothing is wrong
# with it. A rule fleet cannot read, and files the forge cannot list, HOLD
# the change: a guard that fails open is one nobody can trust.
#
# The environment names repositories and no rules, the same way it replaces
# the file: a fleet driving somebody else's repositories is a different fleet.

NEEDS_HUMAN = "needs-human"
# The WORD, so a repository whose name contains it is still a repository.
NEEDS_HUMAN_RE = re.compile(r"(?:^|\s)needs-human(?:\s|$)")
MERGE_RULE_KINDS = ("path", "label")


def auto_merge_rules(root: str | None = None) -> list:
    """Every `needs-human` rule, as (repository or "", kind, value).

    `kind` is "unreadable" for a rule fleet cannot parse, with the text as its
    value: it holds what it covers rather than being skipped, and says why on
    stderr the way a bare slug does.
    """
    if os.environ.get(AUTO_MERGE_ENV, "").strip():
        return []
    path = auto_merge_conf_path(root)
    rules = []
    for line in auto_merge_lines(path):
        rules += _needs_human_line(path, line)
    return rules


def _needs_human_line(path: str, line: str) -> list:
    """The `needs-human` rules one line of auto-merge.conf holds, if any."""
    parts = NEEDS_HUMAN_RE.split(line, 1)
    if len(parts) < 2:
        return []
    scope, rest = parts
    repo = ""
    if scope.strip():
        parsed = forge.RepoId.parse(scope.strip())
        if parsed is None:
            return []  # parse_auto_merge has already refused it out loud
        repo = parsed.qualified
    try:
        words = shlex.split(rest)
    except ValueError:
        words = [rest.strip()]
    return [_merge_rule(path, repo, word) for word in words or [NEEDS_HUMAN]]


def _merge_rule(path: str, repo: str, word: str) -> tuple:
    """One `needs-human` word as (repository or "", kind, value)."""
    kind, sep, value = word.partition(":")
    if not (sep and value and kind in MERGE_RULE_KINDS):
        print(
            f"{path}: {word!r} is not a rule fleet can read — it must be "
            "path:<glob> or label:<name>, and until it is it holds "
            + (f"every merge in {repo}" if repo else "every merge"),
            file=sys.stderr,
        )
        kind, value = "unreadable", word
    return (repo, kind, value)


def needs_human(cr: forge.ChangeRequest) -> str:
    """The rule that holds this change for a person, or "" when none does.

    Labels first, because they arrived with the listing; the changed files
    are asked of the forge only when a path rule covers this repository.
    """
    # Casefolded like `may_auto_merge` and the agent policy: the forge's own
    # spelling and the conf file's name one repository.
    here = cr.repo.qualified.casefold()
    rules = [r for r in auto_merge_rules() if r[0].casefold() in ("", here)]
    for _repo, kind, value in rules:
        if kind == "unreadable":
            return f"an unreadable rule, {value!r}, in {auto_merge_conf_path()}"
    labels = {label.lower() for label in cr.labels}
    for _repo, kind, value in rules:
        if kind == "label" and value.lower() in labels:
            return f"label:{value}"
    globs = [value for _repo, kind, value in rules if kind == "path"]
    if not globs:
        return ""
    return _path_rule_hit(cr, globs)


def _path_rule_hit(cr: forge.ChangeRequest, globs: list) -> str:
    """The first path rule a file this change touches matches, or ""."""
    which, why = forge.for_repo(cr.repo)

    paths, why = which.changed_paths(cr) if which else (None, why)
    if paths is None:
        return f"could not list the files it changes ({why})"
    for glob_ in globs:
        for path in paths:
            if fnmatch.fnmatchcase(path, glob_):
                return f"path:{glob_} ({path})"
    return ""


# --- agent policy by repository owner -----------------------------------------
#
# Which agents may serve which repositories. The operator's copy is
# `orchestration/agent-policy.conf`; the tracked example names nobody. A rule
# maps a host-qualified repository prefix to an ordered list of agents: the
# first is the default, the rest are allowed when named explicitly.


def _parse_policy_prefix(text: str) -> str | None:
    """A host-qualified prefix with at least `host/owner`, or None."""
    parts = [p for p in text.split("/") if p]
    if len(parts) < 2 or "." not in parts[0]:
        return None
    return "/".join(parts).casefold()


def parse_agent_policy(entries: list[str], source: str) -> dict[str, list[str]]:
    """Host-qualified repository prefixes to allowed agents, refusing the rest.

    One parser for both sources. A refusal is LOUD — a line on stderr —
    because silence reads like a repository fleet declined to enforce a rule
    on for one of the good reasons. A line with no agents is refused too;
    an empty allowlist would forbid every agent, which is almost never what
    a hand-edited line means.
    """
    out: dict[str, list[str]] = {}
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            print(
                f"{source}: ignoring {entry!r} — an agent-policy entry must "
                "be REPO=AGENTS",
                file=sys.stderr,
            )
            continue
        repo_text, _, agents_text = entry.partition("=")
        repo_text = repo_text.strip()
        agents_text = agents_text.strip()
        prefix = _parse_policy_prefix(repo_text)
        if prefix is None:
            print(
                f"{source}: ignoring {repo_text!r} — an agent-policy entry must "
                "name its forge, as in github.com/owner/repo",
                file=sys.stderr,
            )
            continue
        agents = [a.strip() for a in agents_text.split(",") if a.strip()]
        if not agents:
            print(
                f"{source}: ignoring {repo_text!r} — no agents listed",
                file=sys.stderr,
            )
            continue
        out[prefix] = agents
    return out


def agent_policy_path(root: str | None = None) -> str:
    """The agent-policy file in force: the operator's copy, or the tracked one."""
    root = root or os.environ.get(AGENT_POLICY_ROOT_ENV) or checkout_root()
    return conf_path(AGENT_POLICY_CONF, AGENT_POLICY_CONF_DEFAULTS, root)


def agent_policy(root: str | None = None) -> dict[str, list[str]]:
    """The repository-prefix agent policy in force, every time.

    Read as DATA — one rule per line, `#` starts a comment — and never
    executed. The environment REPLACES the file rather than adding to it.
    A missing file is an empty policy: no checks, no new behaviour.
    """
    if AGENT_POLICY_ENV in os.environ:
        # The environment REPLACES the file, so an empty string means "no
        # rules" rather than "read the file". Spaces around `=` and after
        # commas are formatting only; split into entries after normalising them.
        raw = os.environ.get(AGENT_POLICY_ENV, "").strip()
        # Split and stripped rather than `re.sub(r"\s*=\s*", ...)`: the same
        # result, without a pattern that backtracks over a long whitespace run.
        normalised = "=".join(part.strip() for part in raw.split("="))
        # Both sides of a comma, not just the space after it: entries are
        # whitespace-separated (there is no newline-per-entry in a shell
        # variable, unlike the file), so a leftover space anywhere around an
        # internal comma reads as a second entry boundary and silently drops
        # every agent after it. `parse_agent_policy` below is the one place
        # that actually understands a rule's syntax; this step only turns one
        # flat string into the same per-line entries the file already hands
        # it, one whitespace-run at a time.
        normalised = ",".join(part.strip() for part in normalised.split(","))

        entries = [e for e in normalised.split() if e]
        return parse_agent_policy(entries, AGENT_POLICY_ENV)
    path = agent_policy_path(root)
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [line.partition("#")[0] for line in fh]
    except OSError:
        return {}
    return parse_agent_policy(lines, path)


def agents_for_repo(
    repo: forge.RepoId | None, policy: dict[str, list[str]]
) -> tuple[list[str], str] | None:
    """The agents allowed for `repo` and the prefix that selected them.

    Longest host-qualified prefix wins, compared case-insensitively. A rule
    for `github.com/owner/repo` beats a rule for `github.com/owner`. Returns
    None when the policy is empty or no prefix matches.
    """
    return longest_prefix_rule(repo, policy)


def longest_prefix_rule(repo: forge.RepoId | None, rules: dict):
    """(value, prefix) of the longest host-qualified prefix matching `repo`, or None."""
    if repo is None or not rules:
        return None
    qualified = repo.qualified.casefold()
    best_prefix = ""
    best = None
    for prefix, value in rules.items():
        if not qualified.startswith(prefix):
            continue
        tail = qualified[len(prefix) :]
        if tail and not tail.startswith("/"):
            continue
        if len(prefix) > len(best_prefix):
            best_prefix = prefix
            best = value
    if not best_prefix:
        return None
    return best, best_prefix


# --- integration branches: repositories whose change request is develop→main --
#
# The operator's copy is `orchestration/flow.conf`; the tracked example names
# none. Read on every `collect`, so a task already in flight is verified by the
# rule as it stands when its result is read.


def integration_branches(root: str | None = None) -> dict[str, str]:
    """Host-qualified repository prefixes to their integration branch.

    A line that names no forge, or no branch, is refused out loud: silence
    would read as a repository fleet checks the ordinary way on purpose.
    """
    root = root or os.environ.get("FLEET_PUBLISH_ROOT") or checkout_root()
    path = conf_path(FLOW_CONF, FLOW_CONF_DEFAULTS, root)
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [line.partition("#")[0].strip() for line in fh]
    except OSError:
        return {}
    out: dict[str, str] = {}
    for entry in filter(None, lines):
        repo_text, _, branch = (part.strip() for part in entry.partition("="))
        prefix = _parse_policy_prefix(repo_text)
        if prefix is None or not branch:
            print(
                f"{path}: ignoring {entry!r} — a flow entry must name its forge "
                "and a branch, as in github.com/owner/repo = develop",
                file=sys.stderr,
            )
            continue
        out[prefix] = branch
    return out


# --- origins: whose conversation a task is, and how to reach it -------------
#
# Every task added inside a thurbox session has an origin. `add` with no
# `--origin` records the calling session as `thurbox:<session id>` — the ONE
# form fleet writes and reads itself, and a stable string `find --origin` and
# `dispatch --origin` match exactly — and, with no `--reply`, the command that
# reaches it. Any other origin is the caller's opaque text.
#
# The operator's copy of the routing is `orchestration/owners.conf`; the tracked
# example names none. One rule per line, `<origin-prefix> = <target>`, where the
# target is a session-name substring, as it always was, or `!<command>`: a
# RELAY, run with the origin and the message appended as two arguments, which
# forwards it to wherever that origin lives. Fleet parses neither the origin nor
# the command. `origin_target` is the one resolver; `notify_origin.py` owns the
# delivery of what an origin is told: its task's outcome, and its worker's
# question.

OWNERS_CONF = "orchestration/owners.conf"
OWNERS_CONF_DEFAULTS = "orchestration/owners.example.conf"
SESSION_ORIGIN = "thurbox:"
RELAY_MARK = "!"


def session_origin(sid) -> str | None:
    """The origin that names thurbox session `sid`, or None for no session."""
    sid = str(sid or "").strip()
    return f"{SESSION_ORIGIN}{sid}" if sid else None


def origin_session(origin) -> str | None:
    """The session id a session origin names, or None for any other origin."""
    if isinstance(origin, str) and origin.startswith(SESSION_ORIGIN):
        return origin[len(SESSION_ORIGIN):].strip() or None
    return None


def session_reply(sid: str) -> str:
    """The default reply to a session origin: its mailbox, the message appended last."""
    return shlex.join(["thurbox-cli", "message", "send", "--to", sid, "--kind", "reply", "--body"])


def add_origin(args) -> tuple[str | None, str | None]:
    """(origin, reply) for a task `add` is creating.

    A stated value always wins. With no `--origin`, the calling thurbox session
    ($THURBOX_SESSION) is the origin; outside thurbox there is none, as before.
    With no `--reply`, a session origin gets the command that reaches it — but
    not on a `--host` task, whose worker runs on a machine that session is not
    on. Any other origin gets no default: fleet cannot know how to reach it.
    """
    origin = (args.origin or "").strip() or session_origin(os.environ.get("THURBOX_SESSION"))
    reply = (args.reply or "").strip() or None
    sid = origin_session(origin)
    if reply is None and sid and not args.host:
        reply = session_reply(sid)
    return origin, reply


def origin_rules(root: str | None = None) -> list[tuple[str, str, str]]:
    """[(origin prefix, "session" | "relay", substring or command)], longest prefix first.

    A line with no `=`, an empty side, or a `!` with no command, is refused out
    loud: silence would read as a task that has no owner on purpose.
    """
    root = root or os.environ.get("FLEET_OWNERS_ROOT") or checkout_root()
    path = conf_path(OWNERS_CONF, OWNERS_CONF_DEFAULTS, root)
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [line.partition("#")[0].strip() for line in fh]
    except OSError:
        return []
    out: list[tuple[str, str, str]] = []
    for entry in filter(None, lines):
        prefix, _, target = (part.strip() for part in entry.partition("="))
        kind = "session"
        if target.startswith(RELAY_MARK):
            kind, target = "relay", target[len(RELAY_MARK):].strip()
        if not prefix or not target:
            print(
                f"{path}: ignoring {entry!r} — an owners entry must be "
                "ORIGIN-PREFIX = SESSION-NAME-SUBSTRING or ORIGIN-PREFIX = !RELAY-COMMAND",
                file=sys.stderr,
            )
            continue
        out.append((prefix, kind, target))
    return sorted(out, key=lambda rule: -len(rule[0]))


class OriginTarget(NamedTuple):
    """Where a message for one origin goes. `kind` is one of:

    session   `value` is the session id a session origin names, and thurbox
              lists it (or could not be asked, which is never called gone).
    owner     `value` is the session-name substring of the owners rule `rule`.
    relay     `value` is the command of the owners rule `rule`; `relay_argv`
              is how it is run.
    gone      a session origin thurbox no longer lists: nobody to tell. A
              worker asks in its own pane; an outcome is logged and not sent.
    unrouted  an origin that is no session and that no rule names: reachable
              in principle — a chat thread does not disappear — and fleet has
              no way to it.

    `why` is one line for a log, saying which of those it is and why.
    """

    kind: str
    origin: str
    value: str
    rule: str | None
    why: str

    @property
    def reachable(self) -> bool:
        """False only for a session origin that is gone: any other origin always is."""
        return self.kind != "gone"


def origin_target(origin, rules: list | None = None, sessions=None) -> OriginTarget | None:
    """Resolve a task's origin to where a message for it goes. None when it has none.

    The longest matching `owners.conf` rule wins, so an operator can route even
    a session origin elsewhere. Otherwise a session origin is that session, or
    `gone` when `thurbox-cli session list` no longer lists it; and any other
    origin is `unrouted`. `rules` is `origin_rules()`'s shape, read when None;
    `sessions` is any container of the session ids thurbox lists, asked only
    when a session origin needs it and not given — None from thurbox is
    "unknown", never "gone". Resolving delivers nothing.
    """
    if not isinstance(origin, str) or not origin.strip():
        return None
    rules = origin_rules() if rules is None else rules
    for prefix, kind, target in rules:
        if origin.startswith(prefix):
            if kind == "relay":
                return OriginTarget("relay", origin, target, prefix, f"relayed by the rule for {prefix!r}")
            return OriginTarget("owner", origin, target, prefix, f"owned by the session named like {target!r}")
    sid = origin_session(origin)
    if not sid:
        return OriginTarget("unrouted", origin, "", None, "no session, and no owners rule names it")
    why = "listed by thurbox"
    if sessions is None:
        sessions, unknown = session_snapshot()
        if sessions is None:
            why = f"not called gone: {unknown}"
    if sessions is not None and sid not in sessions:
        return OriginTarget("gone", origin, sid, None, "thurbox no longer lists the session")
    return OriginTarget("session", origin, sid, None, why)


def relay_argv(target: OriginTarget, message: str) -> list[str]:
    """A relay's command as argv, with the origin and the message as its last two arguments.

    Split by the operator's shell quoting and run with no shell, so the message
    is one argument however it is quoted. Raises ValueError for a command whose
    quote is left open.
    """
    return [*fleet_platform.split_command(target.value), target.origin, message]


def integration_verdict(task: Task, cr: forge.ChangeRequest, branch: str, unit: dict) -> tuple[str, str]:
    """Does this change request from an integration branch carry the task's work?

    "Carries" is the task branch's HEAD being among the commits the FORGE
    lists for it: everything the branch holds is an ancestor of its head, and
    a worker cannot write that list. The head is read from the task's own
    checkout, where its worktree's branch lives. A squash into the
    integration branch rewrites that commit, and is refused like any other
    change request that does not carry it.
    """
    host = task.doc.get("host")
    if host:
        return "unknown", f"{branch} is on host {host}; not checked from here"
    repo = unit["path"]
    tip = ""
    for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        tip = git_out(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]).strip()
        if tip:
            break
    if not tip:
        return "unknown", f"{branch} could not be read in {repo}, so its head cannot be looked for"
    oids = {c.sha.lower() for c in cr.commits}
    if not oids:
        return "unknown", "the forge did not list this pull request's commits"
    head = f"{branch}'s head {tip[:8]}"
    if tip.lower() in oids:
        return "passed", f"is from integration branch {cr.head_branch} and contains {head}"
    if len(cr.commits) >= INTEGRATION_LISTED_PAGE:
        # Both forges answer one page of a long change request's commits, so a
        # full page that lacks the head has not shown it is absent.
        return "unknown", (
            f"the forge listed {len(cr.commits)} commits for it, which may not be "
            f"all of them, and {head} is not among them"
        )
    return "missing", (
        f"the pull request is from integration branch {cr.head_branch} and does not "
        f"contain {head}"
    )


def repos_policy(
    paths: list[str], policy: dict[str, list[str]], resolve=None
) -> tuple[list[str], str] | None:
    """The agents EVERY one of these repositories allows, and the prefixes that said so.

    A task is ONE session running ONE agent, and `--add-repo` is a second
    repository that agent COMMITS in — so an agent has to be allowed in all of
    them. The answer is the INTERSECTION, kept in the order the first matching
    repository lists them so the default is still that repository's first
    agent. An empty intersection is a task no agent may serve, and that is a
    refusal rather than a silent pick.

    One repository answers exactly as `agents_for_repo` does: same agents, same
    order, same prefix, same None. A repository whose `origin` cannot be read
    contributes nothing here; refusing that is the caller's, because only the
    caller knows whether an unreadable checkout is a `--host` task.

    `resolve` turns a path into its repository; this machine's checkout by
    default, and what `host_origins` read for a path on another machine.
    """
    if not policy:
        return None
    resolve = resolve or repo_from_checkout
    matched = [hit for hit in (agents_for_repo(resolve(p), policy) for p in paths) if hit]
    if not matched:
        return None
    allowed = [a for a in matched[0][0] if all(a in rest for rest, _ in matched[1:])]
    return allowed, ", ".join(dict.fromkeys(prefix for _, prefix in matched))


def task_policy(task: Task, policy: dict[str, list[str]]) -> tuple[list[str], str] | None:
    """`repos_policy` over every repository this task spans.

    A `--host` task's repositories are what `host_origins` read on its host,
    and None when nothing was read: refusing that is `_policy_unreadable`'s.
    """
    paths = [unit["path"] for unit in task_repos(task)]
    if task.doc.get("host"):
        read = task.host_origins
        if read is None:
            return None
        return repos_policy(paths, policy, lambda p: read.get(p, (None, ""))[0])
    return repos_policy(paths, policy)


def host_origins(task: Task) -> dict:
    """{path: (RepoId or None, why not)} for each repository a `--host` task spans, read ON ITS HOST.

    The checkout is on another machine, so the repository behind it is a
    question for that machine: `git remote get-url origin` there, through the
    same shell and the same bounded `ssh_run` the dispatch probes use, and
    resolved by the same `forge.repo_from_remote` a local checkout is. Read
    once per Task object, kept on it (never on the record — it is a reading,
    not a fact fleet owns), so the policy check and the agent the spawn names
    are one answer.

    Every failure is a refusal with its reason, never a permission: a host
    that is not in hosts.toml, one that does not answer within `SSH_TIMEOUT`,
    a checkout with no `origin`, an `origin` no forge adapter claims.
    """
    read = task.host_origins
    if read is not None:
        return read
    host = str(task.doc["host"])
    entry, why = host_entry(host)
    read = {}
    for unit in task_repos(task):
        path = unit["path"]
        if not entry:
            read[path] = (None, f"host {host} cannot be asked for {path!r}'s `origin`: {why}")
            continue
        proc = ssh_run(entry, host_shell(entry).origin(path))
        url = last_out_of(proc.stdout) if proc.returncode == 0 else ""
        repo = forge.repo_from_remote(url) if url else None
        if repo is not None:
            read[path] = (repo, "")
        elif url:
            read[path] = (None, f"`origin` of {path!r} on host {host} is {url!r}, "
                                "which no forge adapter here claims")
        else:
            read[path] = (None, f"`origin` of {path!r} could not be read on host {host} "
                                f"({first_line(proc) or 'no answer'})")
    task.host_origins = read
    return read


def agent_policy_refusal(task: Task) -> str | None:
    """Why this task cannot be dispatched under the current policy, or None."""
    policy = agent_policy()
    d = task.doc
    if policy:
        unreadable = _policy_unreadable(task)
        if unreadable:
            return unreadable
    matched = task_policy(task, policy)
    if matched is None:
        return None
    allowed, prefix = matched
    if not allowed:
        return (
            f"agent policy covers this task's repositories with {prefix!r} and no "
            "one agent is allowed in all of them. A task that spans repositories "
            "runs one agent in every one of them, so there is nothing to dispatch."
        )
    flags = profile_flags(d.get("profile") or "default")
    if "--command" in flags:
        return (
            f"profile carries a custom `command`, but agent policy "
            f"covers this repository with {prefix!r} allowing only "
            f"{', '.join(allowed)}. Fleet cannot tell which agent a free "
            "command launches, so dispatch is refused."
        )
    return _recorded_agent_refusal(d, allowed, prefix)


def _policy_unreadable(task: Task) -> str | None:
    """Why a policy in force cannot be checked against this task, or None."""
    d = task.doc
    if d.get("host"):
        # READ, not refused on sight: the checkout is on the host, so its
        # `origin` is asked there. What could not be read stays a refusal.
        for _repo, why in host_origins(task).values():
            if why:
                return f"{why}, so agent policy cannot be checked."
        return None
    for unit in task_repos(task):
        if repo_from_checkout(unit["path"]) is not None:
            continue
        return (
            f"cannot read origin for {unit['path']!r}, so agent policy "
            "cannot be checked."
        )
    return None


def _recorded_agent_refusal(d: dict, allowed: list, prefix: str) -> str | None:
    """Why the agent a task records is no longer allowed by policy, or None."""
    recorded = d.get("agent")
    if recorded and recorded not in allowed:
        # WHO put that value there decides what the operator can do about it.
        # Before `dispatch` recorded the agent it resolved, this field could
        # only hold an `add --agent`, and the message said so. It now also
        # holds a dispatch's own answer, and telling an operator that they
        # named an agent they never typed sent them to edit a record that is
        # no longer the live fact — the session is already running as it.
        if d.get("session"):
            return (
                f"dispatch resolved agent {recorded!r} for this task and its "
                f"session is already running as that agent, but policy for "
                f"{prefix!r} now allows only {', '.join(allowed)}. Editing the "
                "record would not change what is running: cancel the session, "
                "or widen the policy."
            )
        return (
            f"task records agent {recorded!r}, but policy "
            f"for {prefix!r} allows only {', '.join(allowed)}."
        )
    return None


def pr_ref(artifact: str) -> forge.ChangeRef | None:
    """The change request an artifact names, or None for anything else."""
    which, ref = forge.for_url(artifact)
    return ref if which is not None else None


def open_prs(repo: forge.RepoId) -> tuple[list, str]:
    """Every OPEN change request on one repository, straight from the forge.

    THE BUG THIS FIXES. A task records ONE `artifact` — the first pull request
    its worker reported — so a shepherd that enumerated artifacts saw exactly
    those. #25 was a SECOND pull request from a task whose artifact still
    pointed at the already-merged #23; the unattended pass could not see it and
    would never have merged it, and a pull request opened outside the queue was
    equally invisible. The forge knows what is open; the records only know what
    was reported once.

    A non-empty second value is why it could not be read, and a repository that
    could not be read contributes nothing rather than an empty answer.
    """
    which, why = forge.for_repo(repo)
    if which is None:
        return [], why
    return which.open_change_requests(repo)


def check_verdicts(cr: forge.ChangeRequest) -> tuple[list, list]:
    """(names that failed, names still running). Everything else passed.

    A `cancelled` check counts as still running here, not as a failure: that
    is the shepherd's own long-standing reading (`checks-pending`, not
    `checks-failed`), separate from fleet-status's, which is why the forge
    hands back `cancelled` as its own word instead of pre-deciding for either.
    """
    return (
        [c.name for c in cr.checks if c.verdict == "failed"],
        [c.name for c in cr.checks if c.verdict in ("pending", "cancelled")],
    )


# WHY NOT THE FIVE HEADINGS. `collect` looks for `## Intent` and its four
# siblings to check that a WORKER used the pipeline, and that is the right
# check there: it holds a task open until its own worker redoes the push, and
# the worker has no reason to lie to a queue it does not know exists.
#
# It is the wrong check HERE, and this is the whole safety question. This repo
# is public and has a fork, and this command merges unattended on a timer. The
# five headings are text, and text in a pull request body is written by whoever
# opened the pull request — so a check that counts them lets a body authorise
# its own merge. An attestation naming the exact commit the pipeline ran on is
# something a body cannot fake as easily. A stale one from an earlier push is
# refused for the same reason: the verdict is about the code it saw and not
# about the branch's name.
#
# WHICH MARKER is the operator's, because their pipeline already emits one and
# asking them to emit fleet's instead would be fleet dictating a format to a
# tool it has never heard of. `orchestration/publish.example.conf` owns the
# shape; this only compiles whatever it names.
#
# AND SO IS THE SHAPE, which is the half this file got wrong. It read one:
# the JSON inside the comment. A publisher that writes the marker alone in the
# comment and the JSON in a fenced block underneath is not a different marker,
# it is a different SHAPE, so no value of `ATTESTATION_MARKER` reaches it and
# the body reads as unattested — which fails closed, and therefore failed
# silently. Both are matched now, and everything after the match is one rule
# over whichever one carried the block.
DEFAULT_ATTESTATION_MARKER = "publish-attestation/v1"


def attestation_re(marker: str | None = None) -> "re.Pattern":
    """The HTML comment this fleet's pipeline leaves, in either shape."""
    marker = marker or publish_conf().get("ATTESTATION_MARKER", "").strip()
    if not marker:
        marker = DEFAULT_ATTESTATION_MARKER
    m = re.escape(marker)
    return re.compile(
        # The marker alone, then the fenced block that follows it. The closing
        # fence is what bounds the JSON, the same way `-->` bounds it below.
        r"<!--\s*" + m + r"\s*-->\s*```(?:json)?\s*(?P<fenced>\{.*?\})\s*```"
        # Or the JSON inside the comment. `:v1` is optional because the shape
        # that spelled the version apart from the marker named it there.
        r"|<!--\s*" + m + r"(?::v1)?\s+(?P<inline>\{.*?\})\s*-->",
        re.S,
    )


def attestation_json(body: str) -> str | None:
    """The attestation's JSON text, from whichever shape the body carries."""
    m = attestation_re().search(body or "")
    if not m:
        return None
    return m.group("fenced") or m.group("inline")


# The attestation is written DURING the pipeline's `pr` step, so in every body
# that carries one `pr` reads `running` and `ci` reads `pending`. Demanding
# `completed` from those two would reject every real pull request; `ci` is what
# the separate checks-passed gate is for, and the steps that decide whether the
# code is fit — review, test, lint, push — are the ones held to `completed`.
ATTESTATION_TRAILING_STEPS = {"pr", "ci"}
ATTESTATION_DONE = {"completed", "skipped"}
ATTESTATION_TRAILING_OK = ATTESTATION_DONE | {"running", "pending"}

# THE OTHER SHAPE'S VOCABULARY, and it is not a superset of the one above.
# Both spell `skipped` and they mean opposite things: above, a step that had
# nothing to do; here, a step that did not run, which is the failure the
# format was written to make visible and therefore blocks. So a block is read
# under the vocabulary it declares rather than under a union of the two — a
# union would let the looser reading answer for a block that meant the
# stricter one. `verdict` is what declares it: the shape below carries it and
# the shape above has no such field.
ATTESTATION_STEP_OK = {"passed", "not-applicable"}


def _declared_verdict(doc: dict, steps: list, attested: str) -> tuple[bool, str]:
    """The reading for a block that states its own verdict.

    BOTH HALVES, because the format says the verdict is DERIVED from the step
    statuses — passed only when every step passed or did not apply. A block
    whose two halves disagree is malformed, and reading whichever half says
    yes is how a `skipped` step would slip past the one check it was added to
    fail. Neither half is a stronger claim than the head sha above it: this
    says the gate was clean, and that says it was clean about this commit.
    """
    verdict = str(doc.get("verdict") or "").lower()
    bad = [
        f"{st.get('name') or 'an unnamed step'} is "
        f"{str(st.get('status') or '').lower() or 'unreported'}"
        for st in steps
        if str(st.get("status") or "").lower() not in ATTESTATION_STEP_OK
    ]
    if verdict != "passed":
        why = f"the attestation's verdict is {verdict or 'unstated'}"
        if bad:
            why += ": " + ", ".join(bad[:4])
        return False, why
    if bad:
        return False, (
            "the attestation says it passed and its own steps do not: "
            + ", ".join(bad[:4])
        )
    return True, f"the pipeline attests {attested[:8]}, which is this head"


def attestation_verdict(body: str, head_sha: str) -> tuple[bool, str]:
    """(did the pipeline run on THIS commit, one line saying how it is known).

    False is never "probably fine": every way of failing to read the
    attestation is a way of not being merged.
    """
    raw = attestation_json(body)
    if raw is None:
        return False, (
            "the body carries no attestation, so nothing but its own "
            "prose says the pipeline ever ran"
        )
    try:
        doc = json.loads(raw)
    except ValueError:
        return False, "the attestation is not valid JSON"
    if not isinstance(doc, dict):
        return False, "the attestation is not an object"

    attested = str(doc.get("head_sha") or "")
    if not attested:
        return False, "the attestation names no head_sha"
    if not head_sha:
        return False, "GitHub did not say which commit this pull request's head is"
    if attested.lower() != head_sha.lower():
        return False, (
            f"the attestation is for {attested[:8]}, and the head is "
            f"{head_sha[:8]} — it attests a push that is no longer what would merge"
        )

    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        return False, "the attestation lists no steps"
    if not all(isinstance(st, dict) for st in steps):
        return False, "the attestation's steps are malformed"

    if "verdict" in doc:
        return _declared_verdict(doc, steps, attested)

    unfinished = _unfinished_steps(steps)
    if unfinished:
        return False, "the pipeline did not finish: " + ", ".join(unfinished[:4])
    return True, f"the pipeline attests {attested[:8]}, which is this head"


def _unfinished_steps(steps: list) -> list:
    """Each attested step whose status is not a finished one, said as a phrase."""
    unfinished = []
    for st in steps:
        name = str(st.get("step") or "an unnamed step")
        status = str(st.get("status") or "").lower()
        allowed = (
            ATTESTATION_TRAILING_OK
            if name in ATTESTATION_TRAILING_STEPS
            else ATTESTATION_DONE
        )
        if status not in allowed:
            unfinished.append(f"{name} is {status or 'unreported'}")
    return unfinished


def classify(cr: forge.ChangeRequest, method: str | None) -> tuple[str, str]:
    """(condition, one line saying why).

    Four conditions get a fixer, in the order FIXABLE lists them. `ready`
    means the merge gates the forge can answer hold. `foreign` is a pull
    request that is not ours, which is neither merged nor handed to an agent.
    `undetermined` means the answer is not knowable yet and is never treated
    as any of the others.

    `method` is the publish method of the task this pull request belongs to,
    or None when no task records it. It gates exactly ONE condition: `policy`,
    "there is no attestation", is a fault only for a task that was declared
    `attested`. A `pr`-method pull request was never supposed to carry one,
    and the fixer sent at it would tell its worker to go and run a tool the
    operator may not have installed. None keeps the old reading, because an
    unlinked pull request is one fleet knows nothing about and the attestation
    is still the only thing that would ever authorise merging it.
    """
    if cr.state != "open":
        return "closed", (
            f"the pull request is {cr.state or 'in no state the forge named'}"
        )
    if cr.draft:
        return "undetermined", "still a draft"

    # NOT OURS, ASKED BEFORE ANYTHING ELSE. A stranger cannot create a branch
    # inside this repository, so where the head branch lives is the one claim
    # about a pull request that whoever opened it cannot write for themselves.
    # It gates the fixer as hard as it gates the merge: sending an agent to
    # "fix" a stranger's branch is worse than merging one, because it happens
    # without even the pretence of a gate.
    #
    # WHICH FORGE ANSWERS IT. A fork, a mirror and a same-repo branch are told
    # apart differently on every forge, so the adapter decides and this reads
    # its answer. `None` is "could not tell" and is never "not ours".
    if cr.head_is_ours is None:
        return "undetermined", "the forge did not say which repository the head branch is in"
    if not cr.head_is_ours:
        return "foreign", (
            f"its head branch is in {cr.head_location or 'another repository'}, "
            f"not {cr.repo} — fleet neither merges nor sends an agent at a pull "
            "request that is not ours"
        )

    base = cr.base_branch or "its base branch"
    failed, pending = check_verdicts(cr)
    attested, attest_why = attestation_verdict(cr.body, cr.head_sha)

    fixable = {
        "conflicting": (
            cr.mergeable == "conflicting",
            f"conflicts with {base} and cannot be merged as it stands",
        ),
        "checks-failed": (bool(failed), "failed checks: " + ", ".join(failed[:4])),
        "changes-requested": (
            cr.review_decision == "changes-requested",
            "a reviewer requested changes",
        ),
        "policy": (not attested and method in (None, "attested"), attest_why),
    }
    for condition in FIXABLE:
        hit, why = fixable[condition]
        if hit:
            return condition, why

    return _merge_readiness(cr, pending, attested, attest_why)


def _merge_readiness(cr: forge.ChangeRequest, pending: list, attested: bool,
                     attest_why: str) -> tuple[str, str]:
    """`classify` past every fixable condition: `ready`, or why not yet."""
    if pending:
        return "undetermined", "checks still running: " + ", ".join(pending[:4])
    if not cr.checks:
        # NO CHECK AT ALL is not a pass. CI here only fires on pull requests,
        # so a PR whose checks have not been created yet reads exactly like a
        # PR with nothing to run — and merging the first one merges code CI
        # never saw. "No check has reported" is its own answer.
        return "undetermined", "no check has reported yet"
    if cr.mergeable != "mergeable":
        # An unsaid answer is the forge still computing the merge, not a verdict.
        return "undetermined", (
            f"mergeable is {cr.mergeable or 'absent'}; ask again shortly"
        )
    if attested:
        return "ready", f"{attest_why}; checks green, mergeable, and the branch is ours"
    # Only a method that was never asked for an attestation reaches here —
    # `policy` catches the others first. The sentence says what is actually
    # known, because "ready" alone would read as "vetted" (see `publish_word`).
    return "ready", (
        "checks green, mergeable, and the branch is ours — and nothing attests "
        "the head that would merge"
    )


def threads_gate(cr: forge.ChangeRequest, detail: str) -> tuple[str, str]:
    """`ready`, or the unresolved review threads that hold it. (condition, why).

    POLICY.md says a change request with an unanswered thread is not done, and
    for a while shepherd merged one anyway: `classify` reads a review DECISION,
    and a line comment nobody resolved is not one. So the last question before
    a merge decision is the forge's own count of unresolved threads.

    OUTSIDE `classify`, and asked only of what `classify` already calls ready.
    `classify` is pure — it judges the one listing the forge answered — and
    this is a second network call per pull request, which is only worth paying
    right before something could merge. Every other condition already holds
    the merge for a reason of its own.

    A count the forge would not give is `undetermined`, never `ready`: the same
    rule as a check that has not reported. Which threads they are is not asked
    here; `shepherd_comments` reads them whole and sends them to the worker.
    """
    which, why = forge.for_repo(cr.repo)
    count, why = which.threads(cr.ref) if which else (None, why)
    if count is None:
        return "undetermined", f"review threads not read: {why}"
    if count:
        return "threads-open", (
            f"{count} unresolved review thread{'s' if count != 1 else ''}; "
            "a change request with an unanswered thread is not done"
        )
    return "ready", detail


def publish_word(cr: forge.ChangeRequest, condition: str) -> str:
    """`classify`'s condition as one word of the `publish.state` vocabulary.

    `classify` answers "what should fleet DO about this pull request"; the
    record answers "what is this pull request DOING", and a reader of the
    record wants two of those answers split finer than an action ever needs
    them — `undetermined` into `draft` / `checks-running` / `undetermined`, and
    `ready` into `ready` / `green`.

    WHY `green` AND `ready` ARE TWO WORDS, AND MUST STAY TWO. They look
    redundant. Both mean every gate GitHub can answer holds: checks passed, the
    merge is clean, the branch is ours. They are not the same claim, and
    collapsing them is the one edit to this file that would quietly undo the
    property the whole subsystem exists for.

        ready   the pipeline vetted the exact head that would merge. Review,
                tests and lint ran on THIS commit and said so in a form the
                body cannot fake (`attestation_verdict`, and see the comment
                above `attestation_re` for why the prose above it could).
                This is what fleet merges unattended.
        green   the forge is happy and NOBODY vetted anything. The checks that
                passed are whatever checks that repo happens to have, which
                for a repo fleet has never seen may be none at all. Fleet
                reports it and leaves it; the operator merges it, having
                looked.

    For two months every pull request in this queue was an attested pull
    request, so "the forge is happy" and "the pipeline vetted it" were the same
    fact, and both the operator and this code learned to read one as the other.
    The moment a `pr`-method task exists they come apart, and the only things
    standing between them are this word, its colour in the pane, and
    `shepherd_pr`'s refusal to hand a `green` one to `merge_change_request`.
    One word is
    evidence; the other is trust. Merging them merges on the worker's choice of
    tool, for every repo at once, silently.
    """
    if condition == "undetermined":
        # In `classify`'s own order: a draft is a draft whatever its checks say.
        if cr.draft:
            return "draft"
        _failed, pending = check_verdicts(cr)
        if pending or not cr.checks:
            return "checks-running"
        return "undetermined"
    if condition == "ready":
        attested, _why = attestation_verdict(cr.body, cr.head_sha)
        return "ready" if attested else "green"
    if condition == "policy":
        return "unattested"
    if condition == "foreign":
        # Determined, and not about this task's publish at all: the pull
        # request linked here is open from somebody else's repository. The
        # detail recorded beside this word is the sentence that says so.
        return "unknown"
    # `closed`, `conflicting`, `checks-failed`, `changes-requested` and
    # `threads-open` (`threads_gate`'s) are already the vocabulary's own words.
    return condition


# --- what changed underneath -------------------------------------------------


def git_out(repo: str, argv: list, timeout: int = 30) -> str:
    """git, best effort. A failure is the empty string — never an exception."""
    try:
        out = subprocess.run(
            ["git", "-C", repo] + argv, capture_output=True, text=True, encoding="utf-8", timeout=timeout
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def base_drift(repo: str, base: str, branch: str) -> str:
    """What landed on the base branch since this branch last moved.

    This is the sentence a fixer prompt lives or dies on. "Fix PR #14" is
    useless; "#13 merged and deleted these four files" is a rebase somebody can
    actually do. Squash merge is the only method this remote allows, so a
    base-branch commit subject carries its own PR number — which means one
    `git log` answers "which PR merged" and one `git diff --name-status`
    answers "and what it deleted", with no extra API calls.
    """
    git_out(repo, ["fetch", "--quiet", "origin", base], timeout=60)
    for ref in (f"origin/{base}", base):
        mb = git_out(repo, ["merge-base", ref, branch]).strip()
        if not mb:
            continue
        log = git_out(repo, ["log", "--oneline", "--no-decorate", f"{mb}..{ref}"]).strip()
        if not log:
            return ""
        diff = git_out(repo, ["diff", "--name-status", f"{mb}..{ref}"]).strip()
        block = f"Landed on `{base}` since your branch last moved:\n\n```\n{log}\n```"
        if diff:
            block += (
                "\n\nWhat those commits did to the tree "
                "(`D` is a file that is gone now):\n\n"
                f"```\n{diff}\n```"
            )
        return block
    return ""


# --- the fixer's brief -------------------------------------------------------

FIXER_TITLES = {
    "conflicting": "Rebase PR #{n} onto {base}",
    "checks-failed": "Fix the failing checks on PR #{n}",
    "changes-requested": "Address the review on PR #{n}",
    "policy": "Re-open PR #{n} through the pipeline",
}

FIXER_WORK = {
    "conflicting": """\
Rebase this branch onto `origin/{base}` and resolve every conflict. The base
moved under you after the pull request was opened; the commits below are what
moved it. Resolve in favour of what those commits intended — they are already
on `{base}` and are not up for debate here.

Then force-push the rebased branch (`git push --force-with-lease`) so the
existing pull request updates.""",
    "checks-failed": """\
Make the failing checks pass. Run the gate this repository declares — its
AGENTS.md or CONTRIBUTING.md names it — locally, since CI runs the same
checks and a green local run is the thing to get to. Push to the same branch
so the existing pull request re-runs them.""",
    "changes-requested": """\
Address the review that requested changes, then push to the same branch. Reply
to the review only if something in it was mistaken; otherwise let the diff be
the answer.""",
    # `{how}` is the task's OWN recorded publish command, free text the operator
    # wrote — fleet never spells a tool here, because the tool is theirs.
    "policy": """\
Nothing on this pull request shows the required pipeline ran on the commit it
would merge. It must carry an attestation in its body naming the exact head
commit the pipeline ran against, and this one either has none or has one for an
earlier push. Publish it again, on this branch — {how} — so the body is
rewritten, attestation and all, against what is on the branch now. Do not open
a second pull request; the pipeline updates the one that is already there, and
do not hand-edit the body, because an attestation you typed attests nothing.""",
}


def fixer_brief(task: Task, cr: forge.ChangeRequest, condition: str,
                detail: str, drift: str) -> str:
    n = cr.number
    base = cr.base_branch or task.doc.get("base") or "main"
    # A task that recorded no publish command gets a sentence that says so
    # rather than an empty gap where a command should be.
    how = (task.doc.get("publish") or {}).get("how") or (
        "run this repository's publishing pipeline"
    )
    work = FIXER_WORK[condition].format(base=base, how=how)
    parts = [
        f"# {FIXER_TITLES[condition].format(n=n, base=base)}",
        "",
        f"An open pull request from fleet task `{task.ref}` needs work. This is "
        f"the whole instruction set; you share no context with whoever wrote the "
        f"branch.",
        "",
        f"- **Pull request.** {cr.url} — {cr.title}".rstrip(" —"),
        f"- **Repo.** `{task.doc['repo']}`",
        f"- **Branch.** `{cr.head_branch or task.doc['branch']}` "
        f"onto `{base}`. It already exists and you are already on it.",
        f"- **What is wrong.** {detail}.",
        "",
        "## What to do",
        "",
        work,
    ]
    if drift:
        parts += ["", "## What changed underneath you", "", drift]
    parts += [
        "",
        "## Hard constraints",
        "",
        "- **Fix the pull request in place.** Push to the branch that is already"
        " open. Do not open a second pull request, and do not close this one.",
        "- **Do not merge it, and do not merge anything into it.** Merging is not"
        " yours to do here; rebase rather than merging the base branch in.",
        "- Do not widen the change. Fix the stated condition and nothing else —"
        " other work is in flight on other branches in this repo.",
        "",
        "## Done means",
        "",
        f"- {cr.url} is open, updated in place, and the condition above is"
        " gone.",
        "- Nothing else about the pull request changed.",
        "",
        "Reply in your own session when you are done; fleet reads the pull"
        " request itself and does not need a message.",
    ]
    return "\n".join(parts) + "\n"


def next_fix_file(task: Task, condition: str) -> str:
    n = 1 + len([f for f in os.listdir(task.path) if f.startswith("fix-")])
    return task.file(f"fix-{n:02d}-{condition}.md")


# --- reaching the worker that is already there, or making a new one ----------


def fixer_worktrees_root() -> str:
    """Where `branch_checkout` cuts a fixer's checkout: fleet's data directory.

    OUTSIDE every checkout, which is the point. It used to be
    `queue_root()/.worktrees`, inside the control plane, and Claude Code walks
    parent directories for CLAUDE.md: a fixer there found the lead's operating
    guide, stopped on its external-imports dialog before anything else, and
    sat unprompted — and past that dialog it would have been reading
    instructions written for the lead. This sits beside where thurbox keeps
    its own worker worktrees, under no repository.

    `release_fixer_checkouts` is what takes it back once the task lands.
    """
    return os.path.join(fleet_platform.fleet_data_dir(), "worktrees")


def branch_checkout(repo: str, branch: str, slug: str) -> tuple[str, str]:
    """A checkout of an EXISTING branch, to hand thurbox as `--repo-path`.

    THE TRAP THIS EXISTS FOR. `session create --worktree-branch X` only ever
    CREATES X, and fails with `a branch named 'X' already exists` — which is
    every branch that has a pull request on it, which is every branch this
    command cares about. Today's manual workaround was to rename the branch
    aside and base a new one off it; that leaves the pull request pointing at a
    branch nobody is working on, so the fix never reaches the PR.

    The answer is to stop asking thurbox for a branch at all. git already makes
    a second checkout of an existing branch, and `--repo-path` takes any
    directory. So: reuse the worktree the branch is already in, or add one, and
    hand over the path. The fixer then commits on the real branch and its push
    updates the real pull request.

    Returns (path, note). An empty path is a reason, not a path.
    """
    if not os.path.isdir(os.path.join(repo, ".git")) and not os.path.exists(
        os.path.join(repo, ".git")
    ):
        return "", f"{repo} is not a git checkout"
    git_out(repo, ["worktree", "prune"])
    listed = git_out(repo, ["worktree", "list", "--porcelain"])
    path = ""
    for block in listed.split("\n\n"):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        here = next((ln[len(WORKTREE_LINE):] for ln in lines if ln.startswith(WORKTREE_LINE)), "")

        on = next((ln[7:] for ln in lines if ln.startswith("branch ")), "")
        if on == f"refs/heads/{branch}" and here:
            path = here
            break
    if path:
        return path, "already checked out there"

    dest = os.path.join(fixer_worktrees_root(), slug)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        return "", f"{dest} is in the way; remove it and run again"
    try:
        out = subprocess.run(
            ["git", "-C", repo, "worktree", "add", dest, branch],
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"git worktree add failed: {exc}"
    if out.returncode != 0:
        first = (out.stderr or "").strip().splitlines()
        return "", first[-1] if first else "git worktree add failed"
    return dest, "new worktree on the existing branch"


def command_stem(task: Task) -> str:
    """The file stem of a profile's `--command`, or "" when there is none."""
    flags = profile_flags(task.doc.get("profile") or "default")
    try:
        raw = flags[flags.index("--command") + 1]
    except (ValueError, IndexError):
        return ""
    return os.path.basename(raw)


def needs_verified_submit(task: Task) -> bool:
    """Whether the first send after spawn must type, then look, then Enter.

    cursor-agent's composer does not exist when `session create` returns —
    the tmux window is live, the TUI is not. `session send`'s 200ms pause
    between paste and Enter is not enough; Enter lands in cooked mode and
    is gone by the time the line appears next to `→`. `--agent` sessions
    (claude, codex) do not do this. muse was not measured.

    A `--host` task is excluded: `session capture` has no pane on this
    machine for one, so the wait would time out and report that the brief
    never appeared — about a composer that is not here. Those keep the
    one-shot send, which is what they had before this path existed.
    """
    if task.doc.get("host"):
        return False
    return command_stem(task) in {"cursor-agent", "cursor"}


def composer_holds(output: str, text: str) -> bool:
    """The brief is sitting unsubmitted: `→` and the text as one run.

    Whitespace is stripped so a pane wrap cannot hide a match, and the
    arrow is required immediately before the text so a leftover paste
    plus the idle `→ Plan, search…` placeholder does not count.
    """
    blob = re.sub(r"\s+", "", output)
    return "→" + re.sub(r"\s+", "", text) in blob


def wait_for_composer(session: str, text: str, timeout: int) -> bool:
    """Poll `session capture` until `composer_holds`, or the timeout."""
    deadline = time.monotonic() + timeout
    while True:
        cap = subprocess.run(
            ["thurbox-cli", "session", "capture", session, "--json"],
            capture_output=True,
            check=False,
        )
        if cap.returncode == 0:
            try:
                doc = json.loads(cap.stdout.decode())
            except ValueError:
                doc = {}
            output = doc.get("output") if isinstance(doc, dict) else ""
            if isinstance(output, str) and composer_holds(output, text):
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def trust_and_send(
    session: str, text: str, timeout: int = 20, *, verify: bool = False
) -> tuple[bool, str]:
    """Answer the trust dialog, then type. The order is the whole point (§1b).

    The dialog is answered in-process by `session_trust.py`, the module
    `fleet session-trust` runs too: a machine with no bash could not run
    the old shell script, and dispatch then failed after `session create`, leaving a
    session that was never sent its brief.

    `verify` is the type-then-look-then-Enter path for a spawn whose first
    Enter would otherwise be swallowed. `fleet queue send` and a fixer
    reused into a live session leave it off: those panes are already up.
    """
    trust = _load_sibling("fleet_session_trust", "session_trust.py")
    code, report = trust.answer_dialogs(session, timeout, visible_only=True)
    if code != 0:
        return False, report
    # The returncode is READ. It used to be thrown away, so a send into a
    # session that had gone away returned `True` and every caller reported a
    # prompt it had not delivered — the same silence, one layer down, that
    # `sends` exists to end.
    send = ["thurbox-cli", "session", "send", session, text]
    if verify:
        send.append("--no-enter")
    sent = subprocess.run(send, capture_output=True, check=False)
    if sent.returncode != 0:
        detail = (sent.stderr + sent.stdout).decode().strip().splitlines()
        return False, "session send failed: " + (detail[-1] if detail else NO_OUTPUT)
    if not verify:
        return True, report
    if not wait_for_composer(session, text, timeout):
        return False, (
            "typed the brief but it did not appear in the composer; not submitting"
        )
    keyed = subprocess.run(
        ["thurbox-cli", "session", "key", session, "enter"],
        capture_output=True,
        check=False,
    )
    if keyed.returncode != 0:
        detail = (keyed.stderr + keyed.stdout).decode().strip().splitlines()
        return False, "session key enter failed: " + (
            detail[-1] if detail else NO_OUTPUT
        )
    return True, report


def spawn_fixer(
    task: Task, name: str, brief_path: str, branch: str, repo_path: str | None = None
) -> tuple[str, str]:
    """A session on the branch that already exists. (session id, note).

    `branch` is the pull request's own head branch and not the task's record of
    it: a task can carry a second pull request on a different branch, and the
    fix has to land on the branch the pull request is actually open from.

    `repo_path` is the checkout that pull request is IN, for the same reason —
    a task may span repositories, and the fix has to be made in the one the
    change request is open on. It defaults to the primary.
    """
    # A remote task's `repo` is a path on its host, and `branch_checkout` below
    # is local git. Said plainly rather than left to that function, which would
    # otherwise answer "is not a git checkout" about a checkout that exists and
    # is simply somewhere else — a true sentence that sends the reader looking
    # in the wrong place.
    if task.doc.get("host"):
        return "", (
            f"this task ran on host {task.doc['host']} and its checkout is there, so "
            "a fixer would have to be spawned there too — which this does not yet do. "
            "The pull request is still classified and still merged; only the fixer is "
            "withheld. Send the fix into that worker's own session, or fix it by hand."
        )

    problem = profile_problem(task.doc.get("profile") or "default")
    if problem:
        return "", problem
    slug = f"{task.topic}__{task.id}"
    path, note = branch_checkout(repo_path or task.doc["repo"], branch, slug)
    if not path:
        return "", note
    create = _fixer_create_cmd(task, name, path)
    try:
        out = subprocess.run(create, capture_output=True, check=True).stdout
        doc = json.loads(out)
        session = doc["id"]
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as exc:
        detail = (getattr(exc, "stderr", b"") or b"").decode().strip()
        return "", f"could not spawn a fixer: {detail or exc}"
    if not doc.get("created", True):
        # Adopted, so it may be mid-turn. The same rule as everywhere else:
        # only the agent's own word puts it at rest (§4a).
        state, _ = session_state(session)
        if state not in SESSION_AT_REST:
            return "", f"adopted an existing session in state {state or 'unknown'}; not typing into it"
    send = f"Read {os.path.abspath(brief_path)} and do what it says."
    ok, report = trust_and_send(
        session, send, verify=needs_verified_submit(task)
    )
    if not ok:
        return "", f"session {session} exists but was NOT prompted: {report}"
    return session, f"{note}; session {session}"


def _fixer_create_cmd(task: Task, name: str, path: str) -> list:
    """The `session create` command that spawns a fixer in `path`."""
    create = [
        "thurbox-cli", "session", "create",
        # A fixer is a worker fleet spawned, so it wears the same mark under the
        # same setting — and the same byte-safe cut, which is the one that made
        # `[:64]` wrong here rather than merely generous.
        "--name", session_name(name, worker_glyph()),
        "--repo-path", path,
        # Reconciling desired state, so a name already in use is the fixer that
        # is already there rather than news (§1c). `spawn_fixer` reads `created`.
        "--on-existing", "adopt",
    ]
    flags = profile_flags(task.doc.get("profile") or "default")
    if "--command" not in flags:
        # The third site that used to open-code this, and the second door into
        # a policy-covered repository (#116): a fixer is a worker fleet spawns,
        # so it runs the agent the task runs. Whether the policy still clears
        # that agent is asked by the caller, before a dry run answers — see
        # `shepherd_pr`'s `policy-refused` row — because a refusal here would
        # reach nobody until a real pass had already decided to spawn.
        agent = task_agent(task)
        if agent:
            create += ["--agent", agent]
    parent = os.environ.get("THURBOX_SESSION")
    if parent:
        create += ["--parent", parent]
    create += flags + ["--json"]
    return create


def author_can_push(cr: forge.ChangeRequest) -> tuple[bool, str]:
    """Was this pull request opened by someone who owns the repository?

    `classify`'s `foreign` check already proves the CODE is ours: a stranger
    cannot create a branch here. This proves the PULL REQUEST is. Anyone with
    read access can open one between two branches that already exist, and the
    body carrying the attestation would then be theirs to write — so the last
    thing checked before an unattended merge is who opened it.

    "May this account push here" and not "is this account the owner": the owner
    of `Thurbeen/fleet` is an organisation and no pull request is ever authored
    by one. How a forge answers that is the adapter's business, and so is
    caching it for the pass — the answer does not change inside a run and every
    open pull request would otherwise ask it again.
    """
    if not cr.author:
        return False, "the forge did not say who opened it"
    if cr.author_is_bot:
        return False, f"{cr.author} is a bot"
    which, why = forge.for_repo(cr.repo)
    if which is None:
        return False, why
    return which.can_push(cr.repo, cr.author)


def merge_change_request(cr: forge.ChangeRequest) -> tuple[bool, str]:
    """Merge, by the one method fleet's remotes allow.

    A forge that cannot perform that method is a refusal fleet can say out
    loud, and it is said BEFORE the merge rather than reported after one that
    silently used a different method. A GitLab project can forbid squash; this
    is where that shows up as a sentence rather than as a surprise on `main`.
    """
    which, why = forge.for_repo(cr.repo)
    if which is None:
        return False, why
    if MERGE_METHOD not in which.merge_methods:
        return False, (
            f"{which.name} cannot merge by {MERGE_METHOD}, which is the only "
            "method fleet merges by"
        )
    return which.merge(cr, MERGE_METHOD, DELETE_MERGED_BRANCH)


# --- the pass itself ---------------------------------------------------------


def record_shepherd(task: Task, entry: dict | None) -> None:
    """The idempotency record. Its absence means nothing is outstanding."""
    if entry is None:
        task.doc.pop("shepherd", None)
    else:
        task.doc["shepherd"] = entry
    task.save()
    fleet_platform.append_record(
        task.file(PROGRESS_FILE), json.dumps({"shepherd": entry, "observed": now()}) + "\n"
    )


def rec_for(task, url: str) -> dict:
    """The shepherd record, but only if it is about THIS pull request.

    A task's branch can carry a second pull request once its first one merged —
    that is the whole bug this command was rewritten for. Without this, the
    record left behind by the fixer sent for the FIRST one reads as "a fixer is
    already in flight" and the second one is never touched again.
    """
    rec = (task.doc.get("shepherd") or {}) if task else {}
    if not rec:
        return {}
    # A record written before this field existed is about the task's artifact,
    # which is the only pull request the old command could ever have seen.
    return rec if rec.get("pr", url) == url else {}


def shepherd_pr(cr: forge.ChangeRequest, task, args) -> dict:
    """Inspect one open change request and do the one thing it calls for.

    `task` is the task it belongs to, or None: the forge is the source of the
    list now, so a pull request nobody recorded is shepherded like any other.
    It simply has no session to send a fixer into, which is said out loud.
    """
    url = cr.url
    row = {
        "task": task.ref if task else "",
        "pr": url,
        "repo": cr.repo.qualified,
        "condition": "undetermined",
        "detail": "",
        "action": "none",
        "note": "",
    }

    method = task_publish(task)[0] if task else None
    review = review_lifecycle()
    condition, detail, obligation = _shepherd_classify(cr, task, args, method, review)
    if obligation is not None:
        row["review"] = obligation
    row["condition"], row["detail"] = condition, detail
    word = publish_word(cr, condition)
    if obligation and obligation["state"] == "clean" and condition == "ready":
        word = "ready"
    if cr.draft and condition == "ready":
        # Every gate holds, and it is still a draft until the flip below lands.
        word = "draft"
    # What the pass SAW, written down for every linked task and whatever the
    # condition — the facts all arrived in the one forge listing above, and a
    # record that keeps them is the difference between "shipped" and "its
    # checks failed forty minutes ago". A dry run writes nothing, here as
    # everywhere else below.
    if task and not args.dry_run:
        record_publish(task, word, detail, "shepherd")
    rec = rec_for(task, url)

    if condition in ("review-pending", "review-findings"):
        row["action"] = obligation["action"]
        row["note"] = obligation["detail"]
        return row

    if _shepherd_left(cr, condition, task, args, rec, row):
        return row

    if condition == "ready" and cr.draft:
        return _shepherd_mark_ready(cr, task, args, row, rec)
    if condition == "ready":
        return _shepherd_ready(cr, task, args, row, rec, word, method, review)

    # Fixable, but only a task carries a session, a worktree and a brief
    # directory to fix it from.
    if not task:
        row["action"] = "no-task"
        row["note"] = (
            "no task records this pull request, so there is no session to send a "
            "fixer into; it is classified and left for you"
        )
        return row

    if _fixer_refused(task, args, row, rec, condition, detail):
        return row
    return _shepherd_fix(cr, task, args, row, rec, condition, detail)


def _shepherd_classify(cr: forge.ChangeRequest, task, args, method, review) -> tuple:
    """(condition, detail, review obligation) for one change request."""
    if draft_phase(cr, task):
        # Judged as the pull request it will be once it leaves draft, so its
        # fixers and threads work as they do on any other. The independent
        # review is not asked: its reviewer cannot record on a draft, and it
        # starts on the first pass after the flip.
        condition, detail = classify(dataclasses.replace(cr, draft=False), method)
        if condition == "ready":
            condition, detail = threads_gate(cr, detail)
        return condition, detail, None
    required = review.policy(sys.modules[__name__], cr.repo)
    condition, detail = classify(cr, "pr" if required and method is None else method)
    if condition == "ready":
        condition, detail = threads_gate(cr, detail)
    return review.gate(
        sys.modules[__name__], cr, task, args, condition, detail,
    )


def _shepherd_left(cr: forge.ChangeRequest, condition: str, task, args, rec: dict,
                   row: dict) -> bool:
    """Settle a change request shepherd neither merges nor fixes. True when it did."""
    # A dry run writes nothing at all, including the record-clearing below:
    # "change nothing" is the flag's whole contract, and a stale record
    # cleared by a dry run is a fixer that a later real pass re-sends.
    if condition in ("closed", "undetermined"):
        if condition == "closed" and rec and not args.dry_run:
            record_shepherd(task, None)
        return True

    if condition == "foreign":
        # Reported, and nothing else. Not merged, and not handed to an agent.
        row["action"] = "left-alone"
        row["note"] = "not ours; fleet only merges and only fixes its own"
        return True

    if condition == "threads-open":
        # Held, and no fixer: the threads themselves are what the worker needs,
        # and `shepherd_comments` runs next on this same row and sends them —
        # each quoted, with its file and line — into the task's session.
        row["action"] = "not-merged"
        row["note"] = (
            ("not marked ready for review" if cr.draft else "not merged")
            + " while a review thread is unresolved; "
            + ("what nobody has answered yet goes to its session" if task
               else "no task records it, so resolving them is yours")
        )
        return True
    return False


def _shepherd_ready(cr: forge.ChangeRequest, task, args, row: dict, rec: dict,
                    word: str, method, review) -> dict:
    """A change request every gate clears: merge it, or say why not."""
    if rec and not args.dry_run:
        record_shepherd(task, None)
    if word == "green":
        # Asked BEFORE the allowlist, because this is the deeper reason:
        # not "fleet does not merge here" but "nothing vetted the head
        # that would land". Never merged — see `publish_word`
        # for why this is not the same state as `ready`.
        row["action"] = "ready"
        row["note"] = (
            "ready to merge — not attested; yours. Every gate the forge "
            "can answer holds, and nothing says review, tests and lint "
            "ever ran on the commit that would land"
        )
        return row
    if not may_auto_merge(cr.repo):
        row["action"] = "ready"
        row["note"] = f"fleet does not merge in {cr.repo}; this one is yours"
        return row
    if args.no_merge:
        row["action"] = "ready"
        row["note"] = "--no-merge"
        return row
    held = needs_human(cr)
    if held:
        row["action"] = "needs-human"
        row["note"] = f"needs a human: {held}"
        return row
    # The last gate, and the one a pull request body cannot write for
    # itself. Asked before --dry-run answers, so a dry run is honest
    # about what it would actually merge.
    allowed, why = author_can_push(cr)
    if not allowed:
        row["action"] = "not-merged"
        row["note"] = (
            f"{why} — fleet merges unattended only what someone who can "
            "push here opened"
        )
        return row
    if args.dry_run:
        row["action"] = "would-merge"
        row["note"] = f"{_merge_how(cr)} ({why})"
        return row
    return _shepherd_merge(cr, task, row, method, review)


def _shepherd_mark_ready(cr: forge.ChangeRequest, task, args, row: dict, rec: dict) -> dict:
    """A task's draft every gate clears: take it out of draft, once. Never merged here.

    Not merged in the same pass, because "ready for review" is what starts the
    jobs that run on it: the next pass sees their checks, and every merge gate,
    exactly as it would for a pull request that was never a draft.
    """
    if rec and not args.dry_run:
        record_shepherd(task, None)
    flipped = draft_flipped(task, cr.url)
    if flipped:
        row["action"] = "left-alone"
        row["note"] = (
            f"shepherd marked it ready for review at {flipped.get('at', 'an earlier pass')} "
            "and it is a draft again; whoever put it back decides when it leaves"
        )
        return row
    if args.dry_run:
        row["action"] = "would-mark-ready"
        row["note"] = "every check passed, nothing to fix, no open thread"
        return row
    which, why = forge.for_repo(cr.repo)
    done, note = which.mark_ready(cr) if which else (False, why)
    row["action"], row["note"] = ("marked-ready" if done else "mark-ready-failed"), note
    if done:
        every = task.doc.get("shepherd_ready")
        every = dict(every) if isinstance(every, dict) else {}
        every[cr.url] = {"head": cr.head_sha, "at": now()}
        task.doc["shepherd_ready"] = every
        task.save()
        record_publish(task, "draft", "marked ready for review; gated as usual next pass",
                       "shepherd")
    return row


def _merge_how(cr: forge.ChangeRequest) -> str:
    """How a merge of `cr` would be done, in its forge's words."""
    which, _ = forge.for_repo(cr.repo)
    return (
        which.describe_merge(MERGE_METHOD, DELETE_MERGED_BRANCH)
        if which
        else f"merge by {MERGE_METHOD}"
    )


def _shepherd_merge(cr: forge.ChangeRequest, task, row: dict, method, review) -> dict:
    """The review lifecycle's last word, then the merge itself."""
    allowed, why = review.before_merge(sys.modules[__name__], cr, method)
    if not allowed:
        row["action"], row["note"] = "not-merged", why
        return row
    ok, note = merge_change_request(cr)
    row["action"], row["note"] = ("merged" if ok else "merge-failed"), note
    if ok and task:
        record_shepherd(task, {"condition": "merged", "detail": note,
                               "pr": cr.url, "at": now()})
        record_publish(task, "merged", note, "shepherd")
    return row


def _fixer_refused(task: Task, args, row: dict, rec: dict, condition: str,
                   detail: str) -> bool:
    """Whether the agent policy refuses a fixer here. True has filled `row`."""
    url = row["pr"]
    # The second door into a policy-covered repository (#116), and the half of
    # it that stayed open: the fixer already runs the agent the task runs, and
    # now it asks whether that agent may still serve this repository. The same
    # question `dispatch` asks, put to the same function, so nothing here reads
    # a policy for itself. A refusal has no dispatch to fail — this fixer is
    # the reconciler's, not a person's — so it is a row and a record, and the
    # pull request is still classified and still merged like any other.
    #
    # BOTH ROUTES, not only the spawn. Typing a fix brief into the task's own
    # worker puts the same agent back to work on the same repository, so a
    # policy that no longer clears it refuses that too.
    #
    # A `--command` profile is the case with no agent to judge: thurbox refuses
    # `--agent` beside it, so the fixer names none and `task_agent` says "".
    # `agent_policy_refusal` refuses it on a covered repository, and that
    # answer is taken here rather than softened: fleet cannot tell which agent
    # a free command launches, so it cannot tell the policy is kept, and "no
    # agent named" is not the same claim as "no rule to break". On a
    # repository no rule covers it stays silent, so nothing changes for an
    # operator with no policy at all.
    #
    # NOT for a `--host` task, which is the one case where a better refusal
    # already exists: a fixer never goes out for one at all, and `spawn_fixer`
    # says why in terms that help ("its checkout is there, so a fixer would
    # have to be spawned there too"). The policy's own sentence about a host is
    # true and vaguer, and letting it win here would blunt a precise refusal
    # the moment an operator writes their first rule.
    refusal = None if task.doc.get("host") else agent_policy_refusal(task)
    if refusal:
        row["action"] = "policy-refused"
        row["note"] = f"no fixer sent — {refusal}"
        if not args.dry_run:
            entry = {
                "condition": condition,
                "detail": detail,
                # No `session`: nothing was spawned, and a record that named
                # one would read as a fixer in flight on the next pass.
                "refused": refusal,
                "pr": url,
                "at": now(),
            }
            # Two things this must not do. It must not overwrite a record that
            # NAMES a fixer — policy is checked before liveness so even a
            # busy worker reports the refusal — because that id is the only handle a
            # later pass has on a session that is out there working, and
            # dropping it is how a second fixer gets sent at a pull request
            # that already has one. The row still reports the refusal; the
            # record keeps the more important fact.
            #
            # And it must not rewrite an unchanged refusal on every pass: the
            # reconciler comes round every fifteen minutes and the same
            # sentence would grow progress.jsonl forever. Compared on
            # everything except `at`, so a pull request that drifts from
            # `conflicting` to `checks-failed` under a standing refusal still
            # updates the record — the alternative freezes the condition, the
            # detail and the time at whatever the FIRST refusal saw.
            unchanged = ({k: v for k, v in rec.items() if k != "at"}
                         == {k: v for k, v in entry.items() if k != "at"})
            if not rec.get("session") and not unchanged:
                record_shepherd(task, entry)
        return True
    return False


def _shepherd_fix(cr: forge.ChangeRequest, task: Task, args, row: dict, rec: dict,
                  condition: str, detail: str) -> dict:
    """Send a fixer for a fixable change request, unless this one already went out.

    Keyed on (condition, head), not on the pull request. A record that names
    a fixer used to hold back EVERY later send for its pull request, so a
    `checks-failed` fix that finished, then `main` moving under it, read
    "in-flight ... now done" and the conflict reached nobody until `--force`.
    The same condition on the same head is still sent once; anything else
    goes to whichever session is already on it, once that session is at rest
    by its own word.
    """
    # Every liveness check below shares one `session list` snapshot, so a
    # fixer and the task's own worker read "gone" from the same evidence.
    rec_session = str(rec["session"]) if rec.get("session") else ""
    worker = str(task.doc.get("session") or "")
    sent = fixes_sent(rec, cr.head_sha)
    probe = rec_session or worker or any(s.get("session") for s in sent)
    live, live_why = live_sessions() if probe else (set(), "")

    if not args.force and _fix_already_sent(sent, condition, cr.head_sha, live, live_why, row):
        return row

    # A session already on this pull request has the context and the
    # worktree: the fixer recorded for it first, then the task's own worker.
    # Only its own word puts it at rest (§4a), and only when every one of them
    # is gone is a fresh session spawned — never beside one that is there.
    reuse = _fix_target(rec_session, worker, live, live_why, row)
    if reuse is None:
        return row

    base = cr.base_branch or task.doc.get("base") or "main"
    branch = cr.head_branch or task.doc["branch"]
    title = FIXER_TITLES[condition].format(n=cr.number, base=base)

    if args.dry_run:
        row["action"] = "would-ping" if reuse else "would-spawn"
        how = f"typed into {reuse}, already on it" if reuse else "a fresh session on the branch"
        row["note"] = f"{title} ({how})"
        return row

    return _send_fixer(cr, task, row, rec, condition, detail, reuse, title, base, branch)


def fixes_sent(rec: dict, head: str) -> list:
    """What `shepherd` already sent about this pull request's current head.

    One entry per (condition, head) it sent, as `shepherd_comments` keeps one
    per comment id. A push moves the head, which resets the list. A record
    written before the list existed names one send and no head, so it is read
    as having been about this one: the conservative reading, because it only
    ever holds back the condition that record names.
    """
    every = rec.get("sent")
    if isinstance(every, list):
        return [s for s in every if isinstance(s, dict) and s.get("head", "") == head]
    if rec.get("session") and rec.get("condition") in FIXABLE:
        return [{"condition": rec["condition"], "head": head, "session": str(rec["session"]),
                 "at": rec.get("at"), "brief": rec.get("brief")}]
    return []


def _fix_already_sent(sent: list, condition: str, head: str, live, live_why: str,
                      row: dict) -> bool:
    """Whether this (condition, head) already went to a session that is still there.

    True has filled `row`. A session that is gone since takes the fix with
    it, so the same send goes out again — a fixer that crashed must not stall
    its pull request's recovery forever.
    """
    told = next((s for s in reversed(sent) if s.get("condition") == condition
                 and s.get("head", "") == head), None)
    if told is None:
        return False
    sid = str(told.get("session") or "")
    status = session_status(sid, live) if sid else "gone"
    if status == "gone":
        return False
    if status == "unknown":
        row["action"] = "left-alone"
        row["note"] = (
            f"could not tell whether the session sent {condition} at {told.get('at')} "
            f"({sid}) is still there ({live_why or 'no state reported'}). Nothing sent."
        )
        return True
    row["action"] = "already-sent"
    row["note"] = (
        f"{condition} on this head went to {sid} at {told.get('at')} (now {status}); "
        "not again until the condition or the head changes. `--force` overrides."
    )
    return True


def _fix_target(rec_session: str, worker: str, live, live_why: str, row: dict) -> str | None:
    """The session at rest to type the fix into, "" to spawn one, None to wait.

    None has filled `row`. The first candidate that is not gone decides.
    """
    for sid, whose in ((rec_session, "the fixer sent for it"), (worker, "its own worker")):
        if not sid or (sid == worker and whose != "its own worker"):
            continue
        status = session_status(sid, live)
        if status == "gone":
            continue
        if status in SESSION_AT_REST:
            return sid
        row["action"] = "in-flight" if status in SESSION_BUSY else "left-alone"
        if status == "unknown":
            row["note"] = (f"could not tell whether {whose} {sid} is still there "
                           f"({live_why or 'no state reported'}). Nothing sent.")
        elif status in SESSION_BUSY:
            row["note"] = (f"{whose} {sid} is {status} — probably already on it. "
                           "Interrupting a turn is how a fix gets half-applied.")
        else:
            row["note"] = (f"{whose} {sid} reads {status}, which is an observation "
                           "and not the agent saying it is at rest. Nothing sent; look at "
                           f"the pane: thurbox-cli session capture {sid}")
        return None
    return ""


def _send_fixer(cr: forge.ChangeRequest, task: Task, row: dict, rec: dict, condition: str,
                detail: str, reuse: str, title: str, base: str, branch: str) -> dict:
    """Write the fix brief, ping `reuse` or spawn a fresh session, and record it.

    Two different acts with two words: `pinged` typed one line into a session
    that was already there, and `spawned` started a new one on the branch.
    """
    checkout = task_checkout(task, cr.repo)
    drift = base_drift(checkout, base, branch) if condition == "conflicting" else ""

    path = next_fix_file(task, condition)
    fleet_platform.write_record(path, fixer_brief(task, cr, condition, detail, drift))

    if reuse:
        ok, report = trust_and_send(
            reuse, f"Read {os.path.abspath(path)} and do what it says."
        )
        session, how = (reuse if ok else ""), "pinged"
        note = report if not ok else f"{title} -> typed into {reuse}"
    else:
        session, note = spawn_fixer(task, title, path, branch, checkout)
        how = "spawned"
        note = f"{title} -> new session {session}" if session else note

    if not session:
        row["action"] = "not-dispatched"
        row["note"] = note
        return row
    row["action"] = how
    row["note"] = note
    at = now()
    sent = fixes_sent(rec, cr.head_sha) + [{
        "condition": condition, "head": cr.head_sha, "session": session,
        "how": how, "at": at, "brief": os.path.basename(path),
    }]
    record_shepherd(
        task,
        {
            "condition": condition,
            "detail": detail,
            "session": session,
            "how": how,
            "pr": cr.url,
            "head": cr.head_sha,
            "brief": os.path.basename(path),
            "at": at,
            "sent": sent,
        },
    )
    return row


# --- plain review feedback ---------------------------------------------------
#
# `classify` hands a fixer four conditions, and the only review it reads is a
# reviewer pressing "request changes". Everything else a person says on a pull
# request — a review left as a comment, a line comment, a question in the
# conversation — reached nobody: the worker had finished its turn, and fleet
# itself never read past the review decision. This is the fifth thing shepherd
# hands a session, and it is deliberately NOT a condition: a condition decides
# merging and the board's publish word, and a comment must change neither. So
# it runs beside `shepherd_pr` on its own record, and a pull request can be
# `green`, still merged where the operator allows it, and still have its
# comments delivered.

COMMENTS_TITLE = "Answer the review comments on PR #{n}"

# A review is something to answer when it says something. An APPROVED review's
# body is a thank-you far more often than a request, and a DISMISSED or PENDING
# one is not a verdict anybody stands behind.
COMMENT_REVIEW_STATES = ("commented", "changes-requested")


def comments_record(task: Task, url: str) -> dict:
    """What `shepherd` already sent about this pull request's comments.

    Keyed by pull request, not one record per task, because a task that spans
    repositories has a pull request in each — and a single record would flip
    between them, resending every comment each time it did. And kept APART from
    `shepherd`'s own record, whose presence means "a fixer is in flight": a
    comment delivery written there would hold back the `checks-failed` or
    `conflicting` fixer the same pull request needs next.
    """
    every = task.doc.get("shepherd_comments")
    rec = every.get(url) if isinstance(every, dict) else None
    return rec if isinstance(rec, dict) else {}


def record_comments(task: Task, url: str, entry: dict) -> None:
    every = task.doc.get("shepherd_comments")
    every = dict(every) if isinstance(every, dict) else {}
    every[url] = entry
    task.doc["shepherd_comments"] = every
    task.save()
    fleet_platform.append_record(
        task.file(PROGRESS_FILE),
        json.dumps({"shepherd_comments": {"pr": url, **entry}, "observed": now()}) + "\n",
    )


def unanswered_feedback(items: list, me: str, sent, bots: bool = False) -> list:
    """The comments a worker has not been sent and nobody has answered, oldest first.

    Never the account fleet runs as — that is the worker's own reply, or the
    operator's `review-prs` reviewer, and sending a session its own words is
    how it ends up answering itself — and never a bot. A thread somebody
    RESOLVED is answered, and so is a line comment fleet's account replied to
    later IN THE SAME THREAD: that reply is about that comment and nothing
    else. A conversation comment or a review has no such reply to point at,
    so only `sent` answers it. Reading "fleet's account posted anything at the
    top level after it" as an answer dropped a person's comment for good the
    moment fleet's account left an empty review; re-sending, once, a comment
    a worker had already answered before this existed is the cheaper error.

    `bots` is the draft phase's one exception (`draft_repos`): where bots ARE
    the reviewers, a bot's line comment or submitted review is relayed like a
    person's. Its conversation comments still are not — coverage tables and
    preview links are status, not review.
    """
    me = me.lower()
    mine = [f for f in items if f.author.lower() == me]
    last_in: dict = {}
    for f in mine:
        if f.kind == "inline" and f.at:
            last_in[f.thread] = max(last_in.get(f.thread, ""), f.at)
    out = [f for f in items if _feedback_open(f, me, sent, last_in, bots)]
    return sorted(out, key=lambda f: f.at)


def _feedback_open(f, me: str, sent, last_in: dict, bots: bool = False) -> bool:
    """Whether one comment is still to be sent, for `unanswered_feedback`."""
    if f.author_is_bot and not (bots and f.kind in ("inline", "review")):
        return False
    if f.id in sent or not f.author or f.author.lower() == me:
        return False
    if not f.body.strip():
        return False
    if f.kind == "review" and f.state not in COMMENT_REVIEW_STATES:
        return False
    return not (f.kind == "inline" and (f.resolved or (f.at and f.at < last_in.get(f.thread, ""))))


def comments_brief(task: Task, cr: forge.ChangeRequest, items: list) -> str:
    """The fix brief for comments: each one named, quoted, and linked."""
    n = cr.number
    parts = [
        f"# {COMMENTS_TITLE.format(n=n)}",
        "",
        f"Somebody left review feedback on an open pull request from fleet task "
        f"`{task.ref}`. Nobody requested changes, so nothing else would have told "
        "you. This is the whole instruction set.",
        "",
        f"- **Pull request.** {cr.url} — {cr.title}".rstrip(" —"),
        f"- **Repo.** `{task.doc['repo']}`",
        f"- **Branch.** `{cr.head_branch or task.doc['branch']}`. It already exists.",
        "",
        "## The comments",
    ]
    for f in items:
        where = ""
        if f.path:
            where = f" on `{f.path}:{f.line}`" if f.line else f" on `{f.path}`"
        what = {"review": "review", "inline": "line comment"}.get(f.kind, "comment")
        parts += ["", f"### {f.author}, {what}{where}", "", f.url or "(the forge gave no link)", ""]
        parts += ["> " + line if line else ">" for line in f.body.strip().splitlines()]
    parts += [
        "",
        "## What to do",
        "",
        "Answer each one. Where it asks for a change, make it — a test first when it"
        " is behaviour — and push to the same branch. Where it is mistaken or out of"
        " scope, reply on the forge with the reason. Either way reply to it, and"
        " resolve the thread if it is one.",
        "",
        "## Hard constraints",
        "",
        "- Push to the branch that is already open. Do not open a second pull request,"
        " and do not close this one.",
        "- Do not merge it. Merging is not yours to do here.",
        "- Do not widen the change beyond what the comments ask for.",
        "",
        "Fleet reads the pull request itself and does not need a message.",
    ]
    return "\n".join(parts) + "\n"


def shepherd_comments(cr: forge.ChangeRequest, task, args, row: dict) -> None:
    """Hand a pull request's new review comments to a session at rest.

    Runs after `shepherd_pr` and adds `comments` and `comments_note` to its
    row. Same rules as a fixer: the task's own session first, only when the
    agent itself says it is at rest, never into a turn; a fresh session on the
    branch when that session is gone — the same choice a gone worker gets for
    a failing check, and for the same reason: the comment still needs an
    answer. A comment already sent is never sent again, by id.
    """
    row["comments"] = "none"
    if not _comments_wanted(cr, task, row):
        return
    which, why = forge.for_repo(cr.repo)
    if which is None:
        return
    items, why = which.feedback(cr.ref)
    if why:
        row["comments"], row["comments_note"] = "not-read", why
        return
    rec = comments_record(task, cr.url)
    sent = set(rec.get("sent") or [])
    if not any(f.id not in sent for f in items):
        return
    me, why = which.whoami(cr.repo.host)
    if not me:
        # Without fleet's own account, its workers' replies are indistinguishable
        # from a reviewer's, and the first one sent would start a loop.
        row["comments"], row["comments_note"] = "left-alone", (
            f"could not tell which account fleet runs as ({why}); no comment sent"
        )
        return
    new = unanswered_feedback(items, me, sent, bots=draft_listed(cr.repo))
    if not new:
        return
    count = f"{len(new)} new comment{'s' if len(new) != 1 else ''}"

    target = _comments_target(task, cr, rec, count, row)
    if target is None:
        return

    # The policy door `shepherd_pr` asks for a fixer, asked for this too: a
    # comment sent puts the same agent back to work on the same repository.
    refusal = None if task.doc.get("host") else agent_policy_refusal(task)
    if refusal:
        row["comments"], row["comments_note"] = "policy-refused", f"{count}; nothing sent — {refusal}"
        return

    if args.dry_run:
        row["comments"] = "would-send"

        row["comments_note"] = f"{count} -> " + (target or "a fresh session on the branch")
        return

    _send_comments(task, cr, new, sent, target, count, row)


def _comments_wanted(cr: forge.ChangeRequest, task, row: dict) -> bool:
    """Whether `shepherd_comments` should read this pull request's comments at all."""
    if row.get("condition") == "review-findings":
        # The linked assessment already routes all findings, including a
        # reviewer using the same forge account. Do not hand it off twice.
        return False
    if not task or cr.state != "open" or cr.head_is_ours is not True:
        return False
    # A review decision of "request changes" is that condition's fixer, which
    # already says "address the review" — asked of the decision itself, since
    # a conflict outranks it in `classify` and would hide it from the row. A
    # fixer this pass just sent is mid-turn, and a merged one is done.
    if cr.review_decision == "changes-requested" or row.get("condition") in ("closed", "foreign"):
        return False
    return row.get("action") not in ("pinged", "spawned", "policy-refused", "merged")


def _comments_target(task: Task, cr: forge.ChangeRequest, rec: dict, count: str,
                     row: dict) -> str | None:
    """The session at rest to send comments to, "" for a fresh one, None to wait.

    None has already said why on `row`.
    """
    # Who gets them: a session already working on this pull request for fleet —
    # the one sent its last comments, then the fixer `shepherd_pr` recorded —
    # then the task's own worker. The first that is still there decides.
    fixer = rec_for(task, cr.url).get("session")
    candidates = [str(s) for s in (rec.get("session"), fixer, task.doc.get("session")) if s]
    live, live_why = live_sessions() if candidates else (set(), "")
    for sid in dict.fromkeys(candidates):
        status = session_status(sid, live)
        if status == "gone":
            continue
        if status in SESSION_AT_REST:
            return sid
        said = status if status != "unknown" else live_why or "unknown"
        row["comments"] = "left-alone"
        row["comments_note"] = (
            f"{count}; session {sid} is {said}"
            " — sent once it is at rest, never into a turn"
        )
        return None
    return ""


def _send_comments(task: Task, cr: forge.ChangeRequest, new: list, sent: set,
                   target: str, count: str, row: dict) -> None:
    """Write the comments brief, hand it over, and record what was sent."""
    title = COMMENTS_TITLE.format(n=cr.number)
    path = next_fix_file(task, "comments")
    fleet_platform.write_record(path, comments_brief(task, cr, new))
    if target:

        ok, note = trust_and_send(target, f"Read {os.path.abspath(path)} and do what it says.")
        session = target if ok else ""
    else:
        checkout = task_checkout(task, cr.repo)
        session, note = spawn_fixer(task, title, path, cr.head_branch or task.doc["branch"], checkout)
    if not session:
        row["comments"], row["comments_note"] = "not-sent", f"{count}; {note}"
        return
    row["comments"], row["comments_note"] = "sent", f"{count} -> {session}"
    record_comments(task, cr.url, {
        "sent": sorted(sent | {f.id for f in new}),
        "session": session,
        "brief": os.path.basename(path),
        "at": now(),
    })


def repo_from_checkout(repo_path: str) -> forge.RepoId | None:
    """The repository a checkout's `origin` names, or None. Local, and no network.

    The last resort, and the one that makes this work on a topic whose tasks
    have not shipped anything yet: before the first artifact is reported, the
    only thing naming the repository is the checkout the tasks were given.
    Which forge a remote URL belongs to is each adapter's own question.
    """
    if not repo_path or not os.path.isdir(repo_path):
        return None
    return forge.repo_from_remote(
        git_out(repo_path, ["remote", "get-url", "origin"]).strip()
    )


def shepherd_targets(tasks: list) -> dict:
    """task.ref -> [RepoId], derived and never hardcoded.

    The queue's tasks name their repositories: an artifact URL gives the
    repository — host and path — outright, and a repository that has reported
    nothing yet inherits the answer of the other tasks sharing its local
    checkout, or is read off that checkout's `origin`. Merging stays limited to
    `auto_merge_repos()` whatever comes out of here: knowing about a repository
    and being allowed to merge in it are different questions.

    EVERY REPOSITORY THE TASK NAMES, not just the one its artifact happens to
    be on. A task that spans repositories has a change request in each, and a
    shepherd that enumerated the primary alone would stop watching the rest —
    which is exactly the "a pull request nobody is watching goes bad" case this
    command exists for.
    """
    repo_of: dict[str, list] = {}
    by_path = _artifact_repo_paths(tasks)
    for task in tasks:
        found = _checkout_repos(task, by_path)
        # An artifact on a repository none of this task's checkouts resolve to
        # is still this task's — a worker that pushed to a fork, a checkout
        # that has since moved — and dropping it would stop watching a pull
        # request the records name outright.
        for entry in recorded_artifacts(task):
            ref = pr_ref(entry.get("url"))
            if ref and ref.repo not in found:
                found.append(ref.repo)
        if found:
            repo_of[task.ref] = found
    return repo_of


def _artifact_repo_paths(tasks: list) -> dict:
    """checkout path -> RepoId, from every artifact URL the tasks recorded."""
    by_path: dict[str, forge.RepoId] = {}
    for task in tasks:
        for entry in recorded_artifacts(task):
            ref = pr_ref(entry.get("url"))
            if ref:
                by_path.setdefault(str(entry.get("repo") or ""), ref.repo)
    return by_path


def _checkout_repos(task: Task, by_path: dict) -> list:

    """The repositories `task`'s checkouts resolve to, caching each in `by_path`."""
    found: list = []
    for unit in task_repos(task):
        repo = by_path.get(unit["path"])
        if repo is None:
            repo = repo_from_checkout(unit["path"])
            if repo is not None:
                by_path[unit["path"]] = repo
        if repo is not None and repo not in found:
            found.append(repo)
    return found


def task_checkout(task: Task, repo: forge.RepoId) -> str:

    """The local checkout of `repo`, among the ones this task spans.

    A task that spans repositories has a change request in more than one of
    them, and a fixer sent into the primary's checkout would rebase the wrong
    tree and push the wrong branch. A task with ONE repository answers without
    asking git anything, so it costs what it always cost and cannot start
    behaving differently. So does a repository none of the checkouts resolve
    to: the primary is the fallback, which is what this was before there was
    more than one.
    """
    units = task_repos(task)
    if len(units) == 1:
        return units[0]["path"]
    for unit in units:
        if repo_from_checkout(unit["path"]) == repo:
            return unit["path"]
    return units[0]["path"]


def link_task(cr: forge.ChangeRequest, tasks: list) -> object:
    """The task this change request belongs to, or None.

    Two ways, and the second is the one that matters. The artifact is what a
    worker reported once. The HEAD BRANCH is what the pull request is actually
    open from, and it is what connects a task's second pull request back to it
    after its first one merged and its artifact stopped being current.

    Only a task whose artifact IS its own change request links by artifact. A
    `note` task's URL names the pull request it reviewed, and linking that
    would put the note task's method on somebody else's pull request — a method
    that asks for no attestation, which would clear the merge gate for it.
    """
    for task in tasks:
        if task_publish(task)[0] in CHANGE_METHODS and _artifact_is(cr, task):
            return task
    if cr.head_branch:
        for task in tasks:
            if str(task.doc.get("branch") or "") == cr.head_branch:
                return task
    return None


def _artifact_is(cr: forge.ChangeRequest, task: Task) -> bool:
    """True when one of `task`'s recorded artifacts is this change request."""
    for entry in recorded_artifacts(task):
        ref = pr_ref(entry.get("url"))
        if ref and ref.number == cr.number and ref.repo == cr.repo:
            return True
    return False


def shepherd_store():
    """Adoption of outside sessions, and the PR-state store: `shepherd_store.py`."""
    return _load_sibling("fleet_shepherd_store", "shepherd_store.py")


def cmd_prs(args) -> int:
    """Print the PR-state store the last shepherd pass wrote. Reads only that file."""
    return shepherd_store().cmd_prs(sys.modules[__name__], args)


def review_lifecycle():
    return _load_sibling("fleet_review_lifecycle", "review_lifecycle.py")


def cmd_review_record(args) -> int:
    return review_lifecycle().record(sys.modules[__name__], args)


def cmd_shepherd(args) -> int:
    """The fourth thing: the pull requests, after `watch` and after `collect`."""
    # No forge CLI on this machine, which is a local-only fleet and not a
    # fault: nothing to list and nothing to merge, said once rather than once
    # per repository the queue names.
    if not forge.available():
        _shepherd_no_forge(args)
        return 0
    # `all`, and deliberately not the default view. This does not act on the
    # tasks it loads — it derives the REPOSITORIES they name and then asks the
    # forge for every open pull request in each, including ones no task ever
    # recorded (the #25 case). Narrowing that to live topics would mean a queue
    # whose topics have all finished stops watching the repos it worked in, and
    # a pull request that goes bad after the last topic closed would be seen by
    # nobody. It is already a network pass over every repo; reading the
    # archived records costs it nothing it was not already paying.
    q = Queue(queue_root(), scope="all")
    only = q.get(args.ref).ref if args.ref else ""
    tasks = [
        task for task in sorted(q.tasks.values(), key=lambda t: t.ref)
        if not (args.topic and task.topic != args.topic) and not (only and task.ref != only)
    ]

    repo_of = shepherd_targets(tasks)
    repos = _shepherd_repos(repo_of, args.topic or only)
    named = [str(r) for r in repos]

    rows, unreadable, listings, observed = _shepherd_pass(repos, tasks, repo_of, args)

    # Sessions fleet did not spawn, and the one file of every watched PR's
    # state; `shepherd_store.py` owns both and argues them.
    store = shepherd_store()
    adopted = store.after_pass(sys.modules[__name__], args, list(q.tasks.values()),
                               observed, listings, unreadable)
    review_lifecycle().retire(sys.modules[__name__], listings, unreadable, args.dry_run)

    if args.json:
        print(json.dumps({
            "queue": os.path.abspath(queue_root()),
            "repos": [r.qualified for r in repos],
            "unreadable": unreadable,
            "prs": rows,
            "adopted": adopted,
        }, indent=2))
        return 0

    store.print_adopted(adopted, args.dry_run)
    _report_shepherd(q, args, named, rows, unreadable)
    return 0


def _report_shepherd(q: Queue, args, named: list, rows: list, unreadable: list) -> None:
    """`shepherd`'s report of what it found and did, then the run logs it moved."""
    if not named:
        print("shepherd: no task names a repository on a configured forge yet")
        return
    for bad in unreadable:
        print(f"shepherd: could not read the pull requests on {bad['named']}: "
              f"{bad['detail']} — nothing there was touched")
    if not rows:
        if not unreadable:
            print("shepherd: no open pull requests on " + ", ".join(named))
        return

    verb = "would do" if args.dry_run else "did"
    print(f"shepherd: {len(rows)} open pull request(s) on {', '.join(named)} "
          f"— what it {verb}:\n")
    for r in rows:
        _print_shepherd_row(r)
    _print_shepherd_summary(rows, args.dry_run)
    if not args.dry_run:
        refresh_run_logs(q)


def _shepherd_pass(repos: list, tasks: list, repo_of: dict, args) -> tuple:
    """List every repository's open pull requests and shepherd each one.

    (rows, unreadable, listings, observed).
    """
    rows, unreadable = [], []
    # What the PR-state store and adoption read after the pass: every listing
    # it made, and each open PR with the task and row it got.
    listings, observed = {}, []
    for repo in repos:
        here = [t for t in tasks if repo in repo_of.get(t.ref, ())]
        crs, err = open_prs(repo)
        if err:
            # A repository that could not be listed contributes nothing. An
            # empty answer and an unreadable one are not the same claim, and
            # only one of them means "nothing is open".
            unreadable.append({"repo": repo.qualified, "named": str(repo), "detail": err})
            continue
        listings[repo] = crs
        for cr in sorted(crs, key=lambda c: c.number):
            task = link_task(cr, here)
            row = shepherd_pr(cr, task, args)
            shepherd_comments(cr, task, args, row)
            rows.append(row)
            observed.append((cr, task, row))
    return rows, unreadable, listings, observed


def _shepherd_no_forge(args) -> None:
    """`shepherd` on a machine with no forge CLI: said once, and recorded."""
    why = forge.no_forge_reason()
    if not args.dry_run:
        shepherd_store().forge_unavailable(sys.modules[__name__], why)
        review_lifecycle().unavailable(sys.modules[__name__], why)
    if args.json:
        print(json.dumps({"queue": os.path.abspath(queue_root()), "repos": [], "unreadable": [],
                          "prs": [], "unavailable": why}, indent=2))
    else:
        print(f"shepherd: {why}")


def _shepherd_repos(repo_of: dict, narrowed) -> list:
    """Every repository a shepherd pass lists: the tasks', then the configured ones.

    The configured review repositories join only an un-narrowed pass — no
    `--topic`, no ref.
    """
    watched = {r for named in repo_of.values() for r in named}
    if not narrowed:
        for repo in review_lifecycle().configured_repos(sys.modules[__name__]):
            if not any(r.qualified.casefold() == repo.qualified.casefold() for r in watched):
                watched.add(repo)
    return sorted(watched, key=lambda r: r.qualified)


def _print_shepherd_row(r: dict) -> None:
    """One pull request's lines in `shepherd`'s report."""
    print(f"    {r['task'] or '(no task records it)'}  {r['pr']}")
    print(f"        {r['condition']}: {r['detail']}")
    if r["action"] != "none":
        print(f"        {r['action']}: {r['note']}" if r["note"] else f"        {r['action']}")
    if r.get("comments", "none") != "none":
        print(f"        comments {r['comments']}: {r.get('comments_note', '')}")


def _print_shepherd_summary(rows: list, dry_run: bool) -> None:
    """`shepherd`'s closing counts, caveats and merge scope."""
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    print("\nshepherd: " + ", ".join(f"{n} {a}" for a, n in sorted(counts.items())))
    unlinked = [r for r in rows if not r["task"]]
    if unlinked:
        print(f"          {len(unlinked)} belong to no task — the forge is what "
              "lists these, not the\n          records, so a pull request nobody "
              "queued is still watched and still merged.")
    undetermined = [r for r in rows if r["condition"] == "undetermined"]
    if undetermined:
        print(
            f"          {len(undetermined)} could not be determined and were left "
            "exactly as they are —\n          a PR that cannot be read is not a broken one."
        )
    if not dry_run and any(r["action"] in ("pinged", "spawned") for r in rows):
        print("          Fixers are working in place on the existing branches; nothing forked.")
    # An empty allowlist is the default a fresh clone has, so it gets a
    # sentence rather than a dangling "limited to , and only for" — and that
    # sentence names the file, because "fleet merged nothing" and "nobody has
    # told fleet where it may merge" are the same output otherwise.
    allowed = sorted(auto_merge_repos())
    if allowed:
        print(
            "          Merging is limited to " + ", ".join(allowed)
            + ", and only for a pull request whose\n"
            "          head branch is in that repo, that someone who can push there "
            "opened, that\n"
            "          carries vetting evidence for its CURRENT head, whose "
            "checks passed,\n          and that the forge itself calls mergeable."
        )
    else:
        print(
            "          Fleet merges NOTHING: no repository is named in "
            f"{auto_merge_conf_path()}\n"
            f"          (nor in ${AUTO_MERGE_ENV}). Every pull request above is "
            "yours to merge. Copy\n"
            f"          {AUTO_MERGE_CONF_DEFAULTS} to {AUTO_MERGE_CONF} and name "
            "your own; its\n          header owns the format and the five gates a "
            "merge still has to clear."
        )


# --- the run log: the half of it the queue already knows ---------------------
#
# `AGENTS.md` step 5 said "record the run in orchestration/runs/ as it happens".
# Two consecutive runs did not: one file was written only because its lead
# session was being migrated and would otherwise have lost everything it knew,
# the other was reconstructed from chat history at the end of the run after the
# operator asked what had gone wrong. An instruction two leads failed the same
# way is not a discipline problem, it is a tool gap — every other artefact in
# the loop (BRIEF.md, task.yaml, progress.jsonl, result.md) is scaffolded
# without anyone choosing to make it, and the run log was the one that was not.
#
# So the queue writes the half it knows and never touches the half it cannot:
#
#   ONE LOG PER TOPIC.  A topic is one unit of intent, which is what a run is,
#       and its slug and open date name the file — so every command finds the
#       same one with no "current run" pointer to set, drift or get wrong.
#   SCAFFOLDED AT `topic add`, from the tracked _TEMPLATE.md, because that is
#       where a run begins and the point is that nobody has to decide to.
#   REFRESHED BY THE LOOP'S OWN COMMANDS — `dispatch`, `collect`, `shepherd`
#       and the explicit `run` — so the facts arrive without being retyped and
#       without a daemon. They are REWRITTEN, never appended to: `collect` runs
#       many times over one run, and a line appended per pass is a timeline
#       nobody reads, which is this failure relocated rather than fixed. They
#       are a pure function of the records, so a refresh that changes nothing
#       writes nothing and says nothing.
#   INTO A FILE OF THEIR OWN, `<date>-<topic>.facts.md`, AND THE LOG IS THE
#       LEAD'S. The goal in its own words, the decisions, what went wrong, the
#       outcome — none of that can be generated from records. The facts once
#       sat in a fenced block inside the log, and rewriting the log to refresh
#       them lost the lead's prose three ways: an edit saved between the read
#       and the write of a `collect` the loop ran on its own clock, the opening
#       marker quoted in a sentence above the block, and the markers swapped.
#       So the log is created once, by a create that refuses to overwrite, and
#       nothing here writes it again — except to migrate an older log's exact
#       fence once (`refresh_run_log`).

RUNS_DIR = os.path.join("orchestration", "runs")
RUN_TEMPLATE = "_TEMPLATE.md"

# The fence a log written before the facts moved out carries, as a literal
# pair. Read only to MIGRATE such a log, and only when the pair is exact: see
# `legacy_fence`.
FACTS_BEGIN = "<!-- fleet:facts -->"
FACTS_END = "<!-- fleet:facts:end -->"

# What stands where the facts used to be, in a new log and a migrated one
# alike. `_TEMPLATE.md` holds FACTS_LINK_SLOT where it goes.
FACTS_LINK_SLOT = "<!-- fleet:facts-link -->"
FACTS_LINK = ("**Facts.** [`{name}`]({name}) — generated from the queue's records "
              "beside this file. This file is yours: fleet wrote it once and never writes it again.")

# A run is over when the queue has nothing left to do for it. `done` is not
# here: that task's pull request is open, which is the middle of a run.
RUN_CLOSED = ("landed", "abandoned", "stuck", "failed")


def runs_root() -> str:
    """Where run logs are written — FLEET_RUNS_DIR, as FLEET_QUEUE_DIR is.

    A harness pointing the queue at a throwaway directory has to be able to
    point the logs somewhere throwaway too, or every test run scaffolds
    into the operator's own orchestration/runs/.
    """
    return os.environ.get("FLEET_RUNS_DIR") or os.path.join(checkout_root(), RUNS_DIR)


def run_template_path() -> str:
    """The tracked form, anchored to the CHECKOUT the way policy_path() is."""
    return os.path.join(checkout_root(), RUNS_DIR, RUN_TEMPLATE)


def run_log_path(slug: str, topic: dict) -> str:
    """`<opened-date>-<topic>.md`, derived and never recorded.

    Deriving it means the file has no pointer that can go stale and no field
    two commands can disagree about. The date is the topic's, not today's, so
    a run refreshed on its third day still writes to the file it opened.
    """
    day = str(topic.get("created") or now())[:10]
    return os.path.join(runs_root(), f"{day}-{slug}.md")


def run_facts_path(log: str) -> str:
    """The facts file beside a run log: `<opened-date>-<topic>.facts.md`."""
    return log[: -len(".md")] + ".facts.md"


def cell(text) -> str:
    """One markdown table cell: no pipe, no newline, and never empty."""
    out = str(text if text not in (None, "") else "—").replace("|", "\\|")
    return " ".join(out.split())


def run_status(tasks: list) -> str:
    if not tasks:
        return "planning"
    return "done" if all(t.state in RUN_CLOSED for t in tasks) else "running"


def run_events(tasks: list) -> list:
    """The run's timeline, from the timestamps the records already carry.

    Four durable ones per task and the shepherd's outstanding fixer, which is
    not durable and is not meant to be — it is cleared when that pull request
    stops needing one, and this block says what the records say now.
    """
    out = []
    for t in tasks:
        out += _work_events(t)
        out += _end_events(t)
    return sorted(out, key=lambda e: e[0])


def _work_events(t: Task) -> list:
    """`run_events` for one task's dispatch, conclusion and landing."""
    out = []
    d = t.doc
    where = f" on `{d['host']}`" if d.get("host") else ""
    session = d.get("session") or (d.get("reaped") or {}).get("session")
    profile = d.get("profile") or "default"
    if d.get("dispatched_at"):
        out.append((d["dispatched_at"], f"dispatched `{t.id}`{where} to session "
                                        f"`{session}` on profile `{profile}`"))
    if d.get("concluded_at"):
        out.append(_concluded_event(t))
    landing = d.get("landing") or {}
    if landing.get("at") and landing.get("state") in LANDED_STATE:
        out.append((landing["at"], f"`{t.id}` {landing['state']} — {landing.get('detail', '')}"))
    return out


def _concluded_event(t: Task) -> tuple:
    """`run_events`' entry for the moment a task concluded."""
    d = t.doc
    check = (d.get("artifact_check") or {}).get("verdict") or ""
    said = f"`{t.id}` concluded `{d.get('outcome')}`"
    if d.get("artifact"):
        said += f" — {cell(artifact_text(t))}"
    return (d["concluded_at"], said + (f" [pipeline {check}]" if check else ""))


def _end_events(t: Task) -> list:
    """`run_events` for one task's abandonment, reaping and shepherd record."""
    out = []
    d = t.doc
    given_up = d.get("abandoned") or {}
    if given_up.get("at"):
        out.append((given_up["at"], f"`{t.id}` {ABANDON_HOW.get(abandon_how(t), abandon_how(t))} — "
                                     f"{cell(given_up.get('why'))}"))
    reaped = d.get("reaped") or {}
    if reaped.get("at"):
        verb = "kept" if reaped.get("how") == "kept" else "released"
        out.append((reaped["at"], f"{verb} `{t.id}`'s session "
                                  f"`{reaped.get('session')}` ({reaped.get('how')})"))
    shep = d.get("shepherd") or {}
    if shep.get("at"):
        said = (f"shepherd: `{t.id}`'s pull request is "
                f"`{shep.get('condition')}`")
        if shep.get("session"):
            said += f" — fixer `{shep['session']}`"
        elif shep.get("refused"):
            said += f" — no fixer sent: {shep['refused']}"
        out.append((shep["at"], said))
    return out


def run_facts(q: Queue, slug: str) -> str:
    """The facts file, rendered from records and from nothing else."""
    topic = q.topics.get(slug, {})
    tasks = q.by_topic().get(slug, [])
    profiles = sorted({t.doc.get("profile") or "default" for t in tasks})
    log = os.path.basename(run_log_path(slug, topic))

    lines = [
        f"# Run facts: `{log[:10]}` — `{slug}`",
        "",
        "<!-- Generated from the queue's records by `uv run fleet queue`, and",
        "     rewritten every time it runs. Write nothing in here: what you",
        f"     decided goes in {log}, which fleet never writes. -->",
        "",
        f"- **Topic.** `{slug}` — {topic.get('title', '')}",
        f"- **Prompt.** `{os.path.join(os.path.abspath(queue_root()), slug, 'PROMPT.md')}`",
        f"- **Opened.** {topic.get('created', '—')} · **Status.** {run_status(tasks)}"
        f" · **Profile(s).** {', '.join(f'`{p}`' for p in profiles) or '—'}",
        *([f"- **Requested by.** {asked}"] if (asked := requester(topic.get("requested_by"))) else []),
        "",
    ]

    if tasks:
        lines += _run_facts_tasks(q, topic, tasks)
    else:
        lines.append("No tasks yet.")

    events = run_events(tasks)
    if events:
        lines += ["", "### Timeline", ""]
        lines += [f"- `{at}` — {what}" for at, what in events]

    return "\n".join(lines) + "\n"


def _run_facts_tasks(q: Queue, topic: dict, tasks: list) -> list:
    """`run_facts`' task table, then what waited, who asked and what overlapped."""
    lines = [
        "| Task | Where it runs | Branch | State | Artifact |",
        "|---|---|---|---|---|",
    ]
    for t in tasks:
        d = t.doc
        state = "waiting" if t.state == "queued" and not q.is_ready(t) else t.state
        lines.append(
            f"| `{t.id}` — {cell(d.get('title'))} | `{cell(where_it_runs(t))}` "
            f"| `{cell(d.get('branch'))}` | {state} | {cell(artifact_text(t))} |"
        )
    held = sorted({(t.id, blocker_condition(bl) or bl.get("task") or "",
                    bool(blocker_condition(bl)), bl["kind"], bl["why"])
                   for t in tasks for bl in t.blockers})
    # The two things about ordering worth having in a run log, and the
    # reason the last one was typed by hand: what waited and on what
    # condition, and what went out together anyway despite touching one
    # file. The second is the queue's whole doctrine, so it is recorded
    # here rather than left to a lead remembering to mention it.
    #
    # A CONDITION IS NAMED AS ONE, in words rather than as a ref in
    # backticks: a reader of this log who cannot tell "waited for 01" from
    # "waited for somebody to log into Azure" has lost the fact that makes
    # the second one worth writing down.
    for tid, on, is_condition, kind, why in held:
        what = (f"a condition outside the queue — {on}" if is_condition
                else f"`{on}`")
        lines += ["", f"- **`{tid}` waits on {what}** — {kind}: {why}"]
    # Only a task asked for by somebody OTHER than its topic's requester:
    # the rest say what the topic line above already said.
    for t in tasks:
        by = requester(t.doc.get("requested_by"))
        if by and by != requester(topic.get("requested_by")):
            lines += ["", f"- **`{t.id}` was requested by {by}**"]
    for o in q.overlaps(tasks):
        refs = ", ".join(f"`{r.split('/')[-1]}`" for r in o["tasks"])
        lines += ["", f"- **Overlap on `{o['touches']}`** — {refs}. A risk that "
                      "was reported and accepted, never a reason to wait."]
    return lines


def legacy_fence(text: str) -> tuple[str, tuple[int, int] | None]:
    """("none" | "fence" | "malformed", the fence's line span) for one log.

    A fence MIGRATES only when it is exact: each marker exactly once in the
    whole file, each alone on its own line, the opening one first. Anything
    else — a marker quoted in a sentence, the pair swapped, one of them
    missing — is a log whose block cannot be told from the lead's prose by a
    string search, which is how splicing at the first marker deleted that
    prose. It is reported, and nothing guesses.
    """
    begins, ends = text.count(FACTS_BEGIN), text.count(FACTS_END)
    if not begins and not ends:
        return "none", None
    lines = text.splitlines()
    whole_b = [i for i, line in enumerate(lines) if line == FACTS_BEGIN]
    whole_e = [i for i, line in enumerate(lines) if line == FACTS_END]
    if begins == ends == len(whole_b) == len(whole_e) == 1 and whole_b[0] < whole_e[0]:
        return "fence", (whole_b[0], whole_e[0])
    return "malformed", None


def write_run_facts(path: str, text: str) -> bool:
    """Write the facts file if it changed. True when it did.

    Fleet is its only writer, but `collect` from the loop and a command of the
    lead's can refresh one topic at once, so the compare and the write are one
    step under its lock.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with fleet_platform.exclusive_lock(path + LOCK_SUFFIX, wait=RECORD_LOCK_WAIT):
        try:
            with open(path, encoding="utf-8") as fh:
                if fh.read() == text:
                    return False
        except FileNotFoundError:
            pass
        fleet_platform.write_record(path, text)
    return True


def refresh_run_log(q: Queue, slug: str, archived_too: bool = False) -> tuple[str, str]:
    """Write this topic's facts file, opening its log first if there is none.

    Returns (log path, what happened) — "" for nothing. THE LOG IS NEVER
    REWRITTEN. It is created once, from the template, by a create that fails
    rather than overwrites; after that, every word in it is the lead's. The
    facts go in the file beside it, which only fleet writes.

    A log written before the facts moved out carries them inside a fence. An
    exact one is MIGRATED once: the facts file is written, then the block is
    replaced by the link — only if the log still reads as it did when this
    began, so an edit saved meanwhile is kept and the next pass tries again.
    A fence that is not exact is left alone and reported, and the facts file
    beside it is still kept current.

    An ARCHIVED topic is skipped unless `archived_too` (`run --all`): its
    records stopped moving, and a loop pass that rewrote every old log in the
    checkout would be a migration nobody asked for.
    """
    topic = q.topics.get(slug, {})
    path = run_log_path(slug, topic)
    facts = run_facts_path(path)
    if topic.get("archived") and not archived_too:
        return path, ""
    verb = ""
    if os.path.exists(path):
        said = _migrate_run_log(q, slug, path, facts)
        if said is not None:
            return path, said
    else:
        # A topic that was ALREADY archived when this view loaded is REFRESHED
        # and never OPENED. `shepherd` has always read every topic and `collect`
        # now does too — see `reap` — and scaffolding a finished topic's history
        # would hand an operator who deleted one a fresh copy of it on the very
        # next pass, and a checkout that gained a FLEET_RUNS_DIR after the fact
        # one template per topic it has ever finished. The flag is read off the
        # load and `sweep_archives` writes it to disk without touching that, so
        # a topic that archived during THIS pass is still live here and opens
        # its log exactly as it always did.
        if topic.get("archived"):
            return path, ""
        opened, why = _scaffold_run_log(slug, path, facts)
        if why:
            return path, why
        if opened:
            verb = "opened"

    if write_run_facts(facts, run_facts(q, slug)) and not verb:
        verb = "facts updated"
    return path, verb


def _migrate_run_log(q: Queue, slug: str, path: str, facts: str) -> str | None:
    """Migrate an existing log's legacy fence, if it has one.

    What `refresh_run_log` returns when it stops here, and None when the log
    carries no fence and the refresh carries on.
    """
    with open(path, encoding="utf-8") as fh:
        old = fh.read()
    kind, span = legacy_fence(old)
    if kind == "malformed":
        # The facts file is fleet's own, so it stays current regardless.
        write_run_facts(facts, run_facts(q, slug))
        return ("left alone — its fleet:facts markers are malformed (each must "
                "appear once, alone on its line, opening one first); fix or "
                "remove them and run `fleet queue run` again")
    if kind == "fence":
        write_run_facts(facts, run_facts(q, slug))
        with open(path, encoding="utf-8") as fh:
            if fh.read() != old:
                return ("not migrated — it changed while its facts rendered; "
                        "the next pass tries again")
        lines = old.splitlines(keepends=True)
        first, last = span
        lines[first:last + 1] = [FACTS_LINK.format(name=os.path.basename(facts)) + "\n"]
        fleet_platform.write_record(path, "".join(lines))
        return "migrated"
    return None


def _scaffold_run_log(slug: str, path: str, facts: str) -> tuple[bool, str]:
    """Open a topic's log from the template: (created, why it could not be)."""
    try:
        with open(run_template_path(), encoding="utf-8") as fh:
            template = fh.read()
    except OSError as exc:
        return False, f"not scaffolded — {exc}"
    template = (template.replace("<YYYY-MM-DD>", os.path.basename(path)[:10])
                .replace("<slug>", slug)
                .replace(FACTS_LINK_SLOT, FACTS_LINK.format(name=os.path.basename(facts))))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return fleet_platform.create_record(path, template), ""


def refresh_run_logs(q: Queue, only: str | None = None, always: bool = False,
                     archived_too: bool = False) -> None:
    """Refresh every topic's log and say only what changed.

    Silence when nothing moved is the point: a loop that prints a line per
    command per topic is the noise this was supposed to remove.
    """
    for slug in sorted(q.topics):
        if only and slug != only:
            continue
        path, note = refresh_run_log(q, slug, archived_too)
        if note or always:
            print(f"    run log {note or 'unchanged'}: {path}")


def cmd_run(args) -> int:
    """The refresh, made explicit — for a topic older than this and for the path.

    `--all` reaches the archived topics too, which no other pass refreshes, and
    is how their logs are migrated.
    """
    q = Queue(queue_root(), scope="all" if args.all_topics else "live")
    if args.topic and args.topic not in q.topics:
        raise QueueError(f"no such topic: {args.topic}")
    if not q.topics:
        print("run: no topics, so no runs")
        return 0
    refresh_run_logs(q, only=args.topic, always=True, archived_too=args.all_topics)
    return 0


# --- did that message land, and has anything moved since? --------------------
#
# WHY THIS EXISTS. On 2026-09-09 the lead sent new scope to a parked worker
# with `thurbox-cli session send`. The CLI answered `sent: true, submitted:
# true`; ten minutes later the session read `state: done | hook: done | age(s):
# 3043` — a state reported BEFORE the message was sent. On that evidence the
# worker looked dead. It was not: it had taken the message, done the work and
# committed it. The lead found out by opening the worker's worktree and running
# `git log`, which is the third thing the orchestration model exists to avoid.
#
# The gap is not that thurbox lies. It is that the lead knows one thing nothing
# records — WHEN IT SENT — and without that instant there is nothing to compare
# a later observation against. `refuel` already reasons about staleness, but
# only about a stale `working`; a stale `done` after a send has no reader.
#
# So `send` writes the instant down, together with a BASELINE of the things a
# worker cannot fake, and every read-only view compares:
#
#   the branch head   the sha `refs/heads/<branch>` pointed at when the message
#                     went out. A linked worktree's commits update that ref in
#                     the SHARED object store, so the task's own `repo` answers
#                     for a branch it does not have checked out — no session to
#                     ask thurbox about, no worktree path to resolve, and
#                     nothing to go wrong when the session is already reaped.
#   the transitions   progress.jsonl, which `watch` folds thurbox's own event
#                     stream into. Compared on the EVENT's time and never on
#                     the fold's, so a `watch` run that catches up on three
#                     transitions from before the message is not read as three
#                     things the worker did after it.
#
# THREE RULES, and they are the whole design:
#
#   NO NEW PRODUCER   nothing here polls and nothing here runs in the
#                     background. The observations are files `watch` and the
#                     worker's own git already wrote; the comparison happens
#                     when the lead reads.
#   NEVER GUESS       "nothing has moved since" is a fact. "the worker is
#                     stuck" is not, and is never printed. A source that cannot
#                     be read is `not checked` — never a silent `still`.
#   WRITES NOTHING    no `state`, no `outcome`. `collect` stays the only thing
#                     that closes a task, exactly as `refuel` respects.


def branch_head(task: Task) -> tuple[str | None, str | None, str]:
    """(sha, committed_at, why-not) for the head of this task's branch.

    Read out of the task's own `repo` rather than the worker's worktree: they
    share one object store, so the checkout fleet already knows the path of
    answers for a branch it does not have checked out. That is what makes this
    degrade instead of erroring — a session that was reaped, or a worktree that
    was deleted, takes nothing away from the ref.
    """
    host = task.doc.get("host")
    if host:
        return None, None, f"runs on host {host}, whose git is there and not here"
    repo, branch = task.doc.get("repo"), task.doc.get("branch")
    if not repo or not branch:
        return None, None, "the record names no repo and branch"
    if not os.path.isdir(str(repo)):
        return None, None, f"{repo} is not a directory on this machine"
    out = git_out(str(repo), ["log", "-1", "--format=%H%x09%cI", str(branch), "--"]).strip()
    if not out:
        return None, None, f"git could not read `{branch}` in {repo}"
    sha, _, at = out.partition("\t")
    return sha, (at or None), ""


def send_baseline(task: Task) -> dict:
    """What the movable things looked like BEFORE a message goes out.

    Taken before the send and not after, because the gap between them is
    exactly where a fast worker's first commit would land — and baselining
    that commit would hide the movement this whole section exists to see.
    """
    sha, at, why = branch_head(task)
    return {"head": sha, "head_at": at, "head_note": why}


def record_send(
    task: Task, sid: str, text: str, delivered: bool, detail: str, baseline: dict
) -> dict:
    """The receipt. Appended, never replaced — a worker messaged four times is
    a fact about the task, and one that disappears if each send overwrites the
    last. The one other field it writes: a DELIVERED send marks the task
    `prompted`, since the session now has words from the lead, and the bare
    `prompt` sweep must not follow them with the original brief.
    """
    entry = {
        "at": now(),
        "session": sid,
        "text": text,
        "delivered": delivered,
        "baseline": baseline,
    }
    if detail:
        entry["detail"] = detail
    task.doc.setdefault("sends", []).append(entry)
    if delivered:
        task.doc["prompted"] = True
    task.save()
    return entry


def last_transition(task: Task) -> tuple[str | None, str]:
    """The newest transition in progress.jsonl, by the event's own time.

    `at` is when thurbox says the session changed state; `observed` is when
    `watch` folded it in. The first is what answers "has this worker moved",
    so a fold that happens to run after the message cannot manufacture
    movement that predates it. thurbox's epoch `at` comes back as ISO text,
    the spelling every other `at` this queue hands out has.
    """
    path = task.file(PROGRESS_FILE)
    if not os.path.exists(path):
        return None, ""
    try:
        rows = open(path, encoding="utf-8").read().splitlines()
    except OSError as exc:
        return None, f"progress.jsonl could not be read: {exc}"
    newest = None
    for row in rows:
        try:
            ev = json.loads(row)
        except ValueError:
            continue
        stamp = ev.get("at")
        when = record_time(stamp)
        if when and (newest is None or when > newest[0]):
            text = stamp if isinstance(stamp, str) else datetime.fromtimestamp(when, timezone.utc).isoformat()
            newest = (when, text)
    return (newest[1] if newest else None), ""


def movement_since(task: Task, entry: dict) -> list:
    """One row per source, each saying moved / still / not checked, and when.

    Both sources are LOCAL and free: a file this queue already writes and one
    ref read out of a checkout. Nothing here asks thurbox, which is deliberate
    — `hook_state_age_secs` is the reading that produced the wrong answer in
    the first place, and a per-task subprocess is not something a view the
    monitor polls every four seconds can afford.
    """
    sent = record_time(entry.get("at"))
    rows = []

    at, why = last_transition(task)
    if why:
        rows.append({"source": "transition", "status": "unchecked", "at": None, "why": why})
    elif at and record_time(at) > sent:
        rows.append({"source": "transition", "status": "moved", "at": at, "why": ""})
    else:
        rows.append({"source": "transition", "status": "still", "at": at, "why": ""})

    base = entry.get("baseline") or {}
    was = base.get("head")
    if not was:
        rows.append({
            "source": "commit",
            "status": "unchecked",
            "at": None,
            "why": base.get("head_note") or "no branch head was recorded with the message",
        })
        return rows
    sha, at, why = branch_head(task)
    if not sha:
        rows.append({"source": "commit", "status": "unchecked", "at": None, "why": why})
    elif sha != was:
        rows.append({"source": "commit", "status": "moved", "at": at, "why": ""})
    else:
        rows.append({"source": "commit", "status": "still", "at": at, "why": ""})
    return rows


MOVED_WORD = {"transition": "transitioned", "commit": "committed"}


def liveness(task: Task) -> dict | None:
    """"I sent that worker a message. Did it land, and has anything moved?"

    None when nothing ever sent one — which is the ordinary task and has to
    read as TODAY rather than as "no movement", because a question nobody
    asked has no answer and printing one under every row would bury the tasks
    that were actually messaged.
    """
    sends = task.doc.get("sends")
    if not isinstance(sends, list) or not sends:
        return None
    entry = sends[-1] if isinstance(sends[-1], dict) else {}
    ago = age_of(entry.get("at"))

    if not entry.get("delivered"):
        detail = entry.get("detail") or "no reason recorded"
        return {
            "at": entry.get("at"),
            "status": "undelivered",
            "movement": [],
            "line": f"messaged {ago} ago — NOT DELIVERED: {detail}",
        }

    # A concluded task answers this question with its result.md, so the send is
    # part of the record and not part of what is happening. `blocker_view`
    # calls the same thing `moot` for the same reason.
    if task.state in CONCLUDED_STATES:
        return {
            "at": entry.get("at"),
            "status": "moot",
            "movement": [],
            "line": f"was messaged {ago} ago; this task concluded",
        }

    rows = movement_since(task, entry)
    _word_movement(rows)

    moved = [r for r in rows if r["status"] == "moved"]
    still = [r for r in rows if r["status"] == "still"]
    if moved:
        status, tail = "moved", ", ".join(r["line"] for r in moved)
    elif still:
        # Named source by source rather than as one "no movement", so the
        # reader can see WHICH silences this is made of — a remote task's
        # unreadable git is not the same claim as a branch that has not moved.
        status = "still"
        tail = "; ".join(r["line"] for r in rows)
    else:
        status = "unknown"
        tail = "; ".join(r["line"] for r in rows)
    return {
        "at": entry.get("at"),
        "status": status,
        "movement": rows,
        "line": f"messaged {ago} ago · {tail}",
    }


def _word_movement(rows: list) -> None:
    """Give each of `liveness`'s movement rows its `line`, in place."""
    for row in rows:
        word = MOVED_WORD[row["source"]]
        if row["status"] == "moved":
            row["line"] = f"{word} {age_of(row['at'])} ago" if row["at"] else f"{word} since"
        elif row["status"] == "still":
            row["line"] = f"no {row['source']} since"
        else:
            row["line"] = f"{row['source']} not checked: {row['why']}"


# What every view prints under a liveness line that reports no movement, and
# the reason this whole section is a reading and not a verdict. A worker that
# has not answered yet and a worker that never got the message look the same
# from here, and the queue does not guess between them — `AGENTS.md` step 5 and
# the `thurbox-session` skill's session-state section draw that line for
# sessions, and this holds it for messages.
LIVENESS_CAVEAT = (
    "that is what was observed, not what the worker is doing: a message it has "
    "not\n                 answered yet reads exactly like one it never received."
)


def cmd_send(args) -> int:
    """Type one line into a worker's session, and record whether it landed.

    Typed, not mailed. thurbox's mailbox delivers only to a local Claude or
    Codex session it has proven; a worker on a host, or one thurbox has not
    proven yet, keeps the message in a mailbox it never reads, and `sends`
    would then record a delivery that was not one. A worker is meant to act on
    this now, so the interruption the lead is spared is the point here.
    """
    q = Queue(queue_root(), scope="all")
    task = q.get(args.ref)
    text = args.message
    if "\n" in text or "\r" in text:
        raise QueueError(
            "a message is ONE line: `session send` types it and presses Enter, so "
            "the\nsecond line fires the agent on the first and lands in a "
            "half-started turn.\nPut anything longer in a file and send a line "
            "pointing at it — the same shape\nevery brief already uses."
        )
    sid = task.doc.get("session")
    if not sid:
        raise QueueError(
            f"{task.ref} has no session recorded, so there is nobody to send to.\n"
            "`fleet queue dispatch` starts one; `fleet queue attach <ref> <uuid>` records "
            "one you\nspawned by hand."
        )

    baseline = send_baseline(task)
    ok, report = trust_and_send(sid, text, args.timeout)
    record_send(task, sid, text, ok, "" if ok else report, baseline)

    print(f"send: {task.ref} -> {sid}  {'delivered' if ok else 'NOT DELIVERED'}")
    if baseline["head"]:
        print(f"    baseline: {baseline['head'][:12]} on {task.doc.get('branch')}")
    else:
        print(f"    baseline: no branch head — {baseline['head_note']}")
    if ok:
        print(
            "    Recorded. `fleet queue list` and `fleet queue show` now answer whether "
            "anything\n    has moved since, without opening a worktree."
        )
    else:
        # The report says what WAS typed — a dialog's keys may have been — so
        # this line claims only what is always true: the message did not land.
        print(f"    the message was not delivered: {report}", file=sys.stderr)
    return 0 if ok else 1


# --- read-only views ---------------------------------------------------------


def where_it_runs(task: Task) -> str:
    """`repo` for a local task; `host:repo` for one that runs somewhere else.

    One string in every view, in the ssh spelling the operator already types,
    so a path that is not on this machine can never be read as one that is.
    """
    host = task.doc.get("host")
    return f"{host}:{task.doc['repo']}" if host else str(task.doc["repo"])


# --- live: the forge's answer now, beside the record's ----------------------
#
# A record is what the loop last SAW, and a "waiting on you" table built from
# records listed change requests that had already merged as still waiting —
# eleven times on one lead, three of them corrected by the operator. So
# `list --live` asks the forge again, through the same `get` shepherd's
# classification reads (state, checks, review, mergeable), plus the one thing
# `get` leaves out on purpose: the unresolved review threads, a separate call
# so `collect` never pays for it. It READS and writes nothing: the record
# catches up when `collect` runs, and saying where it lags is the point.


def live_summary(cr, threads=None) -> str:
    """One change request as the forge answers for it now, as one line.

    `fleet watch` prints the same words, so a watch line and a list line about
    one change request can never disagree about what they saw.
    """
    parts = [cr.state or "state not said"]
    if cr.draft:
        parts.append("draft")
    if cr.checks:
        counts: dict = {}
        for c in cr.checks:
            counts[c.verdict] = counts.get(c.verdict, 0) + 1
        parts.append("checks: " + ", ".join(
            f"{counts[v]} {v}" for v in ("failed", "cancelled", "pending", "passed") if counts.get(v)
        ))
    elif cr.state == "open":
        parts.append("no check reported")
    if cr.review_decision:
        parts.append(f"review: {cr.review_decision}")
    if cr.state == "open" and cr.mergeable:
        parts.append(cr.mergeable)
    if threads:
        parts.append(f"{threads} unresolved thread{'s' if threads != 1 else ''}")
    return " · ".join(parts)


def live_rows(task: Task) -> list[dict]:
    """Every change request this task's record names, read from the forge now.

    `{"name", "state", "line"}` per artifact: `state` is the forge's word, or
    `unreadable` — which is never a verdict, only that nobody could ask.
    """
    rows = []
    for entry in recorded_artifacts(task):
        url = str(entry.get("url") or "")
        if forge.change_url(url):
            rows.append(_live_row(url))
    return rows


def _live_row(url: str) -> dict:
    """One change request's `live_rows` row, read from its forge now."""
    which, ref = forge.for_url(url)
    if which is None:
        return {"name": url, "state": "unreadable", "line": f"could not be read — {ref}"}
    cr, why = which.get(ref)
    if cr is None:
        return {"name": f"{ref.repo.path}#{ref.number}", "state": "unreadable",
                "line": f"could not be read — {why}"}
    threads, twhy = which.threads(ref) if cr.state == "open" else (None, "")
    line = live_summary(cr, threads)
    if twhy:
        # Left out, a count nobody could read would read as none open.
        line += f" · threads not read: {twhy}"
    if cr.state == "merged":
        line = ("merged on the forge — the record has not caught up; "
                "`fleet queue collect` lands it")
    elif cr.state == "closed":
        line = "closed unmerged on the forge"
    return {"name": cr.name, "state": cr.state or "unreadable", "line": line}


def cmd_list(args) -> int:
    root = queue_root()
    # The first line answers "which queue am I looking at?" without being asked.
    # `fleet queue root` prints the same path, so the two can never disagree
    # silently about what they are showing.
    print(f"queue: {os.path.abspath(root)}")
    # Named explicitly, so a topic stays reachable BY NAME however it is
    # flagged: `list --topic <archived>` is a request for that topic, not a
    # request for the default view narrowed to it.
    scope = "live"
    if args.all_topics or args.topic:
        scope = "all"
    elif args.archived:
        scope = "archived"
    q = Queue(root, scope=scope)
    grouped = q.by_topic()
    if args.topic:
        grouped = {k: v for k, v in grouped.items() if k == args.topic}
    if not grouped:
        print("queue: empty")
        hidden(q)
        return 0
    live_read = [] if args.live else None
    if args.live and not forge.available():
        print(f"live: {forge.no_forge_reason()} — every row below is the record")
        live_read = None
    for topic, tasks in sorted(grouped.items()):
        _list_topic(q, topic, tasks, live_read)
    if live_read is not None:
        counts = {k: live_read.count(k) for k in ("merged", "closed", "open", "unreadable")}
        said = ", ".join(f"{n} {k}" for k, n in counts.items() if n)
        print(f"live: {len(live_read)} change request(s) read" + (f": {said}" if said else ""))
    hidden(q)
    return 0


def _list_topic(q: "Queue", topic: str, tasks: list, live_read: list | None) -> None:
    """One topic's heading in `list`, then each of its tasks."""
    meta = q.topics.get(topic, {})
    flag = "  [archived]" if meta.get("archived") else ""
    by = requester(meta.get("requested_by"))
    print(f"{topic} — {meta.get('title', '')}{f'  by {by}' if by else ''}{flag}")
    for t in tasks:
        _list_task(q, t, live_read)
    print()


def _list_task(q: "Queue", t: Task, live_read: list | None) -> None:

    """One task's row in `list`, its notes, and — with `--live` — its forge read."""
    mark = "waiting" if t.state == "queued" and not q.is_ready(t) else t.state
    extra = t.doc.get("artifact") or t.doc.get("session") or ""
    # The publish state, with the age of the look that produced it —
    # the same field the pane draws, so the two views cannot
    # disagree about what was last seen.
    pub = t.doc.get("publish") or {}
    if pub.get("state"):
        extra = f"{extra}  {pub['state']} {age_of(pub.get('at'))}".strip()
    if asker := requester(t.doc.get("requested_by")):
        extra = f"{extra}  by {asker}".strip()
    print(f"    {t.id:<34} {mark:<11} {where_it_runs(t)}  {extra}")
    # The row is one line and a record can contradict it; task_notes is
    # what says so, and it is the same list the pane renders.
    for note in task_notes(q, t):
        print(f"        {note}")
    # "I sent that worker a message — did it land, and has anything
    # moved since?" Drawn only on a task somebody actually messaged,
    # so a queue nobody has course-corrected looks exactly as it did.
    live = liveness(t)
    if live and live["status"] != "moot":
        print(f"        {live['line']}")
    if live_read is not None and t.state not in TERMINAL_STATES:
        for row in live_rows(t):
            live_read.append(row["state"])
            print(f"        live: {row['name']}  {row['line']}")


def hidden(q: Queue) -> None:
    """What this view did not show, said out loud.

    Every reader prints this line, in these words. A filter that silently
    dropped twenty-two topics would leave an operator reading a short queue as
    an idle one, which is a worse failure than the clutter archiving removes.
    """
    if q.archived_hidden:
        print(
            f"{len(q.archived_hidden)} archived topic(s) hidden — "
            "`list --archived` shows them, `list --all` shows both"
        )


def find_rows(q: "Queue", origin: str) -> list[dict]:
    """Live tasks of `origin`: [{"ref","state","session","host"}], sorted by ref.

    Live is a RECORDED session on a task that is not terminal and was not
    reaped. A record is read as it stands and no thurbox is asked, so "is that
    session still listed" stays the caller's question.
    """
    return [
        {"ref": t.ref, "state": t.state, "session": t.doc["session"], "host": t.doc.get("host")}
        for _ref, t in sorted(q.tasks.items())
        if t.doc.get("origin") == origin and t.doc.get("session")
        and t.state not in TERMINAL_STATES and not t.doc.get("reaped")
    ]


def cmd_find(args) -> int:
    print(json.dumps(find_rows(Queue(queue_root()), args.origin.strip())))
    return 0


def _show_extras(task: Task, d: dict) -> None:
    """`show`'s origin, reply, extra repositories, attachments and artifacts."""
    for key in ("origin", "reply"):
        if d.get(key):
            print(f"    {key + ':':<12} {d[key]}")
    for unit in task_repos(task)[1:]:
        print(f"    {'also:':<12} {unit['path']} off {unit['base']}")
    for extra in d.get("add_dirs") or []:
        print(f"    {'attached:':<12} {extra}")
    # A scalar prints as itself, which is every record written before a task
    # could span repositories and every single-repository task since. A plural
    # one prints a line per repository rather than a Python list.
    entries = recorded_artifacts(task)
    if len(entries) == 1:
        print(f"    {'artifact:':<12} {entries[0]['url']}")
    else:
        for entry in entries:
            print(f"    {'artifact:':<12} {entry['repo']}  {entry['url']}")


def _show_publish(task: Task, d: dict) -> None:
    """`show`'s publish method, target, artifact check and publish state."""
    method, how = task_publish(task)
    print(f"    {'publish:':<12} {method}{f' — {how}' if how else ''}")
    if d.get("target"):
        print(f"    {'target:':<12} {d['target']}")
    check = d.get("artifact_check") or {}
    if check.get("verdict"):
        print(f"    {'checked:':<12} {check['verdict']} — {check.get('detail', '')}")
    for row in check.get("repos") or []:
        print(f"    {'':<12}   {row.get('repo')}: {row.get('verdict')} — {row.get('detail', '')}")
    pub = d.get("publish") or {}
    if pub.get("state"):
        print(
            f"    {'published:':<12} {pub['state']} — {pub.get('detail', '')} "
            f"({pub.get('by', '')}, {age_of(pub.get('at'))} ago)"
        )


def _show_liveness(task: Task) -> None:
    """`show`'s account of a message sent to the worker, when one was."""
    live = liveness(task)
    if not live:
        return
    print(f"    {'messaged:':<12} {live['line']}")
    for row in live["movement"]:
        print(f"    {'':<12}   {row['line']}")
    if live["status"] in ("still", "unknown"):
        print(f"    {'':<12} {LIVENESS_CAVEAT}")


def _show_ends(task: Task, d: dict) -> None:
    """`show`'s landing, review, abandonment, refuels and reaping."""
    landing = d.get("landing") or {}
    if landing.get("state"):
        print(f"    {'landing:':<12} {landing['state']} — {landing.get('detail', '')}")
    review = d.get("review") or {}
    if review.get("closed"):
        print(
            f"    {'reviewed:':<12} closed at {review['closed']}"
            + (f" — {review['why']}" if review.get("why") else "")
        )
    given_up = d.get("abandoned") or {}
    if given_up.get("at"):
        forced = ", forced" if given_up.get("forced") else ""
        print(f"    {'abandoned:':<12} {ABANDON_HOW.get(abandon_how(task), abandon_how(task))} "
              f"at {given_up['at']} (was {given_up.get('was')}{forced}) — {given_up.get('why', '')}")
    refuels = d.get("refuels") or []
    if refuels:
        print(f"    {'refuelled:':<12} {len(refuels)} restart(s) of {REFUEL_CAP}, last "
              f"{refuels[-1].get('at', '')} — {refuels[-1].get('why', '')}")
    reaped = d.get("reaped") or {}
    if reaped.get("session"):
        print(
            f"    {'reaped:':<12} {reaped['session']} {reaped.get('how', '')} "
            f"at {reaped.get('at', '')}"
        )


def _show_shepherd(d: dict) -> None:
    """`show`'s last shepherd record, when there is one."""
    rec = d.get("shepherd")
    if rec:
        # A record does not always name a fixer, and `fixer None sent` was
        # this line's answer whenever it did not — for a merge, which has
        # never written a session, and now for a policy refusal, which is the
        # opposite claim to the one that sentence made.
        sent = f"fixer {rec['session']} sent" if rec.get("session") else "no fixer sent"
        print(f"    {'shepherd:':<12} {rec.get('condition')} — {sent} {rec.get('at')}")
        if rec.get("refused"):
            print(f"    {'':<12} {rec['refused']}")


def _show_files(task: Task) -> None:
    """`show`'s brief, progress and result files."""
    print(f"    {'brief:':<12} {task.file('BRIEF.md')}")
    progress = task.file(PROGRESS_FILE)
    if os.path.exists(progress):
        lines = open(progress, encoding="utf-8").read().splitlines()
        print(f"    {'progress:':<12} {len(lines)} transition(s), last: {lines[-1] if lines else '-'}")
    if os.path.exists(task.file(RESULT_FILE)):
        print(f"    {'result:':<12} {task.file('result.md')}")


def cmd_show(args) -> int:
    q = Queue(queue_root())
    task = q.get(args.ref)
    d = task.doc
    print(f"{task.ref} — {d['title']}")
    for key in ("state", "repo", "host", "branch", "base", "agent", "profile", "session",
                "prompted", "outcome"):
        print(f"    {key + ':':<12} {d.get(key)}")
    print(f"    {'by:':<12} {requester(d.get('requested_by')) or 'not recorded'}")
    _show_extras(task, d)
    remote = d.get("remote") or {}
    if remote.get("worktree"):
        # Named in full because it is the only place the worker's actual
        # filesystem appears: the brief it reads and the result that closes
        # this task are both files on that machine.
        print(f"    {'remote:':<12} {remote.get('destination')}:{remote['worktree']}")
    _show_publish(task, d)
    _show_liveness(task)
    _show_ends(task, d)
    if task.touches:
        print(f"    {'touches:':<12} {', '.join(task.touches)}")
    _show_shepherd(d)
    conflict = state_conflict(task)
    if conflict:
        print(f"    {'conflict:':<12} {conflict}")
    gap = dispatch_gap(q, task)
    if gap:
        print(f"    {'dispatch:':<12} {gap}")
    # Every blocker, moot and cleared ones included: this command is the record
    # itself, and the record keeps what a status line has stopped showing.
    for b in task.blockers:
        print(f"    blocked_by:  {blocker_view(q, task, b)['line']}")
    _show_files(task)
    return 0


def cmd_check(args) -> int:
    root = os.path.abspath(queue_root())
    if not os.path.isdir(root):
        print(f"queue check: ok — {root} not created yet, nothing to validate")
        return 0

    q, problems = record_problems(root)
    for line in problems:
        print(f"    {line}", file=sys.stderr)
    if problems:
        print(f"queue check: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"queue check: ok — {len(q.topics)} topic(s), {len(q.tasks)} task(s) in {root}")
    return 0


def _topic_problems(q: "Queue", slug: str, meta: dict) -> list:
    """What is wrong with one topic's record, for `record_problems`."""
    problems = []
    at = meta.get("archived")
    if at is not None and not isinstance(at, str):
        problems.append(f"{slug}: archived {at!r} is not a timestamp")
        return problems
    # An archived topic holding a live task is the one way this flag can
    # be actively harmful: it is work nothing draws. Nothing fleet does
    # produces one — `add` clears the flag — but a hand-edited topic.yaml
    # can, and this is the check that says so out loud.
    held = unfinished(q.by_topic().get(slug) or []) if at else None
    if held:
        problems.append(
            f"{slug}: archived while {held.ref} is `{held.state}` — "
            "run `fleet queue unarchive` on it"
        )
    why = requester_problem(meta.get("requested_by"))
    if why:
        problems.append(f"{slug}: {why}")
    return problems


def _publish_problems(ref: str, d: dict) -> list:
    """What is wrong with one task's publish, target and how."""
    problems = []
    pub = d.get("publish") or {}
    # Through publish_method(), like every other reader: a record older
    # than the rename is never rewritten, so refusing its word here would
    # fail this check for good.
    if "method" in pub and publish_method(pub["method"]) not in PUBLISH_METHODS:
        problems.append(
            f"{ref}: publish method {pub['method']!r} is not one of "
            + ", ".join(sorted(PUBLISH_METHODS))
        )
    target = d.get("target")
    if target is not None and not (
        isinstance(target, str) and forge.TARGET_URL_RE.match(target)
    ):
        problems.append(f"{ref}: target {target!r} is not a change request or issue URL")
    if publish_method(pub.get("method")) == "note" and not target:
        problems.append(f"{ref}: a `note` task names no target to check its note against")
    # The ONLY thing ever asked of `how`. It names a tool fleet does not
    # know, so "it is text" is the whole contract — anything more is fleet
    # deciding which tools exist.
    if pub.get("how") is not None and not isinstance(pub["how"], str):
        problems.append(f"{ref}: publish how {pub['how']!r} is not text")
    return problems


def _blocker_problems(q: "Queue", ref: str, t: "Task") -> list:
    """What is wrong with one task's blockers."""
    problems = []
    for b in t.blockers:
        problems += _one_blocker_problems(q, ref, b)
    return problems


def _one_blocker_problems(q: "Queue", ref: str, b: dict) -> list:
    """What is wrong with one blocker entry."""
    problems = []
    # The two forms are validated against their own closed sets, and a
    # condition is checked for none of the three things that are only
    # true of a task: there is no upstream record to find.
    cond = blocker_condition(b)
    kinds = CONDITION_KINDS if cond else BLOCKER_KINDS
    if b.get("kind") not in kinds:
        problems.append(f"{ref}: blocker kind {b.get('kind')!r} is not a real one")
    if not (b.get("why") or "").strip():
        problems.append(
            f"{ref}: blocker on {cond or b.get('task')} has no reason"
        )
    if cond and b.get("task"):
        problems.append(
            f"{ref}: blocker names both a task and a condition; it is one or the other"
        )
    if not cond and b.get("task") not in q.tasks:
        problems.append(f"{ref}: blocker names {b.get('task')}, which does not exist")
    return problems


def _placement_problems(ref: str, t: "Task") -> list:
    """What is wrong with where one task runs: its brief, host, extras, artifact."""
    d = t.doc
    problems = []
    if not os.path.exists(t.file(BRIEF_FILE)):
        problems.append(f"{ref}: no BRIEF.md")
    # The host name only — never whether that host exists. A queue is read
    # on machines that are not the one it dispatches from, and a check that
    # asked thurbox would report a perfectly good record as broken there.
    if d.get("host") is not None and not isinstance(d.get("host"), str):
        problems.append(f"{ref}: host {d.get('host')!r} is not a name")
    if (d.get("remote") or {}) and not d.get("host"):
        problems.append(f"{ref}: carries a remote worktree but names no host")
    for key in ("add_dirs", "add_repos"):
        extra = d.get(key)
        if extra is not None and not (
            isinstance(extra, list) and all(isinstance(x, str) and x for x in extra)
        ):
            problems.append(f"{ref}: {key} {extra!r} is not a list of paths")
    problems += _artifact_shape_problems(ref, d.get("artifact"))
    return problems


def _artifact_shape_problems(ref: str, art) -> list:
    """What is wrong with the shape of a task's recorded artifact."""
    # Both shapes are valid, so the check is that it IS one of them: a
    # plural entry that names no url is a record nothing can verify, and a
    # scalar is whatever a worker reported, which `collect` judges and this
    # does not second-guess.
    if isinstance(art, list) and not all(
        isinstance(x, dict) and isinstance(x.get("repo"), str) for x in art
    ):
        return [f"{ref}: artifact {art!r} is a list but not one of {{repo, url}} mappings"]
    return []


def _task_problems(q: "Queue", ref: str, t: "Task") -> list:
    """What is wrong with one task's record, for `record_problems`."""
    d = t.doc
    problems = []
    for key in ("id", "topic", "state"):
        if not d.get(key):
            problems.append(f"{ref}: missing {key}")
    problems += [f"{ref}: {why}" for why in spawn_field_problems(d)]
    problems += _publish_problems(ref, d)
    why = requester_problem(d.get("requested_by"))
    if why:
        problems.append(f"{ref}: {why}")
    for key in ("origin", "reply"):
        if d.get(key) is not None and not (isinstance(d[key], str) and d[key].strip()):
            problems.append(f"{ref}: {key} {d[key]!r} is not text")
    if d.get("session") is not None and not isinstance(d["session"], str):
        problems.append(f"{ref}: session {d['session']!r} is not text")
    if d.get("prompted") is not None and not isinstance(d["prompted"], bool):
        problems.append(f"{ref}: prompted {d['prompted']!r} is not true or false")
    if d.get("state") not in STATES:
        problems.append(f"{ref}: state {d.get('state')!r} is not one of {', '.join(STATES)}")
    if d.get("id") != t.id:
        problems.append(f"{ref}: id {d.get('id')!r} disagrees with its directory")
    problems += _blocker_problems(q, ref, t)
    problems += _placement_problems(ref, t)
    return problems


def record_problems(root: str) -> tuple["Queue", list]:
    """The queue under `root`, and every way one of its records is wrong.

    `fleet queue check` prints these, and `fleet status --records` reports them —
    which is where the OPERATOR'S records are validated. The code gate
    validates none: they are live data in one checkout, and a gate that read
    them gave one commit a different verdict there than on CI.
    """
    # `all`: a record does not stop being a record because its topic left the
    # default view, and a check that only validated what is on screen would go
    # quiet about exactly the records nobody is looking at.
    q = Queue(root, scope="all")
    problems = []
    for slug, meta in sorted(q.topics.items()):
        problems += _topic_problems(q, slug, meta)
    for ref, t in sorted(q.tasks.items()):
        problems += _task_problems(q, ref, t)

    cycle = find_cycle(q)
    if cycle:
        problems.append("blocker cycle: " + " -> ".join(cycle))
    return q, problems


def cmd_root(args) -> int:
    """The resolved queue root, absolute, and nothing else — for scripts.

    `--foreign` asks the other half of the same question: is THIS checkout
    provably not the control plane? It prints the control plane's path and
    exits 0 when it is, and prints nothing and exits 1 otherwise — so a caller
    can branch on the exit status without parsing anything. `fleet
    reconcile` is why it exists: that loop runs `collect`, which closes
    tasks and reaps sessions, and a worker running it in its own worktree
    would reap its own session. Silence covers both "this IS the control
    plane" and "nothing here can tell", exactly as foreign_checkout() does,
    because a guard that fires when it cannot tell is a guard that makes a
    fleet without the thurbox extension unusable.
    """
    if getattr(args, "foreign", False):
        owner = foreign_checkout()
        if not owner:
            return 1
        print(owner)
        return 0
    print(os.path.abspath(queue_root()))
    return 0


# --- entry point -------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fleet queue", add_help=True)
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("topic", help="open a topic from a prompt")
    tsub = t.add_subparsers(dest="topic_cmd", required=True)
    ta = tsub.add_parser("add")
    ta.add_argument("slug")
    ta.add_argument("--title")
    ta.add_argument("--prompt")
    ta.add_argument("--prompt-file")
    ta.add_argument(
        "--by",
        metavar="PERSON",
        help="who asked for this, as free text (a name, a chat handle) — recorded "
        "and shown, never acted on; every task added to the topic inherits it",
    )
    ta.set_defaults(func=cmd_topic_add, creates=True)

    a = sub.add_parser("add", help="add a task to a topic")
    a.add_argument("topic")
    a.add_argument("slug")
    a.add_argument("--title")
    a.add_argument(
        "--by",
        metavar="PERSON",
        help="who asked for this task, when it is not who asked for its topic; "
        "with none the task records its topic's requester, resolved now",
    )
    a.add_argument(
        "--repo",
        required=True,
        help="the repository the worker branches from. WITH --host this is a path "
        "on THAT HOST: nothing local validates it, and dispatch asks the host "
        "whether it is there",
    )
    a.add_argument(
        "--host",
        help="run this task on a remote thurbox host (a name from hosts.toml). The "
        "agent, its tmux window and its worktree live there; --repo is then a path "
        "on that machine. Omit it and everything runs here, unchanged",
    )
    a.add_argument("--branch", required=True)
    a.add_argument("--base", default="main")
    a.add_argument("--profile", default="default")
    a.add_argument(
        "--agent",
        help="the agent to launch (default: orchestration/agent.conf, else thurbox's own)",
    )
    a.add_argument(
        "--publish",
        # argparse applies `type` before `choices`, so the retired word is
        # folded into its shape first and never reaches the record.
        type=publish_method,
        choices=sorted(PUBLISH_METHODS),
        help="what this task must PRODUCE: `pr` or `attested`, a pull request "
        "from its branch; `push`, a commit on its base; `note`, a review or "
        "comment on its --target; `served`, a document served to a reader, "
        "whose session is kept to answer them until `fleet queue reviewed`; "
        "`none`, nothing fleet can check and nobody waiting. Defaults to "
        "orchestration/publish.conf, and to `pr` when there is none",
    )
    a.add_argument(
        "--target",
        help="the change request or issue this task works ON, by URL, when its "
        "own branch does not open it: the pull request a `note` reviews, or a "
        "contributor's pull request a `pr` task pushes to. A `note` task needs "
        "one; a `pr` or `attested` one is then verified against it, not its branch",
    )
    a.add_argument(
        "--how",
        help="the tool the worker should publish with, in your own words "
        "(\"run `/publish`\", \"use `make release`\"). Free text: it is rendered "
        "into the brief and nothing ever parses it, which is what lets it name "
        "a tool fleet knows nothing about",
    )
    a.add_argument(
        "--origin",
        help="who owns this task's conversation, as an opaque string "
        "(`<kind>:<where>`): recorded and shown, never parsed. Default: the "
        "calling thurbox session, as `thurbox:<session id>`. `owners.conf` "
        "routes a matching origin to a session or a relay, and `find "
        "--origin` lists its live tasks",
    )
    a.add_argument(
        "--reply",
        help="the command a worker runs to speak to that origin, as free text "
        "the worker appends its message to (or `--file PATH`). Rendered into the "
        "brief and never run or parsed here. Default, for a session origin: "
        "that session's thurbox mailbox",
    )
    a.add_argument(
        "--add-dir",
        action="append",
        metavar="PATH",
        help="attach another directory to the worker's session, as it is: no "
        "worktree, no branch, nothing to publish. Repeatable. It is how a "
        "worker reads a sibling repository or a docs tree while it works; a "
        "second repository it must COMMIT in is --add-repo",
    )
    a.add_argument(
        "--add-repo",
        action="append",
        metavar="PATH[@BASE]",
        help="a second repository this task also commits in: its own worktree, "
        "on the SAME --branch, off BASE or off --base. Repeatable. The task "
        "then leaves one artifact PER REPOSITORY, each verified on its own, "
        "and lands only when every one of them has. A directory the worker "
        "only reads is --add-dir",
    )
    a.add_argument("--touches", help="comma-separated paths this task expects to change")
    a.add_argument(
        "--brief-file",
        help="the brief's body, refused unless it carries all four headings: "
        + ", ".join(f"`## {h}`" for h in BRIEF_SECTIONS),
    )
    a.add_argument("--number", help="two-digit ordinal; the next free one by default")
    a.set_defaults(func=cmd_add, creates=True)

    b = sub.add_parser(
        "block",
        help="record why a task must wait — for another task, or for a "
        "condition outside the queue",
    )
    b.add_argument("ref")
    # EXACTLY ONE OF THE TWO, enforced here rather than in cmd_block, so the
    # refusal for "both" and for "neither" is argparse's and reads the same as
    # every other missing argument.
    what = b.add_mutually_exclusive_group(required=True)
    what.add_argument("--on", help="the task this one waits for")
    what.add_argument(
        "--condition",
        help="what outside the queue holds this task — a credential, an "
        "approval, a window, a machine. Nothing clears one but `--clear` "
        "naming it back",
    )
    # `choices` lists them in --help; `type` is what answers a wrong one, so
    # the explanation survives instead of argparse's bare "invalid choice".
    # The list is the UNION because argparse has not read `--on` or
    # `--condition` yet; `cmd_block` refuses one from the wrong set.
    b.add_argument(
        "--kind",
        type=blocker_kind,
        choices=sorted(set(BLOCKER_KINDS) | set(CONDITION_KINDS)),
        # `choices` is the union and cannot say which form each belongs to, so
        # the help text does. A lead reading nine values with no split would
        # have to guess the same way the one flat list of four used to make
        # them guess.
        help="the kind of condition that makes this task wait. With --on: "
        + ", ".join(BLOCKER_KINDS)
        + ". With --condition: "
        + ", ".join(CONDITION_KINDS)
        + ". Overlapping files are on neither list — record those with "
        "`add --touches`",
    )
    b.add_argument("--why", help="the concrete reason, in your own words")
    b.add_argument(
        "--clear",
        action="store_true",
        help="remove the blocker naming --on, or the one naming --condition. "
        "A task can carry more than one, so clearing still has to say which",
    )
    b.set_defaults(func=cmd_block)

    pl = sub.add_parser("plan", help="what dispatches now, what waits, and why")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_plan)

    dc = sub.add_parser("declare", help="a worker's own word: paused until a time, or needs input")
    dc.add_argument("ref")
    dc.add_argument("state", choices=DECLARE_STATES)
    dc.add_argument("--until", help="paused only: an ISO-8601 time with an offset, or 45m / 2h; "
                    f"at most {PAUSE_CAP_SECS // 3600}h away")
    dc.add_argument("--why", required=True, help="what you are waiting on, or the decision you need")
    dc.set_defaults(func=cmd_declare)

    d = sub.add_parser("dispatch", help="launch every ready task at once")
    d.add_argument(
        "ref",
        nargs="*",
        help="the tasks to launch. NO REF IS THE NORM and sends the whole ready "
        "set at once; naming refs holds the rest back for this run only and "
        "records nothing, so use it when the operator has not authorized a task "
        "yet — never to drip-feed a queue, which is slower than no queue",
    )
    d.add_argument("--origin", metavar="ID",
                   help="send only the ready tasks recording exactly this origin "
                   "(as `find --origin` compares it); refused beside refs")
    d.add_argument("--topic", metavar="SLUG",
                   help="send only this topic's ready tasks; refused beside refs")
    d.add_argument("--dry-run", action="store_true")
    d.set_defaults(func=cmd_dispatch)

    pr = sub.add_parser("prompt", help="answer the trust dialog and send the brief")
    pr.add_argument("ref", nargs="?", help="one task; every unprompted one by default")
    pr.add_argument("--timeout", type=int, default=20)
    pr.set_defaults(func=cmd_prompt)

    sd = sub.add_parser(
        "send", help="send one line to a task's worker, and record that you did"
    )
    sd.add_argument("ref")
    sd.add_argument("message", help="ONE line; longer text goes in a file you point at")
    sd.add_argument("--timeout", type=int, default=20)
    sd.set_defaults(func=cmd_send)

    at = sub.add_parser("attach", help="record the session doing a task")
    at.add_argument("ref")
    at.add_argument("session")
    prompt_state = at.add_mutually_exclusive_group()
    prompt_state.add_argument(
        "--prompted",
        action="store_true",
        help="record it already briefed, so `fleet queue prompt` does not re-brief it",
    )
    prompt_state.add_argument(
        "--unprompted",
        action="store_true",
        help="record it unprompted whatever it reports, so `fleet queue prompt` "
        "sends it the brief; by default only working, blocked or done "
        "is recorded prompted",
    )
    at.set_defaults(func=cmd_attach)

    w = sub.add_parser("watch", help="fold thurbox's event stream into the records")
    w.add_argument("--for-secs", type=int, default=30)
    w.set_defaults(func=cmd_watch)

    c = sub.add_parser("collect", help="read the results workers wrote")
    c.add_argument(
        "--no-reap",
        action="store_true",
        help="record what landed but leave every session alone",
    )
    c.add_argument(
        "--allow-unverified",
        nargs="+",
        action="extend",
        default=[],
        metavar="REF",
        help="close exactly these tasks although their artifact does not prove "
        "they published, after you have read each artifact and judged it good "
        "anyway; every other held task stays held",
    )
    c.set_defaults(func=cmd_collect)

    rp = sub.add_parser("reap", help="release the sessions of tasks whose work has landed")
    rp.add_argument(
        "--dry-run",
        action="store_true",
        help="say what would land and what would be released, and write nothing",
    )
    rp.set_defaults(func=cmd_reap)

    rv = sub.add_parser(
        "reviewed",
        help="record that the reader is done with a `served` task's document",
    )
    rv.add_argument("ref")
    rv.add_argument("--why", help="what the reader said, or how you know they are done")
    rv.set_defaults(func=cmd_reviewed)

    ab = sub.add_parser("abandon", help="retire tasks that will never run")
    ab.add_argument("refs", nargs="*", help="the tasks to abandon")
    ab.add_argument("--topic", help="every task in this topic that has not landed or been abandoned")
    ab.add_argument("--why", help="why it will never run; recorded on the task")
    ab.add_argument(
        "--force",
        action="store_true",
        help="abandon a dispatched task even though its session is still listed",
    )
    ab.set_defaults(func=cmd_abandon)

    rf = sub.add_parser("refuel", help="restart the workers that ran out of quota")
    rf.add_argument("ref", nargs="?",
                    help="one task; every recorded session by default")
    rf.add_argument(
        "--dry-run",
        action="store_true",
        help="name what would be restarted, and write nothing",
    )
    rf.set_defaults(func=cmd_refuel)

    sh = sub.add_parser("shepherd", help="inspect the PRs this queue produced and act")
    sh.add_argument("--dry-run", action="store_true",
                    help="say exactly what would be dispatched and merged, and change nothing")
    sh.add_argument("--json", action="store_true", help="the machine-readable seam")
    sh.add_argument("--topic", help="narrow which repos to derive to this topic's tasks — "
                    "every open PR in those repos is still shepherded, not just theirs")
    sh.add_argument("--ref", help="narrow which repo to derive to this task's — every open "
                    "PR in that repo is still shepherded, not just this one's")
    sh.add_argument("--no-merge", action="store_true",
                    help="classify and dispatch as usual, but merge nothing")
    sh.add_argument("--force", action="store_true",
                    help="dispatch again for a condition a fixer is already out for")
    sh.set_defaults(func=cmd_shepherd)

    rv = sub.add_parser("review", help="record an independent current-head assessment")
    rvsub = rv.add_subparsers(dest="review_command", required=True)
    rr = rvsub.add_parser("record", help="configured reviewer attests its linked forge summary")
    rr.add_argument("url")
    rr.add_argument("--head", required=True)
    rr.add_argument("--score", required=True, type=int)
    rr.add_argument("--findings", required=True, type=int)
    rr.add_argument("--evidence", required=True)
    rr.set_defaults(func=cmd_review_record)

    ps = sub.add_parser("prs", help="every watched session's PR state, as the last shepherd pass "
                        "recorded it; reads only that file")
    ps.add_argument("--json", action="store_true",
                    help="the store itself; its schema is shepherd_store.py's docstring")
    ps.add_argument("--records", action="store_true",
                    help="one tab-separated line per session and PR, each PR with its dot; the board's wire")
    ps.set_defaults(func=cmd_prs)

    rn = sub.add_parser("run", help="refresh the run log this topic writes into")
    rn.add_argument("topic", nargs="?", help="one topic; every live one by default")
    rn.add_argument("--all", action="store_true", dest="all_topics",
                    help="archived topics as well, which migrates their logs")
    rn.set_defaults(func=cmd_run)

    li = sub.add_parser("list", help="one line per task, grouped by topic")
    li.add_argument("--topic", help="one topic, archived or not")
    li.add_argument("--archived", action="store_true",
                    help="only the archived topics, which the default view hides")
    li.add_argument("--all", action="store_true", dest="all_topics",
                    help="archived topics as well as live ones")
    li.add_argument("--live", action="store_true",
                    help="re-read every open task's change requests from the forge — state, "
                    "checks, review, threads — and say where the record lags")
    li.set_defaults(func=cmd_list)

    ar = sub.add_parser("archive", help="hide a finished topic from the default views")
    ar.add_argument("topic")
    ar.set_defaults(func=cmd_archive)

    ua = sub.add_parser("unarchive", help="put an archived topic back in every view")
    ua.add_argument("topic")
    ua.set_defaults(func=cmd_unarchive)

    s = sub.add_parser("show", help="one task's whole record")
    s.add_argument("ref")
    s.set_defaults(func=cmd_show)

    fi = sub.add_parser(
        "find",
        help="the live tasks of one origin, as JSON — read-only, for another program",
    )
    fi.add_argument("--origin", required=True, help="the origin string, matched exactly")
    fi.add_argument("--json", action="store_true", help="accepted; the answer is always JSON")
    fi.set_defaults(func=cmd_find)

    ch = sub.add_parser("check", help="validate every record")
    ch.set_defaults(func=cmd_check)

    rt = sub.add_parser("root", help="the resolved queue directory, absolute")
    rt.add_argument(
        "--foreign",
        action="store_true",
        help="print the control plane's checkout, and exit 0, only when THIS "
        "checkout is provably not it; silent and 1 otherwise",
    )
    rt.set_defaults(func=cmd_root)

    # `creates` splits the guard: the two commands that can bring a SECOND
    # queue into existence refuse outside the control plane; everything else
    # warns and carries on. See guard_creating().
    p.set_defaults(creates=False)
    return p


def main(argv: list) -> int:
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "creates", False):
            guard_creating()
        else:
            warn_foreign(queue_root())
        return args.func(args)
    except QueueError as exc:
        print(f"queue: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
