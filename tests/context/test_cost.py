"""What one `fleet context repo` costs, counted rather than timed.

Every brief tells its worker to run `fleet context repo` before it starts, so
its cost is paid once per dispatch. Naming the repository a past result belongs
to means asking git about that task's checkout, and the queue's results span
every checkout fleet has worked in. The count of `git` processes per checkout
is the cost that grows with the queue, and it is deterministic where a wall
clock on a shared machine is not.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from contextkit import BRIEF, checkout, context, parse_toon

from harness import run_queue_batch, write


def git_calls(stubs) -> list[str]:
    log = stubs.root / "calls.log"
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return [line for line in lines if line.startswith("git ")]


def test_naming_each_results_checkout_costs_one_git_call_and_one_more_without_an_origin(tmp_path, stubs):
    forged = [checkout(tmp_path, f"app{n}", f"https://github.com/acme/app{n}.git") for n in range(3)]
    local = checkout(tmp_path, "tool", None)
    brief = tmp_path / "brief.md"
    write(brief, BRIEF)
    commands = [["topic", "add", "work", "--title", "Work", "--prompt", "do it"]]
    for n, repo in enumerate([*forged, local], 1):
        commands.append(["add", "work", f"task-{n}", "--title", f"Task {n}", "--repo", str(repo),
                         "--branch", f"t/{n}", "--publish", "none", "--brief-file", str(brief)])
    assert run_queue_batch(commands).code == 0
    for task in sorted((Path(os.environ["FLEET_QUEUE_DIR"]) / "work").glob("0*")):
        write(task / "result.md", "---\noutcome: shipped\n---\nDid it.\n")

    # The real git, logged: every call this command makes goes through it.
    real = shutil.which("git")
    stubs.tool("git", f"import subprocess, sys\nsys.exit(subprocess.run([{real!r}, *sys.argv[1:]]).returncode)\n")
    done = context("repo", "github.com/acme/app0")
    assert done.code == 0, done.out
    assert parse_toon(done.stdout)["results"] == "1 for github.com/acme/app0", done.out

    # One `remote get-url` per checkout answers both "is it a checkout" and
    # "where is it from"; only the checkout with no origin needs a second call.
    calls = git_calls(stubs)
    assert len(calls) == len(forged) + 2, "\n".join(calls)
