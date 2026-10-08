"""`fleet install` keeps the reconciler up across a reboot, behind the plan's one question.

The loop's lock and heartbeat prove it is up; nothing brought it back once a
machine rebooted, and three leads found it down with no down flag eighteen
times. So the install offers the platform's own user service, as a line of
the plan it asks about once: a systemd user unit that runs `reconcile serve`
and restarts it, or on Windows a logon script in the Startup folder that runs
`reconcile ensure`. `--no-service` leaves it out, a machine with neither says
so and fails nothing, and a second run writes nothing and restarts nothing.
"""

from __future__ import annotations

import hashlib
import os
import sys

import pytest

from harness import expect, refute
from installkit import fleet, full_machine, installs, machine, place, plain, tree_snapshot

SYSTEMCTL = """
import sys
a = sys.argv[1:]
raise SystemExit(0 if a[:1] == ["--user"] else 2)
"""


def unit_name(checkout) -> str:
    return "fleet-reconcile-" + hashlib.sha1(os.path.realpath(checkout).encode("utf-8")).hexdigest()[:8]


def complete(stubs, family="posix"):
    machine(stubs, full_machine(family))
    place(stubs, "apt-get" if family == "posix" else "winget", installs({}))


def home(stubs):
    return stubs.root.parent / "home"


@pytest.mark.skipif(sys.platform == "win32", reason="systemd quotes a backslash path, and none ever reaches a unit")
def test_systemd_gets_a_user_unit_that_serves_the_loop_and_restarts_it(stubs, checkout):
    complete(stubs)
    place(stubs, "systemctl", SYSTEMCTL)
    done = fleet(checkout, "install", "--yes", path=str(stubs.bin), family="posix", FLEET_SERVICE_MANAGER="systemd")
    assert done.code == 0, done.out

    unit = home(stubs) / ".config" / "systemd" / "user" / f"{unit_name(checkout)}.service"
    expect(plain(done.stdout), "reconciler", str(unit))
    text = unit.read_text(encoding="utf-8")
    expect(text, f"--project {checkout}", "fleet reconcile serve", "Restart=always", "WantedBy=default.target",
           "Environment=")
    name = f"{unit_name(checkout)}.service"
    calls = [c for c in stubs.calls("systemctl") if "show-environment" not in c]
    assert calls == ["systemctl --user daemon-reload", f"systemctl --user enable {name}",
                     f"systemctl --user restart {name}"], calls

    # The second run: nothing to install, nothing written, nothing restarted.
    stubs.root.joinpath("calls.log").unlink()
    before = unit.read_bytes()
    again = fleet(checkout, "install", path=str(stubs.bin), family="posix", FLEET_SERVICE_MANAGER="systemd")
    assert again.code == 0, again.out
    expect(again.stdout, "Nothing to install")
    assert unit.read_bytes() == before
    assert [c for c in stubs.calls("systemctl") if "show-environment" not in c] == []


def test_windows_gets_a_logon_script_that_ensures_the_loop(stubs, checkout):
    complete(stubs, "windows")
    done = fleet(checkout, "install", "--yes", path=str(stubs.bin), family="windows",
                 FLEET_SERVICE_MANAGER="startup")
    assert done.code == 0, done.out
    startup = home(stubs) / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    script = startup / f"{unit_name(checkout)}.cmd"
    text = script.read_text(encoding="utf-8")
    expect(text, str(checkout), "fleet reconcile ensure", "/min")
    assert b"\r\n" in script.read_bytes(), "cmd.exe reads a batch file with CRLF line endings"


def test_no_service_leaves_it_out_and_says_how_to_add_it(stubs, checkout):
    complete(stubs)
    place(stubs, "systemctl", SYSTEMCTL)
    done = fleet(checkout, "install", "--yes", "--no-service", path=str(stubs.bin), family="posix",
                 FLEET_SERVICE_MANAGER="systemd")
    assert done.code == 0, done.out
    assert not (home(stubs) / ".config" / "systemd").exists()
    assert [c for c in stubs.calls("systemctl") if "show-environment" not in c] == []


def test_a_machine_with_no_service_manager_fails_nothing(stubs, checkout):
    complete(stubs)
    before = tree_snapshot(home(stubs))
    done = fleet(checkout, "install", "--yes", path=str(stubs.bin), family="posix", FLEET_SERVICE_MANAGER="none")
    assert done.code == 0, done.out
    expect(plain(done.stdout), "no user service manager here")
    after = tree_snapshot(home(stubs))
    refute("\n".join(after), "systemd", "Startup")
    assert set(before) <= set(after)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the systemd branch")
def test_systemd_detected_only_where_a_user_manager_answers(stubs, checkout):
    """Not pinned: a `systemctl` that cannot reach a user manager (a container,
    WSL without systemd) is no service manager, and the plan has no row for it."""
    complete(stubs)
    place(stubs, "systemctl", "raise SystemExit(1)\n")
    done = fleet(checkout, "install", "--yes", path=str(stubs.bin), family="posix", FLEET_SERVICE_MANAGER=None)
    assert done.code == 0, done.out
    expect(plain(done.stdout), "no user service manager here")
    assert not (home(stubs) / ".config" / "systemd").exists()
