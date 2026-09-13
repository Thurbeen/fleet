"""The platform seam, proven on whichever OS runs it.

Every test here runs on both POSIX and Windows. Where the two differ, each
branch is asserted only on the OS that takes it, because a branch faked on the
other OS proves nothing about the real one.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from conftest import LIB

WINDOWS = os.name == "nt"


def _clear(monkeypatch, *names):
    for name in names:
        monkeypatch.delenv(name, raising=False)


# --- directories --------------------------------------------------------------


def test_thurbox_config_dir_honours_thurbox_own_pin(fp, monkeypatch, tmp_path):
    monkeypatch.setenv("THURBOX_CONFIG_DIR", str(tmp_path / "pinned"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert fp.thurbox_config_dir() == str(tmp_path / "pinned")


def test_thurbox_config_dir_prefers_xdg_on_every_os(fp, monkeypatch, tmp_path):
    _clear(monkeypatch, "THURBOX_CONFIG_DIR")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    assert fp.thurbox_config_dir() == os.path.join(str(tmp_path / "xdg"), "thurbox")


@pytest.mark.skipif(not WINDOWS, reason="the %APPDATA% branch")
def test_thurbox_config_dir_is_appdata_on_windows(fp, monkeypatch, tmp_path):
    _clear(monkeypatch, "THURBOX_CONFIG_DIR", "XDG_CONFIG_HOME")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert fp.thurbox_config_dir() == os.path.join(str(tmp_path), "thurbox")


@pytest.mark.skipif(WINDOWS, reason="the ~/.config branch")
def test_thurbox_config_dir_is_home_config_elsewhere(fp, monkeypatch, tmp_path):
    _clear(monkeypatch, "THURBOX_CONFIG_DIR", "XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert fp.thurbox_config_dir() == os.path.join(str(tmp_path), ".config", "thurbox")


def test_fleet_data_dir_prefers_xdg_on_every_os(fp, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert fp.fleet_data_dir() == os.path.join(str(tmp_path / "xdg"), "fleet")


@pytest.mark.skipif(not WINDOWS, reason="the %LOCALAPPDATA% branch")
def test_fleet_data_dir_is_localappdata_on_windows(fp, monkeypatch, tmp_path):
    _clear(monkeypatch, "XDG_DATA_HOME")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert fp.fleet_data_dir() == os.path.join(str(tmp_path), "fleet")


@pytest.mark.skipif(WINDOWS, reason="the ~/.local/share branch")
def test_fleet_data_dir_is_local_share_elsewhere(fp, monkeypatch, tmp_path):
    _clear(monkeypatch, "XDG_DATA_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert fp.fleet_data_dir() == os.path.join(str(tmp_path), ".local", "share", "fleet")


# --- records ------------------------------------------------------------------


def test_write_record_puts_lf_on_disk(fp, tmp_path):
    path = tmp_path / "task.yaml"
    fp.write_record(str(path), "id: one\ntitle: two\n")
    assert path.read_bytes() == b"id: one\ntitle: two\n"
    assert [p.name for p in tmp_path.iterdir()] == ["task.yaml"]


def test_records_are_utf8_whatever_the_locale(fp, tmp_path):
    text = "title: dash — moon ◐ rocket \U0001f680\n"
    fp.write_record(str(tmp_path / "task.yaml"), text)
    fp.append_record(str(tmp_path / "progress.jsonl"), text)
    assert (tmp_path / "task.yaml").read_bytes() == text.encode("utf-8")
    assert (tmp_path / "progress.jsonl").read_bytes() == text.encode("utf-8")


def test_write_record_replaces_what_was_there(fp, tmp_path):
    path = tmp_path / "task.yaml"
    path.write_bytes(b"old\r\n")
    fp.write_record(str(path), "new\n")
    assert path.read_bytes() == b"new\n"


def test_append_record_puts_lf_on_disk(fp, tmp_path):
    path = tmp_path / "progress.jsonl"
    fp.append_record(str(path), '{"a": 1}\n')
    fp.append_record(str(path), '{"b": 2}\n')
    assert path.read_bytes() == b'{"a": 1}\n{"b": 2}\n'


def test_write_record_waits_out_a_reader_holding_the_file(fp, tmp_path):
    """On Windows a reader's open handle fails the replace until it closes."""
    path = tmp_path / "task.yaml"
    path.write_bytes(b"old\n")
    reader = open(path, "rb")
    closer = threading.Timer(0.3, reader.close)
    closer.start()
    try:
        fp.write_record(str(path), "new\n")
    finally:
        closer.join()
        reader.close()
    assert path.read_bytes() == b"new\n"
    assert [p.name for p in tmp_path.iterdir()] == ["task.yaml"]


def test_write_record_gives_up_on_a_file_that_stays_held(fp, tmp_path, monkeypatch):
    path = tmp_path / "task.yaml"
    path.write_bytes(b"old\n")
    attempts = []

    def refuse(src, dst):
        attempts.append(src)
        raise PermissionError(13, "held open by a reader")

    monkeypatch.setattr(fp.os, "replace", refuse)
    monkeypatch.setattr(fp.time, "sleep", lambda _s: None)
    with pytest.raises(PermissionError):
        fp.write_record(str(path), "new\n")
    assert 1 < len(attempts) == fp.REPLACE_ATTEMPTS
    assert path.read_bytes() == b"old\n"
    assert [p.name for p in tmp_path.iterdir()] == ["task.yaml"]


# --- the lock -----------------------------------------------------------------

HOLDER = textwrap.dedent(
    """
    import importlib.util, sys, time
    spec = importlib.util.spec_from_file_location("fleet_platform", sys.argv[1])
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    try:
        with fp.exclusive_lock(sys.argv[2]):
            print("got", flush=True)
            if sys.argv[3] == "hold":
                time.sleep(60)
    except fp.LockHeld:
        print("held", flush=True)
    """
)


