"""The harness's own two promises, proven before any test leans on them.

`isolated_env` is `scripts/lib/selftest-env.sh` in Python, so it is held to the
guarantees `scripts/isolation-selftest.sh` §2 holds that helper to: run under a
HOSTILE host — a global git config that signs and hooks every commit and names
`trunk` the default branch, a thurbox hosts.toml in HOME, forge credentials,
forge host overrides and a worker's THURBOX_SESSION in the environment — and
none of it may reach a test.

The stubs are a package installed as console scripts, so fleet's unchanged
`subprocess.run(["gh", ...])` finds a real executable on every platform — a
`gh.exe` on Windows, where `CreateProcess` would never find a `.cmd`. A call
that reaches a real tool instead is the defect, so each stub logs its argv and
a tripwire behind it logs that it ran.
"""

import os
import subprocess
import sys
from pathlib import Path

from harness import REPO, STUB_TOOLS, isolate


def hostile_host(root: Path) -> dict:
    """An environment that breaks every commit and leaks every credential."""
    home = root / "home"
    (home / ".config" / "thurbox").mkdir(parents=True)
    (home / "AppData" / "Roaming" / "thurbox").mkdir(parents=True)
    hosts = '[[hosts]]\nname = "operator-private-box"\n'
    (home / ".config" / "thurbox" / "hosts.toml").write_text(hosts)
    (home / "AppData" / "Roaming" / "thurbox" / "hosts.toml").write_text(hosts)

    gitconfig = root / "gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = Operator Private\n\temail = operator@private.invalid\n"
        "\tsigningkey = DEADBEEF\n[commit]\n\tgpgsign = true\n"
        f"[gpg]\n\tprogram = {(root / 'no-such-gpg').as_posix()}\n"
        f"[init]\n\tdefaultBranch = trunk\n[core]\n\thooksPath = {(root / 'hooks').as_posix()}\n"
    )
    (home / ".gitconfig").write_text(gitconfig.read_text())

    # A tripwire for every stubbed tool, BEHIND the stubs on PATH: it only runs
    # if a stub was not found first, and then it says so.
    trip = root / "bin"
    trip.mkdir()
    for tool in STUB_TOOLS:
        script = trip / tool
        script.write_text(f'#!/bin/sh\necho "{tool} $*" >>"{root / "tripwire.log"}"\nexit 97\n')
        script.chmod(0o755)

    env = dict(os.environ)
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        APPDATA=str(home / "AppData" / "Roaming"),
        LOCALAPPDATA=str(home / "AppData" / "Local"),
        XDG_CONFIG_HOME=str(home / ".config"),
        GIT_CONFIG_GLOBAL=str(gitconfig),
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="commit.gpgsign",
        GIT_CONFIG_VALUE_0="true",
        GIT_DIR=str(root / "not-a-repo"),
        GH_TOKEN="operator-private-token",
        GITHUB_TOKEN="operator-private-token",
        GITLAB_TOKEN="operator-private-token",
        GH_HOST="github.private.invalid",
        GITLAB_HOST="gitlab.private.invalid",
        THURBOX_SESSION="00000000-0000-0000-0000-operatorlead",
        FLEET_QUEUE_DIR=str(REPO / "orchestration" / "queue"),
        PATH=str(trip) + os.pathsep + env_path(),
    )
    return env


def env_path() -> str:
    return os.environ.get("PATH", "")


def git(env: dict, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)


def commit_probe(env: dict, where: Path) -> subprocess.CompletedProcess:
    where.mkdir(parents=True)
    git(env, "init", "-q", cwd=where)
    (where / "f").write_text("x")
    git(env, "add", "f", cwd=where)
    return git(env, "commit", "-qm", "probe", cwd=where)


def test_the_hostile_host_really_breaks_a_commit(tmp_path):
    env = hostile_host(tmp_path / "hostile")
    env.pop("GIT_DIR")
    assert commit_probe(env, tmp_path / "probe").returncode != 0, "the poison is not poison"


