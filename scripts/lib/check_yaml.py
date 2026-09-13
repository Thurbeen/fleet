#!/usr/bin/env python3
"""Every tracked YAML file parses — and, asked separately, a registry map has
the shape the control plane reads.

    check_yaml.py <file>...           every file parses (scripts/check.sh)
    check_yaml.py --registry <map>    that map has the registry's shape

The first form is called by scripts/check.sh (and through it by CI, prek and
the no-mistakes lint step) with the list of tracked *.yml/*.yaml files as
argv. It lives in a file rather than a CI heredoc so the local gate and the
pull-request gate run the same assertions — CI here only fires on pull
requests, while routine control-plane changes go straight to `main`, so the
local run is the one that has to be trustworthy.

Takes the file list from argv (check.sh builds it with `git ls-files`) rather
than walking the filesystem itself: a glob silently skips dot-prefixed paths
like `.github/` and `.no-mistakes.yaml` unless every segment is spelled out,
which previously let this check report a clean tree while parsing almost none
of it.

THE GATE NEVER READS THE OPERATOR'S MAP. `registry/repos.generated.yaml` is
generated from the operator's own `gh` sessions and gitignored, so a gate that
validated it gave one commit a different verdict in the control-plane checkout
than on CI. The shape is still proven, twice: scripts/onboarding-selftest.sh
generates a map with sync-registry.sh against stubs and holds it to
`--registry`, and scripts/fleet-status.sh reads the operator's own map through
registry_problems() and reports what it finds.
"""

import sys

import yaml


def registry_problems(path: str) -> tuple[str, list[str]]:
    """(a one-line summary, every way the map at `path` has the wrong shape)."""
    try:
        with open(path) as fh:
            doc = yaml.safe_load(fh)
    except Exception as exc:  # noqa: BLE001 — an unreadable map is a problem
        return "", [f"{path}: {exc}"]
    if not isinstance(doc, dict):
        return "", [f"{path}: not a mapping"]

    problems = []
    owners = doc.get("owners")
    if not isinstance(owners, list):
        problems.append(f"{path}: owners missing or not a list")
        owners = []
    totals = doc.get("totals")
    if not isinstance(totals, dict) or not isinstance(totals.get("repos"), int):
        problems.append(f"{path}: totals.repos missing")
    for owner in owners:
        if not (isinstance(owner, dict) and owner.get("name") and isinstance(owner.get("repos"), list)):
            problems.append(f"{path}: bad owner: {owner}")
    if problems:
        return "", problems
    return f"{totals['repos']} repos across {len(owners)} owners", []


def main() -> int:
    if sys.argv[1:2] == ["--registry"]:
        if len(sys.argv) != 3:
            print("usage: check_yaml.py --registry <map>", file=sys.stderr)
            return 2
        summary, problems = registry_problems(sys.argv[2])
        for line in problems:
            print(f"::error file={sys.argv[2]}::{line}")
        if problems:
            return 1
        print(f"registry ok: {summary}")
        return 0

    bad = False
    for path in sys.argv[1:]:
        try:
            with open(path) as fh:
                list(yaml.safe_load_all(fh))
        except Exception as exc:  # noqa: BLE001 — report every parse failure
            print(f"::error file={path}::{exc}")
            bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
