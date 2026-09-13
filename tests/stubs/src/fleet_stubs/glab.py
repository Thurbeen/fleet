"""`glab`: a machine with no GitLab configuration at all.

The GitLab adapter asks `glab auth status` which instances this machine holds
the moment the forge registry is built, in nearly every queue command. On the
operator's own laptop the real `glab` would answer, so this stands in for the
machine every test not about GitLab is written for.
"""

import sys

from fleet_stubs import called


def main() -> int:
    called("glab")
    sys.stderr.write("glab: no GitLab instance is configured on this machine\n")
    return 1
