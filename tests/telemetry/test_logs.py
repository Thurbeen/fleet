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
