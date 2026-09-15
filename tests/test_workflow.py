"""The CI workflow keeps the promises its `All Checks` gate rests on.

`scripts/lib/check_workflow.py` holds three: `All Checks` needs every other
job, every job has a timeout, and a job runs on Windows. The last one has two
spellings, a literal `windows-latest` and a matrix that lists it, and the gate
runs as a matrix over both runners.
"""

from harness import lib, write

GATE = """
  all-checks:
    needs: [check]
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps: [{run: "true"}]
"""


def problems(tmp_path, jobs: str) -> list[str]:
    path = tmp_path / "ci.yml"
    write(path, "on: pull_request\njobs:\n" + jobs + GATE)
    return lib("check_workflow.py").problems(str(path))


def test_a_matrix_that_lists_windows_is_a_windows_job(tmp_path):
    assert problems(tmp_path, """
  check:
    strategy: {matrix: {os: [ubuntu-latest, windows-latest]}}
    runs-on: ${{ matrix.os }}
    timeout-minutes: 30
    steps: [{run: "true"}]
""") == []


def test_a_matrix_without_windows_is_not(tmp_path):
    found = problems(tmp_path, """
  check:
    strategy: {matrix: {os: [ubuntu-latest, macos-latest]}}
    runs-on: ${{ matrix.os }}
    timeout-minutes: 30
    steps: [{run: "true"}]
""")
    assert any("windows-latest" in p for p in found), found


def test_a_job_the_gate_does_not_need_and_a_job_with_no_timeout_are_both_named(tmp_path):
    found = problems(tmp_path, """
  check:
    runs-on: windows-latest
    steps: [{run: "true"}]
  stray:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps: [{run: "true"}]
""")
    assert any("`check` has no timeout-minutes" in p for p in found), found
    assert any("does not need job `stray`" in p for p in found), found


def test_the_tracked_workflow_keeps_every_promise():
    assert lib("check_workflow.py").problems(".github/workflows/ci.yml") == []
