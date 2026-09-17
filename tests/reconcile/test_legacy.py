"""A reconciler from before the uv port is found, named and replaced, and nothing else is.

Fleet's loop used to be bash: `scripts/reconcile.sh` put a supervisor under
setsid that wrote a pidfile and held no lock. An update that deleted the script
left that loop running, failing every pass, and invisible to a Python loop whose
liveness is its lock, so `ensure` started a second loop beside it. The stand-in
here has the same shape: bash running `scripts/reconcile.sh
__fleet-reconcile-supervisor` from the checkout root as its own process group,
with a pass in flight and the script deleted from disk under it.

Each test runs its reconciler from a copy of the checkout, so the only legacy
loop it can find is the one it started, whatever else runs on the machine.

Linux only: the bash loop never ran on native Windows, and a process's argv and
working directory are read from /proc.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from harness import REPO, Run, expect, refute, run_fleet, write
from reconcilekit import wait_for

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the bash reconciler never ran on native Windows, and /proc is how a process is proven",
)

# What the legacy supervisor did that matters here: a child in its own group, a
# pidfile in the runtime directory, and waiting.
LEGACY = """\
sleep 300 &
echo "$$" > "$FLEET_RECONCILE_DIR/pid"
echo "$!"
wait
"""


def checkout_copy(where: Path) -> Path:
    for part in ("fleet", "scripts/lib"):
        shutil.copytree(REPO / part, where / part, ignore=shutil.ignore_patterns("__pycache__"))
    return where


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class Legacy:
    """A legacy supervisor this test owns, started from `checkout`."""

    def __init__(self, checkout: Path):
        script = checkout / "scripts" / "reconcile.sh"
        write(script, LEGACY)
        self.proc = subprocess.Popen(
            ["bash", "scripts/reconcile.sh", "__fleet-reconcile-supervisor"],
            cwd=checkout, start_new_session=True, stdout=subprocess.PIPE, text=True,
        )
        self.pass_in_flight = int(self.proc.stdout.readline())
        script.unlink()  # what the update did

    @property
    def pid(self) -> int:
        return self.proc.pid

    def gone(self) -> bool:
        return self.proc.poll() is not None and not alive(self.pass_in_flight)

    def kill(self) -> None:
        if not self.gone():
            with contextlib.suppress(OSError):
                os.killpg(self.pid, signal.SIGKILL)
        self.proc.wait(timeout=30)
        self.proc.stdout.close()


@pytest.fixture
def here(recon, tmp_path) -> Path:
    return checkout_copy(tmp_path / "checkout")


def reconcile(checkout: Path, *args: str) -> Run:
    return run_fleet("reconcile", *args, cwd=checkout)


def test_status_names_a_legacy_loop_still_running_from_this_checkout(here):
    legacy = Legacy(here)
    try:
        out = reconcile(here, "status").out
        expect(out, "legacy    ", f"pid {legacy.pid}", "scripts/reconcile.sh __fleet-reconcile-supervisor",
               "uv run fleet reconcile ensure")
        assert legacy.proc.poll() is None, "status only looks"
    finally:
        legacy.kill()


@pytest.mark.parametrize(("verb", "then"), [("ensure", "ticking (pid "), ("start", "ticking (pid "),
                                            ("restart", "ticking (pid "), ("stop", "down, durably")])
def test_ensure_start_and_stop_end_the_legacy_loop_first(recon, here, verb, then):
    legacy = Legacy(here)
    try:
        done = reconcile(here, verb)
        assert done.code == 0, done.out
        said = f"stopped a legacy bash reconciler (pid {legacy.pid})"
        expect(done.out, said, then)
        assert wait_for(legacy.gone, 10), "the legacy loop, or the pass it was running, outlived it"
        expect(recon.log(), said)
        refute(reconcile(here, "status").out, "legacy    ")
    finally:
        legacy.kill()


def test_a_look_alike_from_another_checkout_is_never_signalled(recon, here, tmp_path):
    """The same argv, and its pid in this runtime directory's pidfile, but run from
    another checkout: nothing proves it is this checkout's loop, so nothing here
    names it or touches it."""
    stranger = Legacy(tmp_path / "elsewhere")
    try:
        assert recon.pid() == str(stranger.pid), "the pidfile names it"
        refute(reconcile(here, "status").out, "legacy    ")
        done = reconcile(here, "ensure")
        assert done.code == 0, done.out
        refute(done.out, "legacy")
        reconcile(here, "stop")
        assert stranger.proc.poll() is None and alive(stranger.pass_in_flight), "a stranger was signalled"
    finally:
        stranger.kill()


def test_a_legacy_loop_goes_even_while_the_fleet_is_asked_down(recon, here):
    """The down flag is about THIS loop, and never a reason to leave the old one
    ticking: its passes fail either way, and the operator asked for none."""
    assert reconcile(here, "stop").code == 0
    legacy = Legacy(here)
    try:
        done = reconcile(here, "ensure")

        assert done.code == 0, done.out
        expect(done.out, f"stopped a legacy bash reconciler (pid {legacy.pid})", "down, and staying down")
        assert wait_for(legacy.gone, 10), "the legacy loop outlived an ensure under the down flag"
        refute(reconcile(here, "status").out, "ticking", "legacy    ")
    finally:
        legacy.kill()
