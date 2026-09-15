"""`fleet install`: one plan, one question, every route run exactly, and a second run that does nothing.

The install is the one command a new operator runs, once, and never again —
which is what makes a regression in it invisible to everyone already set up
and total for the next person. So it is driven here on machines built from
stand-ins, on BOTH OS families through `install_family()`'s pin, where
"missing" means missing and a package manager really puts the tool on PATH:

  - every gap is listed with the exact command that closes it, a manual row
    as "you run", and the question is asked once, or not at all when nothing
    is missing;
  - no terminal and no `--yes` prints the plan and installs nothing;
  - each manager's argv is exact: winget's two agreement flags, sudo only when
    not root, pacman's no-confirm, an installer run through its family's shell;
  - one install failing is reported and the rest still run;
  - a missing required row stops before the extension, and preflight is last.
"""

from __future__ import annotations

import io
import os
import sys

import pytest
from harness import expect, refute
from installkit import (
    GH_LOGGED_OUT, SUDO, WINDOWS, fleet, full_machine, installs, launcher, load_install, machine, place, plain,
    tree_snapshot,
)

FAMILIES = ("posix", "windows")


def run_install(checkout, stubs, family, *args, stdin="", **env):
    return fleet(checkout, "install", *args, stdin=stdin, path=str(stubs.bin), family=family, **env)


def manager_calls(stubs) -> list[str]:
    return [c for tool in ("winget", "apt-get", "dnf", "pacman", "brew", "sudo", "npm", "sh", "powershell")
            for c in stubs.calls(tool)]


def gap_machine(stubs, family):
    """gh logged out, and glab, quota-axi and the multiplexer missing, with the family's manager."""
    mux = "psmux" if family == "windows" else "tmux"
    tools = full_machine(family) | {"gh": GH_LOGGED_OUT}
    machine(stubs, tools, without=("glab", "quota-axi", mux))
    if family == "windows":
        place(stubs, "winget", installs({"GLab.GLab": "glab", "marlocarlo.psmux": "psmux"}))
    else:
        place(stubs, "apt-get", installs({"glab": "glab", "tmux": "tmux"}))
        place(stubs, "sudo", SUDO)
    place(stubs, "npm", installs({"quota-axi": "quota-axi"}))


# --- the plan, and the one question --------------------------------------------


@pytest.mark.parametrize("family", FAMILIES)
def test_the_plan_names_every_gap_with_the_command_that_closes_it(stubs, checkout, family):
    gap_machine(stubs, family)
    done = run_install(checkout, stubs, family)
    out = plain(done.out)
    if family == "windows":
        expect(out, "winget install --id GLab.GLab -e --accept-source-agreements --accept-package-agreements",
               "winget install --id marlocarlo.psmux -e")
    else:
        expect(out, "sudo apt-get install -y glab", "sudo apt-get install -y tmux")
    expect(out, "npm install -g quota-axi", "you run", "gh auth login", ".claude/skills")


@pytest.mark.parametrize("family", FAMILIES)
def test_no_terminal_and_no_yes_prints_the_plan_installs_nothing_and_says_yes(stubs, checkout, family):
    gap_machine(stubs, family)
    done = run_install(checkout, stubs, family)
    assert done.code == 1, done.out
    expect(done.out, "--yes")
    assert manager_calls(stubs) == [], manager_calls(stubs)
    assert "extension install" not in stubs.calls("extension")
    assert not os.path.lexists(checkout / ".claude" / "skills"), "a refused install still linked the skills"
    assert not (stubs.root.parent / "home" / ".claude" / "settings.json").exists()


@pytest.mark.parametrize("family", FAMILIES)
def test_yes_runs_every_route_exactly_then_the_extension_then_preflight(stubs, checkout, family):
    gap_machine(stubs, family)
    done = run_install(checkout, stubs, family, "--yes")
    assert done.code == 1, "gh auth is still missing, and it is required"
    calls = manager_calls(stubs)
    npm = "npm install -g quota-axi"
    if family == "windows":
        assert calls == [
            "winget install --id marlocarlo.psmux -e --accept-source-agreements --accept-package-agreements",
            "winget install --id GLab.GLab -e --accept-source-agreements --accept-package-agreements",
            npm,
        ], calls
    else:
        assert calls == [
            "apt-get install -y tmux", "apt-get install -y glab",
            "sudo apt-get install -y tmux", "sudo apt-get install -y glab", npm,
        ], calls
    out = plain(done.stdout)
    refute(out, "gh auth login\n  running")
    expect(out, "REQUIRED", "Stopped before the extension", "gh auth")
    assert stubs.calls("extension") == [], "the extension ran with a required row missing"