def test_isolated_env_defeats_the_hostile_host(tmp_path, stub_bin):
    hostile = hostile_host(tmp_path / "hostile")
    env = isolate(hostile, tmp_path / "env", stub_bin)

    probe = tmp_path / "probe"
    done = commit_probe(env, probe)
    assert done.returncode == 0, f"leak: host git config broke a commit\n{done.stderr}"
    assert git(env, "rev-parse", "--abbrev-ref", "HEAD", cwd=probe).stdout.strip() == "main"
    assert git(env, "config", "--get", "core.hooksPath", cwd=probe).stdout == ""

    for var in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        assert Path(env[var]).is_relative_to(tmp_path / "env"), f"leak: {var}={env[var]}"
    for var in (
        "THURBOX_SESSION", "GH_TOKEN", "GITHUB_TOKEN", "GITLAB_TOKEN", "GH_HOST", "GITLAB_HOST",
        "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_DIR",
    ):
        assert var not in env, f"leak: {var} reaches a test"
    assert env.get("GIT_CONFIG_GLOBAL") != hostile["GIT_CONFIG_GLOBAL"]

    # Whatever a child resolves as HOME, thurbox's hosts.toml is not there.
    reads = subprocess.run(
        [sys.executable, "-c",
         "import os, pathlib; h = pathlib.Path(os.path.expanduser('~'));"
         "a = pathlib.Path(os.environ['APPDATA']);"
         "print([str(p) for p in (h/'.config/thurbox/hosts.toml', a/'thurbox/hosts.toml') if p.exists()]);"
         "import yaml; print('yaml imports')"],
        env=env, capture_output=True, text=True,
    )
    assert reads.stdout.splitlines() == ["[]", "yaml imports"], reads.stderr

    queue = Path(env["FLEET_QUEUE_DIR"])
    assert queue.is_dir() and not queue.is_relative_to(REPO)
    for var in ("FLEET_RUNS_DIR", "FLEET_RECONCILE_DIR"):
        assert Path(env[var]).is_dir() and not Path(env[var]).is_relative_to(REPO)
    assert not Path(env["FLEET_REGISTRY_FILE"]).exists()
    for var in ("FLEET_AUTO_MERGE_ROOT", "FLEET_PUBLISH_ROOT", "FLEET_AGENT_ROOT", "FLEET_GLYPH_ROOT"):
        confs = sorted(p.name for p in (Path(env[var]) / "orchestration").iterdir())
        assert confs == sorted(p.name for p in (REPO / "orchestration").glob("*.example.conf"))


def test_a_child_process_finds_every_stub_and_no_real_tool(tmp_path, stub_bin):
    hostile = hostile_host(tmp_path / "hostile")
    env = isolate(hostile, tmp_path / "env", stub_bin)

    # From a CHILD, as fleet calls them: a bare name, no shell, the child's PATH.
    calls = "; ".join(f"subprocess.run([{tool!r}, 'fleet-harness-probe'])" for tool in STUB_TOOLS)
    done = subprocess.run(
        [sys.executable, "-c", f"import subprocess; {calls}"],
        env=env, capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr

    trip = tmp_path / "hostile" / "tripwire.log"
    assert not trip.exists(), f"a real tool ran instead of its stub:\n{trip.read_text()}"
    logged = (Path(env["FLEET_STUB_ROOT"]) / "calls.log").read_text().splitlines()
    assert logged == [f"{tool} fleet-harness-probe" for tool in STUB_TOOLS]


def test_a_test_can_stand_in_for_any_tool_by_name(stubs):
    # A tool the stub package does not declare — onboarding asks for `lua`,
    # `rumdl` and a dozen more — found by bare name from a child, as fleet
    # calls it, with an answer and an exit code the test chose.
    stubs.tool("rumdl", "import sys\nprint('rumdl answered', *sys.argv[1:])\nraise SystemExit(3)\n")
    done = subprocess.run(["rumdl", "--version"], capture_output=True, text=True)
    assert (done.returncode, done.stdout) == (3, "rumdl answered --version\n"), done.stderr
    assert stubs.calls("rumdl") == ["rumdl --version"]


def test_a_scripted_answer_replaces_a_built_in_stub(stubs):
    # The per-test answer wins over the package's canned one, so a test about
    # five `gh` logins does not need a sixth stub package.
    stubs.tool("gh", "import sys\nprint('scripted', *sys.argv[1:])\n")
    done = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    assert (done.returncode, done.stdout) == (0, "scripted auth status\n"), done.stderr
    assert stubs.calls("gh") == ["gh auth status"]
