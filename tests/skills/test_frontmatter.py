"""Discover tracked skill sources and parse the metadata consumers receive.

Validate YAML and required discovery fields without imposing a consumer's
optional-field allowlist: Claude invocation fields are valid metadata too.
"""

import subprocess

import pytest
import yaml

from harness import REPO


def tracked_skills():
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", ".agents/skills/*/SKILL.md"],
        cwd=REPO, capture_output=True, check=True, encoding="utf-8",
    )
    return sorted(path for path in result.stdout.split("\0") if path)


SKILLS = tracked_skills()


def test_tracked_skills_are_discovered():
    assert SKILLS
    assert set(SKILLS) == {
        path.relative_to(REPO).as_posix()
        for path in REPO.glob(".agents/skills/*/SKILL.md")
    }


@pytest.mark.parametrize("source", SKILLS)
def test_source_skill_frontmatter(source):
    path = REPO / source
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---", f"{source}: missing frontmatter"
    assert "---" in lines[1:], f"{source}: unterminated frontmatter"
    data = yaml.safe_load("\n".join(lines[1:lines.index("---", 1)]))
    assert isinstance(data, dict), f"{source}: frontmatter must be a mapping"
    for field in ("name", "description"):
        assert isinstance(data.get(field), str), f"{source}: {field} must be a string"
        assert data[field].strip(), f"{source}: {field} must not be empty"
    assert data["name"] == path.parent.name
    # Preserve platform-specific fields rather than rejecting or filtering them.
    if "user-invocable" in data:
        assert isinstance(data["user-invocable"], bool)
    if "argument-hint" in data:
        assert isinstance(data["argument-hint"], str)
    if "metadata" in data:
        assert isinstance(data["metadata"], dict)
    if "allowed-tools" in data:
        assert isinstance(data["allowed-tools"], (str, list))
