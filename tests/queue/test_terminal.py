"""The two ends nobody declares: a change request closed unmerged, and a session that vanished.

On one fleet about eleven tasks were force-abandoned by hand after their pull
requests were closed, and about eight more after their session was gone. Both
facts were on the forge and in thurbox all along; the queue just never read
them as an end. `collect` now does, and retires each task as `abandoned` in
ITS OWN WORDS — `closed unmerged`, `session gone` — so the record never claims
a person gave up on work the forge or the machine ended.
"""

import yaml
from queuekit import S1, S2, deletions, ok, result

from harness import expect, refute
from harness import run_queue as q

PR = "https://github.com/Thurbeen/thurbox/pull/999"


def record(queue_dir, ref: str) -> dict:
    return yaml.safe_load((queue_dir / ref / "task.yaml").read_text(encoding="utf-8"))


def backdate(queue_dir, ref: str, key: str, field: str, stamp: str) -> None:
    path = queue_dir / ref / "task.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc[key][field] = stamp
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def test_a_shipped_result_whose_pull_request_was_closed_unmerged_ends_the_task(attached, stubs, queue_dir):
    ref = f"{attached}/01-drop-idle-default"
    stubs.session_is(S1, "idle")
    stubs.session_is(S2, "working")
    stubs.pipeline_pr(999, "fix/drop-idle-default")
    stubs.pr_state(999, "CLOSED")
    result(queue_dir / ref, "shipped", "Opened it.", PR)

    out = ok(q("collect")).out
    expect(out, "01-drop-idle-default", "closed unmerged")
    refute(out, "NOT CLOSED")
    doc = record(queue_dir, ref)
    assert doc["state"] == "abandoned"
    assert doc["abandoned"]["how"] == "closed-unmerged"
    # Its session is released like any other terminal task's.
    expect(deletions(stubs), S1)

    listed = q("list").out
    expect(listed, "closed unmerged")
    refute(listed, "abandoned by hand", "disagrees")


def test_a_done_task_whose_pull_request_closes_later_says_so_in_its_own_words(attached, stubs, queue_dir):
    ref = f"{attached}/01-drop-idle-default"
    stubs.session_is(S1, "idle")
    stubs.session_is(S2, "working")
    stubs.pipeline_pr(999, "fix/drop-idle-default")
    result(queue_dir / ref, "shipped", "Opened it.", PR)
    ok(q("collect"))
    assert record(queue_dir, ref)["state"] == "done"

    stubs.pr_state(999, "CLOSED")
    ok(q("reap"))
    doc = record(queue_dir, ref)
    assert doc["state"] == "abandoned"
    assert doc["abandoned"]["how"] == "closed-unmerged"
    listed = q("list").out
    expect(listed, "closed unmerged")
    refute(listed, "abandoned by hand", "disagrees")


def test_a_dispatched_task_whose_session_vanished_ends_after_a_second_look(attached, stubs, queue_dir):
    ref = f"{attached}/01-drop-idle-default"
    # 02's worker is alive; 01's session is nowhere in thurbox's list.
    stubs.session_is(S2, "working")

    out = ok(q("collect")).out
    expect(out, "01-drop-idle-default", "not listed")
    doc = record(queue_dir, ref)
    # One look is not an end: a listing hiccup must not retire live work.
    assert doc["state"] == "dispatched"
    assert doc["vanished"]["session"] == S1

    backdate(queue_dir, ref, "vanished", "since", "2020-01-01T00:00:00+00:00")
    out = ok(q("collect")).out
    expect(out, "01-drop-idle-default", "session gone")
    doc = record(queue_dir, ref)
    assert doc["state"] == "abandoned"
    assert doc["abandoned"]["how"] == "session-gone"
    assert doc["session"] is None, "the id that no longer resolves is dropped"
    assert doc["reaped"]["session"] == S1
    assert record(queue_dir, f"{attached}/02-document-the-states")["state"] == "dispatched"

    listed = q("list").out
    expect(listed, "session gone")
    refute(listed, "abandoned by hand")


def test_a_session_that_comes_back_clears_the_first_look(attached, stubs, queue_dir):
    ref = f"{attached}/01-drop-idle-default"
    stubs.session_is(S2, "working")
    ok(q("collect"))
    assert "vanished" in record(queue_dir, ref)

    stubs.session_is(S1, "working")
    ok(q("collect"))
    doc = record(queue_dir, ref)
    assert doc["state"] == "dispatched"
    assert "vanished" not in doc


def test_a_vanished_session_with_a_result_waiting_is_collects_and_not_retired(attached, stubs, queue_dir):
    ref = f"{attached}/01-drop-idle-default"
    stubs.session_is(S2, "working")
    stubs.pipeline_pr(999, "fix/drop-idle-default")
    result(queue_dir / ref, "shipped", "Opened it.", PR)
    ok(q("collect"))
    assert record(queue_dir, ref)["state"] == "done"


def test_thurbox_that_cannot_be_asked_retires_nothing(attached, stubs, queue_dir):
    ref = f"{attached}/01-drop-idle-default"
    stubs.tool("thurbox-cli", "import sys; sys.exit(1)")
    ok(q("collect"))
    doc = record(queue_dir, ref)
    assert doc["state"] == "dispatched"
    assert "vanished" not in doc


def test_abandoning_by_hand_still_says_by_hand(attached, stubs, queue_dir):
    ref = f"{attached}/02-document-the-states"
    stubs.session_is(S2, "idle")
    ok(q("abandon", ref, "--why", "superseded", "--force"))
    assert record(queue_dir, ref)["abandoned"]["how"] == "hand"
    expect(q("list").out, "abandoned by hand: superseded")
