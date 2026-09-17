"""The one-liners: `install.sh` under `sh` and `install.ps1` under PowerShell, end to end.

Each bootstrap is run the way an operator meets it — the script on stdin of
`sh`, or `irm | iex`'s shape — in a throwaway HOME against a local clone of
this tree, with `uv` and `git` as stand-ins. The same claims hold for both:

  - a fresh machine gets uv (from an installer the test stands in), a clone at
    the default place, and `fleet install` run to the end off that clone;
  - a second run changes nothing, and says the checkout is current;
  - an existing clone is fast-forwarded and never overwritten: dirty-and-behind,
    diverged, another branch, and a directory that is not a fleet clone are
    each refused and left exactly as they were;
  - `FLEET_DIR` is honoured, and the Mission Control lead's checkout is sticky;
  - a missing required dependency stops before the extension;
  - no git: installed through the OS's manager on `--yes`, refused with the
    command when nobody can be asked.

Why a stand-in uv installer: the real one downloads. `FLEET_TEST_UV_INSTALLER`
names a local script to run instead, and nothing reads it unless it is set.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from harness import REPO, Run, expect, refute, write
from installkit import (
    EXE, FLOOR, GIT, SUDO, WINDOWS, clone, full_machine, git, installs, launcher, machine, place, plain, tree_snapshot,
    uv_standin,
)

SH = shutil.which("sh")
# install.ps1 is the Windows bootstrap. A Linux runner that ships pwsh (GitHub's
# ubuntu image does) is not a machine it is for: it finds no uv.exe and no winget.
POWERSHELL = (shutil.which("powershell") or shutil.which("pwsh")) if WINDOWS else None


class Sh:
    name = "sh"
    manager = ("apt-get", {"git": "git"})
    git_line = "sudo apt-get install -y git"

    def base(self, where: Path) -> str:
        """The few tools install.sh itself uses, and nothing else from their directory."""
        where.mkdir(parents=True, exist_ok=True)
        for tool in ("sh", "dirname", "ls", "mkdir", "id"):
            real = shutil.which(tool)
            if real and not (where / tool).exists():
                os.symlink(real, where / tool)
        return str(where)

    def run(self, env: dict, *args: str, piped: bool = True) -> Run:
        script = REPO / "install.sh"
        if piped and not args:
            with open(script, "rb") as fh:
                done = subprocess.run([SH], stdin=fh, env=env, capture_output=True, start_new_session=True)
        else:
            done = subprocess.run([SH, str(script), *args], stdin=subprocess.DEVNULL, env=env, capture_output=True,
                                  start_new_session=True)
        return Run(done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace"))

    def uv_installer(self, where: Path) -> Path:
        path = where / "uv-installer.sh"
        write(path, f'"{sys.executable}" -c "import os, shutil; shutil.copy2({launcher()!r}, '
                    f'os.path.join(os.environ[\'UV_INSTALL_DIR\'], \'uv\'))"\n')
        return path


class PowerShell:
    name = "powershell"
    manager = ("winget", {"Git.Git": "git"})
    git_line = "winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements"

    def base(self, where: Path) -> str:
        system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        return os.pathsep.join([system, os.path.dirname(POWERSHELL)])

    def run(self, env: dict, *args: str, piped: bool = True) -> Run:
        script = REPO / "install.ps1"
        if piped and not args:
            # `irm | iex`'s shape: the script's TEXT, evaluated, with no file and no param().
            argv = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                    f"Get-Content -Raw -LiteralPath '{script}' | Invoke-Expression; exit $FleetInstallExit"]
        else:
            argv = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args]
        done = subprocess.run(argv, input=b"", env=env, capture_output=True)
        return Run(done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace"))

    def uv_installer(self, where: Path) -> Path:
        path = where / "uv-installer.ps1"
        write(path, f"Copy-Item -LiteralPath '{launcher()}' -Destination (Join-Path $env:UV_INSTALL_DIR 'uv.exe')\n")
        return path


DRIVERS = [
    pytest.param(Sh(), id="sh", marks=pytest.mark.skipif(SH is None or WINDOWS,
                                                          reason="no POSIX sh on this machine")),
    pytest.param(PowerShell(), id="powershell", marks=pytest.mark.skipif(POWERSHELL is None,
                                                                          reason="install.ps1 runs on Windows")),
]


def family(driver) -> str:
    return "windows" if driver.name == "powershell" else "posix"


@pytest.fixture(params=DRIVERS)
def driver(request):
    return request.param


@pytest.fixture
def origin(upstream, tmp_path) -> Path:
    """Upstream for this test alone, so a test can move it."""
    return clone(upstream, tmp_path / "origin")


@pytest.fixture
def box(driver, stubs, origin, tmp_path, isolated_env):
    """A machine with everything, and a way to run the bootstrap on it."""
    tools = full_machine(family(driver)) | {"uv": uv_standin()}
    machine(stubs, tools)
    path = os.pathsep.join([str(stubs.bin), driver.base(tmp_path / "base")])
    home = isolated_env / "home"

    def run(*args: str, piped: bool = True, **extra: str | None) -> Run:
        env = dict(os.environ)
        env.update(PATH=path, FLEET_REPO=str(origin), FLEET_INSTALL_FAMILY=family(driver))
        env.pop("FLEET_DIR", None)
        for k, v in extra.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        done = driver.run(env, *args, piped=piped)
        return Run(done.code, plain(done.stdout), plain(done.stderr))

    run.home = home
    run.clone = home / "fleet"
    run.path = path
    return run


def extension_calls(stubs) -> list[str]:
    return stubs.calls("extension")


# --- 1a. a fresh machine ------------------------------------------------------------


def test_a_fresh_machine_gets_uv_a_clone_and_a_whole_fleet_install(box, driver, stubs, tmp_path):
    (stubs.bin / ("uv" + EXE)).unlink()
    uv_home = tmp_path / "uv-home"
    uv_home.mkdir()
    done = box(FLEET_YES="1", UV_INSTALL_DIR=str(uv_home), UV_NO_MODIFY_PATH="1",
               FLEET_TEST_UV_INSTALLER=str(driver.uv_installer(tmp_path)))
    assert done.code == 0, done.out
    assert (uv_home / ("uv" + EXE)).exists(), "uv was not installed where UV_INSTALL_DIR said"
    expect(done.out, str(box.clone), "REQUIRED", "Every required dependency is present")
    assert (box.clone / "extension.toml.in").is_file()
    calls = (stubs.root / "calls.log").read_text(encoding="utf-8").splitlines()
    install = f"extension install {box.clone}"
    assert install in calls, calls
    probe = next(i for i, c in enumerate(calls) if c.startswith("thurbox-cli --version"))
    assert probe < calls.index(install), "the extension ran before the prerequisites were probed"
    assert (box.clone / ".claude" / "skills" / "fleet-queue" / "SKILL.md").is_file()


def test_a_uv_installer_that_installs_nothing_stops_the_bootstrap_before_the_clone(box, driver, stubs, tmp_path):
    """The uv step's refusal is the end of the run. A child installer that prints
    and installs nothing must not read as success and walk on into the clone."""
    (stubs.bin / ("uv" + EXE)).unlink()
    uv_home = tmp_path / "uv-home"
    uv_home.mkdir()
    installer = tmp_path / ("uv-noop.ps1" if driver.name == "powershell" else "uv-noop.sh")
    write(installer, "Write-Output 'the stand-in installer ran and installed nothing'\n"
                     if driver.name == "powershell" else "echo 'the stand-in installer ran and installed nothing'\n")
    done = box(FLEET_YES="1", UV_INSTALL_DIR=str(uv_home), UV_NO_MODIFY_PATH="1",
               FLEET_TEST_UV_INSTALLER=str(installer))
    assert "the stand-in installer ran" in done.out, f"the uv step never ran, so this proves nothing:\n{done.out}"
    assert done.code != 0, done.out
    assert not box.clone.exists(), f"the bootstrap cloned after its uv step refused:\n{done.out}"
    refute(done.out, "Cloned ")


@pytest.mark.parametrize("name", ["install.ps1", "install.sh"])
def test_the_bootstraps_are_ascii_with_lf_line_endings(name):
    """Both bootstraps are also read as files: install.ps1's header and the README
    offer `irm <url> -OutFile install.ps1` then `powershell -File install.ps1`,
    and Windows PowerShell 5.1 decodes a .ps1 with no byte-order mark in the
    ANSI code page, so one non-ASCII byte would reach it as different text than
    the tracked file. A CR, in turn, is part of every line `sh` reads."""
    data = (REPO / name).read_bytes()
    assert b"\r" not in data, f"{name} has CR line endings"
    bad = [(n, line) for n, line in enumerate(data.split(b"\n"), 1) if any(b > 127 for b in line)]
    assert not bad, f"{name} has non-ASCII bytes on lines {[n for n, _ in bad]}"


def test_a_uv_an_earlier_run_installed_is_found_and_not_downloaded_again(box, driver, stubs, tmp_path):
    """UV_NO_MODIFY_PATH, or a window opened before the installer changed PATH,
    leaves uv off a new shell's PATH: the next run looks where the installer puts it."""
    (stubs.bin / ("uv" + EXE)).unlink()
    uv_home = tmp_path / "uv-home"
    uv_home.mkdir()
    installer = driver.uv_installer(tmp_path)
    env = dict(FLEET_YES="1", UV_INSTALL_DIR=str(uv_home), UV_NO_MODIFY_PATH="1",
               FLEET_TEST_UV_INSTALLER=str(installer))
    assert box(**env).code == 0
    write(installer, "exit 3\n")
    again = box(**env)
    assert again.code == 0, again.out
    refute(again.out, "uv is not installed")


