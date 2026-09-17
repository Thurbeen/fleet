"""The CI workflow keeps the promises its `All Checks` gate rests on.

`scripts/lib/check_workflow.py` holds four: `All Checks` needs every other
job, every job has a timeout, a job runs on Windows, and the names the jobs
pass to `fleet check` cover every check the gate knows. The Windows one has two
spellings, a literal `windows-latest` and a matrix that lists it, and the gate
runs as a matrix over both runners.

The fourth exists because the gate is sharded across jobs: an area added to
`check.py` and not to a shard would never run on CI, and nothing else here
would notice. It is held per RUNNER, so a shard only the Linux job takes is
reported too — that area would never run on Windows, which is what the matrix
is for.
"""

from harness import lib, write

GATE = """
  all-checks:
    needs: [%s]
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps: [{run: "true"}]
"""


def problems(tmp_path, jobs: str, needs: str = "check") -> list[str]:
    path = tmp_path / "ci.yml"
    write(path, "on: pull_request\njobs:\n" + jobs + GATE % needs)
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


# A shard that only one runner takes: the whole area list is covered across the
# workflow, and half of it never runs on Windows.
ONE_RUNNER = """
  check:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
        shard: [{name: most, areas: "%s"}]
    runs-on: ${{ matrix.os }}
    timeout-minutes: 30
    steps: [{run: "uv run --frozen fleet check ${{ matrix.shard.areas }}"}]
  extra:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps: [{run: "uv run --frozen fleet check %s"}]
"""


def test_a_check_that_runs_on_one_runner_only_is_reported(tmp_path):
    """Covered in aggregate is not covered. `install` running on Linux alone
    leaves the Windows promise to a green `All Checks` that never ran it — and
    Windows is the runner the gate has this matrix for."""
    *covered, linux_only = every_check()
    found = problems(tmp_path, ONE_RUNNER % (" ".join(covered), linux_only), needs="check, extra")

    assert any(linux_only in p and "windows-latest" in p for p in found), found
    assert not any(linux_only in p and "ubuntu-latest" in p for p in found), found


def test_a_bare_fleet_check_on_one_runner_does_not_excuse_the_other(tmp_path):
    """The bare run is every check — on the runner that takes it, and nowhere else."""
    *covered, windows_misses = every_check()
    found = problems(tmp_path, """
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps: [{run: "uv run --frozen fleet check"}]
  windows:
    runs-on: windows-latest
    timeout-minutes: 30
    steps: [{run: "uv run --frozen fleet check %s"}]
""" % " ".join(covered), needs="check, windows")

    assert any(windows_misses in p and "windows-latest" in p for p in found), found


def test_a_runner_that_cannot_be_resolved_is_reported_rather_than_dropped(tmp_path):
    """`${{ matrix.image }}` against a matrix that has no `image`: the job runs
    the gate, and silently counting it for nobody is how a promise passes on
    nothing at all."""
    found = problems(tmp_path, """
  check:
    strategy: {matrix: {os: [windows-latest]}}
    runs-on: ${{ matrix.image }}
    timeout-minutes: 30
    steps: [{run: "uv run --frozen fleet check lint"}]
  windows:
    runs-on: windows-latest
    timeout-minutes: 30
    steps: [{run: "true"}]
""", needs="check, windows")

    assert any("matrix.image" in p for p in found), found


def test_a_gate_run_chained_after_another_command_is_read_for_its_own_words(tmp_path):
    """`fleet check lint && echo done` runs one check, not three invented ones."""
    found = problems(tmp_path, """
  check:
    runs-on: windows-latest
    timeout-minutes: 30
    steps: [{run: "uv run --frozen fleet check lint && echo done"}]
""")

    assert not any("echo" in p or "done" in p for p in found), found
    assert any("lock" in p for p in found), "the checks that step does not run are still missing"


def test_a_redirected_gate_run_is_read_for_its_own_words(tmp_path):
    """`fleet check lint 2>&1 | tee gate.log` runs one check. The `2` of the
    redirect is not a second one, and reporting it as an invented check name is
    a gate that fails on a step that is doing nothing wrong."""
    found = problems(tmp_path, """
  check:
    runs-on: windows-latest
    timeout-minutes: 30
    steps: [{run: "uv run --frozen fleet check lint 2>&1 | tee gate.log"}]
""")

    assert not any("`fleet check 2`" in p or "tee" in p for p in found), found


SHARED_LABEL = """
  check:
    runs-on: [self-hosted, linux]
    timeout-minutes: 30
    steps: [{run: "uv run --frozen fleet check %s"}]
  windows:
    runs-on: [self-hosted, windows-latest]
    timeout-minutes: 30
    steps: [{run: "uv run --frozen fleet check %s"}]
"""


def test_two_runners_sharing_a_label_do_not_cover_for_each_other(tmp_path):
    """`runs-on: [self-hosted, linux]` picks ONE machine carrying both labels.
    Counted a runner per label, the two jobs' shards both land under
    `self-hosted`, which is then covered by halves that ran on two different
    machines — and `linux`, which is no machine at all, is reported as missing
    everything the other job ran."""
    *most, only_linux = every_check()
    found = problems(tmp_path, SHARED_LABEL % (only_linux, " ".join(most)), needs="check, windows")

    assert any(only_linux in p and "windows-latest" in p for p in found), found
    assert not any(p.endswith("on linux") or p.endswith("on self-hosted") for p in found), found
