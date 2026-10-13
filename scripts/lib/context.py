#!/usr/bin/env python3
"""`fleet context`: what fleet remembers, carried in one capped read to whoever needs it next.

    uv run fleet context                      the lead's summary (≤ 1.5 KB)
    uv run fleet context lead [--all]         the same, by name
    uv run fleet context pending [--all]      every task waiting on a decision
    uv run fleet context repo <repo> [--task <ref>] [--all] [--full] [--fields replaces,at]
                                              a project's live facts and recent results (≤ 4 KB)
            [--peers [--refresh] [--timeout <s>]]
                                              and what every other fleet shares about it
    uv run fleet context learn <repo> "<fact>" [--replaces <id>] [--source <src>]
                                              record one fact; a repeat is `already`;
                                              `--` before a fact that starts with `-`

A FACT IS ONE FILE, CREATED ONCE AND NEVER EDITED:
`registry/facts/<repo>/<id>.md`, YAML front matter (`id`, `repo`, `at`,
`source`, and `replaces` when it supersedes one) over one or two sentences.
`fleet_platform.create_record` writes it, 0600 — a temp file of the
writer's own, linked to the name, which fails when the name exists — the one
create seam the run log uses too, so any number of workers and fleet
processes write at once with no lock, a reader never sees half a fact, and a write that would collide keeps the first. The id is derived
from what is written: `<date>-<topic>-<task>-<n>` from a task's `learned:`
entry, `<date>-<hash of repo, text and replaces>` from `learn`. So `collect` running
twice, or the loop and the lead running it together, makes each file once, and
`learn` said twice answers `already`.

A fact is LIVE unless a newer one names it in `replaces`; the old file stays
as history. Two facts replacing one id are both live and both shown, with
their dates, under `contested`: a contradiction is surfaced, never resolved
here. A wrong fact goes by writing one that replaces it, or by deleting its
file. Nothing here summarises, merges or reviews — that would be a writer of
facts nobody stated. The lead writes none of this; workers state their own
facts in `result.md`, and `collect` records them (`capture_learned`).

A REPOSITORY IS NAMED BY WHERE IT LIVES, never by its directory name alone:
`<host>/<path>` for a checkout whose `origin` is on a forge
(`github.com/acme/app`, `gitlab.example.com/group/sub/app`), lowercased, so
the same path on two forges is two stores; `local/<name>-<hash>` for one whose
origin is a local path or missing, hashed from that path, so two local repos
called `tool` are two stores too. Every segment is checked before it becomes a
directory — no `..`, no separator, no drive, no device name — so no argument
reaches outside `registry/facts/`. A bare name (`app`) resolves when exactly
one known repository ends in it, and is refused as ambiguous otherwise.

THE OUTPUT IS AN AXI (axi.md, `axi/1.0-2026-07`): TOON on stdout, totals
first, an explicit empty state, a cap on every default output with `more:`
naming what was cut and `--all`/`--full` to see it, `help[]` last, `--help` on
every subcommand, an `error:`/`code:`/`help[]` block on stdout with exit 1
for an error and 2 for a usage mistake, and no prompt — stdin is never read.
`tests/context/test_axi.py` holds every subcommand to all of it.

PEERS ARE READ, NEVER WRITTEN. `repo <repo> --peers` asks every other fleet
`peers.py` finds — thurbox's session list on this machine and every
`hosts.toml` host, plus `peers.conf` — through `peers.run_read`, the same
local-or-ssh path and timeout the board's peer view uses, and the one thing a
peer runs is its own `context.py repo <identity> --wire json`. Nothing
replicates, merges or syncs: each fleet stays the only writer of its facts,
and a reader keeps one only by `learn ... --source peer:<fleet>/<id>`.

  the wire     the hidden machine half, one JSON line no agent reads:
               `{"wire": "fleet-context/1", "repo", "share", "facts": [{id, at,
               source, text, replaces}], "results": [{task, state, date,
               summary}]}`, or the same with `refused`. It is asked by full
               identity only — a bare name would resolve against the PEER's
               map, a path would read the peer's disk — never fans out again,
               and carries no `--task`. Everything the reader receives is
               re-validated and bounded (`check_wire`): size, counts, fact ids,
               the repository it answers for; an answer broken anywhere is
               rejected whole.
  SHARE=       in the PEER's `orchestration/fleet.conf`: unset means facts,
               `facts,results` adds each result's first line, `none` refuses.
               No word serves prompts, briefs, OPERATOR.md, run logs,
               transcripts or narrative, and an unknown word is dropped.
  status       per fleet, as `peers.py` reports it: ok, stale (the last good
               answer, with its age), unreachable (why), or refused (SHARE).
               Answers are cached in `context.json` beside the peers cache for
               `peers.TTL` seconds, keyed by fleet AND repository identity, so
               two fleets or two forges of one name stay two rows.

`registry/` is beside FLEET_REGISTRY_FILE when that is set (the test harness
relocates the map, and with it the facts) and this checkout's otherwise.
Everything here is gitignored working state, like the queue it reads.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import yaml


def _load_sibling(name: str, filename: str):
    """A scripts/lib module, keyed as every loader keys it (queue.py's `_load_sibling`)."""
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fleet_platform = _load_sibling("fleet_platform", "fleet_platform.py")
agent_settings = _load_sibling("fleet_agent_settings", "agent_settings.py")


def _queue():
    """queue.py, loaded on first use: it loads this module back from `collect`."""
    return _load_sibling("fleet_queue", "queue.py")


def _peers():
    """peers.py, loaded only by a `--peers` read: it loads queue.py and the pane probe."""
    return _load_sibling("fleet_peers", "peers.py")


MAX_LEARNED = 5
MAX_FACT_CHARS = 500
TEXT_CAP = 160
LEAD_BYTES = 1536
REPO_BYTES = 4096
PENDING_BYTES = 4096
FACT_ROWS = 12
RESULT_ROWS = 5
TOPIC_ROWS = 8
PENDING_ROWS = 12
FACT_FIELDS = ["id", "date", "source", "text"]
EXTRA_FIELDS = ("replaces", "at")
FACT_ID_RE = re.compile(r"^\d{8}-[a-z0-9][a-z0-9-]{0,160}$")
SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
DEVICE_NAMES = {"con", "prn", "aux", "nul"} | {f"{p}{n}" for p in ("com", "lpt") for n in range(1, 10)}
LOCAL = "local"
# The hidden machine wire between two fleets: one JSON document, never read by an agent.
WIRE = "fleet-context/1"
WIRE_FACTS = 50
WIRE_RESULTS = RESULT_ROWS
WIRE_BYTES = 64 * 1024
SOURCE_CHARS = 120
SHARE_WORDS = ("facts", "results")
SHARE_OFF = ("none", "off", "no", "false", "0")
PEER_FACT_ROWS = 8
PEER_RESULT_ROWS = 5
# A peer's last answer older than this is forgotten rather than shown as stale.
PEER_CACHE_DAYS = 7
RESULT_FILE = "result.md"
TASK_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
# Everything a peer says reaches a terminal: C0, DEL and C1 (an 8-bit CSI is
# U+009B), and the bidi marks and overrides that make text read as other text.
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]")


