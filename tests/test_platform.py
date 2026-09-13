"""The platform seam, proven on whichever OS runs it.

Every test here runs on POSIX and on Windows. Where the two differ, each branch
is asserted only on the OS that takes it: a branch faked on the other OS proves
nothing about the real one.

    uv run python -m unittest discover -s tests
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fleet.cli import LIB, load

fp = load("fleet_platform.py")
PLATFORM = os.path.join(LIB, "fleet_platform.py")
WINDOWS = os.name == "nt"


@contextmanager
def environ(**values):
    """os.environ with these set, and any given as None removed; restored after."""
    with mock.patch.dict(os.environ):
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def names(self) -> list[str]:
        return sorted(p.name for p in self.tmp.iterdir())


class Directories(TempDirCase):
    def test_thurbox_config_dir_honours_thurbox_own_pin(self):
        with environ(THURBOX_CONFIG_DIR=str(self.tmp / "pinned"), XDG_CONFIG_HOME=str(self.tmp / "xdg")):
            self.assertEqual(fp.thurbox_config_dir(), str(self.tmp / "pinned"))

    def test_thurbox_config_dir_prefers_xdg_on_every_os(self):
        with environ(THURBOX_CONFIG_DIR=None, XDG_CONFIG_HOME=str(self.tmp / "xdg"),
                     APPDATA=str(self.tmp / "appdata")):
            self.assertEqual(fp.thurbox_config_dir(), os.path.join(str(self.tmp / "xdg"), "thurbox"))

    @unittest.skipUnless(WINDOWS, "the %APPDATA% branch")
    def test_thurbox_config_dir_is_appdata_on_windows(self):
        with environ(THURBOX_CONFIG_DIR=None, XDG_CONFIG_HOME=None, APPDATA=str(self.tmp)):
            self.assertEqual(fp.thurbox_config_dir(), os.path.join(str(self.tmp), "thurbox"))

    @unittest.skipIf(WINDOWS, "the ~/.config branch")
    def test_thurbox_config_dir_is_home_config_elsewhere(self):
        with environ(THURBOX_CONFIG_DIR=None, XDG_CONFIG_HOME=None, HOME=str(self.tmp)):
            self.assertEqual(fp.thurbox_config_dir(), os.path.join(str(self.tmp), ".config", "thurbox"))

    def test_fleet_data_dir_prefers_xdg_on_every_os(self):
        with environ(XDG_DATA_HOME=str(self.tmp / "xdg"), LOCALAPPDATA=str(self.tmp / "local")):
            self.assertEqual(fp.fleet_data_dir(), os.path.join(str(self.tmp / "xdg"), "fleet"))

    @unittest.skipUnless(WINDOWS, "the %LOCALAPPDATA% branch")
    def test_fleet_data_dir_is_localappdata_on_windows(self):
        with environ(XDG_DATA_HOME=None, LOCALAPPDATA=str(self.tmp)):
            self.assertEqual(fp.fleet_data_dir(), os.path.join(str(self.tmp), "fleet"))

    @unittest.skipIf(WINDOWS, "the ~/.local/share branch")
    def test_fleet_data_dir_is_local_share_elsewhere(self):
        with environ(XDG_DATA_HOME=None, HOME=str(self.tmp)):
            self.assertEqual(fp.fleet_data_dir(), os.path.join(str(self.tmp), ".local", "share", "fleet"))


class Records(TempDirCase):
    def test_write_record_puts_lf_on_disk(self):
        path = self.tmp / "task.yaml"
        fp.write_record(str(path), "id: one\ntitle: two\n")
        self.assertEqual(path.read_bytes(), b"id: one\ntitle: two\n")
        self.assertEqual(self.names(), ["task.yaml"])

    def test_records_are_utf8_whatever_the_locale(self):
        text = "title: dash — moon ◐ rocket \U0001f680\n"
        fp.write_record(str(self.tmp / "task.yaml"), text)
        fp.append_record(str(self.tmp / "progress.jsonl"), text)
        self.assertEqual((self.tmp / "task.yaml").read_bytes(), text.encode("utf-8"))
        self.assertEqual((self.tmp / "progress.jsonl").read_bytes(), text.encode("utf-8"))

    def test_write_record_replaces_what_was_there(self):
        path = self.tmp / "task.yaml"
        path.write_bytes(b"old\r\n")
        fp.write_record(str(path), "new\n")
        self.assertEqual(path.read_bytes(), b"new\n")

    def test_append_record_puts_lf_on_disk(self):
        path = self.tmp / "progress.jsonl"
        fp.append_record(str(path), '{"a": 1}\n')
        fp.append_record(str(path), '{"b": 2}\n')
        self.assertEqual(path.read_bytes(), b'{"a": 1}\n{"b": 2}\n')

    def test_write_record_waits_out_a_reader_holding_the_file(self):
        """On Windows a reader's open handle fails the replace until it closes."""
        path = self.tmp / "task.yaml"
        path.write_bytes(b"old\n")
        reader = open(path, "rb")
        closer = threading.Timer(0.3, reader.close)
        closer.start()
        try:
            fp.write_record(str(path), "new\n")
        finally:
            closer.join()
            reader.close()
        self.assertEqual(path.read_bytes(), b"new\n")
        self.assertEqual(self.names(), ["task.yaml"])

    def test_write_record_gives_up_on_a_file_that_stays_held(self):
        path = self.tmp / "task.yaml"
        path.write_bytes(b"old\n")
        refuse = mock.Mock(side_effect=PermissionError(13, "held open by a reader"))
        with mock.patch.object(fp.os, "replace", refuse), mock.patch.object(fp.time, "sleep"):
            with self.assertRaises(PermissionError):
                fp.write_record(str(path), "new\n")
        self.assertGreater(refuse.call_count, 1)
        self.assertEqual(refuse.call_count, fp.REPLACE_ATTEMPTS)
        self.assertEqual(path.read_bytes(), b"old\n")
        self.assertEqual(self.names(), ["task.yaml"])


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


