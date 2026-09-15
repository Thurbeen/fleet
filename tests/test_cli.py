"""The `fleet` command, the one entry point on Linux and native Windows.

Two claims, both easy to lose without anyone noticing:

- `fleet` names every group it has, and an unknown one is a usage error.
- What `fleet` writes is UTF-8 whatever the console's code page is. A Windows
  console defaults to cp1252, where `—` comes out as a different byte and `🚀`
  raises before the line is written at all.

Run it through uv, which is what puts the `fleet` console script on PATH:

    uv run pytest tests/test_cli.py

It reads no operator state. Every FLEET_* root points into a throwaway
directory, as tests/harness.py's `isolated_env` does for pytest, and the
forge and thurbox variables are dropped.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLEET = shutil.which("fleet")

# Outside cp1252 on purpose, so it proves both halves on a Windows console: the
# record `topic add` writes holds it, and the line `list` prints encodes it.
TITLE = "Ship it — now 🚀"

DROPPED = ("GH_", "GITHUB_", "GITLAB_", "GLAB_", "FLEET_", "THURBOX_", "PYTHONUTF8", "PYTHONIOENCODING")


class FleetTestCase(unittest.TestCase):
    """A throwaway queue holding one topic whose title no cp1252 console can print."""

    def setUp(self) -> None:
        self.assertIsNotNone(FLEET, "no `fleet` on PATH: run this through `uv run`")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

        settings = self.tmp / "settings"
        (settings / "orchestration").mkdir(parents=True)
        for conf in (ROOT / "orchestration").glob("*.example.conf"):
            shutil.copy(conf, settings / "orchestration")

        env = {k: v for k, v in os.environ.items() if not k.startswith(DROPPED)}
        env.update(
            FLEET_QUEUE_DIR=str(self.tmp / "queue"),
            FLEET_RUNS_DIR=str(self.tmp / "runs"),
            FLEET_RECONCILE_DIR=str(self.tmp / "reconcile"),
            FLEET_REGISTRY_FILE=str(self.tmp / "registry" / "repos.generated.yaml"),
            FLEET_AUTO_MERGE_ROOT=str(settings),
            FLEET_PUBLISH_ROOT=str(settings),
            FLEET_AGENT_ROOT=str(settings),
            FLEET_GLYPH_ROOT=str(settings),
            FLEET_VOICE_CONF=str(settings / "orchestration" / "voice.example.conf"),
        )
        for d in ("queue", "runs", "reconcile"):
            (self.tmp / d).mkdir()
        self.env = env

        made = self.fleet("queue", "topic", "add", "probe", "--title", TITLE, "--prompt", "the prompt")
        self.assertEqual(made.returncode, 0, made.stderr.decode("utf-8", "replace"))

    def run_argv(self, argv: list, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(argv, cwd=ROOT, env={**self.env, **env}, capture_output=True, timeout=120)

    def fleet(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return self.run_argv([FLEET, *args], **env)


class Utf8OutputTest(FleetTestCase):
    """What `fleet` writes survives a cp1252 console, on stdout and on stderr."""

    CP1252 = {"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}

    def test_stdout_keeps_a_dash_as_utf8(self) -> None:
        out = self.fleet("queue", "plan", **self.CP1252)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("—", out.stdout.decode("utf-8"))

    def test_stdout_prints_what_cp1252_cannot_encode(self) -> None:
        # A queue directory `root` prints and no command here writes into a file.
        rocket = self.tmp / "queue-🚀"
        rocket.mkdir()
        out = self.fleet("queue", "root", FLEET_QUEUE_DIR=str(rocket), **self.CP1252)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(rocket.name, out.stdout.decode("utf-8"))

    def test_a_record_read_back_prints_as_utf8(self) -> None:
        out = self.fleet("queue", "list", **self.CP1252)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(TITLE, out.stdout.decode("utf-8"))

    def test_stderr_is_utf8_too(self) -> None:
        out = self.fleet("queue", "show", "nope/🚀", **self.CP1252)
        self.assertEqual(out.returncode, 2)
        self.assertIn("nope/🚀", out.stderr.decode("utf-8"))


class EntryPointTest(unittest.TestCase):
    """`fleet` names its groups, and an unknown one is a usage error."""

    def test_help_names_every_group(self) -> None:
        self.assertIsNotNone(FLEET, "no `fleet` on PATH: run this through `uv run`")
        out = subprocess.run([FLEET, "--help"], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0)
        for group in ("queue", "status"):
            self.assertIn(group, out.stdout)

    def test_unknown_group_is_a_usage_error(self) -> None:
        self.assertIsNotNone(FLEET, "no `fleet` on PATH: run this through `uv run`")
        out = subprocess.run([FLEET, "no-such-group"], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 2)
        self.assertIn("no-such-group", out.stderr)


if __name__ == "__main__":
    unittest.main()
