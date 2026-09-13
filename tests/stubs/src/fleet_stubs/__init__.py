"""What every stub shares: where its answers live, and the log of how it was called.

Every stub reads its answers from files under FLEET_STUB_ROOT, which the test
harness writes, and appends `<tool> <args>` to `calls.log` there before doing
anything else — so a test can assert on exactly what fleet asked for, including
a session that would have been deleted and was not.
"""

import os
import sys
from pathlib import Path


def root() -> Path:
    value = os.environ.get("FLEET_STUB_ROOT")
    if not value:
        sys.stderr.write("fleet stub: FLEET_STUB_ROOT is not set\n")
        raise SystemExit(2)
    return Path(value)


def called(tool: str) -> Path:
    """Log this call and return the stub root."""
    where = root()
    with open(where / "calls.log", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(" ".join([tool, *sys.argv[1:]]) + "\n")
    return where


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
