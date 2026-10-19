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
