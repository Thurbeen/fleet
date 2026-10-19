"""The loop sends each task's trace on a clock of its own — only when the operator turned it on.

`fleet telemetry export` is a bounded child like `sync`, run while the setting
says `EXPORT=on` and never otherwise, so a fleet that did not ask runs nothing
new. A collector that is down costs one log line and never the pass.
"""

from __future__ import annotations

import socket

import pytest
from reconcilekit import wait_for
from telemetrykit import Collector, task, telemetry_conf


@pytest.fixture
def collector():
    c = Collector()
    yield c
    c.close()


@pytest.fixture
def landed(monkeypatch):
    monkeypatch.setenv("FLEET_RECONCILE_TELEMETRY_SECS", "2")
    task("ship-it", "01-thing", {
        "state": "abandoned", "abandoned": {"at": "2026-10-01T09:00:00+00:00", "why": "not needed"},
    })


def passes(recon, n: int) -> None:
    start = recon.count("collect")
    assert wait_for(lambda: recon.count("collect") >= start + n, 30), recon.log()


def test_the_loop_sends_a_tasks_trace_when_telemetry_is_on(recon, collector, landed):
    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")
    recon("ensure")
    assert wait_for(lambda: any(s["name"] == "factory task" for s in collector.spans()), 30), recon.log()
    assert wait_for(lambda: "telemetry: " in recon.log(), 30), recon.log()
    passes(recon, 2)
    assert sum(s["name"] == "factory task" for s in collector.spans()) == 1, "the loop sent the same span twice"


def test_the_loop_runs_no_export_while_telemetry_is_off(recon, collector, landed, monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", collector.endpoint)
    recon("ensure")
    passes(recon, 3)
    assert collector.requests == []
    assert "telemetry" not in recon.log()


def test_a_dead_collector_is_one_log_line_and_never_the_pass(recon, landed):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{s.getsockname()[1]}"
    telemetry_conf(f"EXPORT=on\nENDPOINT={dead}\n")
    recon("ensure")
    assert wait_for(lambda: "unreachable" in recon.log(), 30), recon.log()
    passes(recon, 3)
    assert recon.log().count("unreachable") == 1, recon.log()
    status = recon("status")
    assert "telemetry" not in status.out, "a dead collector is not a failing child"


def test_a_failing_child_and_a_notice_reach_the_collector_as_log_records(recon, collector, landed, monkeypatch):
    monkeypatch.setenv("RECON_FAIL", "refuel")
    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")
    recon.ready("ship-it/02-next")
    recon("ensure")

    def events():
        return {r["attrs"].get("event.name") for r in collector.logs()}

    assert wait_for(lambda: {"fleet.reconcile.child_failure", "fleet.reconcile.notice"} <= events(), 40), \
        (recon.log(), collector.logs())
    failed = next(r for r in collector.logs() if r["attrs"]["event.name"] == "fleet.reconcile.child_failure")
    assert failed["attrs"]["fleet.reconcile.child"] == "refuel"
    notice = next(r for r in collector.logs() if r["attrs"]["event.name"] == "fleet.reconcile.notice")
    assert notice["attrs"]["fleet.notice.kind"] == "ready"
    assert notice["attrs"]["factory.task.id"] == "ship-it/02-next"
