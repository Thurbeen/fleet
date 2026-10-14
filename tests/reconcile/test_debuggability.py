"""A loop that is wrong says so where a person looks, and not only in its log.

On 2026-10-14 the control plane's loop had been started before an update that
renamed the notifier it runs as a child. Every ready, stalled, needs-input and
sync notice failed for eight hours — 515 log lines in a megabyte of log —
while `status` said "up, reconciling" and every SessionStart `ensure` said
"adopted, not restarted". Three things were missing, and each is a test here:

  ITS CODE.   A supervisor holds the modules it started with. A loop whose code
              changed on disk replaces itself, `status` names one that has not
              and exits 1, and `ensure` replaces it rather than adopting it.
  ITS CHILDREN.  A child that fails — a script that is not there, a non-zero
              exit, a bound — is in `status`: which, how often, since when, and
              its last words.
  ITS NOTICES.  Every notice decision is one line that starts `notice`, naming
              the kind, who it went to, by which road, and why.

Each runs its loop from a copy of the checkout, so changing that loop's code
changes nothing else.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from harness import Run, expect, refute, run_fleet
from reconcilekit import checkout_copy, wait_for


@pytest.fixture
def here(recon, tmp_path) -> Path:
    return checkout_copy(tmp_path / "checkout")


def reconcile(checkout: Path, *args: str, **env: str | None) -> Run:
    return run_fleet("reconcile", *args, cwd=checkout, **env)


def edit(path: Path) -> None:
    """A change on disk, as a fast-forward makes one: new bytes, a new mtime."""
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n# changed under a running loop\n")


def age_the_record(recon) -> None:
    """Make the loop's record of its code disagree with the disk, without
    touching the code itself: what `status` and `ensure` read is the record."""
    record = recon.rt / "code.json"
    assert wait_for(record.is_file), "the loop never recorded the code it runs"
    doc = json.loads(record.read_text(encoding="utf-8"))
    rel = next(iter(doc["files"]))
    doc["files"][rel] = [0, 0]
    record.write_text(json.dumps(doc), encoding="utf-8")


# --- its code ---------------------------------------------------------------


def test_a_loop_whose_own_code_changed_on_disk_replaces_itself(recon, here):
    """The incident's cure: the loop that fast-forwarded its own checkout runs
    the new code at the next pass, with nobody asked to restart it."""
    assert reconcile(here, "ensure").code == 0
    old = recon.pid()
    assert old

    edit(here / "scripts" / "lib" / "reconcile.py")

    assert wait_for(lambda: recon.pid() not in ("", old), 60), recon.log()
    expect(recon.log(), "its own code changed on disk", "scripts/lib/reconcile.py")
    status = reconcile(here, "status")
    assert status.code == 0, status.out
    refute(status.out, "stale")


def test_status_says_a_loop_runs_code_older_than_the_checkout(recon, here):
    assert reconcile(here, "ensure").code == 0
    age_the_record(recon)

    status = reconcile(here, "status")

    assert status.code == 1, "a loop on old code is a fault, not a reading"
    expect(status.out, "stale     ", "older than the checkout", "uv run fleet reconcile restart")


def test_ensure_replaces_a_loop_on_old_code_rather_than_adopting_it(recon, here):
    assert reconcile(here, "ensure").code == 0
    old = recon.pid()
    age_the_record(recon)

    again = reconcile(here, "ensure")

    assert again.code == 0, again.out
    refute(again.out, "adopted, not restarted")
    expect(again.out, "older than the checkout", "ticking (pid ")
    assert recon.pid() not in ("", old), "the loop on old code is still the one running"
    assert reconcile(here, "status").code == 0


def test_a_loop_from_before_the_record_is_replaced_too(recon, here):
    """The loop running when this lands keeps no record of its code. A loop
    that is up and names no code is one from before it, and is replaced."""
    assert reconcile(here, "ensure").code == 0
    old = recon.pid()
    assert wait_for((recon.rt / "code.json").is_file)
    (recon.rt / "code.json").unlink()

    expect(reconcile(here, "status").out, "stale     ")
    again = reconcile(here, "ensure")

    expect(again.out, "older than the checkout", "ticking (pid ")
    assert recon.pid() not in ("", old)


def test_ensure_leaves_a_loop_asked_down_down_whatever_its_code(recon, here):
    assert reconcile(here, "ensure").code == 0
    age_the_record(recon)
    assert reconcile(here, "stop").code == 0

    again = reconcile(here, "ensure")

    expect(again.out, "staying down")
    refute(reconcile(here, "status").out, "ticking")


# --- its children -----------------------------------------------------------


def test_a_notifier_that_is_not_there_is_in_status_not_only_the_log(recon, here):
    """The incident's own shape: the child script the loop runs is gone from
    disk, and the loop's own code is not changed."""
    assert reconcile(here, "ensure").code == 0
    (here / "scripts" / "lib" / "notify_conductor.py").unlink()
    recon.ready("alpha/01-first")

    def failing() -> str:
        return reconcile(here, "status").out

    assert wait_for(lambda: "failing   notify" in failing(), 30), recon.log()
    status = reconcile(here, "status")
    assert status.code == 1, "a child failing every pass is a fault"
    expect(status.out, "failing   notify", "time(s) since", "notify_conductor.py")


