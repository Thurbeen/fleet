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

The look is the thurbox website's, token for token (website/css/variables.css),
and it is entirely local: the two files the page needs — the emblem cropped out
of media/fleet-banner.jpg and the vendored display font — are served from this
repo by the ASSETS whitelist below. Nothing is fetched at runtime, and the
Content-Security-Policy says so rather than trusting it.

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

# The repo this file ships in, found from this file rather than from the
# working directory — webui.sh may start the server from anywhere.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The only files this server hands out besides the page itself, named one by
# one. A whitelist rather than a document root: there is no path to join, so
# there is no traversal to get wrong, and the set stays small enough to read.
#
# They exist because the page must render with the network unplugged — no CDN
# and no Google Fonts, which the Content-Security-Policy below also enforces.
ASSETS = {
    "/assets/fleet-banner.jpg": ("media/fleet-banner.jpg", "image/jpeg"),
    "/assets/press-start-2p.woff2": (
        "media/fonts/press-start-2p-400.woff2",
        "font/woff2",
    ),
}

# The HUD's counters, in the order the bar shows them: a bucket of display
# states, the class that gives the bucket its one colour, and the word that
# always travels with it. Derived on every request and stored nowhere — the
# same rule the topic classification follows.
HUD_GROUPS = (
    ("ready", "ready", ("queued",)),
    ("running", "running", ("dispatched",)),
    ("waiting", "blocked", ("waiting",)),
    ("done", "done", ("done", "landed", "abandoned")),
    ("failed", "attention", ("stuck", "failed")),
)

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


def hud_counts(by_state: dict) -> list:
    """The five HUD numbers, folded out of the same by-state tally the pills
    use. Buckets, not new states: `failed` is stuck plus failed, `done` is done
    plus landed plus abandoned, and every display state lands in exactly one of
    them."""
    return [
        {
            "key": key,
            "class": cls,
            "count": sum(by_state.get(s, 0) for s in states),
            "states": list(states),
        }
        for key, cls, states in HUD_GROUPS
    ]


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
    if all(s in ("done", "landed", "abandoned") for s in states):
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
            "counts": {
                "topics": 0,
                "tasks": 0,
                "by_state": {},
                "hud": hud_counts({}),
            },
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
            "hud": hud_counts(by_state),
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
/* The thurbox website's tokens, verbatim (website/css/variables.css), because
   this is the same product's second surface and not a parallel theme. Its own
   comment is the rule this page is held to: Doom is an accent, not the whole
   room. The room is warm charcoal; red is the frame, the emphasis and the
   alarm, and nothing that has to be READ sits on a glow. */
:root {
  color-scheme: dark;

  --bg-primary: #15120f; --bg-secondary: #1e1a16; --bg-card: #1e1a16;
  --bg-code: #0d0b09;    --bg-hover: #2a241e;
  --text-primary: #e0e0e0; --text-secondary: #a0a0a0; --text-muted: #948a7d;
  --accent: #ff5c54; --red: #ff3b30; --green: #6eff6e;
  --yellow: #ffb627;  --blue: #00d9ff; --purple: #ff8c8c;
  --border: #36302a; --border-light: #26211c;

  --glow-red: 0 0 5px rgb(255, 92, 84, .7), 0 0 12px rgb(255, 59, 48, .4);
  --glow-green: 0 0 5px rgb(110, 255, 110, .7), 0 0 12px rgb(110, 255, 110, .4);
  --glow-cyan: 0 0 5px rgb(0, 217, 255, .7), 0 0 12px rgb(0, 217, 255, .4);
  --scanline: rgb(0, 0, 0, .2);
  /* Softer than the website's: that one frames a hero, this one frames a
     wall of text that has to stay readable out to the corners of a second
     monitor. Same technique, a third of the weight. */
  --vignette: radial-gradient(ellipse at 50% 35%, transparent 72%, rgb(0, 0, 0, .22));

  --hud-height: 30px; --hud-bg: #14110e; --hud-bg-deep: #0a0807; --hud-edge: #3a322b;
  --bevel-light: rgb(255, 255, 255, .14); --bevel-dark: rgb(0, 0, 0, .62);
  --bevel-frame: #07060a;
  --bevel: 0 0 0 2px var(--bevel-frame), inset 2px 2px 0 0 var(--bevel-light),
           inset -2px -2px 0 0 var(--bevel-dark);

  --space-sm: .5rem; --space-md: 1rem; --space-lg: 1.5rem;
  --border-radius: 0;

  --font-display: 'Press Start 2P', ui-monospace, monospace;
  --font-mono: ui-monospace, 'JetBrains Mono', SFMono-Regular, Menlo, Consolas, monospace;
  --font-body: Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;

  /* ONE HUE PER STATE, and the same hue wherever that state appears — pill,
     row edge, tally chip, progress segment, HUD counter. The word always
     travels with it; colour never carries the meaning on its own. */
  --st-ready: var(--blue);
  --st-running: var(--green);
  --st-blocked: var(--yellow);
  --st-attention: var(--red);
  --st-done: var(--text-muted);
  --st-empty: #5f574e;
  --hue: var(--text-secondary);
  --glow: none;
}

