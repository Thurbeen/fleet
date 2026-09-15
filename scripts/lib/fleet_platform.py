"""The platform seam: every place fleet behaves differently on POSIX and Windows.

One function per difference, with both branches inside it, so no caller ever
asks `os.name`. `tests/test_platform.py` proves each branch on the OS that
takes it.

Named `fleet_platform` and loaded by path (`queue.py`'s `_load_sibling`),
never as `platform`: a script run from this directory has it first on
sys.path, where that name would shadow the standard library's module.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time

WINDOWS = os.name == "nt"

if WINDOWS:
    import msvcrt
else:
    import fcntl


# --- directories --------------------------------------------------------------


def _base(xdg: str, windows: str, home_relative: str) -> str:
    return (
        os.environ.get(xdg)
        or (WINDOWS and os.environ.get(windows))
        or os.path.join(os.path.expanduser("~"), home_relative)
    )


def thurbox_config_dir() -> str:
    """Where thurbox keeps hosts.toml and hooks/, by thurbox's own rule.

    That rule is `src/paths/mod.rs`: `THURBOX_CONFIG_DIR` first, which thurbox
    pins into every session it spawns so a command run inside one reads the
    same config; then `XDG_CONFIG_HOME` on every OS; then `%APPDATA%` on
    Windows and `~/.config` elsewhere.
    """
    return os.environ.get("THURBOX_CONFIG_DIR") or os.path.join(
        _base("XDG_CONFIG_HOME", "APPDATA", ".config"), "thurbox"
    )


def fleet_data_dir() -> str:
    """Fleet's own data: under `XDG_DATA_HOME`, else `%LOCALAPPDATA%` or `~/.local/share`."""
    return os.path.join(
        _base("XDG_DATA_HOME", "LOCALAPPDATA", os.path.join(".local", "share")), "fleet"
    )


# --- installing ---------------------------------------------------------------


def install_family() -> str:
    """Which OS family's install routes apply here: "windows" or "posix".

    "windows" means winget and PowerShell installers; "posix" means a distro's
    package manager or Homebrew, and `sh` installers. preflight's table keys
    every route by it, so nothing there asks which OS it is on.

    `FLEET_INSTALL_FAMILY` pins it, so either family's routes can be driven
    against stand-in package managers on any OS; any other value is ignored.
    """
    pinned = os.environ.get("FLEET_INSTALL_FAMILY")
    if pinned in ("windows", "posix"):
        return pinned
    return "windows" if WINDOWS else "posix"


def running_as_root() -> bool:
    """Whether a package manager's `sudo` would be redundant here.

    POSIX: the effective uid is 0, as in a container. Windows: never — no route
    there goes through sudo, and winget installs for the user who runs it.
    """
    return not WINDOWS and os.geteuid() == 0


def merged_path(current: str, *more: str) -> str:
    """`current`, then every entry of `more` it does not already hold, in order."""
    seen, entries = set(), []
    for value in (current, *more):
        for entry in value.split(os.pathsep):
            key = os.path.normcase(entry)
            if entry and key not in seen:
                seen.add(key)
                entries.append(entry)
    return os.pathsep.join(entries)


def refresh_path() -> None:
    """Let this process find what an installer just put on the user's PATH.

    Windows: an installer writes the new directory into the registry's user or
    machine `Path`, which a running process never re-reads, so the next line
    would not find the tool it just installed. Both are read and appended to
    this process's PATH, which keeps what it already had first. POSIX: a
    package manager installs onto a PATH that is already there; nothing to do.
    """
    if not WINDOWS:
        return
    import winreg

    values = []
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(hive, key) as handle:
                values.append(os.path.expandvars(winreg.QueryValueEx(handle, "Path")[0]))
        except OSError:
            continue
    os.environ["PATH"] = merged_path(os.environ.get("PATH", ""), *values)


# --- directory links ------------------------------------------------------------


def is_dir_link(path: str) -> bool:
    """Whether `path` is a link to a directory as `make_dir_link` makes one: a
    symlink, or on Windows a junction, which `os.path.islink` does not see."""
    if os.path.islink(path):
        return True
    if not WINDOWS:
        return False
    import stat

    try:
        return os.lstat(path).st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT
    except (OSError, AttributeError):
        return False


def make_dir_link(link: str, target: str) -> None:
    """Make `link` point at the directory `target`, replacing a link already there.

    POSIX: a RELATIVE symlink, so a checkout that moves keeps it. Windows: a
    directory junction, because a symlink needs Developer Mode or an elevated
    process and a junction needs neither; a junction holds an absolute path, so
    a moved checkout gets it re-made by the next install. Never replaces a
    file or a real directory: that is the caller's decision to make first.
    """
    if is_dir_link(link):
        if WINDOWS:
            os.rmdir(link)  # removes a junction, never what it points at
        else:
            os.unlink(link)
    if WINDOWS:
        import _winapi

        _winapi.CreateJunction(os.path.abspath(target), os.path.abspath(link))
    else:
        os.symlink(os.path.relpath(os.path.abspath(target), os.path.dirname(os.path.abspath(link))), link)


# --- records ------------------------------------------------------------------

