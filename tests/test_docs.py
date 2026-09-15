"""The README's links resolve to tracked files on every OS.

`scripts/lib/check_docs.py` holds each relative link against `git ls-files`,
which names paths with forward slashes on every platform. A link has to be
resolved the same way, or on Windows every link in the README, the diagram
included, reads as untracked and the gate fails on a correct README.
"""

import subprocess

from harness import PYTHON, REPO, run, write


def test_the_tracked_readme_passes_the_docs_check():
    done = run([*PYTHON, "scripts/lib/check_docs.py", "README.md"], cwd=REPO)
    assert done.code == 0, done.out


def test_a_link_into_a_subdirectory_resolves_to_its_tracked_path(tmp_path):
    repo = tmp_path / "repo"
    write(repo / "docs" / "guide" / "page.md", "# page\n")
    write(repo / "notes" / "index.md", "See [the page](../docs/guide/page.md) and [here](./index.md).\n")
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    done = run([*PYTHON, str(REPO / "scripts" / "lib" / "check_docs.py"), "notes/index.md"], cwd=repo)
    assert done.code == 0, done.out
