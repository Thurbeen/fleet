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
STATES = ("queued", "dispatched", "done", "stuck", "failed", "abandoned")
OUTCOMES = {
    "shipped": "done",
    "not-applicable": "done",
    "stuck": "stuck",
    "failed": "failed",
}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
BRIEF_PLACEHOLDER = "<!-- WRITE THE INSTRUCTIONS HERE -->"


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
    # blocker, and only until that blocker's task is genuinely `done` — a
    # session that stopped, or a task abandoned, never releases it.
    def is_ready(self, task: Task) -> bool:
        if task.state != "queued":
            return False
        return all(self.blocker_cleared(b) for b in task.blockers)

    def blocker_cleared(self, blocker: dict) -> bool:
        try:
            return self.get(blocker["task"]).state == "done"
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
    """The worker's whole world, in one file it reads by absolute path.

    The result contract at the end is the half of completion the event stream
    cannot supply: a transition says a turn ended, and only this file says what
    the worker concluded.
    """
    d = task.doc
    result = os.path.abspath(task.file("result.md"))
    prompt = os.path.abspath(os.path.join(os.path.dirname(task.path), "PROMPT.md"))
    return f"""# {d["title"]}

Task `{task.ref}` of topic **{topic.get("title", task.topic)}**.
The prompt this came from is at `{prompt}`; read it if the goal here is unclear.

- **Repo.** `{d["repo"]}`
- **Branch.** `{d["branch"]}` off `{d["base"]}`
- **Expected to touch.** {", ".join(f"`{p}`" for p in d["touches"]) or "not recorded"}

This brief is the only instruction set you need. Other tasks are running
against other briefs at the same time, possibly in this same repo — that is
normal and expected. Do not read their briefs, do not widen your scope to
tidy what they are doing, and do not wait for them. If your change conflicts
with theirs, an ordinary rebase resolves it.

## What to do

{body.strip() if body else BRIEF_PLACEHOLDER}

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

The lead reads that file on its own schedule. Do **not** use
`thurbox-cli message send` — it injects into the lead's terminal and
interrupts whoever is talking to it. Your file is what closes this task;
without it the lead sees only that a turn ended, which is not the same claim.
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
    trust = subprocess.run(
        ["./scripts/session-trust.sh", session, "--timeout", str(timeout)],
        capture_output=True,
        check=False,
    )
    report = (trust.stdout + trust.stderr).decode().strip()
    if trust.returncode != 0:
        return False, report
    _, send = spawn_commands(task)
    subprocess.run(
        ["thurbox-cli", "session", "send", session, send],
        capture_output=True,
        check=False,
    )
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


def cmd_collect(args) -> int:
    q = Queue(queue_root())
    concluded = 0
    for task in sorted(q.tasks.values(), key=lambda t: t.ref):
        path = task.file("result.md")
        if task.state in ("done", "stuck", "failed", "abandoned") or not os.path.exists(path):
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
        task.doc["outcome"] = outcome
        task.doc["artifact"] = meta.get("artifact")
        task.doc["state"] = OUTCOMES[outcome]
        task.doc["concluded_at"] = now()
        task.save()
        concluded += 1
        line = f"    {task.ref}  {outcome}"
        if meta.get("artifact"):
            line += f"  {meta['artifact']}"
        print(line)
        first = body.splitlines()[0] if body.splitlines() else ""
        if first:
            print(f"        {first}")

    print(f"collect: {concluded} result(s) read")
    if concluded:
        print("         Run `queue.sh plan` — a blocker may have cleared.")
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
    if task.touches:
        print(f"    {'touches:':<12} {', '.join(task.touches)}")
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
    c.set_defaults(func=cmd_collect)

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