/* Every element that carries a state gets one of these, and takes its colour
   from --hue by inheritance. Both vocabularies land here: `c-` is a topic
   classification, `s-` is a task's display state. */
.c-ready, .s-queued        { --hue: var(--st-ready);     --glow: var(--glow-cyan); }
.c-running, .s-dispatched  { --hue: var(--st-running);   --glow: var(--glow-green); }
.c-blocked, .s-waiting     { --hue: var(--st-blocked);   --glow: none; }
.c-attention, .s-stuck, .s-failed { --hue: var(--st-attention); --glow: var(--glow-red); }
.c-done, .s-done, .s-landed, .s-abandoned { --hue: var(--st-done); --glow: none; }
.c-empty                   { --hue: var(--st-empty);     --glow: none; }

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

/* Vendored, not fetched: this page must render with the network unplugged.
   media/fonts/README.md says why it is the only face that ships. */
@font-face {
  font-family: 'Press Start 2P';
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url('assets/press-start-2p.woff2') format('woff2');
}

body {
  min-height: 100vh;
  font-family: var(--font-body); font-size: 14px; line-height: 1.55;
  color: var(--text-primary); background-color: var(--bg-primary);
  background-image: radial-gradient(ellipse at 50% -15%, rgb(255, 59, 48, .07), transparent 60%);
  background-attachment: fixed;
  -webkit-font-smoothing: antialiased;
  overflow-x: clip;
  scrollbar-color: var(--border) transparent;
}
* { scrollbar-width: thin; scrollbar-color: var(--border) transparent; }

/* CRT scanline overlay — one global, click-through pseudo-element, the same
   technique base.css uses. A 1px dark line every 3px; the 2px->3px band is
   the line. It sits over everything and can be clicked through. */
body::after {
  content: ''; position: fixed; inset: 0; z-index: 9999; pointer-events: none;
  background: repeating-linear-gradient(to bottom,
    transparent 0, transparent 2px, var(--scanline) 2px, var(--scanline) 3px);
  mix-blend-mode: multiply;
}
body::before {
  content: ''; position: fixed; inset: 0; z-index: 9998; pointer-events: none;
  background: var(--vignette);
}

/* --- the HUD: the one thing visible without scrolling -------------------- */

