"""`fleet check`: the whole gate, one command, the same on Linux and native Windows.

Three claims a gate can lose without anyone noticing:

- **It checks something.** A test area whose directory was renamed or never
  written would run pytest over nothing and pass. Every area `--list` names
  has to exist.
- **A failure fails the run, and says which check.** One red check in a green
  list is the whole message.
- **A missing tool fails its check.** A gate that skips when its linter is
  absent passes on the machine that has the least.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from harness import PYTHON, REPO, run, run_fleet, write


def listed() -> dict[str, tuple[str, list[str]]]:
    done = run_fleet("check", "--list")
    assert done.code == 0, done.out
    checks = {}
    for line in done.stdout.splitlines():
        name, kind, targets = line.split("\t")
        checks[name] = (kind, [] if targets == "-" else targets.split())
    return checks


def test_every_check_is_listed_and_every_test_area_exists():
    checks = listed()
    for name in ("lock", "lint", "markdown", "docs", "yaml", "workflow", "profiles", "cli", "queue", "reconcile", "status",
                 "sync", "onboarding", "pane", "extension", "install", "skills", "automerge", "isolation",
                 "architecture", "local"):
        assert name in checks, f"no {name} check: {sorted(checks)}"
    for name, (kind, targets) in checks.items():
        assert kind in ("static", "tests"), (name, kind)
        if kind == "tests":
            assert targets, f"{name} runs no tests, so it passes on nothing"
            for target in targets:
                assert (REPO / target).exists(), f"{name} runs {target}, which does not exist"


def test_every_test_file_belongs_to_a_check():
    # A test directory no area names is a test the gate never runs.
    covered = [REPO / t for _, targets in listed().values() for t in targets]
    for test in sorted((REPO / "tests").rglob("test_*.py")):
        if "stubs" in test.parts:
            continue
        assert any(test == c or c in test.parents for c in covered), f"no check runs {test.relative_to(REPO)}"


def test_an_unknown_check_is_a_usage_error():
    done = run_fleet("check", "no-such-check")
    assert done.code == 2, done.out
    assert "no-such-check" in done.stderr


def tracked_copy(where: Path) -> Path:
    """This tree's tracked files, committed into a repository of their own."""
    files = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    for name in filter(None, files.decode("utf-8").split("\0")):
        src = REPO / name
        if src.is_file() and not src.is_symlink():
            (where / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, where / name)
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "the tree under test"]):
        subprocess.run(["git", *args], cwd=where, check=True, capture_output=True)
    return where


def test_a_failing_check_fails_the_run_and_names_itself(tmp_path):
    copy = tracked_copy(tmp_path / "copy")
    write(copy / "orchestration" / "broken.yaml", "key: [unclosed\n")
    subprocess.run(["git", "add", "-A"], cwd=copy, check=True, capture_output=True)

    done = run([*PYTHON, str(copy / "scripts" / "lib" / "check.py"), "yaml"], cwd=copy)
    assert done.code == 1, done.out
    assert "FAIL" in done.out and "yaml" in done.out, done.out
    assert "broken.yaml" in done.out, done.out


def test_a_missing_tool_fails_its_check_rather_than_skipping(tmp_path):
    bare = tmp_path / "bin"
    bare.mkdir()
    done = run([*PYTHON, str(REPO / "scripts" / "lib" / "check.py"), "markdown"], PATH=str(bare))
    assert done.code == 1, done.out
    assert "rumdl not found" in done.out, done.out


def test_parallel_check_runs_independent_tests_and_propagates_failure(tmp_path):
    copy = tracked_copy(tmp_path / "copy")
    area = copy / "tests" / "skills"
    shutil.rmtree(area)
    for name, other in (("first", "second"), ("second", "first")):
        write(area / f"test_{name}.py", f'''
import time
from pathlib import Path


def test_{name}():
    root = Path(__file__).parent
    (root / "{name}.ready").touch()
    deadline = time.monotonic() + 10
    while not (root / "{other}.ready").exists():
        assert time.monotonic() < deadline, "tests ran serially"
        time.sleep(0.01)
    assert "{name}" != "second", "deliberate worker failure"
''')
    done = run([*PYTHON, str(copy / "scripts" / "lib" / "check.py"), "--jobs", "2", "skills"], cwd=copy)
    assert "AssertionError: tests ran serially" not in done.out, done.out
    assert "deliberate worker failure" in done.out, done.out
    assert done.code == 1 and "FAIL" in done.out, done.out


@pytest.mark.parametrize("value", [None, "0", "-1", "many"])
def test_invalid_worker_count_is_a_usage_error(value):
    done = run_fleet("check", "--jobs", *([] if value is None else [value]))
    assert done.code == 2, done.out
    assert "positive integer" in done.stderr, done.out


def test_partitions_run_every_test_exactly_once(tmp_path):
    copy = tracked_copy(tmp_path / "copy")
    area = copy / "tests" / "skills"
    shutil.rmtree(area)
    write(area / "test_parts.py", '''from pathlib import Path
import pytest


@pytest.mark.parametrize("number", range(6))
def test_part(number):
    marker = Path(__file__).parent / f"{number}.seen"
    assert not marker.exists(), "a test ran in two partitions"
    marker.touch()
''')
    for part in (1, 2):
        done = run([*PYTHON, str(copy / "scripts" / "lib" / "check.py"),
                    "skills", "--jobs", "1", "--partition", f"{part}/2"], cwd=copy)
        assert done.code == 0, done.out
        assert len(list(area.glob("*.seen"))) == 3 * part, done.out


@pytest.mark.parametrize("value", [None, "0/2", "3/2", "1/0", "1", "x/2"])
def test_invalid_partition_is_a_usage_error(value):
    done = run_fleet("check", "--partition", *([] if value is None else [value]))
    assert done.code == 2, done.out
    assert "1 <= I <= N" in done.stderr, done.out