@pytest.mark.parametrize("family", FAMILIES)
def test_a_complete_machine_asks_nothing_installs_nothing_and_changes_nothing(stubs, checkout, family):
    machine(stubs, full_machine(family))
    place(stubs, "winget" if family == "windows" else "apt-get", installs({}))
    first = run_install(checkout, stubs, family, "--yes")
    assert first.code == 0, first.out
    expect(first.stdout, "extension: installed (stand-in)", "Every required dependency is present")
    settings = stubs.root.parent / "home" / ".claude" / "settings.json"
    before = (tree_snapshot(checkout), settings.read_bytes())

    stubs.root.joinpath("calls.log").unlink()
    again = run_install(checkout, stubs, family)
    assert again.code == 0, again.out
    expect(again.stdout, "Nothing to install")
    refute(again.out, "[y/N]", "--yes")
    assert manager_calls(stubs) == []
    assert (tree_snapshot(checkout), settings.read_bytes()) == before
    assert stubs.calls("extension") == [f"extension install {checkout}"], "the extension step is re-applied"


@pytest.mark.parametrize("family", FAMILIES)
def test_one_failed_install_is_reported_and_the_rest_still_run(stubs, checkout, family):
    machine(stubs, full_machine(family), without=("glab", "quota-axi"))
    if family == "windows":
        place(stubs, "winget", installs({}, fail=("GLab.GLab",)))
    else:
        place(stubs, "apt-get", installs({}, fail=("glab",)))
        place(stubs, "sudo", SUDO)
    place(stubs, "npm", installs({"quota-axi": "quota-axi"}))
    done = run_install(checkout, stubs, family, "--yes")
    assert done.code == 1, done.out
    expect(plain(done.out), "failed: glab")
    assert stubs.calls("npm") == ["npm install -g quota-axi"], "the install after the failed one never ran"
    assert (stubs.bin / ("quota-axi" + (".exe" if WINDOWS else ""))).exists()
    assert stubs.calls("extension"), "a recommended failure is no reason to skip the extension"


@pytest.mark.parametrize(("manager", "want"), [
    ("brew", "brew install glab"),
    ("dnf", "sudo dnf install -y glab"),
    ("pacman", "sudo pacman -S --needed --noconfirm glab"),
])
def test_each_posix_manager_gets_its_own_argv(stubs, checkout, manager, want):
    machine(stubs, full_machine("posix"), without=("glab",))
    place(stubs, manager, installs({"glab": "glab"}))
    place(stubs, "sudo", SUDO)
    done = run_install(checkout, stubs, "posix", "--yes")
    assert done.code == 0, done.out
    assert stubs.calls(want.split()[0]) [:1] == [want], manager_calls(stubs)


@pytest.mark.parametrize("family", FAMILIES)
def test_an_installer_one_liner_runs_through_its_familys_shell(stubs, checkout, family):
    machine(stubs, full_machine(family), without=("thurbox-cli",))
    shell = "powershell" if family == "windows" else "sh"
    place(stubs, shell, installs({}))
    place(stubs, "winget" if family == "windows" else "apt-get", installs({}))
    done = run_install(checkout, stubs, family, "--yes")
    calls = stubs.calls(shell)
    if family == "windows":
        assert calls == ["powershell -ExecutionPolicy ByPass -c irm "
                         "https://raw.githubusercontent.com/Thurbeen/thurbox/main/scripts/install.ps1 | iex"], calls
    else:
        assert calls == ["sh -c curl -fsSL https://raw.githubusercontent.com/Thurbeen/thurbox/main/scripts/install.sh"
                         " | sh"], calls
    assert done.code == 1, "the stand-in installed nothing, so thurbox-cli is still missing"


