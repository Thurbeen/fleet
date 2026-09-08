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
`fleet` session opens — and never to the process cwd. A second clone of this
repo is supported and common (a control plane with no `origin` needs one that
workers push from), and resolving the queue against the cwd meant working in
that clone silently forked it. `queue_root()` and `guard_creating()` below own
that; the refuse-vs-warn split is argued at `guard_creating`.

Layout under $FLEET_QUEUE_DIR (default: this checkout's orchestration/queue,
gitignored):

    <topic>/topic.yaml            the topic record
    <topic>/PROMPT.md             the prompt, verbatim
    <topic>/<NN>-<slug>/task.yaml       intent and current state  (the PLAN)
    <topic>/<NN>-<slug>/BRIEF.md        what the worker reads     (the PLAN)
    <topic>/<NN>-<slug>/progress.jsonl  transitions, appended     (the PROGRESS)
    <topic>/<NN>-<slug>/result.md       the worker's conclusion   (the RESULT)
    .cursor                       the last watch sequence handled

Those four files are why a topic view needs no field this file does not
already have: intent, progress and outcome are separate artifacts rather than
one status word.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
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

# The five headings a `no-mistakes` pull request body carries, and the ONE
# place they are written down: POLICY.md quotes this list, collect checks
# against it, and nothing else restates it.
#
# WHY A CHECK AT ALL. "Open the pull request by running `/no-mistakes --yes`"
# is an instruction about a METHOD, and a method leaves no trace: a worker that
# produced a good-looking PR with a bare `gh pr create` satisfied every visible
# requirement. Two tasks were once collected `shipped` that way and nothing
# noticed until the operator read the bodies himself. These headings are the
# artifact the pipeline leaves behind, so this is the requirement restated as
# something a reader can verify.
PIPELINE_HEADINGS = ("Intent", "What Changed", "Risk Assessment", "Testing", "Pipeline")

# A pull request URL, and nothing else — group 1 is the canonical form, so a
# link someone pasted with `/files` or a `#comment` on the end still resolves
# to the pull request it names. `not-applicable` and `stuck` produce no
# artifact at all, and an artifact that is not a PR (an issue, a doc, a commit)
# is not a pipeline claim — neither is a failure, and neither is checked.
PR_URL_RE = re.compile(r"^(https?://[^/\s]+/[^/\s]+/[^/\s]+/pull/\d+)(?:[/?#].*)?$")

# Standing policy for every worker, tracked beside the otherwise-gitignored
# queue. Anchored to the CHECKOUT, not to FLEET_QUEUE_DIR: it lives with the
# repo and does not move when the queue does.
POLICY_FILE = os.path.join("orchestration", "queue", "POLICY.md")


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
#   the live session thurbox-cli reports the fleet session's real `cwd`, which
#                    is the only authority when the clone has moved.
#
# When neither answers — no rendered manifest, no thurbox-cli, no such session —
# the answer is "unknown", and unknown must stay SILENT. A fleet used without
# the extension installed is a legitimate setup and may not be made unusable by
# a guard that cannot tell whether it is even warranted.

SESSION_REPO_PATH_RE = re.compile(r'^\s*repo_path\s*=\s*"([^"]*)"', re.M)
SESSION_NAME_RE = re.compile(r'^\s*name\s*=\s*"([^"]*)"', re.M)


def manifest_session(path: str) -> tuple[str | None, str | None]:
    """(session name, repo_path) from the first [[sessions]] block of a manifest."""
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError:
        return None, None
    _, sep, sessions = text.partition("[[sessions]]")
    if not sep:
        return None, None
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
    """`_TEMPLATE` and dotfiles are the shipped form and the cursor, not records."""
    return not name.startswith(("_", "."))


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