def test_the_uv_installer_seam_never_fires_unset(driver):
    """The test seam is read in exactly one place, and only when set."""
    text = (REPO / ("install.ps1" if driver.name == "powershell" else "install.sh")).read_text(encoding="utf-8")
    assert "astral.sh/uv/install" in text
    assert text.count("FLEET_TEST_UV_INSTALLER") >= 1


# --- 1b. the second run changes nothing -----------------------------------------------


def test_a_second_run_changes_nothing(box, driver, stubs):
    assert box(FLEET_YES="1").code == 0
    head = git("rev-parse", "HEAD", cwd=box.clone)
    before = tree_snapshot(box.clone)
    settings = (box.home / ".claude" / "settings.json").read_bytes()

    # The inspectable form this time: downloaded, read, run as a file.
    done = box(piped=False)
    assert done.code == 0, done.out
    expect(done.out, "already current", "Nothing to install")
    assert git("rev-parse", "HEAD", cwd=box.clone) == head
    assert tree_snapshot(box.clone) == before
    assert (box.home / ".claude" / "settings.json").read_bytes() == settings
    assert git("status", "--porcelain", cwd=box.clone) == "", "something the install wrote is not gitignored"


# --- 1c. upstream moved: fast-forward, never re-clone ----------------------------------


def test_an_existing_clone_behind_upstream_is_fast_forwarded(box, origin):
    assert box(FLEET_YES="1").code == 0
    with open(origin / "README.md", "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\nmoved\n")
    git("commit", "-qam", "upstream moves", cwd=origin)
    done = box(FLEET_YES="1")
    assert done.code == 0, done.out
    expect(done.out, "Fast-forwarded")
    assert git("rev-parse", "HEAD", cwd=origin) == git("rev-parse", "HEAD", cwd=box.clone)


