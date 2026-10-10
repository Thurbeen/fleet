"""The reconciler's clocks, from `orchestration/reconcile.conf`.

The environment was the only way to set an interval, and a service manager's
unit is not where an operator expects to tune one. The file sets each clock,
the environment still beats the file, and with no copy at all the loop runs on
exactly the numbers it always ran on.
"""

from __future__ import annotations

import pytest

from harness import lib, write

CLOCKS = ("WATCH", "COLLECT", "REFUEL", "SHEPHERD", "SYNC")


@pytest.fixture
def clean(isolated_env, monkeypatch):
    for name in CLOCKS:
        monkeypatch.delenv(f"FLEET_RECONCILE_{name}_SECS", raising=False)
    return isolated_env / "settings" / "orchestration"


def clocks() -> tuple:
    cfg = lib("reconcile.py").Config.from_env()
    return cfg.watch, cfg.collect, cfg.refuel, cfg.shepherd, cfg.sync


def test_with_no_copy_every_clock_is_the_default(clean):
    assert not (clean / "reconcile.conf").exists()
    assert clocks() == (20, 120, 300, 900, 900)


def test_the_file_sets_a_clock_and_the_environment_beats_it(clean, monkeypatch):
    write(clean / "reconcile.conf", "SHEPHERD_SECS=120\nCOLLECT_SECS=30\n# REFUEL_SECS=1\n")
    assert clocks() == (20, 30, 300, 120, 900)
    monkeypatch.setenv("FLEET_RECONCILE_SHEPHERD_SECS", "600")
    assert clocks() == (20, 30, 300, 600, 900)


def test_a_value_that_is_not_a_number_refuses_and_names_it(clean):
    write(clean / "reconcile.conf", "SHEPHERD_SECS=soon\n")
    with pytest.raises(SystemExit, match="SHEPHERD_SECS='soon' in reconcile.conf"):
        clocks()
