"""The reconciler's two greppable lines — a notice decision and a child's
failure — as OTLP log records, spooled while telemetry is on and sent by the
next export.

The loop never talks to a collector itself: it appends to a spool in the
telemetry runtime directory, which costs a file append, and the export child
ships it. A spool the collector could not take stays for the next export.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from telemetrykit import Collector, telemetry_conf

from harness import lib, run_fleet

NOTICE = "notice ready → Gate Control [lead-uui] typed: 1 task(s) ready — ship-it/02-next"


@pytest.fixture
def collector():
    c = Collector()
    yield c
    c.close()


def spool() -> Path:
    return Path(os.environ["FLEET_TELEMETRY_DIR"]) / "logs.jsonl"


def test_nothing_is_spooled_while_telemetry_is_off():
    tel = lib("telemetry.py")
    tel.record_line(NOTICE)
    tel.record_child("collect", "exit 5: boom", 1)
    assert not spool().exists()


def test_a_notice_and_a_child_failure_reach_the_collector_as_log_records(collector):
    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")
    tel = lib("telemetry.py")
    tel.record_line("[2026-10-19T11:00:00+02:00] notify: " + NOTICE)
    tel.record_line("[2026-10-19T11:00:00+02:00] collect:")  # not a notice: nothing
    tel.record_child("collect", "exit 5: boom", 3)
    tel.record_child("collect", "", 0)

    run = run_fleet("telemetry", "export")
    assert run.code == 0, run.out
    logs = collector.logs()
    assert len(logs) == 3, logs
    notice, failed, recovered = logs
    assert notice["body"]["stringValue"] == NOTICE
    assert notice["attrs"]["event.name"] == "fleet.reconcile.notice"
    assert notice["attrs"]["fleet.notice.kind"] == "ready"
    assert notice["attrs"]["fleet.notice.recipient"] == "Gate Control"
    assert notice["attrs"]["fleet.notice.road"] == "typed"
    assert notice["attrs"]["factory.task.id"] == "ship-it/02-next"
    assert notice["attrs"]["factory.topic"] == "ship-it"
    assert notice["severityText"] == "INFO"

    assert failed["attrs"]["event.name"] == "fleet.reconcile.child_failure"
    assert failed["attrs"]["fleet.reconcile.child"] == "collect"
    assert failed["attrs"]["fleet.reconcile.failures"] == 3
    assert failed["severityText"] == "WARN"
    assert recovered["attrs"]["event.name"] == "fleet.reconcile.child_recovered"

    # Sent is gone: a second export sends no record twice.
    assert run_fleet("telemetry", "export").code == 0
    assert len(collector.logs()) == 3


def test_a_spool_the_collector_could_not_take_waits_for_the_next_export(collector):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{s.getsockname()[1]}"
    telemetry_conf(f"EXPORT=on\nENDPOINT={dead}\n")
    lib("telemetry.py").record_line(NOTICE)
    assert run_fleet("telemetry", "export").code == 0

    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")
    assert run_fleet("telemetry", "export").code == 0
    assert [r["body"]["stringValue"] for r in collector.logs()] == [NOTICE]


def test_refused_logs_cannot_grow_the_pending_spool_without_bound(collector):
    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")
    collector.statuses["/v1/logs"] = 401
    tel = lib("telemetry.py")
    for _ in range(4):
        tel.record_child("collect", "x" * (2 * 1024 * 1024), 1)
        assert run_fleet("telemetry", "export").code == 0
    pending = sum(p.stat().st_size for p in spool().parent.glob("logs*.jsonl"))
    assert pending <= tel.SPOOL_MAX_BYTES, pending
    collector.statuses["/v1/logs"] = 200
    assert run_fleet("telemetry", "export").code == 0
    assert not list(spool().parent.glob("logs*.jsonl"))


def test_busy_spool_drops_a_record_without_waiting():
    telemetry_conf("EXPORT=on\n")
    tel = lib("telemetry.py")
    spool().parent.mkdir(parents=True, exist_ok=True)
    with tel.fleet_platform.exclusive_lock(str(spool()) + ".lock"):
        started = time.monotonic()
        tel.record_line(NOTICE)
        assert time.monotonic() - started < 1
    assert not spool().exists()


def test_concurrent_exports_do_not_send_the_same_logs_twice(collector):
    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")
    lib("telemetry.py").record_line(NOTICE)
    entered, release = threading.Event(), threading.Event()
    handler = collector.server.RequestHandlerClass
    original = handler.do_POST

    def paused_post(request):
        if request.path == "/v1/logs":
            entered.set()
            release.wait(5)
        original(request)

    handler.do_POST = paused_post
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_fleet, "telemetry", "export")
        try:
            assert entered.wait(5)
            second = pool.submit(run_fleet, "telemetry", "export")
            assert second.result(timeout=8).code == 0
        finally:
            release.set()
        assert first.result(timeout=8).code == 0
    assert len(collector.logs()) == 1


def test_export_before_anything_is_spooled_succeeds(collector):
    telemetry_conf(f"EXPORT=on\nENDPOINT={collector.endpoint}\n")
    spool().parent.rmdir()
    run = run_fleet("telemetry", "export")
    assert run.code == 0, run.out
    assert collector.logs() == []
