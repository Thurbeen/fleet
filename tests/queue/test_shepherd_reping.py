"""The shepherd sends a fix once per (condition, head), not once per pull request.

Observed on one pull request: a `checks-failed` fix went into the task's own
worker, which finished it. Then `main` moved and the pull request turned
`conflicting`, and the shepherd said "in-flight: a fixer went out for this
(now done). Nothing sent." until somebody passed `--force`. A record of a fix
sent held back every LATER fix for the same pull request.

So the record is keyed by (condition, head), the way `shepherd_comments` keys
comments by id: the same one is still sent once, a different condition or a new
head goes to the session already on it once that session is at rest by its own
word, a working session is never typed into, and only when every session on it
is gone is a fresh one spawned. Typing into a session and spawning a session are
two different acts, and each has its own word.
"""

import json

import pytest
from test_shepherd_comments import PR, WORKER, talk_on

from queuekit import ok

from harness import expect, refute
from harness import run_queue as q

FAILED = [{"__typename": "CheckRun", "name": "CI", "status": "COMPLETED", "conclusion": "FAILURE"}]


@pytest.fixture
def talk(stubs, tmp_path, queue_dir):
    t = talk_on(stubs, tmp_path, queue_dir, vetted=False)
    t.shep.update(201, statusCheckRollup=FAILED)
    return t


def fix_sends(talk, sid: str = WORKER) -> list[str]:
    return [c for c in talk.sends(sid) if "fix-" in c and "comments" not in c]


def test_a_changed_condition_re_pings_the_session_at_rest(talk):
    out = talk.pass_()
    expect(out, "checks-failed", "pinged:", WORKER)
    assert len(fix_sends(talk)) == 1, talk.shep.tbx_log()

    # The worker finished; then `main` moved under the pull request.
    talk.shep.update(201, mergeable="CONFLICTING")
    out = talk.pass_()
    expect(out, "conflicting", "pinged:")
    refute(out, "in-flight")
    assert len(fix_sends(talk)) == 2, talk.shep.tbx_log()
    assert talk.shep.creates() == [], talk.shep.tbx_log()

    sent = talk.task()["shepherd"]["sent"]
    assert [s["condition"] for s in sent] == ["checks-failed", "conflicting"], sent
    assert {s["session"] for s in sent} == {WORKER}, sent


def test_the_same_condition_on_the_same_head_is_sent_once(talk):
    talk.pass_()
    out = talk.pass_()
    expect(out, "already-sent", "not again until the condition or the head changes")
    assert len(fix_sends(talk)) == 1, talk.shep.tbx_log()

    # A push is a new head, so the same condition on it is news.
    talk.shep.update(201, headRefOid="a" * 40)
    out = talk.pass_()
    expect(out, "checks-failed", "pinged:")
    assert len(fix_sends(talk)) == 2, talk.shep.tbx_log()
    # And the record forgets the old head's sends.
    sent = talk.task()["shepherd"]["sent"]
    assert [s["head"] for s in sent] == ["a" * 40], sent


def test_force_re_sends_the_same_condition(talk):
    talk.pass_()
    out = talk.pass_("--force")
    expect(out, "pinged:")
    assert len(fix_sends(talk)) == 2, talk.shep.tbx_log()


@pytest.mark.parametrize("state", ["working", "blocked"])
def test_a_busy_session_is_never_pinged(talk, state):
    talk.pass_()
    talk.shep.session(WORKER, state)
    talk.shep.update(201, mergeable="CONFLICTING")
    out = talk.pass_()
    expect(out, "in-flight", state)
    assert len(fix_sends(talk)) == 1, talk.shep.tbx_log()
    assert talk.shep.creates() == [], talk.shep.tbx_log()


def test_a_gone_session_is_replaced_by_a_spawn_and_the_spawn_is_pinged_next(talk):
    talk.pass_()
    (talk.shep.stubs.root / "sessions" / f"{WORKER}.json").unlink()
    talk.shep.update(201, mergeable="CONFLICTING")
    out = talk.pass_()
    expect(out, "conflicting", "spawned:", "new session")
    assert len(talk.shep.creates()) == 1, talk.shep.tbx_log()
    rec = talk.task()["shepherd"]
    assert rec["how"] == "spawned" and rec["session"] != WORKER, rec

    # The spawned fixer is now the session on it: a later condition is typed
    # into it at rest, never a second spawn beside it.
    talk.shep.update(201, mergeable="MERGEABLE", headRefOid="b" * 40)
    out = talk.pass_()
    expect(out, "checks-failed", "pinged:", rec["session"])
    assert len(talk.shep.creates()) == 1, talk.shep.tbx_log()


def store_actions(sid: str) -> dict:
    store = json.loads(q("prs", "--json").stdout)
    return {p["url"]: p["action"] for p in (store["sessions"].get(sid) or {}).get("prs") or []}


def test_ping_and_spawn_are_distinct_words_in_the_report_and_the_store(talk):
    dry = q("shepherd", "--topic", talk.topic, "--dry-run").out
    expect(dry, "would-ping")
    refute(dry, "would-spawn")
    talk.pass_()
    assert store_actions(WORKER)[PR] == "sent"

    (talk.shep.stubs.root / "sessions" / f"{WORKER}.json").unlink()
    talk.shep.update(201, mergeable="CONFLICTING")
    dry = q("shepherd", "--topic", talk.topic, "--dry-run").out
    expect(dry, "would-spawn")
    refute(dry, "would-ping")
    # Unnarrowed, so the store settles the worker thurbox no longer lists.
    expect(ok(q("shepherd")).out, "spawned:")
    fixer = talk.task()["shepherd"]["session"]
    # Filed under the session that is on it now, in the store's own word.
    assert store_actions(fixer)[PR] == "fixer"
    assert store_actions(WORKER) == {}
