"""Who this operator's repos belong to, guessed from what is already on the
machine — so onboarding asks one question instead of asking for a list the
operator has written down nowhere.

registry/owners.txt is the one input the repo map genuinely needs, and every
part of it is already recorded: in the `gh` logins, in the git configuration,
and in the remotes of the clones the operator works in. This reads all three
and prints candidates with the EVIDENCE for each, because a guess an operator
cannot check is one they have to verify by hand anyway.

  gh account     `gh api user` — one per `gh` login, not just the active one
  gh org         `gh api user/orgs` — the orgs each of those logins can see
  git config     `github.user`, and a @users.noreply.github.com commit email
  local clones   the `origin` of every git checkout under the roots scanned

IT WRITES NOTHING. registry/owners.txt is the operator's file and stays theirs.

GITHUB OWNERS ONLY, because that is what the file holds — the map is built
from `gh`. GitLab clones found on the way are reported in their own section and
belong in no owners file: a task targets a GitLab repo by path, through the
forge seam in scripts/lib/forge.py.

Usage:
  uv run fleet discover-owners                 # candidates with their evidence
  uv run fleet discover-owners ~/work ~/oss    # scan these roots instead

Exit: 0 when at least one candidate was found, 1 when none was — which on an
authenticated machine means `gh auth status` is the thing to read first — and
2 on a usage error.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OWNERS_FILE = REPO / "registry" / "owners.txt"


def _load_sibling(name: str, filename: str):
    """A module beside this file, keyed in sys.modules so every loader shares one copy."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(os.path.abspath(__file__)), filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gh_accounts = _load_sibling("fleet_gh_accounts", "gh_accounts.py")

# Where clones live when no root is named: the checkout's own parent first — a
# control plane is usually cloned beside the work it orchestrates — then the
# directories people keep code in, under the home directory on every OS. A
# root that does not exist costs nothing.
CODE_DIRS = ("code", "src", "dev", "projects", "work", "git", "repos")
SCAN_DEPTH = 5
# Vendored and package-manager checkouts are not repos the operator works in:
# `~/.vim/plugged/<plugin>/.git` would rank a plugin author above the operator.
# Every dot-directory is pruned too — `.git` is matched first, so that does not
# prune the thing being looked for.
PRUNED = ("node_modules", "vendor")
NOREPLY = "@users.noreply.github.com"


def default_roots() -> list[Path]:
    return [REPO.parent, *(Path.home() / d for d in CODE_DIRS)]


class Candidates:
    """Owner -> its evidence, in first-seen order, so gh's answers lead."""

    def __init__(self) -> None:
        self.sources: dict[str, list[str]] = {}

    def note(self, owner: str, source: str) -> None:
        seen = self.sources.setdefault(owner, [])
        if source not in seen:
            seen.append(source)


# --- what gh already knows ----------------------------------------------------


def _api(token: str, *args: str) -> str:
    try:
        done = gh_accounts.api_as(token, *args, stdin=subprocess.DEVNULL)
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def ask_gh(candidates: Candidates) -> str:
    """Every gh login's account and orgs, noted; returns the scope hint, or "".

    EVERY LOGIN, not just the active one: a personal account and an employer's
    reach disjoint orgs, and an owner never offered never reaches the map. Gated
    on gh being installed AND NOTHING MORE — `gh auth status` exits 1 when any
    one login has lapsed, which threw away every healthy one. A login that
    cannot say who it is is NAMED, never silently dropped.

    An empty org list may be a missing scope rather than an answer, and the two
    look identical, so the advice is given only when NO login listed an org.
    """
    if not shutil.which("gh"):
        return "no gh account answered, so no login and no org is below: gh auth login"

    answered, orgs_seen, orgless = False, False, []

    def one(token: str) -> bool:
        nonlocal orgs_seen
        login = _api(token, "user", "--jq", ".login")
        if not login:
            return False
        candidates.note(login, "gh account")
        orgs = [o for o in _api(token, "user/orgs", "--jq", ".[].login").splitlines() if o]
        for org in orgs:
            candidates.note(org, "gh org")
        orgs_seen = orgs_seen or bool(orgs)
        if not orgs:
            orgless.append(f"'{login}'")
        return True

    # GH_HOST is the variable `gh api` itself obeys, so logins for any other
    # host would name tokens the calls above never use.
    host = os.environ.get("GH_HOST") or "github.com"
    logins = gh_accounts.accounts(host)
    if not logins:
        answered = one("")
    for login in logins:
        token = gh_accounts.account_token(host, login)
        if not token:
            sys.stderr.write(f"warning: no usable token for gh account '{login}' — its orgs are not below\n")
        elif one(token):
            answered = True
        else:
            sys.stderr.write(f"warning: gh account '{login}' could not say who it is — its orgs are not below\n")

    if not answered:
        return "no gh account answered, so no login and no org is below: gh auth login"
    if not orgs_seen:
        return (f"gh listed no orgs for {', '.join(orgless)}. If you expect some, "
                "that token is missing a scope: gh auth refresh -s read:org")
    return ""


# --- what the git configuration remembers -------------------------------------


