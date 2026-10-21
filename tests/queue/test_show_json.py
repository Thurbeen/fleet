"""`show --json` is the stable read of one task for another program.

An orchestrator mirroring fleet's tasks scraped `show`'s text, re-read
progress.jsonl for a declaration (and applied the invalidation rule wrong),
string-matched shepherd's "marked ready" line, parsed "N restart(s) of M", and
walked thurbox's session tree. Each of those is a field here, answered by the
same helper fleet's own commands use.
"""

import json
from datetime import timedelta
from pathlib import Path

import yaml
from queuekit import S1, S2, ok
from test_declare import REF, at_rest, plan, stamp
from test_hosts import hosts  # noqa: F401 — the fixture
from test_shepherd_draft import draft  # noqa: F401 — the fixture
from test_shepherd_comments import PR

from harness import run_queue as q
from harness import write

KEYS = {
    "ref", "topic", "slug", "title", "state", "outcome", "origin", "publish", "agent",
    "repo", "host", "branch", "session", "artifacts", "abandoned", "refuels",
    "needs_input", "paused", "live_sessions", "live_sessions_count",
    "live_sessions_unreadable", "files",
}


def show(ref: str = REF) -> dict:
    return json.loads(ok(q("show", ref, "--json")).stdout)


def edit(queue_dir: Path, ref: str, **fields) -> None:
    record = queue_dir / ref / "task.yaml"
    doc = yaml.safe_load(record.read_text(encoding="utf-8"))
    doc.update(fields)
    record.write_text(yaml.safe_dump(doc), encoding="utf-8")


def session(stubs, sid: str, parent: str | None = None) -> None:
    stubs.session_is(sid, "working")
    path = stubs.root / "sessions" / f"{sid}.json"
    row = json.loads(path.read_text(encoding="utf-8"))
    write(path, json.dumps(row | {"parent_session_id": parent}) + "\n")


def test_every_documented_key_is_there_with_the_record(attached, stubs, queue_dir):
    stubs.session_is(S1, "working")
    doc = show()
    assert set(doc) == KEYS
    topic, slug = REF.split("/")
    assert (doc["ref"], doc["topic"], doc["slug"]) == (REF, topic, slug)
    assert doc["state"] == "dispatched" and doc["session"] == S1
    assert doc["publish"] == "attested"
    assert doc["artifacts"] == [] and doc["abandoned"] is None
    assert doc["refuels"] == {"count": 0, "cap": 3}
    assert doc["needs_input"] is None and doc["paused"] is None
    files = doc["files"]
    assert files["brief"] == str((queue_dir / REF / "BRIEF.md").resolve())
    assert files["progress"].endswith("progress.jsonl") and files["result"].endswith("result.md")


def test_refuels_count_against_the_cap(attached, queue_dir):
    edit(queue_dir, REF, refuels=[{"at": stamp(-timedelta(minutes=9)), "session": S1, "why": "quota"},
                                  {"at": stamp(-timedelta(minutes=1)), "session": S1, "why": "quota"}])
    assert show()["refuels"] == {"count": 2, "cap": 3}


def test_an_abandoned_task_says_how_and_why(attached):
    ok(q("abandon", REF, "--why", "superseded", "--force"))
    doc = show()
    assert doc["state"] == "abandoned"
    assert doc["abandoned"] == {"how": "hand", "why": "superseded"}


def test_needs_input_is_the_standing_one_plan_reads(attached, stubs, queue_dir):
    at_rest(stubs, queue_dir)
    ok(q("declare", REF, "needs-input", "--why", "keep the flag?"))
    said = show()["needs_input"]
    assert said["why"] == "keep the flag?"
    assert said["declared"] == plan()["needs_input"][0]["declared"]
    assert show()["paused"] is None


def test_a_written_result_voids_the_question(attached, stubs, queue_dir):
    at_rest(stubs, queue_dir)
    ok(q("declare", REF, "needs-input", "--why", "keep the flag?"))
    assert show()["needs_input"]
    write(queue_dir / REF / "result.md", "outcome: shipped\n")
    assert show()["needs_input"] is None


def test_a_pause_carries_its_until(attached, stubs, queue_dir):
    at_rest(stubs, queue_dir)
    ok(q("declare", REF, "paused", "--until", "2h", "--why", "waiting on CI"))
    doc = show()
    assert doc["needs_input"] is None
    assert doc["paused"]["why"] == "waiting on CI" and doc["paused"]["until"]


