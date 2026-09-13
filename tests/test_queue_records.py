"""The queue's records and directories, through the platform seam, on this OS."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from conftest import LIB

WINDOWS = os.name == "nt"


def _queue(tmp_path, *args):
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("FLEET_", "THURBOX_"))
    }
    settings = tmp_path / "settings"
    settings.mkdir(exist_ok=True)
    env.update(
        FLEET_QUEUE_DIR=str(tmp_path / "queue"),
        FLEET_RUNS_DIR=str(tmp_path / "runs"),
        FLEET_PUBLISH_ROOT=str(settings),
        FLEET_AGENT_ROOT=str(settings),
        FLEET_GLYPH_ROOT=str(settings),
        PYTHONUTF8="1",
    )
    return subprocess.run(
        [sys.executable, str(LIB / "queue.py"), *args],
        capture_output=True, text=True, env=env, timeout=120, cwd=tmp_path,
    )


def _topic_with_a_task(tmp_path) -> str:
    out = _queue(
        tmp_path, "topic", "add", "line-endings", "--title", "Line endings",
        "--prompt", "first line\nsecond line",
    )
    assert out.returncode == 0, out.stderr
    topic = out.stdout.strip()
    out = _queue(
        tmp_path, "add", topic, "lf-on-disk", "--title", "LF on disk",
        "--repo", str(tmp_path / "repo"), "--branch", "fix/lf-on-disk",
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _written(tmp_path):
    return [
        p for d in ("queue", "runs") for p in (tmp_path / d).rglob("*") if p.is_file()
    ]


def test_records_are_written_with_lf(tmp_path):
    _topic_with_a_task(tmp_path)
    names = {p.name for p in _written(tmp_path)}
    assert {"topic.yaml", "PROMPT.md", "task.yaml", "BRIEF.md"} <= names
    crlf = [str(p.relative_to(tmp_path)) for p in _written(tmp_path) if b"\r\n" in p.read_bytes()]
    assert crlf == []


def test_records_written_with_crlf_still_read(tmp_path):
    ref = _topic_with_a_task(tmp_path)
    for path in _written(tmp_path):
        data = path.read_bytes().replace(b"\r\n", b"\n")
        path.write_bytes(data.replace(b"\n", b"\r\n"))

    for verb in (["list"], ["show", ref], ["check"]):
        out = _queue(tmp_path, *verb)
        assert out.returncode == 0, f"{verb}: {out.stderr}"
    assert "LF on disk" in _queue(tmp_path, "show", ref).stdout


def test_a_crlf_result_parses(queue_mod):
    meta, body = queue_mod.parse_result(
        "---\r\noutcome: shipped\r\nartifact: https://example.com/pr/1\r\n---\r\nDone.\r\n"
    )
    assert meta == {"outcome": "shipped", "artifact": "https://example.com/pr/1"}
    assert body == "Done."


def test_hosts_toml_fallback_follows_thurbox_config_dir(queue_mod, monkeypatch, tmp_path):
    monkeypatch.setattr(queue_mod, "thurbox_config", lambda: {})
    monkeypatch.delenv("THURBOX_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert queue_mod.hosts_file() == os.path.join(str(tmp_path), "thurbox", "hosts.toml")


@pytest.mark.skipif(not WINDOWS, reason="the %APPDATA% branch")
def test_hosts_toml_fallback_is_under_appdata_on_windows(queue_mod, monkeypatch, tmp_path):
    monkeypatch.setattr(queue_mod, "thurbox_config", lambda: {})
    monkeypatch.delenv("THURBOX_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert queue_mod.hosts_file() == os.path.join(str(tmp_path), "thurbox", "hosts.toml")


def test_fixer_worktrees_live_in_fleet_data_dir(queue_mod, monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    if WINDOWS:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        expected = os.path.join(str(tmp_path), "fleet", "worktrees")
    else:
        monkeypatch.setenv("HOME", str(tmp_path))
        expected = os.path.join(str(tmp_path), ".local", "share", "fleet", "worktrees")
    assert queue_mod.fixer_worktrees_root() == expected
