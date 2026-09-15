"""The machines onboarding's readers are run against, built from stand-ins.

A PATH here holds ONLY the test's own bin: every tool on it is a copy of the
stub launcher answering as that name, so a tool a test says is missing really
is missing on a machine that has it installed. `git` is the one real tool, and
it is reached through a stand-in that runs the real binary, so putting it on
PATH does not bring the rest of its directory along.
"""

from __future__ import annotations

import os
import re
import shutil
from functools import cache
from pathlib import Path

from harness import REPO, Stubs

REAL_GIT = shutil.which("git")
EXE = ".exe" if os.name == "nt" else ""
ANSI = re.compile(r"\x1b\[[0-9;]*m")

FLOOR = re.search(
    r'^min_thurbox_version *= *"(.*)"', (REPO / "extension.toml.in").read_text(encoding="utf-8"), re.MULTILINE
).group(1)


def says(line: str) -> str:
    """A tool that prints one line, whatever it is asked, and reads no stdin."""
    return f"print({line!r})\n"


GIT = f"import subprocess, sys\nraise SystemExit(subprocess.call([{REAL_GIT!r}, *sys.argv[1:]]))\n"

# The fully equipped machine's gh: a version, a session that authenticates, and
# no `auth status --json`, so the row takes the active-session fallback.
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

GLAB = """
import sys
if sys.argv[1:2] == ["auth"]:
    raise SystemExit(0)
print("glab 1.60.0")
"""


def full_machine() -> dict[str, str]:
    """Every tool preflight probes, on either OS family, plus a manager for each."""
    return {
        "git": GIT,
        "gh": GH,
        "uv": says("uv 0.12.13"),
        "thurbox-cli": says(f"thurbox-cli {FLOOR}"),
        "tmux": says("tmux 3.4"),
        "psmux": says("tmux 3.3.6"),
        "quota-axi": says("0.1.41"),
        "glab": GLAB,
        "lua": says("Lua 5.4.4"),
        "prek": says("prek 0.5.0"),
        # A package manager per family, so a gap turns into a runnable line
        # rather than a URL. Which one is not the point; that it is a command is.
        "apt-get": says(""),
        "winget": says(""),
    }


@cache
def launcher() -> str:
    """The stub launcher, found once while the stub package is still on PATH:
    a test that has already narrowed PATH to its own bin builds more tools."""
    found = shutil.which("fleet-stub")
    if not found:
        raise RuntimeError("fleet-stub not on PATH: the stub package is not installed")
    return found


def place(stubs: Stubs, name: str, script: str) -> None:
    """`name` on this test's PATH, answering with `script`.

    Every name gets its own copy of the launcher, the stub package's own tools
    included: that package's directory is not on a machine built here.
    """
    scripts = stubs.root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / f"{name}.py").write_text(script, encoding="utf-8", newline="\n")
    stubs.bin.mkdir(parents=True, exist_ok=True)
    shutil.copy2(launcher(), stubs.bin / (name + EXE))


def machine(stubs: Stubs, tools: dict[str, str], without: tuple[str, ...] = ()) -> str:
    """A PATH holding exactly `tools` minus `without`, and nothing else."""
    for name in without:
        (stubs.bin / (name + EXE)).unlink(missing_ok=True)
    for name, script in tools.items():
        if name not in without:
            place(stubs, name, script)
    return str(stubs.bin)


def plain(text: str) -> str:
    """Colour codes out, so `missing  gh auth` can be matched as one string."""
    return ANSI.sub("", text)


def between(text: str, start: str, end: str | None = None) -> str:
    """The lines from the one starting `start` up to the one starting `end`."""
    lines = text.splitlines()
    begin = next((i for i, line in enumerate(lines) if line.startswith(start)), len(lines))
    stop = next((i for i, line in enumerate(lines) if end and i > begin and line.startswith(end)), len(lines))
    return "\n".join(lines[begin:stop])


def git_config(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return str(path)
