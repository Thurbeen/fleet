"""Everything fleet needs on this machine, in one pass, with the remedy for each.

WHY ONE TABLE. Onboarding used to probe these one at a time in prose, and an
operator learned about a missing tool one restart at a time while the list
drifted: `quota-axi` was load-bearing for refuel and the pane's fuel rows for
months while no setup document mentioned it. One table, one owner, and it is
DATA: `dependencies()` is the list of records, `missing()` the ones whose
probe fails, `package_manager()` the manager this machine has, and
`install_plan()` the argv that installs a record with it — so a second module
can act on exactly what this one reports.

IT WRITES NOTHING AND INSTALLS NOTHING. It probes, and prints the command that
would fix each gap. `--commands` hands those lines to whoever said yes.

THREE TIERS, because "missing" does not mean one thing:

  required     fleet cannot run. Missing one is a non-zero exit.
  recommended  a named capability degrades and the rest still works —
               so it is reported, never fatal.
  gate         only `uv run fleet check` needs it. A control plane that
               never pushes a change never needs these.

Usage:
  uv run fleet preflight                  # the table, grouped by tier
  uv run fleet preflight --commands       # just the install lines for what is missing
  uv run fleet preflight --tier required  # only that tier (repeatable)

`--tier` is what makes "install the required ones only" a command rather than
a judgement call about which lines to copy out of a longer list.

Exit: 0 when every REQUIRED dependency is present and authenticated, 1 when
one is not, 2 on a usage error. Recommended and gate gaps never fail it.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load_sibling(name: str, filename: str):
    """A module beside this file, keyed in sys.modules so every loader shares one copy."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(os.path.abspath(__file__)), filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Which OS family's routes apply is the platform seam's question, not this file's.
fleet_platform = _load_sibling("fleet_platform", "fleet_platform.py")
# The two authentication rows are PER ACCOUNT and PER HOST, through the seams
# the scripts that do the real reading share. See each row for why.
gh_accounts = _load_sibling("fleet_gh_accounts", "gh_accounts.py")
glab_hosts = _load_sibling("fleet_glab_hosts", "glab_hosts.py")

TIERS = ("required", "recommended", "gate")

# --- package managers ---------------------------------------------------------
#
# Each manager's executable and the argv that installs one package with it.
# ORDER is fixed and documented: winget is the Windows manager; elsewhere a
# distro's own manager comes before Homebrew, because a Linux machine that also
# carries brew still gets its system tools from the distro.

MANAGERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "winget": ("winget", ("winget", "install", "--id", "{}", "-e")),
    "apt": ("apt-get", ("sudo", "apt-get", "install", "-y", "{}")),
    "dnf": ("dnf", ("sudo", "dnf", "install", "-y", "{}")),
    "pacman": ("pacman", ("sudo", "pacman", "-S", "--needed", "{}")),
    "brew": ("brew", ("brew", "install", "{}")),
}
MANAGER_ORDER = {"windows": ("winget",), "posix": ("apt", "dnf", "pacman", "brew")}


@dataclass(frozen=True)
class Plan:
    """How one dependency gets installed: the argv, and the line an operator reads."""

    argv: tuple[str, ...]
    text: str


def shell(line: str) -> Plan:
    """A POSIX installer one-liner, which is a pipeline and so needs a shell."""
    return Plan(("sh", "-c", line), line)


def powershell(expr: str) -> Plan:
    """A Windows installer expression, runnable from cmd and PowerShell alike."""
    return Plan(("powershell", "-ExecutionPolicy", "ByPass", "-c", expr),
                f'powershell -ExecutionPolicy ByPass -c "{expr}"')


def command(*argv: str) -> Plan:
    return Plan(tuple(argv), " ".join(argv))


# --- the table ----------------------------------------------------------------


@dataclass(frozen=True)
class Dependency:
    """One row. A row is probed by `tool` on PATH (with `floor` when one exists)
    or decided by `check`; `needs` names a tool it is only probed after.

    A route to install it, in the order `install_plan` takes them: `packages`,
    one name per manager in MANAGERS; `installer`, the tool's own installer per
    OS family, used where no manager present carries it; `see`, a page to read
    when neither applies. `alternatives` records a package that exists and is
    NOT the recommended route. A row with `manual` is not a package at all: it
    is the command the operator runs themselves.
    """

    name: str
    tier: str
    why: str
    tool: str = ""
    floor: str = ""
    floor_why: str = ""
    version_args: tuple[str, ...] = ("--version", "-v")
    check: Callable[[], tuple[str, str, str]] | None = None
    needs: str = ""
    packages: Mapping[str, str] = field(default_factory=dict)
    alternatives: Mapping[str, str] = field(default_factory=dict)
    installer: Mapping[str, Plan] = field(default_factory=dict)
    manual: str = ""
    see: str = ""


