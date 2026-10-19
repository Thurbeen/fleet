"""One trace per task, rebuilt from its records and sent to any OTLP endpoint — or nowhere.

`fleet telemetry export` reads the queue the way `fleet queue list` does and
turns each task's stages into spans: queued, work, the session's working and
at-rest stretches, the change request, every review round with its score, and
the landing. The trace id is the task id's, so any tool computes the same one,
and a span id is its stage's, so sending a task twice is the same spans twice.
"""

from __future__ import annotations

import hashlib
import time

import pytest
from telemetrykit import Collector, attrs, review, task, telemetry_conf

from harness import run_fleet

PR = "https://github.com/Thurbeen/fleet/pull/42"
HEAD1 = "a" * 40
HEAD2 = "b" * 40


@pytest.fixture
def collector():
    c = Collector()
    yield c
    c.close()


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


def trace_id(ref: str) -> str:
    return hashlib.sha256(("fleet.task\0" + ref).encode()).hexdigest()[:32]


def ns(iso: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(iso).timestamp()) * 1_000_000_000


def by_name(spans):
    out = {}
    for span in spans:
        out.setdefault(span["name"], []).append(span)
    return out


def test_off_by_default_nothing_is_sent_and_nothing_is_said(collector):
    landed_task()
    run = run_fleet("telemetry", "export", OTEL_EXPORTER_OTLP_ENDPOINT=collector.endpoint)
    assert run.code == 0, run.out
    assert run.out.strip() == ""
    assert collector.requests == []


def test_a_landed_task_is_one_trace_with_a_span_per_stage(collector):
    landed_task()
    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")

    run = run_fleet("telemetry", "export")
    assert run.code == 0, run.out
    spans = collector.spans()
    assert spans, run.out
    assert {s["traceId"] for s in spans} == {trace_id("ship-it/01-thing")}
    names = by_name(spans)

    root, = names["factory task"]
    assert "parentSpanId" not in root or not root["parentSpanId"]
    assert int(root["startTimeUnixNano"]) == ns("2026-10-01T08:00:00+00:00")
    assert int(root["endTimeUnixNano"]) == ns("2026-10-01T11:05:00+00:00")
    ra = attrs(root)
    assert ra["factory.task.id"] == "ship-it/01-thing"
    assert ra["factory.topic"] == "ship-it"
    assert ra["factory.repo"] == "github.com/Thurbeen/fleet"
    assert ra["factory.agent"] == "claude"
    assert ra["cicd.pipeline.name"] == "factory"
    assert ra["cicd.pipeline.run.id"] == "ship-it/01-thing"
    assert ra["cicd.pipeline.result"] == "success"
    assert root["resource"]["service.name"] == "fleet"

    # Every other span hangs off the task, or off the change request.
    ids = {s["spanId"]: s for s in spans}
    for span in spans:
        if span is not root:
            assert span["parentSpanId"] in ids, span["name"]
        assert attrs(span)["factory.task.id"] == "ship-it/01-thing"

    queued, = names["queued"]
    assert (int(queued["startTimeUnixNano"]), int(queued["endTimeUnixNano"])) == (
        ns("2026-10-01T08:00:00+00:00"), ns("2026-10-01T08:10:00+00:00"))
    work, = names["work"]
    assert int(work["endTimeUnixNano"]) == ns("2026-10-01T10:00:00+00:00")
    assert attrs(work)["factory.outcome"] == "shipped"
    assert [e["name"] for e in work.get("events", [])] == ["lead send"]

    assert len(names["working"]) == 2
    assert len(names["at rest"]) == 2  # idle, and done until the task landed

    cr, = names["change request"]
    ca = attrs(cr)
    assert ca["vcs.change.id"] == "42"
    assert ca["vcs.repository.url.full"] == "https://github.com/Thurbeen/fleet"
    assert ca["vcs.change.state"] == "merged"
    assert int(cr["endTimeUnixNano"]) == ns("2026-10-01T11:00:00+00:00")
    assert "checks-failed" in [e["name"] for e in cr.get("events", [])]

    rounds = sorted(names["review"], key=lambda s: int(s["endTimeUnixNano"]))
    assert [attrs(r)["factory.review.score"] for r in rounds] == [3, 5]
    assert [attrs(r)["factory.review.round"] for r in rounds] == [1, 2]
    assert attrs(rounds[1])["vcs.ref.head.revision"] == HEAD2
    assert all(r["parentSpanId"] == cr["spanId"] for r in rounds)

    landed, = names["landed"]
    assert (int(landed["startTimeUnixNano"]), int(landed["endTimeUnixNano"])) == (
        ns("2026-10-01T11:00:00+00:00"), ns("2026-10-01T11:05:00+00:00"))