.hud {
  position: sticky; top: 0; z-index: 50;
  background: linear-gradient(180deg, var(--hud-bg), var(--hud-bg-deep));
  border-bottom: 2px solid var(--bevel-frame);
  box-shadow: 0 1px 0 0 rgb(232, 24, 11, .45), 0 8px 20px rgb(0, 0, 0, .55);
  display: flex; flex-wrap: wrap; align-items: center;
  gap: var(--space-sm) var(--space-md); padding: 9px var(--space-lg) 8px;
}
.hud-brand { display: flex; align-items: center; gap: 10px; min-width: 0; }
.hud-mark {
  --mark: 46px;
  width: var(--mark); height: var(--mark); flex: none;
  background-color: var(--bg-code);
  background-image: url('assets/fleet-banner.jpg');
  background-repeat: no-repeat;
  /* The banner's central emblem is a 164px square at (430,304) of 1024x572.
     Cropping in CSS keeps one image on disk instead of a second, cut copy. */
  background-size: calc(var(--mark) * 1024 / 164) auto;
  background-position: calc(var(--mark) * -430 / 164) calc(var(--mark) * -304 / 164);
  /* The banner is a dark painting; at 46px it needs lifting or it reads as
     a smudge. */
  filter: brightness(1.22) saturate(1.15) contrast(1.05);
  box-shadow: var(--bevel), 0 0 12px rgb(232, 24, 11, .45);
}
.hud-word { display: flex; flex-direction: column; gap: 4px; line-height: 1; }
.hud-word b {
  font-family: var(--font-display); font-size: 15px; font-weight: 400;
  color: var(--accent); text-shadow: var(--glow-red); letter-spacing: .02em;
}
.hud-word i {
  font-family: var(--font-display); font-size: 7px; font-style: normal;
  color: var(--text-muted); letter-spacing: .2em;
}

.counters { display: flex; flex-wrap: wrap; gap: 5px; margin-left: auto; }
.counter {
  min-width: 5.4rem; min-height: var(--hud-height);
  padding: 6px 9px; background: var(--hud-bg); box-shadow: var(--bevel);
  border-top: 2px solid var(--hue);
  display: flex; flex-direction: column; justify-content: center; gap: 4px;
}
.counter b {
  font-family: var(--font-display); font-size: 15px; font-weight: 400;
  line-height: 1; color: var(--hue); text-shadow: var(--glow);
}
.counter span {
  font-family: var(--font-display); font-size: 6.5px; letter-spacing: .12em;
  color: var(--text-muted); text-transform: uppercase;
}
/* A counter at zero stops shouting, so the ones that are not at zero read as
   the signal. The label stays, so an empty bucket is still legible. */
.counter.zero { border-top-color: var(--border); }
.counter.zero b { color: var(--text-muted); text-shadow: none; opacity: .7; }
.vrule { width: 1px; align-self: stretch; background: var(--hud-edge); margin: 2px 5px; }

.hud-meta {
  width: 100%; display: flex; flex-wrap: wrap; align-items: baseline;
  gap: 4px var(--space-md);
  font-family: var(--font-mono); font-size: 11px; color: var(--text-muted);
}
.hud-meta .path { color: var(--text-secondary); overflow-wrap: anywhere; min-width: 0; }
.link { display: inline-flex; align-items: center; gap: 6px; margin-left: auto; }
.link .led {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--st-running); box-shadow: var(--glow-green);
  animation: blip 2.4s ease-in-out infinite;
}
.link.bad { color: var(--red); }
.link.bad .led { background: var(--red); box-shadow: var(--glow-red); }
@keyframes blip { 0%, 100% { opacity: 1; } 50% { opacity: .3; } }
@media (prefers-reduced-motion: reduce) { .link .led { animation: none; } }

/* --- the list ------------------------------------------------------------ */

main { max-width: 1440px; margin: 0 auto; padding: var(--space-md) var(--space-lg) 3rem; }
.topics { display: flex; flex-direction: column; gap: 5px; }

