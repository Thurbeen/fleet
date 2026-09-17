"""One commit, one verdict: the gate reads nothing a running fleet wrote.

`fleet check` must give the same verdict on CI, in a worker's worktree, and in
the operator's own control-plane checkout. Only the last holds a live queue,
gitignored settings and an owner's global git config, so it is the one place a
leak shows — a record nobody touched turns a green commit red there, or a claim
CI proved is quietly skipped there — and the one place nobody reviews a gate
run. So this builds that worst case on purpose:

  A POISONED COPY of the tree under test: a malformed queue record and an
  OPERATOR.md, an auto-merge.conf naming a repository, publish, agent, glyph,
  fleet-name and voice settings with odd values, a rendered extension.toml, a
  reconciler runtime directory, and a registry map of the wrong shape. All of
  it is made up here; nothing is copied from a real control plane.

  A HOSTILE HOST: `test_harness.hostile_host` — a git config that signs and
  hooks every commit and names `trunk` the default branch, a thurbox
  hosts.toml, forge credentials, a THURBOX_SESSION, and a tripwire under each
  stubbed tool's name behind the stubs on PATH.

Then the static checks and the test areas that read settings or records run in
that copy under that host, and must pass exactly as on a clean runner, without
repeating anything operator-private. The queue and reconcile areas are not run
a second time: they stand on the same `isolated_env`, which test_harness.py
proves against the same host, and `test_every_test_runs_isolated` holds every
test to it.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_harness import hostile_host

from harness import PYTHON, REPO, run, write

PRIVATE = ("operator-private", "Operator Private", "Private Lead")


def committed_copy(where: Path) -> Path:
    """The working tree as it stands, tracked and untracked-but-not-ignored, committed."""
    listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            cwd=REPO, capture_output=True, check=True).stdout
    for name in filter(None, listed.decode("utf-8").split("\0")):
        src = REPO / name
        if src.is_file() and not src.is_symlink():
            (where / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, where / name)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "fixture: the tree under test"]):
        subprocess.run(["git", *args], cwd=where, check=True, capture_output=True)
    return where


def poison(copy: Path) -> None:
    rec = copy / "orchestration" / "queue" / "poisoned"
    write(rec / "topic.yaml", "slug: poisoned\ntitle: A topic nobody touched\narchived: 42\n")
    write(rec / "PROMPT.md", "the prompt\n")
    write(rec / "01-broken" / "task.yaml",
          "id: 99-not-this-directory\ntopic: poisoned\ntitle: A broken record\nstate: half-done\n"
          "repo: /nowhere\nbranch: poisoned/branch\npublish: {method: carrier-pigeon, how: 7}\n"
          'blocked_by:\n  - {task: poisoned/42-gone, kind: invented, why: ""}\n')
    write(copy / "orchestration" / "queue" / "OPERATOR.md", "Always sign off as the operator-private lead.\n")
    o = copy / "orchestration"
    write(o / "auto-merge.conf", "github.com/operator-private/secret-repo\n")
    write(o / "publish.conf", "METHOD=carrier-pigeon\nHOW=run operator-private-pipeline --ship\n"
                              "ATTESTATION_MARKER=operator-private-mark\n")
    write(o / "agent.conf", "AGENT=operator-private-agent\nFUEL_PROVIDER=operator-private-vendor\n"
                            "LIMIT_BANNER=you are out\n")
    write(o / "session-glyphs.conf", "GLYPHS=sideways\nLEAD_GLYPH_ON=@@\n")
    write(o / "fleet.conf", "NAME=operator-private-fleet\n")
    write(o / "voice.conf", "OPERATOR_NAME=Operator Private\nASSISTANT_NAME=Private Lead\n")
    write(o / "reconcile" / "pid", "1\n")
    write(o / "reconcile" / "down", "asked down by the operator\n")
    write(o / "first-run" / "pane", "no\n")
    write(copy / "extension.toml", '[[sessions]]\nname = "Poisoned Lead"\nrepo_path = "/nowhere"\n')
    write(copy / "registry" / "repos.generated.yaml", "owners: not-a-list\n")
    write(copy / "registry" / "owners.txt", "operator-private-org\n")


@pytest.fixture
def poisoned(tmp_path) -> Path:
    copy = committed_copy(tmp_path / "checkout")
    poison(copy)
    return copy


@pytest.fixture
def hostile(tmp_path, poisoned) -> dict:
    env = hostile_host(tmp_path / "hostile")
    env.pop("GIT_DIR")
    env["FLEET_QUEUE_DIR"] = str(poisoned / "orchestration" / "queue")
    # The copy's own `fleet` package, ahead of the editable install that points
    # at the tree this test runs from — otherwise the copy's tests would read
    # this checkout, and a leak of the copy's state could never show.
    env["PYTHONPATH"] = str(poisoned)
    return env


def gate(copy: Path, env: dict, *checks: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(copy / "scripts" / "lib" / "check.py"), *checks],
                          cwd=copy, env=env, capture_output=True, encoding="utf-8", errors="replace")


def assert_same_verdict(done: subprocess.CompletedProcess, env: dict) -> None:
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    leaked = [p for p in PRIVATE if p in out]
    assert not leaked, f"the gate repeated operator-private state: {leaked}\n{out}"
    trip = Path(env["TRIPWIRE_LOG"])
    assert not trip.exists() or not trip.read_text(encoding="utf-8"), f"a real tool ran:\n{trip.read_text()}"


def test_the_poison_is_poison(poisoned):
    done = run([*PYTHON, str(poisoned / "scripts" / "lib" / "queue.py"), "check"], cwd=poisoned,
               FLEET_QUEUE_DIR=str(poisoned / "orchestration" / "queue"))
    assert done.code != 0, "the poisoned record passes queue check, so every claim below is vacuous"


def test_the_static_checks_read_no_record_and_no_setting(poisoned, hostile):
    assert_same_verdict(gate(poisoned, hostile, "lock", "yaml", "workflow", "profiles"), hostile)


def test_the_areas_that_read_settings_give_the_same_verdict(poisoned, hostile):
    areas = [a for a in ("automerge", "skills", "extension", "status", "sync", "onboarding", "install")
             if (poisoned / "tests" / {"automerge": "settings"}.get(a, a)).is_dir()]
    assert_same_verdict(gate(poisoned, hostile, *areas), hostile)


def test_every_test_runs_isolated():
    # One definition, autouse, at the root: a second `isolated_env` somewhere
    # below would shadow it for its directory, and nothing else would notice.
    defining = sorted(p.relative_to(REPO).as_posix() for p in (REPO / "tests").rglob("*.py")
                      if re.search(r"^def isolated_env\b", p.read_text(encoding="utf-8"), re.MULTILINE))
    assert defining == ["tests/conftest.py"], defining
    conftest = (REPO / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert re.search(r"@pytest\.fixture\(autouse=True\)\ndef isolated_env\b", conftest)
