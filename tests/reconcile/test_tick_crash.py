"""A loop whose own pass crashes is a fault in `status`, not a green "up".

`supervise` restarts a pass that raises, and every restart beats again before
the pass crashes, so the heartbeat stays fresh and the lock stays held while
nothing past `root` ever runs. Only children were ever counted in
`failures.json`, never the loop itself, so `status` said "up" and exited 0 and
every SessionStart `ensure` adopted it (issue #248).

Each runs its loop from a copy of the checkout, so breaking that loop's code
breaks nothing else.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from harness import Run, expect, refute, run_fleet
from reconcilekit import checkout_copy, wait_for


@pytest.fixture
def here(recon, tmp_path) -> Path:
    return checkout_copy(tmp_path / "checkout")


def reconcile(checkout: Path, *args: str, **env: str | None) -> Run:
    return run_fleet("reconcile", *args, cwd=checkout, **env)


def break_the_pass(checkout: Path, unless: Path | None = None) -> None:
    """Make every pass raise inside its body, after its first beat, as a
    persistent fault in code the loop holds does: the forge probe raises.
    With `unless`, only while that file does not exist."""
    guard = f"if not __import__('os').path.exists({str(unless)!r}):\n        " if unless else ""
    with open(checkout / "scripts" / "lib" / "forge.py", "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n\n_real_available = available\n\n\ndef available():\n    "
                 f"{guard}raise RuntimeError('forge probe broken on purpose')\n"
                 "    return _real_available()\n")


def test_a_pass_that_crashes_every_time_is_failing_in_status(recon, here):
    break_the_pass(here)
    assert reconcile(here, "ensure").code == 0, recon.log()

    # Restarted at least once: what the issue saw as "up" for hours.
    assert wait_for(lambda: recon.log().count("tick loop raised") >= 2, 30), recon.log()
    assert recon.count("collect") == 0, "the broken pass ran collect after all"

    status = reconcile(here, "status")
    assert status.code == 1, f"a loop whose every pass crashes is a fault:\n{status.out}"
    expect(status.out, "failing   tick", "RuntimeError: forge probe broken on purpose")

    again = reconcile(here, "ensure")
    assert again.code == 1, again.out
    refute(again.out, "adopted, not restarted")
    expect(again.out, "its pass crashes", "RuntimeError: forge probe broken on purpose")


def test_a_pass_that_runs_again_clears_its_crash(recon, here, tmp_path):
    fixed = tmp_path / "fixed"
    break_the_pass(here, unless=fixed)
    assert reconcile(here, "ensure").code == 0, recon.log()
    assert wait_for(lambda: "failing   tick" in reconcile(here, "status").out, 30), recon.log()

    fixed.touch()

    assert wait_for(lambda: recon.count("collect") >= 1, 90), recon.log()
    assert wait_for(lambda: reconcile(here, "status").code == 0, 30), reconcile(here, "status").out
    refute(reconcile(here, "status").out, "failing")


def test_a_sibling_whose_import_failed_is_imported_again_not_handed_back_half_made(here):
    """`_load_sibling` registered the module before running it, so one failed
    import left a half-made module in `sys.modules` that every later load
    returned: a permanent AttributeError instead of a retried import."""
    lib = here / "scripts" / "lib"
    marker = here / "failed-once"
    (lib / "flaky.py").write_text(
        "import os\n"
        f"if not os.path.exists({str(marker)!r}):\n"
        f"    open({str(marker)!r}, 'w').close()\n"
        "    raise RuntimeError('failed once on purpose')\n"
        "VALUE = 'imported'\n",
        encoding="utf-8",
    )
    probe = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('fleet_reconcile', sys.argv[1])\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['fleet_reconcile'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "try:\n"
        "    mod._load_sibling('fleet_flaky', 'flaky.py')\n"
        "except RuntimeError as exc:\n"
        "    print('first:', exc)\n"
        "print('second:', getattr(mod._load_sibling('fleet_flaky', 'flaky.py'), 'VALUE', 'half-made'))\n"
    )
    done = subprocess.run([sys.executable, "-c", probe, str(lib / "reconcile.py")],
                          capture_output=True, text=True, encoding="utf-8", cwd=here)

    assert done.returncode == 0, done.stdout + done.stderr
    expect(done.stdout, "first: failed once on purpose", "second: imported")
