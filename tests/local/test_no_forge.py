"""A fleet with no forge: no `gh`, no `glab`, and no login to either anywhere.

The operator's rule: a forge login is not a requirement, and a machine doing
local-only work runs everything. So this drives every group a local-only
operator touches on a PATH that holds NEITHER CLI — asserted, so a runner where
one leaks onto that PATH fails here instead of passing by accident:

  - preflight exits 0 and shows the forge tier as optional;
  - install plans no forge unless one is asked for;
  - the whole loop runs against a local repo whose `origin` is a bare
    repository: topic add, add (`push` and `none`), dispatch through the
    thurbox stub, a worker that commits and pushes, result, collect, reap;
  - a task whose proof needs a forge says so and never crashes;
  - `shepherd` says "no forge configured" and exits 0;
  - the reconciler runs passes with no error line and says the shepherd skip
    once, not once per pass;
  - status, the pane probe and the registry tools degrade to exit 0.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path

import pytest

from harness import PYTHON, REPO, Run, expect, git, lib, refute, run, run_fleet, write
from harness import run_queue as q

EXE = ".exe" if os.name == "nt" else ""
FORGE_CLIS = ("gh", "glab")
FORGE_ROWS = {"gh", "gh auth", "glab", "glab auth"}
ANSI = re.compile(r"\x1b\[[0-9;]*m")
FLOOR = re.search(
    r'^min_thurbox_version *= *"(.*)"', (REPO / "extension.toml.in").read_text(encoding="utf-8"), re.MULTILINE
).group(1)

BRIEF = "## What to do\n\nDo the thing.\n\n## Hard constraints\n\nNone.\n\n## Coordination\n\nNone.\n\n## Done means\n\nIt is done.\n"

# `thurbox-cli`: a fresh idle session per `session create`, so two tasks
# dispatched together get two sessions, and the stub package's own answer for
# everything else. Its version is the manifest's floor, for preflight.
THURBOX = '''
import json
import os
import sys
from pathlib import Path

import fleet_stubs.thurbox as stub

ROOT = Path(os.environ["FLEET_STUB_ROOT"])
ARGS = sys.argv[1:]
if ARGS[:1] in (["--version"], ["-V"], ["-v"]):
    print("thurbox-cli " + os.environ.get("LOCAL_THURBOX_VERSION", "0.0.0"))
    raise SystemExit(0)
if ARGS[:2] == ["session", "create"]:
    sessions = ROOT / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    sid = "10ca1000-0000-0000-0000-%012d" % (len(list(sessions.glob("*.json"))) + 1)
    record = {"id": sid, "name": ARGS[ARGS.index("--name") + 1], "state": "idle", "agent": "claude",
              "hook_reported": True, "hook_state": "idle", "hook_state_age_secs": 1,
              "agent_session_id": "agent-" + sid, "cwd": str(ROOT), "backend_type": "local-tmux",
              "worktrees": []}
    (sessions / (sid + ".json")).write_text(json.dumps(record) + "\\n", encoding="utf-8")
    print(json.dumps({"id": sid, "created": True}))
    raise SystemExit(0)
stub.called = lambda tool: ROOT
raise SystemExit(stub.main())
'''


def forge_free(path: str) -> bool:
    return not any(shutil.which(tool, path=path) for tool in FORGE_CLIS)


def clean(done: Run) -> Run:
    """Ran, exited 0, and raised nothing on the way."""
    assert done.code == 0, done.out
    refute(done.out, "Traceback")
    return done


def says(line: str) -> str:
    return f"print({line!r})\n"


@pytest.fixture
def no_forge(tmp_path, stubs, monkeypatch) -> str:
    """This test's stand-ins, `git`, and this Python's directory — and no forge CLI."""
    launcher = shutil.which("fleet-stub")
    assert launcher, "fleet-stub not on PATH: the stub package is not installed"

    def place(name: str, script: str) -> None:
        write(stubs.root / "scripts" / f"{name}.py", script)
        stubs.bin.mkdir(parents=True, exist_ok=True)
        shutil.copy2(launcher, stubs.bin / (name + EXE))

    place("thurbox-cli", THURBOX)
    for name, line in (("uv", "uv 0.12.13"), ("tmux", "tmux 3.4"), ("psmux", "tmux 3.3.6")):
        place(name, says(line))

    dirs = [str(stubs.bin)]
    real_git = shutil.which("git")
    git_dir = str(Path(real_git).parent)
    if not forge_free(git_dir):
        # git shares its directory with gh (a Linux /usr/bin): carry git alone.
        alone = tmp_path / "git-alone"
        alone.mkdir()
        (alone / ("git" + EXE)).symlink_to(real_git)
        git_dir = str(alone)
    dirs.append(git_dir)
    if forge_free(str(Path(sys.executable).parent)):
        dirs.append(str(Path(sys.executable).parent))
    if os.name == "nt":
        dirs.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))
    path = os.pathsep.join(dict.fromkeys(dirs))
    assert forge_free(path), f"a forge CLI is on the PATH this test runs under: {path}"
    monkeypatch.setenv("PATH", path)
    monkeypatch.setenv("LOCAL_THURBOX_VERSION", FLOOR)
    return path


def local_repo(root: Path) -> Path:
    """A clone whose `origin` is a bare repository on disk: no forge owns it."""
    root.mkdir(parents=True, exist_ok=True)
    origin, work = root / "origin.git", root / "repo"
    git("init", "-q", "--bare", "--initial-branch=main", str(origin), cwd=root)
    git("clone", "-q", str(origin), str(work), cwd=root)
    write(work / "README.md", "seed\n")
    git("add", "README.md", cwd=work)
    git("commit", "-q", "-m", "seed", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    return work


def add(topic: str, slug: str, number: str, repo: Path, method: str, brief: Path) -> str:
    clean(q("add", topic, slug, "--title", f"Task {slug}", "--repo", str(repo), "--branch", f"local/{slug}",
            "--number", number, "--publish", method, "--brief-file", str(brief)))
    return f"{topic}/{number}-{slug}"


def result(task_dir: Path, outcome: str, note: str, artifact: str | None = None) -> None:
    front = f"outcome: {outcome}\n" + (f"artifact: {artifact}\n" if artifact else "")
    write(task_dir / "result.md", f"---\n{front}---\n{note}\n")


def state_of(task_dir: Path) -> str:
    found = re.search(r"^state: (\S+)", (task_dir / "task.yaml").read_text(encoding="utf-8"), re.MULTILINE)
    return found.group(1) if found else ""


def plain(text: str) -> str:
    return ANSI.sub("", text)


# --- preflight and install -----------------------------------------------------


def test_preflight_passes_with_no_forge_and_shows_the_forge_tier_as_optional(no_forge):
    done = run_fleet("preflight")
    out = plain(done.out)
    assert done.code == 0, out
    expect(out, "FORGE — optional", "Every required dependency is present")
    required = out.split("REQUIRED", 1)[1].split("\n\n", 1)[0]
    for row in ("gh", "glab"):
        assert not re.search(rf"^\s+\S+\s+{row}\b", required, re.MULTILINE), f"{row} is still required:\n{out}"
    # Each forge row says what it ADDS, not what breaks.
    forge = out.split("FORGE — optional", 1)[1].split("\n\n", 1)[0]
    expect(forge, "gh ", "glab ", "repo map", "shepherd")


def test_install_plans_no_forge_unless_one_is_asked_for(no_forge):
    install = lib("install.py")
    names = {row.name for row in install.plan_rows(dev=False, root=False)}
    assert not names & FORGE_ROWS, names
    asked = {row.name for row in install.plan_rows(dev=False, root=False, forges=("github",))}
    assert {"gh", "gh auth"} <= asked, asked
    assert not asked & {"glab", "glab auth"}, asked
    assert run_fleet("install", "--forge", "nowhere").code == 2


# --- the whole loop ---------------------------------------------------------------


def test_the_whole_loop_runs_against_a_local_repo_with_a_bare_origin(no_forge, tmp_path, stubs):
    repo = local_repo(tmp_path / "local")
    brief = tmp_path / "brief.md"
    write(brief, BRIEF)
    topic = clean(q("topic", "add", "local-only", "--title", "Work with no forge",
                    "--prompt", "no forge login anywhere")).stdout.strip()
    pushed = add(topic, "pushed", "01", repo, "push", brief)
    add(topic, "unpublished", "02", repo, "none", brief)
    for args in (("list",), ("plan",), ("show", pushed)):
        clean(q(*args))

    out = clean(q("dispatch")).out
    refute(out, "NOT PROMPTED", "spawn failed")
    assert len(stubs.calls("thurbox-cli", "session create")) == 2, out
    clean(q("watch", "--for-secs", "0"))

    # The worker: commit onto the base branch and push it to the bare origin.
    worker = tmp_path / "local" / "worker"
    git("clone", "-q", str(tmp_path / "local" / "origin.git"), str(worker))
    write(worker / "CHANGE.md", "done\n")
    git("add", "CHANGE.md", cwd=worker)
    git("commit", "-q", "-m", "the change", cwd=worker)
    git("push", "-q", "origin", "HEAD:main", cwd=worker)
    sha = git("rev-parse", "HEAD", cwd=worker).strip()

    queue = Path(os.environ["FLEET_QUEUE_DIR"]) / topic
    # A local remote has no commit URL: the sha alone is the artifact, and git proves it.
    result(queue / "01-pushed", "shipped", "Pushed to main.", sha)
    result(queue / "02-unpublished", "shipped", "Nothing to publish.")

    out = clean(q("collect")).out
    expect(out, "[publish verified: push]", "[publish not checked: none]")
    refute(out, "HELD OPEN")
    assert state_of(queue / "01-pushed") == "landed", out
    assert state_of(queue / "02-unpublished") == "landed", out
    deleted = "\n".join(stubs.calls("thurbox-cli", "session delete"))
    for n in (1, 2):
        assert f"10ca1000-0000-0000-0000-{n:012d}" in deleted, out

    for args in (("reap",), ("list",), ("plan",), ("show", pushed), ("refuel",)):
        clean(q(*args))
    shepherd = clean(q("shepherd")).out
    expect(shepherd, "no forge configured")
    assert shepherd.count("no forge configured") == 1, shepherd


def test_a_push_whose_commit_never_reached_origin_is_still_held_open(no_forge, tmp_path):
    """Git alone is a real witness: a sha that is not on origin is not a push."""
    repo = local_repo(tmp_path / "local")
    brief = tmp_path / "brief.md"
    write(brief, BRIEF)
    topic = clean(q("topic", "add", "local-only", "--title", "Work with no forge", "--prompt", "p")).stdout.strip()
    add(topic, "pushed", "01", repo, "push", brief)
    write(repo / "LOCAL.md", "never pushed\n")
    git("add", "LOCAL.md", cwd=repo)
    git("commit", "-q", "-m", "local only", cwd=repo)
    sha = git("rev-parse", "HEAD", cwd=repo).strip()
    queue = Path(os.environ["FLEET_QUEUE_DIR"]) / topic
    result(queue / "01-pushed", "shipped", "Pushed, it says.", sha)

    out = clean(q("collect")).out
    expect(out, "NOT CLOSED", "is not on origin/main")
    assert state_of(queue / "01-pushed") == "queued", out


def test_a_task_that_needs_a_forge_says_it_cannot_be_verified_and_how_to_add_one(no_forge, tmp_path):
    """`unknown` and never `missing`: it closes on the worker's word, says in
    plain words why nothing checked it, and names the command that adds a forge."""
    repo = local_repo(tmp_path / "local")
    brief = tmp_path / "brief.md"
    write(brief, BRIEF)
    topic = clean(q("topic", "add", "local-only", "--title", "Work with no forge", "--prompt", "p")).stdout.strip()
    add(topic, "reviewed", "01", repo, "pr", brief)
    queue = Path(os.environ["FLEET_QUEUE_DIR"]) / topic
    result(queue / "01-reviewed", "shipped", "Opened it.", "https://github.com/acme/widgets/pull/7")

    out = clean(q("collect")).out
    expect(out, "publish unchecked", "no forge configured", "preflight --tier forge")
    refute(out, "HELD OPEN")
    assert state_of(queue / "01-reviewed") == "done", out


# --- the reconciler -------------------------------------------------------------------


def wait_for(predicate, secs: float = 90) -> bool:
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


def test_the_reconciler_runs_with_no_forge_and_logs_no_error(no_forge, tmp_path, monkeypatch):
    for var in ("WATCH", "COLLECT", "REFUEL", "SHEPHERD"):
        monkeypatch.setenv(f"FLEET_RECONCILE_{var}_SECS", "1")
    monkeypatch.setenv("FLEET_LEAD_SESSION", "Mission Control")
    repo = local_repo(tmp_path / "local")
    brief = tmp_path / "brief.md"
    write(brief, BRIEF)
    topic = clean(q("topic", "add", "local-only", "--title", "Work with no forge", "--prompt", "p")).stdout.strip()
    add(topic, "pushed", "01", repo, "push", brief)

    log = Path(os.environ["FLEET_RECONCILE_DIR"]) / "reconcile.log"

    def text() -> str:
        try:
            return log.read_text(encoding="utf-8")
        except OSError:
            return ""

    try:
        clean(run_fleet("reconcile", "start"))
        assert wait_for(lambda: text().count("result(s) read") >= 3), text()
    finally:
        run_fleet("reconcile", "stop")

    out = text()
    refute(out, "Traceback", "raised")
    assert not re.search(r"\b(collect|shepherd|refuel|watch): exit \d", out), out
    assert out.count("no forge configured") == 1, out


# --- status, the pane probe and the registry ---------------------------------------------


def test_status_the_pane_probe_and_the_registry_tools_degrade_cleanly(no_forge, tmp_path):
    repo = local_repo(tmp_path / "local")
    brief = tmp_path / "brief.md"
    write(brief, BRIEF)
    topic = clean(q("topic", "add", "local-only", "--title", "Work with no forge", "--prompt", "p")).stdout.strip()
    ref = add(topic, "pushed", "01", repo, "push", brief)
    clean(q("attach", ref, "10ca1000-0000-0000-0000-000000000009"))

    status = clean(run_fleet("status"))
    expect(status.out, "no forge configured")
    clean(run_fleet("status", "--json"))

    probe = clean(run([*PYTHON, str(REPO / "scripts" / "lib" / "pane_probe.py")]))
    assert probe.stdout.startswith("R\t"), probe.out

    # The registry tools resolve their paths from where they live and write the
    # map there, so they run from a copy.
    sandbox = tmp_path / "checkout"
    (sandbox / "scripts" / "lib").mkdir(parents=True)
    (sandbox / "registry").mkdir()
    for name in ("sync_registry.py", "add_owner.py", "gh_accounts.py", "glab_hosts.py", "fleet_platform.py"):
        shutil.copy(REPO / "scripts" / "lib" / name, sandbox / "scripts" / "lib" / name)

    def module(name: str, *args: str) -> Run:
        return run([*PYTHON, str(sandbox / "scripts" / "lib" / name), *args], cwd=sandbox)

    # No owners file and no gh: the map is optional, so there is nothing to sync.
    done = clean(module("sync_registry.py"))
    expect(done.out, "optional")
    assert not (sandbox / "registry" / "repos.generated.yaml").exists()
    # Owners listed and still no gh: the same, and the map is left alone.
    write(sandbox / "registry" / "owners.txt", "octo\n")
    expect(clean(module("sync_registry.py")).out, "optional")
    assert not (sandbox / "registry" / "repos.generated.yaml").exists()
    clean(module("add_owner.py"))
    # Adding an owner by name still writes the owners file, and says the map was
    # not synced rather than reporting a change to a map nobody wrote.
    done = clean(module("add_owner.py", "acme"))
    expect(done.out, "NOT synced", "preflight --tier forge")
    refute(done.out, "MAP CHANGED")
    assert (sandbox / "registry" / "owners.txt").read_text(encoding="utf-8").splitlines() == ["octo", "acme"]

    empty = tmp_path / "no-clones"
    empty.mkdir()
    clean(run_fleet("discover-owners", str(empty)))
