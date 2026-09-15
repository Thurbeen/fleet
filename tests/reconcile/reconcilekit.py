"""What every reconciler test shares: the running loop's surroundings, and patience.

The loop is a real detached process over a throwaway queue and runtime
directory, driving `queue_stub.py` instead of the queue and the stub
`thurbox-cli` instead of a lead. Cadences are compressed to seconds so a day's
clock fits in a test; the ORDER is what is tested, not the real numbers.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from harness import Run, run_fleet, write

STUB = Path(__file__).resolve().parent / "queue_stub.py"
LEAD = "Gate Control"


def wait_for(predicate, secs: float = 20) -> bool:
    """Wait for a condition rather than sleeping a guessed amount: a slow
    machine makes a test slower and never makes it flaky."""
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


@dataclass
class Recon:
    root: Path

    @property
    def rt(self) -> Path:
        return Path(os.environ["FLEET_RECONCILE_DIR"])

    @property
    def queue(self) -> Path:
        return Path(os.environ["FLEET_QUEUE_DIR"])

    def __call__(self, *args: str, **env: str | None) -> Run:
        return run_fleet("reconcile", *args, **env)

    def calls(self, verb: str | None = None) -> list[str]:
        try:
            lines = (self.root / "calls").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [line for line in lines if verb is None or line.split(" ")[0] == verb]

    def count(self, verb: str) -> int:
        return len(self.calls(verb))

    def log(self) -> str:
        try:
            return (self.rt / "reconcile.log").read_text(encoding="utf-8")
        except OSError:
            return ""

    def moved(self, n: int) -> None:
        write(self.root / "moved", f"{n}\n")

    def ready(self, *refs: str) -> None:
        write(self.root / "ready", "".join(r + "\n" for r in refs))

    def lead(self, state: str | None) -> None:
        """The lead's state, or None for no lead session at all."""
        record = self.root / "stubs" / "sessions" / "lead-uuid.json"
        if state is None:
            record.unlink(missing_ok=True)
        else:
            write(record, json.dumps({"id": "lead-uuid", "name": LEAD, "state": state}) + "\n")

    def pid(self) -> str:
        try:
            return (self.rt / "pid").read_text(encoding="utf-8").strip()
        except OSError:
            return ""


def queue_cmd(*argv: str) -> str:
    """FLEET_RECONCILE_QUEUE_CMD as a JSON argv, which survives a Windows path."""
    return json.dumps(list(argv))
