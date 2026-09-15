#!/usr/bin/env python3
"""The gate checks that already run natively on Windows, in Python.

    fleet check --platform-ported [--fix]   every check below
    fleet check [--fix] <check>...          only the named ones

Checks: markdown, yaml, profiles, queue, cli, pane, workflow. Only `markdown`
has a fixer.

`scripts/check.sh` is still the whole gate on Linux, and every check here is
one of its checks (or, for `queue`, the read half of one) rather than a second
definition of it: each calls the same Python module or Lua harness that
check.sh does. The Windows CI job runs this, so the set is exactly the checks
that pass on `windows-latest`. A task that ports another check adds it here in
the same pull request; the last one retires `--platform-ported`, and then
`fleet check` is the gate on both.

LIKE check.sh, IT READS NO OPERATOR STATE. `queue` builds its own throwaway
queue under a throwaway HOME, with the tracked `*.example.conf` as the only
settings — the same pins `scripts/lib/selftest-env.sh` gives every selftest —
and every other check reads tracked files only. A missing tool fails its check
rather than skipping it.
"""

from __future__ import annotations

import glob
import os
import shutil
import site
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORTED = ["markdown", "yaml", "profiles", "queue", "cli", "pane", "workflow"]


def tracked(*patterns: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *patterns], cwd=REPO, capture_output=True, check=True
    ).stdout
    return [f for f in out.decode().split("\0") if f]


def run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=REPO, **kw)


def missing(tool: str) -> str | None:
    return None if shutil.which(tool) else f"{tool} not found"


def check_markdown(fix: bool) -> str | None:
    # An explicit file list, not a directory walk, for check.sh's reason: rumdl
    # walks nothing inside a linked git worktree, where every worker runs.
    if err := missing("rumdl"):
        return err
    files = tracked("*.md")
    if not files:
        return "no tracked *.md files found"
    if run(["rumdl", "check", *(["--fix"] if fix else []), *files]).returncode:
        return "rumdl"
    print(f"      rumdl clean ({len(files)} tracked files)")
    return None


def check_yaml(fix: bool) -> str | None:
    files = tracked("*.yml", "*.yaml")
    if not files:
        return "no tracked *.yml/*.yaml files found"
    if run([sys.executable, "scripts/lib/check_yaml.py", *files]).returncode:
        return "scripts/lib/check_yaml.py"
    print(f"      {len(files)} tracked files parse")
    return None


def check_profiles(fix: bool) -> str | None:
    argv = [sys.executable, "scripts/lib/session_profiles.py", "orchestration/session-profiles.yaml", "--check"]
    return "scripts/lib/session_profiles.py --check" if run(argv).returncode else None


