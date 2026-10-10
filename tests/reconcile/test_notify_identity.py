""""The lead has been told" is true of ONE conversation, and the record says which.

`notified.json` remembered what was typed into the lead and nothing about who
it was typed into, so a lead replaced by a fresh one — `session delete`, and
thurbox's self-heal spawning the extension's session again — inherited
"already told" and was never told. The record now carries the lead it was
true of: thurbox's session id, which owns the mailbox, and the agent's
conversation id, which owns what was typed. A new conversation is told again;
a resumed one (`restart`, `refuel`) is not; a mailbox note still waiting in
the same session's inbox is not posted twice.

And the record is replaced in one step, so a reader racing the writer never
sees a torn file and reads it as "nothing was told".
"""

from __future__ import annotations

import json
import threading

from harness import PYTHON, REPO, expect, lib, run, write
from reconcilekit import LEAD

NOTIFY = REPO / "scripts" / "lib" / "notify_lead.py"


def notify(state_dir, ready=()):
    plan = json.dumps({"ready": list(ready), "stalled": []})
    return run([*PYTHON, str(NOTIFY), "--state-dir", str(state_dir)], stdin=plan, FLEET_LEAD_SESSION=LEAD)


def lead(stubs, sid: str | None, conversation: str = "", state: str = "idle") -> None:
    """The one lead session, as `session list` shows it, or None for none at all."""
    for old in (stubs.root / "sessions").glob("*.json"):
        old.unlink()
    if sid is None:
        return
    row = {"id": sid, "name": LEAD, "state": state, "agent_session_id": conversation}
    write(stubs.root / "sessions" / f"{sid}.json", json.dumps(row) + "\n")
    stubs.composer(sid, "")


def sends(stubs) -> list[str]:
    return stubs.calls("thurbox-cli", "session send")


def test_a_lead_replaced_by_a_fresh_one_is_told_again(stubs, tmp_path):
    lead(stubs, "lead-1", "conversation-1")
    notify(tmp_path, ready=["alpha/01-first"])
    notify(tmp_path, ready=["alpha/01-first"])
    assert len(sends(stubs)) == 1, "the first lead is told once"

    # `session delete`, then self-heal: a new session id and a new conversation.
    lead(stubs, "lead-2", "conversation-2")
    notify(tmp_path, ready=["alpha/01-first"])
    assert len(sends(stubs)) == 2, "a fresh lead inherited 'already told' and was never told"
    expect(sends(stubs)[-1], "session send lead-2", "alpha/01-first")

    notify(tmp_path, ready=["alpha/01-first"])
    assert len(sends(stubs)) == 2, "and the new lead is told once, too"


def test_a_resumed_conversation_is_not_told_twice(stubs, tmp_path):
    lead(stubs, "lead-1", "conversation-1")
    notify(tmp_path, ready=["alpha/01-first"])

    # `restart` resumes the same conversation under the same session.
    lead(stubs, "lead-1", "conversation-1", state="working")
    notify(tmp_path, ready=["alpha/01-first"])
    lead(stubs, "lead-1", "conversation-1")
    notify(tmp_path, ready=["alpha/01-first"])
    assert len(sends(stubs)) == 1, "a resumed lead was told what it already knows"


def test_a_new_conversation_in_the_same_session_keeps_its_pending_note(stubs, tmp_path):
    """The mailbox belongs to the session, so a note waiting there is not
    posted again; what was TYPED belongs to the conversation, so the wake is."""
    lead(stubs, "lead-1", "conversation-1")
    notify(tmp_path, ready=["alpha/01-first"])

    lead(stubs, "lead-1", "conversation-2", state="working")
    notify(tmp_path, ready=["alpha/01-first", "beta/02-second"])
    assert len(stubs.inbox("lead-1")) == 1
    notify(tmp_path, ready=["alpha/01-first", "beta/02-second"])
    assert len(stubs.inbox("lead-1")) == 1, "a note still waiting in the same inbox was posted again"

    lead(stubs, "lead-1", "conversation-2")
    notify(tmp_path, ready=["alpha/01-first", "beta/02-second"])
    assert len(sends(stubs)) == 2, "the new conversation was never woken"


def test_a_new_session_gets_its_own_note(stubs, tmp_path):
    lead(stubs, "lead-1", "conversation-1", state="working")
    notify(tmp_path, ready=["alpha/01-first"])
    assert len(stubs.inbox("lead-1")) == 1

    lead(stubs, "lead-2", "conversation-2", state="working")
    notify(tmp_path, ready=["alpha/01-first"])
    assert len(stubs.inbox("lead-2")) == 1, "the old session's inbox is not the new lead's"


def test_a_record_from_before_the_lead_key_resets_once(stubs, tmp_path):
    write(tmp_path / "notified.json", json.dumps(
        {"told": ["alpha/01-first"], "posted": [], "stalled": [], "stalled_posted": [], "note": ""}))
    lead(stubs, "lead-1", "conversation-1")

    notify(tmp_path, ready=["alpha/01-first"])
    assert len(sends(stubs)) == 1, "a record that names no lead is not proof this one was told"
    notify(tmp_path, ready=["alpha/01-first"])
    assert len(sends(stubs)) == 1, "the migration happens once"


def test_a_lead_that_is_away_keeps_what_it_was_told(stubs, tmp_path):
    lead(stubs, "lead-1", "conversation-1")
    notify(tmp_path, ready=["alpha/01-first"])

    lead(stubs, None)
    notify(tmp_path, ready=["alpha/01-first"])
    lead(stubs, "lead-1", "conversation-1")
    notify(tmp_path, ready=["alpha/01-first"])
    assert len(sends(stubs)) == 1, "a lead nobody could see for a pass is not a new lead"


def test_a_reader_never_sees_a_torn_record(isolated_env):
    """D4: written in place, a reader racing the writer read `{}`, and the lead
    was told twice."""
    mod = lib("notify_lead.py")
    told = [f"topic/{n:03d}-task" for n in range(400)]
    memory = {"told": told, "lead": {"session": "lead-1", "conversation": "conversation-1"}}
    mod.write_state(str(isolated_env), memory, "")
    done = threading.Event()
    torn = []

    def writer():
        for _ in range(300):
            mod.write_state(str(isolated_env), memory, "")
        done.set()

    thread = threading.Thread(target=writer)
    thread.start()
    while not done.is_set():
        if mod.read_state(str(isolated_env)).get("told") != told:
            torn.append(1)
    thread.join()
    assert not torn, f"a reader saw a torn notified.json {len(torn)} time(s)"