@dataclass(frozen=True)
class Finding:
    """What probing one row found: `ok`, `missing` or `stale`."""

    dependency: Dependency
    state: str
    detail: str = ""
    manual: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def manifest_floor() -> str:
    """The thurbox floor. It has ONE owner, the manifest, which records why it sits there."""
    try:
        text = (REPO / "extension.toml.in").read_text(encoding="utf-8")
    except OSError:
        return ""
    found = re.search(r'^min_thurbox_version *= *"(.*)"', text, re.MULTILINE)
    return found.group(1) if found else ""


def dependencies(family: str | None = None) -> list[Dependency]:
    """Every dependency, in the order the table prints, for this OS family."""
    family = family or fleet_platform.install_family()
    table = [
        Dependency(
            "git", "required", "every checkout, and the worktree each worker gets", tool="git",
            packages={"winget": "Git.Git", "apt": "git", "dnf": "git", "pacman": "git", "brew": "git"},
            see="https://git-scm.com/downloads",
        ),
        Dependency(
            "gh", "required", "builds the repo map from registry/owners.txt, and is fleet's GitHub forge adapter",
            tool="gh",
            packages={"winget": "GitHub.cli", "apt": "gh", "dnf": "gh", "pacman": "github-cli", "brew": "gh"},
            see="https://cli.github.com",
        ),
        Dependency(
            "gh auth", "required", "the registry sync reads GitHub as you — EVERY account, no PAT, no CI secret",
            needs="gh", check=_gh_auth, manual="gh auth login",
        ),
        Dependency(
            "uv", "required",
            "runs `fleet`, with the Python and PyYAML uv.lock pins and the gate's rumdl, ruff and pytest",
            tool="uv", packages={"winget": "astral-sh.uv", "pacman": "uv", "brew": "uv"},
            installer={"posix": shell("curl -LsSf https://astral.sh/uv/install.sh | sh"),
                       "windows": powershell("irm https://astral.sh/uv/install.ps1 | iex")},
        ),
        # thurbox's own docs make its installer the recommended route on both
        # families, because the winget package trails releases: winget's is
        # recorded as the alternative and never chosen.
        Dependency(
            "thurbox-cli", "required", "the sessions fleet spawns, the extension, and the queue pane all live in it",
            tool="thurbox-cli", floor=manifest_floor(), floor_why="extension.toml.in sets the floor at {floor} and says why",
            packages={"brew": "thurbeen/thurbox/thurbox"}, alternatives={"winget": "Thurbeen.thurbox"},
            installer={
                "posix": shell("curl -fsSL https://raw.githubusercontent.com/Thurbeen/thurbox/main/scripts/install.sh | sh"),
                "windows": powershell("irm https://raw.githubusercontent.com/Thurbeen/thurbox/main/scripts/install.ps1 | iex"),
            },
            see="https://github.com/Thurbeen/thurbox",
        ),
    ]
    if family == "windows":
        table.append(Dependency(
            "psmux", "required", "the multiplexer thurbox runs every session in on native Windows", tool="psmux",
            version_args=("-V",), packages={"winget": "marlocarlo.psmux"}, see="https://github.com/psmux/psmux",
        ))
    else:
        table.append(Dependency(
            "tmux", "required", "the multiplexer thurbox runs every session in", tool="tmux",
            floor="3.2", floor_why="thurbox needs tmux {floor} or newer", version_args=("-V",),
            packages={"apt": "tmux", "dnf": "tmux", "pacman": "tmux", "brew": "tmux"},
            see="https://github.com/tmux/tmux/wiki/Installing",
        ))
    table += [
        Dependency(
            "quota-axi", "recommended",
            "the fuel the pane and `uv run fleet status` draw, and the account window "
            "`uv run fleet queue refuel` checks before it restarts a worker",
            tool="quota-axi",
            installer={"posix": command("npm", "install", "-g", "quota-axi"),
                       "windows": command("npm", "install", "-g", "quota-axi")},
        ),
        Dependency(
            "glab", "recommended", "fleet's GitLab forge adapter; nothing needs it until a task's repo lives on GitLab",
            tool="glab",
            packages={"winget": "GLab.GLab", "apt": "glab", "dnf": "glab", "pacman": "glab", "brew": "glab"},
            see="https://gitlab.com/gitlab-org/cli",
        ),
        Dependency(
            "glab auth", "recommended",
            "a merge request is read with a credential for ITS host, not for every host glab knows",
            needs="glab", check=_glab_auth, manual="glab auth login   # it asks which instance",
        ),
        Dependency(
            "lua", "gate", "`uv run fleet check pane` renders the queue pane offline with it", tool="lua",
            packages={"winget": "DEVCOM.Lua", "apt": "lua5.4", "dnf": "lua", "pacman": "lua", "brew": "lua"},
            see="https://www.lua.org/download.html",
        ),
        Dependency(
            "prek", "gate", "the pre-commit hooks in .pre-commit-config.yaml, which run the same gate", tool="prek",
            packages={"winget": "j178.Prek", "brew": "prek"},
            installer={"posix": command("uv", "tool", "install", "prek"),
                       "windows": command("uv", "tool", "install", "prek")},
        ),
        Dependency(
            "commit signing", "gate",
            "git commit signing is on with no key outside this checkout, so every commit in a repo "
            "that key does not cover fails — any sandbox or worktree",
            needs="git", check=_signing,
            manual="git config --global user.signingkey <key>   # or: commit.gpgsign false",
        ),
    ]
    return table


