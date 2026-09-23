"""Every command and repository path a skill names exists.

A skill is prose loaded into a session's context, and a stale instruction in it
sends a worker down a dead end without any test going red: a verb is renamed, a
file is deleted, and the skill goes on naming the old one. `update-fleet` named
`scripts/reconcile.sh` for weeks after the uv port removed it. So this holds
every `fleet <group>`, `fleet queue <verb>`, `fleet check <name>`,
`fleet reconcile <verb>` and `fleet paths <key>` the instruction files spell
against the code that defines them, and every repository path they name against
`git ls-files` — or `.gitignore`, because a path a running fleet writes is
documented there and tracked nowhere.

Only the instruction files: the skills, the two guides, the worker policy and
the queue's own README. Module docstrings are code and are held by the code.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from harness import REPO, lib

DOCS = sorted(REPO.glob(".agents/skills/*/SKILL.md")) + [
    REPO / "AGENTS.md",
    REPO / "FLEET.md",
    REPO / "CONTRIBUTING.md",
    REPO / "orchestration" / "queue" / "README.md",
    REPO / "orchestration" / "queue" / "POLICY.md",
    REPO / "orchestration" / "queue" / "OPERATOR.example.md",
]
IDS = [str(d.relative_to(REPO)) for d in DOCS]

# The directories a repository path starts with. A token elsewhere (`~/.claude`,
# `plugins/…` under thurbox's config, a worker's `BRIEF.md`) is not this repo's.
ROOTS = r"(?:\.agents|\.claude|scripts|tests|fleet|interface|orchestration|registry|docs|media)"
PATH = re.compile(rf"(?<![\w/.~-]){ROOTS}/[A-Za-z0-9_./-]+")
CODE = re.compile(r"```.*?```|`[^`\n]+`", re.S)


def code_spans(text: str) -> str:
    """The fenced blocks and inline code of a file, joined — where a command is spelled."""
    return "\n".join(m.group(0) for m in CODE.finditer(text))


def named(command: str, text: str) -> set[str]:
    """Every word spelled after `fleet <command>` where a command is typed: at the
    start of a line, after a backtick, or after `run`. Prose inside a sample
    output block ("the fleet is waiting") is not a command and is not read."""
    pattern = rf"(?m)(?:^\s*|`|\brun )fleet {command}((?:[a-z]+-)*[a-z]+)\b"
    return set(re.findall(pattern, code_spans(text)))


def queue_verbs() -> set[str]:
    source = (REPO / "scripts" / "lib" / "queue.py").read_text(encoding="utf-8")
    return set(re.findall(r'sub\.add_parser\(\s*"([a-z]+)"', source))


def tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    return {f for f in out.decode("utf-8").split("\0") if f}


def ignored(path: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=REPO).returncode == 0


@pytest.mark.parametrize("doc", DOCS, ids=IDS)
def test_every_fleet_group_named_exists(doc):
    from fleet.cli import GROUPS

    found = named("", doc.read_text(encoding="utf-8"))
    assert found - set(GROUPS) == set(), f"{doc.name} names a `fleet` group the CLI does not have"


@pytest.mark.parametrize("doc", DOCS, ids=IDS)
def test_every_queue_verb_named_exists(doc):
    found = named("queue ", doc.read_text(encoding="utf-8"))
    assert found - queue_verbs() == set(), f"{doc.name} names a `fleet queue` verb that does not exist"


@pytest.mark.parametrize("doc", DOCS, ids=IDS)
def test_every_check_named_exists(doc):
    check = lib("check.py")
    checks = set(check.STATIC) | set(check.TESTS)
    found = named("check ", doc.read_text(encoding="utf-8"))
    assert found - checks == set(), f"{doc.name} names a check `fleet check` does not run"


@pytest.mark.parametrize("doc", DOCS, ids=IDS)
def test_every_reconcile_verb_named_exists(doc):
    verbs = set(lib("reconcile.py").COMMANDS.split())
    found = named("reconcile ", doc.read_text(encoding="utf-8"))
    assert found - verbs == set(), f"{doc.name} names a `fleet reconcile` verb that does not exist"


@pytest.mark.parametrize("doc", DOCS, ids=IDS)
def test_every_path_key_named_exists(doc):
    keys = set(lib("fleet_platform.py").paths())
    found = named("paths ", doc.read_text(encoding="utf-8"))
    assert found - keys == set(), f"{doc.name} names a `fleet paths` key that does not exist"


@pytest.mark.parametrize("doc", DOCS, ids=IDS)
def test_every_repository_path_named_is_tracked_or_documented_as_ignored(doc):
    """A directory counts when anything tracked lives under it; a gitignored
    path counts because `.gitignore`'s header documents every one of them."""
    files = tracked()
    missing = []
    for token in sorted(set(PATH.findall(doc.read_text(encoding="utf-8")))):
        path = token.rstrip("./")
        if path in files or any(f.startswith(path + "/") for f in files) or ignored(path) or ignored(path + "/"):
            continue
        missing.append(path)
    assert missing == [], f"{doc.name} names paths that are neither tracked nor gitignored: {missing}"