.topic {
  background: var(--bg-card);
  border: 1px solid var(--border-light); border-left: 4px solid var(--hue);
  box-shadow: 0 1px 0 0 rgb(0, 0, 0, .5);
}
.topic > summary {
  cursor: pointer; list-style: none;
  display: grid; align-items: center;
  grid-template-columns: 1.1rem 7.6rem minmax(0, 1fr) minmax(0, 30rem);
  grid-template-areas: "car pil tit rdo";
  gap: 4px var(--space-md); padding: 9px 12px;
}
.topic > summary:hover { background: var(--bg-hover); }
.topic > summary::-webkit-details-marker { display: none; }
.topic > summary::before {
  grid-area: car; content: '\\25B8'; color: var(--hue); font-size: 11px;
}
.topic[open] > summary::before { content: '\\25BE'; }
.topic > summary > .pill { grid-area: pil; }
.topic-title { grid-area: tit; min-width: 0; }
.topic-title h2 {
  font-family: var(--font-mono); font-size: 13.5px; font-weight: 700;
  color: var(--text-primary); line-height: 1.35; overflow-wrap: anywhere;
}
.topic-title .slug { font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); }
.readout {
  grid-area: rdo; display: flex; flex-wrap: wrap; align-items: center;
  justify-content: flex-end; gap: 6px var(--space-md); min-width: 0;
}

.pill {
  display: inline-block; justify-self: start; white-space: nowrap;
  font-family: var(--font-display); font-size: 7.5px; line-height: 1;
  letter-spacing: .06em; text-transform: uppercase;
  padding: 6px 6px 5px; color: var(--hue);
  border: 1px solid currentColor;
  background: var(--bg-code);
  background: color-mix(in srgb, currentColor 12%, var(--bg-code));
  text-shadow: var(--glow);
}

.chips { display: flex; flex-wrap: wrap; gap: 4px; min-width: 0; }
.chip {
  font-family: var(--font-mono); font-size: 10.5px; line-height: 1.5;
  padding: 1px 6px; white-space: nowrap; color: var(--hue);
  border: 1px solid currentColor;
  background: color-mix(in srgb, currentColor 10%, transparent);
}

.prog { display: flex; align-items: center; gap: 8px; min-width: 9rem; flex: 1 1 9rem; }
.bar {
  flex: 1 1 auto; min-width: 0; height: 12px; display: flex;
  background: var(--bg-code); box-shadow: var(--bevel);
}
.bar > i { display: block; height: 100%; background: var(--hue); }
/* Concluded work is solid; work still in flight is drawn back to a tint, so a
   topic with three running tasks does not read as a full bar. */
.bar > i.s-dispatched, .bar > i.s-queued, .bar > i.s-waiting { opacity: .3; }
.bar > i.s-abandoned { opacity: .55; }
.frac { font-family: var(--font-mono); font-size: 11px; color: var(--text-secondary); white-space: nowrap; }

/* --- inside a topic ------------------------------------------------------ */

.body { padding: 0 12px 10px; }
.prompt {
  border-left: 3px solid var(--accent); background: var(--bg-code);
  padding: 8px 12px; margin: 2px 0 8px; white-space: pre-wrap;
  font-family: var(--font-mono); font-size: 11.5px; color: var(--text-secondary);
  max-height: 11em; overflow: auto;
}
.task { border-top: 1px solid var(--border-light); }
.task-head {
  cursor: pointer; display: grid; align-items: center;
  grid-template-columns: 1.1rem 7.6rem minmax(0, 1fr) minmax(0, 30rem);
  grid-template-areas: "car pil tit rdo";
  gap: 4px var(--space-md); padding: 8px 0;
}
.task-head:hover { background: var(--bg-hover); }
.task-head::before { grid-area: car; content: '\\25B8'; color: var(--hue); font-size: 11px; }
.task.open .task-head::before { content: '\\25BE'; }
.task-head > .pill { grid-area: pil; }
.task-title { grid-area: tit; min-width: 0; }
.task-title .id { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); }
.task-title .name { font-family: var(--font-mono); font-size: 13px; font-weight: 700; overflow-wrap: anywhere; }
.task-meta {
  grid-area: rdo; justify-self: end; text-align: right; min-width: 0;
  font-family: var(--font-mono); font-size: 11px; color: var(--text-muted);
  overflow-wrap: anywhere;
}
.blocker {
  font-family: var(--font-mono); font-size: 11.5px; color: var(--yellow);
  padding: 0 0 6px 2.1rem;
}
.blocker.cleared { color: var(--text-muted); text-decoration: line-through; }