# --- 1d. a dirty checkout is refused, and kept ------------------------------------------


def test_a_dirty_checkout_behind_upstream_is_refused_and_kept(box, origin, stubs):
    assert box(FLEET_YES="1").code == 0
    with open(box.clone / "AGENTS.md", "a", encoding="utf-8", newline="\n") as fh:
        fh.write("operator edit\n")
    dirty = (box.clone / "AGENTS.md").read_bytes()
    head = git("rev-parse", "HEAD", cwd=box.clone)
    with open(origin / "README.md", "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\nagain\n")
    git("commit", "-qam", "upstream moves again", cwd=origin)
    (stubs.root / "calls.log").unlink()

    done = box(FLEET_YES="1")
    assert done.code != 0, done.out
    expect(done.out, "uncommitted")
    assert (box.clone / "AGENTS.md").read_bytes() == dirty
    assert git("rev-parse", "HEAD", cwd=box.clone) == head
    assert extension_calls(stubs) == []


# --- 1e. a diverged checkout is refused, never reset --------------------------------------


def test_a_diverged_checkout_is_refused_and_its_commit_kept(box, origin, stubs):
    assert box(FLEET_YES="1").code == 0
    write(box.clone / "local-note.md", "local\n")
    git("add", "local-note.md", cwd=box.clone)
    git("commit", "-qm", "a local commit", cwd=box.clone)
    with open(origin / "README.md", "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\nupstream\n")
    git("commit", "-qam", "upstream moves", cwd=origin)
    head = git("rev-parse", "HEAD", cwd=box.clone)
    (stubs.root / "calls.log").unlink()

    done = box(FLEET_YES="1")
    assert done.code != 0, done.out
    expect(done.out, "diverged")
    assert git("rev-parse", "HEAD", cwd=box.clone) == head
    assert extension_calls(stubs) == []


