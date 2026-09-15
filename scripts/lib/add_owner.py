"""What this machine can reach that the map does not cover yet, and the one command that fixes it.

    uv run fleet add-owner                  what is new, and nothing is written
    uv run fleet add-owner <owner> ...      add those, then sync, then say what moved
    uv run fleet add-owner --all            add every owner above that is new

WHY THIS EXISTS. Onboarding is a FIRST RUN, but what actually happens later is
not a re-run: the operator gains an owner, a repository or a whole `gh`
account, and the map has to catch up. That meant hand-editing
registry/owners.txt and remembering what to run afterwards, and nothing told
them what a newly authenticated account even reaches. So the report groups
owners BY THE ACCOUNT THAT REACHES THEM, which is the shape of the question
after a `gh auth login`.

IT LOGS NOBODY IN. `gh auth login` and `glab auth login` are interactive and the
operator's; this reads what they have already done, offers, and syncs.

GITLAB IS EVIDENCE, NEVER AN OWNER. An authenticated GitLab host changes what
preflight reports and what a task can target through scripts/lib/forge.py, and
contributes nothing to registry/owners.txt, which is a list of GITHUB owners
read by `gh`. It is reported so the operator knows what it did and did not change.

THE FILE IS THE OPERATOR'S. Its comment header documents the format, and its
ORDER is the order the map is emitted in, so a new owner is APPENDED and
nothing is ever reshuffled. EVERY name is checked before ANY is written.

Options:
  --all         add every owner the report marks new

Exit: 0 when the report was printed or the owners were added, 1 when there was
nothing it could do (no owners file, no account that could be read, a
duplicate, a name that is not a GitHub owner, a failed sync), 2 on a usage error.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys

LIB = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(LIB))
OWNERS_FILE = os.path.join(REPO_ROOT, "registry", "owners.txt")
MAP = os.path.join(REPO_ROOT, "registry", "repos.generated.yaml")

# At most this many names, then a count. A hundred repositories arriving with a
# new employer is a line the operator scrolls past, not one they read.
SHOW = 8


def _load_sibling(name: str, filename: str):
    """A module beside this file, keyed in sys.modules as every loader keys it."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(LIB, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gh = _load_sibling("fleet_gh_accounts", "gh_accounts.py")
glab = _load_sibling("fleet_glab_hosts", "glab_hosts.py")


def fail(message: str) -> int:
    sys.stderr.write(f"error: {message}\n")
    return 1


def list_some(items: list[str]) -> str:
    shown = ", ".join(items[:SHOW])
    return shown + (f", +{len(items) - SHOW} more" if len(items) > SHOW else "")


def reach() -> list[tuple[str, list[str]]]:
    """(login, [login, org, ...]) for every account that could say who it is.

    `shutil.which("gh")` AND NOTHING MORE: `gh auth status` exits 1 when an
    account on ANY host has trouble, so gating on it throws away every healthy
    login the moment one has lapsed.
    """
    if not shutil.which("gh"):
        return []

    def ask(token: str) -> tuple[str, list[str]] | None:
        who = gh.api_as(token, "user", "--jq", ".login")
        login = who.stdout.strip() if who.returncode == 0 else ""
        if not login:
            return None
        orgs = gh.api_as(token, "--paginate", "user/orgs", "--jq", ".[].login")
        return login, [login, *(orgs.stdout.split() if orgs.returncode == 0 else [])]

    host = os.environ.get("GH_HOST") or "github.com"
    accounts = gh.accounts(host)
    if not accounts:
        # An EMPTY token is the active session, the fallback the seam documents.
        one = ask("")
        return [one] if one else []

    found = []
    for login in accounts:
        token = gh.account_token(host, login)
        if not token:
            sys.stderr.write(f"warning: no usable token for gh account '{login}' — what it reaches is not below\n")
            continue
        one = ask(token)
        if one:
            found.append(one)
        else:
            sys.stderr.write(f"warning: gh account '{login}' could not say who it is — what it reaches is not below\n")
    return found


