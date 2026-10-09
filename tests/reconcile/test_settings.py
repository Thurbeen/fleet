"""The clocks are a setting: reconcile.conf, under the environment, re-read every pass.

The operator's ask was a shepherd every two minutes without exporting a
variable into whatever starts the loop — a service unit, a SessionStart hook.
So the file sets it, the environment still wins, an edit lands on the next pass
with no restart, and `status` prints what the RUNNING loop uses and where each
number came from, rather than what its own shell would have read.
"""

from __future__ import annotations

import os
from pathlib import Path

from harness import expect, write
from reconcilekit import wait_for


def conf(text: str) -> None:
    write(Path(os.environ["FLEET_RECONCILE_CONF_ROOT"]) / "orchestration" / "reconcile.conf", text)


def test_the_file_sets_a_clock_and_the_environment_beats_it(recon, monkeypatch):
    monkeypatch.delenv("FLEET_RECONCILE_SHEPHERD_SECS")
    conf("SHEPHERD_SECS=120\nREFUEL_SECS=600\n")
    out = recon("status").out
    expect(out, "what a start would use", "shepherd  every 120s (reconcile.conf)",
           # The fixture exports this one, and the environment wins.
           "refuel    every 4s (FLEET_RECONCILE_REFUEL_SECS)",
           "sync      off (FLEET_RECONCILE_SYNC_SECS)")

    # The operator's copy REPLACES the tracked example rather than merging into
    # it, so a key it leaves out is fleet's default, said as one.
    monkeypatch.delenv("FLEET_RECONCILE_COLLECT_SECS")
    expect(recon("status").out, "collect   every 120s (default)")
    # With no copy at all, the tracked example answers — with the same number.
    (Path(os.environ["FLEET_RECONCILE_CONF_ROOT"]) / "orchestration" / "reconcile.conf").unlink()
    expect(recon("status").out, "collect   every 120s (reconcile.example.conf)",
           "shepherd  every 900s (reconcile.example.conf)")


def test_a_clock_that_is_not_a_number_is_the_default_said_out_loud(recon, monkeypatch):
    monkeypatch.delenv("FLEET_RECONCILE_SHEPHERD_SECS")
    conf("SHEPHERD_SECS=two minutes\n")
    expect(recon("status").out, "shepherd  every 900s (default — reconcile.conf says 'two minutes'")


def test_the_running_loop_reads_the_file_and_picks_up_an_edit(recon, monkeypatch):
    monkeypatch.delenv("FLEET_RECONCILE_SHEPHERD_SECS")
    conf("SHEPHERD_SECS=3600\n")
    recon("ensure")
    assert wait_for(lambda: recon.count("shepherd") >= 1, 15), recon.calls()
    # The status shell below has a different environment from the loop's, so
    # what it prints has to come from the loop.
    expect(recon("status", FLEET_RECONCILE_WATCH_SECS="99").out,
           "what the loop is using", "watch     every 1s", "shepherd  every 3600s (reconcile.conf)")
    assert wait_for(lambda: recon.count("collect") >= 3, 25)
    assert recon.count("shepherd") == 1, recon.calls()

    conf("SHEPHERD_SECS=2\n")
    assert wait_for(lambda: recon.count("shepherd") >= 3, 25), recon.calls()
    expect(recon.log(), "cadence shepherd: every 3600s -> every 2s (reconcile.conf)")
    expect(recon("status").out, "shepherd  every 2s (reconcile.conf)")
