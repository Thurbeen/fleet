"""FLEET.md reaching the lead, which shipping it as the payload never did.

The extension lays `FLEET.rendered.md` down under its own home and symlinks
`CLAUDE.md`, `AGENTS.md` and `GEMINI.md` at it there. All of that worked, and
none of it was ever read: an agent loads its context files from its CWD and
that directory's ancestors, and the lead's cwd is the CHECKOUT — which the
extension home is neither. So the lead loaded the checkout's own `CLAUDE.md`,
the pointer at `AGENTS.md`, and held no `FLEET.md` at all.

The fix is in that pointer: the checkout root already holds the rendered
payload, so importing it from there is what puts it on the lead's cwd chain.
These tests resolve the chain the way an agent does, over a checkout with the
payload and over a fresh clone without one.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from harness import REPO, expect, refute, run_fleet

IMPORT = re.compile(r"^@(\S+)\s*$", re.M)

# The h1 of FLEET.md, which nothing else in the checkout's chain carries.
LEAD_CONTEXT = "# FLEET.md — standing context for the control-plane session"


def run_render(dest: Path):
    return run_fleet("install-extension", "--render-only", str(dest))


def imported(text: str) -> list[str]:
    return IMPORT.findall(text)


def chain(root: Path) -> str:
    """What an agent whose cwd is `root` is handed: CLAUDE.md and its imports.

    An import naming a file that is not there is skipped rather than an error
    — measured against Claude Code 2.1.274, and the whole reason a fresh clone
    with no rendered payload still starts.
    """
    entry = root / "CLAUDE.md"
    text = entry.read_text(encoding="utf-8")
    for name in imported(text):
        target = root / name
        if target.is_file():
            text += "\n" + target.read_text(encoding="utf-8")
    return text


def checkout(dest: Path) -> Path:
    for name in ("CLAUDE.md", "AGENTS.md"):
        (dest / name).write_text((REPO / name).read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def test_the_lead_is_handed_fleet_md_from_its_own_cwd(tmp_path):
    root = checkout(tmp_path)
    done = run_render(root)
    assert done.code == 0, done.out
    text = chain(root)
    expect(text, LEAD_CONTEXT, "Slayer", "VEGA")
    refute(text, "@OPERATOR_NAME@", "@ASSISTANT_NAME@")


def test_a_fresh_clone_has_no_payload_and_still_gets_agents_md(tmp_path):
    """The payload is gitignored, so it does not exist until install-extension
    writes it. The import has to be optional or a fresh clone is worse off."""
    root = checkout(tmp_path)
    assert not (root / "FLEET.rendered.md").exists()
    text = chain(root)
    expect(text, "# AGENTS.md — operating guide for this control plane")
    refute(text, LEAD_CONTEXT)


def test_the_payload_the_manifest_ships_is_the_file_the_checkout_imports(tmp_path):
    """One filename, two readers. Rename it in one place and the lead goes
    quiet again with every test but this one still green."""
    done = run_render(tmp_path)
    assert done.code == 0, done.out
    manifest = tomllib.loads((tmp_path / "extension.toml").read_text(encoding="utf-8"))
    shipped = [f["path"] for f in manifest["files"]]
    assert shipped == ["FLEET.rendered.md"]
    assert shipped[0] in imported((REPO / "CLAUDE.md").read_text(encoding="utf-8"))
