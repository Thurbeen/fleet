"""The environment every test runs in, and the stand-ins it runs against.

WHY ISOLATE. A test runs in three places — CI, a worker's worktree, and the
operator's own control-plane checkout — and only the last carries a global git
config that signs every commit or routes every hook, a HOME holding thurbox's
hosts.toml and every forge CLI's login, a THURBOX_SESSION when a worker runs
the gate, and gitignored settings files in the checkout itself. `isolate` is
what keeps all of that out, held to these guarantees by
`tests/test_harness.py`:

  git       no system config, no caller GIT_CONFIG_* or GIT_DIR-family
            variable, and a HOME whose only config is an identity, no signing
            and `main`.
  HOME      a throwaway one — and USERPROFILE, APPDATA and LOCALAPPDATA, which
            is where Windows looks instead — with every XDG base inside it.
  env       forge credentials and host overrides, THURBOX_SESSION, and every
            FLEET_* variable the caller had.
  settings  FLEET_{AUTO_MERGE,PUBLISH,AGENT,GLYPH}_ROOT and FLEET_VOICE_CONF at
            a copy of the TRACKED *.example.conf only; FLEET_QUEUE_DIR,
            FLEET_RUNS_DIR and FLEET_RECONCILE_DIR at empty directories; and
            FLEET_REGISTRY_FILE at a registry map that does not exist.
  keeps     PATH, PYTHONUSERBASE, and uv's cache and Python directories: where
            the tools are installed is not operator state.

WHY STUBS ARE CONSOLE SCRIPTS. Fleet calls `gh`, `thurbox-cli`, `ssh`, `glab`
and `quota-axi` by bare name. `tests/stubs` declares each as a console script,
so installing it gives a real executable per tool — `gh.exe` on Windows, where
`CreateProcess` appends `.exe` and nothing else — and that directory goes first
on PATH. What each stub answers comes from files under FLEET_STUB_ROOT, which
`Stubs` writes.
"""

from __future__ import annotations

import json
import os
import shutil
import site
import subprocess
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STUBS = REPO / "tests" / "stubs"
STUB_TOOLS = ("gh", "thurbox-cli", "ssh", "glab", "quota-axi")

DROPPED = {
    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_HOST",
    "GH_CONFIG_DIR", "GITLAB_TOKEN", "GITLAB_HOST", "GLAB_CONFIG_DIR", "THURBOX_SESSION",
    "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_PARAMETERS",
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX",
}
DROPPED_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_", "FLEET_")

# How fleet's own code is run: any text read or written in the locale's encoding
# is an error. That is UTF-8 on a Linux runner and cp1252 on a Windows console,
# so a record that only round-trips on one of them fails on both.
PYTHON = (sys.executable, "-X", "warn_default_encoding", "-W", "error::EncodingWarning")


@cache
def uv_dir(kind: str) -> str | None:
    """`uv cache dir` or `uv python dir`, read once under the real HOME."""
    uv = shutil.which("uv")
    done = subprocess.run([uv, kind, "dir"], capture_output=True, text=True, encoding="utf-8") if uv else None
    return done.stdout.strip() if done and done.returncode == 0 else None


def install_stubs(where: Path) -> Path:
    """Install tests/stubs into a fresh venv and return its executables' directory."""
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv not found: the stub tools are installed with it")
    venv = where / "venv"
    bindir = venv / ("Scripts" if os.name == "nt" else "bin")
    python = bindir / ("python.exe" if os.name == "nt" else "python")
    for cmd in (
        [uv, "venv", "--quiet", "--python", sys.executable, str(venv)],
        [uv, "pip", "install", "--quiet", "--python", str(python), str(STUBS)],
    ):
        done = subprocess.run(cmd, capture_output=True, text=True)
        if done.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} failed:\n{done.stderr}")
    return bindir


