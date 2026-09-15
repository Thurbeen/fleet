"""Any tool at all: a copy of this launcher answers as whatever it is named.

`Stubs.tool` copies the `fleet-stub` executable to `<tool>` (`<tool>.exe` on
Windows, where uv's launcher carries its entry point inside itself, so a
renamed copy still runs this) and writes the answer that name gives.
"""

import os
import sys

from fleet_stubs import called


def main() -> int:
    tool = os.path.basename(sys.argv[0])
    if tool.lower().endswith(".exe"):
        tool = tool[:-4]
    called(tool)
    sys.stderr.write(f"fleet stub: nothing scripted for {tool}\n")
    return 127
