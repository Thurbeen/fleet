#!/usr/bin/env python3
"""The fleet task queue: durable records on disk, ordered by recorded blockers.

Called through scripts/queue.sh, which owns the usage text. This file owns the
model. Three ideas, and the whole thing follows from them:

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
recipient: an arriving worker message injects into the lead's terminal and
interrupts whoever is talking to it. A stream that is read and a file that is
read interrupt nobody.

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
copied to the host before the worker is prompted, and the result is fetched back
into this same result.md before it is read. See the remote-hosts section below
for why the alternative (a worker reporting through `message send`) was refused.
A task with no host is untouched by any of it.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import tomllib
from datetime import datetime, timezone

import yaml


def _load_forge():
    """Load scripts/lib/forge.py beside this file, under a name of its own.

    Not a plain `import forge`: this file is loaded three ways — as `queue` off
    `scripts/lib` on sys.path, and by `fleet_status.py` and the pane harness
    through importlib with no path change at all — and only the first of those
    would find a sibling module. Keyed in sys.modules so every one of them
    shares ONE registry, and therefore one answer about which forges are
    configured.
    """
    if "fleet_forge" in sys.modules:
        return sys.modules["fleet_forge"]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forge.py")
    spec = importlib.util.spec_from_file_location("fleet_forge", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fleet_forge"] = mod
    spec.loader.exec_module(mod)
    return mod


# WHICH FORGE. Everything fleet knows about a change request — a pull request
# on GitHub, a merge request on GitLab — it asks this module for. Nothing in
# this file runs a forge CLI or builds a forge URL; the one exception is
# FORGE_PROBE_TEMPLATE's ssh check below, which is a different coupling (git
# hosting, not the forge API) and reads the repository's own `origin` rather
# than naming a forge.
forge = _load_forge()

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
# ready and `notify_lead.py` correctly woke the lead to dispatch it. The only
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

# Concluded AND not on main. `sweep_landings` promotes a `done` task and only a
# `done` task, so `landed` is unreachable from any of these: a blocker naming
# one can never clear, and calling it "held by" describes a wait with no end.
UNLANDABLE_STATES = ("stuck", "failed", "abandoned")

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
        elif stripped.startswith("```") or stripped.startswith("~~~"):
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
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
        elif stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
        elif line.startswith("## "):
            heading = line[3:].strip()
            current = heading if heading in BRIEF_SECTIONS else None
            if current:
                out.setdefault(current, [])
            continue
        if current is not None:
            out.setdefault(current, []).append(line)
    return {h: "\n".join(v).strip() for h, v in out.items()}


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
        with open(path) as fh:
            text = fh.read()
    except OSError:
        return "never written"
    missing = unfilled_sections(text)
    if missing:
        return "still the scaffold's own text under " + ", ".join(missing)
    return ""


# HOW A TASK PUBLISHES, as three words about the ARTIFACT it leaves behind —
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
# pull request or a commit on the base branch, so these three words cover every
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
PUBLISH_DEFAULT = "pr"

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
            "an attestation for the commit that would merge"
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
        "artifact": "that commit's URL",
        "proof": "the commit is an ancestor of the base branch on `origin`",
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
    path answers "which checkout is this queue.sh", which is the one that has a
    single right answer no matter where it is invoked from. A git worktree of
    this repo is a different checkout by this rule, and that is correct — it
    has its own orchestration/queue/.
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- the session glyph, which is one setting and two names --------------------
#
# thurbox has no per-session icon field, so a session that wears a mark wears it
# in its NAME. `orchestration/session-glyphs.example.conf` is the one place the
# mark is chosen, `session-glyphs.conf` beside it is the operator's gitignored
# override, and the two readers are this file (every worker fleet spawns) and
# `scripts/install-extension.sh` (the lead). No line of CODE here spells a
# glyph — one would be a second copy of a setting this file does not own — and
# the comment below spells one only to do the arithmetic it is about.
#
# `GLYPHS=off` leaves a worker's name exactly what it was before glyphs existed,
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
    `FLEET_QUEUE_DIR` relocates queue state — so a selftest can dispatch a real
    task without inheriting whatever the developer's own gitignored
    session-glyphs.conf says.
    """
    root = root or os.environ.get("FLEET_GLYPH_ROOT") or checkout_root()
    return read_kv_conf(conf_path(GLYPH_CONF, GLYPH_CONF_DEFAULTS, root))


# --- the operator's settings files, which are all read the same way ----------
#
# One parse for every `orchestration/*.conf`: the operator's copy when it is
# there, the tracked `*.example.conf` beside it when it is not. Read as DATA —
# `KEY=value`, no quoting, no continuation — and never executed. A setting that
# can run is a different kind of file.
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
    """`KEY=value` lines, as data. An unreadable file is no settings at all."""
    conf: dict[str, str] = {}
    try:
        with open(path) as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                conf[key.strip()] = value.strip()
    except OSError:
        return {}
    return conf


PUBLISH_CONF = "orchestration/publish.conf"
PUBLISH_CONF_DEFAULTS = "orchestration/publish.example.conf"
AGENT_CONF = "orchestration/agent.conf"
AGENT_CONF_DEFAULTS = "orchestration/agent.example.conf"


def publish_conf(root: str | None = None) -> dict[str, str]:
    """The publish settings in force. `FLEET_PUBLISH_ROOT` relocates them."""
    root = root or os.environ.get("FLEET_PUBLISH_ROOT") or checkout_root()
    return read_kv_conf(conf_path(PUBLISH_CONF, PUBLISH_CONF_DEFAULTS, root))


def agent_conf(root: str | None = None) -> dict[str, str]:
    """The agent settings in force. `FLEET_AGENT_ROOT` relocates them."""
    root = root or os.environ.get("FLEET_AGENT_ROOT") or checkout_root()
    return read_kv_conf(conf_path(AGENT_CONF, AGENT_CONF_DEFAULTS, root))


def configured_agent() -> str | None:
    """The agent every spawn names, or None to leave thurbox its own default.

    Empty is the shipped answer and is not a gap: `agents.toml` already records
    which agent the operator runs and `session create` already honours it, so a
    name here would be a second copy of that answer.
    """
    return agent_conf().get("AGENT", "").strip() or None


def worker_glyph(root: str | None = None) -> str:
    """The mark every worker fleet spawns wears, or "" when glyphs are off."""
    conf = glyph_conf(root)
    setting = conf.get("GLYPHS", "on")
    if setting not in ("on", "off", ""):
        raise QueueError(f"GLYPHS in {GLYPH_CONF} is neither 'on' nor 'off'")
    if setting == "off":
        return ""
    return conf.get("WORKER_GLYPH_ON", "")


def session_name(title: str, glyph: str) -> str:
    """A worker's session name: its title, wearing the mark, within the cap.

    The glyph goes in FRONT and the title is what gets cut, so a run of workers
    is a column of marks with the work beside it. Truncation is by byte and on a
    codepoint boundary — thurbox counts bytes (see SESSION_NAME_BYTES) and a
    name cut through the middle of a character is not a name it accepts either.
    """
    name = f"{glyph} {title}" if glyph else title
    encoded = name.encode()
    if len(encoded) <= SESSION_NAME_BYTES:
        return name
    return encoded[:SESSION_NAME_BYTES].decode(errors="ignore")


def session_name_refusal(title: str, glyph: str) -> str:
    """Why `session create` would refuse this title, asked at `add` time.

    thurbox's rule MIRRORED, never re-invented and never tightened: a session
    name becomes a path segment there, so `paths::validate_safe_name` refuses
    an empty name, one over the byte cap, one starting `.`, and one holding
    `/`, `\\` or `..` — the four shapes its own `unsafe_names_are_rejected`
    enumerates. Everything else it accepts, and so does this: a title is
    human-facing text and narrowing it further would be a defect of its own.

    Asked about the RENDERED name and not the raw title, because the rendered
    name is what `dispatch` hands to `session create`: the glyph goes in front,
    so a title starting `.` is unsafe exactly when no mark precedes it, and the
    title is cut to the cap, so one that is merely long never reaches thurbox
    long. `add` took `Rust crate, CI/CD and the profile model`; `dispatch` died
    on it with thurbox's bare exit status, and the repair was a hand-edit of
    `title` in task.yaml, because there is no retitle verb.
    """
    name = session_name(title, glyph)
    if not name:
        why = "and an empty name is not one it accepts"
    elif name.startswith("."):
        why = "and a name beginning with '.' is not one it accepts"
    else:
        why = ""
        for bad in ("/", "\\", ".."):
            if bad in name:
                why = f"and it contains {bad!r}, which thurbox refuses"
                break
    if not why:
        return ""
    return (
        f"--title {title!r} cannot become a session name:\n"
        f"thurbox is asked to create {name!r}, {why}.\n"
        "That name becomes a path there, so it carries no '/', no '\\', no "
        "'..' and no\nleading '.'. The spawn fails with thurbox's own refusal "
        "and the task stays\nqueued.\n"
        "Retitle the task; nothing else about it has to change."
    )


def queue_root() -> str:
    """Where the queue lives, as an absolute path.

    FLEET_QUEUE_DIR is honoured verbatim and never second-guessed — the
    selftests and anyone pointing a harness at a temp directory rely on that.
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
        with open(policy_path()) as fh:
            text = fh.read()
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) != 3:
        return None
    try:
        doc = yaml.safe_load(parts[1])
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
    half now: it documents the three artifact shapes, ships `pr`, and names no
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
        with open(operator_path()) as fh:
            return fh.read().strip()
    except OSError:
        return ""


# --- which checkout owns the queue -------------------------------------------
#
# The control plane is one checkout. A second clone of this repo is the
# SUPPORTED shape — the control plane may have no `origin` of its own, so
# workers branch and push from a clone that does — and that is exactly how the
# queue silently forked: a `topic add` run with the shell in the second clone
# wrote records the TUI pane was right to not show, and nothing said a word.
#
# Two ways to recognise the control plane, cheapest first, and both are things
# the install already produced rather than new state this file invents:
#
#   extension.toml   rendered into the control-plane clone by
#                    scripts/install-extension.sh, with `repo_path` naming it.
#                    Gitignored, so its presence IS the claim. No thurbox needed.
#   the live session thurbox-cli reports the lead session's real `cwd`, which
#                    is the only authority when the clone has moved. Its NAME
#                    comes from the manifest, never from a constant here.
#
# When neither answers — no rendered manifest, no thurbox-cli, no such session —
# the answer is "unknown", and unknown must stay SILENT. A fleet used without
# the extension installed is a legitimate setup and may not be made unusable by
# a guard that cannot tell whether it is even warranted.

SESSION_REPO_PATH_RE = re.compile(r'^\s*repo_path\s*=\s*"([^"]*)"', re.M)
SESSION_NAME_RE = re.compile(r'^\s*name\s*=\s*"([^"]*)"', re.M)
# The TABLE HEADER, anchored at the start of a line — the same thing
# scripts/install-extension.sh matches with `/^\[\[sessions\]\]/`. A plain
# substring search finds the manifest header's own PROSE about `[[sessions]]`
# first and reads the top-level extension name as the session's. That was
# invisible for as long as the two were the same word, and stopped being
# invisible the day the session was renamed to `⌖ Mission Control`.
SESSION_TABLE_RE = re.compile(r"^\[\[sessions\]\]", re.M)


def manifest_session(path: str) -> tuple[str | None, str | None]:
    """(session name, repo_path) from the first [[sessions]] block of a manifest."""
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError:
        return None, None
    table = SESSION_TABLE_RE.search(text)
    if not table:
        return None, None
    sessions = text[table.end():]
    name = SESSION_NAME_RE.search(sessions)
    repo = SESSION_REPO_PATH_RE.search(sessions)
    return (name.group(1) if name else None, repo.group(1) if repo else None)


def live_session_cwd(name: str) -> str | None:
    """The directory the named thurbox session actually opens, if it is running."""
    try:
        out = subprocess.run(
            ["thurbox-cli", "session", "list", "--json"],
            capture_output=True,
            text=True,
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
    """The checkout that owns the queue, or None when it cannot be told."""
    here = checkout_root()
    # The tracked extension.toml.in is the fallback source for the session's
    # NAME, so a rename (README's "Renaming" section) reaches this without a
    # second edit.
    name, repo_path = manifest_session(os.path.join(here, "extension.toml"))
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
    if owner and owner != checkout_root().rstrip("/"):
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
        f"      {os.path.join(owner, 'scripts', 'queue.sh')} ...\n"
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
    `check.sh` run in every worktree is how a warning stops being read.
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
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def age_of(stamp) -> str:
    """How long ago, as one short token — or "" for anything unreadable.

    Freshness is part of the fact. `collect` and `shepherd` run on the lead's
    cadence, so a state observed forty minutes ago has to look forty minutes
    old wherever it is drawn, rather than reading as what is true now.
    """
    try:
        then = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return ""
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    secs = max(0, int((datetime.now(timezone.utc) - then).total_seconds()))
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def read_yaml(path: str) -> dict:
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict):
        raise QueueError(f"{path}: expected a mapping")
    return doc


def write_yaml(path: str, doc: dict, header: str) -> None:
    body = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(header.rstrip() + "\n" + body)
    os.replace(tmp, path)


TASK_HEADER = """\
# A fleet task record. scripts/queue.sh owns this file's shape and rewrites it,
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


