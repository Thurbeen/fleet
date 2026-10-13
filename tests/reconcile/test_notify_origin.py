"""A worker's question goes to its task's origin; everything else goes to the conductor.

The five operational notices — ready, stalled, unprompted, reviews and the
sync — are the conductor's whatever a task's origin, and `owners.conf` no
longer routes them. A `needs-input` row on a task whose origin fleet can reach
— the session itself, an owners rule's session, or a relay — is told to THAT
origin, by the conductor's own rules: at rest, into an empty input line, else
the mailbox with `--no-wake`, once per question. A session origin that is gone
is told nothing and the conductor is not told instead. An origin fleet has no
road to, or none at all, leaves the question with the conductor.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import yaml

from harness import PYTHON, REPO, expect, run, run_queue as q, write
from reconcilekit import LEAD

NOTIFY = REPO / "scripts" / "lib" / "notify_conductor.py"
ORIGIN = "chat:room/42"
SID = "origin-id"


def conf(text: str) -> None:
    write(Path(os.environ["FLEET_OWNERS_ROOT"]) / "orchestration" / "owners.conf", text)


def task(slug: str, origin: str | None = None, session: str | None = None) -> str:
    topic = "owned"
    q("topic", "add", topic, "--title", "Owned", "--prompt", "p")
    ref = q("add", topic, slug, "--title", slug, "--repo", "/tmp/repo-a", "--branch", f"fix/{slug}",
            "--publish", "none", *(["--origin", origin] if origin else []),
            THURBOX_SESSION=None).stdout.strip()
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


def notify(state_dir, ready=(), asking=()) -> str:
    plan = json.dumps({"ready": list(ready), "stalled": [], "needs_input": [
        {"task": ref, "state": "needs-input", "why": why, "declared": at} for ref, why, at in asking]})
    return run([*PYTHON, str(NOTIFY), "--state-dir", str(state_dir)], stdin=plan,
               FLEET_LEAD_SESSION=LEAD).stdout


def asks(ref: str, why: str = "which database?", at: str = "2026-10-13T10:00:00+00:00"):
    return [(ref, why, at)]


def sends(stubs) -> list[str]:
    return stubs.calls("thurbox-cli", "session send")


def to(stubs, sid: str) -> list[str]:
    return [s for s in sends(stubs) if s.split()[3] == sid]


# --- the operational notices are the conductor's -----------------------------


def test_ready_work_on_an_owned_origin_goes_to_the_conductor_and_not_the_owner(stubs, tmp_path):
    conf("chat: = Concierge\n")
    ref = task("mine", ORIGIN)
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    session(stubs, "owner", "Concierge", "owner-id", backend_type="local:tmux")
    notify(tmp_path, [ref])
    assert to(stubs, "owner-id") == [], "owners.conf still routes an operational notice"
    expect(to(stubs, "lead-id")[0], ref, "uv run fleet queue dispatch")


def test_a_notified_json_from_before_the_rename_is_still_read(stubs, tmp_path):
    """The loop's memory keeps its file and its keys, so a conductor that was
    told as the lead is not told again after an upgrade."""
    ref = task("old")
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    write(tmp_path / "notified.json", json.dumps({
        "told": [ref], "posted": [], "note": "",
        "lead": {"session": "lead-id", "conversation": "lead-id"},
    }))
    notify(tmp_path, [ref])
    assert sends(stubs) == [], "an existing notified.json was not read, and the lead was told twice"


# --- a question goes to its origin -------------------------------------------


def test_a_question_reaches_the_session_origin_once_and_not_the_conductor(stubs, tmp_path):
    ref = task("mine", f"thurbox:{SID}")
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    session(stubs, "origin", "Somebody", SID, backend_type="local:tmux")
    notify(tmp_path, asking=asks(ref))
    assert to(stubs, "lead-id") == [], "the conductor was told a question its origin owns"
    expect(to(stubs, SID)[0], ref, "which database?", f"fleet queue send {ref}")
    notify(tmp_path, asking=asks(ref))
    assert len(to(stubs, SID)) == 1, "the same question was asked twice"
    notify(tmp_path, asking=asks(ref, "and which schema?", "2026-10-13T11:00:00+00:00"))
    assert len(to(stubs, SID)) == 2, "a second question is news"
    expect(to(stubs, SID)[1], "and which schema?")


def test_a_question_whose_session_origin_is_gone_is_told_to_nobody(stubs, tmp_path):
    ref = task("orphan", "thurbox:deleted-by-accident")
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    out = notify(tmp_path, asking=asks(ref))
    assert sends(stubs) == [] and stubs.inbox("lead-id") == [], "a gone origin fell back to the lead"
    expect(out, ref, "is gone", "stays in its own pane")
    assert notify(tmp_path, asking=asks(ref)) == "", "the same gone origin was logged again"


def test_a_question_with_no_origin_or_no_road_to_it_stays_with_the_conductor(stubs, tmp_path):
    plain, unrouted = task("plain"), task("mail", "mail:inbox/7")
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    notify(tmp_path, asking=asks(plain) + asks(unrouted))
    expect(to(stubs, "lead-id")[0], "need input", plain, unrouted)


def test_an_origin_mid_turn_gets_its_mailbox_once_and_the_wake_at_rest(stubs, tmp_path):
    ref = task("mine", f"thurbox:{SID}")
    session(stubs, "origin", "Somebody", SID, state="working", backend_type="local:tmux")
    notify(tmp_path, asking=asks(ref))
    assert sends(stubs) == []
    assert [m["woke"] for m in stubs.inbox(SID)] == [False]
    notify(tmp_path, asking=asks(ref))
    assert len(stubs.inbox(SID)) == 1, "the mailbox note was repeated"
    session(stubs, "origin", "Somebody", SID, backend_type="local:tmux")
    notify(tmp_path, asking=asks(ref))
    assert len(to(stubs, SID)) == 1, "the wake did not land once the origin was at rest"


def test_an_origin_with_something_typed_is_not_typed_into(stubs, tmp_path):
    ref = task("mine", f"thurbox:{SID}")
    session(stubs, "origin", "Somebody", SID, backend_type="local:tmux")
    stubs.composer(SID, "half a thought")
    notify(tmp_path, asking=asks(ref))
    assert sends(stubs) == []
    assert len(stubs.inbox(SID)) == 1


def test_an_owners_rule_routes_a_question_to_its_session(stubs, tmp_path):
    conf("chat: = Concierge\n")
    ref = task("mine", ORIGIN)
    task("worker", None, session="worker-id")
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    session(stubs, "owner", "Concierge", "owner-id", backend_type="local:tmux")
    # A worker of this fleet whose title shares the word is not the owner.
    session(stubs, "worker", "Concierge fix", "worker-id", backend_type="local:tmux")
    notify(tmp_path, asking=asks(ref))
    assert len(to(stubs, "owner-id")) == 1
    assert to(stubs, "lead-id") == [] and to(stubs, "worker-id") == []


def test_an_owner_that_is_only_a_mirrored_remote_session_is_not_typed_into(stubs, tmp_path):
    conf("chat: = Concierge\n")
    ref = task("mine", ORIGIN)
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    session(stubs, "owner", "Concierge", "remote-owner", backend_type="ssh:devbox:tmux")
    out = notify(tmp_path, asking=asks(ref))
    assert sends(stubs) == [], "a session on another machine was typed into"
    assert stubs.inbox("remote-owner") == []
    expect(out, "none of them is on this machine")


def test_two_local_owners_are_ambiguous_and_get_nothing(stubs, tmp_path):
    conf("chat: = Concierge\n")
    ref = task("mine", ORIGIN)
    session(stubs, "a", "Concierge", "one", backend_type="local:tmux")
    session(stubs, "b", "Concierge", "two", backend_type="local:tmux")
    out = notify(tmp_path, asking=asks(ref))
    assert sends(stubs) == []
    expect(out, "2 sessions are named like 'Concierge'")


def relay_rule(script: str, *args: str) -> str:
    return "!" + shlex.join([sys.executable, "-c", script, *args])


def test_a_relay_is_run_with_the_origin_and_the_question(stubs, tmp_path):
    got = tmp_path / "relayed.json"
    keep = "import json, sys; open(sys.argv[1], 'w').write(json.dumps(sys.argv[2:]))"
    conf(f"chat: = {relay_rule(keep, str(got))}\n")
    ref = task("mine", ORIGIN)
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    notify(tmp_path, asking=asks(ref))
    origin, line = json.loads(got.read_text(encoding="utf-8"))
    assert origin == ORIGIN
    expect(line, ref, "which database?")
    assert sends(stubs) == [], "a relayed question also reached a session"


def test_a_failing_relay_fails_nothing_and_is_given_up_on(stubs, tmp_path):
    conf(f"chat: = {relay_rule('import sys; sys.exit(3)')}\n")
    ref = task("mine", ORIGIN)
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    for _ in range(3):
        done = run([*PYTHON, str(NOTIFY), "--state-dir", str(tmp_path)],
                   stdin=json.dumps({"needs_input": [{"task": ref, "why": "?", "declared": "t"}]}),
                   FLEET_LEAD_SESSION=LEAD)
        assert done.code == 0
    expect(done.stdout, "exit 3", "given up after 3 tries")
    assert sends(stubs) == []


def test_the_tracked_owners_example_names_nobody():
    text = (REPO / "orchestration" / "owners.example.conf").read_text(encoding="utf-8")
    live = [line for line in text.splitlines() if line.split("#", 1)[0].strip()]
    assert live == [], f"owners.example.conf names an owner; the tracked copy must name none: {live}"


def test_nothing_to_tell_asks_thurbox_nothing(stubs, tmp_path):
    conf("chat: = Concierge\n")
    session(stubs, "lead", LEAD, "lead-id", cwd=str(REPO))
    notify(tmp_path)
    assert not stubs.calls("thurbox-cli", "session list"), "an idle pass polled the session list"
