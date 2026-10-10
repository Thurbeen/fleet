#!/usr/bin/env python3
"""`fleet context`: what fleet remembers, carried in one capped read to whoever needs it next.

    uv run fleet context                      the lead's summary (≤ 1.5 KB)
    uv run fleet context lead [--all]         the same, by name
    uv run fleet context pending [--all]      every task waiting on a decision
    uv run fleet context repo <repo> [--task <ref>] [--all] [--full] [--fields replaces,at]
                                              a project's live facts and recent results (≤ 4 KB)
    uv run fleet context learn <repo> "<fact>" [--replaces <id>] [--source <src>]
                                              record one fact; a repeat is `already`;
                                              `--` before a fact that starts with `-`

A FACT IS ONE FILE, CREATED ONCE AND NEVER EDITED:
`registry/facts/<repo>/<id>.md`, YAML front matter (`id`, `repo`, `at`,
`source`, and `replaces` when it supersedes one) over one or two sentences.
`fleet_platform.create_once` writes it — a temp file of the writer's own,
linked to the name, which fails when the name exists — so any number of
workers and fleet processes write at once with no lock, a reader never sees
half a fact, and a write that would collide keeps the first. The id is derived
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

`registry/` is beside FLEET_REGISTRY_FILE when that is set (the test harness
relocates the map, and with it the facts) and this checkout's otherwise.
Everything here is gitignored working state, like the queue it reads.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import subprocess
import sys
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


def _queue():
    """queue.py, loaded on first use: it loads this module back from `collect`."""
    return _load_sibling("fleet_queue", "queue.py")


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
    """The identity of the repository a checkout belongs to: its origin's, or its own path's."""
    if not _git(path, "rev-parse", "--git-dir"):
        raise ContextError("invalid_repo", f"{path} is not a git checkout", NAME_HINTS)
    origin = _git(path, "remote", "get-url", "origin")
    if origin:
        found = remote_identity(origin)
        if found:
            return found
        local = origin[7:] if origin.startswith("file://") else origin
        return local_identity(local if os.path.isabs(local) else os.path.join(path, local))
    common = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
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
    for owner in doc.get("owners") or [] if isinstance(doc, dict) else []:
        for repo in (owner or {}).get("repos") or []:
            ident = remote_identity(str((repo or {}).get("url") or ""))
            if ident:
                found.add(ident)
    return found


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
    made = fleet_platform.create_once(path, render_fact(fid, repo, text, source, at or stamp(), replaces))
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
    hints = tuple(filter(None, (path_identity(u["path"]) for u in q.task_repos(task))))
    # The task's own date and not today's: a pass tomorrow must name the same file.
    day = re.sub(r"\D", "", str(task.doc.get("created") or "")[:10])
    if len(day) != 8:
        day = f"{datetime.now(UTC):%Y%m%d}"
    lines, made, kept = [], 0, 0
    for n, entry in enumerate(learned[:MAX_LEARNED], 1):
        if not isinstance(entry, dict):
            lines.append(f"learned[{n}]: not a mapping — skipped")
            continue
        if not str(entry.get("repo") or "").strip():
            lines.append(f"learned[{n}]: no repo — skipped")
            continue
        try:
            repo = resolve_repo(str(entry["repo"]), hints)
            text = clean_text(entry.get("fact"))
            replaces = check_replaces(repo, entry.get("replaces"))
            new, _path = write_fact(repo, f"{day}-{task.topic}-{task.id}-{n}", text, task.ref, replaces)
        except ContextError as exc:
            lines.append(f"learned[{n}]: {exc.message} — skipped")
            continue
        except OSError as exc:
            lines.append(f"learned[{n}]: could not be written ({exc.strerror or exc}) — skipped")
            continue
        made += new
        kept += not new
    summary = []
    if made or not kept:
        summary.append(f"{made} fact{'' if made == 1 else 's'} recorded")
    if kept:
        summary.append(f"{kept} already recorded")
    if len(learned) > MAX_LEARNED:
        summary.append(f"{len(learned) - MAX_LEARNED} over the limit of {MAX_LEARNED} — skipped")
    return [*lines, ", ".join(summary)]


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
        path = task.file("result.md")
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
        rows.append({"task": up.ref, "state": up.state, "path": up.file("result.md")})
    return rows


def cmd_repo(opts: dict) -> str:
    queue = _queue()
    repo = resolve_repo(opts["args"][0])
    facts = read_facts(repo)
    fields = list(FACT_FIELDS) + [f for f in opts["fields"] if f not in FACT_FIELDS]
    full, every = opts["full"], opts["all"]
    q = queue.Queue(queue.queue_root(), scope="all")
    results = repo_results(q, repo)
    prior = []
    if opts["task"]:
        try:
            prior = prior_results(q, opts["task"])
        except queue.QueueError as exc:
            raise ContextError("unknown_task", str(exc), [f"Run `{fleet_command()} queue list`"]) from exc
    narrative = os.path.join(registry_dir(), "context", f"{short_name(repo)}.md")
    fleet = fleet_command()
    hints = [f'Run `{fleet} context learn {repo} "<fact>"` to record what you learned']
    if facts["live"] and not every:
        hints.append(f"Run `{fleet} context repo {repo} --all --full` for every fact in full")

    def render(counts):
        nfacts, nresults = counts
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
        lines.append(fleet_platform.toon_field("narrative", narrative if os.path.exists(narrative) else "none"))
        lines.append(fleet_platform.toon_field("results", f"{len(results)} for {repo}"))
        if results and nresults:
            lines.append(fleet_platform.toon_table("recent", ["task", "state", "date", "path"], results[:nresults]))
        if prior:
            lines.append(fleet_platform.toon_table("prior", ["task", "state", "path"], prior))
        if facts["malformed"]:
            lines.append(fleet_platform.toon_field(
                "skipped", f"{len(facts['malformed'])} malformed fact file(s) — {facts['malformed'][0]}"))
        more = more_line([(len(facts["live"]) - nfacts, "facts"),
                          (len(results) - min(nresults, len(results)), "results")])
        if more:
            lines.append(more)
        lines.append(help_line(hints))
        return "\n".join(lines) + "\n"

    if every:
        return render([len(facts["live"]), len(results)])
    return capped(render, [min(FACT_ROWS, len(facts["live"])), min(RESULT_ROWS, len(results))],
                  None if full else REPO_BYTES)


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
        conditions = [queue.blocker_condition(b) for b in task.blockers if queue.blocker_condition(b)]
        publish = (task.doc.get("publish") or {}).get("state")
        result = task.file("result.md")
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
    return rows


TOPIC_ORDER = ("stalled", "ready", "stuck", "failed", "held", "condition", "review", "working", "awaiting merge",
               "waiting", "landed")


def topic_row(slug: str, tasks: list, pending: dict) -> dict:
    words = {}
    for task in tasks:
        why = pending.get(task.ref, {}).get("why")
        word = why or {"dispatched": "working", "done": "awaiting merge", "queued": "waiting",
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
        "awaiting merge": f"{len(refs)} awaiting merge",
        "waiting": "waiting on another task",
        "landed": "archives on the next collect",
    }[state]
    return {"slug": slug, "state": state, "next": nxt}


def lead_view(every: bool) -> str:
    queue = _queue()
    q = queue.Queue(queue.queue_root())
    stalled = {s["task"] for s in queue.stalled_tasks(q)}
    pending = {r["task"]: r for r in pending_rows(q, stalled)}
    topics = [topic_row(slug, tasks, pending) for slug, tasks in sorted(q.by_topic().items()) if tasks]
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
            lines.append(fleet_platform.toon_table("open", ["slug", "state", "next"], topics[:n]))
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
            lines.append(fleet_platform.toon_table("tasks", ["task", "why", "next"], rows[:n]))
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
        ["repo"], {"--all": False, "--full": False, "--task": True, "--fields": True},
        "fleet context repo <repo> [--task <ref>] [--all] [--full] [--fields replaces,at]",
        "a repository's live facts, its narrative, and the results of tasks on it",
        {"--task": "add the results of the tasks <ref> waits on", "--all": "every fact and result, uncapped",
         "--full": "no fact text truncated", "--fields": "add replaces and/or at to each fact row"},
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


def parse(argv: list) -> tuple[str, dict | None]:
    """(subcommand, options), or (subcommand, None) for --help. Raises usage errors before anything runs."""
    sub = "lead"
    rest = list(argv)
    if rest and not rest[0].startswith("-"):
        sub = rest.pop(0)
        if sub not in SUBCOMMANDS:
            raise usage_error(f"no subcommand {sub!r}")
    positionals, flags, *_ = SUBCOMMANDS[sub]
    opts = {"args": [], "all": False, "full": False, "task": None, "fields": [], "replaces": None, "source": None}
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
            name, eq, value = word.partition("=")
            if name not in flags:
                raise usage_error(f"unknown flag {name}", sub)
            if flags[name]:
                if not eq:
                    if not rest:
                        raise usage_error(f"{name} needs a value", sub)
                    value = rest.pop(0)
                opts[name[2:]] = value
            else:
                opts[name[2:]] = True
            continue
        opts["args"].append(word)
    if wants_help:
        return sub, None
    if len(opts["args"]) != len(positionals):
        raise ContextError("bad_argument", f"{sub} takes {len(positionals)} argument(s), "
                           f"{', '.join(f'<{p}>' for p in positionals) or 'none'}; got {len(opts['args'])}",
                           [f"Run `{fleet_command()} context {sub} --help`"])
    if isinstance(opts["fields"], str):
        fields = [f.strip() for f in opts["fields"].split(",") if f.strip()]
        unknown = [f for f in fields if f not in EXTRA_FIELDS + tuple(FACT_FIELDS)]
        if unknown:
            raise ContextError("bad_argument", f"no field {unknown[0]!r}; --fields takes "
                               + ",".join(EXTRA_FIELDS), [f"Run `{fleet_command()} context repo --help`"])
        opts["fields"] = fields
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
