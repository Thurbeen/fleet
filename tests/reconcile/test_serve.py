"""The loop comes back by itself, and says so loudly when it has not.

Three leads found the reconciler "down — not running, and no down flag" eighteen
times: after a reboot, a WSL restart, a lead restart. Nothing was wrong with
the loop; nothing started it again. So:

  - `serve` is what a service manager runs: the loop in the FOREGROUND, which
    waits rather than exits while the operator's down flag stands or another
    loop holds the lock, and takes over the moment that loop dies;
  - `ensure --if-lead` is what the lead's SessionStart hook runs: `ensure`, but
    only for this checkout's Mission Control session, and never a failure;
  - `status` with no loop and no flag says DOWN and exits non-zero.

None of them clears a down flag: only `start` does.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from harness import PYTHON, REPO, expect, lib, refute, write
from reconcilekit import wait_for

BOOT = "import sys\nsys.path.insert(0, sys.argv[1])\nfrom fleet.cli import main\nsys.exit(main(sys.argv[2:]))"


def serve() -> subprocess.Popen:
    return subprocess.Popen([*PYTHON, "-c", BOOT, str(REPO), "reconcile", "serve"], cwd=REPO,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def serving(recon) -> bool:
    """Up, with no `ensure` run. Not `proc.pid`: a Windows venv's python.exe is a
    launcher, and the loop runs in the interpreter it starts."""
    return bool(recon.pid()) and "up        reconciling" in recon("status").out


def end(proc: subprocess.Popen) -> None:
    proc.kill()
    proc.wait(timeout=30)


def test_serve_runs_the_loop_in_the_foreground_and_waits_out_a_stop(recon):
    proc = serve()
    try:
        assert wait_for(lambda: serving(recon)), f"serve did not run the loop itself\n{recon.log()}"
        assert wait_for(lambda: recon.count("watch") >= 1)

        stopped = recon("stop")
        assert stopped.code == 0, stopped.out
        quiet = recon.count("watch")
        time.sleep(2)
        assert proc.poll() is None, "a service that exits on a stop is restarted into a loop"
        assert recon.count("watch") == quiet, "serve honoured the operator's down flag"
        assert (recon.rt / "down").is_file(), "and never cleared it"

        recon("start")
        assert wait_for(lambda: recon.count("watch") > quiet), "start brings it back"
    finally:
        end(proc)


def test_serve_takes_over_when_the_loop_it_waited_on_dies(recon):
    recon("ensure")
    first = recon.pid()
    proc = serve()
    try:
        time.sleep(2)
        assert recon.pid() == first, "serve adopted the running loop rather than fighting it"
        os.kill(int(first), 9)
        assert wait_for(lambda: recon.pid() not in ("", first) and serving(recon), 30), \
            f"nobody brought it back\n{recon.log()}"
    finally:
        end(proc)


def test_status_is_loud_when_nothing_asked_it_down(recon):
    done = recon("status")
    assert done.code != 0, "a loop that should be up and is not is a failure, not a line of a report"
    expect(done.out, "DOWN", "nothing asked it down", "uv run fleet reconcile ensure", "fleet install")


def test_status_of_an_operators_stop_is_not_a_failure(recon):
    recon("stop")
    done = recon("status")
    assert done.code == 0, done.out
    expect(done.out, "asked down")


def lead_env(sid: str) -> dict:
    # The agent's own conversation id rides beside it and is not the session's.
    return {"THURBOX_SESSION": sid, "THURBOX_SESSION_ID": "agent-conversation"}


def test_the_lead_session_start_brings_it_up(recon):
    done = recon("ensure", "--if-lead", **lead_env("lead-uuid"))
    assert done.code == 0, done.out
    expect(done.out, "ticking (pid ")
    assert recon.pid()


def test_any_other_session_start_leaves_it_alone(recon, stubs):
    write(stubs.root / "sessions" / "worker-uuid.json",
          json.dumps({"id": "worker-uuid", "name": "a worker", "state": "working"}) + "\n")
    for env in (lead_env("worker-uuid"), {"THURBOX_SESSION_ID": None, "THURBOX_SESSION": None}):
        done = recon("ensure", "--if-lead", **env)
        assert done.code == 0, done.out
        refute(done.out, "ticking")
    assert not recon.pid(), "a worker's session start started a loop that reaps sessions"


def test_the_lead_session_start_honours_the_down_flag(recon):
    recon("stop")
    done = recon("ensure", "--if-lead", **lead_env("lead-uuid"))
    assert done.code == 0, done.out
    expect(done.out, "staying down")
    assert (recon.rt / "down").is_file()
    assert not recon.pid()


def test_the_session_start_hook_never_fails_the_session(recon, monkeypatch, capsys, tmp_path):
    """A loop that cannot start is reported, and the session still starts."""
    monkeypatch.setenv("FLEET_RECONCILE_QUEUE_CMD", json.dumps([(tmp_path / "nothing-here").as_posix()]))
    for var, value in lead_env("lead-uuid").items():
        monkeypatch.setenv(var, value)
    mod = lib("reconcile.py")
    monkeypatch.setattr(mod, "START_WAIT_SECS", 3)
    assert mod.main(["ensure", "--if-lead"]) == 0
    expect(capsys.readouterr().err, "did not tick")


def test_a_bad_setting_does_not_fail_the_session_start_either(recon):
    """CONTRIBUTING: a SessionStart hook exits 0, whatever it finds."""
    done = recon("ensure", "--if-lead", **lead_env("lead-uuid"), FLEET_RECONCILE_WATCH_SECS="soon")
    assert done.code == 0, done.out
    expect(done.out, "could not ensure")