def test_a_failed_plan_is_said_and_a_recovery_clears_it(recon, monkeypatch):
    """`plan` failing used to be silent: no notice, and not one log line."""
    monkeypatch.setenv("RECON_FAIL", "plan")
    recon("ensure")

    assert wait_for(lambda: "failing   plan" in recon("status").out, 30), recon.log()
    expect(recon.log(), "plan: exit 5")
    expect(recon("status").out, "plan failed on purpose")

    monkeypatch.delenv("RECON_FAIL")
    recon("restart")
    start = recon.count("plan")
    assert wait_for(lambda: recon.count("plan") >= start + 1, 30)
    time.sleep(0.5)
    refute(recon("status").out, "failing")
    assert recon("status").code == 0


# --- its notices ------------------------------------------------------------


def test_each_notice_decision_is_one_greppable_line(recon, stubs):
    recon("ensure")
    recon.ready("alpha/01-first")
    assert wait_for(lambda: "notice ready → Gate Control [lead-uui] typed" in recon.log(), 30), recon.log()

    recon.lead("working")
    recon.ready("alpha/01-first", "beta/02-second")
    assert wait_for(lambda: "notice ready → Gate Control [lead-uui] mailbox" in recon.log(), 30), recon.log()
    expect(recon.log(), "Gate Control is working")

    recon.lead(None)
    recon.ready("alpha/01-first", "beta/02-second", "gamma/03-third")
    assert wait_for(lambda: "notice ready → Gate Control waiting" in recon.log(), 30), recon.log()


def test_a_stop_during_a_replacement_is_not_undone(recon, here, monkeypatch, capsys):
    """`replace` writes a down flag of its own to bring the old loop down. An
    operator's `stop` landing in that wait overwrites it, and is theirs: the
    replacement leaves it standing and launches nothing."""
    from harness import lib

    assert reconcile(here, "ensure").code == 0
    age_the_record(recon)
    mod = lib("reconcile.py")
    real = mod.bring_down

    def stopped_meanwhile(cfg):
        (recon.rt / "down").write_text("stopped by the operator\n", encoding="utf-8")
        return real(cfg)

    monkeypatch.setattr(mod, "bring_down", stopped_meanwhile)

    mod.main(["ensure"])

    expect(capsys.readouterr().out, "staying down")
    assert (recon.rt / "down").read_text(encoding="utf-8") == "stopped by the operator\n"
    assert not wait_for(lambda: mod.running(mod.Config.from_env()), 3), "a new loop launched over the stop"