def test_a_checkout_on_another_branch_is_refused(box, stubs):
    assert box(FLEET_YES="1").code == 0
    git("checkout", "-qb", "mine", cwd=box.clone)
    (stubs.root / "calls.log").unlink()
    done = box(FLEET_YES="1")
    assert done.code != 0, done.out
    expect(done.out, "mine")
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=box.clone).strip() == "mine"
    assert extension_calls(stubs) == []


# --- 1f. a directory that is not a fleet clone is left alone -----------------------------


def test_a_directory_that_is_not_a_fleet_clone_is_left_alone(box, tmp_path):
    other = tmp_path / "not-fleet"
    write(other / "notes.txt", "mine\n")
    done = box(FLEET_YES="1", FLEET_DIR=str(other))
    assert done.code != 0, done.out
    expect(done.out, "not a fleet clone")
    assert [p.name for p in other.iterdir()] == ["notes.txt"]
    assert (other / "notes.txt").read_text(encoding="utf-8") == "mine\n"


# --- 1g. FLEET_DIR is honoured, and printed -------------------------------------------------


def test_fleet_dir_is_honoured_and_the_extension_points_there(box, stubs, tmp_path):
    elsewhere = tmp_path / "elsewhere" / "fleet"
    done = box(FLEET_YES="1", FLEET_DIR=str(elsewhere))
    assert done.code == 0, done.out
    expect(done.out, str(elsewhere))
    assert extension_calls(stubs) == [f"extension install {elsewhere}"]
    assert not box.clone.exists()


# --- 1h. the location is sticky: an installed lead decides the default ---------------------


def test_the_mission_control_leads_checkout_is_the_default(box, stubs, tmp_path):
    """thurbox reuses the lead by name and never moves it, so a second clone would
    get a manifest thurbox never applies."""
    elsewhere = tmp_path / "elsewhere" / "fleet"
    assert box(FLEET_YES="1", FLEET_DIR=str(elsewhere)).code == 0
    sessions = json.dumps([{"name": "X Mission Control", "cwd": str(elsewhere)}])
    place(stubs, "thurbox-cli", f"""
import sys
if sys.argv[1:3] == ["session", "list"]:
    print({sessions!r})
else:
    print("thurbox-cli {FLOOR}")
""")
    done = box(FLEET_YES="1")
    assert done.code == 0, done.out
    expect(done.out, str(elsewhere), "Mission Control")
    assert not box.clone.exists(), "a second clone was made"


def leads(stubs, *rows: dict) -> None:
    """What `thurbox-cli session list --json` answers the bootstrap's probe."""
    listing = json.dumps(list(rows), ensure_ascii=False)
    place(stubs, "thurbox-cli", f"""
import sys
if sys.argv[1:3] == ["session", "list"]:
    sys.stdout.buffer.write({listing!r}.encode("utf-8") + b"\\n")
else:
    print("thurbox-cli {FLOOR}")
""")


# --- 1i. a SECOND fleet, which is a clone of its own and a name of its own -------


