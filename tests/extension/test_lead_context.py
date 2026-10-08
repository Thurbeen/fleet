"""The checkout hands both lead agents the same rendered standing context.

Claude follows CLAUDE.md's imports. Codex loads the generated project config
alongside AGENTS.md. Neither route reaches a worker's separate worktree.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

from panekit import clone

from harness import PYTHON, REPO, expect, refute, run, run_fleet, write

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


def test_codex_lead_loads_the_complete_payload_without_replacing_builtin_instructions(tmp_path):
    write(Path(os.environ["FLEET_AGENT_ROOT"]) / "orchestration/agent.conf", "AGENT=codex\n")
    assert run_render(tmp_path).code == 0
    manifest = tomllib.loads((tmp_path / "extension.toml").read_text(encoding="utf-8"))
    assert manifest["sessions"][0]["agent"] == "codex"
    config = tomllib.loads((tmp_path / ".codex/config.toml").read_text(encoding="utf-8"))
    assert config["developer_instructions"] == (tmp_path / "FLEET.rendered.md").read_text(encoding="utf-8")
    assert config["project_doc_max_bytes"] >= 32768 + len((REPO / "AGENTS.md").read_bytes())
    assert "model_instructions_file" not in config
    assert "project_doc_fallback_filenames" not in config


def test_claude_render_does_not_add_codex_context(tmp_path):
    assert run_render(tmp_path).code == 0
    assert not (tmp_path / ".codex/config.toml").exists()


def test_codex_context_refreshes_and_is_removed_when_switching_back(tmp_path):
    conf = Path(os.environ["FLEET_AGENT_ROOT"]) / "orchestration/agent.conf"
    write(conf, "AGENT=codex\n")
    assert run_render(tmp_path).code == 0
    config_path = tmp_path / ".codex/config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    start = config["hooks"]["SessionStart"][0]["hooks"]
    assert [h["command"] for h in start] == [
        "uv run --frozen --quiet fleet sync-checkout",
        "uv run --frozen --quiet fleet reconcile ensure --if-lead",
    ]
    assert config["hooks"]["Stop"][0]["hooks"][0]["command"] == "uv run --frozen --quiet fleet reconcile nudge"
    write(Path(os.environ["FLEET_VOICE_CONF"]), "OPERATOR_NAME=Reader\nASSISTANT_NAME=Guide\n")
    assert run_render(tmp_path).code == 0
    expect(config_path.read_text(encoding="utf-8"), "Reader", "Guide")
    write(conf, "AGENT=claude\n")
    assert run_render(tmp_path).code == 0
    assert not config_path.exists()


def test_codex_render_refuses_user_owned_config_before_writing(tmp_path):
    write(Path(os.environ["FLEET_AGENT_ROOT"]) / "orchestration/agent.conf", "AGENT=codex\n")
    config = tmp_path / ".codex/config.toml"
    write(config, 'model = "operator-choice"\n')
    done = run_render(tmp_path)
    assert done.code == 1
    expect(done.out, "user-owned", "refusing")
    assert config.read_text(encoding="utf-8") == 'model = "operator-choice"\n'
    assert not (tmp_path / "extension.toml").exists()


def test_codex_context_is_at_the_lead_cwd_and_never_in_a_worker_worktree(tmp_path):
    root = clone(tmp_path)
    write(root / "orchestration/agent.conf", "AGENT=codex\n")
    done = run([*PYTHON, str(root / "scripts/lib/install_extension.py"), "--render-only", str(root)],
               FLEET_AGENT_ROOT=str(root))
    assert done.code == 0, done.out
    manifest = tomllib.loads((root / "extension.toml").read_text(encoding="utf-8"))
    lead_cwd = Path(manifest["sessions"][0]["repo_path"])
    assert (lead_cwd / ".codex/config.toml").is_file()
    assert not run(["git", "status", "--porcelain"], cwd=root).stdout
    worker = tmp_path / "worker"
    assert run(["git", "worktree", "add", "--detach", str(worker)], cwd=root).code == 0
    assert (worker / "AGENTS.md").is_file()
    assert not (worker / ".codex/config.toml").exists()
    assert not (worker / "FLEET.rendered.md").exists()
