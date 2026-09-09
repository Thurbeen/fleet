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

# The four conditions that justify making one task wait for another. They are
# firstmate's, and they are a closed set on purpose: "these edit the same file"
# is not among them and cannot be spelled here.
BLOCKER_KINDS = {
    "semantic-dependency": "this task consumes something the other one introduces",
    "shared-external-state": "both mutate the same external state",
    "incompatible-migration": "the two migrations cannot be in flight together",
    "other": "another concrete condition that makes independent progress unsafe",
}

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

# HOW A TASK PUBLISHES, as three words about the ARTIFACT it leaves behind —
# and the ONE place each is written down. `render_brief` writes `brief` into
# the worker's instructions, `publish_verdict` goes and looks for `artifact`,
# and `report_unverified` and `cmd_show` quote `proof` when it does not hold.
#
# WHY A CHECK AT ALL. "Open the pull request by running `/no-mistakes --yes`"
# is an instruction about a METHOD, and a method leaves no trace: a worker that
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
PUBLISH_DEFAULT = "pr"

PUBLISH_METHODS = {
    "no-mistakes": {
        "brief": (
            "open a pull request through the `no-mistakes` pipeline, which is "
            "the review, the tests, the lint, the push and the pull request in "
            "one pass"
        ),
        "artifact": "that pull request's URL",
        "proof": (
            "the pull request is from this task's branch and its body carries a "
            "`no-mistakes` attestation for the commit that would merge"
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

# A pull request URL, and nothing else — group 1 is the canonical form, so a
# link someone pasted with `/files` or a `#comment` on the end still resolves
# to the pull request it names. `not-applicable` and `stuck` produce no
# artifact at all, and an artifact that is not a PR (an issue, a doc, a commit)
# is not a pipeline claim — neither is a failure, and neither is checked.
PR_URL_RE = re.compile(r"^(https?://[^/\s]+/[^/\s]+/[^/\s]+/pull/\d+)(?:[/?#].*)?$")

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


def queue_root() -> str:
    """Where the queue lives, as an absolute path.

    FLEET_QUEUE_DIR is honoured verbatim and never second-guessed — webui.sh,
    the selftests and anyone pointing a harness at a temp directory rely on
    that. Otherwise the queue belongs to this CHECKOUT, not to the process cwd.
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


def policy_publish_default() -> tuple[str, str | None]:
    """The operator's own publish default, from POLICY.md's YAML frontmatter.

        ---
        publish:
          method: no-mistakes
          how: run `/no-mistakes --yes`
        ---

    Here, and deliberately not in OPERATOR.md, whose own example file says in
    bold that it is prose and that nothing parses it. POLICY.md is fleet's
    standing policy, it is tracked, it is already the file that says how a
    worker publishes, and every brief already points at it — so a change to
    this default is reviewable in a diff rather than a surprise in a record.

    It exists because the alternative is the lead retyping `--publish` on every
    task, and a forgotten flag would silently downgrade that task's
    verification, which is the failure this whole subsystem exists to prevent.

    No frontmatter is the SHIPPED state and answers `pr` with no `how`: a fresh
    clone needs no configuration at all. A word that is not a method is refused
    rather than ignored, for the same reason — ignoring it would downgrade
    quietly.
    """
    try:
        with open(policy_path()) as fh:
            text = fh.read()
    except OSError:
        return PUBLISH_DEFAULT, None

    block: dict = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            try:
                doc = yaml.safe_load(parts[1])
            except yaml.YAMLError as exc:
                raise QueueError(f"{policy_path()}: its frontmatter is not YAML: {exc}")
            if isinstance(doc, dict) and "publish" in doc:
                pub = doc["publish"]
                if not isinstance(pub, dict):
                    raise QueueError(
                        f"{policy_path()}: publish is {pub!r}, and must be a "
                        "mapping with method/how, not a bare value"
                    )
                block = pub

    method = str(block.get("method") or "").strip() or PUBLISH_DEFAULT
    if method not in PUBLISH_METHODS:
        raise QueueError(
            f"{policy_path()}: publish.method is {method!r}, and the methods are "
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
# wrote records the monitor was right to not show, and nothing said a word.
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
        "  Records written here are invisible to the monitor and to the lead.\n"
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
        "       records here are invisible to its monitor and to its lead.",
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
# a kind from a closed set plus a reason. Overlapping files are recorded under
# `touches` instead, where they are reported as a risk and hold nothing up.
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
# screenfuls on every list, every status line, every monitor poll and every
# pane refresh.
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
# `queue.sh list`, `queue.sh plan`, `fleet-status.sh` and the monitor all show
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

    `cleared` is kept as a field of its own because `is_ready` asks exactly
    that question and nothing else.
    """
    ref = blocker.get("task")
    try:
        upstream = q.get(ref).state
    except QueueError:
        upstream = None

    if task.state in CONCLUDED_STATES:
        status = "moot"
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
    """
    what = f"{view['kind']} on {view['task']}"
    why = view["why"] or "no reason recorded"
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
# derived on the spot (webui.py's `classify`), and this could have been too.
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
    print(args.slug)
    return 0


def next_number(tpath: str) -> str:
    used = [
        int(n[:2])
        for n in os.listdir(tpath)
        if os.path.isdir(os.path.join(tpath, n)) and is_record_dir(n) and n[:2].isdigit()
    ]
    return f"{max(used) + 1 if used else 1:02d}"


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

    # Resolution, first hit wins and per FIELD. A stated method with no stated
    # tool drops the operator's global one rather than inheriting it: "run
    # `/no-mistakes --yes`" is the wrong sentence to hand a `push` task. A
    # stated `how` alone keeps the operator's method, because a lead adding a
    # note about the tool must not be able to downgrade the check by accident.
    method, how = policy_publish_default()
    if args.publish:
        method, how = args.publish, args.how
    elif args.how:
        how = args.how

    os.makedirs(path)

    doc = {
        "id": tid,
        "topic": args.topic,
        "title": args.title or args.slug.replace("-", " "),
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
    task.save()

    brief = open(args.brief_file).read() if args.brief_file else None
    with open(task.file("BRIEF.md"), "w") as fh:
        fh.write(render_brief(task, read_yaml(os.path.join(tpath, "topic.yaml")), brief))
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
    not structure. `--brief-file` fills the first section and leaves the rest
    for the lead, so a body handed in on the command line still gets the same
    skeleton and the same refusal.

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
    filled = {BRIEF_SECTIONS[0]: body.strip()} if body and body.strip() else {}
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


def cmd_block(args) -> int:
    q = Queue(queue_root())
    task = q.get(args.ref)
    target = q.get(args.on)

    if args.clear:
        before = len(task.blockers)
        task.doc["blocked_by"] = [b for b in task.blockers if b["task"] != target.ref]
        task.save()
        print(f"{task.ref}: {before - len(task.doc['blocked_by'])} blocker(s) cleared")
        return 0

    if args.kind not in BLOCKER_KINDS or not (args.why or "").strip():
        kinds = "\n".join(f"    {k:<24} {v}" for k, v in BLOCKER_KINDS.items())
        raise QueueError(
            "a blocker needs --kind and --why, because it is the one thing that\n"
            "makes work wait and it has to survive the next planning pass.\n"
            f"--kind is one of:\n{kinds}\n"
            "Overlapping files are not on that list. Record them with `add --touches`;\n"
            "they are reported as a risk beside the ready set and hold nothing up."
        )
    if target.ref == task.ref:
        raise QueueError(f"{task.ref} cannot block itself")

    task.doc.setdefault("blocked_by", [])
    task.doc["blocked_by"] = [b for b in task.blockers if b["task"] != target.ref]
    task.doc["blocked_by"].append(
        {"task": target.ref, "kind": args.kind, "why": args.why.strip(), "recorded": now()}
    )
    task.save()

    cycle = find_cycle(Queue(queue_root()))
    if cycle:
        task.doc["blocked_by"] = [b for b in task.blockers if b["task"] != target.ref]
        task.save()
        raise QueueError("that blocker closes a cycle: " + " -> ".join(cycle))

    print(f"{task.ref} waits on {target.ref} ({args.kind})")
    return 0


def find_cycle(q: Queue) -> list | None:
    colour: dict[str, int] = {}
    stack: list[str] = []

    def walk(ref: str) -> list | None:
        colour[ref] = 1
        stack.append(ref)
        for b in q.tasks[ref].blockers:
            nxt = b["task"]
            if nxt not in q.tasks:
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
# and the monitor would each have to learn the difference. ssh confines that
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


def first_line(proc) -> str:
    """The one line of an ssh failure worth reporting, stderr before stdout."""
    for stream in (proc.stderr, proc.stdout):
        lines = [ln.strip() for ln in (stream or "").splitlines() if ln.strip()]
        if lines:
            return lines[-1]
    return ""


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


def ssh_run(entry: dict, script: str, stdin: str | None = None):
    """One POSIX shell command on the host. Never raises; the caller reads it."""
    try:
        return subprocess.run(
            ssh_argv(entry) + [script],
            input=stdin, capture_output=True, text=True, timeout=SSH_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(
            args=[], returncode=SSH_CONNECTION_FAILED, stdout="", stderr=str(exc)
        )


# What probe 2 accepts, and why it is two questions and not one. §1a names
# `ssh -T git@github.com`, which proves an SSH key. A host that clones over
# HTTPS with a `gh` token has no such key and is perfectly able to push, so
# testing only the key would refuse a working host. Either credential passes;
# neither is read, moved, or reported beyond the word that says which was found.
FORGE_PROBE = """\
if ssh -o BatchMode=yes -T git@github.com 2>&1 | grep -q 'successfully authenticated'; then
	printf 'an ssh key'
	exit 0
fi
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
	printf 'a gh token'
	exit 0
fi
exit 1
"""


def probe_host(entry: dict, repo: str) -> list:
    """§1a's questions, in §1a's order, stopping at the first NO.

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

    forge = ssh_run(entry, FORGE_PROBE)
    if forge.returncode != 0:
        out.append({"check": "forge", "ok": False, "detail": (
            "the host has no GitHub credentials of its own — neither an ssh key "
            "nor a `gh` login. It cannot clone, fetch or push. Give that MACHINE "
            "its own credentials; nothing here sends yours.")})
        return out
    out.append({"check": "forge", "ok": True,
                "detail": f"reaches GitHub with {forge.stdout.strip() or 'a credential'}"})

    quoted = shlex.quote(repo)
    check = (
        f"if [ ! -d {quoted} ]; then printf no-dir; exit 1; fi\n"
        f"if [ ! -e {quoted}/.git ]; then printf no-git; exit 1; fi\n"
        "printf ok\n"
    )
    seen = ssh_run(entry, check)
    if seen.returncode != 0:
        why = {
            "no-dir": f"{repo} does not exist on that host",
            "no-git": f"{repo} exists on that host but is not a git checkout",
        }.get(seen.stdout.strip(), first_line(seen) or "could not be checked")
        out.append({"check": "repo", "ok": False, "detail": (
            f"{why}. `--repo` is a path on the HOST for a remote task, and "
            "nothing local validates it.")})
        return out
    out.append({"check": "repo", "ok": True, "detail": f"{repo} is a git checkout there"})
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
    proc = ssh_run(entry, f"cat > {shlex.quote(dest)}", stdin=text)
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
    proc = ssh_run(entry, f"cat {shlex.quote(src)}")
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
    """A brief that is missing reads as unwritten, which stops the dispatch."""
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
        "--name", d["title"][:64],
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
        create += ["--agent", d.get("agent") or "claude"]
    parent = os.environ.get("THURBOX_SESSION")
    if parent:
        create += ["--parent", parent]
    create += flags + ["--json"]
    send = f"Read {brief_target(task)} and do what it says."
    return create, send


def shell_quote(argv: list) -> str:
    return " ".join(shlex.quote(a) for a in argv)


def cmd_dispatch(args) -> int:
    q = Queue(queue_root())
    ready = q.ready()

    unfilled = [t for t in ready if BRIEF_PLACEHOLDER in read_text(t.file("BRIEF.md"))]
    if unfilled:
        raise QueueError(
            "these tasks still carry an unwritten BRIEF.md, and a worker sent one\n"
            "would have nothing to do:\n"
            + "\n".join(f"    {t.file('BRIEF.md')}" for t in unfilled)
        )
    if not ready:
        print("dispatch: nothing ready")
        return 0

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
                print("        reachable and a POSIX shell / has its own GitHub"
                      " credentials / the repo is there")
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
        try:
            out = subprocess.run(create, capture_output=True, check=True).stdout
            session = json.loads(out)["id"]
        except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as exc:
            detail = getattr(exc, "stderr", b"") or b""
            print(f"    {t.ref}: spawn failed: {detail.decode().strip() or exc}", file=sys.stderr)
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
    fleet's: this repo's frontmatter says `no-mistakes`, so every task already
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
        unknown   the check could not run — no `gh`, no network, no such pull
                  request, a base branch this machine cannot read.

    `unknown` is a fourth word on purpose and never collapses into `passed` or
    `missing`. An offline machine and a CI runner with no `gh` must both still
    be able to collect, and "could not check" must never be reported as either
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
    arrives in the same `gh pr view`. It closes the one hole no body check ever
    closed: a worker pasting somebody ELSE's good pull request. "This pull
    request is from this task's branch" is the one claim about it that a worker
    cannot write into its own result.md.

    `no-mistakes` then asks for the attestation rather than for headings in the
    prose. Same class of evidence — the tool's own trace in the body — and
    strictly stronger, because it names the head commit the pipeline ran on and
    a stale one is refused. It is also the check the shepherd already makes, so
    the two commands now agree about what proves a pipeline ran.
    """
    match = PR_URL_RE.match((url or "").strip())
    if not match:
        if outcome == "shipped":
            return "missing", "shipped with no pull request to check"
        return "skipped", "no pull request to check"
    url = match.group(1)
    if not shutil.which("gh"):
        return "unknown", "gh not found on PATH"
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", url, "--json", "body,headRefOid,headRefName,state"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "unknown", f"gh pr view could not run: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return "unknown", "gh pr view failed: " + (detail[-1] if detail else "no output")
    try:
        pr = json.loads(proc.stdout)
    except ValueError:
        return "unknown", "gh pr view did not answer with JSON"

    branch = str(task.doc.get("branch") or "")
    head = str(pr.get("headRefName") or "")
    if not head:
        return "unknown", "GitHub did not say which branch this pull request is from"
    if head != branch:
        return "missing", (
            f"the pull request is from branch {head}, and this task's is {branch}"
        )

    if method == "no-mistakes":
        attested, why = attestation_verdict(pr.get("body"), pr.get("headRefOid") or "")
        return ("passed" if attested else "missing"), why

    state = str(pr.get("state") or "").upper()
    if state not in ("OPEN", "MERGED"):
        return "missing", f"the pull request is {state.lower() or 'in no state gh named'}"
    return "passed", f"the pull request is {state.lower()} and is from {branch}"


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
    """
    block = dict(task.doc.get("publish") or {})
    block.update({"state": state, "detail": detail, "at": now(), "by": by})
    task.doc["publish"] = block


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
# `done`, discovered by asking `gh`, never by a worker claiming it.

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
        unknown   the forge could not be asked — no `gh`, no network, no such
                  pull request. It never collapses into any of the others, for
                  the same reason `collect`'s pipeline check has a fourth word:
                  a timeout must not be able to manufacture a merge, and a
                  merge is what authorises a deletion.
    """
    match = PR_URL_RE.match((artifact or "").strip())
    if not match:
        return "none", "no pull request to wait for"
    url = match.group(1)
    if not shutil.which("gh"):
        return "unknown", "gh not found on PATH"
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", url, "--json", "state", "-q", ".state"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "unknown", f"gh pr view could not run: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return "unknown", "gh pr view failed: " + (detail[-1] if detail else "no output")

    state = proc.stdout.strip().upper()
    if state == "MERGED":
        return "merged", f"{url} is merged"
    if state == "CLOSED":
        return "closed", f"{url} was closed without merging"
    if state == "OPEN":
        return "open", f"{url} is still open — work awaiting review"
    return "unknown", f"gh answered an unrecognised pull request state: {state!r}"


def sweep_landings(q: Queue, dry: bool) -> dict:
    """Ask the forge about every `done` task and promote the ones that landed.

    Works from the RECORD and `gh` alone. That is a requirement, not an
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

# The agent's own limit line, as observed on 2026-09-08 in this machine's
# transcripts and on the pane that renders them:
#
#     You've hit your session limit · resets 11:30pm (Europe/Paris)
#
# Matched on the sentence and not the whole line, so the window's name and the
# reset time can vary. Nothing is matched that was not observed: an agent whose
# banner reads differently answers `quiet` here and is reported as such rather
# than guessed at.
LIMIT_BANNER_RE = re.compile(r"you'?ve hit your \w+ limit", re.I)

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
# one edit, and the lead's gauge and this command can never disagree about how
# much fuel there is.
#
# Loaded by path and LAZILY, because fleet_status.py imports this file: at
# import time that is a cycle, and inside the one function that needs it, it is
# not.
QUOTA_CMD = "quota-axi"

# The provider that gauge reads, and so the only agent whose account this can
# speak for. A task running something else is not refused — its account window
# is undetermined, and undetermined restarts nothing.
FUEL_AGENT = "claude"


def fuel_gauge():
    """The `fleet_status` module, loaded from beside this file."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fleet_status.py")
    spec = importlib.util.spec_from_file_location("fleet_status_for_queue", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def account_fuel() -> tuple[str, str]:
    """('fuel' | 'spent' | 'unknown', detail) for the account every session spends.

    `effectivePercentRemaining` is the subscription window the lead and every
    worker draw on at once — six workers dispatched together spend one window
    six ways — so this is ONE reading for the whole pass and never a per-session
    one. There is no per-session number anywhere: `session get --json` carries
    no token, usage, cost or limit field at all.
    """
    try:
        sec = fuel_gauge().probe_fuel()
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


def transcript_root() -> str:
    """Where Claude Code keeps its transcripts. `CLAUDE_CONFIG_DIR` is its own
    knob for moving them, so this reads that rather than assuming a home."""
    home = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return os.path.join(home, "projects")


def transcript_file(agent_sid: str) -> str:
    """The agent's own transcript, named by the session id thurbox records."""
    root = transcript_root()
    hits = glob.glob(os.path.join(root, "*", f"{agent_sid}.jsonl"))
    if not hits:
        hits = glob.glob(os.path.join(root, "**", f"{agent_sid}.jsonl"), recursive=True)
    return hits[0] if hits else ""


def transcript_exhaustion(agent_sid: str) -> tuple[str, str]:
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
    path = transcript_file(agent_sid)
    if not path:
        return "unknown", f"no transcript for {agent_sid} under {transcript_root()}"
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


def pane_exhaustion(sid: str) -> tuple[str, str]:
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
    lines = [ln.strip() for ln in str(doc.get("output") or "").splitlines() if ln.strip()]
    for line in lines[-PANE_TAIL_LINES:]:
        if LIMIT_BANNER_RE.search(line):
            return "exhausted", f"the pane ends on the agent's own banner: {line}"
    return "quiet", f"no limit banner in the pane's last {PANE_TAIL_LINES} lines"


def exhaustion(doc: dict) -> tuple[str, str]:
    """('exhausted' | 'quiet' | 'undetermined', detail) for one live session.

    The transcript outranks the pane wherever it can be read: it is the same
    event, recorded rather than rendered, and it says which window rejected the
    turn. The pane is what answers for an agent that keeps no transcript here.
    """
    seen, detail = transcript_exhaustion(doc.get("agent_session_id") or "")
    if seen != "unknown":
        return seen, detail
    pane, pane_detail = pane_exhaustion(str(doc.get("id") or ""))
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
    verdict, detail = account_fuel()
    print(f"    account {FUEL_AGENT:<10} "
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
        agent = task.doc.get("agent") or FUEL_AGENT
        if agent != FUEL_AGENT:
            print(f"    {task.ref:<46} undetermined  the fuel gauge reads the "
                  f"{FUEL_AGENT} account and this task runs `{agent}`")
            kept += 1
            continue
        if verdict != "fuel":
            # The reason is the account line above, printed once: repeating a
            # hundred characters of it per task buries the one thing a reader
            # is looking for, which is which tasks it applies to.
            word = "kept" if verdict == "spent" else "undetermined"
            print(f"    {task.ref:<46} {word:<13} the {FUEL_AGENT} account window is "
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
# contract is "read a file, close a task" makes `collect` fail when gh is down,
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
#                No gh, no network, no thurbox: say what could not be
#                determined and carry on. Spawning a fixer for a healthy PR is
#                the one failure that costs more than the bug.
#   Never touch  A pull request whose head branch lives in someone else's
#   a stranger.  fork is reported and left alone — never merged, never handed
#                a fixer — because a stranger cannot create a branch inside
#                this repository, so that is the one claim a pull request
#                cannot make for itself.

# GitHub-specific and deliberately NOT the forge-agnostic PR_URL_RE above: this
# one exists to name `owner/repo`, which is what AUTO_MERGE_REPOS is checked
# against. Defining a second `PR_URL_RE` here shadowed that one and broke the
# pipeline check; the two answer different questions and keep different names.
GH_PR_URL_RE = re.compile(r"^https://github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)/?$")


# One `gh pr list` answers everything below, so a pass costs one call per
# repository rather than one per pull request. The last four are the safety
# fields: `headRefOid` is what an attestation has to name, and the other three
# are how a fork's pull request is told from ours.
GH_PR_FIELDS = (
    "number,state,url,title,isDraft,mergeable,reviewDecision,"
    "statusCheckRollup,body,headRefName,baseRefName,headRefOid,"
    "author,headRepositoryOwner,isCrossRepository"
)

# `gh pr list --limit` is a request cap, not a page size — gh paginates the
# GraphQL calls itself to reach it. Set high enough that hitting it means the
# repository genuinely has that many open pull requests, which open_prs then
# treats as unreadable rather than silently returning a truncated list.
GH_PR_LIST_LIMIT = 1000

# A check that FAILED. Anything still running is NOT a failure — reading a
# pending check as a broken one is how a shepherd spawns fixers for PRs whose
# CI simply has not finished, and how it would merge one whose CI has not
# either. Both directions of that mistake are covered by `checks-pending`.
CHECK_FAILED = {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED", "ERROR"}
CHECK_PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}

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
# A repo that is not named here is reported `ready to merge` and left for a
# human, which is what every repo did before this list existed.
#
# The gates below are the operator's, and all must hold: the head branch lives
# in this repository (`classify`'s `foreign` check — a fork is never merged),
# the body carries a `no-mistakes` attestation naming the pull request's
# CURRENT head commit (so a stale attestation from an earlier push can never
# authorise the push that replaced it), every check has CONCLUDED and passed,
# GitHub itself says MERGEABLE, and whoever opened it can push to this repo
# (`author_can_push` — the last thing checked, because it is the one claim the
# pull request body cannot make for itself). `--squash --delete-branch`
# because squash is the only method the remote allows; CONTRIBUTING.md owns
# that.
AUTO_MERGE_REPOS = {"Thurbeen/fleet"}


def pr_ref(artifact: str) -> tuple[str, int] | None:
    """('owner/repo', number) for a GitHub PR URL, or None for anything else."""
    m = GH_PR_URL_RE.match((artifact or "").strip())
    return (m.group(1), int(m.group(2))) if m else None


def gh_json(argv: list) -> tuple[object, str]:
    """Run gh and parse its JSON. A non-empty second value is why it could not.

    Every caller treats that string as UNDETERMINED and never as a verdict.
    """
    if not shutil.which("gh"):
        return None, "gh is not installed"
    try:
        out = subprocess.run(["gh"] + argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"gh could not be run: {exc}"
    if out.returncode != 0:
        first = (out.stderr or "").strip().splitlines()
        return None, first[0] if first else f"gh exited {out.returncode}"
    try:
        return json.loads(out.stdout), ""
    except ValueError:
        return None, "gh returned output that is not JSON"


def open_prs(slug: str) -> tuple[list, str]:
    """Every OPEN pull request on one repository, straight from the forge.

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
    docs, err = gh_json(
        ["pr", "list", "--repo", slug, "--state", "open", "--limit",
         str(GH_PR_LIST_LIMIT), "--json", GH_PR_FIELDS]
    )
    if err:
        return [], err
    if not isinstance(docs, list):
        return [], "gh returned something that is not a list of pull requests"
    docs = [d for d in docs if isinstance(d, dict)]
    if len(docs) >= GH_PR_LIST_LIMIT:
        return [], (
            f"{slug} has at least {GH_PR_LIST_LIMIT} open pull requests; "
            "gh's result may be truncated, so treating it as unreadable "
            "rather than silently dropping some"
        )
    return docs, ""


def check_verdicts(rollup) -> tuple[list, list]:
    """(names that failed, names still running). Everything else passed."""
    failed, pending = [], []
    for c in rollup or []:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or c.get("context") or "a required check"
        if "state" in c and "conclusion" not in c:
            # A StatusContext: one word, and PENDING is not a failure.
            verdict = str(c.get("state") or "").upper()
        elif str(c.get("status") or "").upper() != "COMPLETED":
            pending.append(name)
            continue
        else:
            verdict = str(c.get("conclusion") or "").upper()
        if verdict in CHECK_FAILED:
            failed.append(name)
        elif verdict not in CHECK_PASSED:
            pending.append(name)
    return failed, pending


# WHY NOT THE FIVE HEADINGS. `collect` looks for `## Intent` and its four
# siblings to check that a WORKER used the pipeline, and that is the right
# check there: it holds a task open until its own worker redoes the push, and
# the worker has no reason to lie to a queue it does not know exists.
#
# It is the wrong check HERE, and this is the whole safety question. This repo
# is public and has a fork, and this command merges unattended on a timer. The
# five headings are text, and text in a pull request body is written by whoever
# opened the pull request — so a check that counts them lets a body authorise
# its own merge. `no-mistakes` leaves something a body cannot fake as easily:
# an attestation naming the exact commit the pipeline ran on. A stale one from
# an earlier push is refused for the same reason, because the pipeline's
# verdict is about the code it saw and not about the branch's name.
ATTESTATION_RE = re.compile(
    r"<!--\s*no-mistakes-pipeline-attestation:v1\s+(\{.*?\})\s*-->", re.S
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
    m = ATTESTATION_RE.search(body or "")
    if not m:
        return False, (
            "the body carries no no-mistakes attestation, so nothing but its own "
            "prose says the pipeline ever ran"
        )
    try:
        doc = json.loads(m.group(1))
    except ValueError:
        return False, "the no-mistakes attestation is not valid JSON"
    if not isinstance(doc, dict):
        return False, "the no-mistakes attestation is not an object"

    attested = str(doc.get("head_sha") or "")
    if not attested:
        return False, "the no-mistakes attestation names no head_sha"
    if not head_sha:
        return False, "GitHub did not say which commit this pull request's head is"
    if attested.lower() != head_sha.lower():
        return False, (
            f"the no-mistakes attestation is for {attested[:8]}, and the head is "
            f"{head_sha[:8]} — it attests a push that is no longer what would merge"
        )

    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        return False, "the no-mistakes attestation lists no steps"
    unfinished = []
    for st in steps:
        if not isinstance(st, dict):
            return False, "the no-mistakes attestation's steps are malformed"
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


def head_owner(pr: dict) -> str:
    """The login owning the repository the head branch lives in, or ''."""
    return str((pr.get("headRepositoryOwner") or {}).get("login") or "")


def classify(pr: dict, slug: str) -> tuple[str, str]:
    """(condition, one line saying why).

    Four conditions get a fixer, in the order FIXABLE lists them. `ready`
    means the merge gates GitHub can answer hold. `foreign` is a pull request
    that is not ours, which is neither merged nor handed to an agent.
    `undetermined` means the answer is not knowable yet and is never treated
    as any of the others.
    """
    if str(pr.get("state") or "").upper() != "OPEN":
        return "closed", f"the pull request is {str(pr.get('state')).lower()}"
    if pr.get("isDraft"):
        return "undetermined", "still a draft"

    # NOT OURS, ASKED BEFORE ANYTHING ELSE. A stranger cannot create a branch
    # inside this repository, so where the head branch lives is the one claim
    # about a pull request that whoever opened it cannot write for themselves.
    # It gates the fixer as hard as it gates the merge: sending an agent to
    # "fix" a stranger's branch is worse than merging one, because it happens
    # without even the pretence of a gate.
    owner = slug.split("/", 1)[0]
    where = head_owner(pr)
    if not where:
        return "undetermined", "GitHub did not say which repository the head branch is in"
    if pr.get("isCrossRepository") or where.lower() != owner.lower():
        return "foreign", (
            f"its head branch is in {where}'s repository, not {slug} — "
            "fleet neither merges nor sends an agent at a pull request that is not ours"
        )

    mergeable = str(pr.get("mergeable") or "").upper()
    base = pr.get("baseRefName") or "its base branch"
    failed, pending = check_verdicts(pr.get("statusCheckRollup"))
    attested, attest_why = attestation_verdict(pr.get("body"), pr.get("headRefOid") or "")

    fixable = {
        "conflicting": (
            mergeable == "CONFLICTING",
            f"conflicts with {base} and cannot be merged as it stands",
        ),
        "checks-failed": (bool(failed), "failed checks: " + ", ".join(failed[:4])),
        "changes-requested": (
            str(pr.get("reviewDecision") or "").upper() == "CHANGES_REQUESTED",
            "a reviewer requested changes",
        ),
        "policy": (not attested, attest_why),
    }
    for condition in FIXABLE:
        hit, why = fixable[condition]
        if hit:
            return condition, why

    if pending:
        return "undetermined", "checks still running: " + ", ".join(pending[:4])
    if not pr.get("statusCheckRollup"):
        # An EMPTY rollup is not a pass. CI here only fires on pull requests,
        # so a PR whose checks have not been created yet reads exactly like a
        # PR with nothing to run — and merging the first one merges code CI
        # never saw. "No check has reported" is its own answer.
        return "undetermined", "no check has reported yet"
    if mergeable != "MERGEABLE":
        # UNKNOWN is GitHub still computing the merge, not a verdict.
        return "undetermined", f"mergeable is {mergeable or 'absent'}; ask again shortly"
    return "ready", f"{attest_why}; checks green, mergeable, and the branch is ours"


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
    "policy": """\
Nothing on this pull request shows the required pipeline ran on the commit it
would merge. `no-mistakes` leaves an attestation in the body naming the exact
head commit it ran against, and this one either has none or has one for an
earlier push. Re-run it:

    /no-mistakes --yes

on this branch. That rewrites the body — attestation and all five sections —
and actually runs the checks, against what is on the branch now. Do not open a
second pull request; the pipeline updates the one that is already there, and
do not hand-edit the body, because an attestation you typed attests nothing.""",
}


def fixer_brief(task: Task, pr: dict, condition: str, detail: str, drift: str) -> str:
    n = pr.get("number")
    base = pr.get("baseRefName") or task.doc.get("base") or "main"
    work = FIXER_WORK[condition].format(base=base)
    parts = [
        f"# {FIXER_TITLES[condition].format(n=n, base=base)}",
        "",
        f"An open pull request from fleet task `{task.ref}` needs work. This is "
        f"the whole instruction set; you share no context with whoever wrote the "
        f"branch.",
        "",
        f"- **Pull request.** {pr.get('url')} — {pr.get('title') or ''}".rstrip(" —"),
        f"- **Repo.** `{task.doc['repo']}`",
        f"- **Branch.** `{pr.get('headRefName') or task.doc['branch']}` "
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
        f"- {pr.get('url')} is open, updated in place, and the condition above is"
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
    subprocess.run(
        ["thurbox-cli", "session", "send", session, text],
        capture_output=True,
        check=False,
    )
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
        "--name", name[:64],
        "--repo-path", path,
        # Reconciling desired state, so a name already in use is the fixer that
        # is already there rather than news (§1c). `created` is read below.
        "--on-existing", "adopt",
    ]
    flags = profile_flags(task.doc.get("profile") or "default")
    if "--command" not in flags:
        create += ["--agent", task.doc.get("agent") or "claude"]
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


# One answer per (repo, login) per pass. The question does not change inside
# a run and every open pull request would otherwise ask it again.
_PUSH_ACCESS: dict[tuple[str, str], tuple[bool, str]] = {}
PUSH_PERMISSIONS = {"admin", "maintain", "write"}


def author_can_push(slug: str, pr: dict) -> tuple[bool, str]:
    """Was this pull request opened by someone who owns the repository?

    `classify`'s `foreign` check already proves the CODE is ours: a stranger
    cannot create a branch here. This proves the PULL REQUEST is. Anyone with
    read access can open one between two branches that already exist, and the
    body carrying the attestation would then be theirs to write — so the last
    thing checked before an unattended merge is who opened it.

    Asked as "may this login push here" rather than "is this login the owner"
    because the owner of `Thurbeen/fleet` is an organisation and no pull
    request is ever authored by one. `gh` has no `authorAssociation` field in
    every version; the collaborator permission endpoint is in all of them.
    """
    author = pr.get("author") or {}
    login = str(author.get("login") or "")
    if not login:
        return False, "GitHub did not say who opened it"
    if author.get("is_bot"):
        return False, f"{login} is a bot"
    key = (slug, login)
    if key not in _PUSH_ACCESS:
        doc, err = gh_json(["api", f"repos/{slug}/collaborators/{login}/permission"])
        if err or not isinstance(doc, dict):
            _PUSH_ACCESS[key] = (
                False,
                f"could not check whether {login} can push to {slug}: "
                f"{err or 'unexpected output'}",
            )
        else:
            perm = str(doc.get("permission") or "").lower()
            # GitHub spells "no access" as the literal string `none`.
            said = "no" if perm in ("", "none") else perm
            _PUSH_ACCESS[key] = (
                perm in PUSH_PERMISSIONS,
                f"{login} has {said} access to {slug}",
            )
    return _PUSH_ACCESS[key]


def gh_merge(url: str) -> tuple[bool, str]:
    """Squash-merge, which is the only method this remote allows."""
    if not shutil.which("gh"):
        return False, "gh is not installed"
    try:
        out = subprocess.run(
            ["gh", "pr", "merge", url, "--squash", "--delete-branch"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"gh could not be run: {exc}"
    if out.returncode != 0:
        first = ((out.stderr or "") + (out.stdout or "")).strip().splitlines()
        return False, first[0] if first else f"gh exited {out.returncode}"
    return True, "squash-merged, branch deleted"


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


def shepherd_pr(pr: dict, slug: str, task, args) -> dict:
    """Inspect one open pull request and do the one thing it calls for.

    `task` is the task it belongs to, or None: the forge is the source of the
    list now, so a pull request nobody recorded is shepherded like any other.
    It simply has no session to send a fixer into, which is said out loud.
    """
    url = pr.get("url") or f"https://github.com/{slug}/pull/{pr.get('number')}"
    row = {
        "task": task.ref if task else "",
        "pr": url,
        "repo": slug,
        "condition": "undetermined",
        "detail": "",
        "action": "none",
        "note": "",
    }

    condition, detail = classify(pr, slug)
    row["condition"], row["detail"] = condition, detail
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
        if slug not in AUTO_MERGE_REPOS:
            row["action"] = "ready"
            row["note"] = f"fleet does not merge in {slug}; this one is yours"
            return row
        if args.no_merge:
            row["action"] = "ready"
            row["note"] = "--no-merge"
            return row
        # The last gate, and the one a pull request body cannot write for
        # itself. Asked before --dry-run answers, so a dry run is honest
        # about what it would actually merge.
        allowed, why = author_can_push(slug, pr)
        if not allowed:
            row["action"] = "not-merged"
            row["note"] = (
                f"{why} — fleet merges unattended only what someone who can "
                "push here opened"
            )
            return row
        if args.dry_run:
            row["action"] = "would-merge"
            row["note"] = f"gh pr merge --squash --delete-branch ({why})"
            return row
        ok, note = gh_merge(url)
        row["action"], row["note"] = ("merged" if ok else "merge-failed"), note
        if ok and task:
            record_shepherd(task, {"condition": "merged", "detail": note,
                                   "pr": url, "at": now()})
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

    base = pr.get("baseRefName") or task.doc.get("base") or "main"
    branch = pr.get("headRefName") or task.doc["branch"]
    title = FIXER_TITLES[condition].format(n=pr.get("number"), base=base)

    if args.dry_run:
        row["action"] = "would-dispatch"
        how = f"reusing its own worker {reuse}" if reuse else "a fresh session on the branch"
        row["note"] = f"{title} ({how})"
        return row

    drift = base_drift(task.doc["repo"], base, branch) if condition == "conflicting" else ""
    path = next_fix_file(task, condition)
    with open(path, "w") as fh:
        fh.write(fixer_brief(task, pr, condition, detail, drift))

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


GH_REMOTE_RE = re.compile(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$")


def slug_from_remote(repo_path: str) -> str:
    """`owner/repo` from a checkout's `origin`, or ''. Local, and no network.

    The last resort, and the one that makes this work on a topic whose tasks
    have not shipped anything yet: before the first artifact is reported, the
    only thing naming the repository is the checkout the tasks were given.
    """
    if not repo_path or not os.path.isdir(repo_path):
        return ""
    m = GH_REMOTE_RE.search(git_out(repo_path, ["remote", "get-url", "origin"]).strip())
    return m.group(1) if m else ""


def shepherd_targets(tasks: list) -> dict:
    """task.ref -> `owner/repo`, derived and never hardcoded.

    The queue's tasks name their repositories: an artifact URL gives
    `owner/repo` outright, and a task that has not reported one yet inherits
    the slug of the other tasks sharing its local checkout. Merging stays
    limited to AUTO_MERGE_REPOS whatever comes out of here — knowing about a
    repository and being allowed to merge in it are different questions.
    """
    slug_of: dict[str, str] = {}
    by_path: dict[str, str] = {}
    for task in tasks:
        ref = pr_ref(task.doc.get("artifact"))
        if ref:
            slug_of[task.ref] = ref[0]
            by_path.setdefault(str(task.doc.get("repo") or ""), ref[0])
    for task in tasks:
        if task.ref in slug_of:
            continue
        path = str(task.doc.get("repo") or "")
        slug = by_path.get(path)
        if not slug:
            slug = slug_from_remote(path)
            if slug:
                by_path[path] = slug
        if slug:
            slug_of[task.ref] = slug
    return slug_of


def link_task(pr: dict, tasks: list) -> object:
    """The task this pull request belongs to, or None.

    Two ways, and the second is the one that matters. The artifact is what a
    worker reported once. The HEAD BRANCH is what the pull request is actually
    open from, and it is what connects a task's second pull request back to it
    after its first one merged and its artifact stopped being current.
    """
    number = pr.get("number")
    for task in tasks:
        ref = pr_ref(task.doc.get("artifact"))
        if ref and ref[1] == number:
            return task
    head = str(pr.get("headRefName") or "")
    if head:
        for task in tasks:
            if str(task.doc.get("branch") or "") == head:
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

    slug_of = shepherd_targets(tasks)
    slugs = sorted(set(slug_of.values()))

    rows, unreadable = [], []
    for slug in slugs:
        here = [t for t in tasks if slug_of.get(t.ref) == slug]
        prs, err = open_prs(slug)
        if err:
            # A repository that could not be listed contributes nothing. An
            # empty answer and an unreadable one are not the same claim, and
            # only one of them means "nothing is open".
            unreadable.append({"repo": slug, "detail": err})
            continue
        for pr in sorted(prs, key=lambda d: d.get("number") or 0):
            rows.append(shepherd_pr(pr, slug, link_task(pr, here), args))

    if args.json:
        print(json.dumps({
            "queue": os.path.abspath(queue_root()),
            "repos": slugs,
            "unreadable": unreadable,
            "prs": rows,
        }, indent=2))
        return 0

    if not slugs:
        print("shepherd: no task names a GitHub repository yet")
        return 0
    for bad in unreadable:
        print(f"shepherd: could not read the pull requests on {bad['repo']}: "
              f"{bad['detail']} — nothing there was touched")
    if not rows:
        if not unreadable:
            print("shepherd: no open pull requests on " + ", ".join(slugs))
        return 0

    verb = "would do" if args.dry_run else "did"
    print(f"shepherd: {len(rows)} open pull request(s) on {', '.join(slugs)} "
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
    print(
        "          Merging is limited to " + ", ".join(sorted(AUTO_MERGE_REPOS))
        + ", and only for a pull request whose\n"
        "          head branch is in that repo, that someone who can push there "
        "opened, that\n"
        "          carries a no-mistakes attestation for its CURRENT head, whose "
        "checks passed,\n          and that GitHub calls MERGEABLE."
    )
    return 0


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
    # webui.sh status prints the same path, so the two can never disagree
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
            # the same field the pane and the monitor draw, so the three views
            # cannot disagree about what was last seen.
            pub = t.doc.get("publish") or {}
            if pub.get("state"):
                extra = f"{extra}  {pub['state']} {age_of(pub.get('at'))}".strip()
            print(f"    {t.id:<34} {mark:<11} {where_it_runs(t)}  {extra}")
            # The row is one line and a record can contradict it; task_notes is
            # what says so, and it is the same list the monitor renders.
            for note in task_notes(q, t):
                print(f"        {note}")
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
            if b.get("kind") not in BLOCKER_KINDS:
                problems.append(f"{ref}: blocker kind {b.get('kind')!r} is not a real one")
            if not (b.get("why") or "").strip():
                problems.append(f"{ref}: blocker on {b.get('task')} has no reason")
            if b.get("task") not in q.tasks:
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
    """The resolved queue root, absolute, and nothing else — for scripts."""
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
    a.add_argument("--agent", default="claude", help="the agent to launch (default claude)")
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

    b = sub.add_parser("block", help="record why one task must wait for another")
    b.add_argument("ref")
    b.add_argument("--on", required=True)
    # Deliberately not an argparse `choices`: a wrong --kind gets the full
    # explanation below rather than a bare "invalid choice".
    b.add_argument("--kind")
    b.add_argument("--why")
    b.add_argument("--clear", action="store_true")
    b.set_defaults(func=cmd_block)

    pl = sub.add_parser("plan", help="what dispatches now, what waits, and why")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_plan)

    d = sub.add_parser("dispatch", help="launch every ready task at once")
    d.add_argument("--dry-run", action="store_true")
    d.set_defaults(func=cmd_dispatch)

    pr = sub.add_parser("prompt", help="answer the trust dialog and send the brief")
    pr.add_argument("ref", nargs="?", help="one task; every unprompted one by default")
    pr.add_argument("--timeout", type=int, default=20)
    pr.set_defaults(func=cmd_prompt)

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
