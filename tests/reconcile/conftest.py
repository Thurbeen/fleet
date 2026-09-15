import sys

import pytest
from reconcilekit import LEAD, STUB, Recon, queue_cmd


@pytest.fixture
def recon(isolated_env, monkeypatch, stubs):
    """A reconciler over a throwaway queue, stopped whatever the test did."""
    root = isolated_env
    monkeypatch.setenv("RECON_CALLS", str(root / "calls"))
    monkeypatch.setenv("RECON_MOVED", str(root / "moved"))
    monkeypatch.setenv("RECON_READY", str(root / "ready"))
    monkeypatch.setenv("FLEET_RECONCILE_QUEUE_CMD", queue_cmd(sys.executable, str(STUB)))
    # The lead's name is normally read out of the rendered extension.toml, which
    # belongs to the operator's checkout; this is the override that seam exists for.
    monkeypatch.setenv("FLEET_LEAD_SESSION", LEAD)
    for var, secs in (("WATCH", "1"), ("COLLECT", "2"), ("REFUEL", "4"), ("SHEPHERD", "6")):
        monkeypatch.setenv(f"FLEET_RECONCILE_{var}_SECS", secs)
    r = Recon(root)
    r.lead("idle")
    yield r
    # Unconditional: a leaked loop keeps running over a directory about to be deleted.
    r("stop")