class Task:
    def __init__(self, topic: str, tid: str, path: str, doc: dict):
        self.topic = topic
        self.id = tid
        self.path = path
        self.doc = doc

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
        write_yaml(self.file("task.yaml"), self.doc, TASK_HEADER)


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
            meta = os.path.join(tpath, "topic.yaml")
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
            for tid in sorted(os.listdir(tpath)):
                dpath = os.path.join(tpath, tid)
                if not os.path.isdir(dpath) or not is_record_dir(tid):
                    continue
                rec = os.path.join(dpath, "task.yaml")
                if os.path.exists(rec):
                    t = Task(topic, tid, dpath, read_yaml(rec))
                    self.tasks[t.ref] = t

    def get(self, ref: str) -> Task:
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
        raise QueueError(f"no such task: {ref}")

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
        candidates = []
        if "/" in ref:
            candidates.append(tuple(ref.split("/", 1)))
        else:
            for topic in self.archived_hidden:
                tpath = os.path.join(self.root, topic)
                if os.path.isdir(os.path.join(tpath, ref)):
                    candidates.append((topic, ref))
        found = []
        for topic, tid in candidates:
            if not (is_record_dir(topic) and is_record_dir(tid)):
                continue
            dpath = os.path.join(self.root, topic, tid)
            rec = os.path.join(dpath, "task.yaml")
            if not os.path.exists(rec):
                continue
            found.append(Task(topic, tid, dpath, read_yaml(rec)))
        if len(found) > 1:
            raise QueueError(
                f"{ref} names {len(found)} tasks across topics; use <topic>/<task>"
            )
        if found:
            task = found[0]
            self._fetched[ref] = task
            return task
        return None

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
            return self.get(blocker["task"]).state == "landed"
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
# `queue.sh list`, `queue.sh plan`, `fleet-status.sh` and the TUI pane all show
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
    how long ago one should have been. `queue.sh` has no clock in its output
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
    return os.path.join(root, slug, "topic.yaml")


def unfinished(tasks: list, state_of=None) -> Task | None:
    """The first task that is not finished, or None when every one of them is.

    THE one predicate. The automatic sweep, `cmd_archive` and `cmd_check` all
    ask this and nothing else, because three implementations of "is this topic
    done" is three chances to hide a topic somebody is still working in.
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
        print("      `queue.sh list --archived` still shows them; `show <ref>` still reaches them.")
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


def cmd_topic_add(args) -> int:
    root = queue_root()
    if not SLUG_RE.match(args.slug):
        raise QueueError(f"{args.slug!r} is not a slug (lowercase, digits, hyphens)")
    path = os.path.join(root, args.slug)
    if os.path.exists(path):
        raise QueueError(f"topic {args.slug} already exists at {path}")

    prompt = args.prompt
    if args.prompt_file:
        prompt = sys.stdin.read() if args.prompt_file == "-" else open(args.prompt_file).read()
    if not prompt:
        raise QueueError("a topic needs the prompt that opened it: --prompt or --prompt-file")

    os.makedirs(path)
    write_yaml(
        os.path.join(path, "topic.yaml"),
        {
            "slug": args.slug,
            "title": args.title or args.slug.replace("-", " "),
            "created": now(),
        },
        TOPIC_HEADER,
    )
    with open(os.path.join(path, "PROMPT.md"), "w") as fh:
        fh.write(prompt.rstrip() + "\n")

    # Opening a topic is where a run begins, so it is where its log begins —
    # nobody has to decide to make one. On stderr because stdout is the VALUE
    # here: `topic="$(queue.sh topic add ...)"` still gets a bare slug.
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


def cmd_add(args) -> int:
    root = queue_root()
    tpath = os.path.join(root, args.topic)
    if not os.path.isdir(tpath):
        raise QueueError(f"no such topic: {args.topic} (open one with `topic add`)")
    if not SLUG_RE.match(args.slug):
        raise QueueError(f"{args.slug!r} is not a slug (lowercase, digits, hyphens)")

    # A topic that grows a new task is live again, whatever it was. Without
    # this an operator who did not notice the flag would add and dispatch into
    # a topic no default view draws — and would then be watching for progress
    # on a screen that had already decided not to show it.
    if read_yaml(os.path.join(tpath, "topic.yaml")).get("archived"):
        set_archived(root, args.topic, None)
        print(f"{args.topic} was archived; a new task un-archives it", file=sys.stderr)

    number = args.number or next_number(tpath)
    tid = f"{number}-{args.slug}"
    path = os.path.join(tpath, tid)
    if os.path.exists(path):
        raise QueueError(f"task {args.topic}/{tid} already exists")

    # Refused here, before a directory exists and long before a brief is
    # written: a host thurbox does not know, or one fleet cannot drive, is a
    # typo or a decision to make, and both are cheapest at `add` time.
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
    refusal = branch_refusal(args.repo, args.branch, args.base, args.host)
    if refusal:
        raise QueueError(refusal)

    # And one layer down again: the title becomes the worker's session NAME,
    # and thurbox refuses a name it could not make a path segment of. Asked
    # here for the branch's own reason, only harder — a title that gets past
    # `add` is repaired by hand-editing task.yaml and the brief's H1, because
    # nothing here retitles a task.
    title = args.title or args.slug.replace("-", " ")
    refusal = session_name_refusal(title, worker_glyph())
    if refusal:
        raise QueueError(refusal)

    # Resolution, first hit wins and per FIELD. A stated method with no stated
    # tool drops the operator's global one rather than inheriting it: "run
    # attesting pipeline" is the wrong sentence to hand a `push` task. A
    # stated `how` alone keeps the operator's method, because a lead adding a
    # note about the tool must not be able to downgrade the check by accident.
    method, how = policy_publish_default()
    if args.publish:
        method, how = args.publish, args.how
    elif args.how:
        how = args.how

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
        "profile": args.profile,
        "agent": args.agent,
        "touches": [s.strip() for s in (args.touches or "").split(",") if s.strip()],
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
    brief = open(args.brief_file).read() if args.brief_file else None
    text = render_brief(task, read_yaml(os.path.join(tpath, "topic.yaml")), brief)
    if args.brief_file:
        missing = unfilled_sections(text)
        if missing:
            raise QueueError(
                f"{args.brief_file} leaves {len(missing)} of the brief's "
                "sections unwritten:\n"
                + "\n".join(f"    {h}" for h in missing)
                + "\nA worker gets all four whatever the file says, so one the "
                "file does not\nname stays the scaffold's placeholder and "
                "`dispatch` refuses it. Add the\nheading — `None.` is a "
                "complete answer — and run this again. Nothing was created."
            )

    os.makedirs(path)
    task.save()
    with open(task.file("BRIEF.md"), "w") as fh:
        fh.write(text)

    print(task.ref)
    return 0


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
    copies each of those alongside the brief in the worktree it mints there, and
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
    result = os.path.abspath(task.file("result.md"))
    prompt = os.path.abspath(os.path.join(os.path.dirname(task.path), "PROMPT.md"))
    has_operator = bool(operator_instructions())
    if host:
        prompt_ref = "`PROMPT.md`, alongside this file"
        policy_ref = "`POLICY.md`, alongside this file"
        operator_ref = "`OPERATOR.md`, alongside this file"
    else:
        prompt_ref = f"`{prompt}`"
        policy_ref = f"`{policy_path()}`"
        operator_ref = f"`{operator_path()}`"
    operator_line = f"\n- **Operator's standing instructions.** {operator_ref}" if has_operator else ""
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
    if host:
        result_target = (
            "    result.md — in the root of this worktree, beside the BRIEF.md\n"
            "    you are reading now"
        )
        delete_names = (
            "`BRIEF.md`, `POLICY.md`, `PROMPT.md`, and `OPERATOR.md`"
            if has_operator
            else "`BRIEF.md`, `POLICY.md`, and `PROMPT.md`"
        )
        result_note = f"""
You are running on the remote host `{host}`, so the control plane's own
directories are not on this filesystem and an absolute path to one would
resolve to nothing here. `queue.sh collect` fetches that file over ssh, and it
closes this task exactly as it would locally.

**Delete {delete_names} before you commit**, or they land in your pull
request. Write `result.md` after the pull request is open, and do not commit
it either.
"""
    else:
        result_target = f"    {result}"
        result_note = ""
    return f"""# {d["title"]}

Task `{task.ref}` of topic **{topic.get("title", task.topic)}**.
The prompt this came from is at {prompt_ref}; read it if the goal here is unclear.

- **Repo.** `{d["repo"]}`{where}
- **Branch.** `{d["branch"]}` off `{d["base"]}`
{publish_line}
- **Expected to touch.** {", ".join(f"`{p}`" for p in d["touches"]) or "not recorded"}
- **Standing policy.** {policy_ref}{operator_line}

**Read that policy file before you start.** It is the rest of your
instructions and it is not repeated here: how to verify your own publish, who
merges it, the gate to run before you push, and what the other workers running
beside you mean for you. This brief does not override it.{operator_note}

{sections}

## Reporting back — write a file, do not send mail

When you are finished, or when you have concluded you cannot finish, write:

{result_target}

with exactly this shape:

```markdown
---
outcome: shipped | stuck | failed | not-applicable
artifact: <PR URL, or commit URL for a `push` task, or omit>
---
A short paragraph: what you actually did, and anything the lead must know.
```

That file is what closes this task, and the policy's last section says why it,
and not a message, is what does it.
{result_note}"""


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
        return block_on_condition(q, task, args.condition.strip(), args)

    target = q.get(args.on)

    if args.clear:
        before = len(task.blockers)
        task.doc["blocked_by"] = [
            b for b in task.blockers if b.get("task") != target.ref
        ]
        task.save()
        print(f"{task.ref}: {before - len(task.doc['blocked_by'])} blocker(s) cleared")
        return 0

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


def block_on_condition(q: Queue, task: Task, condition: str, args) -> int:
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
        f"    Nothing clears this but you: ./scripts/queue.sh block {task.ref} "
        f"--clear --condition {shlex.quote(condition)}"
    )
    return 0


def find_cycle(q: Queue) -> list | None:
    colour: dict[str, int] = {}
    stack: list[str] = []

    def walk(ref: str) -> list | None:
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
                found = walk(nxt)
                if found:
                    return found
        stack.pop()
        colour[ref] = 2
        return None

    for ref in sorted(q.tasks):
        if colour.get(ref, 0) == 0:
            found = walk(ref)
            if found:
                return found
    return None


def cmd_plan(args) -> int:
    q = Queue(queue_root())
    ready, waiting = q.ready(), q.waiting()
    overlaps = q.overlaps(ready)

    if args.json:
        print(
            json.dumps(
                {
                    "ready": [t.ref for t in ready],
                    "waiting": [
                        {"task": t.ref, "blocked_by": t.blockers} for t in waiting
                    ],
                    "overlaps": overlaps,
                },
                indent=2,
            )
        )
        return 0

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

    print()
    print(f"waiting: {len(waiting)} task(s) — each held by a durable, recorded blocker")
    for t in waiting:
        print(f"    {t.ref}")
        for b in t.blockers:
            view = blocker_view(q, t, b)
            if view["status"] == "cleared":
                continue
            print(f"        {view['line']}")
    return 0


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
# The second was refused. `message send` INJECTS into the lead's terminal and
# interrupts whoever is talking to it — that is why this file's header says
# workers write files and do not send mail, and it is as true of a remote worker
# as of a local one. It would also make completion arrive by two mechanisms
# depending on where a task happened to run, so `collect`, `reap`, `shepherd`
# and the pane would each have to learn the difference. ssh confines that
# difference to `push_brief` and `fetch_result`. By the time anything else reads
# a task, its result.md is a local file that says nothing about where it came
# from.
#
# POSIX HOSTS ONLY, and it is checked rather than assumed. Every remote command
# here is POSIX shell — `printf`, `test`, `cat >`. A Windows host answering ssh
# with PowerShell runs none of them the way they read, and the operator's own
# hosts.toml has one such host in it. So probe 1 asks the host to print a
# sentinel and a shell that cannot is refused BY NAME, before any session
# exists, instead of producing a worker that fails at its first command.
#
# CREDENTIALS ARE NEVER MOVED. The host needs its OWN forge credentials to
# clone, fetch and push; ours are not inherited and nothing here sends them.
# Probe 2 asks whether the host has any, and refuses the dispatch when it does
# not — that is the whole of fleet's involvement. Forwarding an SSH agent would
# also fix it and would forward every key that agent holds; that is the
# operator's call to make on their own machine, not something a dispatch makes
# for them.

# What probe 1 asks the host to print. A POSIX shell echoes it; a PowerShell
# host has no `printf` and answers with an error, which is the distinction.
POSIX_SENTINEL = "fleet-posix-ok"

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

HOSTS_TOML_FALLBACK = os.path.expanduser("~/.config/thurbox/hosts.toml")


# THE SHELL EVERY REMOTE COMMAND RUNS IN, and the reason it is not the default
# one. `ssh host 'cmd'` gets a shell that is neither a login shell nor an
# interactive one: none of the account's profile has run, so `PATH` is the bare
# system default. Agent and thurbox binaries live in `~/.local/bin`, which is
# exactly what a profile puts on `PATH` — so `command -v thurbox-cli` answered
# "not found" on debian-hp, where thurbox-cli 2.20.0 is installed, and the lead
# read that as an unprovisioned host.
#
# This is thurbox pull request #1100's bug in fleet's own code, and `/bin/sh
# -lc` is thurbox's own remedy for it (`login_wrap_for_remote`). The host's
# login shell is the host's business and nothing here may hard-code one:
# measured on debian-hp, whose login shell IS zsh, `/bin/sh -lc` finds the
# binary through `~/.profile` while `zsh -lc` does not, because a
# non-interactive zsh reads no `~/.zshrc`. So the POSIX login shell is both the
# simpler answer and the better one.
LOGIN_SHELL = "/bin/sh"


def login_wrap(script: str) -> str:
    """One command, run by the host's POSIX login shell."""
    return f"{LOGIN_SHELL} -lc {shlex.quote(script)}"


