"""A task's notices go to the session that owns its origin, and to nobody else.

`orchestration/owners.conf` maps an origin prefix to a session-name substring.
A matching task's notice is delivered to that session by the lead's own rules —
at rest, into an empty input line, else the mailbox with `--no-wake`, once per
transition — and the lead is not told of it. A task nothing matches goes to the
lead as it always did. The owner is typed into only when it is provably the one
local session: a mirrored one from another host, or an ambiguous name, is not.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from harness import PYTHON, REPO, expect, run, run_queue as q, write
from reconcilekit import LEAD

NOTIFY = REPO / "scripts" / "lib" / "notify_lead.py"
ORIGIN = "chat:room/42"


def conf(text: str) -> None:
    write(Path(os.environ["FLEET_OWNERS_ROOT"]) / "orchestration" / "owners.conf", text)


def task(slug: str, origin: str | None = None, session: str | None = None) -> str:
    topic = "owned"
    q("topic", "add", topic, "--title", "Owned", "--prompt", "p")
    ref = q("add", topic, slug, "--title", slug, "--repo", "/tmp/repo-a", "--branch", f"fix/{slug}",
            "--publish", "none", *(["--origin", origin] if origin else [])).stdout.strip()
    if session:
        path = Path(os.environ["FLEET_QUEUE_DIR"]) / ref / "task.yaml"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        doc["session"] = session
        path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return ref


def session(stubs, file: str, name: str, sid: str, state: str = "idle", **row) -> None:
    write(stubs.root / "sessions" / f"{file}.json",
          json.dumps({"name": name, "id": sid, "state": state, **row}) + "\n")
    stubs.composer(sid, "")


def notify(state_dir, ready) -> str:
    plan = json.dumps({"ready": list(ready), "stalled": []})
    return run([*PYTHON, str(NOTIFY), "--state-dir", str(state_dir)], stdin=plan,
               FLEET_LEAD_SESSION=LEAD).stdout


def sends(stubs) -> list[str]:
    return stubs.calls("thurbox-cli", "session send")


def test_a_matching_origin_reaches_the_owner_and_not_the_lead(stubs, tmp_path):
    conf("chat: = Concierge\n")
    ref = task("mine", ORIGIN)
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    session(stubs, "owner", "Concierge", "owner-id", backend_type="local:tmux", cwd="/srv/other")
    notify(tmp_path, [ref])
    assert len(sends(stubs)) == 1
    expect(sends(stubs)[0], "session send owner-id", ref)
    # Once per transition: the same notice again types nothing.
    notify(tmp_path, [ref])
    assert len(sends(stubs)) == 1


def test_an_unmatched_or_originless_task_still_goes_to_the_lead(stubs, tmp_path):
    conf("chat: = Concierge\n")
    plain, other = task("plain"), task("other", "mail:inbox/7")
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    session(stubs, "owner", "Concierge", "owner-id", backend_type="local:tmux")
    notify(tmp_path, [plain, other])
    assert len(sends(stubs)) == 1
    expect(sends(stubs)[0], "session send lead-id", plain, other)


def test_the_split_sends_each_recipient_only_its_own(stubs, tmp_path):
    conf("chat: = Concierge\n")
    mine, theirs = task("mine", ORIGIN), task("lead-work")
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    session(stubs, "owner", "Concierge", "owner-id", backend_type="local:tmux")
    notify(tmp_path, [mine, theirs])
    by = {s.split()[3]: s for s in sends(stubs)}
    assert mine in by["owner-id"] and theirs not in by["owner-id"]
    assert theirs in by["lead-id"] and mine not in by["lead-id"]


def test_an_owner_mid_turn_gets_its_mailbox_and_the_lead_none(stubs, tmp_path):
    conf("chat: = Concierge\n")
    ref = task("mine", ORIGIN)
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    session(stubs, "owner", "Concierge", "owner-id", state="working", backend_type="local:tmux")
    notify(tmp_path, [ref])
    assert not sends(stubs)
    assert [m["woke"] for m in stubs.inbox("owner-id")] == [False]
    assert stubs.inbox("lead-id") == []
    notify(tmp_path, [ref])
    assert len(stubs.inbox("owner-id")) == 1, "the mailbox note was repeated"


def test_an_owner_with_something_typed_is_not_typed_into(stubs, tmp_path):
    conf("chat: = Concierge\n")
    ref = task("mine", ORIGIN)
    session(stubs, "owner", "Concierge", "owner-id", backend_type="local:tmux")
    stubs.composer("owner-id", "half a thought")
    notify(tmp_path, [ref])
    assert not sends(stubs)
    assert len(stubs.inbox("owner-id")) == 1


def test_an_owner_that_is_only_a_mirrored_remote_session_is_not_typed_into(stubs, tmp_path):
    conf("chat: = Concierge\n")
    ref = task("mine", ORIGIN)
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    session(stubs, "owner", "Concierge", "remote-owner", backend_type="ssh:devbox:tmux")
    out = notify(tmp_path, [ref])
    assert not sends(stubs), "a session on another machine was typed into"
    assert stubs.inbox("remote-owner") == []
    expect(out, "none of them is on this machine")


def test_two_local_candidates_are_ambiguous_and_get_nothing(stubs, tmp_path):
    conf("chat: = Concierge\n")
    ref = task("mine", ORIGIN)
    session(stubs, "a", "Concierge", "one", backend_type="local:tmux")
    session(stubs, "b", "Concierge", "two", backend_type="local:tmux")
    out = notify(tmp_path, [ref])
    assert not sends(stubs)
    expect(out, "2 sessions are named like 'Concierge'")


def test_a_worker_of_this_fleet_that_happens_to_share_the_word_is_not_the_owner(stubs, tmp_path):
    conf("chat: = Concierge\n")
    ref = task("mine", ORIGIN)
    task("worker", None, session="worker-id")
    session(stubs, "owner", "Concierge", "owner-id", backend_type="local:tmux")
    session(stubs, "worker", "Concierge fix", "worker-id", backend_type="local:tmux")
    notify(tmp_path, [ref])
    expect(sends(stubs)[0], "session send owner-id")


def test_no_owner_running_leaves_the_notice_waiting_and_the_lead_untold(stubs, tmp_path):
    conf("chat: = Concierge\n")
    ref = task("mine", ORIGIN)
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    out = notify(tmp_path, [ref])
    assert not sends(stubs)
    expect(out, "no session named like 'Concierge' is running")
    session(stubs, "owner", "Concierge", "owner-id", backend_type="local:tmux")
    notify(tmp_path, [ref])
    expect(sends(stubs)[0], "session send owner-id")


def test_no_rules_changes_nothing_even_for_a_task_with_an_origin(stubs, tmp_path):
    ref = task("mine", ORIGIN)
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    notify(tmp_path, [ref])
    expect(sends(stubs)[0], "session send lead-id", ref)


def test_the_tracked_owners_example_names_nobody():
    text = (REPO / "orchestration" / "owners.example.conf").read_text(encoding="utf-8")
    live = [line for line in text.splitlines() if line.split("#", 1)[0].strip()]
    assert live == [], f"owners.example.conf names an owner; the tracked copy must name none: {live}"


def test_rules_with_nothing_to_tell_ask_thurbox_nothing(stubs, tmp_path):
    conf("chat: = Concierge\n")
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    notify(tmp_path, [])
    assert not stubs.calls("thurbox-cli", "session list"), "an idle pass polled the session list"
    notify(tmp_path, [task("plain")])
    assert len(stubs.calls("thurbox-cli", "session list")) == 1, "an unowned notice polled for an owner"
