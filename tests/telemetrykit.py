"""An OTLP/HTTP collector in this process, and the records a task leaves behind.

The collector is a real HTTP server on a loopback port, so `fleet telemetry
export` runs as the reconciler runs it — a child process, over the network
stack — and nothing outside the test is ever reached. It keeps every request it
was sent, decoded, and answers what the test tells it to.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from harness import write


class Collector:
    def __init__(self):
        self.requests: list[dict] = []
        self.status = 200
        self.lock = threading.Lock()
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 — the stdlib's name
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                with collector.lock:
                    collector.requests.append({
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": json.loads(body.decode("utf-8")),
                    })
                    status = collector.status
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def spans(self) -> list[dict]:
        """Every span received, each with its resource's attributes as `resource`."""
        out = []
        for req in self.requests:
            if req["path"] != "/v1/traces":
                continue
            for rs in req["body"]["resourceSpans"]:
                resource = attrs(rs["resource"])
                for ss in rs["scopeSpans"]:
                    for span in ss["spans"]:
                        out.append({**span, "resource": resource})
        return out


    def metrics(self, request: int = -1) -> dict:
        """{metric name: (metric, [data points])} from one /v1/metrics request, the last by default."""
        reqs = [r for r in self.requests if r["path"] == "/v1/metrics"]
        out = {}
        if not reqs:
            return out
        for rm in reqs[request]["body"]["resourceMetrics"]:
            for sm in rm["scopeMetrics"]:
                for metric in sm["metrics"]:
                    kind = next(k for k in ("gauge", "sum", "histogram") if k in metric)
                    out[metric["name"]] = (metric, metric[kind]["dataPoints"])
        return out


def point(points: list, **want) -> dict:
    """The one data point whose attributes include `want`."""
    found = [p for p in points if all(attrs(p).get(k) == v for k, v in want.items())]
    assert len(found) == 1, (want, points)
    return found[0]


def attrs(holder: dict) -> dict:
    """OTLP's attribute list as a plain mapping of key to value."""
    out = {}
    for kv in holder.get("attributes") or []:
        (kind, value), = kv["value"].items()
        out[kv["key"]] = int(value) if kind == "intValue" else value
    return out


def telemetry_conf(text: str) -> None:
    """The operator's telemetry.conf, in the settings directory the harness isolates."""
    root = Path(os.environ["FLEET_TELEMETRY_ROOT"])
    write(root / "orchestration" / "telemetry.conf", text)


def task(topic: str, tid: str, doc: dict, progress: list[dict] = ()) -> Path:
    """One task's records, as `fleet queue` leaves them."""
    import yaml

    queue = Path(os.environ["FLEET_QUEUE_DIR"])
    topic_dir = queue / topic
    if not (topic_dir / "topic.yaml").exists():
        write(topic_dir / "topic.yaml", yaml.safe_dump(
            {"slug": topic, "title": topic, "created": "2026-10-01T08:00:00+00:00"}, sort_keys=False))
    tdir = topic_dir / tid
    base = {"id": tid, "topic": topic, "title": tid, "state": "queued", "repo": "/work/fleet",
            "host": None, "branch": f"feat/{tid}", "base": "main", "agent": "claude",
            "publish": {"method": "pr", "how": "open a pull request"}, "blocked_by": [],
            "created": "2026-10-01T08:00:00+00:00"}
    write(tdir / "task.yaml", yaml.safe_dump({**base, **doc}, sort_keys=False))
    write(tdir / "progress.jsonl", "".join(json.dumps(line) + "\n" for line in progress))
    return tdir


def review(url: str, record: dict) -> None:
    """The shepherd's review record for one change request, where review_lifecycle.py keeps it."""
    from harness import lib

    write(Path(lib("review_lifecycle.py").path(lib("queue.py"), url)), json.dumps(record) + "\n")


PR = "https://github.com/Thurbeen/fleet/pull/42"
HEAD1 = "a" * 40
HEAD2 = "b" * 40


def landed_task():
    """A task that went the whole way: queued, worked, published, reviewed twice, landed."""
    task("ship-it", "01-thing", {
        "state": "landed",
        "created": "2026-10-01T08:00:00+00:00",
        "dispatched_at": "2026-10-01T08:10:00+00:00",
        "session": None,
        "outcome": "shipped",
        "artifact": PR,
        "concluded_at": "2026-10-01T10:00:00+00:00",
        "sends": [{"at": "2026-10-01T09:30:00+00:00", "session": "s", "text": "answer the review"}],
        "landing": {"state": "merged", "detail": f"{PR} is merged", "at": "2026-10-01T11:05:00+00:00"},
    }, [
        {"seq": 1, "at": 1790842200000, "from": None, "to": "working", "reason": "state",
         "observed": "2026-10-01T08:10:00+00:00"},
        {"seq": 2, "at": 1790845800000, "from": "working", "to": "idle", "reason": "state",
         "observed": "2026-10-01T09:10:00+00:00"},
        {"publish": {"method": "pr", "how": "x", "state": "checks-failed", "detail": "failed checks: lint",
                     "at": "2026-10-01T09:15:00+00:00", "by": "shepherd"}, "observed": "2026-10-01T09:15:00+00:00"},
        {"seq": 3, "at": 1790847000000, "from": "idle", "to": "working", "reason": "state",
         "observed": "2026-10-01T09:30:00+00:00"},
        {"seq": 4, "at": 1790848800000, "from": "working", "to": "done", "reason": "state",
         "observed": "2026-10-01T10:00:00+00:00"},
        {"publish": {"method": "pr", "how": "x", "state": "open", "detail": "open",
                     "at": "2026-10-01T10:00:00+00:00", "by": "collect"}, "observed": "2026-10-01T10:00:00+00:00"},
        {"shepherd": {"condition": "merged", "detail": "squash-merged", "pr": PR,
                      "at": "2026-10-01T11:00:00+00:00"}, "observed": "2026-10-01T11:00:00+00:00"},
    ])
    review(PR, {"url": PR, "head": HEAD2, "state": "clean",
                "assessment": {"head": HEAD2, "score": 5, "findings": 0, "state": "clean",
                               "at": "2026-10-01T10:40:00+00:00"},
                "rounds": [
                    {"head": HEAD1, "score": 3, "findings": 2, "state": "findings", "at": "2026-10-01T10:20:00+00:00"},
                    {"head": HEAD2, "score": 5, "findings": 0, "state": "clean", "at": "2026-10-01T10:40:00+00:00"},
                ]})
