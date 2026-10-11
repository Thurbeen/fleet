"""Every child the loop runs has a bound, and a loop past its stall says so in its exit code.

A queue command that never returned froze the loop: no heartbeat, no collect,
no shepherd, and `status` reported STALLED with exit 0, so nothing reading the
exit code ever learned of it. The bound kills the child and its tree and the
loop goes on; `status` on a stale heartbeat is a fault, like DOWN.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from harness import expect, lib, write
from reconcilekit import wait_for

from fleet.cli import LIB

# The lock, held by another process: a lock this test held itself would not
# read as held to a probe from the same process on every OS.
HOLDER = (
    "import importlib.util, os, sys, time\n"
    "spec = importlib.util.spec_from_file_location('fleet_platform', sys.argv[1])\n"
    "fp = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(fp)\n"
    "with fp.exclusive_lock(os.path.join(sys.argv[2], 'lock')):\n"
    "    print('held', flush=True)\n"
    "    time.sleep(120)\n"
)


def test_a_collect_past_its_bound_is_killed_logged_and_the_loop_goes_on(recon, monkeypatch, isolated_env):
    slow_pid = isolated_env / "slow.pid"
    monkeypatch.setenv("RECON_SLOW", "collect:120")
    monkeypatch.setenv("RECON_SLOW_PID", str(slow_pid))
    monkeypatch.setenv("FLEET_RECONCILE_BOUND_SECS", "2")
    mod = lib("reconcile.py")

    assert recon("ensure").code == 0
    assert wait_for(lambda: slow_pid.is_file() and slow_pid.read_text(encoding="utf-8").strip(), 30)
    first = int(slow_pid.read_text(encoding="utf-8"))
    try:
        assert wait_for(lambda: not mod.fleet_platform.alive(first), 15), "the collect outlived its bound"
        assert wait_for(lambda: "collect: killed after 2s" in recon.log(), 10), recon.log()
        assert wait_for(lambda: recon.count("collect") >= 2, 20), f"no pass after the kill: {recon.calls()}"
        assert recon.count("watch") >= 1, "the pass went on past the killed collect"
    finally:
        if mod.fleet_platform.alive(first):
            mod.fleet_platform.terminate_tree(first, force=True)


def test_status_on_a_stale_heartbeat_says_stalled_and_exits_1(recon):
    recon.rt.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, os.path.join(LIB, "fleet_platform.py"), str(recon.rt)],
        stdout=subprocess.PIPE, text=True, encoding="utf-8",
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        write(recon.rt / "heartbeat", f"{int(time.time()) - 3600}\n")
        done = recon("status")
        expect(done.out, "STALLED")
        assert done.code == 1, done.out
    finally:
        holder.kill()
        holder.wait(timeout=30)
        holder.stdout.close()


def test_the_heartbeat_is_fresh_when_each_child_starts(recon, monkeypatch, isolated_env):
    # A pass runs several children, each with its own bound. Beaten only at the
    # top of the pass, two slow-but-honest children together aged the heartbeat
    # past `watch + bound`, and `status` exited 1 on a loop that was healthy.
    monkeypatch.setenv("RECON_SLOW", "collect:3,refuel:20")
    monkeypatch.setenv("RECON_SLOW_PID", str(isolated_env / "slow.pid"))
    mod = lib("reconcile.py")

    assert recon("ensure").code == 0
    assert wait_for(lambda: recon.count("refuel") >= 1, 30), recon.calls()
    beat = int((recon.rt / "heartbeat").read_text(encoding="utf-8").strip())
    try:
        assert time.time() - beat < 3, "the heartbeat predates the slow collect that ran before refuel"
    finally:
        recon("stop")
        pid = int((isolated_env / "slow.pid").read_text(encoding="utf-8"))
        if mod.fleet_platform.alive(pid):
            mod.fleet_platform.terminate_tree(pid, force=True)
