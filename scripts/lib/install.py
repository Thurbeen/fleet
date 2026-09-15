"""`fleet install`: everything this checkout needs on this machine, from one plan and one question.

The bootstraps (`install.sh`, `install.ps1`) get uv, git and the checkout,
then hand over here. This owns the rest, in this order:

  1. THE PLAN. Every missing dependency preflight reports — the required and
     recommended tiers, the gate tier too with `--dev` — each with the command
     `preflight.install_plan` gives for this machine's package manager. A row
     with no plan is listed and never run: "you run" for a login only the
     operator can do, "no route" for a tool nothing here can install. Then the
     two pieces of wiring below, when they are not already in place. Python is
     not a row: uv provides it.
  2. ONE QUESTION, for the whole plan. `--yes` answers it. Nothing to do asks
     nothing. No terminal and no `--yes` prints the plan, installs nothing and
     exits 1 — a question nobody can answer is not a yes.
  3. THE INSTALLS, each reported, and a failure never stops the next one.
     winget gets both agreement flags, so it never stops to ask; `sudo` is
     dropped when already root; `npm` is found as `npm.cmd` on Windows. On
     Windows this process then re-reads PATH from the registry, where an
     installer wrote the new directory.
  4. THE SKILLS LINK. `.claude/skills` -> `.agents/skills`, a symlink on POSIX
     and a junction on Windows. It is not tracked: a default Windows clone
     checks a tracked link out as a text file, and Claude Code found no skills.
  5. THE STOP NUDGE. A Claude Code `Stop` hook that runs
     `fleet reconcile nudge`, merged into Claude Code's USER settings. Not
     into thurbox's hooks file: thurbox rewrites that from its embedded payload
     at every TUI start and every automation tick, and its own docs say the
     agent's own settings are what survive — claude merges both, and both run.
  6. THE EXTENSION AND QUEUE PANE, through this checkout's
     `scripts/lib/install_extension.py` — never with a required row still
     missing, since the extension is what a missing tool breaks.
  7. PREFLIGHT, last and printed: the verification, not the managers' exit codes.

Idempotent: a second run on a complete machine asks nothing, installs nothing,
and writes no file.

Usage: uv run fleet install [--yes] [--dev]
Exit: 0 installed, 1 a required dependency still missing, a step failed or the
plan was declined, 2 usage.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

CHECKOUT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_sibling(name: str, filename: str):
    """A module beside this file, keyed in sys.modules so every loader shares one copy."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(os.path.abspath(__file__)), filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fleet_platform = _load_sibling("fleet_platform", "fleet_platform.py")
preflight = _load_sibling("fleet_preflight", "preflight.py")
reconcile = _load_sibling("fleet_reconcile", "reconcile.py")

USAGE = "usage: uv run fleet install [--yes] [--dev]\n"
LINK, TARGET = ".claude/skills", ".agents/skills"
NUDGE = " fleet reconcile nudge"
WINGET_ACCEPT = ("--accept-source-agreements", "--accept-package-agreements")


def say(line: str = "") -> None:
    print(line, flush=True)


# --- the plan -------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    """One line of the plan: `argv` is what runs, or None for a row nothing here runs."""

    name: str
    action: str
    text: str
    argv: tuple[str, ...] | None = None


def prepare(argv: tuple[str, ...], root: bool) -> list[str]:
    """The argv a plan actually runs as: no `sudo` for root, and no manager left to ask a second question."""
    argv = list(argv)
    if argv[:1] == ["sudo"] and root:
        argv = argv[1:]
    tool = argv[1] if argv[:1] == ["sudo"] and len(argv) > 1 else argv[0]
    if tool == "winget":
        argv += [flag for flag in WINGET_ACCEPT if flag not in argv]
    if tool == "pacman" and "--noconfirm" not in argv:
        argv.insert(len(argv) - 1, "--noconfirm")
    return argv


def plan_rows(dev: bool, root: bool) -> list[Row]:
    tiers = ["required", "recommended"] + (["gate"] if dev else [])
    manager = preflight.package_manager()
    rows = []
    for finding in preflight.missing(tiers):
        dep = finding.dependency
        if finding.manual:
            rows.append(Row(dep.name, "you run", finding.manual))
            continue
        plan = preflight.install_plan(dep, manager)
        if plan is None:
            rows.append(Row(dep.name, "no route", f"see {dep.see}" if dep.see else "nothing here installs it"))
        elif plan.argv[0] in ("sh", "powershell"):
            # An installer one-liner: the line is what the operator reads.
            rows.append(Row(dep.name, "install", plan.text, plan.argv))
        else:
            rows.append(Row(dep.name, "install", " ".join(prepare(plan.argv, root)), plan.argv))
    return rows


