#!/usr/bin/env python3
"""Fleet's OpenTelemetry: one trace per task and the factory's measures, rebuilt
from the queue's records, and the reconciler's notice and failure lines as log
records — sent to any OTLP/HTTP endpoint when the operator turns it on.

    uv run fleet telemetry export [--dry-run]   one pass: the spans that closed since the
                                                last, every measure, and the spooled logs
    uv run fleet telemetry trace-id <topic/task> the trace id a task's spans carry

OFF UNLESS ASKED. Nothing is sent until the operator's gitignored
`orchestration/telemetry.conf` says `EXPORT=on`; the tracked
`telemetry.example.conf` beside it sets nothing and documents every key. The
standard OTEL_* variables fill in what the file does not say — endpoint,
headers, timeout, service name, resource attributes — and OTEL_SDK_DISABLED=true
turns it off whatever the file says. The file wins where both speak: it is
written for fleet, and an OTEL_* variable in the environment may have been set
for some other program — OTEL_SERVICE_NAME included: the file's SERVICE_NAME
names the service when it says one.

A TRACE IS REBUILT, NOT RECORDED LIVE. Fleet already writes every boundary a
task crosses — `created`, `dispatched_at`, each session transition `watch`
folds into progress.jsonl, `concluded_at`, the change request's publish
states, each review round, `landing` — so each stage becomes a span from those
stamps, and nothing has to carry a W3C context through an agent, which no agent
does. What that costs: a span appears only once its stage CLOSES, so a trace
fills in as the task goes and is complete when it lands. Live status stays with
thurbox's own `thurbox.sessions` metrics.

    factory task          created → landed / abandoned        cicd.pipeline.*
    ├ queued              created → dispatched
    ├ work                dispatched → result written          events: lead send, refuel
    ├ working / at rest / blocked   each session stretch       from `watch`'s transitions
    ├ paused / needs-input          a worker's declared wait
    ├ change request      first seen → merged / closed         vcs.change.*, events: publish states
    │ └ review            one per recorded round               factory.review.score / .round
    ├ landed              merged → the landing recorded
    └ abandoned           the moment it was given up

The root links to its TOPIC's own trace, whose single `factory topic` span is
sent once the topic is archived.

THE MEASURES are metrics in the same pass, recomputed from the records every
time rather than counted here, so a re-send is the same number and never a
double one:

    factory.queue.tasks           gauge, by factory.state: the live queue now
    factory.tasks.ready           gauge: what `plan` would dispatch now
    factory.tasks.stalled         gauge: `plan`'s stalled set
    factory.tasks.needs_input     gauge: workers that declared they need a decision
    factory.tasks.concluded       cumulative sum, by state and repo: landed, abandoned
    factory.task.lead_time        histogram, s: added → landed
    factory.task.dispatch_to_change_request   histogram, s: dispatched → first seen
    factory.task.review_rounds    histogram: rounds up to the first clean review
    factory.task.rework           histogram: lead sends, shepherd fixer conditions,
                                  failing-check spells and unclean review rounds

The histograms cover every task that landed, by factory.repo, cumulative from
the first export (`metrics_start` in the runtime state); rework per landed task
is their sum over their count. Lead time and landings per repo are what DORA's
lead time and deployment frequency are computed from where a merge is the
deployment.

THE RECONCILER'S LINES are log records. Each notice decision (`notice <kind> →
<who> <road>: <why>`) and each child failing, or running again after failing,
is one record carrying event.name (`fleet.reconcile.notice`,
`.child_failure`, `.child_recovered`), fleet.notice.kind/recipient/road or
fleet.reconcile.child, and factory.task.id and factory.topic when the line
names a task. The loop only APPENDS them to `logs.jsonl` here — never a
network call from the loop — and only while telemetry is on; the export ships
the spool, and what a collector could not take waits in `logs.sending.jsonl`
for the next one. The pending files together are capped at 4 MiB; a full or
busy spool drops new records until it drains.

IDS ARE DERIVED, SO RE-EXPORTING IS HARMLESS. A task's trace id is the first
16 bytes of sha256("fleet.task\\0" + "<topic>/<task>") — any tool can compute it
from the task id, and `trace-id` prints it — and a span id is the first 8 bytes
of sha256 over the task, NUL, and the stage's own key. The same span sent twice
is the same span. On top of that, `exported.json` in the runtime directory
remembers a fingerprint per span sent, so a pass sends only spans that are new
or changed. A topic trace's ids use "fleet.topic\\0" + slug the same way.

ATTRIBUTES. The published OTel semantic conventions where one exists —
`cicd.pipeline.name/run.id/result` and `cicd.pipeline.task.name` on the stages,
`vcs.change.id/state`, `vcs.repository.url.full`, `vcs.ref.head.name/revision`
and `vcs.ref.base.name` on the change request — and `factory.*` for the rest:
factory.task.id, factory.topic, factory.stage, factory.repo, factory.agent,
factory.outcome, factory.review.score/.findings/.round. Every span carries
factory.task.id and factory.topic, so a backend can find a task by either.

A DEAD COLLECTOR COSTS ONE LINE. Every request is held to the OTLP timeout
(OTEL_EXPORTER_OTLP_TIMEOUT, 10s by default), the first failure ends the pass,
and what was not sent is simply sent by a later pass. The failure is printed
once — the same failure again is not news, whichever signal's request met it —
and its recovery once, so the
reconciler's log carries one line per change and never one per pass. A
collector that REJECTS the payload (a 400) would reject the same bytes again, so
those spans are counted as sent and the rejection is printed. Only that and a
2xx settle a request: anything else — a bad token, a wrong path, a redirect —
is the setting's fault and not the spans', so they wait, and the refusal is
printed once, like a collector that is down.

WHY NO SDK. The official Python SDK is built to record spans live, in-process,
with its own random ids and a background batch thread; this module builds
finished spans with explicit timestamps and derived ids, and sends them inside
a bounded child the reconciler runs. Its OTLP/HTTP exporter would also have been
fleet's first dependency chain beyond PyYAML: protobuf, googleapis-common-protos
and requests among ~10 packages. OTLP/JSON over HTTP is a specified encoding
every OTLP/HTTP receiver accepts, and it is a dict and `urllib`. So the
exporter is the standard library, and `http/protobuf` in the settings is read
as "the same endpoint, JSON body"; `grpc` is a different port and is refused.

IT READS THE QUEUE AND WRITES ONLY ITS OWN STATE. No record is written: the
runtime directory, `orchestration/telemetry/` (FLEET_TELEMETRY_DIR moves it),
holds `exported.json` and the log spool. The reconciler runs `export` as a
bounded child on its own clock (`TELEMETRY_SECS`) and only while the setting is
on, so a fleet that never turned it on runs nothing new.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timezone


def _load_sibling(name: str, filename: str):
    """A scripts/lib module under a `fleet_` key, never shadowing the standard library."""
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


fleet_platform = _load_sibling("fleet_platform", "fleet_platform.py")
agent_settings = _load_sibling("fleet_agent_settings", "agent_settings.py")

CHECKOUT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TELEMETRY_CONF = "orchestration/telemetry.conf"
TELEMETRY_CONF_DEFAULTS = "orchestration/telemetry.example.conf"
STATE_FILE = "exported.json"

# thurbox's own default endpoint too, so one local collector takes both.
DEFAULT_ENDPOINT = "http://localhost:4318"
# The OTLP exporter spec's default.
DEFAULT_TIMEOUT_MS = 10_000
# Spans per request. The first export after turning this on sends the whole
# history; a collector's default request limit is a few MiB, and a span here
# is well under 1 KiB.
CHUNK = 500
SCOPE = {"name": "fleet.telemetry"}

ON = ("on", "true", "yes", "1")

# What one export sends, each to its own OTLP/HTTP path.
SIGNALS = ("traces", "metrics", "logs")

# Attribute keys and a unit shared by multiple telemetry records.
REPO = "factory.repo"
STATE = "factory.state"
TOPIC = "factory.topic"
EVENT = "event.name"
TASKS = "{task}"

# watch's session states, as the three stretches a trace shows. Anything else —
# `unreported`, `stopped`, a deletion — ends the stretch before it.
STRETCHES = {"working": "working", "idle": "at rest", "done": "at rest", "blocked": "blocked"}


# --- settings ------------------------------------------------------------------


def signal_settings(conf: dict, signal: str) -> tuple[str, dict, float]:
    """(url, headers, timeout in seconds) for one signal. A per-signal variable
    is that signal's alone: a _TRACES_ token is the traces backend's, and never
    goes to the metrics endpoint."""
    env = os.environ.get
    own = f"OTEL_EXPORTER_OTLP_{signal.upper()}_"
    if conf.get("ENDPOINT"):
        url = conf["ENDPOINT"].rstrip("/") + f"/v1/{signal}"
    else:
        url = env(own + "ENDPOINT") or (env("OTEL_EXPORTER_OTLP_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/") + f"/v1/{signal}"
    headers = parse_pairs(conf.get("HEADERS") or env(own + "HEADERS") or env("OTEL_EXPORTER_OTLP_HEADERS") or "")
    try:
        ms = int(conf.get("TIMEOUT_MS") or env(own + "TIMEOUT") or env("OTEL_EXPORTER_OTLP_TIMEOUT") or DEFAULT_TIMEOUT_MS)
    except ValueError:
        ms = DEFAULT_TIMEOUT_MS
    return url, headers, max(ms, 1) / 1000


class Settings:
    """What one export runs under: whether, where, and what it says about itself."""

    def __init__(self):
        root = os.environ.get("FLEET_TELEMETRY_ROOT") or CHECKOUT
        path = os.path.join(root, TELEMETRY_CONF)
        conf = agent_settings.read_conf(path if os.path.exists(path) else os.path.join(root, TELEMETRY_CONF_DEFAULTS))
        env = os.environ.get

        self.enabled = conf.get("EXPORT", "").lower() in ON and env("OTEL_SDK_DISABLED", "").lower() != "true"
        self.urls, self.headers, self.timeouts = {}, {}, {}
        for signal in SIGNALS:
            self.urls[signal], self.headers[signal], self.timeouts[signal] = signal_settings(conf, signal)
        self.url = self.urls["traces"]
        self.protocol = (conf.get("PROTOCOL") or env("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL")
                         or env("OTEL_EXPORTER_OTLP_PROTOCOL") or "http/json")
        self.resource = parse_pairs(env("OTEL_RESOURCE_ATTRIBUTES") or "")
        self.resource["service.name"] = conf.get("SERVICE_NAME") or env("OTEL_SERVICE_NAME") or "fleet"

    @property
    def problem(self) -> str:
        if self.protocol not in ("http/json", "http/protobuf"):
            return (f"fleet sends OTLP over HTTP, and protocol {self.protocol!r} is not that — "
                    "point ENDPOINT at the collector's HTTP port (4318)")
        return ""


def enabled() -> bool:
    """Whether an export would send anything: what the reconciler asks before it runs one."""
    return Settings().enabled


def parse_pairs(text: str) -> dict:
    """`k=v,k2=v2`, values percent-decoded, as OTEL_EXPORTER_OTLP_HEADERS and
    OTEL_RESOURCE_ATTRIBUTES both spell it."""
    out = {}
    for pair in text.split(","):
        key, sep, value = pair.partition("=")
        if sep and key.strip():
            out[key.strip()] = urllib.parse.unquote(value.strip())
    return out


def state_dir() -> str:
    return os.path.join(CHECKOUT, os.environ.get("FLEET_TELEMETRY_DIR") or os.path.join("orchestration", "telemetry"))


# --- ids -----------------------------------------------------------------------


def trace_id(ref: str, kind: str = "task") -> str:
    return hashlib.sha256(f"fleet.{kind}\0{ref}".encode()).hexdigest()[:32]


def span_id(ref: str, key: str, kind: str = "task") -> str:
    return hashlib.sha256(f"fleet.{kind}\0{ref}\0{key}".encode()).hexdigest()[:16]


# --- the spans one task's records hold ------------------------------------------


def to_ns(q, stamp) -> int | None:
    """A record's timestamp — ISO text or thurbox's epoch milliseconds — in nanoseconds."""
    if stamp is None or stamp == "":
        return None
    then = q.stamp_datetime(stamp)
    if then is None:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return int(then.timestamp() * 1_000_000) * 1000


def progress_lines(task) -> list[dict]:
    try:
        with open(task.file("progress.jsonl"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict):
            out.append(doc)
    return out


def change_request(url: str) -> dict:
    """What a change request URL says by itself: its repository and number."""
    m = re.match(r"^(https?://([^/]+)/(.+?))/(?:pull|-/merge_requests)/(\d+)", url)
    if not m:
        return {}
    return {"repo": f"{m.group(2)}/{m.group(3)}", "repo_url": m.group(1), "number": m.group(4)}


class Builder:
    """One trace's spans, in fleet's terms: a key, a name, two stamps, attributes."""

    def __init__(self, ref: str, common: dict, kind: str = "task"):
        self.ref, self.common, self.kind = ref, common, kind
        self.spans: list[dict] = []

    def add(self, key: str, name: str, start, end, parent: str | None = "root", attrs: dict | None = None,
            events: list | None = None, links: list | None = None, stage: bool = True) -> None:
        """A span, or nothing while either end is unknown: a stage that has not closed is not a span yet."""
        if start is None or end is None:
            return
        attributes = {**self.common, **({"factory.stage": name, "cicd.pipeline.task.name": name} if stage else {}),
                      **(attrs or {})}
        span = {
            "traceId": trace_id(self.ref, self.kind),
            "spanId": span_id(self.ref, key, self.kind),
            "name": name,
            "kind": 1,
            "startTimeUnixNano": str(start),
            "endTimeUnixNano": str(max(start, end)),
            "attributes": otlp_attrs(attributes),
        }
        if parent:
            span["parentSpanId"] = span_id(self.ref, parent, self.kind)
        if events:
            span["events"] = [{"timeUnixNano": str(at), "name": ename, "attributes": otlp_attrs(eattrs)}
                              for at, ename, eattrs in sorted(events, key=lambda e: e[0])]
        if links:
            span["links"] = [{"traceId": t, "spanId": s} for t, s in links]
        self.spans.append(span)


def otlp_attrs(attrs: dict) -> list[dict]:
    out = []
    for key, value in attrs.items():
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            v = {"boolValue": value}
        elif isinstance(value, int):
            v = {"intValue": str(value)}
        elif isinstance(value, float):
            v = {"doubleValue": value}
        else:
            v = {"stringValue": str(value)}
        out.append({"key": key, "value": v})
    return out


def short(text, limit: int = 200) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def mapping(doc: dict, key: str) -> dict:
    """A record field that should be a mapping, or {} for one that is absent or is not."""
    value = doc.get(key)
    return value if isinstance(value, dict) else {}


def http_artifacts(q, task) -> list[str]:
    return [a["url"] for a in q.recorded_artifacts(task) if str(a.get("url") or "").startswith("http")]


def task_repo(q, task) -> str:
    """The task's repository as its first change request names it, else its checkout's name."""
    urls = http_artifacts(q, task)
    named = change_request(urls[0]).get("repo") if urls else None
    return named or os.path.basename(str(task.doc.get("repo") or "").rstrip("/\\"))


def task_spans(q, task, now: int) -> list[dict]:
    """Every closed stage of one task, as OTLP spans."""
    d = task.doc
    created = to_ns(q, d.get("created"))
    if created is None:
        return []
    dispatched, concluded = to_ns(q, d.get("dispatched_at")), to_ns(q, d.get("concluded_at"))
    landed = to_ns(q, mapping(d, "landing").get("at")) if task.state == "landed" else None
    abandoned = to_ns(q, mapping(d, "abandoned").get("at")) if task.state == "abandoned" else None
    end = landed or abandoned
    lines = progress_lines(task)
    artifacts = http_artifacts(q, task)

    b = Builder(task.ref, {"factory.task.id": task.ref, TOPIC: task.topic})
    b.add("root", "factory task", created, end, parent=None, stage=False, attrs=_root_attrs(q, task),
          links=[(trace_id(task.topic, "topic"), span_id(task.topic, "root", "topic"))])
    b.add("queued", "queued", created, dispatched or abandoned)
    if dispatched:
        _work(b, q, d, dispatched, concluded or abandoned)
    _stretches(b, q, lines, end)
    _declares(b, q, lines, end, now)
    for url in artifacts:
        _change_request(b, q, task, url, lines, landed, abandoned, url == artifacts[0])
    if landed:
        merged = next((to_ns(q, line["shepherd"].get("at")) for line in lines
                       if mapping(line, "shepherd").get("condition") == "merged"), None)
        b.add("landed", "landed", merged or landed, landed,
              attrs={"vcs.change.state": "merged", "factory.merged.by": "shepherd" if merged else "forge"})
    if abandoned:
        b.add("abandoned", "abandoned", abandoned, abandoned,
              attrs={"factory.abandon.why": short(mapping(d, "abandoned").get("why"))})
    return b.spans


def _root_attrs(q, task) -> dict:
    d = task.doc
    return {
        "factory.task.title": d.get("title"),
        REPO: task_repo(q, task),
        "factory.agent": d.get("agent"),
        "factory.host": d.get("host"),
        "factory.publish.method": mapping(d, "publish").get("method"),
        "factory.outcome": d.get("outcome"),
        STATE: task.state,
        "vcs.ref.head.name": d.get("branch"),
        "vcs.ref.base.name": d.get("base"),
        "cicd.pipeline.name": "factory",
        "cicd.pipeline.run.id": task.ref,
        "cicd.pipeline.result": "success" if task.state == "landed" else "cancellation",
    }


def _work(b: Builder, q, d: dict, start: int, end) -> None:
    """Dispatched to the result written: the lead's sends and each refuel as its events."""
    events = [(to_ns(q, s.get("at")), "lead send", {}) for s in d.get("sends") or [] if isinstance(s, dict)]
    events += [(to_ns(q, r.get("at")), "refuel", {"factory.refuel.why": short(r.get("why"))})
               for r in d.get("refuels") or [] if isinstance(r, dict)]
    b.add("work", "work", start, end, attrs={"factory.outcome": d.get("outcome")},
          events=[e for e in events if e[0] is not None])


