"""The lifecycle: one loop per queue, a stop that stays stopped, and no phantom called up.

Two reconcilers over one queue would both run `collect`, which closes tasks and
reaps sessions; a stop the next onboarding run undoes is not a stop. Liveness is
a lock the running loop holds for its whole life, so a dead loop proves itself
dead and a pidfile is a note for people, never evidence.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import time

import pytest
from harness import PYTHON, REPO, expect, lib, refute, write
from reconcilekit import wait_for

from fleet.cli import LIB


def test_ensure_starts_the_loop_and_a_second_ensure_adopts_it(recon):
    first = recon("ensure")
    assert first.code == 0, first.out
    expect(first.out, "ticking (pid ")
    pid = recon.pid()
    assert pid and f"(pid {pid})" in first.out

    second = recon("ensure")
    assert second.code == 0, second.out
    expect(second.out, "adopted, not restarted", f"pid {pid}")
    assert recon.pid() == pid, "the adopted loop is the same process"


def test_start_over_a_ticking_loop_adopts_it_too(recon):
    recon("start")
    pid = recon.pid()
    again = recon("start")
    expect(again.out, "adopted, not restarted")
    assert recon.pid() == pid


def test_stop_is_durable_and_ensure_honours_it(recon):
    """The test this file exists for: `ensure` against a loop the operator asked
    down leaves it down, whatever runs it and however often."""
    recon("ensure")
    assert wait_for(lambda: recon.count("watch") >= 1)

    stopped = recon("stop")
    assert stopped.code == 0, stopped.out
    expect(stopped.out, "down, durably")
    assert (recon.rt / "down").is_file(), "stop leaves a flag on disk, not a fact in a process"

    quiet = recon.count("watch")
    time.sleep(2)
    assert recon.count("watch") == quiet, "the loop really stopped ticking"

    after = recon("ensure")
    expect(after.out, "staying down")
    time.sleep(1)
    assert recon.count("watch") == quiet, "ensure did not resurrect it"
    expect(recon("status").out, "asked down")


def test_start_is_the_way_back(recon):
    """Only the operator asking for it clears the flag: the whole difference
    between `start` and `ensure`."""
    recon("ensure")
    recon("stop")
    quiet = recon.count("watch")

    back = recon("start")
    assert back.code == 0, back.out
    expect(back.out, "clearing the down flag", "ticking (pid ")
    assert not (recon.rt / "down").exists(), "the flag is gone after start"
    assert wait_for(lambda: recon.count("watch") > quiet), "it is folding again"


def test_restart_clears_the_flag_and_replaces_the_loop(recon):
    recon("ensure")
    recon("stop")
    out = recon("restart")
    assert out.code == 0, out.out
    expect(out.out, "ticking (pid ")
    assert not (recon.rt / "down").exists()
    expect(recon("status").out, "up        reconciling")


def test_a_supervisor_whose_loop_never_ticks_is_never_up(recon, monkeypatch, capsys, tmp_path):
    """A queue command that is not on the machine makes the loop fail every time
    and the supervisor retry forever. That is a live process which has never
    reconciled anything: never adopted, never reported healthy."""
    monkeypatch.setenv("FLEET_RECONCILE_QUEUE_CMD", f'["{(tmp_path / "nothing-here").as_posix()}"]')
    mod = lib("reconcile.py")
    monkeypatch.setattr(mod, "START_WAIT_SECS", 3)

    assert mod.main(["ensure"]) == 1
    expect(capsys.readouterr().err, "did not tick")
    assert not (recon.rt / "pid").exists(), "a failed launch leaves nothing behind"
    assert not mod.running(mod.Config.from_env()), "and no supervisor is left holding the lock"

    expect(recon("status").out, "not running")
    assert mod.main(["ensure"]) == 1
    refute(capsys.readouterr().out, "adopted")


def test_a_stale_pidfile_and_heartbeat_are_not_trusted(recon):
    """A pidfile outlives a reboot and pids are reused. Here it names a live
    process that is not the reconciler, beside a fresh heartbeat: nothing holds
    the lock, so it is down, and stop signals nobody."""
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        write(recon.rt / "pid", f"{bystander.pid}\n")
        write(recon.rt / "heartbeat", f"{int(time.time())}\n")

        expect(recon("status").out, "not running")
        recon("stop")
        time.sleep(0.5)
        assert bystander.poll() is None, "stop killed a process that only shared the pid"

        recon("start")
        started = recon.pid()
        assert started and started != str(bystander.pid), "start adopted a stale pid"
    finally:
        bystander.kill()
        bystander.wait(timeout=30)


HOLDER = textwrap.dedent(
    """
    import importlib.util, os, sys, time
    spec = importlib.util.spec_from_file_location("fleet_platform", sys.argv[1])
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    with fp.exclusive_lock(os.path.join(sys.argv[2], "lock")):
        with open(os.path.join(sys.argv[2], "pid"), "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        print("held", flush=True)
        time.sleep(120)
    """
)


def test_stop_terminates_a_loop_that_ignores_the_flag(recon, monkeypatch, capsys):
    """The flag first, and the kill only when the lock is still held after a
    bounded wait: a wedged loop is stopped, and still stays down."""
    recon.rt.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, os.path.join(LIB, "fleet_platform.py"), str(recon.rt)],
        stdout=subprocess.PIPE, text=True, encoding="utf-8",
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        mod = lib("reconcile.py")
        monkeypatch.setattr(mod, "STOP_GRACE_SECS", 0)
        assert mod.main(["stop"]) == 0
        expect(capsys.readouterr().out, "down, durably")
        assert holder.wait(timeout=30) is not None, "the wedged loop is gone"
        assert (recon.rt / "down").is_file()
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=30)
        holder.stdout.close()


def test_a_stop_ends_the_queue_command_in_flight_too(recon, monkeypatch, isolated_env, capsys):
    """A refuel or shepherd left running after its loop is gone keeps acting,
    and the next loop's first pass runs the same work beside it."""
    slow_pid = isolated_env / "slow.pid"
    monkeypatch.setenv("RECON_SLOW", "collect:120")
    monkeypatch.setenv("RECON_SLOW_PID", str(slow_pid))
    assert recon("ensure").code == 0
    assert wait_for(lambda: slow_pid.is_file() and slow_pid.read_text(encoding="utf-8").strip(), 30)
    collect = int(slow_pid.read_text(encoding="utf-8"))
    mod = lib("reconcile.py")
    monkeypatch.setattr(mod, "STOP_GRACE_SECS", 0)
    try:
        assert mod.main(["stop"]) == 0, capsys.readouterr().out
        assert wait_for(lambda: not mod.fleet_platform.alive(collect), 15), "the collect in flight outlived the stop"
    finally:
        if mod.fleet_platform.alive(collect):
            mod.fleet_platform.terminate_tree(collect, force=True)


# A test run as the loop sees one: it starts the loop and then is killed before
# any teardown can stop it.
TEST_RUN = textwrap.dedent(
    """
    import os, subprocess, sys, time
    os.environ["FLEET_RECONCILE_PARENT_PID"] = str(os.getpid())
    boot = "import sys\\nfrom fleet.cli import main\\nsys.exit(main())"
    done = subprocess.run([sys.executable, "-c", boot, "reconcile", "ensure"], cwd=sys.argv[1],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(done.returncode, flush=True)
    time.sleep(300)
    """
)

# How long a loop may outlive what it watches: ten ticks of the compressed watch.
ORPHAN_SECS = 10


def test_a_killed_test_run_leaks_no_supervisor(recon):
    """An interrupted test run never reaches the fixture's `stop`, and a detached
    loop would tick against its temp directory forever. It watches the run's pid
    and goes with it."""
    run = subprocess.Popen([*PYTHON, "-c", TEST_RUN, str(REPO)], stdout=subprocess.PIPE, text=True)
    try:
        assert run.stdout.readline().strip() == "0", recon.log()
        supervisor = int(recon.pid())
        assert wait_for(lambda: recon.count("watch") >= 1)

        run.kill()
        run.wait(timeout=30)

        mod = lib("reconcile.py")
        assert wait_for(lambda: not mod.fleet_platform.alive(supervisor), ORPHAN_SECS), (
            f"the supervisor outlived the run that started it\n{recon.log()}")
        expect(recon.log(), "is gone; supervisor exiting")
    finally:
        if run.poll() is None:
            run.kill()
            run.wait(timeout=30)
        run.stdout.close()


@pytest.mark.skipif(os.name == "nt", reason="Windows will not delete a directory whose lock and log the loop holds "
                                            "open; the watched parent is what ends a leaked loop there")
def test_a_supervisor_whose_runtime_directory_is_deleted_exits(recon):
    assert recon("ensure").code == 0
    supervisor = int(recon.pid())
    mod = lib("reconcile.py")

    shutil.rmtree(recon.rt)

    assert wait_for(lambda: not mod.fleet_platform.alive(supervisor), ORPHAN_SECS), (
        "the supervisor kept ticking with its runtime directory gone")
    # Nothing in a pass may make it again: the loop would then read as at home
    # at the next boundary and never exit.
    assert not recon.rt.exists(), "the loop made its runtime directory again instead of exiting"


def test_launch_leaves_a_loop_that_is_already_ticking_alone(recon):
    """Two `ensure`s that overlap: the second must adopt the loop the first just
    started, not end it mid-pass and start another."""
    assert recon("ensure").code == 0
    pid = recon.pid()
    mod = lib("reconcile.py")
    assert mod.launch(mod.Config.from_env())
    assert recon.pid() == pid, "launch replaced a loop that was ticking"
    assert mod.fleet_platform.alive(int(pid))


def test_logs_reads_the_loop_log(recon):
    missing = recon("logs")
    assert missing.code == 1
    expect(missing.out, "no log at")

    recon("ensure")
    assert wait_for(lambda: "starting" in recon.log())
    shown = recon("logs")
    assert shown.code == 0
    expect(shown.out, "starting")


def test_an_unknown_verb_is_refused_and_help_is_the_header(recon):
    bad = recon("dispatch")
    assert bad.code == 2
    expect(bad.out, "unknown command", "ensure start stop restart status nudge hook logs")
    helped = recon("--help")
    assert helped.code == 0
    expect(helped.out, "ensure", "nudge", "FLEET_RECONCILE_QUEUE_CMD")