def execute(row: Row, root: bool) -> bool:
    argv = prepare(row.argv, root)
    # By PATH lookup, so a Windows `npm` is found as the npm.cmd it really is.
    exe = shutil.which(argv[0])
    say(f"  {row.name}: {row.text}")
    if not exe:
        say(f"  failed: {row.name} ({argv[0]} is not on PATH)")
        return False
    try:
        code = subprocess.run([exe, *argv[1:]], check=False).returncode
    except OSError as exc:
        say(f"  failed: {row.name} ({exc})")
        return False
    if code:
        say(f"  failed: {row.name} (exit {code})")
        return False
    return True


# --- the skills link ----------------------------------------------------------------


def link_state(checkout: str) -> tuple[str, str]:
    """"ok", "create", "replace" or "refuse", and why."""
    link, target = os.path.join(checkout, *LINK.split("/")), os.path.join(checkout, *TARGET.split("/"))
    if fleet_platform.is_dir_link(link):
        try:
            if os.path.samefile(link, target):
                return "ok", "already in place"
        except OSError:
            pass
        return "replace", "it points somewhere else"
    if os.path.isfile(link):
        with open(link, encoding="utf-8", errors="replace") as fh:
            text = fh.read().strip().replace("\\", "/")
        if text == "../" + TARGET:
            return "replace", "a clone without symlinks left the link as a text file"
        return "refuse", f"{LINK} is a file fleet did not write; move it aside and run this again"
    if os.path.isdir(link):
        if not os.listdir(link):
            return "replace", "an empty directory"
        return "refuse", f"{LINK} is a directory with something in it; move it aside and run this again"
    return "create", "not there yet"


def make_link(checkout: str) -> tuple[bool, str]:
    state, why = link_state(checkout)
    if state == "ok":
        return True, why
    if state == "refuse":
        return False, why
    link = os.path.join(checkout, *LINK.split("/"))
    if not fleet_platform.is_dir_link(link):
        if os.path.isfile(link):
            os.remove(link)
        elif os.path.isdir(link):
            os.rmdir(link)
    os.makedirs(os.path.dirname(link), exist_ok=True)
    fleet_platform.make_dir_link(link, os.path.join(checkout, *TARGET.split("/")))
    return True, f"{LINK} -> {TARGET}"


# --- the Stop nudge ----------------------------------------------------------------------


def claude_settings_file() -> str:
    """Claude Code's user settings: `CLAUDE_CONFIG_DIR`, else `~/.claude`, on every OS."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "settings.json")


def _settings(path: str) -> tuple[dict | None, str]:
    """The settings as a dict, or None and why it cannot be merged into."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}, ""
    except (OSError, ValueError) as exc:
        return None, f"cannot read it as JSON ({exc})"
    if not isinstance(data, dict):
        return None, "it is not a JSON object"
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict) or not isinstance(hooks.get("Stop", []), list):
        return None, "its hooks are not in the shape Claude Code documents"
    return data, ""


def _nudges(data: dict) -> list[dict]:
    """Every Stop hook that is a fleet nudge, whichever checkout it names."""
    found = []
    for entry in data.get("hooks", {}).get("Stop", []):
        for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
            if isinstance(hook, dict) and str(hook.get("command", "")).endswith(NUDGE):
                found.append(hook)
    return found


def hook_state(path: str, command: str) -> tuple[str, str]:
    """"ok", "add", "update" (a moved checkout's nudge) or "refuse", and why."""
    data, error = _settings(path)
    if data is None:
        return "refuse", f"{path}: {error}; left untouched"
    nudges = _nudges(data)
    if any(h.get("command") == command for h in nudges):
        return "ok", "already in place"
    return ("update", "it names another checkout") if nudges else ("add", "not there yet")