def _stretches(b: Builder, q, lines: list, end) -> None:
    """The session's working / at-rest / blocked stretches, from `watch`'s transitions."""
    moves = sorted((line for line in lines if "to" in line and "seq" in line),
                   key=lambda line: (to_ns(q, line.get("at")) or 0, line.get("seq") or 0))
    current = None  # (name, start, key)
    for move in moves:
        at = to_ns(q, move.get("at"))
        if at is None:
            continue
        name = STRETCHES.get(move.get("to"))
        if current and name == current[0]:
            continue
        if current:
            b.add(current[2], current[0], current[1], at)
        current = (name, at, f"session:{move.get('seq')}") if name else None
    if current and end:
        b.add(current[2], current[0], current[1], end)


def _declares(b: Builder, q, lines: list, end, now: int) -> None:
    """A worker's own word that it waits: from the declaration to the first of its
    deadline, its next declaration, the session working again, or the end."""
    said = [(to_ns(q, line.get("observed")), line["declare"], i) for i, line in enumerate(lines)
            if isinstance(line.get("declare"), dict)]
    working = sorted(to_ns(q, line.get("at")) or 0 for line in lines if line.get("to") == "working")
    for n, (at, declare, i) in enumerate(said):
        if at is None:
            continue
        ends = [x for x in (to_ns(q, declare.get("until")), end) if x is not None]
        ends += [x for x in working if x > at][:1]
        ends += [x for x, _d, _i in said[n + 1:] if x is not None and x >= at][:1]
        stop = min(ends) if ends else None
        if stop is not None and stop > now:
            stop = None  # a deadline still ahead: the wait has not closed
        kind = declare.get("state") if declare.get("state") in ("paused", "needs-input") else "paused"
        b.add(f"declare:{i}", kind, at, stop, attrs={"factory.declare.why": short(declare.get("why"))})


