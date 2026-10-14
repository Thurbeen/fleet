"""Under `serve`, the loop that runs is the service's own, whatever the code did.

`serve` exists so a service manager keeps the loop up. A loop whose own code
changed on disk used to hand itself to a detached `reconcile ensure`, which
started a detached supervisor: from the first update on, the loop running was
nobody's child, the manager's restart no longer covered it, and when it died
`serve` took over on the modules it imported at boot — under a record naming
the dead loop's pid, which `status` read as code older than the checkout.

So under `serve` a code change is an exit, and the manager starts the new code;
and whichever process takes the lock writes the record of its code as its own.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from harness import PYTHON, Run, refute, run_fleet
from reconcilekit import checkout_copy, wait_for

BOOT = "import sys\nsys.path.insert(0, sys.argv[1])\nfrom fleet.cli import main\nsys.exit(main(sys.argv[2:]))"


@pytest.fixture
def here(recon, tmp_path) -> Path:
    return checkout_copy(tmp_path / "checkout")


def reconcile(checkout: Path, *args: str) -> Run:
    return run_fleet("reconcile", *args, cwd=checkout)


def serve(checkout: Path) -> subprocess.Popen:
    return subprocess.Popen([*PYTHON, "-c", BOOT, str(checkout), "reconcile", "serve"], cwd=checkout,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def serving(recon, checkout: Path) -> bool:
    return bool(recon.pid()) and "up        reconciling" in reconcile(checkout, "status").out


def end(proc: subprocess.Popen) -> None:
    proc.kill()
    proc.wait(timeout=30)


def edit(path: Path) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n# changed under a running loop\n")


def test_a_code_change_under_serve_is_an_exit_its_manager_restarts(recon, here):
    proc = serve(here)
    again = None
    try:
        assert wait_for(lambda: serving(recon, here)), recon.log()

        edit(here / "scripts" / "lib" / "reconcile.py")

        assert wait_for(lambda: proc.poll() is not None, 60), \
            f"serve handed its loop to a detached supervisor nobody restarts\n{recon.log()}"
        assert not recon.pid(), f"a loop outside the service runs\n{recon.log()}"
        refute(recon.log(), "`reconcile ensure` replaces it")

        # What Restart=always does next.
        again = serve(here)
        assert wait_for(lambda: serving(recon, here)), recon.log()
        status = reconcile(here, "status")
        assert status.code == 0, status.out
        refute(status.out, "stale")
    finally:
        for each in (proc, again):
            if each:
                end(each)


def test_serve_taking_over_records_the_code_it_runs_as_its_own(recon, here):
    proc = serve(here)
    try:
        assert wait_for(lambda: serving(recon, here)), recon.log()
        assert reconcile(here, "restart").code == 0
        detached = recon.pid()
        assert detached

        os.kill(int(detached), 9)

        assert wait_for(lambda: recon.pid() not in ("", detached) and serving(recon, here), 60), recon.log()
        status = reconcile(here, "status")
        assert status.code == 0, status.out
        refute(status.out, "stale")
    finally:
        end(proc)