def isolate(environ: dict, root: Path, stub_bin: Path) -> dict:
    """`environ` with nothing the caller's machine or checkout configured left in it."""
    home = root / "home"
    appdata = home / "AppData"
    for d in (home / ".config", home / ".cache", home / ".local" / "share", home / ".local" / "state",
              appdata / "Roaming", appdata / "Local", root / "settings" / "orchestration",
              root / "queue", root / "runs", root / "reconcile", root / "stubs" / "bin"):
        d.mkdir(parents=True, exist_ok=True)
    (home / ".gitconfig").write_text(
        "[user]\n\tname = selftest\n\temail = selftest@example.invalid\n"
        "[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n",
        encoding="utf-8",
    )
    for conf in (REPO / "orchestration").glob("*.example.conf"):
        shutil.copy(conf, root / "settings" / "orchestration" / conf.name)

    env = {
        k: v for k, v in environ.items()
        if k not in DROPPED and not k.startswith(DROPPED_PREFIXES)
    }
    # Where the TOOLS are installed is not operator state: a PyYAML installed
    # with `pip --user` lives under the real HOME this replaces.
    env.setdefault("PYTHONUSERBASE", site.getuserbase())
    # So do uv's cache and Pythons, which every `uv run` would fetch again under
    # the throwaway HOME.
    for var, kind in (("UV_CACHE_DIR", "cache"), ("UV_PYTHON_INSTALL_DIR", "python")):
        if var not in env and uv_dir(kind):
            env[var] = uv_dir(kind)
    settings = str(root / "settings")
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        APPDATA=str(appdata / "Roaming"),
        LOCALAPPDATA=str(appdata / "Local"),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_CACHE_HOME=str(home / ".cache"),
        XDG_DATA_HOME=str(home / ".local" / "share"),
        XDG_STATE_HOME=str(home / ".local" / "state"),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="selftest",
        GIT_AUTHOR_EMAIL="selftest@example.invalid",
        GIT_COMMITTER_NAME="selftest",
        GIT_COMMITTER_EMAIL="selftest@example.invalid",
        # A machine a test builds from stand-ins stays that machine: a real
        # Windows registry's PATH would put the real tools back beside them.
        FLEET_NO_PATH_REFRESH="1",
        FLEET_AUTO_MERGE_ROOT=settings,
        FLEET_PUBLISH_ROOT=settings,
        FLEET_AGENT_ROOT=settings,
        FLEET_GLYPH_ROOT=settings,
        FLEET_VOICE_CONF=str(root / "settings" / "orchestration" / "voice.example.conf"),
        FLEET_QUEUE_DIR=str(root / "queue"),
        FLEET_RUNS_DIR=str(root / "runs"),
        FLEET_RECONCILE_DIR=str(root / "reconcile"),
        FLEET_REGISTRY_FILE=str(root / "registry" / "repos.generated.yaml"),
        FLEET_STUB_ROOT=str(root / "stubs"),
        # A test's own stand-ins (`Stubs.tool`) first, then the package's.
        PATH=os.pathsep.join((str(root / "stubs" / "bin"), str(stub_bin), environ.get("PATH", ""))),
        # Output is read as UTF-8 here; a Windows console's code page is not.
        PYTHONIOENCODING="utf-8",
    )
    return env


@dataclass
class Run:
    code: int
    stdout: str
    stderr: str

    @property
    def out(self) -> str:
        return self.stdout + self.stderr


def run(argv: list[str], cwd: Path = REPO, stdin: str | None = None, **env: str | None) -> Run:
    """Run `argv` in a child, as fleet's own subprocess calls do.

    A keyword sets an environment variable for this one call, and None unsets it.
    """
    child = dict(os.environ)
    for k, v in env.items():
        if v is None:
            child.pop(k, None)
        else:
            child[k] = v
    done = subprocess.run(
        argv, cwd=cwd, env=child, input=stdin, capture_output=True, encoding="utf-8", errors="replace",
    )
    return Run(done.returncode, done.stdout, done.stderr)


def run_queue(*args: str, cwd: Path = REPO, script: Path | None = None, **env: str | None) -> Run:
    """Run the queue's real entry point the way an operator would."""
    return run([*PYTHON, str(script or REPO / "scripts" / "lib" / "queue.py"), *args], cwd=cwd, **env)


def run_fleet(*args: str, cwd: Path = REPO, stdin: str | None = None, **env: str | None) -> Run:
    """Run `fleet <args>` through the console script's own `main`, from any cwd."""
    boot = "import sys\nfrom fleet.cli import main\nsys.exit(main())"
    return run([*PYTHON, "-c", boot, *args], cwd=cwd, stdin=stdin, **env)


def tripwires(where: Path, tools) -> Path:
    """A directory holding a tripwire under each tool's name, which logs to TRIPWIRE_LOG and exits 97."""
    launcher = shutil.which("fleet-tripwire")
    if not launcher:
        raise RuntimeError("fleet-tripwire not on PATH: the stub package is not installed")
    where.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        shutil.copy2(launcher, where / (tool + (".exe" if os.name == "nt" else "")))
    return where


def lib(filename: str):
    """A scripts/lib module, loaded by path under `fleet_<name>` exactly as fleet/cli.py loads it."""
    from fleet.cli import load

    return load(filename)