def _change_request(b: Builder, q, task, url: str, lines: list, landed, abandoned, primary: bool) -> None:
    """One change request: first seen to merged or closed, its publish states as
    events, and one child span per review round."""
    cr = change_request(url)
    key = f"cr:{url}"
    # Publish states carry no URL: they are the task's, so they go on its first change request.
    publish = [line["publish"] for line in lines if primary and mapping(line, "publish")]
    herded = [line["shepherd"] for line in lines if mapping(line, "shepherd").get("pr") in (url, None)
              and mapping(line, "shepherd")]
    seen = [to_ns(q, x.get("at")) for x in [*publish, *herded, mapping(task.doc, "artifact_check")]]
    seen = [x for x in seen if x is not None]
    if not seen:
        return
    merged = next((to_ns(q, s.get("at")) for s in herded if s.get("condition") == "merged"), None)
    gone = next((to_ns(q, p.get("at")) for p in publish if p.get("state") in ("merged", "closed")), None)
    if merged or landed:
        state = "merged"
    elif abandoned or gone:
        state = "closed"
    else:
        state = "open"
    b.add(key, "change request", min(seen), merged or gone or landed or abandoned,
          events=_publish_events(q, publish, herded), attrs={
              "vcs.change.id": cr.get("number"),
              "vcs.change.state": state,
              "vcs.repository.url.full": cr.get("repo_url"),
              "vcs.ref.head.name": task.doc.get("branch"),
              "vcs.ref.base.name": task.doc.get("base"),
              REPO: cr.get("repo"),
          })
    _reviews(b, q, url, key, min(seen))


