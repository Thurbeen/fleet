"""Read real queue records through the probe, then drive the board offline."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from harness import PYTHON, REPO, run, write
from panekit import REAL_LUA


def test_board_probe_keeps_navigation_and_attention_fields(tmp_path):
    root = Path(os.environ["FLEET_QUEUE_DIR"])
    write(root / "sample" / "topic.yaml", "title: Sample\n")
    doc = {
        "id": "01-work", "state": "done", "title": "Audit export", "agent": "codex",
        "host": "remote", "session": "worker-one", "review": "https://example.org/review",
        "artifact": "https://github.com/example/project/pull/2",
        "publish": {"method": "pr", "state": "checks-failed", "detail": "failed checks: unit"},
    }
    write(root / "sample" / "01-work" / "task.yaml", yaml.safe_dump(doc))
    before = (root / "sample" / "01-work" / "task.yaml").read_bytes()
    out = run([*PYTHON, str(REPO / "scripts/lib/pane_probe.py")])
    assert out.code == 0, out.out
    rows = [line.split("\t") for line in out.stdout.splitlines()]
    extra = next((r for r in rows if r[0] == "B"), None)
    assert extra, "the board cannot navigate: probe drops session, agent, host and review"
    assert extra[1:6] == ["sample/01-work", "codex", "remote", "worker-one", "https://example.org/review"]
    assert "failed checks: unit" in extra
    assert any(r[0] == "H" for r in rows), "no reconciler health / refresh reading"
    assert (root / "sample" / "01-work" / "task.yaml").read_bytes() == before


@pytest.mark.skipif(not REAL_LUA, reason="requires lua")
@pytest.mark.parametrize("scenario", [
    "columns", "enter-session", "enter-detail", "links", "filters",
    "landed", "mouse", "selection", "fuel", "narrow", "large", "failure", "fuel-unavailable", "buttons", "missing-session", "reserved", "detail-links",
])
def test_board_interactions(scenario):
    done = run([REAL_LUA, "tests/pane/board_harness.lua", scenario], cwd=REPO)
    assert done.code == 0, done.out


def test_bad_reconciler_settings_do_not_hide_the_queue():
    root = Path(os.environ["FLEET_QUEUE_DIR"])
    write(root / "sample" / "topic.yaml", "title: Sample\n")
    write(root / "sample" / "01-work" / "task.yaml", "id: 01-work\nstate: queued\ntitle: Work\n")
    out = run([*PYTHON, str(REPO / "scripts/lib/pane_probe.py")], FLEET_RECONCILE_QUEUE_CMD="[broken")
    assert out.code == 0, out.out
    assert "K\t01-work\tqueued" in out.stdout
    assert "H\tunknown\t" in out.stdout