class Stubs:
    """What the stub tools answer, written as the files they read."""

    def __init__(self, root: Path):
        self.root = root
        self.bin = root / "bin"

    def tool(self, name: str, script: str) -> None:
        """Answer as `name` for this test: `script` is Python run with that tool's argv.

        A name the stub package already declares keeps its executable and
        takes this answer instead of its canned one. Any other name gets a copy
        of the `fleet-stub` launcher in this test's own bin, first on PATH.
        """
        self._write(f"scripts/{name}.py", script)
        if name in STUB_TOOLS:
            return
        launcher = shutil.which("fleet-stub")
        if not launcher:
            raise RuntimeError("fleet-stub not on PATH: the stub package is not installed")
        self.bin.mkdir(parents=True, exist_ok=True)
        shutil.copy2(launcher, self.bin / (name + (".exe" if os.name == "nt" else "")))

    def _write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    # gh
    def pipeline_pr(self, n: int, branch: str, attested: str | None = None) -> None:
        """A pull request the pipeline opened, attested for its own head unless told otherwise."""
        from fleet_stubs import attest

        sha = f"{n:040d}"
        self._write(f"pr-heads/{n}.branch", branch)
        self._write(f"pr-heads/{n}.sha", sha)
        self._write(f"pr-bodies/{n}.md", attest.body(attested or sha))

    def plain_pr(self, n: int, branch: str, body: str = "Opened by hand.") -> None:
        self._write(f"pr-heads/{n}.branch", branch)
        self._write(f"pr-heads/{n}.sha", f"{n:040d}")
        self._write(f"pr-bodies/{n}.md", body + "\n")

    def pr_state(self, n: int, state: str) -> None:
        self._write(f"pr-states/{n}.state", state + "\n")

    def pr_history(self, n: int, *commits: str) -> None:
        self._write(f"pr-commits/{n}.txt", "".join(c + "\n" for c in commits))

    # thurbox-cli
    def session_is(self, sid: str, state: str, age: int = 5, agent: str = "claude") -> None:
        self._write(f"sessions/{sid}.json", json.dumps({
            "id": sid, "name": f"worker {sid}", "state": state, "agent": agent,
            "hook_reported": True, "hook_state": state, "hook_state_age_secs": age,
            "agent_session_id": f"agent-{sid}",
        }) + "\n")

    def stream(self, *events: dict) -> None:
        """Append events to what `thurbox-cli watch --json` replays."""
        with open(self.root / "watch.jsonl", "a", newline="\n") as fh:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")

    def calls(self, tool: str, prefix: str = "") -> list[str]:
        """Every argv a stub was run with, as `<tool> <args>`, filtered by tool and prefix."""
        try:
            lines = (self.root / "calls.log").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        want = f"{tool} {prefix}".rstrip()
        return [line for line in lines if line == want or line.startswith(want + " ")]


def expect(out: str, *wants: str) -> None:
    """Every string is in the output — and a failure shows the whole output,
    because a queue report is short and the interesting part is what is missing."""
    missing = [w for w in wants if w not in out]
    assert not missing, f"expected to find: {missing}\n--- got ---\n{out}"


def refute(out: str, *unwanted: str) -> None:
    found = [w for w in unwanted if w in out]
    assert not found, f"expected NOT to find: {found}\n--- got ---\n{out}"


def write(path: Path, text: str) -> None:
    """Write a fixture file with LF endings on every platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def squeezed(path: Path) -> str:
    """A file with its wrapping squeezed out, so an assertion can name a phrase
    without knowing where the scaffold's own line-breaking put it."""
    return " ".join(path.read_text(encoding="utf-8").split())


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def git_ignored(path: Path) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=REPO).returncode == 0


def queue_module(code: str, *args: str, cwd: Path = REPO) -> str:
    """Run `code` in a child with scripts/lib/queue.py imported as `q`.

    A child and not an import: the module is named `queue`, which would shadow
    the standard library's for the rest of the test run.
    """
    done = subprocess.run(
        [*PYTHON, "-c", "import sys\nsys.path.insert(0, 'scripts/lib')\nimport queue as q\n" + code, *args],
        cwd=cwd, capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def stream_event(seq: int, session: str, to_state: str, event: str = "state", at: int = 1788792150000) -> dict:
    """One `thurbox-cli watch --json` line."""
    return {
        "seq": seq, "at": at, "session": session, "event": event, "from_state": None,
        "to_state": to_state, "state": to_state, "reason": "hook" if event == "state" else None,
    }