class ContextError(Exception):
    """One refusal, as the `error:` block says it: a code, a sentence, and what to run instead."""

    def __init__(self, code: str, message: str, hints: list | None = None, exit_code: int = 1):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hints = hints or []
        self.exit_code = exit_code


# --- where -----------------------------------------------------------------------


def registry_dir() -> str:
    path = os.environ.get("FLEET_REGISTRY_FILE")
    if path:
        return os.path.dirname(os.path.abspath(path))
    return os.path.join(fleet_platform.checkout_dir(), "registry")


def facts_root() -> str:
    return os.path.join(registry_dir(), "facts")


def repo_dir(repo: str) -> str:
    """The store of one validated identity, proven to sit inside `facts_root()`."""
    root = os.path.abspath(facts_root())
    path = os.path.abspath(os.path.join(root, *repo.split("/")))
    if os.path.commonpath([root, path]) != root or path == root:
        raise ContextError("invalid_repo", f"{repo!r} is not a repository name")
    return path


def fleet_command() -> str:
    """How the reader spells `fleet` from where it stands: a worker is in another repository."""
    checkout = os.path.abspath(fleet_platform.checkout_dir())
    here = os.path.abspath(os.getcwd())
    try:
        inside = os.path.commonpath([checkout, here]) == checkout
    except ValueError:
        inside = False
    return "uv run fleet" if inside else f"uv run --project {checkout} fleet"


# --- which repository ----------------------------------------------------------


def qualify(text: str) -> str | None:
    """A validated `<host>/<path>` or `local/<name>` identity, None for a bare name, or a refusal."""
    t = str(text or "").strip().lower()
    if t.endswith(".git"):
        t = t[:-4]
    t = t.rstrip("/")
    if not t or "\\" in t or ":" in t or t.startswith(("/", "~")):
        raise ContextError("invalid_repo", f"{text!r} is not a repository name", NAME_HINTS)
    parts = t.split("/")
    for part in parts:
        if not SEGMENT_RE.match(part) or part.endswith(".") or part.split(".")[0] in DEVICE_NAMES:
            raise ContextError("invalid_repo", f"{text!r} is not a repository name", NAME_HINTS)
    if len(parts) == 1:
        return None
    if parts[0] == LOCAL and len(parts) == 2:
        return t
    if parts[0] != LOCAL and "." in parts[0] and len(parts) >= 3:
        return t
    raise ContextError(
        "invalid_repo",
        f"{text!r} names no host: a repository is <host>/<path>, as in github.com/{t}",
        NAME_HINTS,
    )


NAME_HINTS = [
    "Name a repository as <host>/<owner>/<repo>, as local/<name>-<hash>, or by its checkout's path",
]

_SCHEME_RE = re.compile(r"^(?:https?|ssh|git)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>.+)$", re.I)
_SCP_RE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^/:]{2,}):(?!/)(?P<path>.+)$")


def remote_identity(url: str) -> str | None:
    """`<host>/<path>` for a remote URL on a host, None for a local path or anything else."""
    m = _SCHEME_RE.match(url.strip()) or _SCP_RE.match(url.strip())
    if not m:
        return None
    try:
        return qualify(f"{m['host']}/{m['path']}")
    except ContextError:
        return None