def map_pairs() -> list[str]:
    """`owner/repo` for every repository in the map, in the map's own order."""
    try:
        with open(MAP, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    pairs, owner = [], ""
    for line in lines:
        if m := re.match(r"^  - name: (\S+)", line):
            owner = m.group(1)
        elif (m := re.match(r"^    - name: (\S+)", line)) and owner:
            pairs.append(f"{owner}/{m.group(1)}")
    return pairs


def map_totals() -> str:
    """The two numbers under `totals:`, as one phrase. Four-space indents belong
    to a repository entry, so the two-space match reaches only the totals."""
    try:
        with open(MAP, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return "no map"
    repos = re.findall(r"^  repos: (.*)$", text, re.MULTILINE)
    owners = re.findall(r"^  owners: (.*)$", text, re.MULTILINE)
    return f"{repos[-1] if repos else '?'} repos across {owners[-1] if owners else '?'} owners"


def no_accounts() -> str:
    return (
        "No gh account answered, so there is nothing to compare your map against.\n\n"
        "  gh auth login          then run this again\n"
        "  uv run fleet preflight reads the same accounts and says which are broken\n"
    )


NOTHING_NEW = "Nothing new: every owner these accounts reach is already in registry/owners.txt.\n"


def report(accounts: list[tuple[str, list[str]]], configured: set[str], new: list[str]) -> None:
    out = sys.stdout.write
    if not accounts:
        out(no_accounts())
        return

    # As wide as the widest login: one that overflows a fixed column takes the
    # alignment of every row with it.
    width = max([8, *(len(login) for login, _ in accounts)])
    out("ACCOUNTS gh HOLDS — and the GitHub owners each one reaches\n\n")
    for login, owners in accounts:
        marked = ", ".join(("* " if o in configured else "+ ") + o for o in owners)
        fresh = sum(1 for o in owners if o not in configured)
        out(f"  {login:<{width}}  {marked}" + (f"   ({fresh} new)\n" if fresh else "\n"))
    out("\n  * already in registry/owners.txt      + not in it yet\n")

    hosts = [h for h in glab.hosts() if glab.host_ok(h)]
    if hosts:
        out("\nGITLAB — evidence, never an owner\n\n")
        out(f"  authenticated: {', '.join(hosts)}\n\n")
        out("  That changes what uv run fleet preflight reports and what a task can target\n")
        out("  through the forge seam in scripts/lib/forge.py. It adds no owner here:\n")
        out("  registry/owners.txt is a list of GITHUB owners, read by gh.\n")

    out("\n")
    if not new:
        out(NOTHING_NEW)
        return
    out(f"{len(new)} owner{'' if len(new) == 1 else 's'} not in registry/owners.txt: {list_some(new)}\n\n")
    out("  uv run fleet add-owner --all       add every one of them, then sync\n")
    out(f"  uv run fleet add-owner {new[0]:<10}  or name the ones you want\n")


def main(argv: list[str]) -> int:
    add_all, wanted = False, []
    for arg in argv:
        if arg == "--all":
            add_all = True
        elif arg in ("-h", "--help"):
            sys.stdout.write(__doc__)
            return 0
        elif arg.startswith("-"):
            sys.stderr.write("usage: fleet add-owner [--all] [owner ...]\n")
            return 2
        else:
            wanted.append(arg)

    # A clone with no owners file has not been onboarded, and writing one from
    # here would be a first run done badly: which owners the map should cover is
    # a question somebody has to be asked.
    if not os.path.isfile(OWNERS_FILE):
        message = (f"no {OWNERS_FILE} yet — this is the path for a map that already exists.\n"
                   "       A first run belongs to the fleet-onboarding skill, which asks:\n"
                   "         uv run fleet discover-owners\n"
                   "         cp registry/owners.example.txt registry/owners.txt")
        if add_all or wanted:
            return fail(message)
        # Only a report was asked for, and no map is a fleet that works: a local-only one.
        sys.stdout.write(f"note: {message}\n       The repo map is optional; a local-only fleet has none.\n")
        return 0

    sync_registry = _load_sibling("fleet_sync_registry", "sync_registry.py")
    configured = set(sync_registry.read_owners(OWNERS_FILE))

    accounts = reach()
    new: list[str] = []
    for _, owners in accounts:
        new.extend(o for o in owners if o not in configured and o not in new)

    if not add_all and not wanted:
        report(accounts, configured, new)
        return 0

    if add_all:
        # An empty answer is not agreement: `--all` may not call the map complete.
        if not accounts:
            sys.stderr.write(no_accounts())
            return 1
        if not new:
            sys.stdout.write(NOTHING_NEW)
            return 0
        wanted.extend(new)

    asked: set[str] = set()
    for owner in wanted:
        # Named twice is the same duplicate as one already in the file.
        if owner in asked:
            return fail(f"'{owner}' was named twice. Nothing was written.")
        asked.add(owner)
        # What a GitHub owner is, and nothing wider: a GitLab group path, a
        # host-qualified name or a URL would warn `no accessible repos` forever.
        if not re.fullmatch(r"[A-Za-z0-9-]+", owner):
            return fail(f"'{owner}' is not a GitHub owner — it holds usernames and orgs, one per line.\n"
                        "       A GitLab group is not one: a task targets a GitLab repository by host\n"
                        "       and path, through the seam in scripts/lib/forge.py.")
        if owner in configured:
            return fail(f"'{owner}' is already in registry/owners.txt. Nothing was written.")

    # Appended, never inserted or sorted. A last line with no newline would
    # otherwise get the first new owner glued onto it.
    with open(OWNERS_FILE, "rb") as fh:
        existing = fh.read()
    with open(OWNERS_FILE, "a", encoding="utf-8", newline="\n") as fh:
        if existing and not existing.endswith(b"\n"):
            fh.write("\n")
        fh.write("".join(f"{o}\n" for o in wanted))
    sys.stdout.write(f"added to registry/owners.txt: {list_some(wanted)}\n")

    had_map = os.path.isfile(MAP)
    before_pairs, before_totals = map_pairs(), map_totals()

    sys.stdout.write("\n")
    sys.stdout.flush()
    if sync_registry.main([]) != 0:
        return fail("the sync failed — registry/owners.txt keeps the owners just added,\n"
                    "       so re-running uv run fleet sync-registry is all that is left to do.")

    after_pairs = map_pairs()
    gained = [p for p in after_pairs if p not in set(before_pairs)]
    lost = [p for p in before_pairs if p not in set(after_pairs)]

    out = sys.stdout.write
    out("\nMAP CHANGED\n")
    out(f"  owners added        {list_some(wanted)}\n")
    if gained:
        out(f"  repositories gained {list_some(gained)}\n")
    if lost:
        out(f"  repositories lost   {list_some(lost)}\n")
    if not gained and not lost:
        out("  repositories        none gained, none lost\n")
    if had_map:
        out(f"  totals              {map_totals()} (was {before_totals})\n")
    else:
        out(f"  totals              {map_totals()} (the map did not exist before this)\n")
    out("\nA repository that is in the map and still unexplained belongs in\n"
        "registry/context/<repo>.md, which is where the judgement about a project lives.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