def _publish_events(q, publish: list, herded: list) -> list:
    """Each change of publish state, and each shepherd condition short of the merge."""
    events, last = [], None
    for p in publish:
        at = to_ns(q, p.get("at"))
        if p.get("state") != last and at is not None:
            events.append((at, str(p.get("state")),
                           {"factory.publish.by": p.get("by"), "factory.publish.detail": short(p.get("detail"))}))
        last = p.get("state")
    events += [(to_ns(q, s.get("at")), f"shepherd {s.get('condition')}",
                {"factory.shepherd.detail": short(s.get("detail"))})
               for s in herded if to_ns(q, s.get("at")) is not None and s.get("condition") != "merged"]
    return events


def _reviews(b: Builder, q, url: str, parent: str, start: int) -> None:
    """One span per recorded review round, each from the round before it."""
    prev = start
    for n, rnd in enumerate(q.review_lifecycle().rounds(q, url), 1):
        at = to_ns(q, rnd.get("at"))
        if at is None:
            continue
        number = lambda key: rnd.get(key) if isinstance(rnd.get(key), int) else None  # noqa: E731
        b.add(f"review:{url}:{n}", "review", min(prev, at), at, parent=parent, attrs={
            "factory.review.round": n,
            "factory.review.score": number("score"),
            "factory.review.findings": number("findings"),
            "factory.review.state": rnd.get("state"),
            "vcs.ref.head.revision": rnd.get("head"),
        })
        prev = at