def test_dev_adds_the_gate_tier(stubs, checkout):
    machine(stubs, full_machine("posix"))
    place(stubs, "brew", installs({"lua": "lua", "prek": "prek"}))
    plan = plain(run_install(checkout, stubs, "posix").out)
    refute(plan, "brew install lua")
    dev = plain(run_install(checkout, stubs, "posix", "--dev").out)
    expect(dev, "brew install lua", "brew install prek")


def test_usage(stubs, checkout):
    machine(stubs, full_machine("posix"))
    assert run_install(checkout, stubs, "posix", "--bogus").code == 2
    done = run_install(checkout, stubs, "posix", "--help")
    assert done.code == 0
    expect(done.stdout, "--yes", "--dev")


# --- in-process: the terminal, root, and the argv rules ------------------------


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(("answer", "code"), [("y\n", 0), ("n\n", 1)])
def test_a_terminal_is_asked_once_and_the_answer_decides(stubs, checkout, monkeypatch, capsys, answer, code):
    machine(stubs, full_machine("posix"), without=("glab",))
    place(stubs, "brew", installs({"glab": "glab"}))
    monkeypatch.setenv("PATH", str(stubs.bin))
    monkeypatch.setenv("FLEET_INSTALL_FAMILY", "posix")
    monkeypatch.setattr(sys, "stdin", Terminal(answer))
    install = load_install()
    assert install.main([], checkout=str(checkout)) == code
    out = plain(capsys.readouterr().out)
    assert out.count("[y/N]") == 1, out
    if code == 0:
        assert stubs.calls("brew") == ["brew install glab"]
    else:
        assert stubs.calls("brew") == []
        expect(out, "Nothing was installed")


def test_sudo_is_dropped_when_already_root_and_kept_otherwise():
    install = load_install()
    apt = ("sudo", "apt-get", "install", "-y", "gh")
    assert install.prepare(apt, root=False) == list(apt)
    assert install.prepare(apt, root=True) == ["apt-get", "install", "-y", "gh"]


def test_winget_gets_both_agreement_flags_once():
    install = load_install()
    plan = ("winget", "install", "--id", "GitHub.cli", "-e")
    want = [*plan, "--accept-source-agreements", "--accept-package-agreements"]
    assert install.prepare(plan, root=False) == want
    assert install.prepare(tuple(want), root=False) == want


def test_pacman_is_told_not_to_ask_a_second_time():
    install = load_install()
    assert install.prepare(("sudo", "pacman", "-S", "--needed", "tmux"), root=True) == [
        "pacman", "-S", "--needed", "--noconfirm", "tmux"]


@pytest.mark.skipif(not WINDOWS, reason="npm is npm.cmd only on Windows")
def test_npm_is_found_as_npm_cmd_on_windows(stubs, checkout, tmp_path):
    """CreateProcess appends .exe and nothing else, so a bare `npm` never finds npm.cmd."""
    machine(stubs, full_machine("windows"), without=("quota-axi",))
    place(stubs, "winget", installs({}))
    hidden = tmp_path / "npm-real"
    hidden.mkdir()
    import shutil

    shutil.copy2(launcher(), hidden / "npm.exe")
    (stubs.root / "scripts" / "npm.py").write_text("print('added 1 package')\n", encoding="utf-8")
    (stubs.bin / "npm.cmd").write_text(f'@"{hidden / "npm.exe"}" %*\r\n', encoding="utf-8")
    run_install(checkout, stubs, "windows", "--yes")
    assert stubs.calls("npm") == ["npm install -g quota-axi"]


# --- the extension step -----------------------------------------------------------


def test_the_extension_step_calls_the_checkouts_install_extension_main(checkout, stubs, capsys):
    install = load_install()
    assert install.extension_step(str(checkout)) == 0
    assert stubs.calls("extension") == [f"extension install {checkout}"]


def test_a_checkout_without_the_extension_module_says_so(checkout, capsys):
    (checkout / "scripts" / "lib" / "install_extension.py").unlink()
    (checkout / "scripts" / "install-extension.sh").unlink(missing_ok=True)
    install = load_install()
    assert install.extension_step(str(checkout)) == 1
    expect(capsys.readouterr().out, "scripts/lib/install_extension.py")
