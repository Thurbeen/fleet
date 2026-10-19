"""The factory's measures, as OTLP metrics, from the same records and the same export.

Gauges say what the queue holds now — tasks by state, and how many are ready,
stalled or waiting on a decision. Cumulative histograms over every task that
landed carry the measures the factory is judged by: lead time (added to
landed), dispatch to change request, review rounds to a clean review, and
rework. They are recomputed from the records each pass, so a re-send is the
same count and never a double one.
"""

from __future__ import annotations

import pytest
from telemetrykit import Collector, landed_task, point, task, telemetry_conf

from harness import run_fleet


@pytest.fixture
def collector():
    c = Collector()
    yield c
    c.close()


def queue_of_four():
    landed_task()
    task("ship-it", "02-ready", {"state": "queued"})
    task("ship-it", "03-held", {"state": "queued", "blocked_by": [
        {"task": "ship-it/02-ready", "kind": "semantic-dependency", "why": "needs 02"}]})
    task("ship-it", "04-asks", {"state": "dispatched", "dispatched_at": "2026-10-01T08:10:00+00:00",
                                "session": "s4", "prompted": True}, [
        {"declare": {"state": "needs-input", "why": "which way?"}, "observed": "2026-10-01T09:00:00+00:00"},
    ])


def test_the_queue_is_measured_by_state_and_by_what_waits_on_somebody(collector):
    queue_of_four()
    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")
    run = run_fleet("telemetry", "export")
    assert run.code == 0, run.out
    m = collector.metrics()

    _metric, by_state = m["factory.queue.tasks"]
    assert int(point(by_state, **{"factory.state": "queued"})["asInt"]) == 2
    assert int(point(by_state, **{"factory.state": "dispatched"})["asInt"]) == 1
    assert int(point(by_state, **{"factory.state": "landed"})["asInt"]) == 1
    assert int(m["factory.tasks.ready"][1][0]["asInt"]) == 1
    assert int(m["factory.tasks.needs_input"][1][0]["asInt"]) == 1
    assert int(m["factory.tasks.stalled"][1][0]["asInt"]) == 0


def test_a_landed_task_feeds_the_factory_histograms(collector):
    landed_task()
    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")
    assert run_fleet("telemetry", "export").code == 0
    m = collector.metrics()

    def hist(name):
        metric, points = m[name]
        assert metric["histogram"]["aggregationTemporality"] == 2, "cumulative"
        p = point(points, **{"factory.repo": "github.com/Thurbeen/fleet"})
        return int(p["count"]), p["sum"]

    assert hist("factory.task.lead_time") == (1, 3 * 3600 + 5 * 60)         # 08:00 → 11:05
    assert hist("factory.task.dispatch_to_change_request") == (1, 65 * 60)  # 08:10 → 09:15
    assert hist("factory.task.review_rounds") == (1, 2)                     # 3/5, then 5/5
    # One lead send, one failing-checks spell, one review round with findings.
    assert hist("factory.task.rework") == (1, 3)
    assert m["factory.task.lead_time"][0]["unit"] == "s"

    _metric, ends = m["factory.tasks.concluded"]
    assert int(point(ends, **{"factory.state": "landed"})["asInt"]) == 1


def test_resending_the_same_records_is_the_same_count(collector):
    landed_task()
    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")
    assert run_fleet("telemetry", "export").code == 0
    assert run_fleet("telemetry", "export").code == 0
    first, second = collector.metrics(0), collector.metrics(1)
    a = first["factory.task.lead_time"][1][0]
    b = second["factory.task.lead_time"][1][0]
    assert (a["count"], a["startTimeUnixNano"]) == (b["count"], b["startTimeUnixNano"])


def test_measures_a_collector_rejects_are_told_once_and_not_every_pass(collector):
    """The measures go every pass, so a 400 for them would otherwise be a log line per pass."""
    landed_task()
    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")
    collector.statuses["/v1/metrics"] = 400
    assert "rejected" in run_fleet("telemetry", "export").out
    again = run_fleet("telemetry", "export")
    assert again.code == 0, again.out
    assert again.out.strip() == "", "the same rejection again is not news"


def test_trace_only_settings_stay_with_the_traces(collector):
    """OTEL_EXPORTER_OTLP_TRACES_HEADERS is the traces backend's token, not the metrics one's."""
    landed_task()
    telemetry_conf("EXPORT=on\n")
    run = run_fleet("telemetry", "export",
                    OTEL_EXPORTER_OTLP_ENDPOINT=collector.endpoint,
                    OTEL_EXPORTER_OTLP_TRACES_HEADERS="authorization=Bearer%20traces",
                    OTEL_EXPORTER_OTLP_METRICS_HEADERS="authorization=Bearer%20metrics")
    assert run.code == 0, run.out
    by_path = {r["path"]: r["headers"] for r in collector.requests}
    assert by_path["/v1/traces"]["authorization"] == "Bearer traces"
    assert by_path["/v1/metrics"]["authorization"] == "Bearer metrics"