def topic_spans(q, slug: str, doc: dict) -> list[dict]:
    """A topic's own trace, once it is archived: the parent every task's root links to."""
    start, end = to_ns(q, doc.get("created")), to_ns(q, doc.get("archived"))
    b = Builder(slug, {TOPIC: slug}, kind="topic")
    b.add("root", "factory topic", start, end, parent=None, stage=False, attrs={"factory.topic.title": doc.get("title")})
    return b.spans


# --- the factory's measures -------------------------------------------------------
#
# Recomputed from the records on every export, never accumulated here: the
# histograms are CUMULATIVE over every task that ever landed, from the moment
# this fleet first exported (`metrics_start`), so a re-send is the same count
# and a lost one costs nothing the next pass does not carry. The gauges are
# what the live queue holds now.

# Seconds: five minutes to a week, the span between a quick fix and a stalled one.
TIME_BOUNDS = [300, 900, 1800, 3600, 7200, 14400, 28800, 86400, 172800, 604800]
ROUND_BOUNDS = [1, 2, 3, 4, 5, 8]
REWORK_BOUNDS = [0, 1, 2, 3, 5, 8, 13]

# A shepherd condition that is not a fix somebody had to make.
NOT_REWORK = ("merged", "ready", "open")


def first_seen(q, task, lines: list) -> int | None:
    """When fleet first saw the task's change request: its earliest publish
    state, shepherd condition or artifact check."""
    check = task.doc.get("artifact_check") if isinstance(task.doc.get("artifact_check"), dict) else {}
    seen = [to_ns(q, line[k].get("at")) for line in lines for k in ("publish", "shepherd")
            if isinstance(line.get(k), dict)] + [to_ns(q, check.get("at"))]
    seen = [x for x in seen if x is not None]
    return min(seen) if seen else None


def measures(q, task) -> dict:
    """What one landed task contributes to each histogram, in its own units."""
    d = task.doc
    lines = progress_lines(task)
    out = {}
    created, landed = to_ns(q, d.get("created")), to_ns(q, (d.get("landing") or {}).get("at"))
    if created and landed:
        out["factory.task.lead_time"] = (landed - created) / 1e9
    dispatched, seen = to_ns(q, d.get("dispatched_at")), first_seen(q, task, lines)
    if dispatched and seen:
        out["factory.task.dispatch_to_change_request"] = max(seen - dispatched, 0) / 1e9
    urls = http_artifacts(q, task)
    rounds = q.review_lifecycle().rounds(q, urls[0]) if urls else []
    clean = next((n for n, r in enumerate(rounds, 1) if r.get("state") == "clean"), None)
    if clean:
        out["factory.task.review_rounds"] = clean
    out["factory.task.rework"] = rework(d, lines, rounds)
    return out


