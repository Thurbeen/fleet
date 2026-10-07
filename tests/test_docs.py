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


def _readme_with_diagram(tmp_path, svg):
    repo = tmp_path / "repo"
    write(repo / "README.md", "![diagram](docs/d.svg)\n")
    write(repo / "docs" / "d.svg", svg)
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return run([*PYTHON, str(REPO / "scripts" / "lib" / "check_docs.py"), "README.md"], cwd=repo)


def test_a_diagram_that_paints_its_own_background_needs_no_dark_palette(tmp_path):
    done = _readme_with_diagram(tmp_path, (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
        '<style>text { fill: #bbc2cf; }</style>'
        '<rect x="0.5" y="0.5" width="199" height="99" fill="#282c34"/>'
        '<text x="10" y="50">fleet</text></svg>'))
    assert done.code == 0, done.out


def test_a_transparent_diagram_without_a_dark_palette_fails(tmp_path):
    done = _readme_with_diagram(tmp_path, (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
        '<style>text { fill: #1f2328; }</style>'
        '<rect x="20" y="20" width="60" height="40" fill="#f6f8fa"/>'
        '<text x="10" y="50">fleet</text></svg>'))
    assert done.code == 1
    assert "no dark palette" in done.out


def test_an_unfilled_frame_is_not_a_backdrop(tmp_path):
    done = _readme_with_diagram(tmp_path, (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
        '<style>.frame { stroke: #d1d9e0; }</style>'
        '<rect class="frame" x="0.5" y="0.5" width="199" height="99" fill="none"/>'
        '<text x="10" y="50">fleet</text></svg>'))
    assert done.code == 1
    assert "no dark palette" in done.out