def apply_hook(path: str, command: str) -> tuple[bool, str]:
    state, why = hook_state(path, command)
    if state in ("ok", "refuse"):
        return state == "ok", why
    data, _ = _settings(path)
    if state == "update":
        for hook in _nudges(data):
            hook["command"] = command
    else:
        data.setdefault("hooks", {}).setdefault("Stop", []).append(
            {"hooks": [{"type": "command", "command": command, "timeout": 10}]})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fleet_platform.write_record(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True, f"{'repointed' if state == 'update' else 'added'} in {path}"


# --- the extension -----------------------------------------------------------------------


def extension_step(checkout: str) -> int:
    """This checkout's install-extension group, in-process, under the key fleet/cli.py gives it."""
    module = os.path.join(checkout, "scripts", "lib", "install_extension.py")
    if os.path.isfile(module):
        spec = importlib.util.spec_from_file_location("fleet_install_extension", module)
        loaded = importlib.util.module_from_spec(spec)
        sys.modules["fleet_install_extension"] = loaded
        spec.loader.exec_module(loaded)
        return int(loaded.main([]) or 0)
    say("  scripts/lib/install_extension.py is not in this checkout, so the extension cannot be installed from here.")
    script, bash = os.path.join(checkout, "scripts", "install-extension.sh"), shutil.which("bash")
    if os.path.isfile(script) and bash:
        say("  Falling back to scripts/install-extension.sh.")
        return subprocess.run([bash, script], cwd=checkout, stdin=subprocess.DEVNULL, check=False).returncode
    return 1


# --- the run ---------------------------------------------------------------------------------


def main(argv: list[str], checkout: str | None = None) -> int:
    checkout = checkout or CHECKOUT
    yes = dev = False
    for arg in argv:
        if arg in ("--yes", "-y"):
            yes = True
        elif arg == "--dev":
            dev = True
        elif arg in ("-h", "--help"):
            sys.stdout.write(__doc__)
            return 0
        else:
            sys.stderr.write(USAGE)
            return 2

    root = fleet_platform.running_as_root()
    say(f"fleet install: {checkout}")
    rows = plan_rows(dev, root)
    link, link_why = link_state(checkout)
    settings, command = claude_settings_file(), reconcile.hook_command(checkout)
    hook, hook_why = hook_state(settings, command)

    say()
    say("The plan")
    for row in rows:
        say(f"  {row.action:<9} {row.name:<14} {row.text}")
    if link != "ok":
        say(f"  {'link':<9} {LINK:<14} -> {TARGET} ({link_why})")
    if hook != "ok":
        say(f"  {'hook':<9} {'Stop nudge':<14} {settings} ({hook_why})")
    say(f"  {'then':<9} {'':<14} the thurbox extension and queue pane, then preflight")

    runs = [row for row in rows if row.argv]
    if not runs and link == "ok" and hook == "ok":
        say()
        say("Nothing to install.")
    elif not yes:
        if not sys.stdin.isatty():
            say()
            say("fleet install: nothing was installed — there is no terminal to ask. Run it again with")
            say("--yes to install the plan above.")
            return 1
        say()
        # The question ends its own line: a caller relaying output line by line
        # would otherwise show it only after the answer.
        say("Install everything above? [y/N]")
        if input().strip().lower() not in ("y", "yes"):
            say("Nothing was installed.")
            return 1

    failed = 0
    if runs:
        say()
        say("Installing")
        failed += sum(not execute(row, root) for row in runs)
        if fleet_platform.install_family() == "windows":
            fleet_platform.refresh_path()

    ok, message = make_link(checkout)
    failed += not ok
    say()
    say(f"Skills link: {message}")
    ok, message = apply_hook(settings, command)
    failed += not ok
    say(f"Stop nudge: {message}")

    still = [f.dependency.name for f in preflight.missing(["required"])]
    say()
    if still:
        say(f"Stopped before the extension: {', '.join(still)} still missing, and required.")
        failed += 1
    else:
        say("Thurbox extension")
        failed += extension_step(checkout) != 0

    say()
    say("Preflight")
    code = preflight.main([])
    if code or failed:
        say()
        say("fleet install did not finish; the output above says why. Once it is fixed, run it again:")
        say()
        say(f"  uv run --project {checkout} fleet install")
        return 1
    say(f"""
fleet is installed in {checkout}.

Next: open thurbox and start the Mission Control session. On its first
session it asks you, once, whether to put the queue pane on your screen.

  thurbox

Then run /fleet-onboarding in it for what this did not do: the GitHub owners
your map covers, the map itself, and the reconciler.""")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
