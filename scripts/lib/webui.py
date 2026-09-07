#!/usr/bin/env python3
"""The fleet monitor: a read-only web view of the task queue.

Called through scripts/webui.sh, which owns the lifecycle — this file owns the
view. Three things follow from that split and are worth stating up front:

1. IT IS A READER, AND ONLY A READER. Every route is a GET, every non-GET is a
   405, and nothing here writes into orchestration/queue. The queue's records
   are another task's contract: `queue.sh` is the only thing that edits them,
   the workers are the only things that write result.md, and a monitor that
   also dispatched would be a second writer with a second idea of the model.
   Read-only is also what keeps a bound socket a modest risk rather than a
   serious one.

2. IT ADDS NO FIELD. Every value it shows is already on disk, because the
   queue's four-files-per-task layout answers exactly the four questions a
   monitor asks — intent (task.yaml + BRIEF.md), progress (progress.jsonl),
   outcome (result.md), and where it stands (task.yaml's `state`). The topic
   classification below is DERIVED from its tasks' states each time it is
   asked; it is not stored anywhere and nothing reads it back.

3. IT REUSES queue.py. The Queue class, the state vocabulary, `is_ready`, the
   blocker rule and the overlap report all come from the module that owns
   them. A monitor with its own parallel notion of "ready" would drift from
   the queue's within a release, and the drift would look like a bug in the
   queue.

Binding: 127.0.0.1 by default and nothing wider without an explicit
FLEET_WEBUI_HOST. This serves the operator's prompts, their plans and their
workers' output; nobody should discover it is reachable.

Environment:
    FLEET_QUEUE_DIR    the queue to read (default orchestration/queue)
    FLEET_WEBUI_DIR    runtime state — port, pid, log (default orchestration/webui)
    FLEET_WEBUI_HOST   bind address (default 127.0.0.1; wider is opt-in)
    FLEET_WEBUI_PORT   first port to try (default 7413), then the next 20
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


def _load_queue():
    """Load scripts/lib/queue.py under a name that is not `queue`.

    Putting this directory on sys.path would shadow the standard library's
    `queue` for everything in the process — including whatever http.server
    imports next — so the module is loaded by path and bound to a name of our
    own instead.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.py")
    spec = importlib.util.spec_from_file_location("fleet_queue", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fleet_queue"] = module
    spec.loader.exec_module(module)
    return module


fleetqueue = _load_queue()

DEFAULT_PORT = 7413
PORT_SPAN = 20
LOOPBACK = {"127.0.0.1", "::1", "localhost", "ip6-localhost"}

# The topic classification the view groups by, most urgent first. Every one is
# derived from the states of the topic's tasks — see classify().
TOPIC_CLASSES = ("attention", "running", "ready", "blocked", "done", "empty")


def runtime_dir() -> str:
    return os.environ.get("FLEET_WEBUI_DIR") or os.path.join("orchestration", "webui")


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- the model, read ---------------------------------------------------------


def read_file(path: str, limit: int = 200_000) -> str | None:
    """A file that is not there is a real answer — None, not an empty string."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return None


def read_progress(path: str) -> list:
    """progress.jsonl, oldest first. A malformed line is skipped, not fatal:
    `watch` appends to this file while the view reads it."""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return out


def display_state(q: fleetqueue.Queue, task: fleetqueue.Task) -> str:
    """The same distinction `queue.sh list` draws: a queued task holding on a
    recorded blocker reads as `waiting`, which is not a state on disk."""
    if task.state == "queued" and not q.is_ready(task):
        return "waiting"
    return task.state


def task_view(q: fleetqueue.Queue, task: fleetqueue.Task) -> dict:
    d = task.doc
    progress = read_progress(task.file("progress.jsonl"))
    return {
        "ref": task.ref,
        "id": task.id,
        "topic": task.topic,
        "title": d.get("title") or task.id,
        "state": task.state,
        "display_state": display_state(q, task),
        "ready": q.is_ready(task),
        "repo": d.get("repo"),
        "branch": d.get("branch"),
        "base": d.get("base"),
        "agent": d.get("agent"),
        "profile": d.get("profile"),
        "session": d.get("session"),
        "prompted": d.get("prompted"),
        "touches": task.touches,
        "blocked_by": [
            {
                "task": b.get("task"),
                "kind": b.get("kind"),
                "why": b.get("why"),
                "cleared": q.blocker_cleared(b),
            }
            for b in task.blockers
        ],
        "outcome": d.get("outcome"),
        "artifact": d.get("artifact"),
        "created": d.get("created"),
        "dispatched_at": d.get("dispatched_at"),
        "concluded_at": d.get("concluded_at"),
        "transitions": len(progress),
        "last_transition": progress[-1] if progress else None,
        "has_brief": os.path.exists(task.file("BRIEF.md")),
        "has_result": os.path.exists(task.file("result.md")),
    }


def classify(tasks: list) -> str:
    """One word for a topic, derived from its tasks and stored nowhere.

    Ordered by what an operator should look at first: a worker that gave up
    outranks one that is running, which outranks work merely waiting to go.
    """
    if not tasks:
        return "empty"
    states = [t["display_state"] for t in tasks]
    if any(s in ("stuck", "failed") for s in states):
        return "attention"
    if any(s == "dispatched" for s in states):
        return "running"
    if any(t["ready"] for t in tasks):
        return "ready"
    if any(s == "waiting" for s in states):
        return "blocked"
    if all(s in ("done", "abandoned") for s in states):
        return "done"
    return "ready"


def snapshot() -> dict:
    """The whole view, rebuilt from disk on every request.

    No cache: the queue is small, the reads are local, and a cached monitor
    that shows a task as running twenty seconds after it concluded is worse
    than no monitor. Rebuilding is also what makes this safe to run beside
    `queue.sh watch` writing the same files.
    """
    root = fleetqueue.queue_root()
    topics = []
    by_state: dict[str, int] = {}

    try:
        q = fleetqueue.Queue(root)
    except fleetqueue.QueueError as exc:
        return {
            "generated": now(),
            "queue_root": os.path.abspath(root),
            "error": str(exc),
            "topics": [],
            "counts": {"topics": 0, "tasks": 0, "by_state": {}},
            "overlaps": [],
        }

    grouped = q.by_topic()
    for slug, tasks in sorted(grouped.items()):
        meta = q.topics.get(slug, {})
        views = [task_view(q, t) for t in tasks]
        for v in views:
            by_state[v["display_state"]] = by_state.get(v["display_state"], 0) + 1
        tpath = os.path.join(root, slug)
        topics.append(
            {
                "slug": slug,
                "title": meta.get("title") or slug,
                "created": meta.get("created"),
                "prompt": read_file(os.path.join(tpath, "PROMPT.md")),
                "classification": classify(views),
                "tasks": views,
            }
        )

    # Ordered by classification, so what needs an operator is at the top.
    topics.sort(key=lambda t: (TOPIC_CLASSES.index(t["classification"]), t["slug"]))

    return {
        "generated": now(),
        "queue_root": os.path.abspath(root),
        "topics": topics,
        "counts": {
            "topics": len(topics),
            "tasks": sum(len(t["tasks"]) for t in topics),
            "by_state": by_state,
        },
        # queue.py's own risk report, over the set that would go out together.
        "overlaps": q.overlaps(q.ready()),
    }


def task_detail(ref: str) -> dict:
    """The three documents behind one task, as their authors wrote them.

    The ref is resolved through Queue, which only knows tasks it loaded from
    disk, so this cannot be steered at a path outside the queue.
    """
    q = fleetqueue.Queue(fleetqueue.queue_root())
    task = q.get(ref)
    view = task_view(q, task)
    view["brief"] = read_file(task.file("BRIEF.md"))
    view["result"] = read_file(task.file("result.md"))
    view["progress"] = read_progress(task.file("progress.jsonl"))
    view["prompt"] = read_file(
        os.path.join(os.path.dirname(task.path), "PROMPT.md")
    )
    return view


# --- the page ----------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>fleet monitor</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #f6f7f9; --panel: #fff; --ink: #16181d; --dim: #6b7280;
  --line: #e3e6ea; --accent: #2f6feb;
  --running: #1f7a4d; --attention: #b3261e; --ready: #2f6feb;
  --blocked: #8a6100; --done: #6b7280; --empty: #9aa1ab;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1115; --panel: #171a20; --ink: #e6e8ec; --dim: #9aa1ab;
    --line: #262b33; --accent: #6ea0ff;
    --running: #4ade80; --attention: #ff8a80; --ready: #6ea0ff;
    --blocked: #e0b64a; --done: #8b929c; --empty: #6b7280;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
header {
  position: sticky; top: 0; z-index: 5; background: var(--panel);
  border-bottom: 1px solid var(--line); padding: 12px 20px;
  display: flex; gap: 16px; align-items: baseline; flex-wrap: wrap;
}
header h1 { font-size: 15px; margin: 0; letter-spacing: .02em; }
header .path { font-family: var(--mono); font-size: 12px; color: var(--dim); }
header .tally { margin-left: auto; font-size: 12px; color: var(--dim); }
main { padding: 20px; max-width: 1100px; margin: 0 auto; }
.topic {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  margin-bottom: 18px; overflow: hidden;
}
.topic > summary {
  cursor: pointer; padding: 14px 18px; display: flex; gap: 12px;
  align-items: center; flex-wrap: wrap; list-style: none;
}
.topic > summary::-webkit-details-marker { display: none; }
.topic > summary::before { content: "\\25B8"; color: var(--dim); }
.topic[open] > summary::before { content: "\\25BE"; }
.topic h2 { font-size: 15px; margin: 0; font-weight: 600; }
.slug { font-family: var(--mono); font-size: 12px; color: var(--dim); }
.pill {
  font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
  padding: 2px 8px; border-radius: 999px; border: 1px solid currentColor;
  font-weight: 600;
}
.c-running, .s-dispatched { color: var(--running); }
.c-attention, .s-stuck, .s-failed { color: var(--attention); }
.c-ready, .s-queued { color: var(--ready); }
.c-blocked, .s-waiting { color: var(--blocked); }
.c-done, .s-done, .s-abandoned { color: var(--done); }
.c-empty { color: var(--empty); }
.body { padding: 0 18px 18px; }
.prompt {
  border-left: 3px solid var(--accent); padding: 8px 0 8px 12px;
  margin: 0 0 14px; white-space: pre-wrap; font-family: var(--mono);
  font-size: 12.5px; color: var(--dim); max-height: 12em; overflow: auto;
}
.task { border-top: 1px solid var(--line); padding: 12px 0; }
.task-head {
  display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
  cursor: pointer;
}
.task-head .id { font-family: var(--mono); font-size: 12.5px; }
.task-head .title { font-weight: 600; }
.meta { color: var(--dim); font-size: 12px; font-family: var(--mono); }
.blocker { color: var(--blocked); font-size: 12.5px; margin-top: 6px; }
.blocker.cleared { color: var(--done); text-decoration: line-through; }
.risk {
  color: var(--blocked); font-size: 12.5px; background: var(--panel);
  border: 1px dashed currentColor; border-radius: 8px; padding: 10px 14px;
  margin-bottom: 18px;
}
.panes { margin-top: 12px; display: grid; gap: 12px; }
@media (min-width: 860px) { .panes { grid-template-columns: 1fr 1fr; } }
.pane {
  border: 1px solid var(--line); border-radius: 8px; overflow: hidden;
  min-width: 0;
}
.pane > h4 {
  margin: 0; padding: 7px 12px; font-size: 11px; font-weight: 600;
  letter-spacing: .06em; text-transform: uppercase; color: var(--dim);
  border-bottom: 1px solid var(--line);
}
.pane .doc, .pane .rows {
  margin: 0; padding: 10px 12px; max-height: 22em; overflow: auto;
  font-family: var(--mono); font-size: 12px; white-space: pre-wrap;
  word-break: break-word;
}
.pane.wide { grid-column: 1 / -1; }
.rows { white-space: normal; }
.row { display: flex; gap: 10px; padding: 2px 0; }
.row .seq { color: var(--dim); min-width: 4.5em; }
.row .arrow { color: var(--dim); }
.absent { color: var(--dim); font-style: italic; padding: 10px 12px; }
a { color: var(--accent); }
.empty-state { color: var(--dim); text-align: center; padding: 60px 20px; }
.err {
  color: var(--attention); border: 1px solid currentColor; border-radius: 8px;
  padding: 10px 14px; margin-bottom: 16px; font-family: var(--mono);
  font-size: 12.5px;
}
</style>
</head>
<body>
<header>
  <h1>fleet monitor</h1>
  <span class="path" id="root"></span>
  <span class="tally" id="tally">loading…</span>
</header>
<main id="main"></main>
<script>
const el = (t, c, txt) => {
  const n = document.createElement(t);
  if (c) n.className = c;
  if (txt !== undefined && txt !== null) n.textContent = txt;
  return n;
};

// Which topics and tasks the operator has opened. Kept across the poll so a
// refresh never collapses what someone is reading.
const openTopics = new Set();
const openTasks = new Set();
let detailCache = {};

function pill(word) { return el("span", "pill c-" + word + " s-" + word, word); }

function paneDoc(title, text, absent) {
  const p = el("div", "pane");
  p.appendChild(el("h4", null, title));
  if (text === null || text === undefined || !String(text).trim()) {
    p.appendChild(el("div", "absent", absent));
  } else {
    p.appendChild(el("pre", "doc", text));
  }
  return p;
}

function paneProgress(rows) {
  const p = el("div", "pane");
  p.appendChild(el("h4", null, "progress — progress.jsonl"));
  if (!rows || !rows.length) {
    p.appendChild(el("div", "absent",
      "no transition observed yet. `queue.sh watch` writes this file."));
    return p;
  }
  const box = el("div", "rows");
  for (const r of rows) {
    const row = el("div", "row");
    row.appendChild(el("span", "seq", "seq " + (r.seq ?? "-")));
    row.appendChild(el("span", null, (r.from || "-")));
    row.appendChild(el("span", "arrow", "\\u2192"));
    row.appendChild(el("span", null, (r.to || "-")));
    row.appendChild(el("span", "meta", r.at || r.observed || ""));
    box.appendChild(row);
  }
  p.appendChild(box);
  return p;
}

function renderDetail(host, ref) {
  host.textContent = "";
  const d = detailCache[ref];
  if (!d) { host.appendChild(el("div", "absent", "loading\\u2026")); return; }
  const panes = el("div", "panes");
  panes.appendChild(paneDoc("plan — BRIEF.md", d.brief,
    "no BRIEF.md. `queue.sh dispatch` refuses a task without one."));
  panes.appendChild(paneDoc("implementation — result.md", d.result,
    "no result.md yet. Only the worker writes this, and only it closes a task."));
  panes.appendChild(paneProgress(d.progress));
  const facts = [
    ["repo", d.repo], ["branch", d.branch + " off " + d.base],
    ["agent", d.agent], ["profile", d.profile], ["session", d.session],
    ["prompted", String(d.prompted)], ["touches", (d.touches || []).join(", ")],
    ["outcome", d.outcome], ["artifact", d.artifact],
    ["created", d.created], ["dispatched", d.dispatched_at],
    ["concluded", d.concluded_at],
  ].filter(([, v]) => v !== null && v !== undefined && v !== "");
  const p = el("div", "pane");
  p.appendChild(el("h4", null, "record — task.yaml"));
  const box = el("div", "rows");
  for (const [k, v] of facts) {
    const row = el("div", "row");
    row.appendChild(el("span", "seq", k));
    if (k === "artifact" && /^https?:/.test(v)) {
      const a = el("a", null, v); a.href = v; a.target = "_blank";
      a.rel = "noreferrer"; row.appendChild(a);
    } else {
      row.appendChild(el("span", null, String(v)));
    }
    box.appendChild(row);
  }
  p.appendChild(box);
  panes.appendChild(p);
  host.appendChild(panes);
}

async function loadDetail(ref, host) {
  try {
    const r = await fetch("api/task?ref=" + encodeURIComponent(ref));
    detailCache[ref] = await r.json();
  } catch (e) {
    detailCache[ref] = { brief: null, result: null, progress: [] };
  }
  if (openTasks.has(ref)) renderDetail(host, ref);
}

function renderTask(t) {
  const wrap = el("div", "task");
  const head = el("div", "task-head");
  head.appendChild(pill(t.display_state));
  head.appendChild(el("span", "id", t.id));
  head.appendChild(el("span", "title", t.title));
  const bits = [t.repo, t.branch];
  if (t.transitions) bits.push(t.transitions + " transition(s)");
  // Only before `collect` has read it. On a closed task the result is the
  // outcome already shown in the pill, and "waiting" would read as unread.
  if (t.has_result && (t.state === "queued" || t.state === "dispatched")) {
    bits.push("result waiting to be collected");
  }
  head.appendChild(el("span", "meta", bits.filter(Boolean).join("  \\u00b7  ")));
  wrap.appendChild(head);

  for (const b of t.blocked_by || []) {
    const line = el("div", "blocker" + (b.cleared ? " cleared" : ""),
      (b.cleared ? "cleared: " : "waits on ") + b.task +
      " \\u2014 " + b.kind + ": " + b.why);
    wrap.appendChild(line);
  }

  const detail = el("div");
  detail.hidden = !openTasks.has(t.ref);
  wrap.appendChild(detail);
  if (openTasks.has(t.ref)) {
    renderDetail(detail, t.ref);
    loadDetail(t.ref, detail);
  }
  head.addEventListener("click", () => {
    if (openTasks.has(t.ref)) {
      openTasks.delete(t.ref); detail.hidden = true;
    } else {
      openTasks.add(t.ref); detail.hidden = false;
      renderDetail(detail, t.ref); loadDetail(t.ref, detail);
    }
  });
  return wrap;
}

function renderTopic(topic) {
  const d = el("details", "topic");
  d.open = openTopics.has(topic.slug);
  d.addEventListener("toggle", () => {
    d.open ? openTopics.add(topic.slug) : openTopics.delete(topic.slug);
  });
  const s = el("summary");
  s.appendChild(pill(topic.classification));
  s.appendChild(el("h2", null, topic.title));
  s.appendChild(el("span", "slug", topic.slug));
  const states = {};
  for (const t of topic.tasks) {
    states[t.display_state] = (states[t.display_state] || 0) + 1;
  }
  const tally = Object.entries(states).map(([k, v]) => v + " " + k).join(", ");
  s.appendChild(el("span", "meta", tally || "no tasks"));
  d.appendChild(s);

  const body = el("div", "body");
  if (topic.prompt) {
    body.appendChild(el("blockquote", "prompt", topic.prompt.trim()));
  }
  for (const t of topic.tasks) body.appendChild(renderTask(t));
  d.appendChild(body);
  return d;
}

function render(data) {
  document.getElementById("root").textContent = data.queue_root;
  const c = data.counts;
  const parts = Object.entries(c.by_state).map(([k, v]) => v + " " + k);
  document.getElementById("tally").textContent =
    c.topics + " topic(s), " + c.tasks + " task(s)" +
    (parts.length ? " \\u2014 " + parts.join(", ") : "") +
    "  \\u00b7  read at " + data.generated;

  const main = document.getElementById("main");
  main.textContent = "";
  if (data.error) main.appendChild(el("div", "err", data.error));
  for (const o of data.overlaps || []) {
    main.appendChild(el("div", "risk",
      "risk: " + o.tasks.join(", ") + " all touch " + o.touches +
      " \\u2014 overlap is a risk signal, not a reason to wait."));
  }
  if (!data.topics.length) {
    main.appendChild(el("div", "empty-state",
      "The queue is empty. `scripts/queue.sh topic add` opens one."));
    return;
  }
  for (const t of data.topics) main.appendChild(renderTopic(t));
}

async function poll() {
  try {
    const r = await fetch("api/queue", { cache: "no-store" });
    render(await r.json());
  } catch (e) {
    document.getElementById("tally").textContent =
      "cannot reach the server \\u2014 " + e;
  }
}
poll();
setInterval(poll, 4000);
</script>
</body>
</html>
"""


# --- the server --------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "fleet-webui"
    sys_version = ""

    # Silence the per-request line: the log file is for failures a supervisor
    # restart would otherwise hide, not for an access log nobody reads.
    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # This page reads local files and talks only to itself; nothing it
        # renders should ever be able to fetch or frame anything else.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, doc: dict) -> None:
        self._send(code, json.dumps(doc, indent=2).encode(), "application/json")

    def _host_allowed(self) -> bool:
        """Reject a Host header naming anything but this bound socket.

        A page on any origin can point a browser at 127.0.0.1; the check that
        stops it reading this queue is that the request must arrive addressed
        to a name this server answers to. Only enforced on a loopback bind —
        an operator who opted into a wider host has their own name for it.
        """
        if self.server.bind_host not in LOOPBACK:
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in LOOPBACK or host == ""

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_allowed():
            self._send(403, b"forbidden: unexpected Host header\n", "text/plain")
            return
        path, _, query = self.path.partition("?")
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/api/queue":
            self._json(200, snapshot())
        elif path == "/api/task":
            ref = (parse_qs(query).get("ref") or [""])[0]
            try:
                self._json(200, task_detail(ref))
            except fleetqueue.QueueError as exc:
                self._json(404, {"error": str(exc)})
        elif path == "/api/health":
            self._json(200, {"ok": True, "at": now()})
        else:
            self._json(404, {"error": f"no such path: {path}"})

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    # Every other verb, in one place. This is a monitor: it displays and does
    # not control, so there is no route that could accept a write.
    def _reject(self) -> None:
        self._send(405, b"fleet monitor is read-only\n", "text/plain")

    do_POST = do_PUT = do_DELETE = do_PATCH = _reject  # noqa: N815


def bind(host: str, first: int, span: int) -> ThreadingHTTPServer:
    """The first free port at or after `first`.

    Two fleets on one machine is the ordinary case, not the exotic one: each
    clone runs its own monitor over its own queue, and the second must not
    fail to start because the first got there. The chosen port goes in a file
    the operator can read; nothing hard-codes it.
    """
    last: OSError | None = None
    for port in range(first, first + span + 1):
        try:
            srv = ThreadingHTTPServer((host, port), Handler)
        except OSError as exc:
            last = exc
            continue
        srv.bind_host = host
        srv.daemon_threads = True
        return srv
    raise SystemExit(
        f"fleet monitor: no free port in {first}..{first + span} on {host} "
        f"({last}). Set FLEET_WEBUI_PORT to somewhere else."
    )


def announce(srv: ThreadingHTTPServer, host: str) -> str:
    """Record where the chosen port can be found, then say it once.

    The port is picked at bind time, so the shell that started this cannot
    know it. These two files are the answer to "where is it?" for the skill,
    for `webui.sh status`, and for the operator.
    """
    port = srv.server_address[1]
    shown = f"[{host}]" if ":" in host else host
    url = f"http://{shown}:{port}/"
    rt = runtime_dir()
    os.makedirs(rt, exist_ok=True)
    for name, value in (("port", str(port)), ("host", host), ("url", url)):
        tmp = os.path.join(rt, name + ".tmp")
        with open(tmp, "w") as fh:
            fh.write(value + "\n")
        os.replace(tmp, os.path.join(rt, name))
    return url


def main(argv: list) -> int:
    host = os.environ.get("FLEET_WEBUI_HOST") or "127.0.0.1"
    try:
        first = int(os.environ.get("FLEET_WEBUI_PORT") or DEFAULT_PORT)
    except ValueError:
        print("fleet monitor: FLEET_WEBUI_PORT is not a number", file=sys.stderr)
        return 2

    if "--once" in argv:
        # Render the snapshot and exit: what the selftest and a scripted check
        # use, so neither has to bind a socket to know the reader works.
        print(json.dumps(snapshot(), indent=2))
        return 0

    srv = bind(host, first, PORT_SPAN)
    url = announce(srv, host)
    print(f"fleet monitor: {url} serving {os.path.abspath(fleetqueue.queue_root())}")
    if host not in LOOPBACK:
        print(
            f"fleet monitor: bound to {host}, which is NOT loopback — this "
            "queue is reachable from the network.",
            file=sys.stderr,
        )
    sys.stdout.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
