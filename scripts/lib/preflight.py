"""Everything fleet needs on this machine, in one pass, with the remedy for each.

WHY ONE TABLE. Onboarding used to probe these one at a time in prose, and an
operator learned about a missing tool one restart at a time while the list
drifted: `quota-axi` was load-bearing for refuel and the pane's fuel rows for
months while no setup document mentioned it. One table, one owner, and it is
DATA: `dependencies()` is the list of records, `missing()` the ones whose
probe fails, `package_manager()` the manager this machine has, and
`install_plan()` the argv that installs a record with it — so a second module
can act on exactly what this one reports.

IT KEEPS NOTHING AND INSTALLS NOTHING. It probes, and prints the command that
would fix each gap. The lead tier's probes make a throwaway commit and a
throwaway file, and delete both. `--commands` hands those lines to whoever said yes.

FIVE TIERS, because "missing" does not mean one thing:

  required     fleet cannot run. Missing one is a non-zero exit.
  recommended  a named capability degrades and the rest still works —
               so it is reported, never fatal.
  lead         not a tool: this machine's own environment, which every
               worker and the loop inherit — a signing agent that answers,
               an agent `refuel` can read, the host glab talks to, and
               directories fleet can write. Each one failed silently, one
               blocked worker at a time, so each row is probed by DOING the
               thing. Never fatal, never installed, and never in
               `--commands`: the remedy is the operator's own configuration.
               `uv run fleet status` prints this tier's gaps as MACHINE.
  forge        OPTIONAL. Each row names what it adds: the repo map, publish
               checks on change requests, shepherd merges. A local-only fleet
               — no forge CLI, no login — runs everything else.
  gate         only `uv run fleet check` needs it. A control plane that
               never pushes a change never needs these.

Usage:
  uv run fleet preflight                  # the table, grouped by tier
  uv run fleet preflight --commands       # just the install lines for what is missing
  uv run fleet preflight --tier required  # only that tier (repeatable)
  uv run fleet preflight --tier forge     # what a forge would add, and how
  uv run fleet preflight --tier lead      # this machine's environment alone

`--tier` is what makes "install the required ones only" a command rather than
a judgement call about which lines to copy out of a longer list.

Exit: 0 when every REQUIRED dependency is present, 1 when one is not, 2 on a
usage error. Recommended, lead, forge and gate gaps never fail it.
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

TIERS = ("required", "recommended", "lead", "forge", "gate")

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


def powershell(url: str) -> Plan:
    """Download an installer, then run its file in a child PowerShell."""
    helper = REPO / "scripts" / "lib" / "run_installer.ps1"
    argv = ("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(helper), url)
    return Plan(argv, f'powershell -NoProfile -ExecutionPolicy Bypass -File "{helper}" "{url}"')


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
            "uv", "required",
            "runs `fleet`, with the Python and PyYAML uv.lock pins and the gate's rumdl, ruff and pytest",
            tool="uv", packages={"winget": "astral-sh.uv", "pacman": "uv", "brew": "uv"},
            installer={"posix": shell("curl -LsSf https://astral.sh/uv/install.sh | sh"),
                       "windows": powershell("https://astral.sh/uv/install.ps1")},
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
                "windows": powershell("https://raw.githubusercontent.com/Thurbeen/thurbox/main/scripts/install.ps1"),
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
        # The forge tier: nothing here is needed to run fleet. A local-only
        # fleet dispatches against local repos and proves `push` and `none`
        # tasks with git alone; each row says what that forge adds.
        Dependency(
            "gh", "forge",
            "adds GitHub: the repo map from registry/owners.txt, pull request publish checks, and shepherd merges",
            tool="gh",
            packages={"winget": "GitHub.cli", "apt": "gh", "dnf": "gh", "pacman": "github-cli", "brew": "gh"},
            see="https://cli.github.com",
        ),
        Dependency(
            "gh auth", "forge", "lets gh read GitHub as you — EVERY account, no PAT, no CI secret",
            needs="gh", check=_gh_auth, manual="gh auth login",
        ),
        Dependency(
            "glab", "forge", "adds GitLab: merge request publish checks and shepherd merges for a repo that lives there",
            tool="glab",
            packages={"winget": "GLab.GLab", "apt": "glab", "dnf": "glab", "pacman": "glab", "brew": "glab"},
            see="https://gitlab.com/gitlab-org/cli",
        ),
        Dependency(
            "glab auth", "forge",
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
        # The lead tier. Every row is a `check` with a `manual` remedy, and
        # each one is probed by doing what a worker or the loop will do.
        Dependency(
            "commit signing", "lead",
            "git commit signing is on and a commit made outside this checkout, with no terminal, "
            "cannot be signed — so every worker's commit fails, in any sandbox or worktree",
            needs="git", check=_signing,
            manual="git config --global user.signingkey <key>   # or: commit.gpgsign false",
        ),
        Dependency(
            "refuel agent", "lead",
            "no AGENT, FUEL_PROVIDER or agent policy names an agent, so `fleet queue refuel` cannot "
            "read a worker's quota window and restarts nothing",
            check=_refuel_agent, manual="set AGENT=<the agent your workers run> in orchestration/agent.conf",
        ),
        Dependency(
            "glab host", "lead",
            "outside a repository glab talks to its default host, and that host has no working "
            "credential — every glab call there is a 401",
            needs="glab", check=_glab_host, manual="glab config set host <your instance> --global",
        ),
        Dependency(
            "fleet writes", "lead",
            "this process cannot write where `fleet queue` keeps its records — `collect`, `dispatch` "
            "and `add` fail one call at a time, as a sandbox that denies fleet's own writes makes them",
            check=_fleet_writes, manual="allow writes to the queue in the sandbox this agent runs commands in",
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


SIGNING_SECONDS = 20

# What fixes a signature that could not be made, per `gpg.format`. A worker has
# no terminal, so a key that needs a passphrase typed is a key it cannot use.
SIGNING_FIX = {
    "ssh": "ssh-add -l   # must list your signing key; if not, export SSH_AUTH_SOCK=<the agent "
           "holding it> in the environment thurbox starts its sessions with",
    "openpgp": "gpg-connect-agent /bye   # the agent must hold the key unlocked: a worker has "
               "no terminal to type its passphrase in",
}


def _signing() -> tuple[str, str, str]:
    """NOT A TOOL — the configuration whose failures name anything but itself.

    Asked in a throwaway repository OUTSIDE this checkout: an `includeIf
    gitdir:` block can set the key for the operator's code tree and nowhere
    else, so asking git from in here answers about the wrong place.

    A key being configured is not the question. The agent holding it went away
    on a crash or a reboot and every worker's commit failed while the
    configuration read fine, so the probe SIGNS a commit — with no terminal, as
    a worker has none, so a passphrase prompt fails here rather than waiting.
    The throwaway repository is deleted with its commit; nothing else is written.
    """
    with tempfile.TemporaryDirectory() as probe:
        def get(key: str) -> str:
            return _output(["git", "-C", probe, "config", "--get", key])

        if get("commit.gpgsign") != "true":
            return "ok", "signing off", ""
        if not get("user.signingkey") and not get("gpg.ssh.defaultKeyCommand"):
            return "missing", "", ""
        form = get("gpg.format") or "openpgp"
        fix = SIGNING_FIX.get(form, "")
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        try:
            subprocess.run(["git", "-C", probe, "init", "-q"], stdin=subprocess.DEVNULL,
                           capture_output=True, check=True, timeout=SIGNING_SECONDS)
            done = subprocess.run(
                ["git", "-C", probe, "-c", "user.name=fleet preflight", "-c", "user.email=preflight@localhost",
                 "-c", f"core.hooksPath={os.path.join(probe, 'no-hooks')}",
                 "commit", "--allow-empty", "--no-verify", "-q", "-m", "fleet preflight"],
                stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace",
                env=env, timeout=SIGNING_SECONDS, **fleet_platform.no_terminal(),
            )
        except subprocess.TimeoutExpired:
            return "missing", f"signing did not finish in {SIGNING_SECONDS}s", fix
        except (OSError, subprocess.CalledProcessError):
            return "ok", "not probed: git could not make a repository to sign in", ""
    if done.returncode == 0:
        return "ok", f"a {form} signature made with no terminal", ""
    said = [line.strip() for line in done.stderr.splitlines() if line.strip()]
    useful = [line for line in said if "failed to write commit object" not in line]
    return "missing", (useful or said or ["git commit failed"])[0], fix


def _queue():
    """queue.py, loaded on first use: the rows below ask it, and no other row needs it."""
    return _load_sibling("fleet_queue", "queue.py")


def _refuel_agent() -> tuple[str, str, str]:
    """Which agent a worker runs is what `refuel` reads its quota window by.

    `dispatch` records the agent it named, and names none when `AGENT` is empty
    — thurbox's own default, which is fine for spawning and leaves `refuel`
    with no provider to read. A pinned `FUEL_PROVIDER` or an agent policy
    answers instead. Asked through queue.py, the same reads `refuel` makes.
    """
    queue = _queue()
    settings = queue.agent_settings
    agent = queue.configured_agent()
    if agent:
        return "ok", f"AGENT={agent}", ""
    provider = settings.conf().get("FUEL_PROVIDER", "").strip()
    if provider:
        return "ok", f"FUEL_PROVIDER={provider}", ""
    if queue.agent_policy():
        return "ok", "the agent policy names each repository's agent", ""
    root = settings.conf_root()
    conf = os.path.join(root, settings.AGENT_CONF)
    if os.path.exists(conf):
        return "missing", "", f"set AGENT=<the agent your workers run> in {conf}"
    example = os.path.join(root, settings.AGENT_CONF_DEFAULTS)
    return "missing", "", f'cp "{example}" "{conf}"   # then set AGENT=<the agent your workers run>'


def _glab_host() -> tuple[str, str, str]:
    """The host glab falls back to outside a repository must be one it can log in to.

    Only asked when some instance has a working credential: with none at all,
    `glab auth` is the row that says so.
    """
    working = [h for h in glab_hosts.hosts() if glab_hosts.host_ok(h)]
    default = glab_hosts.default_host()
    if not working or not default or default in working:
        return "ok", default, ""
    if os.environ.get("GITLAB_HOST") or os.environ.get("GL_HOST"):
        return "missing", f"it is {default}", f"export GITLAB_HOST={working[0]}"
    return "missing", f"it is {default}", f"glab config set host {working[0]} --global"


def _fleet_writes() -> tuple[str, str, str]:
    """Write a file where the queue writes, and remove it.

    Probed by writing, because only the write is the answer: a sandbox's rules
    live in an agent's own settings, which fleet does not read, and this
    process runs under them when the lead runs it. A directory not made yet is
    not asked — `fleet queue` creates it.
    """
    queue = _queue()
    denied = []
    for where in dict.fromkeys((queue.queue_root(), queue.runs_root())):
        if not os.path.lexists(where):
            continue
        try:
            seen = os.stat(where)
            with tempfile.NamedTemporaryFile(dir=where, prefix=".fleet-preflight-"):
                pass
        except OSError as exc:
            denied.append(f"{where} ({exc.strerror or exc})")
            continue
        # The directory's times are put back: the queue root's mtime is when
        # the pane says the records last changed. Setting them needs ownership,
        # which a write does not, so failing here is not a denied write.
        with contextlib.suppress(OSError):
            os.utime(where, ns=(seen.st_atime_ns, seen.st_mtime_ns))
    if not denied:
        return "ok", "", ""
    return "missing", "; ".join(denied), (
        "allow writes to the directories above in the sandbox this agent runs commands in, "
        "or run fleet outside it")


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
    "forge": "FORGE — optional: each one names what it adds, and a local-only fleet needs none",
    "lead": "LEAD — this machine's environment, which every worker and the loop inherit",
    "gate": "GATE — only `uv run fleet check` needs these",
}
USAGE = "usage: uv run fleet preflight [--commands] [--tier required|recommended|lead|forge|gate]...\n"


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
                sys.stderr.write("usage: --tier required|recommended|lead|forge|gate\n")
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
        # Only the lines that would change something, in table order, once
        # each. A lead row's remedy is the operator's configuration, with
        # placeholders in it: one to read, never to run unread.
        printed: list[str] = []
        for f in shown:
            line = "" if f.ok or f.dependency.tier == "lead" else remedy(f, manager)
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
                text = f"{d.why} ({f.detail})" if f.detail else d.why
            print(f"  {colour[f.state]} {d.name:<14} {text}".rstrip())
            if not f.ok and (line := remedy(f, manager)):
                label = "fix" if d.tier == "lead" else "install"
                print(f"           {'':<14} {label}: {line}")
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