def lock_probe(lock: Path) -> str:
    out = subprocess.run(
        [sys.executable, "-c", HOLDER, PLATFORM, str(lock), "probe"],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return out.stdout.strip()


class Lock(TempDirCase):
    def test_lock_excludes_another_process_while_held(self):
        lock = self.tmp / "lock"
        with fp.exclusive_lock(str(lock)):
            self.assertEqual(lock_probe(lock), "held")
        self.assertEqual(lock_probe(lock), "got")

    def test_lock_dies_with_the_process_holding_it(self):
        """A killed holder leaves no stale lock, which is what makes it a liveness test."""
        lock = self.tmp / "lock"
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLDER, PLATFORM, str(lock), "hold"],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "got")
            self.assertEqual(lock_probe(lock), "held")
        finally:
            holder.kill()
            holder.wait(timeout=30)
            holder.stdout.close()
        self.assertEqual(lock_probe(lock), "got")


class Alive(unittest.TestCase):
    def test_alive_sees_this_process(self):
        self.assertTrue(fp.alive(os.getpid()))

    def test_alive_does_not_see_an_exited_process(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=30)
        self.assertFalse(fp.alive(child.pid))

    def test_alive_refuses_a_pid_that_names_no_single_process(self):
        for pid in (0, -1):
            with self.subTest(pid=pid):
                self.assertFalse(fp.alive(pid))

    def test_alive_leaves_the_process_alone(self):
        """On Windows `os.kill(pid, 0)` is CTRL_C_EVENT: asking must not interrupt."""
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], creationflags=flags)
        try:
            self.assertTrue(fp.alive(child.pid))
            time.sleep(0.5)
            self.assertIsNone(child.poll())
            self.assertTrue(fp.alive(child.pid))
        finally:
            child.kill()
            child.wait(timeout=30)


# Beats into a file, so "still running" is observed from outside rather than
# inferred from a pid.
BEATER = textwrap.dedent(
    """
    import sys, time
    for _ in range(600):
        with open(sys.argv[1], "w") as fh:
            fh.write(str(time.time()))
        time.sleep(0.1)
    """
)

# Waits for a word on stdin, spawns the beater detached, prints its pid and
# sleeps: the test ends this process's session while it sleeps.
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


def beating(beat: Path, since: float) -> bool:
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if float(beat.read_text() or 0) > since:
                return True
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    return False


class KillOnCloseJob:
    """A Windows job that kills everything in it when its handle closes.

    That is what an ssh session is to the processes started inside it, so
    closing one ends a "session" without an ssh server. Breakaway is allowed,
    as it is for a process started over OpenSSH on Windows 11.
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


class SpawnDetached(TempDirCase):
    def test_detached_child_outlives_the_session_that_started_it(self):
        """End the parent's session — its process group, or its job — and the child keeps beating."""
        beat = self.tmp / "beat"
        parent = subprocess.Popen(
            [sys.executable, "-c", PARENT, PLATFORM, BEATER, str(beat)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            start_new_session=not WINDOWS,
        )
        job = KillOnCloseJob() if WINDOWS else None
        child_pid = 0
        try:
            if job:
                job.adopt(parent.pid)
            parent.stdin.write("go\n")
            parent.stdin.flush()
            child_pid = int(parent.stdout.readline())
            self.assertTrue(beating(beat, 0), "the detached child never started beating")

            if job:
                job.close()
            else:
                os.killpg(parent.pid, signal.SIGKILL)
            parent.wait(timeout=30)
            ended = time.time()

            self.assertTrue(beating(beat, ended + 0.3), "the child died with its parent's session")
            self.assertTrue(fp.alive(child_pid))
            if not WINDOWS:
                # The parent led its own session; the child must lead another.
                self.assertEqual(os.getsid(child_pid), child_pid)
        finally:
            if job:
                job.close()
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=30)
            parent.stdin.close()
            parent.stdout.close()
            if child_pid and fp.alive(child_pid):
                os.kill(child_pid, signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
