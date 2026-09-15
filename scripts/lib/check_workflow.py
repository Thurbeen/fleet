#!/usr/bin/env python3
"""The CI workflow keeps the three promises its `All Checks` gate rests on.

    check_workflow.py [<workflow>]    default: .github/workflows/ci.yml

1. `All Checks` needs EVERY other job. A branch ruleset requires only that one
   status, so a job missing from its `needs:` can fail while the pull request
   still reports green — the comment above it in ci.yml has said so since it
   was written, and nothing held it until this.
2. Every job has a `timeout-minutes`. GitHub's default is six hours, and a
   hung Windows step would hold the required status that long.
3. A job runs on `windows-latest`, directly or as a matrix entry, so a change
   that breaks fleet on Windows fails the pull request instead of the next
   Windows operator.
"""

import sys

import yaml

GATE = "all-checks"


def problems(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict) or GATE not in jobs:
        return [f"{path}: no `{GATE}` job"]

    found = []
    for name, job in jobs.items():
        # A job that calls a reusable workflow (`uses:`) cannot take a timeout;
        # GitHub rejects the key there, and the called workflow's jobs carry it.
        if "timeout-minutes" not in job and "uses" not in job:
            found.append(f"{path}: job `{name}` has no timeout-minutes")

    needs = jobs[GATE].get("needs") or []
    needs = [needs] if isinstance(needs, str) else needs
    for name in jobs:
        if name != GATE and name not in needs:
            found.append(f"{path}: `{GATE}` does not need job `{name}`")

    if not any(runs_on_windows(job) for job in jobs.values()):
        found.append(f"{path}: no job runs on windows-latest")
    return found


def runs_on_windows(job: dict) -> bool:
    """`runs-on: windows-latest`, or a matrix whose values list it."""
    runs_on = job.get("runs-on")
    if runs_on == "windows-latest":
        return True
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    if not isinstance(runs_on, str) or "matrix." not in runs_on or not isinstance(matrix, dict):
        return False
    values = [v for vs in matrix.values() if isinstance(vs, list) for v in vs]
    values += [v for entry in matrix.get("include") or [] if isinstance(entry, dict) for v in entry.values()]
    return "windows-latest" in values


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else ".github/workflows/ci.yml"
    found = problems(path)
    for line in found:
        print(f"::error file={path}::{line}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