# A reader holding a record open makes Windows refuse the replace until it lets
# go, which takes milliseconds. Doubling from 10 ms waits 1.27 s in all, and a
# handle held longer than that surfaces as the PermissionError it is.
REPLACE_ATTEMPTS = 8
REPLACE_FIRST_WAIT = 0.01


def write_record(path: str, text: str) -> None:
    """Replace `path` with `text` in one step, as UTF-8 with LF line endings on every OS.

    `newline="\\n"` because text mode on Windows otherwise writes CRLF, which
    is how records came out there. Readers still accept CRLF: a record can
    arrive from a Windows host, or from an editor. UTF-8 because Windows'
    default is the ANSI code page, which cannot even encode the glyphs fleet
    puts in titles, and every other reader of a record — the pane, a lead on
    another OS — reads UTF-8.
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    wait = REPLACE_FIRST_WAIT
    for attempt in range(1, REPLACE_ATTEMPTS + 1):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS:
                with contextlib.suppress(OSError):
                    os.remove(tmp)
                raise
            time.sleep(wait)
            wait *= 2


def append_record(path: str, text: str) -> None:
    """Append `text` to `path`, with LF line endings on every OS."""
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# --- the lock -----------------------------------------------------------------


class LockHeld(Exception):
    """Another open handle holds the lock."""


@contextlib.contextmanager
def exclusive_lock(path: str):
    """Hold an exclusive lock on `path` for the block, or raise LockHeld at once.

    The OS drops the lock when the holder's handle closes, however its process
    ended, so a lock nobody holds proves its last holder is gone. A pidfile
    cannot: its pid may since belong to somebody else. The converse is not
    instant: Windows releases a killed process's locks when it gets to them,
    so a lock can still read as held for a moment after its holder died.
    """
    fh = open(path, "a+b")
    try:
        try:
            if WINDOWS:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError) as exc:
            raise LockHeld(path) from exc
        try:
            yield
        finally:
            if WINDOWS:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


# --- processes ----------------------------------------------------------------


def spawn_detached(argv: list[str], **popen) -> subprocess.Popen:
    """Start `argv` so that it outlives this process and the session it runs in.

    POSIX: a new session, so the hangup that ends a terminal or an ssh session
    never reaches it. Windows: no console, a process group of its own, and out
    of the job an ssh session kills everything in when it ends. Measured on
    Windows 11, a child started without CREATE_BREAKAWAY_FROM_JOB died with
    its parent's ssh session, and one started with it kept running.
    """
    popen.setdefault("stdin", subprocess.DEVNULL)
    if WINDOWS:
        popen["creationflags"] = popen.get("creationflags", 0) | (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_BREAKAWAY_FROM_JOB
        )
    else:
        popen["start_new_session"] = True
    return subprocess.Popen(argv, **popen)


def alive(pid: int) -> bool:
    """Whether some process has this pid, asked without disturbing it.

    Never `os.kill(pid, 0)` on Windows: there 0 is CTRL_C_EVENT, so the
    question interrupts the answer. On POSIX signal 0 is the standard probe
    and delivers nothing. A pid can be reused, so this cannot say it is the
    SAME process; `exclusive_lock` is what can.
    """
    if pid <= 0:
        return False
    if WINDOWS:
        return _windows_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists; it is somebody else's
    return True


def _windows_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    synchronize, error_access_denied, wait_timeout = 0x00100000, 5, 0x102
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    handle = k32.OpenProcess(synchronize, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied
    try:
        return k32.WaitForSingleObject(wintypes.HANDLE(handle), 0) == wait_timeout
    finally:
        k32.CloseHandle(wintypes.HANDLE(handle))


# --- `fleet paths` --------------------------------------------------------------


def checkout_dir() -> str:
    """The fleet checkout this module belongs to."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def claude_settings_file() -> str:
    """Claude Code's user settings: `CLAUDE_CONFIG_DIR`, else `~/.claude`, on every OS.

    Where `fleet install` merges the reconciler's Stop nudge. Claude Code merges
    these with the settings thurbox hands each worker, and thurbox leaves them
    alone, where it rewrites its own hooks file on every start.
    """
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "settings.json")


def paths() -> dict[str, str]:
    """Every path a skill, a hook or a message names, as this machine spells it."""
    return {
        "checkout": checkout_dir(),
        "thurbox-config": thurbox_config_dir(),
        "thurbox-hooks": os.path.join(thurbox_config_dir(), "hooks", "claude.json"),
        "fleet-data": fleet_data_dir(),
        "claude-settings": claude_settings_file(),
    }


def main(argv: list[str]) -> int:
    """`fleet paths [name]`: every path as `name<TAB>path`, or one name's path alone."""
    known = paths()
    if not argv:
        for name, path in known.items():
            print(f"{name}\t{path}")
        return 0
    if argv[0] in ("-h", "--help"):
        print("usage: fleet paths [" + "|".join(known) + "]")
        return 0
    if len(argv) == 1 and argv[0] in known:
        print(known[argv[0]])
        return 0
    print(f"fleet paths: no path named {' '.join(argv)!r} (have: {', '.join(known)})", file=sys.stderr)
    return 2
