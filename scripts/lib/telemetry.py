#!/usr/bin/env python3
"""Fleet's OpenTelemetry: one trace per task, rebuilt from the task's records
and sent to any OTLP/HTTP endpoint — when the operator turns it on.

    uv run fleet telemetry export [--dry-run]   one pass: send what closed since the last
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
once — the same failure again is not news — and its recovery once, so the
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
holds `exported.json` and nothing else. The reconciler runs `export` as a
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

# watch's session states, as the three stretches a trace shows. Anything else —
# `unreported`, `stopped`, a deletion — ends the stretch before it.
STRETCHES = {"working": "working", "idle": "at rest", "done": "at rest", "blocked": "blocked"}


# --- settings ------------------------------------------------------------------


class Settings:
    """What one export runs under: whether, where, and what it says about itself."""

    def __init__(self):
        root = os.environ.get("FLEET_TELEMETRY_ROOT") or CHECKOUT
        path = os.path.join(root, TELEMETRY_CONF)
        conf = agent_settings.read_conf(path if os.path.exists(path) else os.path.join(root, TELEMETRY_CONF_DEFAULTS))
        env = os.environ.get

        self.enabled = conf.get("EXPORT", "").lower() in ON and env("OTEL_SDK_DISABLED", "").lower() != "true"
        if conf.get("ENDPOINT"):
            self.url = conf["ENDPOINT"].rstrip("/") + "/v1/traces"
        elif env("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
            self.url = env("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        else:
            self.url = (env("OTEL_EXPORTER_OTLP_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/") + "/v1/traces"
        self.protocol = (conf.get("PROTOCOL") or env("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL")
                         or env("OTEL_EXPORTER_OTLP_PROTOCOL") or "http/json")
        self.headers = parse_pairs(conf.get("HEADERS") or env("OTEL_EXPORTER_OTLP_TRACES_HEADERS")
                                   or env("OTEL_EXPORTER_OTLP_HEADERS") or "")
        try:
            ms = int(conf.get("TIMEOUT_MS") or env("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT")
                     or env("OTEL_EXPORTER_OTLP_TIMEOUT") or DEFAULT_TIMEOUT_MS)
        except ValueError:
            ms = DEFAULT_TIMEOUT_MS
        self.timeout = max(ms, 1) / 1000
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

    b = Builder(task.ref, {"factory.task.id": task.ref, "factory.topic": task.topic})
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
        "factory.repo": task_repo(q, task),
        "factory.agent": d.get("agent"),
        "factory.host": d.get("host"),
        "factory.publish.method": mapping(d, "publish").get("method"),
        "factory.outcome": d.get("outcome"),
        "factory.state": task.state,
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
              "factory.repo": cr.get("repo"),
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
    b = Builder(slug, {"factory.topic": slug}, kind="topic")
    b.add("root", "factory topic", start, end, parent=None, stage=False, attrs={"factory.topic.title": doc.get("title")})
    return b.spans


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


def pending_spans(q, state: dict, now: int) -> tuple[list, set]:
    """[(trace key, span)] not yet sent as they are now, and every trace key still in the queue."""
    queue = q.Queue(q.queue_root(), scope="all")
    sent = state["sent"]
    out, keys = [], set()
    for slug, doc in sorted(queue.topics.items()):
        keys.add("topic:" + slug)
        out += [("topic:" + slug, s) for s in topic_spans(q, slug, doc)]
    for ref, task in sorted(queue.tasks.items()):
        keys.add(ref)
        out += [(ref, s) for s in task_spans(q, task, now)]
    return [(k, s) for k, s in out if (sent.get(k) or {}).get(s["spanId"]) != fingerprint(s)], keys


def post(cfg: Settings, spans: list) -> tuple[int, str]:
    """One OTLP/HTTP request: (status, why). Status 0 is the request never answered."""
    body = json.dumps({"resourceSpans": [{
        "resource": {"attributes": otlp_attrs(cfg.resource)},
        "scopeSpans": [{"scope": SCOPE, "spans": spans}],
    }]}).encode("utf-8")
    req = urllib.request.Request(cfg.url, data=body, method="POST",
                                 headers={**cfg.headers, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
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


def send_spans(cfg: Settings, state: dict, todo: list) -> tuple[bool, int]:
    """(whether the collector settled every request, how many spans it took).

    SETTLED IS NARROW: a 2xx, or a 400 — the collector read the payload and
    rejects its content, which the same bytes would meet again. Only those
    count spans as sent. Everything else leaves them pending with one line,
    told once: no answer, a 5xx or a 429 is a collector that is down; a 401 or
    403 is the token, a 404 an endpoint with /v1/traces twice, a 307 or 308 a
    redirect urllib will not follow with a POST. Those are the setting's to
    fix, and the spans wait for it rather than being dropped."""
    sent = 0
    for i in range(0, len(todo), CHUNK):
        batch = todo[i:i + CHUNK]
        status, why = post(cfg, [s for _k, s in batch])
        waiting = len(todo) - i
        if 200 <= status < 300:
            sent += len(batch)
        elif status == 400:
            print(f"telemetry: {cfg.url} rejected {len(batch)} span(s): {why}; they are not sent again")
        elif down(status):
            # Keyed on the failure, not the request: down is one fact.
            tell(state, f"{cfg.url} unreachable: {why}; {waiting} span(s) wait for a later export", f"down:{why}")
            return False, sent
        else:
            tell(state, f"{cfg.url} refused the request: {why}; fix ENDPOINT or HEADERS — "
                        f"{waiting} span(s) wait for it", f"refused:{why}")
            return False, sent
        for k, s in batch:
            state["sent"].setdefault(k, {})[s["spanId"]] = fingerprint(s)
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
    todo, keys = pending_spans(q, state, time.time_ns())
    if dry_run:
        print(f"telemetry: {len(todo)} span(s) would go to {cfg.url}")
        return
    state["sent"] = {k: v for k, v in state["sent"].items() if k in keys}

    answered, sent = send_spans(cfg, state, todo)
    # Recovery is news only once a request went through; with nothing to
    # send, the collector was never asked and a standing failure stands.
    if answered and todo:
        if str(state.get("said") or "").startswith(("down:", "problem:", "refused:")):
            print(f"telemetry: {cfg.url} takes telemetry again")
        state.pop("said", None)
    elif answered and str(state.get("said") or "").startswith("problem:"):
        state.pop("said", None)
    if sent:
        print(f"telemetry: {sent} span(s) sent to {cfg.url}")
    save_state(state)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="fleet telemetry", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="send every task stage that closed since the last export")
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