def test_exporting_twice_sends_nothing_new_and_new_records_send_only_what_is_new(collector):
    task("ship-it", "02-open", {"state": "dispatched", "dispatched_at": "2026-10-01T08:10:00+00:00",
                                "session": "s2"}, [
        {"seq": 1, "at": 1790842200000, "from": None, "to": "working", "reason": "state",
         "observed": "2026-10-01T08:10:00+00:00"},
    ])
    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")

    assert run_fleet("telemetry", "export").code == 0
    first = by_name(collector.spans())
    # A stage that has not closed is not a span yet: the task is still working.
    assert set(first) == {"queued"}

    assert run_fleet("telemetry", "export").code == 0
    assert len(collector.spans()) == 1, "a second export sent the same span again"

    progress = task_dir_progress("ship-it", "02-open")
    with open(progress, "a", encoding="utf-8", newline="\n") as fh:
        fh.write('{"seq": 2, "at": 1790845800000, "from": "working", "to": "idle", "reason": "state", '
                 '"observed": "2026-10-01T09:10:00+00:00"}\n')
    assert run_fleet("telemetry", "export").code == 0
    spans = collector.spans()
    assert [s["name"] for s in spans] == ["queued", "working"]


def task_dir_progress(topic: str, tid: str):
    import os
    from pathlib import Path

    return Path(os.environ["FLEET_QUEUE_DIR"]) / topic / tid / "progress.jsonl"


def test_a_dead_collector_costs_one_line_and_the_spans_go_later(collector, tmp_path):
    landed_task()
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{s.getsockname()[1]}"
    telemetry_conf(f"EXPORT=on\nENDPOINT={dead}\n")

    began = time.monotonic()
    run = run_fleet("telemetry", "export")
    assert run.code == 0, run.out
    assert time.monotonic() - began < 20
    lines = [line for line in run.out.splitlines() if line.strip()]
    assert len(lines) == 1, run.out
    assert dead in lines[0], run.out

    # The same failure again is not news.
    again = run_fleet("telemetry", "export")
    assert again.code == 0, again.out
    assert again.out.strip() == "", again.out

    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")
    assert run_fleet("telemetry", "export").code == 0
    assert by_name(collector.spans())["factory task"], "the spans a dead collector missed never went"


def test_the_standard_otel_variables_are_honoured(collector):
    landed_task()
    telemetry_conf("EXPORT=on\n")
    run = run_fleet(
        "telemetry", "export",
        OTEL_EXPORTER_OTLP_ENDPOINT=collector.endpoint,
        OTEL_EXPORTER_OTLP_HEADERS="authorization=Bearer%20xyz,x-team=factory",
        OTEL_SERVICE_NAME="my-fleet",
        OTEL_RESOURCE_ATTRIBUTES="deployment.environment.name=lab",
    )
    assert run.code == 0, run.out
    req = collector.requests[0]
    assert req["headers"]["authorization"] == "Bearer xyz"
    assert req["headers"]["x-team"] == "factory"
    assert req["headers"]["content-type"] == "application/json"
    span = collector.spans()[0]
    assert span["resource"]["service.name"] == "my-fleet"
    assert span["resource"]["deployment.environment.name"] == "lab"


def test_otel_sdk_disabled_wins_over_the_setting(collector):
    landed_task()
    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")
    run = run_fleet("telemetry", "export", OTEL_SDK_DISABLED="true")
    assert run.code == 0, run.out
    assert collector.requests == []


def test_an_abandoned_task_ends_in_an_abandoned_span(collector):
    task("ship-it", "03-dropped", {
        "state": "abandoned", "abandoned": {"at": "2026-10-01T09:00:00+00:00", "why": "not needed"},
    })
    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")
    assert run_fleet("telemetry", "export").code == 0
    names = by_name(collector.spans())
    root, = names["factory task"]
    assert attrs(root)["cicd.pipeline.result"] == "cancellation"
    queued, = names["queued"]
    assert int(queued["endTimeUnixNano"]) == ns("2026-10-01T09:00:00+00:00")
    gone, = names["abandoned"]
    assert attrs(gone)["factory.abandon.why"] == "not needed"


def test_trace_id_prints_the_id_any_tool_derives_from_the_task():
    run = run_fleet("telemetry", "trace-id", "ship-it/01-thing")
    assert run.code == 0, run.out
    assert run.out.strip() == trace_id("ship-it/01-thing")


def test_nothing_to_send_says_nothing_about_a_collector_it_never_asked(collector):
    """Recovery is announced only after a request went through."""
    import shutil
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{s.getsockname()[1]}"
    telemetry_conf(f"EXPORT=on\nENDPOINT={dead}\n")
    landed_task()
    assert "unreachable" in run_fleet("telemetry", "export").out
    shutil.rmtree(task_dir_progress("ship-it", "01-thing").parent.parent)
    quiet = run_fleet("telemetry", "export")
    assert quiet.code == 0, quiet.out
    assert quiet.out.strip() == "", quiet.out
