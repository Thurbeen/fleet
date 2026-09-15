"""A real tool's name behind the stubs on a hostile PATH: running it at all is the defect.

`tripwires()` in tests/harness.py copies this launcher under each tool's name.
It records the call in the file TRIPWIRE_LOG names, a variable `isolated_env`
does not drop, so a call that slipped past every stub is logged wherever it
ran, and exits 97.
"""

import os
import sys


def main() -> int:
    tool = os.path.basename(sys.argv[0])
    if tool.lower().endswith(".exe"):
        tool = tool[:-4]
    log = os.environ.get("TRIPWIRE_LOG")
    if log:
        with open(log, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(" ".join([tool, *sys.argv[1:]]) + "\n")
    return 97
