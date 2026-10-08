"""The user service that brings this checkout's reconciler back after a reboot.

WHY. The loop's lock and heartbeat prove it is up, and nothing brought it back
once it was not: three leads found it "down — not running, and no down flag"
eighteen times, after a reboot, a WSL restart or a lead restart, while ready
work waited and the operator typed "I merged N". The platform already has a
thing whose job is to start a user's process at boot and keep it running.

TWO SHAPES, by `fleet_platform.service_manager()`:

  systemd   a user unit running `fleet reconcile serve`, Restart=always.
            `serve` is the loop in the foreground; it waits rather than exits
            while the operator's down flag stands or another loop holds the
            lock, so the unit never fights a stop and never runs a twin.
  startup   a logon script in the Windows Startup folder running
            `fleet reconcile ensure`, which leaves the detached loop behind.
            Not a scheduled task: one at logon needs an administrator, and one
            that ran the loop itself would be ended by its 72-hour limit.

Anywhere else — macOS, WSL without systemd, a container — there is none, and
the lead's SessionStart `ensure --if-lead` is what brings the loop back.

NEITHER CLEARS A DOWN FLAG. Only `fleet reconcile start` does, so a stop the
operator asked for survives the reboot this exists for.

ONE PER CHECKOUT. Named by a hash of the checkout's real path, so a second
fleet on the machine gets a unit of its own. The unit carries the PATH `fleet
install` ran with: a systemd user manager's own holds none of the directories
uv, thurbox-cli or a forge CLI are installed in.

Written by `fleet install`, as a line of its plan behind its one question;
`--no-service` leaves it out. `systemctl --user disable --now <unit>`, or
deleting the script, removes it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass


def _load_sibling(name: str, filename: str):
    """A scripts/lib module under a `fleet_` key, never shadowing the standard library."""
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fleet_platform = _load_sibling("fleet_platform", "fleet_platform.py")

HEADER = ("Written by `fleet install`: keeps the reconciler of the fleet checkout",
          "below up across reboots. `fleet reconcile stop` still stops it for good.")


@dataclass(frozen=True)
class Service:
    kind: str
    path: str
    text: str
    activate: tuple[tuple[str, ...], ...]


def name(checkout: str) -> str:
    return "fleet-reconcile-" + hashlib.sha1(os.path.realpath(checkout).encode("utf-8")).hexdigest()[:8]


def _uv() -> str:
    return shutil.which("uv") or os.environ.get("UV", "")


def _systemd_word(word: str) -> str:
    """One ExecStart or Environment word: `%` is a specifier, quotes when it holds a space."""
    word = word.replace("%", "%%")
    if any(c.isspace() or c in "\"'\\" for c in word):
        return '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return word


def _systemd(checkout: str, uv: str) -> Service:
    unit = name(checkout) + ".service"
    command = " ".join(_systemd_word(w) for w in (uv, "run", "--project", checkout, "--frozen", "--quiet",
                                                   "fleet", "reconcile", "serve"))
    text = "\n".join([
        *(f"# {line}" for line in HEADER),
        "[Unit]",
        f"Description=fleet reconciler for {checkout.replace('%', '%%')}",
        "",
        "[Service]",
        f"ExecStart={command}",
        f"Environment={_systemd_word('PATH=' + os.environ.get('PATH', ''))}",
        "Restart=always",
        "RestartSec=30",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    ctl = ("systemctl", "--user")
    return Service("systemd", os.path.join(base, "systemd", "user", unit), text,
                   ((*ctl, "daemon-reload"), (*ctl, "enable", unit), (*ctl, "restart", unit)))


def _startup(checkout: str, uv: str) -> Service:
    def q(word: str) -> str:
        return '"' + word.replace("%", "%%") + '"'

    lines = [
        "@echo off",
        *(f"rem {line}" for line in HEADER),
        f'start "fleet reconciler" /min {q(uv)} run --project {q(checkout)} --frozen --quiet fleet reconcile ensure',
        "",
    ]
    appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    folder = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    # cmd.exe reads a batch file line by line, and with LF alone it misreads labels and long lines.
    return Service("startup", os.path.join(folder, name(checkout) + ".cmd"), "\r\n".join(lines), ())


def service(checkout: str) -> Service | None:
    """This machine's service for `checkout`, or None where nothing would run one."""
    kind = fleet_platform.service_manager()
    if not kind:
        return None
    return (_systemd if kind == "systemd" else _startup)(checkout, _uv())


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    except OSError:
        return None


def state(svc: Service) -> tuple[str, str]:
    """"ok", "add", "update" or "refuse", and why."""
    if not _uv():
        return "refuse", "uv is not on PATH, and the service runs fleet through it"
    held = _read(svc.path)
    if held == svc.text:
        return "ok", "already in place"
    return ("add", "not there yet") if held is None else ("update", "it names another command or PATH")


def apply(svc: Service) -> tuple[bool, str]:
    """Write it and hand it to its manager, only when it is not already what it should be."""
    current, why = state(svc)
    if current in ("ok", "refuse"):
        return current == "ok", why
    try:
        os.makedirs(os.path.dirname(svc.path), exist_ok=True)
        fleet_platform.write_record(svc.path, svc.text)
    except OSError as exc:
        return False, f"could not write {svc.path} ({exc})"
    for argv in svc.activate:
        try:
            done = subprocess.run(list(argv), capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=60, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"{' '.join(argv)} failed ({exc})"
        if done.returncode:
            detail = (done.stderr or done.stdout).strip().splitlines()
            return False, f"{' '.join(argv)} exited {done.returncode}" + (f": {detail[-1]}" if detail else "")
    return True, f"{'written' if current == 'add' else 'rewritten'}: {svc.path}"


def installed(checkout: str) -> str:
    """The path of this checkout's service if one is written here, else ""."""
    svc = service(checkout)
    return svc.path if svc and os.path.isfile(svc.path) else ""
