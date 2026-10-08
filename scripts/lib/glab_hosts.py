"""Which GitLab instances this machine has a credential for, ONE HOST AT A TIME.

Loaded by path, never run. The counterpart to gh_accounts.py one forge over,
and it exists for the same reason: the CLI's own status command is
all-or-nothing, so the question fleet actually has — "is there a credential
for the instance this work lives on" — cannot be read off its exit code.

WHY THIS EXISTS. `glab auth status` exits non-zero when ANY configured instance
fails. An operator authenticated to their company's GitLab, where their
repositories live, and not to gitlab.com, which they have never used, was
therefore told by preflight that `glab auth` was missing — a remedy they had
already run. That contradicts the forge seam, where a SELF-HOSTED INSTANCE IS
THE ORDINARY CASE rather than a special one; forge.py's header owns that.

Verified against glab 1.117.0, which is where these shapes come from:
`--hostname <host>` answers for one instance and exits 0/1 for it alone, and
`--all` lists every configured instance at column 0.

IT READS AND NEVER WRITES. `glab auth login` is interactive and the operator's.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile

# Hosts are the only lines `--all` puts at column 0; the per-instance findings,
# and the error banner when one of them failed, are indented. The `.` keeps a
# stray unindented word out: an instance is a hostname.
HOST_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9._-]*)\s*$")


def hosts() -> list[str]:
    """Every GitLab instance `glab` has configured.

    EMPTY, and not a failure, when the list cannot be read — an older `glab`
    with no `--all`, or none installed. A caller that gets nothing asks the
    bare `glab auth status`, which is what every caller did before it asked
    per host.
    """
    if not shutil.which("glab"):
        return []
    try:
        done = subprocess.run(
            ["glab", "auth", "status", "--all"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
        )
    except OSError:
        return []
    return [m.group(1) for line in done.stdout.splitlines() if (m := HOST_LINE.match(line))]


def default_host() -> str:
    """The instance glab talks to OUTSIDE a repository, by glab's own lookup.

    `glab config get host` reads the environment (`GITLAB_HOST`), then the
    config, and prints nothing when neither sets it — and then glab uses
    gitlab.com. Asked from a directory that is no repository, so a checkout's
    own remote does not answer for it.
    """
    if not shutil.which("glab"):
        return ""
    try:
        with tempfile.TemporaryDirectory() as nowhere:
            done = subprocess.run(["glab", "config", "get", "host"], cwd=nowhere, stdin=subprocess.DEVNULL,
                                  capture_output=True, encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = done.stdout.split() if done.returncode == 0 else []
    return lines[0] if lines else "gitlab.com"


def host_ok(host: str) -> bool:
    """Whether that ONE instance authenticates."""
    if not shutil.which("glab"):
        return False
    try:
        return subprocess.run(["glab", "auth", "status", "--hostname", host], capture_output=True).returncode == 0
    except OSError:
        return False