/* --- the four panes: intent, plan, progress, outcome --------------------- */

/* Columns, not a grid: the four panes are wildly different heights — a long
   brief beside a one-line "no result.md yet" — and a grid row would stretch
   the short one to match. Columns pack them, so an unfinished task does not
   render as a screen of empty boxes. */
.panes { columns: 2; column-gap: 8px; padding: 4px 0 8px 2.1rem; }
@media (max-width: 979px) { .panes { columns: 1; } }
.pane {
  background: var(--bg-secondary); box-shadow: var(--bevel); min-width: 0;
  break-inside: avoid; margin-bottom: 8px;
}
.pane > h4 {
  display: flex; align-items: center; gap: 8px;
  font-family: var(--font-display); font-size: 7px; font-weight: 400;
  letter-spacing: .12em; text-transform: uppercase; color: var(--accent);
  padding: 9px 10px; background: var(--hud-bg-deep);
  border-bottom: 1px solid var(--hud-edge);
}
.pane > h4 em {
  font-style: normal; font-family: var(--font-mono); font-size: 10.5px;
  letter-spacing: 0; color: var(--text-muted); margin-left: auto;
}
.doc, .rows {
  padding: 9px 10px; max-height: 22em; overflow: auto;
  font-family: var(--font-mono); font-size: 11.5px;
}
.doc { white-space: pre-wrap; word-break: break-word; color: var(--text-secondary); background: var(--bg-code); }
.row { display: flex; flex-wrap: wrap; gap: 8px; padding: 2px 0; }
.row .key { color: var(--text-muted); min-width: 6em; }
.row .arrow { color: var(--accent); }
.row .at { color: var(--text-muted); margin-left: auto; }
.absent { padding: 10px; color: var(--text-muted); font-style: italic; font-size: 12px; }
a { color: var(--green); text-decoration: none; }
a:hover { text-shadow: var(--glow-green); }

/* --- notices ------------------------------------------------------------- */

.risk, .err, .empty-state {
  font-family: var(--font-mono); font-size: 11.5px;
  padding: 9px 12px; margin-bottom: 6px;
  display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline;
}
.risk { color: var(--yellow); background: var(--bg-card); border: 1px dashed currentColor; }
.err { color: var(--red); background: var(--bg-card); border: 1px solid currentColor; }
.risk b, .err b {
  font-family: var(--font-display); font-size: 7px; font-weight: 400;
  letter-spacing: .12em; text-transform: uppercase;
}
.empty-state {
  display: block; text-align: center; color: var(--text-muted);
  padding: 4rem 1rem; border: 1px dashed var(--border);
}

/* --- narrow: the readout drops under the title, nothing scrolls sideways - */

@media (max-width: 1080px) {
  .topic > summary, .task-head {
    grid-template-columns: 1.1rem 7.6rem minmax(0, 1fr);
    grid-template-areas: "car pil tit" "car rdo rdo";
  }
  .readout, .task-meta { justify-content: flex-start; justify-self: start; text-align: left; }
}
@media (max-width: 620px) {
  .hud { padding: 9px var(--space-md) 8px; }
  .counters { margin-left: 0; width: 100%; }
  .counter { flex: 1 1 4.4rem; min-width: 0; }
  main { padding: var(--space-sm) var(--space-sm) 3rem; }
  .panes, .blocker { padding-left: 0; }
  .topic > summary, .task-head {
    grid-template-columns: 1.1rem minmax(0, 1fr);
    grid-template-areas: "car pil" "car tit" "car rdo";
  }
}
</style>
</head>
<body>
<header class="hud">
  <div class="hud-brand">
    <span class="hud-mark" aria-hidden="true"></span>
    <span class="hud-word"><b>FLEET</b><i>MONITOR</i></span>
  </div>
  <div class="counters" id="counters"></div>
  <div class="hud-meta">
    <span class="path" id="root"></span>
    <span id="stamp"></span>
    <span class="link" id="link"><span class="led"></span><span id="linktext">linking\\u2026</span></span>
  </div>
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