def first_line(proc) -> str:
    """The one line of an ssh failure worth reporting, stderr before stdout."""
    for stream in (proc.stderr, proc.stdout):
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
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
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
            capture_output=True, text=True, timeout=30,
        )
        return json.loads(proc.stdout) if proc.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def hosts_file() -> str:
    """Where thurbox reads hosts.toml — asked of thurbox rather than assumed."""
    path = ((thurbox_config().get("paths") or {}).get("hosts_toml")) or ""
    return str(path) or HOSTS_TOML_FALLBACK


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

    # hosts.toml spells a Windows host by giving it a non-tmux multiplexer.
    # Refused by name here rather than discovered by a worker that cannot run
    # its first command — see this section's header.
    mux = str(entry.get("multiplexer") or "tmux")
    if mux != "tmux":
        return None, (
            f"host {name!r} runs the {mux!r} multiplexer, which is how hosts.toml "
            "spells a non-POSIX host. Fleet dispatches to POSIX hosts only: its "
            "probes, its brief copy and its result fetch are all POSIX shell. "
            "Run this task locally, or name a POSIX host."
        )

    # THE TRUST DIALOG, decided here. `session capture`, `key` and `send` all
    # work against a remote session — thurbox delegates each verb to the
    # thurbox-cli on the host — so `session-trust.sh` answers a remote dialog
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


