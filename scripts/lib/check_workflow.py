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
4. The names the jobs pass to `fleet check` cover every check `check.py` knows,
   and name nothing it does not. The gate is SHARDED across jobs to cut the
   wall clock, so the workflow now holds a copy of the area list — and a copy
   is a thing that goes stale. An area added to `check.py` and to no shard
   would never run on CI, and `All Checks` would still report green.

   A bare `uv run fleet check` runs every check, so a workflow that only ever
   calls it that way keeps this promise with nothing to list.
"""

import importlib.util
import os
import re
import sys

import yaml

GATE = "all-checks"

# `${{ matrix.shard.areas }}` and friends, which is how a sharded job says
# which checks it runs.
MATRIX_REF = re.compile(r"\$\{\{\s*matrix\.([A-Za-z0-9_.-]+)\s*\}\}")


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

    named = gate_names(jobs)
    if named is not None:
        every = check_names()
        found += [f"{path}: no job runs `fleet check {name}`" for name in every if name not in named]
        found += [f"{path}: a job runs `fleet check {name}`, which is not a check" for name in sorted(named - set(every))]
    return found


def check_names() -> list[str]:
    """Every check name the gate knows, read from check.py beside this file."""
    name = "fleet_check"
    module = sys.modules.get(name)
    if module is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check.py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return [*module.STATIC, *module.TESTS]


def expand(text: str, matrix: dict) -> list[str]:
    """`text` once per value of each matrix reference in it.

    One reference at a time, which is what a shard line holds; two would need
    their product, and no job here writes one.
    """
    ref = MATRIX_REF.search(text)
    if not ref:
        return [text]
    values = matrix_values(matrix, ref.group(1))
    return [text[:ref.start()] + value + text[ref.end():] for value in values]


def matrix_values(matrix: dict, path: str) -> list[str]:
    """Every value `matrix.<path>` takes: a key, or a key and one field of it."""
    key, _, field = path.partition(".")
    entries = matrix.get(key) if isinstance(matrix.get(key), list) else []
    entries = [*entries, *(e.get(key) for e in matrix.get("include") or [] if isinstance(e, dict) and key in e)]
    if not field:
        return [str(e) for e in entries if not isinstance(e, (dict, list))]
    return [str(e[field]) for e in entries if isinstance(e, dict) and field in e]


def gate_names(jobs: dict) -> set | None:
    """Every check name the workflow passes to `fleet check`.

    None when the promise does not apply: no step runs the gate, or one runs it
    bare, which is every check and leaves nothing to list.
    """
    named = set()
    ran = False
    for job in jobs.values():
        matrix = (job.get("strategy") or {}).get("matrix") or {}
        for step in job.get("steps") or []:
            run = step.get("run") if isinstance(step, dict) else None
            for line in (run or "").splitlines():
                _, gate, rest = line.partition("fleet check")
                if not gate:
                    continue
                ran = True
                for text in expand(rest, matrix):
                    words = [w for w in text.split() if not w.startswith("-")]
                    if not words:
                        return None
                    named.update(words)
    return named if ran else None


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