def rework(doc: dict, lines: list, rounds: list) -> int:
    """Lead sends, shepherd fixer conditions, failing-check spells and unclean review rounds."""
    failing, last = 0, None
    for line in lines:
        state = (line.get("publish") or {}).get("state") if isinstance(line.get("publish"), dict) else None
        if state:
            failing += state == "checks-failed" and last != "checks-failed"
            last = state
    fixers = sum(1 for line in lines if isinstance(line.get("shepherd"), dict)
                 and line["shepherd"].get("condition") not in NOT_REWORK)
    return (len([s for s in doc.get("sends") or [] if isinstance(s, dict)]) + fixers + failing
            + sum(1 for r in rounds if r.get("state") != "clean"))


HISTOGRAMS = {
    "factory.task.lead_time": ("s", TIME_BOUNDS, "task added to landed"),
    "factory.task.dispatch_to_change_request": ("s", TIME_BOUNDS, "dispatched to its change request first seen"),
    "factory.task.review_rounds": ("{round}", ROUND_BOUNDS, "review rounds up to the first clean one"),
    "factory.task.rework": ("{event}", REWORK_BOUNDS,
                            "lead sends, shepherd fixer conditions, failing-check spells and unclean review rounds"),
}


def histogram(values: list, bounds: list) -> dict:
    counts = [0] * (len(bounds) + 1)
    for v in values:
        counts[next((i for i, b in enumerate(bounds) if v <= b), len(bounds))] += 1
    return {"count": str(len(values)), "sum": float(sum(values)), "min": float(min(values)),
            "max": float(max(values)), "bucketCounts": [str(c) for c in counts], "explicitBounds": bounds}


def metrics(q, live, every, start: int, now: int) -> list:
    """Every metric one export sends, as OTLP/JSON."""
    def gauge(name, unit, about, rows):
        return {"name": name, "unit": unit, "description": about, "gauge": {"dataPoints": [
            {"attributes": otlp_attrs(a), "timeUnixNano": str(now), "asInt": str(n)} for a, n in rows]}}

    by_state = {}
    for task in live.tasks.values():
        by_state[task.state] = by_state.get(task.state, 0) + 1
    declared = q.declared_tasks(live)
    out = [
        gauge("factory.queue.tasks", TASKS, "tasks in the live queue, by state",
              [({STATE: s}, n) for s, n in sorted(by_state.items())]),
        gauge("factory.tasks.ready", TASKS, "tasks plan would dispatch now", [({}, len(live.ready()))]),
        gauge("factory.tasks.stalled", TASKS, "dispatched, at rest past half an hour, nothing to show",
              [({}, len(q.stalled_tasks(live, declared)))]),
        gauge("factory.tasks.needs_input", TASKS, "workers that declared they need a decision",
              [({}, sum(1 for r in declared.values() if r["state"] == "needs-input"))]),
    ]

    ends, samples = {}, {}
    for task in every.tasks.values():
        if task.state not in q.TERMINAL_STATES:
            continue
        repo = task_repo(q, task)
        ends[(task.state, repo)] = ends.get((task.state, repo), 0) + 1
        if task.state != "landed":
            continue
        for name, value in measures(q, task).items():
            samples.setdefault(name, {}).setdefault(repo, []).append(value)
    out.append({"name": "factory.tasks.concluded", "unit": TASKS, "description": "tasks landed or abandoned",
                "sum": {"aggregationTemporality": 2, "isMonotonic": True, "dataPoints": [
                    {"attributes": otlp_attrs({STATE: s, REPO: r}),
                     "startTimeUnixNano": str(start), "timeUnixNano": str(now), "asInt": str(n)}
                    for (s, r), n in sorted(ends.items())]}})
    for name, (unit, bounds, about) in HISTOGRAMS.items():
        points = [{"attributes": otlp_attrs({REPO: repo}), "startTimeUnixNano": str(start),
                   "timeUnixNano": str(now), **histogram(values, bounds)}
                  for repo, values in sorted(samples.get(name, {}).items())]
        if points:
            out.append({"name": name, "unit": unit, "description": about,
                        "histogram": {"aggregationTemporality": 2, "dataPoints": points}})
    return out


# --- the reconciler's lines, as log records -----------------------------------
#
# The loop writes one greppable line per notice decision and keeps each child's
# failure for `status`; both reach a collector as log records. The loop never
# talks to a collector: it APPENDS to a spool here, which costs a file append,
# and the next export ships it. Only while telemetry is on, so a fleet that
# never asked keeps no spool.

SPOOL = "logs.jsonl"
SENDING = "logs.sending.jsonl"
# A collector down for days must not grow the spool without end: past this,
# new records are dropped until an export drains it.
SPOOL_MAX_BYTES = 4 * 1024 * 1024
SPOOL_LOCK_WAIT = 10.0

NOTICE = re.compile(r"notice (?P<kind>\S+) → (?P<who>.+?)(?: \[(?P<id>[^\]]+)\])? "
                    r"(?P<road>typed|mailbox|waiting|relayed|skipped|dropped|failed|ran)(?:: |$)")
# A task ref as the queue spells one: `<topic>/<NN>-<slug>`.
TASK_REF = re.compile(r"(?<![\w/.-])([a-z0-9][a-z0-9-]*)/(\d{2}-[a-z0-9-]+)")

INFO, WARN = (9, "INFO"), (13, "WARN")


