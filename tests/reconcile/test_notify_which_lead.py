"""The lead is THIS checkout's session, not the first one wearing its name.

thurbox mirrors every host it reaches into `session list`, and one Mission
Control on a remote host beside one on this machine is a normal fleet. Both
wear the same name. Picking the first row by name typed the ready notice into
whichever lead the list happened to put first, and `ensure --if-lead` from the
real local lead compared its id with the remote one's and silently skipped.
The row that is this checkout's lead runs on this machine with this checkout
as its cwd; the name alone answers only for a sole legacy row whose metadata
does not contradict local ownership. Rows known to run elsewhere are no lead.
"""

from __future__ import annotations

import json

import pytest

from harness import PYTHON, REPO, expect, run, write
from reconcilekit import LEAD

NOTIFY = REPO / "scripts" / "lib" / "notify_conductor.py"


def notify(state_dir, ready=("alpha/01-first",)):
    plan = json.dumps({"ready": list(ready), "stalled": []})
    return run([*PYTHON, str(NOTIFY), "--state-dir", str(state_dir)], stdin=plan, FLEET_LEAD_SESSION=LEAD)


def session(stubs, file: str, **row) -> None:
    """One `session list` row. The stub lists them in file-name order."""
    row = {"name": LEAD, "state": "idle", **row}
    write(stubs.root / "sessions" / f"{file}.json", json.dumps(row) + "\n")
    stubs.composer(row["id"], "")


def sends(stubs) -> list[str]:
    return stubs.calls("thurbox-cli", "session send")


def note(state_dir) -> str:
    return json.loads((state_dir / "notified.json").read_text(encoding="utf-8")).get("note", "")


def test_a_remote_lead_listed_first_is_not_woken(stubs, tmp_path):
    session(stubs, "a-remote", id="remote-lead", backend_type="ssh:devbox:tmux", cwd="/srv/fleet")
    session(stubs, "b-local", id="local-lead", backend_type="local:tmux", cwd=str(REPO))
    notify(tmp_path)
    assert sends(stubs), "nobody was woken"
    assert all("remote-lead" not in s for s in sends(stubs)), "the remote lead got this fleet's notice"
    expect(sends(stubs)[-1], "session send local-lead", "alpha/01-first")


def test_the_old_backend_spelling_is_local_too(stubs, tmp_path):
    """A remote row whose cwd happens to spell this path is still not here."""
    session(stubs, "a-remote", id="remote-lead", backend_type="ssh:devbox", cwd=str(REPO))
    session(stubs, "b-local", id="local-lead", backend_type="local-tmux", cwd=str(REPO))
    notify(tmp_path)
    expect(sends(stubs)[-1], "session send local-lead")


def test_one_row_with_the_name_and_no_cwd_is_still_the_lead(stubs, tmp_path):
    session(stubs, "lead", id="lead-uuid")
    notify(tmp_path)
    expect(sends(stubs)[-1], "session send lead-uuid", "alpha/01-first")


def test_several_rows_and_none_of_them_here_is_no_lead(stubs, tmp_path):
    session(stubs, "a-remote", id="remote-lead", backend_type="ssh:devbox:tmux", cwd="/srv/fleet")
    session(stubs, "b-other", id="other-lead", backend_type="local:tmux", cwd=str(tmp_path))
    notify(tmp_path)
    assert not sends(stubs), "a lead was guessed among several"
    expect(note(tmp_path), "2 sessions are named", "none of them is this checkout's")


@pytest.mark.parametrize("backend", ["ssh:example:tmux", "wsl:Example:tmux"])
@pytest.mark.parametrize("cwd", [None, str(REPO)])
def test_a_single_off_machine_lead_is_not_this_checkouts_lead(stubs, tmp_path, backend, cwd):
    session(stubs, "lead", id="off-machine-lead", backend_type=backend, cwd=cwd)
    notify(tmp_path)
    assert not sends(stubs), "the off-machine lead got this checkout's notice"
    expect(note(tmp_path), "none of them is this checkout's")


@pytest.mark.parametrize("backend", [None, "local:tmux"])
def test_a_single_lead_in_another_checkout_is_not_woken(stubs, tmp_path, backend):
    session(stubs, "lead", id="other-lead", backend_type=backend, cwd=str(tmp_path))
    notify(tmp_path)
    assert not sends(stubs), "another checkout's lead got this checkout's notice"
    expect(note(tmp_path), "none of them is this checkout's")


@pytest.mark.parametrize("backend", [None, "local:tmux", ""])
def test_a_single_local_lead_without_cwd_keeps_the_legacy_fallback(stubs, tmp_path, backend):
    session(stubs, "lead", id="lead-uuid", backend_type=backend)
    notify(tmp_path)
    expect(sends(stubs)[-1], "session send lead-uuid", "alpha/01-first")


@pytest.mark.parametrize("backend", ["ssh:example:tmux", "wsl:Example:tmux", "local:tmux"])
def test_ensure_if_lead_rejects_a_single_lead_owned_elsewhere(recon, stubs, tmp_path, backend):
    cwd = str(tmp_path) if backend == "local:tmux" else str(REPO)
    session(stubs, "lead-uuid", id="lead-uuid", backend_type=backend, cwd=cwd)
    done = recon("ensure", "--if-lead", THURBOX_SESSION="lead-uuid")
    assert done.code == 0, done.out
    assert not recon.pid(), "another fleet's session started this checkout's reconciler"