def ask_git_config(candidates: Candidates) -> None:
    """`github.user` and a noreply commit address, asked from OUTSIDE this
    checkout: a repo-local `user.email` outranks the machine's own config, and
    this is a question about the MACHINE."""
    with tempfile.TemporaryDirectory() as probe:
        def get(key: str) -> str:
            try:
                done = subprocess.run(["git", "-C", probe, "config", "--get", key], stdin=subprocess.DEVNULL,
                                      capture_output=True, encoding="utf-8", errors="replace", check=False)
            except OSError:
                return ""
            return done.stdout.strip() if done.returncode == 0 else ""

        user, email = get("github.user"), get("user.email")
    if user:
        candidates.note(user, "git config github.user")
    if email.endswith(NOREPLY):
        handle = email[: -len(NOREPLY)].rsplit("+", 1)[-1]
        if handle:
            candidates.note(handle, "git commit email")


# --- what the clones on this disk say -----------------------------------------


def git_dirs(root: Path, depth: int = 0) -> Iterator[Path]:
    """Every `.git` directory at most SCAN_DEPTH levels under `root`."""
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        if entry.name == ".git":
            yield Path(entry.path)
        elif entry.name not in PRUNED and not entry.name.startswith(".") and depth + 1 < SCAN_DEPTH:
            yield from git_dirs(Path(entry.path), depth + 1)


ORIGIN = re.compile(r'\s*\[remote "origin"\]')
SECTION = re.compile(r"\s*\[")
URL = re.compile(r"\s*url\s*=\s*(.*)")


def origin_url(config: Path) -> str:
    """ORIGIN's url and no other, read from the file rather than by running git.

    A fork carries `upstream` too, and counting that owner would report a
    project the operator has no repos under.
    """
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    inside = False
    for line in text.splitlines():
        if ORIGIN.match(line):
            inside = True
        elif inside and SECTION.match(line):
            inside = False
        elif inside and (found := URL.match(line)):
            return found.group(1).strip()
    return ""


def host_and_owner(url: str) -> tuple[str, str] | None:
    """Host and owner out of any remote shape, INCLUDING an ssh host ALIAS.

    `git@github-perso:owner/repo.git` is what a machine with two GitHub
    accounts looks like, so the host is matched on `github`, not `github.com`.
    A URL with a SCHEME splits on `/` alone, because its host may carry a port
    (`ssh://git@ssh.github.com:443/o/r.git`); the `[:/]` split belongs to the
    scp-style form, the only one where `:` separates host from path.
    """
    if "://" in url:
        rest = url.split("://", 1)[1]
        rest = rest.split("@", 1)[1] if "@" in rest else rest
        if "/" not in rest:
            return None
        host, owner = rest.split("/", 1)
        host = host.split(":", 1)[0]
    else:
        rest = url.split("@", 1)[1] if "@" in url else url
        split = re.search(r"[:/]", rest)
        if not split:
            return None
        host, owner = rest[: split.start()], rest[split.end():]
    owner = owner.split("/", 1)[0]
    return (host, owner) if owner else None


def scan(roots: list[Path]) -> tuple[Counter, Counter]:
    """Local checkouts per GitHub owner, and per GitLab host/owner."""
    github, gitlab = Counter(), Counter()
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        real = root.resolve()
        if real in seen:
            continue
        seen.add(real)
        for git_dir in git_dirs(real):
            parsed = host_and_owner(origin_url(git_dir / "config"))
            if not parsed:
                continue
            host, owner = parsed
            if "github" in host:
                github[owner] += 1
            elif "gitlab" in host:
                gitlab[f"{host}/{owner}"] += 1
    return github, gitlab


# --- output -------------------------------------------------------------------


def configured_owners() -> set[str]:
    """What the operator already committed to, so a re-run says so."""
    try:
        lines = OWNERS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {owner for line in lines if (owner := "".join(line.split("#", 1)[0].split()))}


def main(argv: list[str]) -> int:
    roots: list[Path] = []
    for arg in argv:
        if arg in ("-h", "--help"):
            sys.stdout.write(__doc__)
            return 0
        if arg.startswith("-"):
            sys.stderr.write("usage: uv run fleet discover-owners [root ...]\n")
            return 2
        roots.append(Path(arg))

    candidates = Candidates()
    scope_hint = ask_gh(candidates)
    ask_git_config(candidates)
    github, gitlab = scan(roots or default_roots())
    # Clone counts join AFTER gh's answers, biggest first, so the order an
    # operator reads is the order they would pick in.
    for owner, count in sorted(github.items(), key=lambda kv: (kv[1], kv[0]), reverse=True):
        candidates.note(owner, f"local clones ({count})")

    if not candidates.sources:
        sys.stderr.write("No candidate owners found.\n\n")
        if scope_hint:
            sys.stderr.write(f"  {scope_hint}\n")
        sys.stderr.write(
            "  Nothing on this machine names a GitHub owner: no gh session, no\n"
            "  github.user, and no clone with a github.com remote under the roots\n"
            "  scanned. Name a root to scan, or write registry/owners.txt by hand\n"
            "  from registry/owners.example.txt.\n"
        )
        return 1

    configured = configured_owners()
    print("CANDIDATE OWNERS — for registry/owners.txt, which is a list of GITHUB owners\n")
    for owner, sources in candidates.sources.items():
        mark = "* " if owner in configured else "  "
        print(f"  {mark}{owner:<22} {', '.join(sources)}")
    if configured:
        print("\n  * already in registry/owners.txt")
    if scope_hint:
        print(f"\n  note: {scope_hint}")

    if gitlab:
        print("\nGITLAB CHECKOUTS — evidence, not owners\n")
        for key, count in sorted(gitlab.items()):
            print(f"  {key:<30} {count} local clone(s)")
        print("\n  These belong in no owners file: the map is built with gh. A task targets a\n"
              "  GitLab repo by host and path, through the seam in scripts/lib/forge.py, and\n"
              "  needs glab authenticated for that host.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