class Queue:
    def __init__(self, root: str):
        self.root = root
        self.topics: dict[str, dict] = {}
        self.tasks: dict[str, Task] = {}
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
            self.topics[topic] = read_yaml(meta)
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
        raise QueueError(f"no such task: {ref}")

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

    number = args.number or next_number(tpath)
    tid = f"{number}-{args.slug}"
    path = os.path.join(tpath, tid)
    if os.path.exists(path):
        raise QueueError(f"task {args.topic}/{tid} already exists")
    os.makedirs(path)

    doc = {
        "id": tid,
        "topic": args.topic,
        "title": args.title or args.slug.replace("-", " "),
        "state": "queued",
        "repo": args.repo,
        "branch": args.branch,
        "base": args.base,
        "profile": args.profile,
        "agent": args.agent,
        "touches": [s.strip() for s in (args.touches or "").split(",") if s.strip()],
        "blocked_by": [],
        "session": None,
        "prompted": False,
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
    """
    d = task.doc
    result = os.path.abspath(task.file("result.md"))
    prompt = os.path.abspath(os.path.join(os.path.dirname(task.path), "PROMPT.md"))
    filled = {BRIEF_SECTIONS[0]: body.strip()} if body and body.strip() else {}
    sections = "\n\n".join(
        f"## {h}\n\n{filled.get(h, BRIEF_PLACEHOLDER)}" for h in BRIEF_SECTIONS
    )
    return f"""# {d["title"]}

Task `{task.ref}` of topic **{topic.get("title", task.topic)}**.
The prompt this came from is at `{prompt}`; read it if the goal here is unclear.

- **Repo.** `{d["repo"]}`
- **Branch.** `{d["branch"]}` off `{d["base"]}`
- **Expected to touch.** {", ".join(f"`{p}`" for p in d["touches"]) or "not recorded"}
- **Standing policy.** `{policy_path()}`

**Read that policy file before you start.** It is the rest of your
instructions and it is not repeated here: how to open the pull request and how
that is verified, who merges it, the gate to run before you push, and what the
other workers running beside you mean for you. This brief does not override it.

{sections}

## Reporting back — write a file, do not send mail

When you are finished, or when you have concluded you cannot finish, write:

    {result}

with exactly this shape:

```markdown
---
outcome: shipped | stuck | failed | not-applicable
artifact: <PR url, or omit>
---
A short paragraph: what you actually did, and anything the lead must know.
```

That file is what closes this task, and the policy's last section says why it,
and not a message, is what does it.
"""


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
        print(f"    {t.ref:<52} {t.doc['repo']}  {t.doc['branch']}")
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
            if q.blocker_cleared(b):
                continue
            print(f"        {b['kind']} on {b['task']}: {b['why']}")
    return 0


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
    flags = profile_flags(d.get("profile") or "default")
    # A profile carrying `command` replaces `--agent`; thurbox refuses both.
    if "--command" not in flags:
        create += ["--agent", d.get("agent") or "claude"]
    parent = os.environ.get("THURBOX_SESSION")
    if parent:
        create += ["--parent", parent]
    create += flags + ["--json"]
    send = f"Read {os.path.abspath(task.file('BRIEF.md'))} and do what it says."
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
            print(f"      {shell_quote(create)}")
            print("      ./scripts/session-trust.sh <uuid>   # answer the trust dialog first")
            print(f"      thurbox-cli session send <uuid> {shell_quote([send])}")
        return 0

    # Phase 1: create every session back to back, before any of them is kept
    # waiting on a trust dialog. That is what makes "launched together" true —
    # a whole wave against one repo draws its dialogs simultaneously only if
    # session creation for task 2 does not wait on task 1's trust confirmation.
    attached: list[Task] = []
    for t in ready:
        create, _send = spawn_commands(t)
        try:
            out = subprocess.run(create, capture_output=True, check=True).stdout
            session = json.loads(out)["id"]
        except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as exc:
            detail = getattr(exc, "stderr", b"") or b""
            print(f"    {t.ref}: spawn failed: {detail.decode().strip() or exc}", file=sys.stderr)
            continue
        attach(t, session)
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
    task.save()
    seed_cursor(queue_root())


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
    _, send = spawn_commands(task)
    ok, report = trust_and_send(session, send, timeout)
    if not ok:
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
    """None means no cursor was ever written, distinct from a written 0."""
    path = os.path.join(root, ".cursor")
    try:
        return int(open(path).read().strip())
    except (OSError, ValueError):
        return None


def write_cursor(root: str, seq: int) -> None:
    with open(os.path.join(root, ".cursor"), "w") as fh:
        fh.write(f"{seq}\n")


def watch_command(extra: list) -> list:
    """The stream command, real or the selftest's recorded-stream override."""
    override = os.environ.get("FLEET_QUEUE_WATCH_CMD")
    if override:
        return ["sh", "-c", override]
    return ["thurbox-cli", "watch", "--json"] + extra


def seed_cursor(root: str) -> None:
    """Seed the cursor at the moment a task attaches, from the stream's
    current high-water mark, so the first `watch` after a dispatch resumes
    from the dispatch instant rather than from 0 (replays the whole backlog)
    or from "now" (drops everything in between). Never touches a cursor that
    already exists — a running watch owns it from here on.
    """
    if os.path.exists(os.path.join(root, ".cursor")):
        return
    try:
        proc = subprocess.run(
            watch_command(["--initial", "--for-secs", "0"]),
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if proc.returncode != 0:
        return
    high = 0
    for line in proc.stdout.decode(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        high = max(high, int(ev.get("seq") or 0))
    write_cursor(root, high)


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

    since = read_cursor(root)
    extra = ["--for-secs", str(args.for_secs)]
    if since is not None:
        extra += ["--since", str(since)]
    cmd = watch_command(extra)

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=args.for_secs + 30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QueueError(f"could not read the event stream: {exc}") from exc

    floor = since if since is not None else 0
    high = floor
    touched: dict[str, dict] = {}
    for line in proc.stdout.decode(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        seq = int(ev.get("seq") or 0)
        high = max(high, seq)
        if seq <= floor:
            continue
        task = by_session.get(ev.get("session"))
        if task is None:
            continue
        record_event(task, ev)
        touched[task.ref] = ev
        print(
            f"    seq {seq}  {task.ref}  "
            f"{ev.get('from_state') or '-'} -> {ev.get('to_state') or ev.get('state') or '-'}"
        )

    if high > floor:
        write_cursor(root, high)

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
    entry = {
        "seq": ev.get("seq"),
        "at": ev.get("at"),
        "from": ev.get("from_state"),
        "to": ev.get("to_state") or ev.get("state"),
        "reason": ev.get("reason"),
        "observed": now(),
    }
    with open(task.file("progress.jsonl"), "a") as fh:
        fh.write(json.dumps(entry) + "\n")


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


def pipeline_verdict(outcome, url) -> tuple[str, str]:
    """Does this task's artifact carry the pipeline's proof? Three answers.

        skipped   nothing to check — the outcome does not require a PR
                  (`not-applicable` or `stuck`), and none, or one that is not
                  a PR, was given.
        passed    the PR body carries all five PIPELINE_HEADINGS.
        missing   `shipped` with no PR to check, or a PR body without them:
                  the pipeline was skipped, or never proven at all.
        unknown   the check could not run — no `gh`, no network, no such PR.

    `unknown` is a fourth word on purpose and never collapses into `passed` or
    `missing`. An offline machine and a CI runner with no `gh` must both still
    be able to collect, and "could not check" must never be reported as either
    verdict — that is how a trusted claim gets manufactured out of a timeout.

    A worker that writes `outcome: shipped` is claiming a merged pull request,
    so a missing or malformed `artifact` is not the same silence as
    `not-applicable`/`stuck` legitimately producing none — it is `missing`,
    held open like any other unproven `shipped` claim.
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
            ["gh", "pr", "view", url, "--json", "body", "-q", ".body"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "unknown", f"gh pr view could not run: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return "unknown", "gh pr view failed: " + (detail[-1] if detail else "no output")

    body = proc.stdout
    missing = [
        h for h in PIPELINE_HEADINGS
        if not re.search(rf"^\s*#{{1,6}}\s+{re.escape(h)}\s*$", body, re.M | re.I)
    ]
    if missing:
        return "missing", "the PR body has no " + " or ".join(missing) + " heading"
    return "passed", "all five pipeline headings present"


def report_unverified(task: Task, url, detail: str) -> None:
    """The loud half of the check: the lead sees this AT COLLECT TIME."""
    print(
        f"    {task.ref}: NOT CLOSED — its pull request skipped the pipeline\n"
        f"        {url or '(no pull request given)'}\n"
        f"        {detail}\n"
        "        A `no-mistakes` pull request body carries all five of: "
        + ", ".join(PIPELINE_HEADINGS)
        + ".\n"
        "        Send the worker back to re-open it with `/no-mistakes --yes`,\n"
        "        then collect again. If you have read this pull request and\n"
        "        judged it good as it stands, close it deliberately with\n"
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
        if task.state in ("done", "landed", "stuck", "failed", "abandoned") or not os.path.exists(
            path
        ):
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
        verdict, detail = pipeline_verdict(outcome, artifact)
        # Recorded before the branch below, so a held-back task carries the
        # reason in its record and not only in the terminal that saw it.
        task.doc["artifact_check"] = {"verdict": verdict, "detail": detail, "at": now()}

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
            line += "  [pipeline verified]"
        elif verdict == "missing":
            line += "  [pipeline NOT verified — closed by --allow-unverified]"
        elif verdict == "unknown":
            line += f"  [pipeline unchecked: could not check — {detail}]"
        print(line)
        first = body.splitlines()[0] if body.splitlines() else ""
        if first:
            print(f"        {first}")

    print(f"collect: {concluded} result(s) read")
    if held:
        print(
            f"         {held} task(s) HELD OPEN — their pull requests skipped the "
            "pipeline; see above.",
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
    if not release:
        print("      Sessions left alone (--no-reap); `queue.sh reap` releases them.")
        return acted

    # A dry run promotes nothing, so the state on disk still says `done` for a
    # task that just landed. Project the sweep's answer forward instead, or a
    # dry run would report every reap it is about to do as a task it is keeping.
    def state_of(task: Task) -> str:
        kind = (landings.get(task.ref) or (None,))[0]
        return LANDED_STATE.get(kind, task.state) if dry else task.state

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
# folded into either. It reads every PR the queue's own tasks produced,
# classifies it, DISPATCHES A FIXER for the ones that need work, and merges the
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
#   Never touch  Only artifacts recorded on this queue's own tasks, and only
#   a stranger.  when the artifact is a GitHub pull request URL.

# GitHub-specific and deliberately NOT the forge-agnostic PR_URL_RE above: this
# one exists to name `owner/repo`, which is what AUTO_MERGE_REPOS is checked
# against. Defining a second `PR_URL_RE` here shadowed that one and broke the
# pipeline check; the two answer different questions and keep different names.
GH_PR_URL_RE = re.compile(r"^https://github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)/?$")


GH_PR_FIELDS = (
    "number,state,url,title,isDraft,mergeable,reviewDecision,"
    "statusCheckRollup,body,headRefName,baseRefName"
)

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
# The three gates below are the operator's, and all three must hold: the body
# carries the pipeline's sections (so a PR that skipped the pipeline can never
# be merged by fleet, however green it looks), every check has CONCLUDED and
# passed, and GitHub itself says MERGEABLE. `--squash --delete-branch` because
# squash is the only method the remote allows; CONTRIBUTING.md owns that.
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


def missing_sections(body: str) -> list:
    """The pipeline headings this body does not carry.

    PIPELINE_HEADINGS and this matcher are `pipeline_verdict`'s, reused rather
    than restated: `collect` asks the same question of the same bodies, and two
    copies of "what the pipeline leaves behind" would drift. The difference is
    only where the body comes from — the shepherd already has it in hand, so it
    does not spend a second `gh pr view` to re-fetch it.
    """
    text = body or ""
    return [
        f"## {h}"
        for h in PIPELINE_HEADINGS
        if not re.search(rf"^\s*#{{1,6}}\s+{re.escape(h)}\s*$", text, re.M | re.I)
    ]


def classify(pr: dict) -> tuple[str, str]:
    """(condition, one line saying why).

    Four conditions get a fixer, in the order FIXABLE lists them. `ready`
    means all three merge gates hold. `undetermined` means the answer is not
    knowable yet and is never treated as either of the other two.
    """
    if str(pr.get("state") or "").upper() != "OPEN":
        return "closed", f"the pull request is {str(pr.get('state')).lower()}"
    if pr.get("isDraft"):
        return "undetermined", "still a draft"

    mergeable = str(pr.get("mergeable") or "").upper()
    base = pr.get("baseRefName") or "its base branch"
    failed, pending = check_verdicts(pr.get("statusCheckRollup"))
    missing = missing_sections(pr.get("body"))

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
        "policy": (bool(missing), "the body is missing " + ", ".join(missing)),
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
    return "ready", "pipeline sections present, checks green, mergeable"


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
This pull request was opened outside the required pipeline — its body is
missing sections the pipeline always writes. Re-run it:

    /no-mistakes --yes

on this branch, so the pull request body is rewritten with `## Intent`,
`## What Changed`, `## Risk Assessment`, `## Testing` and `## Pipeline`, and
the checks the pipeline runs actually run. Do not open a second pull request —
the pipeline updates the one that is already there.""",
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


def spawn_fixer(task: Task, name: str, brief_path: str) -> tuple[str, str]:
    """A session on the branch that already exists. (session id, note)."""
    slug = f"{task.topic}__{task.id}"
    path, note = branch_checkout(task.doc["repo"], task.doc["branch"], slug)
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


def shepherd_one(task: Task, repo_slug: str, args) -> dict:
    """Inspect one pull request and do the one thing it calls for."""
    row = {
        "task": task.ref,
        "pr": task.doc["artifact"],
        "repo": repo_slug,
        "condition": "undetermined",
        "detail": "",
        "action": "none",
        "note": "",
    }
    pr, err = gh_json(["pr", "view", task.doc["artifact"], "--json", GH_PR_FIELDS])
    if err or not isinstance(pr, dict):
        row["detail"] = f"could not read the pull request: {err or 'unexpected output'}"
        return row

    condition, detail = classify(pr)
    row["condition"], row["detail"] = condition, detail
    rec = task.doc.get("shepherd") or {}

    if condition in ("closed", "undetermined"):
        if condition == "closed" and rec:
            record_shepherd(task, None)
        return row

    if condition == "ready":
        if rec:
            record_shepherd(task, None)
        if repo_slug not in AUTO_MERGE_REPOS:
            row["action"] = "ready"
            row["note"] = f"fleet does not merge in {repo_slug}; this one is yours"
            return row
        if args.no_merge:
            row["action"] = "ready"
            row["note"] = "--no-merge"
            return row
        if args.dry_run:
            row["action"] = "would-merge"
            row["note"] = "gh pr merge --squash --delete-branch"
            return row
        ok, note = gh_merge(task.doc["artifact"])
        row["action"], row["note"] = ("merged" if ok else "merge-failed"), note
        if ok:
            record_shepherd(task, {"condition": "merged", "detail": note, "at": now()})
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
    title = FIXER_TITLES[condition].format(n=pr.get("number"), base=base)

    if args.dry_run:
        row["action"] = "would-dispatch"
        how = f"reusing its own worker {reuse}" if reuse else "a fresh session on the branch"
        row["note"] = f"{title} ({how})"
        return row

    drift = base_drift(task.doc["repo"], base, task.doc["branch"]) if condition == "conflicting" else ""
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
        session, note = spawn_fixer(task, title, path)

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
            "brief": os.path.basename(path),
            "at": now(),
        },
    )
    return row


def cmd_shepherd(args) -> int:
    """The fourth thing: the pull requests, after `watch` and after `collect`."""
    q = Queue(queue_root())
    only = q.get(args.ref).ref if args.ref else ""
    targets = []
    for task in sorted(q.tasks.values(), key=lambda t: t.ref):
        if args.topic and task.topic != args.topic:
            continue
        if only and task.ref != only:
            continue
        ref = pr_ref(task.doc.get("artifact"))
        if ref:
            targets.append((task, ref[0]))

    rows = [shepherd_one(task, slug, args) for task, slug in targets]

    if args.json:
        print(json.dumps({"queue": os.path.abspath(queue_root()), "prs": rows}, indent=2))
        return 0

    if not targets:
        print("shepherd: no task carries a GitHub pull request yet")
        return 0

    verb = "would do" if args.dry_run else "did"
    print(f"shepherd: {len(rows)} pull request(s) on this queue's own tasks — what it {verb}:\n")
    for r in rows:
        print(f"    {r['task']}  {r['pr']}")
        print(f"        {r['condition']}: {r['detail']}")
        if r["action"] != "none":
            print(f"        {r['action']}: {r['note']}" if r["note"] else f"        {r['action']}")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    print("\nshepherd: " + ", ".join(f"{n} {a}" for a, n in sorted(counts.items())))
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
        + ", and only for a PR whose body\n"
        "          carries the pipeline's sections, whose checks passed, and that "
        "GitHub calls MERGEABLE."
    )
    return 0


# --- read-only views ---------------------------------------------------------


def cmd_list(args) -> int:
    root = queue_root()
    # The first line answers "which queue am I looking at?" without being asked.
    # webui.sh status prints the same path, so the two can never disagree
    # silently about what they are showing.
    print(f"queue: {os.path.abspath(root)}")
    q = Queue(root)
    grouped = q.by_topic()
    if args.topic:
        grouped = {k: v for k, v in grouped.items() if k == args.topic}
    if not grouped:
        print("queue: empty")
        return 0
    for topic, tasks in sorted(grouped.items()):
        meta = q.topics.get(topic, {})
        print(f"{topic} — {meta.get('title', '')}")
        for t in tasks:
            mark = "waiting" if t.state == "queued" and not q.is_ready(t) else t.state
            extra = t.doc.get("artifact") or t.doc.get("session") or ""
            print(f"    {t.id:<34} {mark:<11} {t.doc['repo']}  {extra}")
        print()
    return 0


def cmd_show(args) -> int:
    q = Queue(queue_root())
    task = q.get(args.ref)
    d = task.doc
    print(f"{task.ref} — {d['title']}")
    for key in ("state", "repo", "branch", "base", "agent", "profile", "session",
                "prompted", "outcome", "artifact"):
        print(f"    {key + ':':<12} {d.get(key)}")
    check = d.get("artifact_check") or {}
    if check.get("verdict"):
        print(f"    {'pipeline:':<12} {check['verdict']} — {check.get('detail', '')}")
    landing = d.get("landing") or {}
    if landing.get("state"):
        print(f"    {'landing:':<12} {landing['state']} — {landing.get('detail', '')}")
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
    for b in task.blockers:
        cleared = "cleared" if q.blocker_cleared(b) else "HOLDING"
        print(f"    blocked_by:  {b['task']} [{cleared}] {b['kind']}: {b['why']}")
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

    q = Queue(root)
    problems = []
    for ref, t in sorted(q.tasks.items()):
        d = t.doc
        for key in ("id", "topic", "title", "state", "repo", "branch"):
            if not d.get(key):
                problems.append(f"{ref}: missing {key}")
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
    a.add_argument("--repo", required=True)
    a.add_argument("--branch", required=True)
    a.add_argument("--base", default="main")
    a.add_argument("--profile", default="default")
    a.add_argument("--agent", default="claude", help="the agent to launch (default claude)")
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
        help="close a task whose PR body is missing the pipeline's headings, "
        "after you have read that PR and judged it good anyway",
    )
    c.set_defaults(func=cmd_collect)

    rp = sub.add_parser("reap", help="release the sessions of tasks whose work has landed")
    rp.add_argument(
        "--dry-run",
        action="store_true",
        help="say what would land and what would be released, and write nothing",
    )
    rp.set_defaults(func=cmd_reap)

    sh = sub.add_parser("shepherd", help="inspect the PRs this queue produced and act")
    sh.add_argument("--dry-run", action="store_true",
                    help="say exactly what would be dispatched and merged, and change nothing")
    sh.add_argument("--json", action="store_true", help="the machine-readable seam")
    sh.add_argument("--topic", help="only this topic's tasks")
    sh.add_argument("--ref", help="only this task")
    sh.add_argument("--no-merge", action="store_true",
                    help="classify and dispatch as usual, but merge nothing")
    sh.add_argument("--force", action="store_true",
                    help="dispatch again for a condition a fixer is already out for")
    sh.set_defaults(func=cmd_shepherd)

    li = sub.add_parser("list", help="one line per task, grouped by topic")
    li.add_argument("--topic")
    li.set_defaults(func=cmd_list)

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
