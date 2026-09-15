"""What the install tests build: a committed copy of this tree, clones of it, and
machines made of stand-ins.

    upstream     this working tree as it stands, committed into a repo of its
                 own, plus a stand-in `scripts/lib/install_extension.py` that
                 logs its call — so a test installs the code under test, and
                 never registers anything with a real thurbox
    machine      a PATH holding exactly the tools a test names, each a copy of
                 the stub launcher; `git` is the one real tool, reached through
                 a forwarder so its directory does not come along
    installs     a package manager that really "installs": it puts the named
                 tool on that PATH, so a second run finds it

Named `installkit` and not `kit`: tests/onboarding has a `kit`, and one pytest
run imports both directories.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

from harness import PYTHON, REPO, Run, Stubs

REAL_GIT = shutil.which("git")
EXE = ".exe" if os.name == "nt" else ""
WINDOWS = os.name == "nt"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

FLOOR = re.search(
    r'^min_thurbox_version *= *"(.*)"', (REPO / "extension.toml.in").read_text(encoding="utf-8"), re.MULTILINE
).group(1)

# How fleet is run off a checkout that is not this one: its own `fleet.cli`,
# first on sys.path, ahead of the editable install that points at this tree.
BOOT = "import sys\nsys.path.insert(0, sys.argv[1])\nfrom fleet.cli import main\nsys.exit(main(sys.argv[2:]))"

# The stand-in for S7's module: it logs the checkout it was called for into the
# stub root's calls.log, the same log every stub tool writes, so an assertion
# can read ORDER across the extension and the probes.
EXTENSION_STANDIN = '''\
"""A test stand-in: logs that the extension was installed, and from where."""
import os
import sys

CHECKOUT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv):
    root = os.environ.get("FLEET_STUB_ROOT")
    if root:
        with open(os.path.join(root, "calls.log"), "a", encoding="utf-8", newline="\\n") as fh:
            fh.write("extension install " + CHECKOUT + "\\n")
    print("extension: installed (stand-in)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


def plain(text: str) -> str:
    return ANSI.sub("", text)


def says(line: str) -> str:
    return f"print({line!r})\n"


GIT = f"import subprocess, sys\nraise SystemExit(subprocess.call([{REAL_GIT!r}, *sys.argv[1:]]))\n"

GH = """
import sys
a = sys.argv[1:]
if a[:1] == ["--version"]:
    print("gh version 2.100.0")
elif a[:2] == ["auth", "status"]:
    raise SystemExit(1 if "--json" in a else 0)
elif a[:2] == ["api", "user"]:
    print("octo")
"""

GH_LOGGED_OUT = """
import sys
if sys.argv[1:2] == ["--version"]:
    print("gh version 2.100.0")
else:
    raise SystemExit(1)
"""

GLAB = """
import sys
if sys.argv[1:2] == ["auth"]:
    raise SystemExit(0)
print("glab 1.60.0")
"""


def git_env() -> dict[str, str]:
    """A git that reads no config of the caller's, for fixtures built outside a test's isolation."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME="selftest",
               GIT_AUTHOR_EMAIL="selftest@example.invalid", GIT_COMMITTER_NAME="selftest",
               GIT_COMMITTER_EMAIL="selftest@example.invalid")
    return env


