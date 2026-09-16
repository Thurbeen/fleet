"""The CI workflow keeps the promises its `All Checks` gate rests on.

`scripts/lib/check_workflow.py` holds four: `All Checks` needs every other
job, every job has a timeout, a job runs on Windows, and the names the jobs
pass to `fleet check` cover every check the gate knows. The Windows one has two
spellings, a literal `windows-latest` and a matrix that lists it, and the gate
runs as a matrix over both runners.

The fourth exists because the gate is sharded across jobs: an area added to
`check.py` and not to a shard would never run on CI, and nothing else here
would notice.
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


SHARDED = """
  check:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
        shard: %s
    runs-on: ${{ matrix.os }}
    timeout-minutes: 30
    steps: [{run: "uv run --frozen fleet check ${{ matrix.shard.areas }}"}]
"""


def every_check() -> list[str]:
    check = lib("check.py")
    return [*check.STATIC, *check.TESTS]


def test_shards_that_name_every_check_keep_the_promise(tmp_path):
    shards = '[{name: all, areas: "%s"}]' % " ".join(every_check())
    assert problems(tmp_path, SHARDED % shards) == []


def test_a_check_no_shard_names_is_reported(tmp_path):
    *covered, dropped = every_check()
    shards = '[{name: most, areas: "%s"}]' % " ".join(covered)
    found = problems(tmp_path, SHARDED % shards)
    assert any(dropped in p for p in found), found


def test_a_shard_naming_something_that_is_not_a_check_is_reported(tmp_path):
    shards = '[{name: all, areas: "%s invented"}]' % " ".join(every_check())
    found = problems(tmp_path, SHARDED % shards)
    assert any("invented" in p for p in found), found


def test_a_bare_fleet_check_needs_no_shards(tmp_path):
    assert problems(tmp_path, """
  check:
    runs-on: windows-latest
    timeout-minutes: 30
    steps: [{run: "uv run --frozen fleet check"}]
""") == []
