"""What the sync tests build: a throwaway origin and clone, and a checkout of fleet's own.

    new_repo       a bare origin on disk and a clone of it, both on `main`
    advance_origin a commit on origin the clone does not have yet
    sandbox        a copy of the scripts/lib modules that write the registry, in
                   a root of its own, because they resolve their paths from
                   where they live and OVERWRITE registry/repos.generated.yaml
    GH             a `gh` that answers per token, reading fixtures/ under the
                   stub root, the way `gh api` answers per account
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from harness import PYTHON, REPO, Run, git, run, write


def new_repo(root: Path) -> Path:
    origin = root / "origin.git"
    work = root / "work"
    root.mkdir(parents=True, exist_ok=True)
    git("init", "--quiet", "--bare", "--initial-branch=main", str(origin), cwd=root)
    git("clone", "--quiet", str(origin), str(work), cwd=root)
    write(work / "README.md", "seed\n")
    git("add", "README.md", cwd=work)
    git("commit", "--quiet", "-m", "seed", cwd=work)
    git("push", "--quiet", "origin", "main", cwd=work)
    # `git clone` records origin/HEAD only for a non-empty remote, and the sync
    # falls back to `main` without it. Set it so the fallback is not what is
    # under test.
    git("remote", "set-head", "origin", "main", cwd=work)
    return work


def upstream(root: Path) -> Path:
    """A fresh clone of origin to commit through, in a directory of its own.

    A new directory per call and never a reused one: git writes its objects
    read-only, which makes deleting a clone fail on Windows, and a delete that
    ignores its errors left the old clone standing for `git clone` to refuse as
    a non-empty destination. The second call is the one that broke.
    """
    up = Path(tempfile.mkdtemp(prefix="upstream-", dir=root))
    git("clone", "--quiet", str(root / "origin.git"), str(up), cwd=root)
    return up


def advance_origin(root: Path, file: str = "CHANGELOG.md") -> None:
    up = upstream(root)
    path = up / file
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("incoming\n")
    git("add", file, cwd=up)
    git("commit", "--quiet", "-m", f"incoming: {file}", cwd=up)
    git("push", "--quiet", "origin", "main", cwd=up)


def remove_on_origin(root: Path, file: str) -> None:
    up = upstream(root)
    git("rm", "--quiet", file, cwd=up)
    git("commit", "--quiet", "-m", f"removed: {file}", cwd=up)
    git("push", "--quiet", "origin", "main", cwd=up)


def head_of(work: Path) -> str:
    return git("rev-parse", "HEAD", cwd=work).strip()


# --- the registry -------------------------------------------------------------

SANDBOX_MODULES = ("sync_registry.py", "add_owner.py", "gh_accounts.py", "glab_hosts.py", "fleet_platform.py")


def sandbox(root: Path, owners: str | None) -> Path:
    """A checkout holding only the modules that read owners.txt and write the map."""
    lib = root / "scripts" / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    (root / "registry").mkdir(parents=True, exist_ok=True)
    for name in SANDBOX_MODULES:
        shutil.copy(REPO / "scripts" / "lib" / name, lib / name)
    if owners is not None:
        write(root / "registry" / "owners.txt", owners)
    return root


def run_module(root: Path, module: str, *args: str, **env: str | None) -> Run:
    return run([*PYTHON, str(root / "scripts" / "lib" / module), *args], cwd=root, **env)


def repo_json(owner: str, name: str) -> str:
    return json.dumps({
        "full_name": f"{owner}/{name}", "name": name, "html_url": f"https://github.com/{owner}/{name}",
        "owner": {"login": owner}, "private": False, "role_name": "admin", "archived": False,
        "fork": False, "language": "Rust", "default_branch": "main", "pushed_at": "2026-09-01T00:00:00Z",
        "topics": [], "description": "fixture",
    }) + "\n"


def hosts_json(*logins: tuple[str, str]) -> str:
    return json.dumps({"hosts": {"github.com": [
        {"state": state, "active": i == 0, "host": "github.com", "login": login}
        for i, (login, state) in enumerate(logins)
    ]}})


# `auth status --json hosts` is the account list and `auth token --user` hands
# one over WITHOUT switching the active account. `api` answers as the token it
# was called under, with the active session (`octo`) when there is none.
#
# fixtures/mode picks the machine: `old` is a gh with no `auth status --json`,
# `dead` is every listing failing, and anything else is a working one.
GH = r"""
import os, sys
from pathlib import Path
fx = Path(os.environ["FLEET_STUB_ROOT"]) / "fixtures"
mode = (fx / "mode").read_text(encoding="utf-8").strip() if (fx / "mode").exists() else "ok"
args = sys.argv[1:]
login = os.environ.get("GH_TOKEN", "tok-octo").removeprefix("tok-")

def out(path):
    if not path.exists():
        raise SystemExit(1)
    sys.stdout.write(path.read_text(encoding="utf-8"))

if args[:2] == ["auth", "status"]:
    if "--json" not in args:
        raise SystemExit(0 if mode == "old" else 1)
    if mode == "old":
        sys.stderr.write("unknown flag: --json\n")
        raise SystemExit(1)
    out(fx / os.environ.get("HOSTS_FIXTURE", "hosts.json"))
elif args[:2] == ["auth", "token"]:
    user = args[args.index("--user") + 1]
    if user not in ("octo", "client", "worky"):
        raise SystemExit(1)
    print("tok-" + user)
elif args[:2] == ["api", "user"]:
    if mode == "dead" or login not in ("octo", "client", "worky"):
        raise SystemExit(1)
    print(login)
elif args[:3] == ["api", "--paginate", "user/orgs"]:
    out(fx / f"orgs-{login}.txt")
elif args[:2] == ["api", "--paginate"]:
    if mode == "dead":
        sys.stderr.write("dial tcp: network is unreachable\n")
        raise SystemExit(1)
    out(fx / f"repos-{login}.json")
else:
    sys.stderr.write("unexpected gh call: " + " ".join(args) + "\n")
    raise SystemExit(9)
"""

# What glab 1.117.0 prints for one authenticated instance.
GLAB = r"""
import sys
args = sys.argv[1:]
if args[:2] != ["auth", "status"]:
    print("glab 1.117.0")
elif "--all" in args:
    print("gitlab.example.com")
    print("  ✓ Logged in to gitlab.example.com as someone")
"""


def fixture(stubs, name: str, text: str) -> None:
    write(stubs.root / "fixtures" / name, text)