def _spool(severity: tuple, body: str, attrs: dict) -> None:
    if not enabled():
        return
    path = os.path.join(state_dir(), SPOOL)
    os.makedirs(state_dir(), exist_ok=True)
    record = {"timeUnixNano": str(time.time_ns()), "severityNumber": severity[0], "severityText": severity[1],
              "body": {"stringValue": body}, "attributes": otlp_attrs(attrs)}
    line = json.dumps(record, ensure_ascii=False) + "\n"
    try:
        # Telemetry must never hold up the reconciler, including behind an exporter.
        with fleet_platform.exclusive_lock(path + ".lock"):
            pending = 0
            for name in (SPOOL, SENDING):
                try:
                    pending += os.path.getsize(os.path.join(state_dir(), name))
                except FileNotFoundError:
                    pass
            if pending + len(line.encode("utf-8")) <= SPOOL_MAX_BYTES:
                fleet_platform.append_record(path, line)
    except fleet_platform.LockHeld:
        pass


def record_line(line: str) -> None:
    """A reconciler log line: spooled when it is a notice decision, ignored otherwise."""
    at = line.find("notice ")
    m = NOTICE.match(line, at) if at >= 0 else None
    if not m:
        return
    body = line[at:].strip()
    attrs = {EVENT: "fleet.reconcile.notice", "fleet.notice.kind": m["kind"],
             "fleet.notice.recipient": m["who"], "fleet.notice.road": m["road"]}
    ref = TASK_REF.search(body)
    if ref:
        attrs.update({"factory.task.id": ref.group(0), TOPIC: ref.group(1)})
    _spool(WARN if m["road"] == "failed" else INFO, body, attrs)


def record_child(label: str, why: str, count: int) -> None:
    """A reconciler child failing (`why`), or succeeding again after failing (`why` empty)."""
    if why:
        _spool(WARN, f"{label}: {why}", {EVENT: "fleet.reconcile.child_failure",
                                          "fleet.reconcile.child": label, "fleet.reconcile.failures": count})
    else:
        _spool(INFO, f"{label}: running again", {EVENT: "fleet.reconcile.child_recovered",
                                                  "fleet.reconcile.child": label})


