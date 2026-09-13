"""`quota-axi`: the account's quota window, from `quota.json`.

No file is the tool failing the way an expired credential fails.
"""

import sys

from fleet_stubs import called, read


def main() -> int:
    root = called("quota-axi")
    answer = read(root / "quota.json")
    if not answer:
        sys.stderr.write("quota-axi: no credentials for provider claude\n")
        return 1
    sys.stdout.write(answer)
    return 0