// The bar reads left to right as work leaving the queue: what is finished,
// then what is moving, then what has not started, then what needs someone.
const BAR_ORDER = ["landed", "done", "abandoned", "dispatched", "queued", "waiting", "stuck", "failed"];

function pill(word) { return el("span", "pill c-" + word + " s-" + word, word); }

function renderCounters(counts) {
  const host = document.getElementById("counters");
  host.textContent = "";
  for (const c of counts.hud || []) {
    const cell = el("div", "counter c-" + c["class"] + (c.count ? "" : " zero"));
    cell.title = c.count + " " + c.key + " \\u2014 " + c.states.join(", ");
    cell.appendChild(el("b", null, String(c.count)));
    cell.appendChild(el("span", null, c.key));
    host.appendChild(cell);
  }
  host.appendChild(el("div", "vrule"));
  const topics = el("div", "counter" + (counts.topics ? "" : " zero"));
  topics.appendChild(el("b", null, String(counts.topics)));
  topics.appendChild(el("span", null, "topics"));
  host.appendChild(topics);
}

function tally(tasks) {
  const states = {};
  for (const t of tasks) states[t.display_state] = (states[t.display_state] || 0) + 1;
  return states;
}

function chips(states) {
  const box = el("div", "chips");
  for (const s of BAR_ORDER) {
    if (!states[s]) continue;
    box.appendChild(el("span", "chip s-" + s, states[s] + " " + s));
  }
  if (!box.childNodes.length) box.appendChild(el("span", "chip", "no tasks"));
  return box;
}

function progress(states, total) {
  const wrap = el("div", "prog");
  const bar = el("div", "bar");
  for (const s of BAR_ORDER) {
    if (!states[s]) continue;
    const seg = el("i", "s-" + s);
    seg.style.width = (100 * states[s] / total) + "%";
    seg.title = states[s] + " " + s;
    bar.appendChild(seg);
  }
  wrap.appendChild(bar);
  const closed = (states.done || 0) + (states.landed || 0) + (states.abandoned || 0);
  const frac = el("span", "frac", closed + "/" + total + " done");
  frac.title = closed + " of " + total + " task(s) concluded as done, landed or abandoned";
  wrap.appendChild(frac);
  return wrap;
}

function paneDoc(title, file, text, absent) {
  const p = el("div", "pane");
  const h = el("h4", null, title);
  h.appendChild(el("em", null, file));
  p.appendChild(h);
  if (text === null || text === undefined || !String(text).trim()) {
    p.appendChild(el("div", "absent", absent));
  } else {
    p.appendChild(el("pre", "doc", text));
  }
  return p;
}

function paneProgress(rows) {
  const p = el("div", "pane");
  const h = el("h4", null, "progress");
  h.appendChild(el("em", null, "progress.jsonl"));
  p.appendChild(h);
  if (!rows || !rows.length) {
    p.appendChild(el("div", "absent",
      "no transition observed yet. `queue.sh watch` writes this file."));
    return p;
  }
  const box = el("div", "rows");
  for (const r of rows) {
    const row = el("div", "row");
    row.appendChild(el("span", "key", "seq " + (r.seq ?? "-")));
    row.appendChild(el("span", null, (r.from || "-")));
    row.appendChild(el("span", "arrow", "\\u2192"));
    row.appendChild(el("span", null, (r.to || "-")));
    row.appendChild(el("span", "at", r.at || r.observed || ""));
    box.appendChild(row);
  }
  p.appendChild(box);
  return p;
}