def test_a_later_message_voids_the_question(attached, stubs, queue_dir):
    at_rest(stubs, queue_dir)
    ok(q("declare", REF, "needs-input", "--why", "which?"))
    # Pushed back a minute, so the send is strictly later on every clock.
    progress = queue_dir / REF / "progress.jsonl"
    rows = [json.loads(r) for r in progress.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if "declare" in row:
            row["observed"] = stamp(-timedelta(minutes=1))
    write(progress, "".join(json.dumps(r) + "\n" for r in rows))
    assert show()["needs_input"]
    ok(q("send", REF, "drop it"))
    assert show()["needs_input"] is None


def test_a_later_restart_voids_the_question(attached, stubs, queue_dir):
    at_rest(stubs, queue_dir)
    line = {"declare": {"state": "needs-input", "why": "old"}, "observed": stamp(-timedelta(hours=1))}
    with open(queue_dir / REF / "progress.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    assert show()["needs_input"]["why"] == "old"
    edit(queue_dir, REF, refuels=[{"at": stamp(-timedelta(minutes=5)), "session": S1,
                                   "why": "quota", "prompted": True}])
    assert show()["needs_input"] is None


def test_live_sessions_are_the_task_s_and_everything_under_it(attached, stubs):
    child, grandchild, stranger = ("a" * 8 + S1[8:], "b" * 8 + S1[8:], "c" * 8 + S1[8:])
    session(stubs, S1)
    session(stubs, child, parent=S1)
    session(stubs, grandchild, parent=child)
    session(stubs, stranger, parent=S2)
    doc = show()
    assert doc["live_sessions"] == [S1, child, grandchild]
    assert doc["live_sessions_count"] == 3 and doc["live_sessions_unreadable"] is None


def test_a_session_thurbox_does_not_list_is_not_live(attached, stubs):
    session(stubs, S2)
    doc = show()
    assert doc["live_sessions"] == [] and doc["live_sessions_count"] == 0


def test_unreadable_sessions_are_null_and_say_why(attached, stubs):
    stubs.tool("thurbox-cli", "import sys; sys.exit(1)")
    doc = show()
    assert doc["live_sessions"] is None and doc["live_sessions_count"] is None
    assert doc["live_sessions_unreadable"]


def test_ready_is_what_shepherd_recorded_per_artifact(draft):  # noqa: F811
    ref = f"{draft.topic}/01-talk"
    before = show(ref)["artifacts"]
    assert [(a["url"], a["ready"], a["ready_at"]) for a in before] == [(PR, False, None)]
    draft.pass_()
    after = show(ref)["artifacts"]
    assert after[0]["ready"] is True and after[0]["ready_at"]


def test_ready_is_per_artifact_url_not_per_task(attached, queue_dir):
    """Two repositories, one draft flipped: only that artifact reads ready."""
    one, two = "https://github.com/o/a/pull/1", "https://github.com/o/b/pull/2"
    edit(queue_dir, REF, artifact=[{"repo": "/tmp/repo-a", "url": one}, {"repo": "/tmp/repo-b", "url": two}],
         shepherd_ready={two: {"head": "f" * 40, "at": stamp(-timedelta(minutes=3))}})
    ready = {a["url"]: a["ready"] for a in show()["artifacts"]}
    assert ready == {one: False, two: True}


def test_a_host_task_s_sessions_are_read_on_its_host(attached, stubs, queue_dir, hosts):  # noqa: F811
    """The local list holds a different tree, so reading it would answer wrong."""
    child = "a" * 8 + S1[8:]
    session(stubs, S1)
    session(stubs, S2, parent=S1)
    edit(queue_dir, REF, host="devbox")
    write(hosts.state / "me@devbox.session-list.json", json.dumps([
        {"id": S1, "state": "working"}, {"id": child, "state": "working", "parent_session_id": S1},
    ]))
    doc = show()
    assert doc["live_sessions"] == [S1, child]


def test_the_text_view_is_unchanged_without_the_flag(attached):
    out = ok(q("show", REF)).stdout
    assert out.startswith(f"{REF} — ") and not out.lstrip().startswith("{")