def ssh_run(entry: dict, script: str, stdin: str | None = None, login: bool = True):
    """One POSIX shell command on the host. Never raises; the caller reads it.

    A LOGIN SHELL BY DEFAULT (see LOGIN_SHELL), because what almost every caller
    asks is "what does this host have", and only the profile's `PATH` can
    answer it. The exception is the two calls that MOVE BYTES — the brief push
    and the result fetch — which need no binary beyond `cat` and which a
    profile that prints would corrupt in the middle of. They pass
    `login=False`, and that is the whole rule: a login shell to FIND something,
    a bare one to CARRY something.
    """
    try:
        return subprocess.run(
            ssh_argv(entry) + [login_wrap(script) if login else script],
            input=stdin, capture_output=True, text=True, timeout=SSH_TIMEOUT,
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

    reach = ssh_run(entry, f"printf %s {POSIX_SENTINEL}")
    if reach.returncode == SSH_CONNECTION_FAILED and POSIX_SENTINEL not in reach.stdout:
        out.append({"check": "reachable", "ok": False,
                    "detail": first_line(reach) or "ssh could not connect"})
        return out
    if POSIX_SENTINEL not in reach.stdout:
        out.append({"check": "posix shell", "ok": False, "detail": (
            "the host answered ssh but did not print the POSIX sentinel "
            f"({first_line(reach) or 'no output'}). Fleet dispatches to POSIX "
            "hosts only.")})
        return out
    out.append({"check": "reachable", "ok": True, "detail": "answers ssh, POSIX shell"})

    quoted = shlex.quote(repo)
    check = (
        f"if [ ! -d {quoted} ]; then printf no-dir; exit 1; fi\n"
        f"if [ ! -e {quoted}/.git ]; then printf no-git; exit 1; fi\n"
        "printf ok\n"
    )
    seen = ssh_run(entry, check)
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

    creds = ssh_run(entry, forge_probe(repo))
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
        said = last_out(creds)
        if said.startswith("no forge: "):
            detail = said[len("no forge: "):]
        else:
            tried, _, tools = said.partition("|")
            tried = tried or "its forge"
            found = (
                f"{tools} is installed there and not logged in to it" if tools
                else "neither `gh` nor `glab` is on its login shell's PATH"
            )
            detail = (
                f"the host ANSWERED, and it has no credentials of its own for "
                f"{tried}: no ssh key, and {found}. It cannot clone, fetch or push. "
                "Give that MACHINE its own credentials; nothing here sends yours."
            )
        out.append({"check": "forge", "ok": False, "detail": detail})
        return out
    out.append({"check": "forge", "ok": True,
                "detail": f"reaches {last_out(creds) or 'its forge with a credential'}"})
    return out


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
    probe = ssh_run(entry, f"printf %s {POSIX_SENTINEL}")
    if POSIX_SENTINEL in probe.stdout:
        return True, ""
    return False, first_line(probe) or "ssh could not connect"


def remote_path(worktree: str, name: str) -> str:
    return f"{worktree.rstrip('/')}/{name}"


def session_worktree(sid: str) -> tuple[str, str]:
    """The worktree thurbox made for this session. (path, reason it could not say).

    For a remote session this is a path on the HOST — thurbox mints it there —
    which is exactly what the brief copy and the result fetch need.
    """
    try:
        proc = subprocess.run(
            ["thurbox-cli", "session", "get", sid, "--json"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"thurbox-cli session get could not run: {exc}"
    if proc.returncode != 0:
        return "", "thurbox-cli session get failed"
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        return "", "thurbox-cli session get did not answer JSON"
    trees = (doc or {}).get("worktrees") or []
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
    proc = ssh_run(entry, f"cat > {shlex.quote(dest)}", stdin=text, login=False)
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
    proc = ssh_run(entry, f"cat {shlex.quote(src)}", login=False)
    if proc.returncode != 0:
        return None, first_line(proc) or "no result.md on the host yet"
    return proc.stdout, ""


def settle_remote(task: Task, entry: dict) -> tuple[bool, str]:
    """Resolve the host-side worktree and put the brief in it. (ok, what happened).

    Runs between `session create` and the first `session send`, because the
    worker is about to be told to read a file that does not exist yet.

    The brief points at PROMPT.md, POLICY.md and (when the operator has one)
    OPERATOR.md as siblings of itself — see render_brief — so those must land
    in the worktree too, not only BRIEF.md, or the brief tells the worker to
    read control-plane paths that do not exist on this filesystem.
    """
    wt, why = session_worktree(task.doc["session"])
    if not wt:
        return False, why
    brief = remote_path(wt, "BRIEF.md")
    why = push_brief(entry, brief, read_text(task.file("BRIEF.md")))
    if why:
        return False, f"could not copy the brief to {entry['destination']}: {why}"
    companions = [
        ("PROMPT.md", os.path.join(os.path.dirname(task.path), "PROMPT.md")),
        ("POLICY.md", policy_path()),
    ]
    if operator_instructions():
        companions.append(("OPERATOR.md", operator_path()))
    for name, src in companions:
        why = push_brief(entry, remote_path(wt, name), read_text(src))
        if why:
            return False, f"could not copy {name} to {entry['destination']}: {why}"
    task.doc["remote"] = {
        "host": task.doc["host"],
        "destination": str(entry["destination"]),
        "worktree": wt,
        "brief": brief,
        "result": remote_path(wt, "result.md"),
        "at": now(),
    }
    task.save()
    return True, f"brief copied to {entry['destination']}:{brief}"


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
    with open(task.file("result.md"), "w") as fh:
        fh.write(text)
    return f"result fetched from {rec.get('destination')}:{src}"


# --- dispatch ----------------------------------------------------------------


def read_text(path: str) -> str:
    """A file's content, best effort. A missing one reads as the placeholder.

    What the REMOTE push makes of that: a brief that is not there lands on the
    host as a scaffold, and the worker has nothing to do. `brief_shortfall`
    above is what stops that reaching a host at all, at dispatch.
    """
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return BRIEF_PLACEHOLDER


def profile_flags(profile: str) -> list:
    """The agent settings for this task, from orchestration/session-profiles.yaml."""
    try:
        out = subprocess.run(
            ["./scripts/session-flags.sh", profile],
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [f for f in out.decode().split("\0") if f]


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
        return rec.get("brief") or f"<{task.doc['host']}>:<worktree>/BRIEF.md"
    return os.path.abspath(task.file("BRIEF.md"))


def spawn_commands(task: Task) -> tuple[list, str]:
    d = task.doc
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
    # The whole of what moves a worker to another machine. `--repo-path` above
    # is then a path on THAT machine, which is why nothing here looks for it
    # locally — see `probe_host`, which asks the host instead.
    if d.get("host"):
        create += ["--host", d["host"]]
    flags = profile_flags(d.get("profile") or "default")
    # A profile carrying `command` replaces `--agent`; thurbox refuses both.
    if "--command" not in flags:
        agent = d.get("agent") or configured_agent()
        if agent:
            create += ["--agent", agent]
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
    return create, send


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


def select_for_dispatch(q: Queue, refs: list) -> list:
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
    """
    if not refs:
        return q.ready()
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


def cmd_dispatch(args) -> int:
    q = Queue(queue_root())
    ready = select_for_dispatch(q, args.ref)

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
            f"dispatch: {len(ready)} named task(s), launched together — no "
            "concurrency cap."
            + (
                f"\n          {rest} other ready task(s) stay queued, and nothing "
                "records that:\n          no ref is the norm, and the next bare "
                "`dispatch` sends them."
                if rest > 0
                else ""
            )
        )
    else:
        print(
            f"dispatch: {len(ready)} task(s), launched together — no concurrency cap,\n"
            "          because every one of them has no recorded blocker left."
        )

    if args.dry_run:
        for t in ready:
            create, send = spawn_commands(t)
            print(f"    {t.ref}")
            if t.doc.get("host"):
                print(f"      on host {t.doc['host']} — probed first, and not spawned"
                      " until all three pass:")
                print("        reachable and a POSIX shell / the repo is there"
                      " / it has its own credentials for that repo's forge")
            print(f"      {shell_quote(create)}")
            if t.doc.get("host"):
                print("      ssh <host> 'cat > <worktree>/BRIEF.md'   # the worker's"
                      " filesystem is not this one")
            print("      ./scripts/session-trust.sh <uuid>   # answer the trust dialog first")
            print(f"      thurbox-cli session send <uuid> {shell_quote([send])}")
        return 0

    # Phase 1: create every session back to back, before any of them is kept
    # waiting on a trust dialog. That is what makes "launched together" true —
    # a whole wave against one repo draws its dialogs simultaneously only if
    # session creation for task 2 does not wait on task 1's trust confirmation.
    attached: list[Task] = []
    for t in ready:
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
                continue
            probes = probe_host(entry, t.doc["repo"])
            for p in probes:
                mark = "ok  " if p["ok"] else "FAIL"
                print(f"    {t.ref}  probe {mark} {p['check']}: {p['detail']}",
                      file=None if p["ok"] else sys.stderr)
            if not probes[-1]["ok"]:
                print(f"    {t.ref}: NOT SPAWNED — the `{probes[-1]['check']}` probe "
                      f"failed on host {t.doc['host']}", file=sys.stderr)
                continue

        create, _send = spawn_commands(t)
        proc = None
        try:
            proc = subprocess.run(create, capture_output=True, check=True)
            session = json.loads(proc.stdout)["id"]
        except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as exc:
            print(f"    {t.ref}: spawn failed: {spawn_failure(exc, proc)}", file=sys.stderr)
            continue
        attach(t, session)

        # The remote worker is about to be told to read a file that is not on
        # its filesystem. Put it there first, and record where — `collect`
        # fetches the result back from beside it.
        if entry:
            ok, note = settle_remote(t, entry)
            print(f"    {t.ref}  {note}", file=None if ok else sys.stderr)
            if not ok:
                print(f"    {t.ref}: session exists but was NOT prompted — the brief "
                      "never reached the host", file=sys.stderr)
                continue
        attached.append(t)

    # Phase 2: the session exists, but the agent may be sitting on a trust
    # dialog: thurbox mints a fresh worktree path per session and most agents
    # ask about a directory they have not seen. Sending the brief now would
    # type it INTO that dialog, which is how every fleet-spawned worker used
    # to break.
    unprompted: list = []
    for t in attached:
        ok, report = prompt_session(t)
        print(f"    {t.ref}  -> {t.doc['session']}{'' if ok else '  NOT PROMPTED'}")
        if report:
            print(f"        {report}", file=None if ok else sys.stderr)
        if not ok:
            unprompted.append(t.ref)
    if unprompted:
        print(
            f"\n{len(unprompted)} session(s) exist but were NOT prompted. Look at the\n"
            "pane, then retry the handoff — nothing was typed into them:\n"
            "    ./scripts/queue.sh prompt",
            file=sys.stderr,
        )
    refresh_run_logs(q)
    return 0


def attach(task: Task, session: str) -> None:
    task.doc["session"] = session
    task.doc["state"] = "dispatched"
    task.doc["dispatched_at"] = now()
    task.doc["prompted"] = False
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


def prompt_session(task: Task, timeout: int = 20) -> tuple[bool, str]:
    """Get the session past its trust dialog, then send it its brief.

    Split out of dispatch because it is the retry path too. A session that
    could not be confirmed past the dialog is left recorded and UNPROMPTED
    rather than silently sent a brief that would land in the dialog — the
    record says `prompted: false` and `queue.sh prompt` picks it up again.
    """
    session = task.doc.get("session")
    if not session:
        return False, "no session attached"

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

    _, send = spawn_commands(task)
    ok, report = trust_and_send(session, send, timeout)
    if not ok:
        if task.doc.get("host"):
            report += (
                f"\n(this worker is on host {task.doc['host']}: `session capture` and "
                "`session key` reach it by delegation to the thurbox-cli there, so the "
                "pane is answerable — but `scripts/trust-thurbox-dir.sh` seeds THIS "
                "machine's ~/.claude.json and would do nothing for it.)"
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
    q = Queue(queue_root())
    task = q.get(args.ref)
    attach(task, args.session)
    print(f"{task.ref} -> {args.session}")
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
        return int(open(path).read().strip())
    except (OSError, ValueError):
        return None


def watch_command(extra: list) -> list:
    """The stream command, real or the selftest's recorded-stream override."""
    override = os.environ.get("FLEET_QUEUE_WATCH_CMD")
    if override:
        return ["sh", "-c", override]
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
        fh = open(task.file("progress.jsonl"))
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
    `thurbox-cli message send`, which pushes into the recipient's terminal the
    moment a worker calls it.
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
    for ref, ev in sorted(touched.items()):
        # A turn ending is the ONLY thing worth a second look, and it is still
        # not a completion — the worker's own file is.
        if (ev.get("to_state") or ev.get("state")) not in ("done", "idle"):
            continue
        task = q.tasks[ref]
        if os.path.exists(task.file("result.md")):
            print(f"    {ref}: a result is waiting — read it with `queue.sh collect`")
        else:
            print(
                f"    {ref}: turn ended with no result file yet. That is not a\n"
                "        finished task; leave it be or go and look at the pane."
            )
    return 0


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
        with open(task.file("progress.jsonl"), "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError as exc:
        raise QueueError(
            f"{task.ref}: could not append to progress.jsonl: {exc}\n"
            "Nothing was lost — the stream is replayed from each task's own\n"
            "record, so fix the path and run `queue.sh watch` again."
        ) from exc


# --- collect: the WHAT -------------------------------------------------------


def parse_result(text: str) -> tuple[dict, str]:
    meta: dict = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2]
    if not isinstance(meta, dict):
        meta = {}
    return meta, body.strip()


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
    if block.get("method") in PUBLISH_METHODS:
        method, how = block["method"], block.get("how")
    text = str(how).strip() if how else ""
    return method, text or None


def publish_verdict(task: Task, outcome, url) -> tuple[str, str]:
    """Does this task's artifact prove it published? Four answers, per method.

        skipped   nothing to check — the outcome does not require an artifact
                  (`not-applicable` or `stuck`), and none, or one of the wrong
                  shape, was given.
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
    """
    method, _how = task_publish(task)
    if method == "push":
        return commit_verdict(task, outcome, url)
    return pull_request_verdict(task, method, outcome, url)


def pull_request_verdict(task: Task, method: str, outcome, url) -> tuple[str, str]:
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
    """
    if not forge.change_url(url):
        if outcome == "shipped":
            return "missing", "shipped with no pull request to check"
        return "skipped", "no pull request to check"
    which, ref = forge.for_url(url)
    if which is None:
        return "unknown", ref
    cr, why = which.get(ref)
    if why:
        return "unknown", why

    branch = str(task.doc.get("branch") or "")
    if not cr.head_branch:
        return "unknown", "the forge did not say which branch this pull request is from"
    if cr.head_branch != branch:
        return "missing", (
            f"the pull request is from branch {cr.head_branch}, and this task's "
            f"is {branch}"
        )

    if method == "attested":
        attested, why = attestation_verdict(cr.body, cr.head_sha)
        if not attested:
            why += pipeline_moved_the_head(cr)
        return ("passed" if attested else "missing"), why

    if cr.state not in ("open", "merged"):
        return "missing", (
            f"the pull request is {cr.state or 'in no state the forge named'}"
        )
    return "passed", f"the pull request is {cr.state} and is from {branch}"


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
    m = attestation_re().search(cr.body or "")
    if not m:
        return ""
    try:
        doc = json.loads(m.group(1))
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


def commit_verdict(task: Task, outcome, url) -> tuple[str, str]:
    """git as witness, for the method that ends on the base branch and not in a PR.

    The ancestry question is asked in two halves rather than one, because
    `git_out` answers "" both for a command that failed and for one that
    printed nothing — and `merge-base --is-ancestor` prints nothing either way.
    So: read the two commits first, where an empty answer is `unknown` exactly
    as an unreachable `gh` is; then ask for their merge base, where the answer
    IS the ancestry and an empty one means histories that do not meet.
    """
    match = COMMIT_URL_RE.match((url or "").strip())
    if not match:
        if outcome == "shipped":
            return "missing", "shipped with no commit URL to check"
        return "skipped", "no commit to check"
    sha = match.group(2)

    host = task.doc.get("host")
    if host:
        return "unknown", f"the base branch is on host {host}; not checked from here"
    repo = str(task.doc.get("repo") or "")
    base = str(task.doc.get("base") or "main")

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


def collect_publish_state(verdict: str, method: str) -> str:
    """What `collect` writes into `publish.state`, or "" for nothing to record.

    `skipped` is the "" — a task that legitimately produced no artifact has no
    publish to observe, and an absent state is what says "nothing has looked".
    `pushed` is terminal: the code is on the base branch, so the task lands in
    this same run and there is nothing left for the shepherd to watch.
    """
    if verdict == "passed":
        return "pushed" if method == "push" else "open"
    return {"missing": "unverified", "unknown": "unknown"}.get(verdict, "")


def record_publish(task: Task, state: str, detail: str, by: str) -> None:
    """The publish block, stamped by whoever LOOKED — never by a worker.

    Merged into whatever is already there, so the method and the `how` the lead
    declared at intake survive an observation being written over them.

    Appended to progress.jsonl the way `record_shepherd` appends, because the
    block holds only the LATEST look: "checks-running, then checks-failed, then
    ready" is the story of a pull request, and every retelling of it would
    otherwise be overwritten by the next pass. The entry carries no `seq`, so
    it is not a stream event and never moves a watch floor (`folded_through`).
    """
    block = dict(task.doc.get("publish") or {})
    block.update({"state": state, "detail": detail, "at": now(), "by": by})
    task.doc["publish"] = block
    task.save()
    with open(task.file("progress.jsonl"), "a") as fh:
        fh.write(json.dumps({"publish": dict(block), "observed": now()}) + "\n")


def report_unverified(task: Task, url, detail: str) -> None:
    """The loud half of the check: the lead sees this AT COLLECT TIME."""
    method, how = task_publish(task)
    spec = PUBLISH_METHODS[method]
    told = f"\n        Its brief said: {how}." if how else ""
    print(
        f"    {task.ref}: NOT CLOSED — nothing proves this task published\n"
        f"        {url or '(no artifact given)'}\n"
        f"        {detail}\n"
        f"        A `{method}` task is proven when {spec['proof']}.{told}\n"
        "        Send the worker back to publish again, then collect again.\n"
        "        If you have read the artifact yourself and judged it good as\n"
        "        it stands, close it deliberately with\n"
        "        `queue.sh collect --allow-unverified`.",
        file=sys.stderr,
    )


def cmd_collect(args) -> int:
    q = Queue(queue_root())
    concluded = 0
    held = 0
    artifacts = 0
    for task in sorted(q.tasks.values(), key=lambda t: t.ref):
        path = task.file("result.md")
        if task.state in ("done", "landed", "stuck", "failed", "abandoned"):
            continue

        # The remote transport, and the whole of it. A worker on another machine
        # wrote its result into its own worktree; this pulls that file into the
        # task's own result.md, after which every line below reads a local file
        # and neither knows nor cares which machine wrote it. Nothing is closed
        # here: a result that could not be fetched leaves the task exactly as a
        # missing local one does.
        if task.doc.get("host") and task.state == "dispatched":
            note = pull_remote_result(task)
            if note:
                print(f"    {task.ref}  {note}")

        if not os.path.exists(path):
            continue
        meta, body = parse_result(open(path).read())
        outcome = str(meta.get("outcome", "")).strip()
        if outcome not in OUTCOMES:
            print(
                f"    {task.ref}: result.md has outcome {outcome!r}; expected one of "
                + ", ".join(sorted(OUTCOMES)),
                file=sys.stderr,
            )
            continue
        artifact = meta.get("artifact")
        method, _how = task_publish(task)
        verdict, detail = publish_verdict(task, outcome, artifact)
        # Recorded before the branch below, so a held-back task carries the
        # reason in its record and not only in the terminal that saw it. The
        # METHOD is recorded with it because a verdict is only readable beside
        # what it was asked to prove.
        task.doc["artifact_check"] = {
            "verdict": verdict, "detail": detail, "at": now(), "method": method,
        }
        state = collect_publish_state(verdict, method)
        if state:
            record_publish(task, state, detail, "collect")

        if verdict == "missing" and not args.allow_unverified:
            task.save()
            report_unverified(task, artifact, detail)
            held += 1
            continue

        task.doc["outcome"] = outcome
        task.doc["artifact"] = artifact
        task.doc["state"] = OUTCOMES[outcome]
        task.doc["concluded_at"] = now()
        task.save()
        concluded += 1
        artifacts += 1 if pr_ref(task.doc.get("artifact")) else 0
        line = f"    {task.ref}  {outcome}"
        if artifact:
            line += f"  {artifact}"
        if verdict == "passed":
            line += f"  [publish verified: {method}]"
        elif verdict == "missing":
            line += "  [publish NOT verified — closed by --allow-unverified]"
        elif verdict == "unknown":
            line += f"  [publish unchecked: could not check — {detail}]"
        print(line)
        first = body.splitlines()[0] if body.splitlines() else ""
        if first:
            print(f"        {first}")

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
            "         `queue.sh shepherd --dry-run` — a PR can go bad long\n"
            "         after the worker that wrote it stopped."
        )

    # The third thing, wired into the command the lead already runs rather than
    # left as a chore for it to remember — a step only a human remembers is the
    # step that let twenty gigabytes of merged-and-forgotten worktree pile up.
    # It is safe to run here because its gate is not this command's: it acts on
    # a task whose artifact the FORGE says has landed, which is never true of a
    # result collected a moment ago. Everything else is reported and left be.
    reap(q, dry=False, release=not args.no_reap)

    # The run log's facts move when the records do, and this is the command
    # that moves them most. It prints only when a file actually changed.
    refresh_run_logs(q)
    return 0


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
# `done`, discovered by asking the forge, never by a worker claiming it.

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
REAPABLE_SESSION_STATES = ("idle", "done", "stopped")

# The task states that still hold a session worth reporting on. `queued` never
# had one and `dispatched` is a worker mid-flight.
HOLDING_STATES = ("done", "landed", "abandoned", "stuck", "failed")

# What a landing promotes a `done` task to. `open` and `unknown` are absent on
# purpose: a task waits rather than move on a fact nobody could establish.
LANDED_STATE = {"merged": "landed", "none": "landed", "closed": "abandoned"}


def artifact_landing(artifact) -> tuple[str, str]:
    """Has this task's artifact reached main? Asked of the forge, never of a worker.

        none      nothing to wait for — `not-applicable` produced no artifact,
                  or the artifact is not a pull request. Such a task skips
                  straight through rather than waiting forever for a merge
                  that is never going to happen.
        merged    the pull request is on main.
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


def sweep_landings(q: Queue, dry: bool) -> dict:
    """Ask the forge about every `done` task and promote the ones that landed.

    Works from the RECORD and the forge alone. That is a requirement, not an
    accident: a merge normally happens after the session that produced it is
    gone, so nothing here may depend on a worker being alive to say so.

    Returns {ref: (kind, detail)} for every task it asked about, so the caller
    can say why a task it did not promote is being kept.
    """
    seen: dict = {}
    for task in sorted(q.tasks.values(), key=lambda t: t.ref):
        if task.state != "done":
            continue
        kind, detail = artifact_landing(task.doc.get("artifact"))
        seen[task.ref] = (kind, detail)
        nxt = LANDED_STATE.get(kind)
        if dry:
            if nxt:
                print(f"    {task.ref:<46} would be {nxt:<10} {detail}")
            continue
        task.doc["landing"] = {"state": kind, "detail": detail, "at": now()}
        if kind in ("merged", "closed"):
            # The same fact in the block that carries every other observation
            # of this task's publish, so a reader of that block alone is never
            # left at the last thing the shepherd saw while the pull request
            # has since merged. `open`, `none` and `unknown` write nothing:
            # they say the sweep looked and learned nothing new.
            record_publish(task, kind, detail, "reap")
        if nxt:
            task.doc["state"] = nxt
            print(f"    {task.ref:<46} {nxt:<10} {detail}")
        task.save()
    return seen


def live_sessions() -> tuple[set | None, str]:
    """Every session thurbox currently knows about, or None when it cannot be asked.

    None is emphatically not an empty set. "thurbox did not answer" must never
    read as "every session is already gone" — that would drop the id of a
    session still holding a worktree, and the worktree with it.
    """
    if not shutil.which("thurbox-cli"):
        return None, "thurbox-cli not found on PATH"
    try:
        proc = subprocess.run(
            ["thurbox-cli", "session", "list", "--json"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"thurbox-cli session list could not run: {exc}"
    if proc.returncode != 0:
        return None, "thurbox-cli session list failed"
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        return None, "thurbox-cli session list did not answer JSON"
    if not isinstance(doc, list):
        return None, "thurbox-cli session list did not answer a list"
    return {s.get("id") for s in doc if isinstance(s, dict)}, ""


def session_state(sid: str) -> tuple[str | None, str]:
    """What thurbox says the session is doing, or None with the reason it cannot say.

    `session get` and not `session list`: only `get` probes the pane, and that
    probe is the whole difference between `uncovered` — this agent is wired to
    report nothing, so its silence means nothing — and `running`, an agent
    holding the pane right now. A reap that read the cheaper answer would
    delete both.
    """
    try:
        proc = subprocess.run(
            ["thurbox-cli", "session", "get", sid, "--json"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"thurbox-cli session get could not run: {exc}"
    if proc.returncode != 0:
        return None, "thurbox-cli session get failed"
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        return None, "thurbox-cli session get did not answer JSON"
    state = doc.get("state") if isinstance(doc, dict) else None
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


def reap(q: Queue, dry: bool = False, release: bool = True) -> int:
    """Land what has landed, then release the session of every task that is finished.

    Only ever acts on a session THIS QUEUE recorded. The lead's own session and
    anything made by hand are not in the records and so are never candidates;
    the lead's is additionally named and refused, because a task attached to it
    by mistake would otherwise be a deletion.

    Returns how many tasks it had something to say about, so a caller can stay
    silent when there was nothing.
    """
    landings = sweep_landings(q, dry)
    acted = sum(1 for kind, _ in landings.values() if kind in LANDED_STATE)
    if acted and not dry:
        print("      Run `queue.sh plan` — a blocker clears when the task it names LANDS.")

    # A dry run promotes nothing, so the state on disk still says `done` for a
    # task that just landed. Project the sweep's answer forward instead, or a
    # dry run would report every reap it is about to do as a task it is keeping.
    def state_of(task: Task) -> str:
        kind = (landings.get(task.ref) or (None,))[0]
        return LANDED_STATE.get(kind, task.state) if dry else task.state

    # A topic can only become finished when one of its tasks moves into a
    # terminal state, and this is the pass that moves them — so the flag is
    # written here rather than left as something the lead has to remember.
    # Before the `--no-reap` return: archiving is a flag on a topic and has
    # nothing to do with whether a session is released.
    acted += sweep_archives(q, state_of, dry)

    if not release:
        print("      Sessions left alone (--no-reap); `queue.sh reap` releases them.")
        return acted

    holders = [
        t
        for t in sorted(q.tasks.values(), key=lambda t: t.ref)
        if t.doc.get("session") and state_of(t) in HOLDING_STATES
    ]
    if not holders:
        return acted

    lead = os.environ.get("THURBOX_SESSION")
    live: set | None = None
    live_detail = ""
    if any(state_of(t) in ("landed", "abandoned") for t in holders):
        live, live_detail = live_sessions()

    reaped = kept = dropped = 0
    for task in holders:
        sid = task.doc["session"]
        state = state_of(task)
        acted += 1
        if sid == lead:
            why = "that is the lead's own session, not a worker's"
        elif state in ("stuck", "failed"):
            why = (
                f"the worker's own verdict is `{state}` — its session is the "
                "evidence, and a human decides"
            )
        elif state == "done":
            why = landings.get(task.ref, ("", "its artifact has not landed"))[1]
        elif live is None:
            why = live_detail
        else:
            why = ""
        if why:
            print(f"    {task.ref:<46} kept       {why}")
            kept += 1
            continue

        # A remote session is asked about its HOST first. Both answers below
        # would otherwise be wrong for a host that is merely down: an absent id
        # would read as a session that finished, and a latched `idle` would
        # read as an agent at rest. See REAPABLE_SESSION_STATES.
        if task.doc.get("host"):
            up, unreachable_why = host_reachable(task.doc["host"])
            if not up:
                print(f"    {task.ref:<46} kept       unreachable: host "
                      f"{task.doc['host']} — {unreachable_why}")
                kept += 1
                continue

        if sid not in live:
            if dry:
                print(f"    {task.ref:<46} would drop {sid}  thurbox no longer has it")
            else:
                record_reaped(task, sid, "already gone")
                print(f"    {task.ref:<46} gone       {sid}  thurbox no longer had it")
                dropped += 1
            continue

        live_state, detail = session_state(sid)
        if live_state not in REAPABLE_SESSION_STATES:
            print(
                f"    {task.ref:<46} kept       thurbox says `{live_state or detail}`; "
                "only " + ", ".join(REAPABLE_SESSION_STATES) + " are reaped"
            )
            kept += 1
            continue

        if dry:
            print(f"    {task.ref:<46} would reap {sid}  ({live_state})")
            reaped += 1
            continue

        # `--force`, never a plain `delete`. Plain delete soft-deletes the row
        # and leaves the TUI to reap the tmux window and the worktrees on its
        # next sync; headless — which this is — that never happens and the disk
        # is never actually freed, which is the entire point.
        proc = subprocess.run(
            ["thurbox-cli", "session", "delete", sid, "--force"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip().splitlines()
            print(
                f"    {task.ref:<46} NOT REAPED  session delete failed: "
                + (err[-1] if err else "no output"),
                file=sys.stderr,
            )
            kept += 1
            continue
        record_reaped(task, sid, "deleted")
        print(f"    {task.ref:<46} reaped     {sid}  ({live_state})")
        reaped += 1

    if reaped or kept or dropped:
        line = (
            f"reap: {'would release' if dry else 'released'} {reaped} session(s), "
            f"kept {kept}"
        )
        if dropped:
            line += f", dropped {dropped} stale id(s)"
        print(line)
    return acted


def cmd_reap(args) -> int:
    if reap(Queue(queue_root()), dry=args.dry_run) == 0:
        print("reap: nothing has landed and no finished task is still holding a session")
    return 0


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
# And detection of ONE wedged session is a conjunction, because either half
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

# HOW EACH AGENT SAYS IT RAN OUT, one entry per agent fleet has actually
# WATCHED do it — the same shape as `scripts/session-trust.sh`'s per-agent
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
    fleet claiming to know a sentence nobody observed.
    """
    sig = dict(AGENT_LIMIT_SIGNALS.get((agent or "").strip(), {}))
    conf = agent_conf()
    banner = conf.get("LIMIT_BANNER", "").strip()
    tdir = conf.get("TRANSCRIPT_DIR", "").strip()
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

# The provider that gauge reads, and so the only agent whose account this can
# speak for. A task running something else is not refused — its account window
# is undetermined, and undetermined restarts nothing.
#
# NO NAME IS WRITTEN HERE. A literal would gate every operator's fleet on one
# operator's vendor, and reading the WRONG provider is worse than reading none:
# a spent window somewhere the fleet never dispatches would strand a worker at
# its limit. `FUEL_PROVIDER` in `orchestration/agent.conf` sets it outright;
# with that empty this reads the provider off the tasks in hand, and answers
# None when they do not agree — which reaches every caller as `undetermined`,
# which restarts nothing.


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


def fuel_agent(tasks=()) -> str | None:
    """The provider `refuel` may speak for on this pass, or None to not guess.

    `FUEL_PROVIDER` pins one outright. Otherwise the agent in hand is mapped
    through `AGENT_PROVIDERS` and, failing that, used as its own provider name.
    Tasks that do not agree on one agent answer None, which reaches the caller
    as `undetermined` and restarts nothing.
    """
    pinned = agent_conf().get("FUEL_PROVIDER", "").strip()
    if pinned:
        return pinned
    agents = {(t.doc.get("agent") or "").strip() for t in tasks}
    agents.discard("")
    if not agents:
        agent = configured_agent()
    elif len(agents) == 1:
        agent = agents.pop()
    else:
        return None
    if not agent:
        return None
    return agent_providers().get(agent, agent)


def provider_is_known(provider: str) -> tuple[bool, str]:
    """Does quota-axi hold a credential for this provider?

    Asked rather than assumed, because quota-axi supports many providers and a
    list in this file would be stale the day it gained another. A provider it
    does not name is reported, never guessed past: reading the WRONG window is
    worse than reading none.
    """
    try:
        names, why = fuel_gauge().authenticated_providers()
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
    """The `fleet_status` module, loaded from beside this file."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fleet_status.py")
    spec = importlib.util.spec_from_file_location("fleet_status_for_queue", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def account_fuel(provider: str) -> tuple[str, str]:
    """('fuel' | 'spent' | 'unknown', detail) for the account every session spends.

    `effectivePercentRemaining` is the subscription window the lead and every
    worker draw on at once — six workers dispatched together spend one window
    six ways — so this is ONE reading for the whole pass and never a per-session
    one. There is no per-session number anywhere: `session get --json` carries
    no token, usage, cost or limit field at all.
    """
    try:
        sec = fuel_gauge().probe_fuel(provider)
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
    """
    sig = limit_signal(agent)
    home = sig.get("home")
    if not home:
        return ""
    env = sig.get("home_env")
    home = (env and os.environ.get(env)) or os.path.expanduser(home)
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
        return "unknown", "thurbox-cli not found on PATH"
    try:
        proc = subprocess.run(
            ["thurbox-cli", "session", "capture", sid, "--lines", str(CAPTURE_LINES), "--json"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "unknown", f"session capture could not run: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return "unknown", "session capture failed: " + (detail[-1] if detail else "no output")
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
    """`session get --json` in full — the hook fields are the whole point here."""
    if not shutil.which("thurbox-cli"):
        return None, "thurbox-cli not found on PATH"
    try:
        proc = subprocess.run(
            ["thurbox-cli", "session", "get", sid, "--json"],
            capture_output=True, text=True, timeout=30,
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


def record_refuel(task: Task, sid: str, why: str, prompted: bool) -> None:
    """The receipt, and the cap's only memory.

    Appended, never replaced: a session that keeps running dry is a fact about
    the task, and one that is invisible if each pass overwrites the last.
    """
    task.doc.setdefault("refuels", []).append(
        {"at": now(), "session": sid, "why": why, "prompted": prompted}
    )
    task.save()


def restart_session(sid: str) -> tuple[bool, str]:
    """`session restart`: kill the window, re-spawn with `--resume`.

    The conversation survives, so the worker still holds its brief and whatever
    it had already worked out — which is why this and not a fresh spawn.
    """
    try:
        proc = subprocess.run(
            ["thurbox-cli", "session", "restart", sid], capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"session restart could not run: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return False, "session restart failed: " + (detail[-1] if detail else "no output")
    return True, ""


def stale_since(age: float) -> float:
    """When the state this age describes was reported, as an epoch second."""
    return datetime.now(timezone.utc).timestamp() - age


def record_time(stamp) -> float:
    """One of this file's own `at` timestamps as an epoch second.

    Unreadable reads as the beginning of time, which makes an unparseable
    receipt fall through to the ordinary path rather than block it.
    """
    try:
        return datetime.fromisoformat(str(stamp)).timestamp()
    except (TypeError, ValueError):
        return 0.0


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
    provider = fuel_agent(holders)
    if provider is None:
        print(
            "    account ?          undetermined  no provider to read: "
            f"{AGENT_CONF} names none and these\n"
            "      tasks do not agree on one agent. Undetermined restarts "
            "nothing — name a\n"
            f"      FUEL_PROVIDER there (see {AGENT_CONF_DEFAULTS}) to gate on "
            "one window."
        )
        verdict, detail = "unknown", "no provider named"
    else:
        known, why = provider_is_known(provider)
        if not known:
            verdict, detail = "unknown", why
        else:
            verdict, detail = account_fuel(provider)
        print(f"    account {provider:<10} "
              f"{'undetermined' if verdict == 'unknown' else verdict:<12} {detail}")
    if verdict == "spent":
        print(
            "      The account window is SPENT, and it is the operator's own "
            "subscription —\n"
            "      the lead and every worker draw on it. The fleet is waiting on "
            "the window,\n"
            "      not on any session: resuming a worker now would hit the same "
            "wall and burn\n"
            "      the reset. Nothing is touched until it comes back."
        )
    if verdict == "unknown":
        print(
            "      The account's own quota could not be read, and undetermined is "
            "never a pass\n"
            f"      and never a failure — so nothing is acted on. {QUOTA_CMD}: "
            "https://github.com/kunchenguid/quota-axi"
        )

    lead = os.environ.get("THURBOX_SESSION")
    acted = fired = kept = 0
    for task in holders:
        sid = task.doc["session"]
        acted += 1

        if sid == lead:
            # `reap`'s guard, for the same reason and a worse consequence: a
            # restart of the lead kills the operator's own conversation.
            print(f"    {task.ref:<46} kept          that is the session running this "
                  "command — the lead's own, not a worker's")
            kept += 1
            continue
        if task.state != "dispatched":
            print(f"    {task.ref:<46} kept          its task is `{task.state}`; only a "
                  "worker still in flight is refuelled")
            kept += 1
            continue
        agent = task.doc.get("agent") or provider
        if provider is None or agent != provider:
            runs = agent or "thurbox's own default"
            print(f"    {task.ref:<46} undetermined  the fuel gauge reads the "
                  f"{provider or 'unnamed'} account and this task runs `{runs}`")
            kept += 1
            continue
        if verdict != "fuel":
            # The reason is the account line above, printed once: repeating a
            # hundred characters of it per task buries the one thing a reader
            # is looking for, which is which tasks it applies to.
            word = "kept" if verdict == "spent" else "undetermined"
            print(f"    {task.ref:<46} {word:<13} the {provider} account window is "
                  f"{'spent' if verdict == 'spent' else 'unreadable'} — see above")
            kept += 1
            continue
        if task.doc.get("host"):
            # `session capture` is local-only and the transcript is on that
            # machine, so neither half of the detection can be established from
            # here. Undetermined, and never a guess.
            print(f"    {task.ref:<46} undetermined  runs on host {task.doc['host']}: its "
                  "pane and its transcript are there, not here")
            kept += 1
            continue

        doc, why = session_doc(sid)
        if doc is None:
            print(f"    {task.ref:<46} undetermined  {why}")
            kept += 1
            continue

        # The agent's own word, and only that. `running`, `uncovered` and
        # `unreported` are observations about a pane, not a claim by the agent
        # about itself (`thurbox-session` §4a), and a worker that is genuinely
        # at rest is `collect`'s business, not this command's.
        hook = doc.get("hook_state")
        age = doc.get("hook_state_age_secs")
        if hook != "working":
            print(f"    {task.ref:<46} kept          its hook says `{hook or doc.get('state')}`, "
                  "which is not a stale working state")
            kept += 1
            continue
        if not isinstance(age, (int, float)) or isinstance(age, bool):
            print(f"    {task.ref:<46} undetermined  thurbox reports no age for that "
                  "`working` state")
            kept += 1
            continue
        if age < STALE_WORKING_SECS:
            print(f"    {task.ref:<46} kept          working for {age / 60:.0f}m, under the "
                  f"{STALE_WORKING_SECS // 60}m staleness threshold")
            kept += 1
            continue

        seen, detail = exhaustion(doc)
        if seen == "undetermined":
            print(f"    {task.ref:<46} undetermined  {detail}")
            kept += 1
            continue
        if seen != "exhausted":
            # The whole reason detection is a conjunction: this worker has been
            # in one turn for a long time and its agent has said nothing about
            # a limit. Slow is not dry.
            print(f"    {task.ref:<46} kept          working for {age / 60:.0f}m and quiet "
                  f"about it — slow, not dry ({detail})")
            kept += 1
            continue

        # A `working` state REPORTED BEFORE the last restart is evidence from
        # before that restart: the re-spawned agent has simply not reported yet.
        # Without this, two passes a minute apart spend the cap on one wedge and
        # kill a window that was coming back up.
        history = task.doc.get("refuels") or []
        if history and stale_since(age) < record_time(history[-1].get("at")):
            print(f"    {task.ref:<46} kept          restarted at "
                  f"{history[-1].get('at')}, and this `working` was reported before "
                  "that — give it a moment")
            kept += 1
            continue

        already = len(history)
        if already >= REFUEL_CAP:
            print(f"    {task.ref:<46} kept          ran dry again after {already} restart(s); "
                  f"the cap is {REFUEL_CAP} — a human decides now")
            kept += 1
            continue

        if dry:
            print(f"    {task.ref:<46} would restart {sid}  {detail}")
            fired += 1
            continue

        ok, note = restart_session(sid)
        if not ok:
            print(f"    {task.ref:<46} NOT RESTARTED {note}", file=sys.stderr)
            kept += 1
            continue

        # The dispatch path's handoff, in dispatch's order and for its reason:
        # a re-spawned agent in a worktree can ask the trust question again, and
        # `session send` into that dialog types the prompt INTO it.
        send = (
            f"Read {brief_target(task)} and do what it says. Your session was "
            "restarted after your agent ran out of quota mid-task, so the "
            "conversation above is yours: continue from where you stopped rather "
            "than starting over."
        )
        prompted, report = trust_and_send(sid, send)
        record_refuel(task, sid, detail, prompted)
        print(f"    {task.ref:<46} restarted     {sid}"
              f"{'' if prompted else '  NOT PROMPTED'}")
        print(f"        {detail}")
        if not prompted:
            print(f"        the session is up but was NOT prompted: {report}\n"
                  f"        nothing was typed into it — retry with `queue.sh prompt {task.ref}`",
                  file=sys.stderr)
        fired += 1

    if fired or kept:
        print(
            f"refuel: {fired} restart(s){' (dry run)' if dry else ''}, "
            f"{kept} session(s) left alone"
        )
    return acted


def cmd_refuel(args) -> int:
    if refuel(Queue(queue_root()), ref=args.ref, dry=args.dry_run) == 0:
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
# and `--json` is the seam `scripts/fleet-status.sh` reads it through.
#
# THE RULES THAT KEEP IT FROM BEING WORSE THAN NOTHING:
#
#   Idempotent.  A dispatched fixer is recorded on the task under `shepherd`,
#                with the condition it went out for. A second pass over the
#                same still-broken PR sees work in flight, not a second job.
#   Never guess. "Could not check" is its own outcome and is never `broken`.
#                No forge, no network, no thurbox: say what could not be
#                determined and carry on. Spawning a fixer for a healthy PR is
#                the one failure that costs more than the bug.
#   Never touch  A pull request whose head branch lives in someone else's
#   a stranger.  fork is reported and left alone — never merged, never handed
#                a fixer — because a stranger cannot create a branch inside
#                this repository, so that is the one claim a pull request
#                cannot make for itself.

# §4a of .agents/skills/thurbox-session/SKILL.md, as code. These two groups are
# the AGENT SPEAKING about itself. Every other word in that table is an
# observation — `running`, `uncovered`, `unreported` — and an observation is
# not permission to type into somebody's pane.
SESSION_BUSY = {"working", "blocked"}
SESSION_AT_REST = {"idle", "done"}

# Conditions that get a fixer, worst first: one PR gets ONE fixer, for the
# thing that has to be fixed before any of the others can even be judged.
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
# itself calls it mergeable, and whoever opened it can push to this repo
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
# a different fleet, not this one plus an extra. `queue-selftest.sh` is the
# second fleet it was written for.
AUTO_MERGE_ENV = "FLEET_AUTO_MERGE_REPOS"

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
                f"{source}: ignoring {entry!r} — an auto-merge entry must "
                "name its forge, as in github.com/owner/repo",
                file=sys.stderr,
            )
            continue
        out.add(repo.qualified)
    return out


def auto_merge_conf_path(root: str | None = None) -> str:
    """The auto-merge list in force: the operator's copy, or the tracked one.

    `FLEET_AUTO_MERGE_ROOT` overrides where it is read from, the same way
    `FLEET_GLYPH_ROOT` relocates the glyph setting — so a selftest can exercise
    the file itself without inheriting whatever the developer's own gitignored
    auto-merge.conf says.
    """
    root = root or os.environ.get("FLEET_AUTO_MERGE_ROOT") or checkout_root()
    path = os.path.join(root, AUTO_MERGE_CONF)
    if not os.path.exists(path):
        path = os.path.join(root, AUTO_MERGE_CONF_DEFAULTS)
    return path


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
    try:
        with open(path) as fh:
            lines = [line.partition("#")[0] for line in fh]
    except OSError:
        return set()
    return parse_auto_merge(lines, path)


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


def attestation_re(marker: str | None = None) -> "re.Pattern":
    """The HTML comment this fleet's pipeline leaves, per publish.conf."""
    marker = marker or publish_conf().get("ATTESTATION_MARKER", "").strip()
    if not marker:
        marker = "fleet-attestation"
    return re.compile(
        r"<!--\s*" + re.escape(marker) + r":v1\s+(\{.*?\})\s*-->", re.S
    )

# The attestation is written DURING the pipeline's `pr` step, so in every body
# that carries one `pr` reads `running` and `ci` reads `pending`. Demanding
# `completed` from those two would reject every real pull request; `ci` is what
# the separate checks-passed gate is for, and the steps that decide whether the
# code is fit — review, test, lint, push — are the ones held to `completed`.
ATTESTATION_TRAILING_STEPS = {"pr", "ci"}
ATTESTATION_DONE = {"completed", "skipped"}
ATTESTATION_TRAILING_OK = ATTESTATION_DONE | {"running", "pending"}


def attestation_verdict(body: str, head_sha: str) -> tuple[bool, str]:
    """(did the pipeline run on THIS commit, one line saying how it is known).

    False is never "probably fine": every way of failing to read the
    attestation is a way of not being merged.
    """
    m = attestation_re().search(body or "")
    if not m:
        return False, (
            "the body carries no attestation, so nothing but its own "
            "prose says the pipeline ever ran"
        )
    try:
        doc = json.loads(m.group(1))
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
    unfinished = []
    for st in steps:
        if not isinstance(st, dict):
            return False, "the attestation's steps are malformed"
        name = str(st.get("step") or "an unnamed step")
        status = str(st.get("status") or "").lower()
        allowed = (
            ATTESTATION_TRAILING_OK
            if name in ATTESTATION_TRAILING_STEPS
            else ATTESTATION_DONE
        )
        if status not in allowed:
            unfinished.append(f"{name} is {status or 'unreported'}")
    if unfinished:
        return False, "the pipeline did not finish: " + ", ".join(unfinished[:4])
    return True, f"the pipeline attests {attested[:8]}, which is this head"


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


def publish_word(cr: forge.ChangeRequest, condition: str, method: str | None) -> str:
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
    # `closed`, `conflicting`, `checks-failed` and `changes-requested` are
    # already the vocabulary's own words.
    return condition


# --- what changed underneath -------------------------------------------------


def git_out(repo: str, argv: list, timeout: int = 30) -> str:
    """git, best effort. A failure is the empty string — never an exception."""
    try:
        out = subprocess.run(
            ["git", "-C", repo] + argv, capture_output=True, text=True, timeout=timeout
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
Make the failing checks pass. `./scripts/check.sh` is this repo's whole gate
and CI runs the same script, so a green local run is the thing to get to.
Push to the same branch so the existing pull request re-runs them.""",
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
        here = next((ln[9:] for ln in lines if ln.startswith("worktree ")), "")
        on = next((ln[7:] for ln in lines if ln.startswith("branch ")), "")
        if on == f"refs/heads/{branch}" and here:
            path = here
            break
    if path:
        return path, "already checked out there"

    dest = os.path.join(queue_root(), ".worktrees", slug)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        return "", f"{dest} is in the way; remove it and run again"
    try:
        out = subprocess.run(
            ["git", "-C", repo, "worktree", "add", dest, branch],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"git worktree add failed: {exc}"
    if out.returncode != 0:
        first = (out.stderr or "").strip().splitlines()
        return "", first[-1] if first else "git worktree add failed"
    return dest, "new worktree on the existing branch"


def trust_and_send(session: str, text: str, timeout: int = 20) -> tuple[bool, str]:
    """Answer the trust dialog, then type. The order is the whole point (§1b)."""
    trust = subprocess.run(
        ["./scripts/session-trust.sh", session, "--timeout", str(timeout)],
        capture_output=True,
        check=False,
    )
    report = (trust.stdout + trust.stderr).decode().strip()
    if trust.returncode != 0:
        return False, report
    # The returncode is READ. It used to be thrown away, so a send into a
    # session that had gone away returned `True` and every caller reported a
    # prompt it had not delivered — the same silence, one layer down, that
    # `sends` exists to end.
    sent = subprocess.run(
        ["thurbox-cli", "session", "send", session, text],
        capture_output=True,
        check=False,
    )
    if sent.returncode != 0:
        detail = (sent.stderr + sent.stdout).decode().strip().splitlines()
        return False, "session send failed: " + (detail[-1] if detail else "no output")
    return True, report


def spawn_fixer(task: Task, name: str, brief_path: str, branch: str) -> tuple[str, str]:
    """A session on the branch that already exists. (session id, note).

    `branch` is the pull request's own head branch and not the task's record of
    it: a task can carry a second pull request on a different branch, and the
    fix has to land on the branch the pull request is actually open from.
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

    slug = f"{task.topic}__{task.id}"
    path, note = branch_checkout(task.doc["repo"], branch, slug)
    if not path:
        return "", note
    create = [
        "thurbox-cli", "session", "create",
        # A fixer is a worker fleet spawned, so it wears the same mark under the
        # same setting — and the same byte-safe cut, which is the one that made
        # `[:64]` wrong here rather than merely generous.
        "--name", session_name(name, worker_glyph()),
        "--repo-path", path,
        # Reconciling desired state, so a name already in use is the fixer that
        # is already there rather than news (§1c). `created` is read below.
        "--on-existing", "adopt",
    ]
    flags = profile_flags(task.doc.get("profile") or "default")
    if "--command" not in flags:
        agent = task.doc.get("agent") or configured_agent()
        if agent:
            create += ["--agent", agent]
    parent = os.environ.get("THURBOX_SESSION")
    if parent:
        create += ["--parent", parent]
    create += flags + ["--json"]
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
    ok, report = trust_and_send(session, send)
    if not ok:
        return "", f"session {session} exists but was NOT prompted: {report}"
    return session, f"{note}; session {session}"


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
    with open(task.file("progress.jsonl"), "a") as fh:
        fh.write(json.dumps({"shepherd": entry, "observed": now()}) + "\n")


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
    condition, detail = classify(cr, method)
    row["condition"], row["detail"] = condition, detail
    word = publish_word(cr, condition, method)
    # What the pass SAW, written down for every linked task and whatever the
    # condition — the facts all arrived in the one forge listing above, and a
    # record that keeps them is the difference between "shipped" and "its
    # checks failed forty minutes ago". A dry run writes nothing, here as
    # everywhere else below.
    if task and not args.dry_run:
        record_publish(task, word, detail, "shepherd")
    rec = rec_for(task, url)

    # A dry run writes nothing at all, including the record-clearing below:
    # "change nothing" is the flag's whole contract, and a stale record
    # cleared by a dry run is a fixer that a later real pass re-sends.
    if condition in ("closed", "undetermined"):
        if condition == "closed" and rec and not args.dry_run:
            record_shepherd(task, None)
        return row

    if condition == "foreign":
        # Reported, and nothing else. Not merged, and not handed to an agent.
        row["action"] = "left-alone"
        row["note"] = "not ours; fleet only merges and only fixes its own"
        return row

    if condition == "ready":
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
        if cr.repo.qualified not in auto_merge_repos():
            row["action"] = "ready"
            row["note"] = f"fleet does not merge in {cr.repo}; this one is yours"
            return row
        if args.no_merge:
            row["action"] = "ready"
            row["note"] = "--no-merge"
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
            which, _ = forge.for_repo(cr.repo)
            how = (
                which.describe_merge(MERGE_METHOD, DELETE_MERGED_BRANCH)
                if which
                else f"merge by {MERGE_METHOD}"
            )
            row["action"] = "would-merge"
            row["note"] = f"{how} ({why})"
            return row
        ok, note = merge_change_request(cr)
        row["action"], row["note"] = ("merged" if ok else "merge-failed"), note
        if ok and task:
            record_shepherd(task, {"condition": "merged", "detail": note,
                                   "pr": url, "at": now()})
            record_publish(task, "merged", note, "shepherd")
        return row

    # Fixable, but only a task carries a session, a worktree and a brief
    # directory to fix it from.
    if not task:
        row["action"] = "no-task"
        row["note"] = (
            "no task records this pull request, so there is no session to send a "
            "fixer into; it is classified and left for you"
        )
        return row

    # Everything below here needs a fixer. Both liveness checks below share
    # one `session list` snapshot, so a fixer and the task's own worker read
    # "gone" from the same evidence.
    rec_session = str(rec["session"]) if rec.get("session") else ""
    worker = str(task.doc.get("session") or "")
    live, live_why = live_sessions() if (rec_session or worker) else (set(), "")

    # A fixer already dispatched for THIS pull request is still the one
    # doing the work, whatever the PR now classifies as — the condition can
    # drift between passes while the fixer is mid-fix, and that drift must
    # never look like nobody is on it.
    if not args.force and rec_session:
        status = session_status(rec_session, live)
        if status == "unknown":
            row["action"] = "left-alone"
            row["note"] = (
                f"could not tell whether the fixer sent for this at {rec.get('at')} "
                f"(session {rec_session}) is still working ({live_why or 'no state reported'}). "
                "Nothing sent."
            )
            return row
        if status != "gone":
            row["action"] = "in-flight"
            row["note"] = (
                f"a fixer went out for this at {rec.get('at')} "
                f"(session {rec_session}, now {status}). "
                "Nothing sent. `--force` overrides."
            )
            return row

    # The task's own session has the context and the worktree, so it is the
    # first choice — but only its own word puts it at rest (§4a). A session
    # that is gone reads as no session at all, which is the ordinary case once
    # a run has been cleaned up.
    reuse = ""
    if worker:
        status = session_status(worker, live)
        if status == "unknown":
            row["action"] = "left-alone"
            row["note"] = (
                f"could not tell whether its own worker {worker} is still there "
                f"({live_why or 'no state reported'}). Nothing sent."
            )
            return row
        if status in SESSION_BUSY:
            row["action"] = "left-alone"
            row["note"] = (
                f"its own worker {worker} is {status} — probably already on it. "
                "Interrupting a turn is how a fix gets half-applied."
            )
            return row
        if status in SESSION_AT_REST:
            reuse = worker
        elif status != "gone":
            row["action"] = "left-alone"
            row["note"] = (
                f"its own worker {worker} reads {status}, which is an observation "
                "and not the agent saying it is at rest. Nothing sent; look at "
                f"the pane: thurbox-cli session capture {worker}"
            )
            return row

    base = cr.base_branch or task.doc.get("base") or "main"
    branch = cr.head_branch or task.doc["branch"]
    title = FIXER_TITLES[condition].format(n=cr.number, base=base)

    if args.dry_run:
        row["action"] = "would-dispatch"
        how = f"reusing its own worker {reuse}" if reuse else "a fresh session on the branch"
        row["note"] = f"{title} ({how})"
        return row

    drift = base_drift(task.doc["repo"], base, branch) if condition == "conflicting" else ""
    path = next_fix_file(task, condition)
    with open(path, "w") as fh:
        fh.write(fixer_brief(task, cr, condition, detail, drift))

    if reuse:
        ok, report = trust_and_send(
            reuse, f"Read {os.path.abspath(path)} and do what it says."
        )
        session = reuse if ok else ""
        note = report if not ok else "reused its own worker"
    else:
        session, note = spawn_fixer(task, title, path, branch)

    if not session:
        row["action"] = "not-dispatched"
        row["note"] = note
        return row
    row["action"] = "dispatched"
    row["note"] = f"{title} -> {session}"
    record_shepherd(
        task,
        {
            "condition": condition,
            "detail": detail,
            "session": session,
            "pr": url,
            "brief": os.path.basename(path),
            "at": now(),
        },
    )
    return row


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
    """task.ref -> RepoId, derived and never hardcoded.

    The queue's tasks name their repositories: an artifact URL gives the
    repository — host and path — outright, and a task that has not reported one
    yet inherits the repository of the other tasks sharing its local checkout.
    Merging stays limited to `auto_merge_repos()` whatever comes out of here:
    knowing about a repository and being allowed to merge in it are different
    questions.
    """
    repo_of: dict[str, forge.RepoId] = {}
    by_path: dict[str, forge.RepoId] = {}
    for task in tasks:
        ref = pr_ref(task.doc.get("artifact"))
        if ref:
            repo_of[task.ref] = ref.repo
            by_path.setdefault(str(task.doc.get("repo") or ""), ref.repo)
    for task in tasks:
        if task.ref in repo_of:
            continue
        path = str(task.doc.get("repo") or "")
        repo = by_path.get(path)
        if repo is None:
            repo = repo_from_checkout(path)
            if repo is not None:
                by_path[path] = repo
        if repo is not None:
            repo_of[task.ref] = repo
    return repo_of


def link_task(cr: forge.ChangeRequest, tasks: list) -> object:
    """The task this change request belongs to, or None.

    Two ways, and the second is the one that matters. The artifact is what a
    worker reported once. The HEAD BRANCH is what the pull request is actually
    open from, and it is what connects a task's second pull request back to it
    after its first one merged and its artifact stopped being current.
    """
    for task in tasks:
        ref = pr_ref(task.doc.get("artifact"))
        if ref and ref.number == cr.number and ref.repo == cr.repo:
            return task
    if cr.head_branch:
        for task in tasks:
            if str(task.doc.get("branch") or "") == cr.head_branch:
                return task
    return None


def cmd_shepherd(args) -> int:
    """The fourth thing: the pull requests, after `watch` and after `collect`."""
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
    tasks = []
    for task in sorted(q.tasks.values(), key=lambda t: t.ref):
        if args.topic and task.topic != args.topic:
            continue
        if only and task.ref != only:
            continue
        tasks.append(task)

    repo_of = shepherd_targets(tasks)
    repos = sorted(set(repo_of.values()), key=lambda r: r.qualified)
    named = [str(r) for r in repos]

    rows, unreadable = [], []
    for repo in repos:
        here = [t for t in tasks if repo_of.get(t.ref) == repo]
        crs, err = open_prs(repo)
        if err:
            # A repository that could not be listed contributes nothing. An
            # empty answer and an unreadable one are not the same claim, and
            # only one of them means "nothing is open".
            unreadable.append({"repo": repo.qualified, "named": str(repo), "detail": err})
            continue
        for cr in sorted(crs, key=lambda c: c.number):
            rows.append(shepherd_pr(cr, link_task(cr, here), args))

    if args.json:
        print(json.dumps({
            "queue": os.path.abspath(queue_root()),
            "repos": [r.qualified for r in repos],
            "unreadable": unreadable,
            "prs": rows,
        }, indent=2))
        return 0

    if not repos:
        print("shepherd: no task names a repository on a configured forge yet")
        return 0
    for bad in unreadable:
        print(f"shepherd: could not read the pull requests on {bad['named']}: "
              f"{bad['detail']} — nothing there was touched")
    if not rows:
        if not unreadable:
            print("shepherd: no open pull requests on " + ", ".join(named))
        return 0

    verb = "would do" if args.dry_run else "did"
    print(f"shepherd: {len(rows)} open pull request(s) on {', '.join(named)} "
          f"— what it {verb}:\n")
    for r in rows:
        print(f"    {r['task'] or '(no task records it)'}  {r['pr']}")
        print(f"        {r['condition']}: {r['detail']}")
        if r["action"] != "none":
            print(f"        {r['action']}: {r['note']}" if r["note"] else f"        {r['action']}")
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
    if not args.dry_run and any(r["action"] == "dispatched" for r in rows):
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
            "          carries an attestation for its CURRENT head, whose "
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
    if not args.dry_run:
        refresh_run_logs(q)
    return 0


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
#       without a daemon. A fenced block is REWRITTEN in place, never appended
#       to: `collect` runs many times over one run, and a line appended per
#       pass is a timeline nobody reads, which is this failure relocated rather
#       than fixed. The block is a pure function of the records, so a refresh
#       that changes nothing writes nothing and says nothing.
#   AND EVERYTHING OUTSIDE THE FENCE IS THE LEAD'S. The goal in its own words,
#       the decisions, what went wrong, the outcome — none of that can be
#       generated from records, and it is why the file exists. Nothing here
#       reads it, nothing here writes it, and a log whose fence has been
#       removed is a log the lead has taken over: it is reported and left
#       exactly as it is.

RUNS_DIR = os.path.join("orchestration", "runs")
RUN_TEMPLATE = "_TEMPLATE.md"

# The fence, as a literal pair, so preserving what surrounds it is a string
# search and not a parse of someone's prose.
FACTS_BEGIN = "<!-- fleet:facts -->"
FACTS_END = "<!-- fleet:facts:end -->"

# A run is over when the queue has nothing left to do for it. `done` is not
# here: that task's pull request is open, which is the middle of a run.
RUN_CLOSED = ("landed", "abandoned", "stuck", "failed")


def runs_root() -> str:
    """Where run logs are written — FLEET_RUNS_DIR, as FLEET_QUEUE_DIR is.

    A harness pointing the queue at a throwaway directory has to be able to
    point the logs somewhere throwaway too, or every selftest run scaffolds
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
        d = t.doc
        where = f" on `{d['host']}`" if d.get("host") else ""
        session = d.get("session") or (d.get("reaped") or {}).get("session")
        profile = d.get("profile") or "default"
        if d.get("dispatched_at"):
            out.append((d["dispatched_at"], f"dispatched `{t.id}`{where} to session "
                                            f"`{session}` on profile `{profile}`"))
        if d.get("concluded_at"):
            check = (d.get("artifact_check") or {}).get("verdict") or ""
            said = f"`{t.id}` concluded `{d.get('outcome')}`"
            if d.get("artifact"):
                said += f" — {cell(d['artifact'])}"
            out.append((d["concluded_at"], said + (f" [pipeline {check}]" if check else "")))
        landing = d.get("landing") or {}
        if landing.get("at") and landing.get("state") in LANDED_STATE:
            out.append((landing["at"], f"`{t.id}` {landing['state']} — {landing.get('detail', '')}"))
        reaped = d.get("reaped") or {}
        if reaped.get("at"):
            out.append((reaped["at"], f"released `{t.id}`'s session "
                                      f"`{reaped.get('session')}` ({reaped.get('how')})"))
        shep = d.get("shepherd") or {}
        if shep.get("at"):
            said = (f"shepherd: `{t.id}`'s pull request is "
                    f"`{shep.get('condition')}`")
            if shep.get("session"):
                said += f" — fixer `{shep['session']}`"
            out.append((shep["at"], said))
    return sorted(out, key=lambda e: e[0])


def run_facts(q: Queue, slug: str) -> str:
    """The fenced block, rendered from records and from nothing else."""
    topic = q.topics.get(slug, {})
    tasks = q.by_topic().get(slug, [])
    profiles = sorted({t.doc.get("profile") or "default" for t in tasks})

    lines = [
        FACTS_BEGIN,
        "",
        "<!-- Generated from the queue's records by `./scripts/queue.sh`, and",
        "     rewritten in place every time it runs. Write nothing in here;",
        "     everything outside this fence is yours and is never touched. -->",
        "",
        f"- **Topic.** `{slug}` — {topic.get('title', '')}",
        f"- **Prompt.** `{os.path.join(os.path.abspath(queue_root()), slug, 'PROMPT.md')}`",
        f"- **Opened.** {topic.get('created', '—')} · **Status.** {run_status(tasks)}"
        f" · **Profile(s).** {', '.join(f'`{p}`' for p in profiles) or '—'}",
        "",
    ]

    if tasks:
        lines += [
            "| Task | Where it runs | Branch | State | Artifact |",
            "|---|---|---|---|---|",
        ]
        for t in tasks:
            d = t.doc
            state = "waiting" if t.state == "queued" and not q.is_ready(t) else t.state
            lines.append(
                f"| `{t.id}` — {cell(d.get('title'))} | `{cell(where_it_runs(t))}` "
                f"| `{cell(d.get('branch'))}` | {state} | {cell(d.get('artifact'))} |"
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
        for o in q.overlaps(tasks):
            refs = ", ".join(f"`{r.split('/')[-1]}`" for r in o["tasks"])
            lines += ["", f"- **Overlap on `{o['touches']}`** — {refs}. A risk that "
                          "was reported and accepted, never a reason to wait."]
    else:
        lines.append("No tasks yet.")

    events = run_events(tasks)
    if events:
        lines += ["", "### Timeline", ""]
        lines += [f"- `{at}` — {what}" for at, what in events]

    lines += ["", FACTS_END]
    return "\n".join(lines)


def refresh_run_log(q: Queue, slug: str) -> tuple[str, str]:
    """Write this topic's run log. Returns (path, what happened) — "" for nothing.

    The three outcomes that matter: it did not exist and now does, it existed
    and its facts moved on, or someone removed the fence and it is theirs now.
    """
    path = run_log_path(slug, q.topics.get(slug, {}))
    old = ""
    if os.path.exists(path):
        with open(path) as fh:
            old = fh.read()
        verb = "updated"
    else:
        try:
            with open(run_template_path()) as fh:
                old = fh.read()
        except OSError as exc:
            return path, f"not scaffolded — {exc}"
        old = old.replace("<YYYY-MM-DD>", os.path.basename(path)[:10]).replace("<slug>", slug)
        verb = "opened"

    if FACTS_BEGIN not in old or FACTS_END not in old:
        return path, "left alone — no generated block in it"

    head, _, rest = old.partition(FACTS_BEGIN)
    _, _, tail = rest.partition(FACTS_END)
    new = head + run_facts(q, slug) + tail
    if new == old:
        return path, ""

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(new)
    os.replace(tmp, path)
    return path, verb


def refresh_run_logs(q: Queue, only: str | None = None, always: bool = False) -> None:
    """Refresh every topic's log and say only what changed.

    Silence when nothing moved is the point: a loop that prints a line per
    command per topic is the noise this was supposed to remove.
    """
    for slug in sorted(q.topics):
        if only and slug != only:
            continue
        path, note = refresh_run_log(q, slug)
        if note or always:
            print(f"    run log {note or 'unchanged'}: {path}")


def cmd_run(args) -> int:
    """The refresh, made explicit — for a topic older than this and for the path."""
    q = Queue(queue_root())
    if args.topic and args.topic not in q.topics:
        raise QueueError(f"no such topic: {args.topic}")
    if not q.topics:
        print("run: no topics, so no runs")
        return 0
    refresh_run_logs(q, only=args.topic, always=True)
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
    last. Nothing else on the record is touched.
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
    task.save()
    return entry


def last_transition(task: Task) -> tuple[str | None, str]:
    """The newest transition in progress.jsonl, by the event's own time.

    `at` is when thurbox says the session changed state; `observed` is when
    `watch` folded it in. The first is what answers "has this worker moved",
    so a fold that happens to run after the message cannot manufacture
    movement that predates it.
    """
    path = task.file("progress.jsonl")
    if not os.path.exists(path):
        return None, ""
    try:
        rows = open(path).read().splitlines()
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
            newest = (when, str(stamp))
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
    for row in rows:
        word = MOVED_WORD[row["source"]]
        if row["status"] == "moved":
            row["line"] = f"{word} {age_of(row['at'])} ago" if row["at"] else f"{word} since"
        elif row["status"] == "still":
            row["line"] = f"no {row['source']} since"
        else:
            row["line"] = f"{row['source']} not checked: {row['why']}"

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
            "`queue.sh dispatch` starts one; `queue.sh attach <ref> <uuid>` records "
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
            "    Recorded. `queue.sh list` and `queue.sh show` now answer whether "
            "anything\n    has moved since, without opening a worktree."
        )
    else:
        print(f"    nothing was typed into that session: {report}", file=sys.stderr)
    return 0 if ok else 1


# --- read-only views ---------------------------------------------------------


def where_it_runs(task: Task) -> str:
    """`repo` for a local task; `host:repo` for one that runs somewhere else.

    One string in every view, in the ssh spelling the operator already types,
    so a path that is not on this machine can never be read as one that is.
    """
    host = task.doc.get("host")
    return f"{host}:{task.doc['repo']}" if host else str(task.doc["repo"])


def cmd_list(args) -> int:
    root = queue_root()
    # The first line answers "which queue am I looking at?" without being asked.
    # `queue.sh root` prints the same path, so the two can never disagree
    # silently about what they are showing.
    print(f"queue: {os.path.abspath(root)}")
    # Named explicitly, so a topic stays reachable BY NAME however it is
    # flagged: `list --topic <archived>` is a request for that topic, not a
    # request for the default view narrowed to it.
    scope = "all" if (args.all_topics or args.topic) else (
        "archived" if args.archived else "live"
    )
    q = Queue(root, scope=scope)
    grouped = q.by_topic()
    if args.topic:
        grouped = {k: v for k, v in grouped.items() if k == args.topic}
    if not grouped:
        print("queue: empty")
        hidden(q)
        return 0
    for topic, tasks in sorted(grouped.items()):
        meta = q.topics.get(topic, {})
        flag = "  [archived]" if meta.get("archived") else ""
        print(f"{topic} — {meta.get('title', '')}{flag}")
        for t in tasks:
            mark = "waiting" if t.state == "queued" and not q.is_ready(t) else t.state
            extra = t.doc.get("artifact") or t.doc.get("session") or ""
            # The publish state, with the age of the look that produced it —
            # the same field the pane draws, so the two views cannot
            # disagree about what was last seen.
            pub = t.doc.get("publish") or {}
            if pub.get("state"):
                extra = f"{extra}  {pub['state']} {age_of(pub.get('at'))}".strip()
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
        print()
    hidden(q)
    return 0


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


def cmd_show(args) -> int:
    q = Queue(queue_root())
    task = q.get(args.ref)
    d = task.doc
    print(f"{task.ref} — {d['title']}")
    for key in ("state", "repo", "host", "branch", "base", "agent", "profile", "session",
                "prompted", "outcome", "artifact"):
        print(f"    {key + ':':<12} {d.get(key)}")
    remote = d.get("remote") or {}
    if remote.get("worktree"):
        # Named in full because it is the only place the worker's actual
        # filesystem appears: the brief it reads and the result that closes
        # this task are both files on that machine.
        print(f"    {'remote:':<12} {remote.get('destination')}:{remote['worktree']}")
    method, how = task_publish(task)
    print(f"    {'publish:':<12} {method}{f' — {how}' if how else ''}")
    check = d.get("artifact_check") or {}
    if check.get("verdict"):
        print(f"    {'checked:':<12} {check['verdict']} — {check.get('detail', '')}")
    pub = d.get("publish") or {}
    if pub.get("state"):
        print(
            f"    {'published:':<12} {pub['state']} — {pub.get('detail', '')} "
            f"({pub.get('by', '')}, {age_of(pub.get('at'))} ago)"
        )
    live = liveness(task)
    if live:
        print(f"    {'messaged:':<12} {live['line']}")
        for row in live["movement"]:
            print(f"    {'':<12}   {row['line']}")
        if live["status"] in ("still", "unknown"):
            print(f"    {'':<12} {LIVENESS_CAVEAT}")
    landing = d.get("landing") or {}
    if landing.get("state"):
        print(f"    {'landing:':<12} {landing['state']} — {landing.get('detail', '')}")
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
    if task.touches:
        print(f"    {'touches:':<12} {', '.join(task.touches)}")
    rec = d.get("shepherd")
    if rec:
        print(f"    {'shepherd:':<12} {rec.get('condition')} — fixer {rec.get('session')} "
              f"sent {rec.get('at')}")
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
    print(f"    {'brief:':<12} {task.file('BRIEF.md')}")
    progress = task.file("progress.jsonl")
    if os.path.exists(progress):
        lines = open(progress).read().splitlines()
        print(f"    {'progress:':<12} {len(lines)} transition(s), last: {lines[-1] if lines else '-'}")
    if os.path.exists(task.file("result.md")):
        print(f"    {'result:':<12} {task.file('result.md')}")
    return 0


def cmd_check(args) -> int:
    root = os.path.abspath(queue_root())
    if not os.path.isdir(root):
        print(f"queue check: ok — {root} not created yet, nothing to validate")
        return 0

    # `all`: a record does not stop being a record because its topic left the
    # default view, and a check that only validated what is on screen would go
    # quiet about exactly the records nobody is looking at.
    q = Queue(root, scope="all")
    problems = []
    for slug, meta in sorted(q.topics.items()):
        at = meta.get("archived")
        if at is not None and not isinstance(at, str):
            problems.append(f"{slug}: archived {at!r} is not a timestamp")
            continue
        # An archived topic holding a live task is the one way this flag can
        # be actively harmful: it is work nothing draws. Nothing fleet does
        # produces one — `add` clears the flag — but a hand-edited topic.yaml
        # can, and this is the check that says so out loud.
        held = unfinished(q.by_topic().get(slug) or []) if at else None
        if held:
            problems.append(
                f"{slug}: archived while {held.ref} is `{held.state}` — "
                "run `queue.sh unarchive` on it"
            )
    for ref, t in sorted(q.tasks.items()):
        d = t.doc
        for key in ("id", "topic", "title", "state", "repo", "branch"):
            if not d.get(key):
                problems.append(f"{ref}: missing {key}")
        pub = d.get("publish") or {}
        if "method" in pub and pub["method"] not in PUBLISH_METHODS:
            problems.append(
                f"{ref}: publish method {pub['method']!r} is not one of "
                + ", ".join(sorted(PUBLISH_METHODS))
            )
        # The ONLY thing ever asked of `how`. It names a tool fleet does not
        # know, so "it is text" is the whole contract — anything more is fleet
        # deciding which tools exist.
        if pub.get("how") is not None and not isinstance(pub["how"], str):
            problems.append(f"{ref}: publish how {pub['how']!r} is not text")
        if d.get("state") not in STATES:
            problems.append(f"{ref}: state {d.get('state')!r} is not one of {', '.join(STATES)}")
        if d.get("id") != t.id:
            problems.append(f"{ref}: id {d.get('id')!r} disagrees with its directory")
        for b in t.blockers:
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
        if not os.path.exists(t.file("BRIEF.md")):
            problems.append(f"{ref}: no BRIEF.md")
        # The host name only — never whether that host exists. A queue is read
        # on machines that are not the one it dispatches from, and a check that
        # asked thurbox would report a perfectly good record as broken there.
        if d.get("host") is not None and not isinstance(d.get("host"), str):
            problems.append(f"{ref}: host {d.get('host')!r} is not a name")
        if (d.get("remote") or {}) and not d.get("host"):
            problems.append(f"{ref}: carries a remote worktree but names no host")

    cycle = find_cycle(q)
    if cycle:
        problems.append("blocker cycle: " + " -> ".join(cycle))

    for line in problems:
        print(f"    {line}", file=sys.stderr)
    if problems:
        print(f"queue check: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"queue check: ok — {len(q.topics)} topic(s), {len(q.tasks)} task(s) in {root}")
    return 0


def cmd_root(args) -> int:
    """The resolved queue root, absolute, and nothing else — for scripts.

    `--foreign` asks the other half of the same question: is THIS checkout
    provably not the control plane? It prints the control plane's path and
    exits 0 when it is, and prints nothing and exits 1 otherwise — so a caller
    can branch on the exit status without parsing anything. `scripts/
    reconcile.sh` is why it exists: that loop runs `collect`, which closes
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
    p = argparse.ArgumentParser(prog="queue.sh", add_help=True)
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("topic", help="open a topic from a prompt")
    tsub = t.add_subparsers(dest="topic_cmd", required=True)
    ta = tsub.add_parser("add")
    ta.add_argument("slug")
    ta.add_argument("--title")
    ta.add_argument("--prompt")
    ta.add_argument("--prompt-file")
    ta.set_defaults(func=cmd_topic_add, creates=True)

    a = sub.add_parser("add", help="add a task to a topic")
    a.add_argument("topic")
    a.add_argument("slug")
    a.add_argument("--title")
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
        choices=sorted(PUBLISH_METHODS),
        help="what this task must PRODUCE. Defaults to the publish block in "
        "POLICY.md's frontmatter, and to `pr` when there is none",
    )
    a.add_argument(
        "--how",
        help="the tool the worker should publish with, in your own words "
        "(\"run `/publish`\", \"use `make release`\"). Free text: it is rendered "
        "into the brief and nothing ever parses it, which is what lets it name "
        "a tool fleet knows nothing about",
    )
    a.add_argument("--touches", help="comma-separated paths this task expects to change")
    a.add_argument("--brief-file")
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

    d = sub.add_parser("dispatch", help="launch every ready task at once")
    d.add_argument(
        "ref",
        nargs="*",
        help="the tasks to launch. NO REF IS THE NORM and sends the whole ready "
        "set at once; naming refs holds the rest back for this run only and "
        "records nothing, so use it when the operator has not authorized a task "
        "yet — never to drip-feed a queue, which is slower than no queue",
    )
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
        action="store_true",
        help="close a task whose artifact does not prove it published, after "
        "you have read that artifact and judged it good anyway",
    )
    c.set_defaults(func=cmd_collect)

    rp = sub.add_parser("reap", help="release the sessions of tasks whose work has landed")
    rp.add_argument(
        "--dry-run",
        action="store_true",
        help="say what would land and what would be released, and write nothing",
    )
    rp.set_defaults(func=cmd_reap)

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

    rn = sub.add_parser("run", help="refresh the run log this topic writes into")
    rn.add_argument("topic", nargs="?", help="one topic; every one by default")
    rn.set_defaults(func=cmd_run)

    li = sub.add_parser("list", help="one line per task, grouped by topic")
    li.add_argument("--topic", help="one topic, archived or not")
    li.add_argument("--archived", action="store_true",
                    help="only the archived topics, which the default view hides")
    li.add_argument("--all", action="store_true", dest="all_topics",
                    help="archived topics as well as live ones")
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