def git(*args: str, cwd: Path) -> str:
    done = subprocess.run([REAL_GIT, "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
                          cwd=cwd, env=git_env(), capture_output=True, encoding="utf-8", check=False)
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout


def build_upstream(where: Path) -> Path:
    """This working tree, as it stands, committed into a repo of its own."""
    listed = subprocess.run([REAL_GIT, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            cwd=REPO, env=git_env(), capture_output=True, check=True).stdout
    for rel in filter(None, listed.decode("utf-8").split("\0")):
        src = REPO / rel
        if src.is_symlink() or not src.is_file():
            continue
        dest = where / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    (where / "scripts" / "lib" / "install_extension.py").write_text(EXTENSION_STANDIN, encoding="utf-8",
                                                                     newline="\n")
    git("init", "-q", cwd=where)
    git("add", "-A", cwd=where)
    git("commit", "-qm", "fixture: the tree under test", cwd=where)
    return where


def clone(upstream: Path, dest: Path) -> Path:
    git("clone", "-q", str(upstream), str(dest), cwd=dest.parent)
    return dest


@cache
def launcher() -> str:
    found = shutil.which("fleet-stub")
    if not found:
        raise RuntimeError("fleet-stub not on PATH: the stub package is not installed")
    return found


def place(stubs: Stubs, name: str, script: str) -> None:
    """`name` on this test's PATH, answering with `script`."""
    scripts = stubs.root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / f"{name}.py").write_text(script, encoding="utf-8", newline="\n")
    stubs.bin.mkdir(parents=True, exist_ok=True)
    shutil.copy2(launcher(), stubs.bin / (name + EXE))


def full_machine(family: str) -> dict[str, str]:
    """Every tool the required and recommended tiers probe, for `family`."""
    tools = {
        "git": GIT,
        "gh": GH,
        "uv": says("uv 0.12.13"),
        "thurbox-cli": says(f"thurbox-cli {FLOOR}"),
        "quota-axi": says("0.1.41"),
        "glab": GLAB,
    }
    tools["psmux" if family == "windows" else "tmux"] = says("tmux 3.4")
    return tools


def machine(stubs: Stubs, tools: dict[str, str], without: tuple[str, ...] = ()) -> str:
    """A PATH holding exactly `tools` minus `without`."""
    for name in without:
        (stubs.bin / (name + EXE)).unlink(missing_ok=True)
    for name, script in tools.items():
        if name not in without:
            place(stubs, name, script)
    return str(stubs.bin)


def installs(tools: dict[str, str], fail: tuple[str, ...] = (), answers: dict[str, str] | None = None) -> str:
    """A package manager (or sudo, or npm) that puts the tool each package names on PATH.

    `tools` maps a package name to the tool it provides, which then answers with
    `answers[tool]`, else a version line; a package in `fail` exits 100 and
    installs nothing. A package it does not know is a no-op success, as a
    manager reports a package already present.
    """
    return f"""
import os, shutil, sys
from pathlib import Path
TOOLS = {tools!r}
FAIL = {fail!r}
ANSWERS = {answers or {}!r}
root = Path(os.environ["FLEET_STUB_ROOT"])
for arg in sys.argv[1:]:
    if arg in FAIL:
        sys.stderr.write("E: could not install " + arg + "\\n")
        raise SystemExit(100)
    if arg in TOOLS:
        tool = TOOLS[arg]
        answer = ANSWERS.get(tool, "print('" + tool + " 9.9.9')\\n")
        (root / "scripts" / (tool + ".py")).write_text(answer, encoding="utf-8")
        shutil.copy2({launcher()!r}, root / "bin" / (tool + {EXE!r}))
"""


def uv_standin() -> str:
    """A `uv` that runs `fleet` off the checkout `--project` names with this test run's
    Python — the one holding PyYAML — and `python` itself for `run --no-project`."""
    return f"""
import subprocess, sys
PY = {sys.executable!r}
BOOT = {BOOT!r}
a = sys.argv[1:]
if a[:1] in (["--version"], ["-V"]):
    print("uv 0.12.13")
    raise SystemExit(0)
if a[:1] != ["run"]:
    raise SystemExit(2)
rest, project = a[1:], None
while rest and rest[0].startswith("-"):
    opt = rest.pop(0)
    if opt == "--project":
        project = rest.pop(0)
if rest[:1] == ["fleet"] and project:
    raise SystemExit(subprocess.call([PY, "-c", BOOT, project, *rest[1:]]))
if rest[:1] == ["python"]:
    raise SystemExit(subprocess.call([PY, *rest[1:]]))
raise SystemExit(2)
"""


# sudo runs the rest of its argv, found on the same PATH, as the real one does.
SUDO = """
import shutil, subprocess, sys
raise SystemExit(subprocess.call([shutil.which(sys.argv[1]), *sys.argv[2:]]))
"""


def fleet(checkout: Path, *args: str, stdin: str | None = "", path: str, family: str | None = None,
          **env: str | None) -> Run:
    """`fleet <args>` run off `checkout`, with PATH and the install family pinned."""
    child = dict(os.environ)
    child["PATH"] = path
    if family:
        child["FLEET_INSTALL_FAMILY"] = family
    for k, v in env.items():
        if v is None:
            child.pop(k, None)
        else:
            child[k] = v
    done = subprocess.run([*PYTHON, "-c", BOOT, str(checkout), *args], cwd=checkout, env=child, input=stdin,
                          capture_output=True, encoding="utf-8", errors="replace",
                          start_new_session=not WINDOWS)
    return Run(done.returncode, done.stdout, plain(done.stderr))


def tree_snapshot(root: Path) -> dict[str, bytes]:
    """Every file under `root` outside .git, by relative path, with its bytes."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", ".venv")]
        for name in filenames:
            path = Path(dirpath) / name
            found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def load_install():
    """scripts/lib/install.py, loaded in-process the way fleet/cli.py loads it."""
    from fleet.cli import load

    return load("install.py")
