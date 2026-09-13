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
    cannot: its pid may since belong to somebody else.
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
