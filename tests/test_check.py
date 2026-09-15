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
    for name in ("lock", "lint", "markdown", "yaml", "workflow", "profiles", "cli", "queue", "reconcile", "status",
                 "sync", "onboarding", "pane", "extension", "install", "skills", "automerge", "isolation"):
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
