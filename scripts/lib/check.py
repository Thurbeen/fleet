#!/usr/bin/env python3
"""The control plane's whole gate, in one place, the same on Linux and Windows.

    uv run fleet check                   every check
    uv run fleet check yaml queue        only the named ones
    uv run fleet check --fix markdown    apply the fixes a check can apply
    uv run fleet check --list            every check: name, kind, what it runs

CI runs it on a Linux and a Windows runner, the prek hooks run it, and
`.publish.yaml` declares it as the gate the `publish` skill runs. One
definition means a green local run and a green CI run mean the same thing —
the failure this repo is most exposed to, because CI only fires on pull
requests while routine control-plane changes go straight to `main`.

TWO KINDS OF CHECK. A STATIC check reads tracked files: the lock, ruff, rumdl,
the YAML, the workflow's promises and the session profiles. A TESTS check is a
pytest area under `tests/`, each driving fleet's real entry points against
stub tools — except `architecture`, which parses fleet's own source, because
a rule like "only the platform seam reads the OS" is true of lines no run
reaches and false on nobody's machine until the next operator's. A full run
does every static check and then ONE pytest run over every area, so the stub
tools install once. `markdown` and `lint` have fixers; `--fix` is a no-op for
the rest, so it is always safe to pass.

IT READS NO OPERATOR STATE. One commit gets one verdict — on CI, in a worker's
worktree and in the control-plane checkout — so no check reads what a running
fleet wrote: the queue's records, the registry map, the gitignored
`orchestration/*.conf`, the reconciler's runtime, or the caller's HOME and git
config. Static checks read tracked files; every test runs inside
`tests/conftest.py`'s `isolated_env`; `isolation` proves both in a poisoned
checkout under a hostile host. The operator's live records are validated by
`uv run fleet status --records` instead.

A MISSING TOOL FAILS ITS CHECK rather than skipping it: a gate that passes
because its linter is absent passes on the machine that has the least.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# name -> (targets, tools the area needs on PATH, what it holds). Targets are
# globs under the checkout; `--list` prints what they expand to, and
# tests/test_check.py fails an area that expands to nothing.
TESTS = {
    "cli": (["tests/test_*.py"], ["uv"], "the fleet command, the harness, the platform seam and the gate itself"),
    "queue": (["tests/queue"], ["uv", "git"], "ordering, wake, collect, shepherd, hosts, refuel, forges, dispatch"),
    "reconcile": (["tests/reconcile"], ["uv"], "the loop: adoption, a durable stop, its clocks, one writer"),
    "status": (["tests/status"], ["uv", "git"], "fleet status degrades a section at a time and writes nothing"),
    "sync": (["tests/sync"], ["uv", "git"], "sync-checkout, sync-registry and add-owner"),
    "onboarding": (["tests/onboarding"], ["uv"], "preflight and discover-owners"),
    "pane": (["tests/pane"], ["uv", "lua"], "the queue pane renders, agrees with its docs, and is placed on request"),
    "extension": (["tests/extension"], ["uv"], "the manifest, the rendered payload and the first-run asks"),
    "install": (["tests/install"], ["uv", "git"], "the one-command install, converging and idempotent"),
    "skills": (["tests/skills"], [], "one skills tree, and every skill has a SKILL.md"),
    "architecture": (["tests/architecture"], [], "the seams hold in the source, including the branch this run never takes"),
    "automerge": (["tests/settings"], [], "no tracked setting names a repository, a tool or an agent"),
    "isolation": (["tests/isolation"], ["uv", "git"], "a poisoned checkout under a hostile host gets the same verdict"),
    "local": (["tests/local"], ["uv", "git"], "a fleet with no forge CLI and no login: preflight, the loop, the reconciler"),
}


def tracked(*patterns: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--", *patterns], cwd=REPO, capture_output=True, check=True).stdout
    return [f for f in out.decode("utf-8").split("\0") if f]


def run(argv: list[str]) -> int:
    return subprocess.run(argv, cwd=REPO).returncode


def missing(*tools: str) -> str | None:
    gone = [t for t in tools if not shutil.which(t)]
    return f"{', '.join(gone)} not found" if gone else None


def expand(targets: list[str]) -> list[str]:
    return sorted({os.path.relpath(p, REPO).replace(os.sep, "/") for t in targets for p in glob.glob(os.path.join(REPO, t))})


# --- static checks ------------------------------------------------------------


def check_lock(fix: bool) -> str | None:
    # First, because every `uv run --frozen` trusts uv.lock without noticing
    # one that no longer matches pyproject.toml.
    if err := missing("uv"):
        return err
    return "uv.lock does not match pyproject.toml — run `uv lock`" if run(["uv", "lock", "--check"]) else None


def check_lint(fix: bool) -> str | None:
    # The rules live in pyproject.toml, so no user-level ruff config decides.
    if err := missing("ruff"):
        return err
    return "ruff" if run(["ruff", "check", *(["--fix"] if fix else []), "fleet", "scripts", "tests"]) else None


def check_markdown(fix: bool) -> str | None:
    # An explicit file list, not a directory walk: rumdl walks nothing inside a
    # LINKED git worktree, where `.git` is a file, and every worker runs in one.
    if err := missing("rumdl") or missing("git"):
        return err
    files = tracked("*.md")
    if not files:
        return "no tracked *.md files found"
    if run(["rumdl", "check", *(["--fix"] if fix else []), *files]):
        return "rumdl"
    print(f"      rumdl clean ({len(files)} tracked files)")
    return None


def check_docs(fix: bool) -> str | None:
    # rumdl checks the prose, not where it points: a relative link to a file that
    # moved renders and 404s, and an SVG that does not parse renders as a broken
    # image. check_docs.py holds both for the README.
    if err := missing("git"):
        return err
    return "scripts/lib/check_docs.py README.md" if run([sys.executable, "scripts/lib/check_docs.py", "README.md"]) else None


def check_yaml(fix: bool) -> str | None:
    if err := missing("git"):
        return err
    files = tracked("*.yml", "*.yaml")
    if not files:
        return "no tracked *.yml/*.yaml files found"
    if run([sys.executable, "scripts/lib/check_yaml.py", *files]):
        return "scripts/lib/check_yaml.py"
    print(f"      {len(files)} tracked files parse")
    return None


def check_workflow(fix: bool) -> str | None:
    # All Checks is the one required status, so a job it does not need can fail
    # while the pull request reports green.
    return "scripts/lib/check_workflow.py" if run([sys.executable, "scripts/lib/check_workflow.py"]) else None


def check_profiles(fix: bool) -> str | None:
    # The two rules that keep a profile safe — no THURBOX_* key thurbox would
    # discard, no `command` without `reports_as` or `uncovered` — held where a
    # commit meets them.
    argv = [sys.executable, "scripts/lib/session_profiles.py", "orchestration/session-profiles.yaml", "--check"]
    return "session_profiles.py --check" if run(argv) else None


STATIC = {
    "lock": check_lock,
    "lint": check_lint,
    "markdown": check_markdown,
    "docs": check_docs,
    "yaml": check_yaml,
    "workflow": check_workflow,
    "profiles": check_profiles,
}
FIXERS = {"markdown", "lint"}


def check_tests(names: list[str]) -> str | None:
    tools = sorted({tool for name in names for tool in TESTS[name][1]})
    if err := missing(*tools):
        return err
    targets = expand([t for name in names for t in TESTS[name][0]])
    if not targets:
        return "no tests to run"
    return "pytest" if run([sys.executable, "-m", "pytest", "-q", *targets]) else None


def listing() -> str:
    rows = [f"{name}\tstatic\t-" for name in STATIC]
    rows += [f"{name}\ttests\t{' '.join(expand(targets)) or '-'}" for name, (targets, _, _) in TESTS.items()]
    return "".join(row + "\n" for row in rows)


def main(argv: list[str]) -> int:
    # Line-buffered, so each verdict lands after the tool output it judges.
    sys.stdout.reconfigure(line_buffering=True)
    everything = [*STATIC, *TESTS]
    usage = f"usage: fleet check [--fix] [--list] [check...]\nchecks: {' '.join(everything)}\n"
    if "-h" in argv or "--help" in argv:
        sys.stdout.write(__doc__.strip() + "\n\n" + usage)
        return 0
    if "--list" in argv:
        sys.stdout.write(listing())
        return 0
    fix = "--fix" in argv
    names = [a for a in argv if a != "--fix"] or everything
    unknown = [n for n in names if n not in STATIC and n not in TESTS]
    if unknown:
        sys.stderr.write(f"fleet check: no check named {', '.join(unknown)}\n{usage}")
        return 2

    verdicts: list[tuple[str, str | None]] = []
    for name in dict.fromkeys(n for n in names if n in STATIC):
        verdicts.append((name, STATIC[name](fix)))
        report(*verdicts[-1])
    areas = list(dict.fromkeys(n for n in names if n in TESTS))
    if areas:
        label = "tests" if len(areas) > 1 else areas[0]
        verdicts.append((label, check_tests(areas)))
        report(*verdicts[-1])
    failed = [name for name, err in verdicts if err]
    if failed:
        print(f"\033[31mFAIL\033[0m  {len(failed)} of {len(verdicts)}: {' '.join(failed)}", file=sys.stderr)
    return 1 if failed else 0


def report(name: str, err: str | None) -> None:
    if err:
        print(f"\033[31mFAIL\033[0m  {name}: {err}", file=sys.stderr)
    else:
        print(f"\033[32mok\033[0m    {name}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