# --- probing ------------------------------------------------------------------

VERSION = re.compile(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?")


def _output(argv: list[str]) -> str:
    """What `argv` prints on stdout when it succeeds, else ""."""
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace", check=False)
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _succeeds(argv: list[str]) -> bool:
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, check=False).returncode == 0
    except OSError:
        return False


def version_of(path: str, flags: Iterable[str]) -> str:
    """The first version-shaped token a tool prints in its first three lines.

    Stdin is closed, so a tool that reads it gets an empty column, not a stall.
    """
    for flag in flags:
        try:
            done = subprocess.run([path, flag], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", check=False)
        except OSError:
            continue
        found = VERSION.search("\n".join(done.stdout.splitlines()[:3]))
        if found:
            return found.group(0)
    return ""


def _older(have: str, floor: str) -> bool:
    def parts(v: str) -> list[int]:
        return [int(p) for p in v.split(".")]

    a, b = parts(have), parts(floor)
    width = max(len(a), len(b))
    return a + [0] * (width - len(a)) < b + [0] * (width - len(b))


def _gh_auth() -> tuple[str, str, str]:
    """ASKED PER ACCOUNT, because `gh auth status` is all-or-nothing across every
    login: one lapsed token among three reported this REQUIRED row missing while
    two working accounts sat there. The seam's own warning about each skipped
    login is passed through verbatim, so a thinner answer is never silent.

    GitHub unless GH_HOST says otherwise, the variable `gh api` itself obeys.
    """
    heard = io.StringIO()
    with contextlib.redirect_stderr(heard):
        logins = gh_accounts.accounts(os.environ.get("GH_HOST") or "github.com")
    sys.stderr.write(heard.getvalue())
    if logins:
        total = len(logins) + len(heard.getvalue().splitlines())
        detail = ", ".join(logins)
        return "ok", (f"{len(logins)} of {total} accounts — {detail}" if total > 1 else detail), ""
    # The fallback the seam documents: no account list (an older gh, or a
    # GH_TOKEN that overrides every stored login) asks the ACTIVE session alone.
    if _succeeds(["gh", "auth", "status"]):
        return "ok", _output(["gh", "api", "user", "--jq", ".login"]), ""
    return "missing", "", ""


def _glab_auth() -> tuple[str, str, str]:
    """ASKED PER HOST, because `glab auth status` is all-or-nothing across every
    instance: an operator authenticated to their company's GitLab and not to
    gitlab.com read `missing` beside a remedy they had already run. A
    self-hosted instance is the ORDINARY case for this row.
    """
    host = os.environ.get("GITLAB_HOST")
    if host:
        # glab's own variable for which instance to talk to: that one must work.
        if glab_hosts.host_ok(host):
            return "ok", host, ""
        return "missing", "", f"glab auth login --hostname {host}"
    configured = glab_hosts.hosts()
    working = [h for h in configured if glab_hosts.host_ok(h)]
    if working:
        # One working credential is the whole question: fleet reaches a GitLab
        # repository by HOST plus path.
        return "ok", ", ".join(working), ""
    if not configured and _succeeds(["glab", "auth", "status"]):
        # An older glab with no `--all` enumerates nothing.
        return "ok", "", ""
    return "missing", "", ""


def _signing() -> tuple[str, str, str]:
    """NOT A TOOL — the configuration whose failures name anything but itself.

    Read from a directory OUTSIDE this checkout: an `includeIf gitdir:` block can
    set the key for the operator's code tree and nowhere else, so asking git
    from in here answers about the wrong place.
    """
    with tempfile.TemporaryDirectory() as probe:
        def get(key: str) -> str:
            return _output(["git", "-C", probe, "config", "--get", key])

        on, key, key_command = get("commit.gpgsign"), get("user.signingkey"), get("gpg.ssh.defaultKeyCommand")
    return ("missing" if on == "true" and not key and not key_command else "ok"), "", ""


def probe(dependency: Dependency) -> Finding | None:
    """Probe one row; None when the tool it needs is absent, since that row already says so."""
    if dependency.needs and not shutil.which(dependency.needs):
        return None
    if dependency.check:
        state, detail, manual = dependency.check()
        return Finding(dependency, state, detail, manual or dependency.manual)
    path = shutil.which(dependency.tool)
    if not path:
        return Finding(dependency, "missing", manual=dependency.manual)
    have = version_of(path, dependency.version_args)
    if dependency.floor and have and _older(have, dependency.floor):
        return Finding(dependency, "stale", have, dependency.manual)
    return Finding(dependency, "ok", have, dependency.manual)


def findings(tiers: Iterable[str] | None = None) -> list[Finding]:
    """Every row probed, in table order, limited to `tiers` when given."""
    wanted = set(tiers or TIERS)
    return [f for d in dependencies() if d.tier in wanted and (f := probe(d))]


def missing(tiers: Iterable[str] | None = None) -> list[Finding]:
    """The rows whose probe fails — missing or stale — limited to `tiers` when given."""
    return [f for f in findings(tiers) if not f.ok]


def package_manager(family: str | None = None) -> str | None:
    """The first manager in MANAGER_ORDER for this OS family that is on PATH."""
    family = family or fleet_platform.install_family()
    return next((m for m in MANAGER_ORDER.get(family, ()) if shutil.which(MANAGERS[m][0])), None)


def install_plan(dependency: Dependency, manager: str | None, family: str | None = None) -> Plan | None:
    """The argv that installs `dependency` with `manager`, else its own installer
    for this family, else None — which is also the answer for a `manual` row."""
    if dependency.manual:
        return None
    if manager in dependency.packages and manager in MANAGERS:
        argv = tuple(dependency.packages[manager] if part == "{}" else part for part in MANAGERS[manager][1])
        return Plan(argv, " ".join(argv))
    return dependency.installer.get(family or fleet_platform.install_family())


def remedy(finding: Finding, manager: str | None) -> str:
    """What the table prints after `install:` for a gap."""
    if finding.manual:
        return finding.manual
    plan = install_plan(finding.dependency, manager)
    if plan:
        return plan.text
    return f"see {finding.dependency.see}" if finding.dependency.see else ""


# --- output -------------------------------------------------------------------

HEADINGS = {
    "required": "REQUIRED — fleet cannot run without these",
    "recommended": "RECOMMENDED — each one names what degrades without it",
    "gate": "GATE — only `uv run fleet check` needs these",
}
USAGE = "usage: uv run fleet preflight [--commands] [--tier required|recommended|gate]...\n"


def main(argv: list[str]) -> int:
    commands, tiers = False, []
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--commands":
            commands = True
        elif arg == "--tier":
            tier = args.pop(0) if args else ""
            if tier not in TIERS:
                sys.stderr.write("usage: --tier required|recommended|gate\n")
                return 2
            tiers.append(tier)
        elif arg in ("-h", "--help"):
            sys.stdout.write(__doc__)
            return 0
        else:
            sys.stderr.write(USAGE)
            return 2

    # Every tier is probed whatever was asked to be SHOWN: a required gap is
    # still one when the caller only asked to see the gate tools, and a
    # preflight that passed because of how it was queried would be worse than
    # none.
    found = findings()
    shown = [f for f in found if not tiers or f.dependency.tier in tiers]
    required_missing = sum(1 for f in found if f.dependency.tier == "required" and not f.ok)
    manager = package_manager()

    if commands:
        # Only the lines that would change something, in table order, once each.
        printed: list[str] = []
        for f in shown:
            line = "" if f.ok else remedy(f, manager)
            if line and not line.startswith("see ") and line not in printed:
                printed.append(line)
                print(line)
        return 1 if required_missing else 0

    colour = {"ok": "\033[32mok\033[0m      ", "stale": "\033[33mstale\033[0m   ", "missing": "\033[31mmissing\033[0m "}
    for tier in TIERS:
        if tiers and tier not in tiers:
            continue
        print(f"\n{HEADINGS[tier]}")
        for f in (f for f in shown if f.dependency.tier == tier):
            d = f.dependency
            if f.state == "ok":
                text = f.detail
            elif f.state == "stale":
                text = f"{f.detail} — {d.floor_why.format(floor=d.floor)}"
            else:
                text = d.why
            print(f"  {colour[f.state]} {d.name:<14} {text}".rstrip())
            if not f.ok and (line := remedy(f, manager)):
                print(f"           {'':<14} install: {line}")
    print()
    if required_missing == 0:
        print('Every required dependency is present. "--commands" lists the\n'
              "install lines for anything above that is not.")
    else:
        noun = "dependencies" if required_missing > 1 else "dependency"
        print(f"{required_missing} required {noun} missing. Install before onboarding\n"
              "writes anything: a half-onboarded clone is worse than one that\n"
              "never started.\n\n  uv run fleet preflight --commands")
    return 1 if required_missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