function paneRecord(d) {
  const facts = [
    ["repo", d.repo], ["branch", d.branch + " off " + d.base],
    ["agent", d.agent], ["profile", d.profile], ["session", d.session],
    ["prompted", String(d.prompted)], ["touches", (d.touches || []).join(", ")],
    ["outcome", d.outcome], ["artifact", d.artifact],
    ["created", d.created], ["dispatched", d.dispatched_at],
    ["concluded", d.concluded_at],
  ].filter(([, v]) => v !== null && v !== undefined && v !== "");
  const p = el("div", "pane");
  const h = el("h4", null, "record");
  h.appendChild(el("em", null, "task.yaml"));
  p.appendChild(h);
  const box = el("div", "rows");
  for (const [k, v] of facts) {
    const row = el("div", "row");
    row.appendChild(el("span", "key", k));
    if (k === "artifact" && /^https?:/.test(v)) {
      const a = el("a", null, v); a.href = v; a.target = "_blank";
      a.rel = "noreferrer"; row.appendChild(a);
    } else {
      row.appendChild(el("span", null, String(v)));
    }
    box.appendChild(row);
  }
  p.appendChild(box);
  return p;
}

// Four panes, because the queue keeps four files and each answers a different
// question — orchestration/queue/README.md owns that table. Not three, not five.
function renderDetail(host, ref) {
  host.textContent = "";
  const d = detailCache[ref];
  if (!d) { host.appendChild(el("div", "absent", "loading\\u2026")); return; }
  const panes = el("div", "panes");
  panes.appendChild(paneDoc("plan", "BRIEF.md", d.brief,
    "no BRIEF.md. `queue.sh dispatch` refuses a task without one."));
  panes.appendChild(paneDoc("outcome", "result.md", d.result,
    "no result.md yet. Only the worker writes this, and only it closes a task."));
  panes.appendChild(paneProgress(d.progress));
  panes.appendChild(paneRecord(d));
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
  const open = openTasks.has(t.ref);
  const wrap = el("div", "task s-" + t.display_state + (open ? " open" : ""));
  const head = el("div", "task-head");
  head.appendChild(pill(t.display_state));
  const title = el("div", "task-title");
  title.appendChild(el("div", "id", t.id));
  title.appendChild(el("div", "name", t.title));
  head.appendChild(title);
  const bits = [t.repo, t.branch];
  if (t.transitions) bits.push(t.transitions + " transition(s)");
  // Only before `collect` has read it. On a closed task the result is the
  // outcome already shown in the pill, and "waiting" would read as unread.
  if (t.has_result && (t.state === "queued" || t.state === "dispatched")) {
    bits.push("result waiting to be collected");
  }
  head.appendChild(el("div", "task-meta", bits.filter(Boolean).join("  \\u00b7  ")));
  wrap.appendChild(head);

  for (const b of t.blocked_by || []) {
    wrap.appendChild(el("div", "blocker" + (b.cleared ? " cleared" : ""),
      (b.cleared ? "cleared: " : "waits on ") + b.task +
      " \\u2014 " + b.kind + ": " + b.why));
  }

  const detail = el("div");
  detail.hidden = !open;
  wrap.appendChild(detail);
  if (open) {
    renderDetail(detail, t.ref);
    loadDetail(t.ref, detail);
  }
  head.addEventListener("click", () => {
    if (openTasks.has(t.ref)) {
      openTasks.delete(t.ref); detail.hidden = true; wrap.classList.remove("open");
    } else {
      openTasks.add(t.ref); detail.hidden = false; wrap.classList.add("open");
      renderDetail(detail, t.ref); loadDetail(t.ref, detail);
    }
  });
  return wrap;
}