def isolated_env(tmp: str) -> dict[str, str]:
    """selftest-env.sh's pins, for a Python caller on either OS."""
    env = dict(os.environ)
    # Kept, as selftest-env.sh keeps it: a `pip --user` PyYAML lives under the
    # real HOME this replaces.
    env.setdefault("PYTHONUSERBASE", site.getuserbase())
    home = os.path.join(tmp, "home")
    for var, sub in [
        ("HOME", ""), ("USERPROFILE", ""), ("APPDATA", "AppData/Roaming"),
        ("LOCALAPPDATA", "AppData/Local"), ("XDG_CONFIG_HOME", ".config"),
        ("XDG_CACHE_HOME", ".cache"), ("XDG_DATA_HOME", ".local/share"), ("XDG_STATE_HOME", ".local/state"),
    ]:
        env[var] = os.path.join(home, sub) if sub else home
        os.makedirs(env[var], exist_ok=True)
    for var in [k for k in env if k.startswith("GIT_") or k.startswith("GIT_CONFIG_")]:
        del env[var]
    for var in ["GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_HOST",
                "GH_CONFIG_DIR", "GITLAB_TOKEN", "GITLAB_HOST", "GLAB_CONFIG_DIR", "THURBOX_SESSION"]:
        env.pop(var, None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    # A Python child on Windows writes its pipe in the console code page (cp1252
    # on the runner), so queue.py's em dash arrives as a byte UTF-8 cannot read.
    env["PYTHONIOENCODING"] = "utf-8"

    settings = os.path.join(tmp, "settings")
    os.makedirs(os.path.join(settings, "orchestration"))
    for conf in glob.glob(os.path.join(REPO, "orchestration", "*.example.conf")):
        shutil.copy(conf, os.path.join(settings, "orchestration"))
    for var in ["FLEET_AUTO_MERGE_ROOT", "FLEET_PUBLISH_ROOT", "FLEET_AGENT_ROOT", "FLEET_GLYPH_ROOT"]:
        env[var] = settings
    env["FLEET_VOICE_CONF"] = os.path.join(settings, "orchestration", "voice.example.conf")
    for var, sub in [("FLEET_QUEUE_DIR", "queue"), ("FLEET_RUNS_DIR", "runs"), ("FLEET_RECONCILE_DIR", "reconcile")]:
        env[var] = os.path.join(tmp, sub)
        os.makedirs(env[var])
    env["FLEET_REGISTRY_FILE"] = os.path.join(tmp, "registry", "repos.generated.yaml")
    return env


def check_queue(fix: bool) -> str | None:
    # The READ half of check.sh's queue check: the verbs a lead runs to see the
    # queue, against a topic and task built for it. The selftest that proves the
    # ordering and wake claims is bash, and stays check.sh's until it is ported.
    with tempfile.TemporaryDirectory() as tmp:
        env = isolated_env(tmp)
        queue = [sys.executable, "scripts/lib/queue.py"]
        task = "ported-checks/01-first-task"
        steps = [
            ["topic", "add", "ported-checks", "--prompt", "a queue built to be read"],
            ["add", "ported-checks", "first-task", "--title", "A task to read back",
             "--repo", os.path.join(tmp, "repo"), "--branch", "fix/first-task"],
            ["root"], ["list"], ["plan"], ["check"], ["show", task],
        ]
        for step in steps:
            done = run([*queue, *step], env=env, capture_output=True, text=True, encoding="utf-8")
            if done.returncode:
                sys.stdout.write(done.stdout + done.stderr)
                return f"queue.py {' '.join(step[:2])} exited {done.returncode}"
            if step == ["plan"] and task not in done.stdout:
                sys.stdout.write(done.stdout)
                return f"queue.py plan does not list {task} as ready"
    print("      topic add, add, root, list, plan, check and show read back a throwaway queue")
    return None


def check_cli(fix: bool) -> str | None:
    # check.sh's cli check, whole: the lock first, then tests/, which skip their
    # bash half on Windows. `uv run` is what puts `fleet` on PATH for them.
    if err := missing("uv"):
        return err
    if run(["uv", "lock", "--check"]).returncode:
        return "uv.lock does not match pyproject.toml — run `uv lock`"
    if run(["uv", "run", "--frozen", "--quiet", "python", "-m", "unittest", "discover", "-s", "tests"]).returncode:
        return "tests/"
    return None


def check_pane(fix: bool) -> str | None:
    # The render, at both widths pane-selftest.sh holds the pane to. Its row
    # assertions are bash and stay check.sh's until that selftest is ported.
    if err := missing("lua"):
        return err
    for args in [["44"], ["30"], ["44", "--marks"]]:
        done = run(["lua", "scripts/lib/pane_harness.lua", *args], capture_output=True)
        if done.returncode or not done.stdout.strip():
            sys.stdout.buffer.write(done.stdout + done.stderr)
            return f"the pane did not render: lua scripts/lib/pane_harness.lua {' '.join(args)}"
    print("      renders at 44 and 30 columns, and with --marks")
    return None


def check_workflow(fix: bool) -> str | None:
    return "scripts/lib/check_workflow.py" if run([sys.executable, "scripts/lib/check_workflow.py"]).returncode else None


CHECKS = {name: globals()[f"check_{name}"] for name in PORTED}


def main(argv: list[str]) -> int:
    # Line-buffered, so each verdict lands after the tool output it judges.
    sys.stdout.reconfigure(line_buffering=True)
    usage = (
        f"usage: fleet check [--fix] --platform-ported | <check>...  (checks: {' '.join(PORTED)})\n"
        "The whole gate is still ./scripts/check.sh."
    )
    if "-h" in argv or "--help" in argv:
        print(usage)
        return 0
    fix = "--fix" in argv
    names = [a for a in argv if a not in ("--fix", "--platform-ported")]
    if "--platform-ported" in argv:
        names = PORTED + names
    unknown = [n for n in names if n not in CHECKS]
    if unknown or not names:
        print(usage, file=sys.stderr)
        return 2

    failed = False
    for name in dict.fromkeys(names):
        err = CHECKS[name](fix)
        if err:
            print(f"\033[31mFAIL\033[0m  {name}: {err}", file=sys.stderr)
            failed = True
        else:
            print(f"\033[32mok\033[0m    {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
