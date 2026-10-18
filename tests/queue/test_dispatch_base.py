"""A worker branches off its base as `origin` has it, never as this checkout last saw it.

thurbox cuts a worktree off whatever `--base-branch` names and fetches nothing,
so a dispatching machine's days-old local `main` once handed a worker a tree
from before the config its role needed. Dispatch fetches every repository's
base first and hands thurbox `origin/<base>`; a fetch that fails refuses the
task rather than falling back to the stale branch, and a checkout with no
`origin` keeps its own.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from kit_dispatch import ANSWERING_KEYS, next_session
from queuekit import fill_brief, ok

from harness import expect, git
from harness import run_queue as q

SID = "ba5e0000-0000-0000-0000-000000000001"


@pytest.fixture
def queue_dir(isolated_env) -> Path:
    return Path(os.environ["FLEET_QUEUE_DIR"])


@pytest.fixture
def topic() -> str:
    return ok(q("topic", "add", "fresh-base", "--title", "Branch off a fresh base",
                "--prompt", "a worker starts from origin, not a stale local main")).stdout.strip()


def checkout(root: Path, name: str, origin: Path | None) -> Path:
    """A checkout whose local `main` is one commit behind its origin's, or a
    local-only one when `origin` is None."""
    repo = root / name
    git("init", "-q", "-b", "main", str(repo))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=repo)
    if origin is not None:
        git("clone", "-q", "--bare", str(repo), str(origin))
        git("remote", "add", "origin", str(origin), cwd=repo)
        ahead = root / f"{name}-ahead"
        git("clone", "-q", str(origin), str(ahead))
        git("commit", "-q", "--allow-empty", "-m", "landed since", cwd=ahead)
        git("push", "-q", "origin", "main", cwd=ahead)
    return repo


def creates(stubs) -> list[str]:
    return [c for c in stubs.calls("thurbox-cli", "session create") if "--repo-path" in c]


def test_every_repository_branches_off_the_base_its_origin_just_gave(topic, queue_dir, stubs, tmp_path):
    primary = checkout(tmp_path, "app", tmp_path / "app.git")
    sibling = checkout(tmp_path, "lib", tmp_path / "lib.git")
    local = checkout(tmp_path, "notes", None)
    ok(q("add", topic, "spans", "--title", "Spans three", "--repo", str(primary),
         "--branch", "fix/spans", "--number", "01",
         "--add-repo", str(sibling), "--add-repo", str(local)))
    fill_brief(queue_dir / topic / "01-spans" / "BRIEF.md")
    next_session(stubs, SID)
    stubs.session_is(SID, "idle")
    stubs.tool("thurbox-cli", ANSWERING_KEYS)

    ok(q("dispatch"))
    create = creates(stubs)[0]
    # The sibling inherits `origin/main`, which its own fetch wrote; the
    # local-only checkout has no such ref, so its base is spelled out.
    expect(create, "--base-branch origin/main",
           f"--add-repo {sibling} --add-repo {local}@main")
    for repo in (primary, sibling):
        fetched = git("rev-parse", "origin/main", cwd=repo).strip()
        assert fetched == git("rev-parse", "main", cwd=tmp_path / f"{repo.name}.git").strip()
        assert fetched != git("rev-parse", "main", cwd=repo).strip(), "local main stays as it was"
    # The change request's base is the record's, as written.
    expect(q("show", f"{topic}/01-spans").out, "base:        main")


def test_a_base_that_cannot_be_fetched_refuses_the_task(topic, queue_dir, stubs, tmp_path):
    primary = checkout(tmp_path, "app", None)
    git("remote", "add", "origin", str(tmp_path / "gone.git"), cwd=primary)
    ok(q("add", topic, "stale", "--title", "Would be stale", "--repo", str(primary),
         "--branch", "fix/stale", "--number", "01"))
    fill_brief(queue_dir / topic / "01-stale" / "BRIEF.md")

    done = q("dispatch")
    assert done.code != 0, done.out
    expect(done.out, f"NOT SPAWNED — {primary}: could not fetch origin/main: fatal:")
    assert creates(stubs) == [], "never spawned off the local main instead"
    expect(q("show", f"{topic}/01-stale").out, "queued")