function renderTopic(topic) {
  const d = el("details", "topic c-" + topic.classification);
  d.open = openTopics.has(topic.slug);
  d.addEventListener("toggle", () => {
    d.open ? openTopics.add(topic.slug) : openTopics.delete(topic.slug);
  });

  const s = el("summary");
  s.appendChild(pill(topic.classification));
  const title = el("div", "topic-title");
  title.appendChild(el("h2", null, topic.title));
  title.appendChild(el("div", "slug", topic.slug));
  s.appendChild(title);

  const states = tally(topic.tasks);
  const readout = el("div", "readout");
  readout.appendChild(chips(states));
  if (topic.tasks.length) readout.appendChild(progress(states, topic.tasks.length));
  s.appendChild(readout);
  d.appendChild(s);

  const body = el("div", "body");
  if (topic.prompt) body.appendChild(el("blockquote", "prompt", topic.prompt.trim()));
  for (const t of topic.tasks) body.appendChild(renderTask(t));
  d.appendChild(body);
  return d;
}

function render(data) {
  document.getElementById("root").textContent = data.queue_root;
  renderCounters(data.counts);
  document.getElementById("stamp").textContent =
    data.counts.tasks + " task(s) \\u00b7 read at " + data.generated;

  const main = document.getElementById("main");
  main.textContent = "";
  if (data.error) {
    const e = el("div", "err");
    e.appendChild(el("b", null, "queue error"));
    e.appendChild(el("span", null, data.error));
    main.appendChild(e);
  }
  for (const o of data.overlaps || []) {
    const r = el("div", "risk");
    r.appendChild(el("b", null, "risk"));
    r.appendChild(el("span", null,
      o.tasks.join(", ") + " all touch " + o.touches +
      " \\u2014 overlap is a risk signal, not a reason to wait."));
    main.appendChild(r);
  }
  if (!data.topics.length) {
    main.appendChild(el("div", "empty-state",
      "The queue is empty. `scripts/queue.sh topic add` opens one."));
    return;
  }
  const list = el("div", "topics");
  for (const t of data.topics) list.appendChild(renderTopic(t));
  main.appendChild(list);
}

function link(ok, text) {
  document.getElementById("link").className = ok ? "link" : "link bad";
  document.getElementById("linktext").textContent = text;
}

async function poll() {
  try {
    const r = await fetch("api/queue", { cache: "no-store" });
    render(await r.json());
    link(true, "link ok");
  } catch (e) {
    link(false, "no link \\u2014 " + e);
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

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        # This page reads local files and talks only to itself; nothing it
        # renders should ever be able to fetch or frame anything else. The
        # img-src and font-src entries are 'self' and nothing more, which is
        # the rule that keeps the theme offline: a CDN font or a remote image
        # would be blocked here rather than quietly working on this machine
        # and failing on a laptop with no network.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; img-src 'self'; font-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'",
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
        elif path in ASSETS:
            self._asset(*ASSETS[path])
        elif path == "/api/health":
            self._json(200, {"ok": True, "at": now()})
        else:
            self._json(404, {"error": f"no such path: {path}"})

    def _asset(self, relpath: str, ctype: str) -> None:
        """One of the named files in ASSETS, read from the repo this file is in.

        Cached hard, because these two never change under a running monitor and
        the page asks for them on every reload. A missing one is a 404 and not
        an error: the theme degrades to a system font and an empty tile, which
        is the right outcome for a checkout without media/.
        """
        try:
            with open(os.path.join(REPO_ROOT, relpath), "rb") as fh:
                body = fh.read()
        except OSError:
            self._json(404, {"error": f"asset not found: {relpath}"})
            return
        self._send(200, body, ctype, cache="max-age=86400")

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


def main() -> int:
    host = os.environ.get("FLEET_WEBUI_HOST") or "127.0.0.1"
    try:
        first = int(os.environ.get("FLEET_WEBUI_PORT") or DEFAULT_PORT)
    except ValueError:
        print("fleet monitor: FLEET_WEBUI_PORT is not a number", file=sys.stderr)
        return 2

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
    sys.exit(main())
