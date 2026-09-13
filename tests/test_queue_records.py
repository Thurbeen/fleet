"""The queue's records and directories, through the platform seam, on this OS.

    uv run python -m unittest discover -s tests
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fleet.cli import LIB, load

queue = load("queue.py")
QUEUE = os.path.join(LIB, "queue.py")
WINDOWS = os.name == "nt"


class QueueRecords(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        settings = self.tmp / "settings"
        settings.mkdir()
        self.env = {
            k: v for k, v in os.environ.items() if not k.startswith(("FLEET_", "THURBOX_"))
        }
        self.env.update(
            FLEET_QUEUE_DIR=str(self.tmp / "queue"),
            FLEET_RUNS_DIR=str(self.tmp / "runs"),
            FLEET_PUBLISH_ROOT=str(settings),
            FLEET_AGENT_ROOT=str(settings),
            FLEET_GLYPH_ROOT=str(settings),
            PYTHONUTF8="1",
        )

    def queue(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, QUEUE, *args],
            capture_output=True, text=True, env=self.env, timeout=120, cwd=self.tmp,
        )

    def topic_with_a_task(self) -> str:
        out = self.queue("topic", "add", "line-endings", "--title", "Line endings",
                         "--prompt", "first line\nsecond line")
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.queue("add", out.stdout.strip(), "lf-on-disk", "--title", "LF on disk",
                         "--repo", str(self.tmp / "repo"), "--branch", "fix/lf-on-disk")
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def written(self) -> list[Path]:
        return [p for d in ("queue", "runs") for p in (self.tmp / d).rglob("*") if p.is_file()]

    def test_records_are_written_with_lf(self):
        self.topic_with_a_task()
        names = {p.name for p in self.written()}
        self.assertLessEqual({"topic.yaml", "PROMPT.md", "task.yaml", "BRIEF.md"}, names)
        crlf = [str(p.relative_to(self.tmp)) for p in self.written() if b"\r\n" in p.read_bytes()]
        self.assertEqual(crlf, [])

    def test_records_written_with_crlf_still_read(self):
        ref = self.topic_with_a_task()
        for path in self.written():
            data = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(data.replace(b"\n", b"\r\n"))

        for verb in (["list"], ["show", ref], ["check"]):
            out = self.queue(*verb)
            self.assertEqual(out.returncode, 0, f"{verb}: {out.stderr}")
        self.assertIn("LF on disk", self.queue("show", ref).stdout)

    def test_a_crlf_result_parses(self):
        meta, body = queue.parse_result(
            "---\r\noutcome: shipped\r\nartifact: https://example.com/pr/1\r\n---\r\nDone.\r\n"
        )
        self.assertEqual(meta, {"outcome": "shipped", "artifact": "https://example.com/pr/1"})
        self.assertEqual(body, "Done.")


class QueueDirectories(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        patches = [
            mock.patch.dict(os.environ),
            mock.patch.object(queue, "thurbox_config", return_value={}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        for name in ("THURBOX_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
            os.environ.pop(name, None)

    def test_hosts_toml_fallback_follows_thurbox_config_dir(self):
        os.environ["XDG_CONFIG_HOME"] = self.tmp
        self.assertEqual(queue.hosts_file(), os.path.join(self.tmp, "thurbox", "hosts.toml"))

    @unittest.skipUnless(WINDOWS, "the %APPDATA% branch")
    def test_hosts_toml_fallback_is_under_appdata_on_windows(self):
        os.environ["APPDATA"] = self.tmp
        self.assertEqual(queue.hosts_file(), os.path.join(self.tmp, "thurbox", "hosts.toml"))

    def test_fixer_worktrees_live_in_fleet_data_dir(self):
        if WINDOWS:
            os.environ["LOCALAPPDATA"] = self.tmp
            expected = os.path.join(self.tmp, "fleet", "worktrees")
        else:
            os.environ["HOME"] = self.tmp
            expected = os.path.join(self.tmp, ".local", "share", "fleet", "worktrees")
        self.assertEqual(queue.fixer_worktrees_root(), expected)


if __name__ == "__main__":
    unittest.main()
