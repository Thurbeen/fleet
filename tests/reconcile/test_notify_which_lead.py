"""The lead is THIS checkout's session, not the first one wearing its name.

thurbox mirrors every host it reaches into `session list`, and one Mission
Control on a remote host beside one on this machine is a normal fleet. Both
wear the same name. Picking the first row by name typed the ready notice into
whichever lead the list happened to put first, and `ensure --if-lead` from the
real local lead compared its id with the remote one's and silently skipped.
The row that is this checkout's lead runs on this machine with this checkout
as its cwd; the name alone answers only when exactly one row carries it, and
several rows with none of them here are no lead, said so, never a guess.
"""

from __future__ import annotations

import json

from harness import PYTHON, REPO, expect, run, write
from reconcilekit import LEAD

NOTIFY = REPO / "scripts" / "lib" / "notify_lead.py"


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