def drain_spool() -> list:
    """Retry the pending batch first; otherwise rotate the spool in one step."""
    spool, sending = os.path.join(state_dir(), SPOOL), os.path.join(state_dir(), SENDING)
    if not os.path.exists(sending) and os.path.exists(spool):
        with fleet_platform.exclusive_lock(spool + ".lock", wait=SPOOL_LOCK_WAIT):
            os.replace(spool, sending)
    try:
        with open(sending, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    if not out:
        os.remove(sending)
        print("telemetry: discarded unreadable log batch")
    return out


def keep_unsent(records: list) -> None:
    """What is still to send, written back in one step; nothing left removes the file."""
    sending = os.path.join(state_dir(), SENDING)
    if records:
        fleet_platform.write_record(sending, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    elif os.path.exists(sending):
        os.remove(sending)


# --- the export ----------------------------------------------------------------


def fingerprint(span: dict) -> str:
    return hashlib.sha256(json.dumps(span, sort_keys=True).encode()).hexdigest()[:16]


def load_state() -> dict:
    try:
        with open(os.path.join(state_dir(), STATE_FILE), encoding="utf-8") as fh:
            doc = json.loads(fh.read() or "{}")
    except (OSError, ValueError):
        doc = {}
    if not isinstance(doc, dict) or not isinstance(doc.get("sent"), dict):
        doc = {"sent": {}}
    return doc


def save_state(doc: dict) -> None:
    os.makedirs(state_dir(), exist_ok=True)
    fleet_platform.write_record(os.path.join(state_dir(), STATE_FILE), json.dumps(doc, sort_keys=True) + "\n")


def pending_spans(q, queue, state: dict, now: int) -> tuple[list, set]:
    """[(trace key, span)] not yet sent as they are now, and every trace key still in the queue."""
    sent = state["sent"]
    out, keys = [], set()
    for slug, doc in sorted(queue.topics.items()):
        keys.add("topic:" + slug)
        out += [("topic:" + slug, s) for s in topic_spans(q, slug, doc)]
    for ref, task in sorted(queue.tasks.items()):
        keys.add(ref)
        out += [(ref, s) for s in task_spans(q, task, now)]
    return [(k, s) for k, s in out if (sent.get(k) or {}).get(s["spanId"]) != fingerprint(s)], keys


def post(cfg: Settings, signal: str, items: list) -> tuple[int, str]:
    """One OTLP/HTTP request: (status, why). Status 0 is the request never answered."""
    kind, scoped, field = {"traces": ("resourceSpans", "scopeSpans", "spans"),
                           "metrics": ("resourceMetrics", "scopeMetrics", "metrics"),
                           "logs": ("resourceLogs", "scopeLogs", "logRecords")}[signal]
    body = json.dumps({kind: [{
        "resource": {"attributes": otlp_attrs(cfg.resource)},
        scoped: [{"scope": SCOPE, field: items}],
    }]}).encode("utf-8")
    req = urllib.request.Request(cfg.urls[signal], data=body, method="POST",
                                 headers={**cfg.headers[signal], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeouts[signal]) as resp:
            return resp.status, ""
    except urllib.error.HTTPError as exc:
        return exc.code, f"HTTP {exc.code} {exc.reason}"
    except (OSError, ValueError) as exc:  # URLError is an OSError
        return 0, str(getattr(exc, "reason", exc))


def down(status: int) -> bool:
    """A request the collector did not take but may take later: unanswered, a 5xx, or a 429."""
    return status == 0 or status >= 500 or status == 429


def tell(state: dict, line: str, key: str) -> None:
    """One line per CHANGE: the reconciler runs an export every few minutes, and
    a collector that stays down is one fact, not a line per pass."""
    if state.get("said") != key:
        print(f"telemetry: {line}")
    state["said"] = key


def answer(cfg: Settings, state: dict, signal: str, status: int, why: str, waiting: str) -> bool:
    """Whether the collector settled one request: it took it (2xx), or it read
    the payload and rejects its content (400), which the same bytes would meet
    again. Otherwise the request waits for a later export, told once: down (no
    answer, a 5xx, a 429), or refused for where or as whom it was sent — a 401
    or 403 is the token, a 404 an endpoint with /v1/traces twice, a 307 or 308
    a redirect urllib will not follow with a POST. That is the operator's to
    fix, and what it carried waits for it rather than being dropped. Keyed on
    the failure, not the signal, when it is down: one fact whichever request
    found it. A refusal is the signal's own, so a fixed traces token that leaves
    the metrics one wrong is news.
    Rejected spans are never sent again, so theirs is said each time; the
    measures go every pass, so theirs is told once."""
    url = cfg.urls[signal]
    if 200 <= status < 300:
        return True
    if status == 400:
        line = f"{url} rejected {waiting}: {why}; it is not sent again"
        if signal == "metrics":
            tell(state, line, f"rejected:{signal}:{why}")
        else:
            print(f"telemetry: {line}")
        return True
    if down(status):
        tell(state, f"{url} unreachable: {why}; {waiting} wait(s) for a later export", f"down:{why}")
    else:
        tell(state, f"{url} refused the request: {why}; fix ENDPOINT or HEADERS — {waiting} wait(s) for it",
             f"refused:{signal}:{why}")
    return False


def send_logs(cfg: Settings, state: dict) -> bool:
    """The spooled log records; whether every request was settled. What is not
    stays in the sending file for the next export."""
    os.makedirs(state_dir(), exist_ok=True)
    try:
        # A manual export may overlap the reconciler's child. Only one drains logs.
        with fleet_platform.exclusive_lock(os.path.join(state_dir(), "logs.export.lock")):
            records = drain_spool()
            while records:
                status, why = post(cfg, "logs", records[:CHUNK])
                if not answer(cfg, state, "logs", status, why, f"{len(records)} log record(s)"):
                    return False
                records = records[CHUNK:]
                keep_unsent(records)
            return True
    except fleet_platform.LockHeld:
        return False


def send_spans(cfg: Settings, state: dict, todo: list) -> tuple[bool, int]:
    """(whether every request was settled, how many spans the collector took)."""
    sent = 0
    for i in range(0, len(todo), CHUNK):
        batch = todo[i:i + CHUNK]
        status, why = post(cfg, "traces", [s for _k, s in batch])
        if not answer(cfg, state, "traces", status, why, f"{len(todo) - i} span(s)"):
            return False, sent
        for k, s in batch:
            state["sent"].setdefault(k, {})[s["spanId"]] = fingerprint(s)
        sent += len(batch) if 200 <= status < 300 else 0
        save_state(state)
    return True, sent


def export(dry_run: bool = False) -> None:
    cfg = Settings()
    if not cfg.enabled:
        return
    state = load_state()
    if cfg.problem:
        tell(state, cfg.problem, "problem:" + cfg.problem)
        if not dry_run:
            save_state(state)
        return

    q = _load_sibling("fleet_queue", "queue.py")
    now = time.time_ns()
    every = q.Queue(q.queue_root(), scope="all")
    todo, keys = pending_spans(q, every, state, now)
    measured = metrics(q, q.Queue(q.queue_root()), every, int(state.get("metrics_start") or now), now)
    if dry_run:
        print(f"telemetry: {len(todo)} span(s) would go to {cfg.url}, {len(measured)} metric(s) to "
              f"{cfg.urls['metrics']}")
        return
    state.setdefault("metrics_start", now)
    state["sent"] = {k: v for k, v in state["sent"].items() if k in keys}

    settled, sent = send_spans(cfg, state, todo)
    if settled:
        status, why = post(cfg, "metrics", measured)
        answer(cfg, state, "metrics", status, why, "the measures")
        # The measures go every pass, so their 2xx is the collector taking
        # telemetry again; their 400 keeps its rejection said.
        settled = 200 <= status < 300
    if settled:
        settled = send_logs(cfg, state)
    if settled:
        if str(state.get("said") or "").startswith(("down:", "problem:", "refused:")):
            print("telemetry: the collector takes telemetry again")
        state.pop("said", None)
    if sent:
        print(f"telemetry: {sent} span(s) sent to {cfg.url}")
    save_state(state)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="fleet telemetry", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="send closed task stages, the measures, and spooled reconciler logs")
    ex.add_argument("--dry-run", action="store_true", help="count what would be sent; send and write nothing")
    tid = sub.add_parser("trace-id", help="the trace id a task's spans carry")
    tid.add_argument("ref", help="<topic>/<task>")
    args = parser.parse_args(argv)
    if args.cmd == "trace-id":
        print(trace_id(args.ref))
    else:
        export(args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
