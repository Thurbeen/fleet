#!/usr/bin/env python3
"""Every tracked YAML file parses, and the registry has the shape the control
plane reads.

Called by scripts/check.sh (and through it by CI, prek and the no-mistakes
lint step) with the list of tracked *.yml/*.yaml files as argv, never on its
own. It lives in a file rather than a CI heredoc so the local gate and the
pull-request gate run the same assertions — CI here only fires on pull
requests, while routine control-plane changes go straight to `main`, so the
local run is the one that has to be trustworthy.

Takes the file list from argv (check.sh builds it with `git ls-files`) rather
than walking the filesystem itself: a glob silently skips dot-prefixed paths
like `.github/` and `.no-mistakes.yaml` unless every segment is spelled out,
which previously let this check report a clean tree while parsing almost none
of it.
"""

import os
import sys

import yaml


def main() -> int:
    paths = sys.argv[1:]
    bad = False

    for path in paths:
        try:
            with open(path) as fh:
                list(yaml.safe_load_all(fh))
        except Exception as exc:  # noqa: BLE001 — report every parse failure
            print(f"::error file={path}::{exc}")
            bad = True

    if bad:
        return 1

    registry = "registry/repos.generated.yaml"
    # The map is generated from the operator's own `gh` session and gitignored,
    # so it is absent in a fresh clone and in any that has not synced yet.
    # That is a normal state, not a failure — there is simply nothing to assert.
    if not os.path.exists(registry):
        print(f"registry: {registry} not present (not synced yet) — nothing to check")
        return 0

    doc = yaml.safe_load(open(registry))
    owners = doc.get("owners")
    assert isinstance(owners, list), f"{registry}: owners missing or not a list"
    assert isinstance(
        doc.get("totals", {}).get("repos"), int
    ), f"{registry}: totals.repos missing"
    for owner in owners:
        assert owner.get("name") and isinstance(
            owner.get("repos"), list
        ), f"{registry}: bad owner: {owner}"

    print(f"registry ok: {doc['totals']['repos']} repos across {len(owners)} owners")
    return 0


if __name__ == "__main__":
    sys.exit(main())