def _git(path: str, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", path, *args], capture_output=True, encoding="utf-8",
                              errors="replace", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def local_identity(base: str) -> str:
    name = re.sub(r"[^a-z0-9._-]+", "-", os.path.basename(base.rstrip("/\\")).lower())
    name = re.sub(r"\.git$", "", name).strip("-._") or "repo"
    digest = hashlib.sha256(os.path.normcase(os.path.realpath(base)).encode("utf-8")).hexdigest()[:8]
    return f"{LOCAL}/{name[:80]}-{digest}"


def checkout_identity(path: str) -> str:
    """The identity of the repository a checkout belongs to: its origin's, or its own path's.

    An origin is only ever read from a checkout, so its answer settles both
    questions in one `git`; asking for the common directory first would cost
    every results row a second process for the rare checkout with no origin.
    """
    origin = _git(path, "remote", "get-url", "origin")
    if origin:
        found = remote_identity(origin)
        if found:
            return found
        local = origin[7:] if origin.startswith("file://") else origin
        return local_identity(local if os.path.isabs(local) else os.path.join(path, local))
    common = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common and not _git(path, "rev-parse", "--git-dir"):
        raise ContextError("invalid_repo", f"{path} is not a git checkout", NAME_HINTS)
    return local_identity(os.path.dirname(common) if common.endswith(".git") else path)


def path_identity(path: str) -> str | None:
    """`checkout_identity`, or None for a path that is not a checkout here (a remote task's)."""
    if not path or not os.path.isdir(path):
        return None
    try:
        return checkout_identity(path)
    except ContextError:
        return None


def short_name(identity: str) -> str:
    last = identity.rsplit("/", 1)[-1]
    return re.sub(r"-[0-9a-f]{8}$", "", last) if identity.startswith(LOCAL + "/") else last


def known_repos() -> set:
    """Every identity that has facts here, or is in the registry map."""
    found = set()
    root = facts_root()
    for dirpath, _dirs, files in os.walk(root):
        if any(f.endswith(".md") for f in files):
            found.add(os.path.relpath(dirpath, root).replace(os.sep, "/"))
    path = os.environ.get("FLEET_REGISTRY_FILE") or os.path.join(registry_dir(), "repos.generated.yaml")
    try:
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        doc = {}
    found.update(_registry_identities(doc))
    return found


def _registry_identities(doc) -> list[str]:
    """Every identity the registry map's repositories resolve to, in its order."""
    idents = []
    for owner in doc.get("owners") or [] if isinstance(doc, dict) else []:
        for repo in (owner or {}).get("repos") or []:
            ident = remote_identity(str((repo or {}).get("url") or ""))
            if ident:
                idents.append(ident)
    return idents


def resolve_repo(text: str, hints: tuple = ()) -> str:
    """One identity for what a reader typed: a checkout path, an identity, or an unambiguous name."""
    raw = str(text or "").strip()
    if os.path.isabs(raw) or raw.startswith((".", "~")):
        path = os.path.expanduser(raw)
        if not os.path.isdir(path):
            raise ContextError("invalid_repo", f"no checkout at {raw}", NAME_HINTS)
        return checkout_identity(path)
    qualified = qualify(raw)
    if qualified:
        return qualified
    name = raw.lower()
    hits = sorted({h for h in (*hints, *known_repos()) if h and short_name(h) == name})
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise ContextError("ambiguous_repo", f"{raw!r} could be any of {len(hits)} repositories",
                           [f"Run `{fleet_command()} context repo {h}`" for h in hits[:3]])
    raise ContextError("unknown_repo", f"no known repository is called {raw!r}", NAME_HINTS)


# --- facts -----------------------------------------------------------------------


def stamp(when: datetime | None = None) -> str:
    return (when or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_text(text) -> str:
    """One line of fact, or a refusal: facts are a sentence or two, never a log."""
    line = " ".join(str(text or "").split())
    if not line:
        raise ContextError("empty_fact", "a fact needs some text", [f'Run `{fleet_command()} context learn '
                                                                    '<repo> "<fact>"`'])
    if re.fullmatch(r"<[^<>]*>", line):
        raise ContextError("empty_fact", f"{line} is the brief's placeholder, not a fact",
                           ["Say what you learned in one or two sentences, or leave learned: out"])
    if len(line) > MAX_FACT_CHARS:
        raise ContextError("fact_too_long", f"a fact is one or two sentences, at most {MAX_FACT_CHARS} "
                           f"characters; this one is {len(line)}", ["Split it, or say only what was learned"])
    return line


def render_fact(fid: str, repo: str, text: str, source: str, at: str, replaces: str | None) -> str:
    front = {"id": fid, "repo": repo, "at": at, "source": source}
    if replaces:
        front["replaces"] = replaces
    return "---\n" + yaml.safe_dump(front, sort_keys=False, allow_unicode=True) + "---\n" + text + "\n"


def write_fact(repo: str, fid: str, text: str, source: str, replaces: str | None = None,
               at: str | None = None) -> tuple[bool, str]:
    """(made it, its path). Never overwrites: an existing id is kept as it is."""
    path = os.path.join(repo_dir(repo), f"{fid}.md")
    made = fleet_platform.create_record(path, render_fact(fid, repo, text, source, at or stamp(), replaces), mode=0o600)
    return made, path


def parse_fact(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read().replace("\r\n", "\n")
        if not text.startswith("---\n"):
            return None
        _, front, body = text.split("---", 2)
        meta = yaml.safe_load(front)
    except (OSError, ValueError, yaml.YAMLError):
        return None
    fid = os.path.basename(path)[:-3]
    if not isinstance(meta, dict) or str(meta.get("id")) != fid or not body.strip():
        return None
    at = str(meta.get("at") or "")
    return {
        "id": fid, "at": at, "date": at[:10], "source": str(meta.get("source") or ""),
        "replaces": str(meta.get("replaces") or ""), "text": " ".join(body.split()), "path": path,
    }


def read_facts(repo: str) -> dict:
    """{live, replaced, contested, malformed} for one repository, newest first."""
    directory = repo_dir(repo)
    facts, malformed = [], []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        names = []
    for name in names:
        if name.startswith(".") or not name.endswith(".md"):
            continue
        fact = parse_fact(os.path.join(directory, name))
        (facts.append(fact) if fact else malformed.append(os.path.join(directory, name)))
    by_target: dict[str, list] = {}
    for fact in facts:
        if fact["replaces"]:
            by_target.setdefault(fact["replaces"], []).append(fact)
    live = [f for f in facts if f["id"] not in by_target]
    live.sort(key=lambda f: (f["at"], f["id"]), reverse=True)
    contested = {t: [f["id"] for f in fs if f in live] for t, fs in by_target.items()}
    return {
        "live": live,
        "replaced": [f for f in facts if f["id"] in by_target],
        "contested": {t: ids for t, ids in sorted(contested.items()) if len(ids) > 1},
        "malformed": malformed,
    }


def check_replaces(repo: str, replaces) -> str | None:
    if not replaces:
        return None
    fid = str(replaces).strip()
    if not FACT_ID_RE.match(fid):
        raise ContextError("invalid_fact", f"{fid!r} is not a fact id")
    if not os.path.exists(os.path.join(repo_dir(repo), f"{fid}.md")):
        raise ContextError("unknown_fact", f"{repo} has no fact {fid}",
                           [f"Run `{fleet_command()} context repo {repo} --all --fields replaces` to list its ids"])
    return fid


def learn(repo_text: str, text: str, replaces: str | None, source: str | None) -> tuple[str, bool, str, str]:
    """(repo, made it, id, path) for one fact said by a local agent."""
    repo = resolve_repo(repo_text)
    line = clean_text(text)
    replaces = check_replaces(repo, replaces)
    src = " ".join(str(source or "").split()) or f"agent:{os.environ.get('THURBOX_SESSION') or 'local'}"
    if len(src) > 120:
        raise ContextError("invalid_source", "a source is a task ref, agent:<who> or peer:<fleet>/<id>")
    # What it replaces is part of what is said: restating an old fact over a
    # newer one is a new statement, and a repeat of both is not.
    digest = hashlib.sha256(f"{repo}\n{line}\n{replaces or ''}".encode()).hexdigest()[:8]
    directory = repo_dir(repo)
    if os.path.isdir(directory):
        for name in os.listdir(directory):
            if name.endswith(f"-{digest}.md"):
                return repo, False, name[:-3], os.path.join(directory, name)
    fid = f"{datetime.now(UTC):%Y%m%d}-{digest}"
    made, path = write_fact(repo, fid, line, src, replaces)
    return repo, made, fid, path


def capture_learned(task, learned) -> list[str]:
    """Record a result's `learned:` entries as facts; the lines `collect` prints about it.

    Never raises and never holds a task: a bad entry is one line and is skipped.
    The id comes from the task and the entry's place, so a second pass over
    the same result makes nothing new.
    """
    if learned is None:
        return []
    if not isinstance(learned, list):
        return ["learned: not a list — skipped"]
    q = _queue()
    hints = tuple(filter(None, [path_identity(u["path"]) for u in q.task_repos(task)]
                         + [(task.doc.get("remote") or {}).get("repo")]))
    # The task's own date and not today's: a pass tomorrow must name the same file.
    day = re.sub(r"\D", "", str(task.doc.get("created") or "")[:10])
    if len(day) != 8:
        day = f"{datetime.now(UTC):%Y%m%d}"
    lines, made, kept = [], 0, 0
    for n, entry in enumerate(learned[:MAX_LEARNED], 1):
        new, skipped = _learn_one(task, n, entry, hints, day)
        if skipped:
            lines.append(skipped)
            continue
        made += new
        kept += not new
    return [*lines, _learned_summary(made, kept, len(learned))]


def _learn_one(task, n: int, entry, hints: tuple, day: str) -> tuple[bool, str]:
    """(whether the fact is new, "") for one `learned:` entry, or (False, the line saying why it was skipped)."""
    if not isinstance(entry, dict):
        return False, f"learned[{n}]: not a mapping — skipped"
    if not str(entry.get("repo") or "").strip():
        return False, f"learned[{n}]: no repo — skipped"
    try:
        repo = resolve_repo(str(entry["repo"]), hints)
        text = clean_text(entry.get("fact"))
        replaces = check_replaces(repo, entry.get("replaces"))
        new, _path = write_fact(repo, f"{day}-{task.topic}-{task.id}-{n}", text, task.ref, replaces)
    except ContextError as exc:
        return False, f"learned[{n}]: {exc.message} — skipped"
    except OSError as exc:
        return False, f"learned[{n}]: could not be written ({exc.strerror or exc}) — skipped"
    return new, ""


def _learned_summary(made: int, kept: int, total: int) -> str:
    summary = []
    if made or not kept:
        summary.append(f"{made} fact{'' if made == 1 else 's'} recorded")
    if kept:
        summary.append(f"{kept} already recorded")
    if total > MAX_LEARNED:
        summary.append(f"{total - MAX_LEARNED} over the limit of {MAX_LEARNED} — skipped")
    return ", ".join(summary)


# --- rendering ---------------------------------------------------------------------


def cut(text: str, full: bool) -> str:
    if full or len(text) <= TEXT_CAP:
        return text
    return f"{text[:TEXT_CAP].rstrip()} (truncated, {len(text)} chars — use --full)"


def help_line(hints: list) -> str:
    return fleet_platform.toon_list("help", hints)


def capped(render, counts: list, cap: int | None) -> str:
    """`render(counts)` with rows taken off its longest list until it fits `cap`."""
    out = render(counts)
    while cap and len(out.encode("utf-8")) > cap and any(counts):
        longest = max(range(len(counts)), key=lambda i: counts[i])
        counts[longest] -= 1
        out = render(counts)
    return out


def more_line(cuts: list) -> str | None:
    named = [f"{n} {what}" for n, what in cuts if n > 0]
    return fleet_platform.toon_field("more", " · ".join(named) + " — use --all") if named else None


# --- repo ------------------------------------------------------------------------


def repo_results(q, repo: str) -> list[dict]:
    """Every task on this repository that has a result, newest first."""
    queue = _queue()
    ids: dict[str, str | None] = {}
    rows = []
    for task in q.tasks.values():
        path = task.file(RESULT_FILE)
        if task.doc.get("host") or not os.path.exists(path):
            continue
        for unit in queue.task_repos(task):
            if unit["path"] not in ids:
                ids[unit["path"]] = path_identity(unit["path"])
            if ids[unit["path"]] == repo:
                when = str(task.doc.get("concluded_at") or task.doc.get("created") or "")
                rows.append({"task": task.ref, "state": task.state, "date": when[:10], "path": path, "at": when})
                break
    return sorted(rows, key=lambda r: (r["at"], r["task"]), reverse=True)


def prior_results(q, ref: str) -> list[dict]:
    queue = _queue()
    task = q.get(ref)
    rows = []
    for blocker in task.blockers:
        if queue.blocker_condition(blocker):
            continue
        try:
            up = q.get(blocker.get("task"))
        except queue.QueueError:
            continue
        rows.append({"task": up.ref, "state": up.state, "path": up.file(RESULT_FILE)})
    return rows


def _repo_prior(q, ref: str | None) -> list[dict]:
    if not ref:
        return []
    queue = _queue()
    try:
        return prior_results(q, ref)
    except queue.QueueError as exc:
        raise ContextError("unknown_task", str(exc), [f"Run `{fleet_command()} queue list`"]) from exc


def _repo_hints(fleet: str, repo: str, facts: dict, every: bool, peered: list | None, peer_facts: list) -> list:
    hints = [f'Run `{fleet} context learn {repo} "<fact>"` to record what you learned']
    if peer_facts:
        hints.append(f'Run `{fleet} context learn {repo} "<fact>" --source peer:<fleet>/<id>` to keep a peer\'s fact')
    elif peered is None:
        hints.append(f"Run `{fleet} context repo {repo} --peers` to add other fleets' facts")
    if facts["live"] and not every:
        hints.append(f"Run `{fleet} context repo {repo} --all --full` for every fact in full")
    return hints


def _fact_lines(repo: str, facts: dict, fields: list, full: bool, nfacts: int) -> list:
    lines = [fleet_platform.toon_field(
        "facts", f"{len(facts['live'])} live · {len(facts['replaced'])} replaced"
        if facts["live"] or facts["replaced"] else f"0 live for {repo}")]
    lines.append(fleet_platform.toon_field("repo", repo))
    if facts["live"]:
        rows = [dict(f, text=cut(f["text"], full)) for f in facts["live"][:nfacts]]
        lines.append(fleet_platform.toon_table("live", fields, rows))
    if facts["contested"]:
        lines.append(fleet_platform.toon_field("contested", " ; ".join(
            f"{t} replaced by {' · '.join(ids)}" for t, ids in facts["contested"].items())))
    return lines


def _peer_lines(peered: list | None, peer_facts: list, peer_results: list, full: bool, npf: int, npr: int) -> list:
    if peered is None:
        return []
    states = {w: sum(1 for p in peered if p["status"] == w) for w in ("ok", "stale", "unreachable", "refused")}
    lines = [fleet_platform.toon_field("peers", f"{len(peered)} · " + " · ".join(
        f"{w} {n}" for w, n in states.items()) if peered else "0 — no other fleet found")]
    if peered:
        lines.append(fleet_platform.toon_table("fleets", ["fleet", "status", "age", "facts", "reason"], [
            dict(p, facts=len(p["facts"])) for p in peered]))
    if peer_facts and npf:
        lines.append(fleet_platform.toon_table("peer_facts", ["fleet", "id", "date", "source", "text"],
                                               [dict(f, text=cut(f["text"], full)) for f in peer_facts[:npf]]))
    if peer_results and npr:
        lines.append(fleet_platform.toon_table("peer_results", ["fleet", "task", "state", "date", "summary"],
                                               peer_results[:npr]))
    return lines


def cmd_repo(opts: dict) -> str:
    if opts.get("wire"):
        return wire_view(str(opts["args"][0]).strip())
    queue = _queue()
    repo = resolve_repo(opts["args"][0])
    facts = read_facts(repo)
    fields = list(FACT_FIELDS) + [f for f in opts["fields"] if f not in FACT_FIELDS]
    full, every = opts["full"], opts["all"]
    q = queue.Queue(queue.queue_root(), scope="all")
    results = repo_results(q, repo)
    prior = _repo_prior(q, opts["task"])
    narrative = os.path.join(registry_dir(), "context", f"{short_name(repo)}.md")
    fleet = fleet_command()
    peered = peer_view(repo, opts.get("refresh", False), opts.get("timeout")) if opts.get("peers") else None
    peer_facts = sorted(({**f, "fleet": p["fleet"]} for p in peered or [] for f in p["facts"]),
                        key=lambda f: (f["at"], f["fleet"], f["id"]), reverse=True)
    peer_results = [{**r, "fleet": p["fleet"]} for p in peered or [] for r in p["results"]]
    hints = _repo_hints(fleet, repo, facts, every, peered, peer_facts)

    def render(counts):
        nfacts, nresults, npf, npr = counts
        lines = _fact_lines(repo, facts, fields, full, nfacts)
        lines.append(fleet_platform.toon_field("narrative", narrative if os.path.exists(narrative) else "none"))
        lines.append(fleet_platform.toon_field("results", f"{len(results)} for {repo}"))
        if results and nresults:
            lines.append(fleet_platform.toon_table("recent", ["task", "state", "date", "path"], results[:nresults]))
        if prior:
            lines.append(fleet_platform.toon_table("prior", ["task", "state", "path"], prior))
        if facts["malformed"]:
            lines.append(fleet_platform.toon_field(
                "skipped", f"{len(facts['malformed'])} malformed fact file(s) — {facts['malformed'][0]}"))
        lines += _peer_lines(peered, peer_facts, peer_results, full, npf, npr)
        more = more_line([(len(facts["live"]) - nfacts, "facts"),
                          (len(results) - min(nresults, len(results)), "results"),
                          (len(peer_facts) - npf, "peer facts"),
                          (len(peer_results) - npr, "peer results")])
        if more:
            lines.append(more)
        lines.append(help_line(hints))
        return "\n".join(lines) + "\n"

    if every:
        return render([len(facts["live"]), len(results), len(peer_facts), len(peer_results)])
    return capped(render, [min(FACT_ROWS, len(facts["live"])), min(RESULT_ROWS, len(results)),
                           min(PEER_FACT_ROWS, len(peer_facts)), min(PEER_RESULT_ROWS, len(peer_results))],
                  None if full else REPO_BYTES)


PRIOR_BYTES = 2048


def worker_bundle(repo: str | None, ref: str) -> str:
    """CONTEXT.md: `repo <repo> --task <ref>`, rendered for a worker that cannot run it.

    A remote host has no control plane, so its worker reads this file, copied
    beside its worktree at dispatch, instead of the command a local brief
    names. It is SELF-CONTAINED where that matters: the result of each task
    this one waits on is inlined, capped at PRIOR_BYTES in all, because the
    path `--task` would name is on this machine. Every other path in it is
    this machine's too, and the header says so. Never raises: a bundle that
    cannot be rendered says why, and the worker starts anyway.
    """
    head = [
        f"# Project memory for {ref}",
        "",
        "Rendered by the control plane when this task was dispatched. This host",
        "cannot run `fleet context`, and the paths and commands below are the",
        "control plane's: read this file instead, and report what you learn in",
        "result.md's `learned:` under the `repo:` named here.",
        "",
        "```toon",
    ]
    if repo:
        try:
            body = cmd_repo({"args": [repo], "task": None, "fields": [], "full": False, "all": False})
        except (ContextError, OSError) as exc:
            body = fleet_platform.toon_field("facts", f"not rendered — {getattr(exc, 'message', exc)}") + "\n"
    else:
        body = (fleet_platform.toon_field("facts", "none — the host's origin could not be read or names no"
                                          " forge, so no store here is this repository's") + "\n"
                + fleet_platform.toon_field("repo", "unknown") + "\n")
    lines = [*head, body.rstrip("\n"), "```"]
    q = _queue()
    try:
        prior = prior_results(q.Queue(q.queue_root(), scope="all"), ref)
    except q.QueueError:
        prior = []
    budget = PRIOR_BYTES
    for row in prior:
        try:
            with open(row["path"], encoding="utf-8") as fh:
                text = fh.read().strip()
        except OSError:
            text = "(no result.md)"
        if len(text.encode("utf-8")) > budget:
            text = text.encode("utf-8")[:max(budget, 0)].decode("utf-8", "ignore").rstrip()
            text += "\n(truncated)"
        budget -= len(text.encode("utf-8"))
        lines += ["", f"## Prior result: {row['task']} ({row['state']})", "", text]
        if budget <= 0:
            break
    return "\n".join(lines) + "\n"


# --- what this fleet shares, and what its peers share with it -------------------------


def share_conf() -> str:
    """The operator's `fleet.conf`, else the tracked example — the file `install-extension` reads NAME from."""
    root = os.environ.get("FLEET_NAME_ROOT") or fleet_platform.checkout_dir()
    own = os.path.join(root, "orchestration", "fleet.conf")
    return own if os.path.isfile(own) else os.path.join(root, "orchestration", "fleet.example.conf")


def shared() -> set:
    """What this fleet serves a peer's read: `facts` unless SHARE says otherwise.

    A word fleet does not know is dropped, never widened into something it
    might mean: prompts, briefs, OPERATOR.md, run logs and narrative have no
    word, so no setting serves them.
    """
    words = {w.strip().lower() for w in re.split(r"[,\s]+", agent_settings.read_conf(share_conf()).get("SHARE", ""))
             if w.strip()}
    if not words:
        return {"facts"}
    if words & set(SHARE_OFF):
        return set()
    return words & set(SHARE_WORDS)


def summary_of(path: str) -> str:
    """The first line of a result's prose, which is all `collect` ever printed of it."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            _meta, body = _queue().parse_result(fh.read())
    except OSError:
        return ""
    line = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    return cut(line, False)


def wire_view(raw: str) -> str:
    """The hidden machine wire: one JSON document a peer's `--peers` read parses, and no agent reads.

    Asked by full identity only: a bare name would resolve against THIS
    fleet's map, and a path would read this machine's filesystem, for a reader
    who is not here.
    """
    if os.path.isabs(raw) or raw.startswith((".", "~")):
        raise ContextError("invalid_repo", "a peer is asked by repository identity, never by a path", NAME_HINTS)
    repo = qualify(raw)
    if not repo:
        raise ContextError("invalid_repo", f"{raw!r} is a bare name; a peer is asked by <host>/<path>", NAME_HINTS)
    share = shared()
    doc: dict = {"wire": WIRE, "repo": repo, "share": sorted(share), "facts": [], "results": []}
    if not share:
        doc["refused"] = "this fleet shares nothing (SHARE=none)"
        return json.dumps(doc, ensure_ascii=False) + "\n"
    if "facts" in share:
        facts = read_facts(repo)
        doc["live"], doc["replaced"] = len(facts["live"]), len(facts["replaced"])
        doc["facts"] = [{"id": f["id"], "at": f["at"], "source": f["source"][:SOURCE_CHARS],
                         "text": f["text"][:MAX_FACT_CHARS], "replaces": f["replaces"]}
                        for f in facts["live"][:WIRE_FACTS]]
    if "results" in share:
        queue = _queue()
        rows = repo_results(queue.Queue(queue.queue_root(), scope="all"), repo)
        doc["results"] = [{"task": r["task"], "state": r["state"], "date": r["date"],
                           "summary": summary_of(r["path"])} for r in rows[:WIRE_RESULTS]]
    return json.dumps(doc, ensure_ascii=False) + "\n"


def plain(value, cap: int) -> str:
    return CONTROL_RE.sub(" ", " ".join(str(value).split()))[:cap]


def _wire_facts(facts: list) -> list[dict] | None:
    """A peer's facts, bounded, or None when any one of them is malformed."""
    good = []
    for fact in facts:
        if not isinstance(fact, dict) or not FACT_ID_RE.match(str(fact.get("id", ""))) \
                or not isinstance(fact.get("text"), str) or not fact["text"].strip():
            return None
        at = plain(fact.get("at") or "", 40)
        good.append({"id": fact["id"], "at": at, "date": at[:10],
                     "source": plain(fact.get("source") or "", SOURCE_CHARS),
                     "text": plain(fact["text"], MAX_FACT_CHARS)})
    return good


def _wire_results(results: list) -> list[dict] | None:
    """A peer's results, bounded, or None when any one of them is malformed."""
    good = []
    for row in results:
        if not isinstance(row, dict) or not TASK_REF_RE.match(str(row.get("task", ""))):
            return None
        good.append({"task": row["task"], "state": plain(row.get("state") or "", 20),
                     "date": plain(row.get("date") or "", 10),
                     "summary": plain(row.get("summary") or "", TEXT_CAP)})
    return good


def check_wire(text: str, repo: str) -> tuple[dict | None, str]:
    """(a peer's answer, validated and bounded, "") or (None, why it is not one).

    A peer is another fleet's code on another machine, so nothing it prints is
    trusted to be the shape asked for: an answer that breaks the wire anywhere
    is rejected whole, never shown in part.
    """
    if len(text.encode("utf-8")) > WIRE_BYTES:
        return None, f"answered more than {WIRE_BYTES} bytes"
    lines = [ln for ln in text.splitlines() if ln.strip()]
    line = next((ln for ln in reversed(lines) if ln.lstrip().startswith("{")), None)
    if line is None:
        said = next((ln for ln in lines if ln.startswith("error: ")), lines[0] if lines else "")
        return None, plain(said, 160) or "answered nothing"
    try:
        doc = json.loads(line)
    except ValueError:
        return None, "answered JSON that does not parse"
    if not isinstance(doc, dict) or doc.get("wire") != WIRE:
        return None, f"answered no {WIRE} wire — an older fleet?"
    if doc.get("repo") != repo:
        return None, "answered for another repository"
    if doc.get("refused"):
        return {"refused": plain(doc["refused"], 160), "facts": [], "results": []}, ""
    facts, results = doc.get("facts"), doc.get("results", [])
    if not isinstance(facts, list) or not isinstance(results, list):
        return None, "answered a wire whose facts or results are not lists"
    if len(facts) > WIRE_FACTS or len(results) > WIRE_RESULTS:
        return None, f"answered more than {WIRE_FACTS} facts or {WIRE_RESULTS} results"
    good_facts = _wire_facts(facts)
    if good_facts is None:
        return None, "answered a malformed fact"
    good_results = _wire_results(results)
    if good_results is None:
        return None, "answered a malformed result"
    return {"refused": "", "facts": good_facts, "results": good_results}, ""


def peer_cache_path() -> str:
    return os.path.join(os.path.dirname(_peers().cache_path()), "context.json")


def load_peer_cache() -> dict:
    try:
        with open(peer_cache_path(), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def save_peer_cache(doc: dict) -> None:
    """Under the same rule as the peers cache beside it: one writer, and a failed write costs only a re-read."""
    path = peer_cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with fleet_platform.exclusive_lock(path + ".lock"):
            fleet_platform.write_record(path, json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    except (OSError, fleet_platform.LockHeld):
        pass


def strip_for_peer(key: str) -> bool:
    """A local peer runs with none of THIS fleet's relocations, so it reads its own registry and SHARE."""
    return key.startswith("FLEET_") and key != "FLEET_STUB_ROOT"


def ask_peer(entry: dict, repo: str, timeout: float) -> tuple[dict | None, str]:
    peers = _peers()
    proc, why = peers.run_read(entry, timeout, "context.py", ("repo", repo, "--wire", "json"), strip_for_peer)
    if proc is None:
        return None, why
    answer, why = check_wire(proc.stdout, repo)
    if answer is None and proc.returncode and not proc.stdout.strip():
        why = _queue().first_line(proc) or why
    return answer, why


def _remember_peer(seen: dict, reply: tuple[dict | None, str], now: float) -> None:
    answer, why = reply
    seen["tried_at"] = now
    if answer is not None:
        seen.update(answer=answer, ok_at=now, error="", failing_since=None)
    else:
        seen["error"] = why
        seen["failing_since"] = seen.get("failing_since") or now


def _peer_status(seen: dict, answer: dict | None, error: str, now: float) -> tuple[str, float, str]:
    """(status, age, the reason shown) for one peer's cached reading."""
    if not error and answer and answer.get("refused"):
        return "refused", now - float(seen.get("ok_at") or now), answer["refused"]
    if not error:
        return "ok", now - float(seen.get("ok_at") or now), error
    if answer:
        return "stale", now - float(seen.get("ok_at") or now), error
    return "unreachable", now - float(seen.get("failing_since") or now), error


def peer_view(repo: str, refresh: bool, timeout: float) -> list[dict]:
    """Every other fleet's answer about `repo`: read now, from the cache, or why it could not be."""
    peers = _peers()
    entries = peers.peers(peers.load_cache())
    old = load_peer_cache()
    now = time.time()

    def key(entry):
        return f"{entry['key']} {repo}"

    due = [e for e in entries if refresh or now - float((old.get(key(e)) or {}).get("tried_at") or 0) >= peers.TTL]
    answers = {}
    if due:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(due)) as pool:
            for entry, answer in zip(due, pool.map(lambda e: ask_peer(e, repo, timeout), due), strict=True):
                answers[key(entry)] = answer
    cache = {k: v for k, v in old.items() if isinstance(v, dict)
             and now - float(v.get("tried_at") or 0) < PEER_CACHE_DAYS * 86400}
    out = []
    for entry in entries:
        seen = dict(old.get(key(entry)) or {})
        if key(entry) in answers:
            _remember_peer(seen, answers[key(entry)], now)
        seen["label"] = entry["label"]
        cache[key(entry)] = seen
        answer = seen.get("answer")
        status, age, error = _peer_status(seen, answer, seen.get("error") or "", now)
        shown = answer if status in ("ok", "stale") else {}
        out.append({"fleet": entry["label"], "key": entry["key"], "status": status, "age": int(age),
                    "reason": plain(error, 160), "facts": (shown or {}).get("facts") or [],
                    "results": (shown or {}).get("results") or []})
    save_peer_cache(cache)
    return out


# --- lead and pending -----------------------------------------------------------------


def facts_since(day: str) -> int:
    count = 0
    for dirpath, _dirs, files in os.walk(facts_root()):
        for name in files:
            if name.endswith(".md") and not name.startswith("."):
                fact = parse_fact(os.path.join(dirpath, name))
                count += bool(fact and fact["date"] >= day)
    return count


def pending_rows(q, stalled: set) -> list[dict]:
    """Every task a person has to act on next, with the command or file that acts on it."""
    queue = _queue()
    fleet = fleet_command()
    rows = []
    for task in sorted(q.tasks.values(), key=lambda t: t.ref):
        n = len(rows)
        conditions = [queue.blocker_condition(b) for b in task.blockers if queue.blocker_condition(b)]
        publish = (task.doc.get("publish") or {}).get("state")
        result = task.file(RESULT_FILE)
        if task.ref in stalled:
            rows.append({"task": task.ref, "why": "stalled", "next": f"{fleet} queue show {task.ref}"})
        elif q.is_ready(task):
            rows.append({"task": task.ref, "why": "ready", "next": f"{fleet} queue dispatch {task.ref}"})
        elif task.state == "queued" and conditions:
            rows.append({"task": task.ref, "why": "condition",
                         "next": f"{conditions[0]} — then {fleet} queue block {task.ref} --clear --condition …"})
        elif task.state in ("stuck", "failed"):
            rows.append({"task": task.ref, "why": task.state, "next": result})
        elif task.state == "dispatched" and publish == "unverified":
            rows.append({"task": task.ref, "why": "held", "next": result})
        elif task.state == "done" and queue.task_publish(task)[0] == "served" and not queue.review_closed(task):
            rows.append({"task": task.ref, "why": "review", "next": f"{fleet} queue reviewed {task.ref}"})
        # Whom the decision belongs to, as the record names them; null when
        # nobody was recorded.
        if len(rows) > n:
            rows[-1]["by"] = queue.requester(task.doc.get("requested_by"))
    return rows


AWAITING_MERGE = "awaiting merge"
TOPIC_ORDER = ("stalled", "ready", "stuck", "failed", "held", "condition", "review", "working", AWAITING_MERGE,
               "waiting", "landed")


def topic_row(slug: str, tasks: list, pending: dict, by=None) -> dict:
    words = {}
    for task in tasks:
        why = pending.get(task.ref, {}).get("why")
        word = why or {"dispatched": "working", "done": AWAITING_MERGE, "queued": "waiting",
                       "landed": "landed", "abandoned": "landed"}.get(task.state, task.state)
        words.setdefault(word, []).append(task.ref)
    state = next((w for w in TOPIC_ORDER if w in words), "landed")
    refs = words.get(state, [])
    nxt = {
        "ready": f"dispatch {len(refs)} ready",
        "stalled": f"look at {refs[0] if refs else ''}",
        "stuck": f"read {refs[0] if refs else ''}",
        "failed": f"read {refs[0] if refs else ''}",
        "held": "an artifact is not proven",
        "condition": "a person clears a condition",
        "review": "waiting on reader",
        "working": f"{len(refs)} working",
        AWAITING_MERGE: f"{len(refs)} {AWAITING_MERGE}",
        "waiting": "waiting on another task",
        "landed": "archives on the next collect",
    }[state]
    return {"slug": slug, "state": state, "by": by, "next": nxt}


def lead_view(every: bool) -> str:
    queue = _queue()
    q = queue.Queue(queue.queue_root())
    stalled = {s["task"] for s in queue.stalled_tasks(q)}
    pending = {r["task"]: r for r in pending_rows(q, stalled)}
    topics = [topic_row(slug, tasks, pending, queue.requester(q.topics.get(slug, {}).get("requested_by")))
              for slug, tasks in sorted(q.by_topic().items()) if tasks]
    topics.sort(key=lambda r: (TOPIC_ORDER.index(r["state"]), r["slug"]))
    ready = sum(1 for r in pending.values() if r["why"] == "ready")
    since = f"{datetime.now(UTC) - timedelta(days=1):%Y-%m-%d}"
    fleet = fleet_command()
    hints = []
    if ready:
        hints.append(f"Run `{fleet} queue plan` to dispatch the ready task{'s' if ready > 1 else ''}")
    if pending:
        hints.append(f"Run `{fleet} context pending` for what waits on a decision")
    if not topics:
        hints.append(f"Run `{fleet} queue topic add <slug> --title <title> --prompt <prompt>` to open one")
    elif not every and len(topics) > TOPIC_ROWS:
        hints.append(f"Run `{fleet} context --all` for every topic")
    head = fleet_platform.toon_field(
        "topics", f"{len(topics)} open · ready {ready} · stalled {len(stalled)} · facts {facts_since(since)}"
        f" since {since}")

    def render(counts):
        [n] = counts
        lines = [head]
        if topics:
            lines.append(fleet_platform.toon_table("open", ["slug", "state", "by", "next"], topics[:n]))
        more = more_line([(len(topics) - n, "topics")])
        if more:
            lines.append(more)
        lines.append(help_line(hints[:2]))
        return "\n".join(lines) + "\n"

    if every:
        return render([len(topics)])
    return capped(render, [min(TOPIC_ROWS, len(topics))], LEAD_BYTES)


def pending_view(every: bool) -> str:
    queue = _queue()
    q = queue.Queue(queue.queue_root())
    rows = pending_rows(q, {s["task"] for s in queue.stalled_tasks(q)})
    counts = {}
    for row in rows:
        counts[row["why"]] = counts.get(row["why"], 0) + 1
    fleet = fleet_command()
    head = fleet_platform.toon_field("pending", (
        f"{len(rows)} · " + " · ".join(f"{w} {counts.get(w, 0)}" for w in
                                         ("ready", "stalled", "condition", "review", "held", "stuck", "failed"))
    ) if rows else "0 — nothing waits on a decision")
    hints = ([f"Run `{fleet} queue plan` to see the ready set and its overlaps"] if counts.get("ready") else []) \
        + [f"Run `{fleet} context` for every topic's state"]

    def render(view):
        [n] = view
        lines = [head]
        if rows:
            lines.append(fleet_platform.toon_table("tasks", ["task", "why", "by", "next"], rows[:n]))
        more = more_line([(len(rows) - n, "tasks")])
        if more:
            lines.append(more)
        lines.append(help_line(hints))
        return "\n".join(lines) + "\n"

    if every:
        return render([len(rows)])
    return capped(render, [min(PENDING_ROWS, len(rows))], PENDING_BYTES)


# --- learn ---------------------------------------------------------------------------


def cmd_learn(opts: dict) -> str:
    repo, made, fid, path = learn(opts["args"][0], opts["args"][1], opts["replaces"], opts["source"])
    live = len(read_facts(repo)["live"])
    fleet = fleet_command()
    return "\n".join([
        fleet_platform.toon_field("facts", f"{live} live for {repo}"),
        fleet_platform.toon_field("recorded" if made else "already", fid),
        fleet_platform.toon_field("path", path),
        help_line([f"Run `{fleet} context repo {repo}` to read it back"]),
    ]) + "\n"


# --- the command line ------------------------------------------------------------------

# subcommand -> (positionals, {flag: takes a value}, usage, about, {flag: about})
SUBCOMMANDS = {
    "lead": (
        [], {"--all": False, "--full": False},
        "fleet context [lead] [--all]",
        "where every open topic stands, what is ready and stalled, and how many facts are new",
        {"--all": "every topic, past the 1.5 KB cap", "--full": "accepted; nothing here is truncated"},
    ),
    "pending": (
        [], {"--all": False},
        "fleet context pending [--all]",
        "every task waiting on a decision, and the command or file that acts on it",
        {"--all": "every task, past the cap"},
    ),
    "repo": (
        ["repo"], {"--all": False, "--full": False, "--task": True, "--fields": True, "--peers": False,
                   "--refresh": False, "--timeout": True, "--wire": True},
        "fleet context repo <repo> [--task <ref>] [--all] [--full] [--fields replaces,at] "
        "[--peers [--refresh] [--timeout <s>]]",
        "a repository's live facts, its narrative, and the results of tasks on it",
        {"--task": "add the results of the tasks <ref> waits on", "--all": "every fact and result, uncapped",
         "--full": "no fact text truncated", "--fields": "add replaces and/or at to each fact row",
         "--peers": "add what every other fleet shares about it, read over ssh and never written",
         "--refresh": "with --peers, ask every peer now instead of within the cache's seconds",
         "--timeout": "with --peers, how long one peer may take (default 15)"},
    ),
    "learn": (
        ["repo", "fact"], {"--replaces": True, "--source": True},
        'fleet context learn <repo> "<fact>" [--replaces <id>] [--source <src>]',
        "record one fact about a repository; the same fact again answers already",
        {"--replaces": "the id of the fact this one supersedes; that file stays as history",
         "--source": "who says so: a task ref, agent:<who> or peer:<fleet>/<id>"},
    ),
}


def usage_error(message: str, sub: str | None = None) -> ContextError:
    usage = SUBCOMMANDS[sub][2] if sub else "fleet context [lead|pending|repo|learn] ..."
    return ContextError("usage", message, [f"Run `{fleet_command()} context {sub or ''} --help`".replace(
        "  ", " ") + f" — {usage}"], exit_code=2)


def _read_words(rest: list, flags: dict, opts: dict, sub: str) -> bool:
    """Fill `opts` from the words after the subcommand; True when one asked for --help."""
    wants_help = False
    while rest:
        word = rest.pop(0)
        if word == "--":
            opts["args"] += rest
            break
        if word in ("-h", "--help"):
            wants_help = True
            continue
        if word.startswith("--"):
            _read_flag(word, rest, flags, opts, sub)
            continue
        opts["args"].append(word)
    return wants_help


def _read_flag(word: str, rest: list, flags: dict, opts: dict, sub: str) -> None:
    name, eq, value = word.partition("=")
    if name not in flags:
        raise usage_error(f"unknown flag {name}", sub)
    if not flags[name]:
        opts[name[2:]] = True
        return
    if not eq:
        if not rest:
            raise usage_error(f"{name} needs a value", sub)
        value = rest.pop(0)
    opts[name[2:]] = value


def _check_fields(opts: dict) -> None:
    if not isinstance(opts["fields"], str):
        return
    fields = [f.strip() for f in opts["fields"].split(",") if f.strip()]
    unknown = [f for f in fields if f not in EXTRA_FIELDS + tuple(FACT_FIELDS)]
    if unknown:
        raise ContextError("bad_argument", f"no field {unknown[0]!r}; --fields takes "
                           + ",".join(EXTRA_FIELDS), [f"Run `{fleet_command()} context repo --help`"])
    opts["fields"] = fields


def _check_wire(opts: dict, sub: str) -> None:
    if opts["wire"] is not None:
        # The wire is one fleet answering another: it never fans out again, and
        # never carries what --task would add, the results this task waits on.
        if opts["wire"] != "json":
            raise usage_error(f"no wire {opts['wire']!r}", sub)
        if opts["peers"] or opts["refresh"] or opts["timeout"] is not None or opts["task"]:
            raise usage_error("--wire answers one read: no --peers, --refresh, --timeout or --task", sub)
    elif (opts["refresh"] or opts["timeout"] is not None) and not opts["peers"]:
        raise usage_error("--refresh and --timeout go with --peers", sub)


def _check_timeout(opts: dict) -> None:
    if opts["timeout"] is not None:
        try:
            opts["timeout"] = float(opts["timeout"])
        except ValueError:
            opts["timeout"] = -1.0
        if not 0 < opts["timeout"] <= 300:
            raise ContextError("bad_argument", "--timeout is a number of seconds, above 0 and at most 300",
                               [f"Run `{fleet_command()} context repo --help`"])
    elif opts["peers"]:
        opts["timeout"] = _peers().TIMEOUT


def parse(argv: list) -> tuple[str, dict | None]:
    """(subcommand, options), or (subcommand, None) for --help. Raises usage errors before anything runs."""
    sub = "lead"
    rest = list(argv)
    if rest and not rest[0].startswith("-"):
        sub = rest.pop(0)
        if sub not in SUBCOMMANDS:
            raise usage_error(f"no subcommand {sub!r}")
    positionals, flags, *_ = SUBCOMMANDS[sub]
    opts = {"args": [], "all": False, "full": False, "task": None, "fields": [], "replaces": None, "source": None,
            "peers": False, "refresh": False, "timeout": None, "wire": None}
    if _read_words(rest, flags, opts, sub):
        return sub, None
    if len(opts["args"]) != len(positionals):
        raise ContextError("bad_argument", f"{sub} takes {len(positionals)} argument(s), "
                           f"{', '.join(f'<{p}>' for p in positionals) or 'none'}; got {len(opts['args'])}",
                           [f"Run `{fleet_command()} context {sub} --help`"])
    _check_fields(opts)
    _check_wire(opts, sub)
    _check_timeout(opts)
    return sub, opts


def help_text(sub: str) -> str:
    _pos, _flags, usage, about, flag_about = SUBCOMMANDS[sub]
    rows = [{"flag": f, "about": a} for f, a in flag_about.items()]
    others = [s for s in SUBCOMMANDS if s != sub]
    return "\n".join([
        fleet_platform.toon_field("usage", usage),
        fleet_platform.toon_field("about", about),
        fleet_platform.toon_table("flags", ["flag", "about"], rows),
        help_line([f"Run `{fleet_command()} context {s} --help`" for s in others]),
    ]) + "\n"


def error_text(exc: ContextError) -> str:
    return "\n".join([
        fleet_platform.toon_field("error", exc.message),
        fleet_platform.toon_field("code", exc.code),
        help_line(exc.hints or [f"Run `{fleet_command()} context --help`"]),
    ]) + "\n"


def main(argv: list) -> int:
    try:
        sub, opts = parse(argv)
        if opts is None:
            out = help_text(sub)
        elif sub == "lead":
            out = lead_view(opts["all"])
        elif sub == "pending":
            out = pending_view(opts["all"])
        elif sub == "repo":
            out = cmd_repo(opts)
        else:
            out = cmd_learn(opts)
    except ContextError as exc:
        sys.stdout.write(error_text(exc))
        return exc.exit_code
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
