"""Every `gh` login this machine holds, not just the active one.

Loaded by path, never run. Every caller that asks GitHub who the operator is
shares it and wants the same three things — the account list, one account's
token, one `gh api` call as that account:

  sync_registry.py    what the repo map is built from
  discover_owners.py  the one question onboarding asks the operator
  add_owner.py        what a newly authenticated login reaches
  preflight.py        the `gh auth` row, which is decided per ACCOUNT

WHY THIS EXISTS. `gh api user/repos` and `gh api user/orgs` answer for
whichever account is ACTIVE. A machine with a personal login and an employer's
reaches two disjoint sets, so asking once described half the machine — and in
the map's case the symptom was the `no accessible repos` warning a mistyped
owner produces, which made the two indistinguishable.

NOTHING HERE SWITCHES THE ACTIVE ACCOUNT. Tokens are read BY NAME with
`gh auth token --user`, which hands one over without touching which account
`gh` is pointing at — and `gh` is a tool the operator uses for everything else,
so a sync that left it pointing somewhere new would be a side effect on their
shell, not a read. `tests/test_accounts.py` holds all of it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys


def accounts(host: str) -> list[str]:
    """The login of every account `gh` holds for `host` whose credential still works.

    In `gh`'s own order, which puts the active account first.

    EMPTY, and not a failure, when the list cannot be read: an older `gh` has
    no `auth status --json`, and a `GH_TOKEN`/`GITHUB_TOKEN` already in the
    environment overrides every stored account anyway, so enumerating them
    would describe repos the caller cannot reach. A caller that gets nothing
    asks the ACTIVE session, which is what every caller did before it asked
    more than one.

    An account whose state is not `success` is named on stderr and skipped:
    one expired login costs its own repos and never the caller's whole answer.
    """
    if not shutil.which("gh") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        return []
    try:
        done = subprocess.run(
            ["gh", "auth", "status", "--json", "hosts"],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        listing = json.loads(done.stdout)["hosts"].get(host) or [] if done.returncode == 0 else []
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []

    logins = []
    for entry in listing if isinstance(listing, list) else []:
        if not isinstance(entry, dict) or not entry.get("login"):
            continue
        if entry.get("state") == "success":
            logins.append(entry["login"])
        else:
            sys.stderr.write(f"warning: gh account '{entry['login']}' on {host} is {entry.get('state')} — skipped\n")
    return logins


def account_token(host: str, login: str) -> str:
    """That account's token, or "". Reading it is not switching to it."""
    try:
        done = subprocess.run(
            ["gh", "auth", "token", "--hostname", host, "--user", login],
            capture_output=True, encoding="utf-8", errors="replace",
        )
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def api_as(token: str, *args: str, **run) -> subprocess.CompletedProcess:
    """One `gh api` call as one account. An EMPTY token means the active session.

    The token goes into that one child's environment and nowhere else, so the
    last account asked is never still in force for whatever runs next.
    """
    env = dict(run.pop("env", None) or os.environ)
    if token:
        env["GH_TOKEN"] = token
    run.setdefault("capture_output", True)
    run.setdefault("encoding", "utf-8")
    run.setdefault("errors", "replace")
    return subprocess.run(["gh", "api", *args], env=env, **run)