def test_a_named_fleet_clones_beside_the_first_and_names_itself(box, stubs, tmp_path):
    """The first fleet's lead is running, and its checkout is sticky — for the
    FIRST fleet. `--name` says this is another one, so it lands beside that
    clone instead of on it, and arrives already naming itself."""
    first = tmp_path / "first" / "fleet"
    assert box(FLEET_YES="1", FLEET_DIR=str(first)).code == 0
    leads(stubs, {"name": "X Mission Control", "cwd": str(first)})

    done = box("--name", "acme", FLEET_YES="1")
    assert done.code == 0, done.out
    second = box.home / "fleet-acme"
    assert (second / "extension.toml.in").is_file(), done.out
    assert (second / "orchestration" / "fleet.conf").read_text(encoding="utf-8").strip() == "NAME=acme"
    assert not (first / "orchestration" / "fleet.conf").exists(), "the first fleet was renamed"
    assert extension_calls(stubs)[-1] == f"extension install {second}"


def test_a_named_leads_checkout_is_sticky_the_way_an_unnamed_ones_is(box, stubs, tmp_path):
    """A fleet that named itself is still the fleet this machine has, so a
    bootstrap naming no directory still lands on it rather than cloning a
    second one beside it."""
    elsewhere = tmp_path / "elsewhere" / "fleet-acme"
    assert box("--name", "acme", FLEET_YES="1", FLEET_DIR=str(elsewhere)).code == 0
    leads(stubs, {"name": "X Mission Control \u00b7 acme", "cwd": str(elsewhere)})

    done = box(FLEET_YES="1")
    assert done.code == 0, done.out
    expect(done.out, str(elsewhere))
    assert not box.clone.exists(), "a second clone was made"


def test_two_leads_and_nothing_saying_which_is_refused_before_anything_is_cloned(box, stubs, tmp_path):
    """Two fleets, and no directory and no name given: which one this run means
    is not a thing to guess, and guessing wrong installs over a live fleet."""
    one, two = tmp_path / "one" / "fleet", tmp_path / "two" / "fleet-lab"
    assert box(FLEET_YES="1", FLEET_DIR=str(one)).code == 0
    assert box("--name", "lab", FLEET_YES="1", FLEET_DIR=str(two)).code == 0
    leads(stubs,
          {"name": "X Mission Control", "cwd": str(one)},
          {"name": "X Mission Control \u00b7 lab", "cwd": str(two)})

    done = box(FLEET_YES="1")
    assert done.code != 0, done.out
    expect(done.out, str(one), str(two), "--dir", "--name")
    assert not box.clone.exists()


def test_a_fleet_that_already_named_itself_is_never_silently_renamed(box, tmp_path):
    """Naming a fleet that is already running is a RENAME, which costs its lead
    its conversation. The bootstrap does not make that choice for anybody."""
    where = tmp_path / "named" / "fleet-acme"
    assert box("--name", "acme", FLEET_YES="1", FLEET_DIR=str(where)).code == 0
    done = box("--name", "lab", FLEET_YES="1", FLEET_DIR=str(where))
    assert done.code != 0, done.out
    expect(done.out, "acme", "lab")
    assert (where / "orchestration" / "fleet.conf").read_text(encoding="utf-8").strip() == "NAME=acme"


def test_a_name_that_is_not_a_bare_token_is_refused(box, tmp_path):
    done = box("--name", "two words", FLEET_YES="1", FLEET_DIR=str(tmp_path / "nope"))
    assert done.code != 0, done.out
    assert not (tmp_path / "nope").exists()


def test_a_leads_checkout_under_a_non_ascii_path_is_still_the_default(box, stubs, tmp_path):
    """thurbox-cli writes UTF-8, and Windows PowerShell 5.1 decodes a native
    command's output in the console's code page unless told otherwise."""
    elsewhere = tmp_path / "José" / "fleet"
    assert box(FLEET_YES="1", FLEET_DIR=str(elsewhere)).code == 0
    sessions = json.dumps([{"name": "X Mission Control", "cwd": str(elsewhere)}], ensure_ascii=False)
    place(stubs, "thurbox-cli", f"""
import sys
if sys.argv[1:3] == ["session", "list"]:
    sys.stdout.buffer.write({sessions!r}.encode("utf-8") + b"\\n")
else:
    print("thurbox-cli {FLOOR}")
""")
    done = box(FLEET_YES="1")
    assert done.code == 0, done.out
    assert not box.clone.exists(), f"a second clone was made:\n{done.out}"


