#!/usr/bin/env python3
"""The CI workflow keeps the four promises its `All Checks` gate rests on.

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
   ON EVERY RUNNER THAT RUNS THE GATE, and name nothing it does not. The gate
   is SHARDED across jobs to cut the wall clock, so the workflow now holds a
   copy of the area list — and a copy is a thing that goes stale. An area added
   to `check.py` and to no shard would never run on CI, and `All Checks` would
   still report green.

   Per runner, because covered in aggregate is not covered: a shard only the
   Linux job takes leaves promise 3 to a green status that never ran it, and
   "still works on Windows" is the property this matrix exists for.

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

# Where the gate's own words end and the next command begins, so a step that
# chains or redirects one is read for what it runs the gate with — not for
# `echo`, and not for the `2` in `2>&1`.
CHAINED = re.compile(r"&&|\|\||;|\||\d*>>?")


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

    # `.split()`: a runner is named by its labels, and `windows-latest` may be one of several.
    if not any("windows-latest" in runner.split() for job in jobs.values() for runner in runners(job)):
        found.append(f"{path}: no job runs on windows-latest")

    every = check_names()
    named = gate_names(jobs, every)
    if named is not None:
        for runner, names in sorted(named.items()):
            found += [f"{path}: nothing runs `fleet check {n}` on {runner}" for n in every if n not in names]
        invented = {n for names in named.values() for n in names} - set(every)
        found += [f"{path}: a job runs `fleet check {n}`, which is not a check" for n in sorted(invented)]
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


def gate_names(jobs: dict, every: list[str]) -> dict[str, set] | None:
    """Every check name the workflow passes to `fleet check`, per runner it runs on.

    A bare `fleet check` is every check there is, so it contributes all of them
    to its job's runners rather than excusing the workflow: a bare run on one
    runner says nothing about what the other one skipped.

    None when no step runs the gate at all, which is the one shape this promise
    cannot apply to.
    """
    named: dict[str, set] = {}
    for job in jobs.values():
        matrix = (job.get("strategy") or {}).get("matrix") or {}
        for step in job.get("steps") or []:
            run = step.get("run") if isinstance(step, dict) else None
            for line in (run or "").splitlines():
                _, gate, rest = line.partition("fleet check")
                if not gate:
                    continue
                for text in expand(CHAINED.split(rest)[0], matrix):
                    words = [w for w in text.split() if not w.startswith("-")]
                    for runner in runners(job):
                        named.setdefault(runner, set()).update(words or every)
    return named or None


def runners(job: dict) -> list[str]:
    """Every runner this job runs on, one entry each, named as `runs-on` names it.

    A LIST IS ONE RUNNER: `runs-on: [self-hosted, windows-latest]` picks a
    single machine carrying every label, so it is one entry spelling all of
    them, and not one runner per label that would each be asked to run the
    whole gate.

    A `runs-on` naming a matrix key that is not there stays as it is written,
    so an unresolved runner is reported under its own spelling rather than
    dropping the job — and with it every check that job was the only one to run.
    """
    runs_on = job.get("runs-on")
    if isinstance(runs_on, list):
        return [" ".join(str(v) for v in runs_on)]
    if not isinstance(runs_on, str):
        return []
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    return expand(runs_on, matrix if isinstance(matrix, dict) else {}) or [runs_on]



def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else ".github/workflows/ci.yml"
    found = problems(path)
    for line in found:
        print(f"::error file={path}::{line}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
