"""One skills tree, `.agents/skills/`, which every agent CLI loads.

`.claude/skills` points at it so Claude Code (and opencode, which reads
`.claude/skills`) finds the same copy. It is NOT tracked: a default Windows
clone has `core.symlinks=false` and checks a tracked link out as a text file
holding its target, so Claude Code found no skills at all. `fleet install`
creates it instead — a symlink on POSIX, a junction on Windows, which needs no
privilege — and `.gitignore` lists it so a sync never sees a dirty tree.

Codex reads `.agents/skills` directly in this checkout. The install tests own
its other-repository case: each child is linked under the user's
`~/.agents/skills`, still pointing at this one tracked tree.
"""

import os
import subprocess

from harness import REPO


def test_every_skill_has_a_skill_md():
    skills = [d for d in (REPO / ".agents" / "skills").iterdir() if d.is_dir()]
    assert skills
    assert [d.name for d in skills if not (d / "SKILL.md").is_file()] == []


def test_the_link_is_not_tracked_and_is_ignored():
    tracked = subprocess.run(["git", "ls-files", "--", ".claude/skills"], cwd=REPO, capture_output=True,
                             encoding="utf-8").stdout
    assert tracked == "", ".claude/skills is tracked, so a default Windows clone checks it out as a text file"
    ignored = subprocess.run(["git", "check-ignore", "-q", "--no-index", ".claude/skills"], cwd=REPO)
    assert ignored.returncode == 0, ".claude/skills is not ignored, so creating it dirties the tree"


def test_no_skill_lives_only_under_claude():
    link = REPO / ".claude" / "skills"
    if not os.path.lexists(link):
        return
    assert link.resolve() == (REPO / ".agents" / "skills").resolve(), f".claude/skills resolves to {link.resolve()}"