@pytest.mark.skipif(POWERSHELL is None, reason="install.ps1 runs on Windows")
def test_the_bootstrap_leaves_the_operators_console_encoding_as_it_found_it(box, stubs, origin):
    """`irm | iex` runs in the operator's own window: reading thurbox-cli as
    UTF-8 must not leave every later native command in that window decoded so."""
    # No FLEET_DIR: the lead's checkout is only looked for when nothing names one.
    assert box(FLEET_YES="1").code == 0
    place(stubs, "thurbox-cli", f"""
import sys
if sys.argv[1:3] == ["session", "list"]:
    print("[]")
else:
    print("thurbox-cli {FLOOR}")
""")
    env = dict(os.environ, PATH=box.path, FLEET_REPO=str(origin), FLEET_INSTALL_FAMILY="windows", FLEET_YES="1")
    env.pop("FLEET_DIR", None)
    script = REPO / "install.ps1"
    probe = ("[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(437); "
             f"Get-Content -Raw -LiteralPath '{script}' | Invoke-Expression; "
             "'encoding after: ' + [Console]::OutputEncoding.CodePage")
    done = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", probe],
                          input=b"", env=env, capture_output=True)
    out = done.stdout.decode("utf-8", "replace")
    assert "encoding after: 437" in out, out[-2000:]


# --- 1i. a missing required dependency stops before the extension ---------------------------


def test_a_missing_required_dependency_stops_before_the_extension(box, driver, stubs):
    (stubs.bin / ("gh" + EXE)).unlink()
    name, _ = driver.manager
    place(stubs, name, installs({}, fail=("gh", "GitHub.cli")))
    place(stubs, "sudo", SUDO)

    asked = box()
    assert asked.code != 0, asked.out
    expect(asked.out, "--yes", "install --id GitHub.cli" if driver.name == "powershell" else "apt-get install -y gh")
    assert extension_calls(stubs) == []

    failed = box(FLEET_YES="1")
    assert failed.code != 0, failed.out
    expect(failed.out, "failed: gh", "Stopped before the extension")
    assert extension_calls(stubs) == []


# --- 1j. no git ----------------------------------------------------------------------------


def test_no_git_and_nobody_to_ask_is_refused_with_the_command(box, driver, stubs):
    (stubs.bin / ("git" + EXE)).unlink()
    name, packages = driver.manager
    place(stubs, name, installs(packages, answers={"git": GIT}))
    place(stubs, "sudo", SUDO)
    done = box()
    assert done.code != 0, done.out
    expect(done.out, "git", driver.git_line)
    assert stubs.calls(name) == []
    assert not box.clone.exists()


def test_no_git_and_yes_installs_git_through_the_os_manager_then_clones(box, driver, stubs):
    (stubs.bin / ("git" + EXE)).unlink()
    name, packages = driver.manager
    place(stubs, name, installs(packages, answers={"git": GIT}))
    place(stubs, "sudo", SUDO)
    done = box(FLEET_YES="1")
    assert done.code == 0, done.out
    if driver.name == "sh":
        # A fresh image ships with no package lists: refreshed first, or git is "not found".
        assert stubs.calls("sudo") == ["sudo apt-get update", driver.git_line], stubs.calls("sudo")
    else:
        assert driver.git_line in stubs.calls(name), stubs.calls(name)
    assert (box.clone / "extension.toml.in").is_file()


def test_no_git_and_no_manager_says_to_install_git(box, driver, stubs):
    (stubs.bin / ("git" + EXE)).unlink()
    done = box(FLEET_YES="1")
    assert done.code != 0, done.out
    expect(done.out, "git")
    refute(done.out, "Traceback")
    assert not box.clone.exists()
