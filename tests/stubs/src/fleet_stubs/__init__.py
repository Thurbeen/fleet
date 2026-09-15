"""What every stub shares: where its answers live, and the log of how it was called.

Every stub reads its answers from files under FLEET_STUB_ROOT, which the test
harness writes, and appends `<tool> <args>` to `calls.log` there before doing
anything else — so a test can assert on exactly what fleet asked for, including
a session that would have been deleted and was not.

A test that needs a tool to answer in a way no canned stub does writes
`scripts/<tool>.py` there (`Stubs.tool`), and that script answers instead,
with the tool's own argv. It is how one test holds five `gh` logins and the
next holds none, without a stub package per case.
"""

import os
import runpy
import sys
from pathlib import Path


def root() -> Path:
    value = os.environ.get("FLEET_STUB_ROOT")
    if not value:
        sys.stderr.write("fleet stub: FLEET_STUB_ROOT is not set\n")
        raise SystemExit(2)
    return Path(value)


def called(tool: str) -> Path:
    """Log this call and return the stub root, or run the test's script for this tool and exit."""
    where = root()
    with open(where / "calls.log", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(" ".join([tool, *sys.argv[1:]]) + "\n")
    script = where / "scripts" / f"{tool}.py"
    if script.is_file():
        runpy.run_path(str(script), run_name="__main__")
        raise SystemExit(0)
    return where


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