def _lock_probe(lock: Path) -> str:
    out = subprocess.run(
        [sys.executable, "-c", HOLDER, str(LIB / "fleet_platform.py"), str(lock), "probe"],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return out.stdout.strip()


def test_lock_excludes_another_process_while_held(fp, tmp_path):
    lock = tmp_path / "lock"
    with fp.exclusive_lock(str(lock)):
        assert _lock_probe(lock) == "held"
    assert _lock_probe(lock) == "got"


def test_lock_dies_with_the_process_holding_it(tmp_path):
    """A killed holder leaves no stale lock, which is what makes it a liveness test."""
    lock = tmp_path / "lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(LIB / "fleet_platform.py"), str(lock), "hold"],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "got"
        assert _lock_probe(lock) == "held"
    finally:
        holder.kill()
        holder.wait(timeout=30)
        holder.stdout.close()
    assert _lock_probe(lock) == "got"


# --- liveness -----------------------------------------------------------------


def test_alive_sees_this_process(fp):
    assert fp.alive(os.getpid())


def test_alive_does_not_see_an_exited_process(fp):
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)
    assert not fp.alive(child.pid)


@pytest.mark.parametrize("pid", [0, -1])
def test_alive_refuses_a_pid_that_names_no_single_process(fp, pid):
    assert not fp.alive(pid)


def test_alive_leaves_the_process_alone(fp):
    """On Windows `os.kill(pid, 0)` is CTRL_C_EVENT: asking must not interrupt."""
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], creationflags=flags
    )
    try:
        assert fp.alive(child.pid)
        time.sleep(0.5)
        assert child.poll() is None
        assert fp.alive(child.pid)
    finally:
        child.kill()
        child.wait(timeout=30)


# --- detached spawn -----------------------------------------------------------

# Beats into a file until told to stop, so "still running" is observed from the
# outside rather than inferred from a pid.
BEATER = textwrap.dedent(
    """
    import os, sys, time
    beat = sys.argv[1]
    for _ in range(600):
        with open(beat, "w") as fh:
            fh.write(str(time.time()))
        time.sleep(0.1)
    """
)

# Waits for a word on stdin, spawns the beater detached, prints its pid and
# then sleeps: the test kills this process's whole group or job while it sleeps.
PARENT = textwrap.dedent(
    """
    import importlib.util, subprocess, sys, time
    spec = importlib.util.spec_from_file_location("fleet_platform", sys.argv[1])
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    sys.stdin.readline()
    child = fp.spawn_detached(
        [sys.executable, "-c", sys.argv[2], sys.argv[3]],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(child.pid, flush=True)
    time.sleep(60)
    """
)


def _beating(beat: Path, since: float) -> bool:
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if float(beat.read_text() or 0) > since:
                return True
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    return False


def _stop(fp, pid: int) -> None:
    if fp.alive(pid):
        os.kill(pid, signal.SIGTERM)


class _KillOnCloseJob:
    """A Windows job that kills everything in it when its handle closes.

    This is what an ssh session is to the processes started inside it, so
    closing one is how a test ends a "session" without an ssh server. Breakaway
    is allowed, as it is on the sshd the plan measured.
    """

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.k32 = k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        self.job = k32.CreateJobObjectW(None, None)
        assert self.job, ctypes.get_last_error()
        info = ExtendedLimits()
        kill_on_close, breakaway_ok = 0x2000, 0x800
        info.BasicLimitInformation.LimitFlags = kill_on_close | breakaway_ok
        extended_limit_information = 9
        assert k32.SetInformationJobObject(
            wintypes.HANDLE(self.job), extended_limit_information,
            ctypes.byref(info), ctypes.sizeof(info),
        ), ctypes.get_last_error()

    def adopt(self, pid: int) -> None:
        from ctypes import get_last_error, wintypes

        set_quota_and_terminate = 0x0100 | 0x0001
        proc = self.k32.OpenProcess(set_quota_and_terminate, False, pid)
        assert proc, get_last_error()
        try:
            assert self.k32.AssignProcessToJobObject(
                wintypes.HANDLE(self.job), wintypes.HANDLE(proc)
            ), get_last_error()
        finally:
            self.k32.CloseHandle(wintypes.HANDLE(proc))

    def close(self) -> None:
        from ctypes import wintypes

        if self.job:
            self.k32.CloseHandle(wintypes.HANDLE(self.job))
            self.job = None


def test_detached_child_outlives_the_session_that_started_it(fp, tmp_path):
    """End the parent's session — its process group, or its job — and the child keeps beating."""
    beat = tmp_path / "beat"
    parent = subprocess.Popen(
        [sys.executable, "-c", PARENT, str(LIB / "fleet_platform.py"), BEATER, str(beat)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        start_new_session=not WINDOWS,
    )
    job = _KillOnCloseJob() if WINDOWS else None
    child_pid = 0
    try:
        if job:
            job.adopt(parent.pid)
        parent.stdin.write("go\n")
        parent.stdin.flush()
        child_pid = int(parent.stdout.readline())
        assert _beating(beat, 0), "the detached child never started beating"

        if job:
            job.close()
        else:
            os.killpg(parent.pid, signal.SIGKILL)
        parent.wait(timeout=30)
        ended = time.time()

        assert _beating(beat, ended + 0.3), "the child died with its parent's session"
        assert fp.alive(child_pid)
        if not WINDOWS:
            # The parent led its own session; the child must lead another.
            assert os.getsid(child_pid) == child_pid
    finally:
        if job:
            job.close()
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=30)
        parent.stdin.close()
        parent.stdout.close()
        if child_pid:
            _stop(fp, child_pid)
