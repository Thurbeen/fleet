"""`prompted` follows every delivery, so a bare `prompt` never re-briefs a working agent.

The bare `fleet queue prompt` sweep picks every `dispatched` task whose record
says `prompted: false` and types its ORIGINAL brief in. Only the dispatch
handoff used to write `prompted: true`: `attach` reset it to false even onto a
session already deep in its work, and `send` never set it, so the next bare
sweep typed "Read …/BRIEF.md and do what it says." into a working agent.

And the other end: a dispatched task nobody ever prompted is not at rest, so
`plan`'s stalled set never named it and no standing surface did. `plan` now
names it `unprompted` once it has had a short grace to be prompted in.
"""

import json
from datetime import datetime, timedelta, timezone

import yaml
import pytest
from queuekit import ok

from harness import expect, refute, write
from harness import run_queue as q

WORKING = "dddddddd-0000-0000-0000-000000000001"
FRESH = "dddddddd-0000-0000-0000-000000000002"


def task_doc(queue_dir, ref: str) -> dict:
    return yaml.safe_load((queue_dir / ref / "task.yaml").read_text(encoding="utf-8"))


def add_task(slug: str, number: str) -> str:
    topic = "prompted-follows"
    q("topic", "add", topic, "--title", "Prompted follows delivery", "--prompt", "deliver once")
    ok(q("add", topic, slug, "--title", f"Task {slug}", "--repo", "/tmp/repo-a",
         "--branch", f"fix/{slug}", "--number", number))
    return f"{topic}/{number}-{slug}"


def sends(stubs) -> str:
    return "\n".join(stubs.calls("thurbox-cli", "session send"))


def test_attaching_a_working_session_records_it_prompted(stubs, queue_dir):
    ref = add_task("adopted", "01")
    stubs.session_is(WORKING, "working")
    ok(q("attach", ref, WORKING))

    out = q("prompt").out
    assert sends(stubs) == "", "a bare prompt re-sent the brief into a working agent"
    expect(out, "already been prompted")
    assert task_doc(queue_dir, ref)["prompted"] is True


def test_attaching_a_session_with_no_agent_report_stays_unprompted(stubs, queue_dir):
    ref = add_task("fresh", "01")
    ok(q("attach", ref, FRESH))
    assert task_doc(queue_dir, ref)["prompted"] is False


def test_attaching_a_fresh_idle_session_delivers_the_brief(stubs, queue_dir):
    ref = add_task("fresh-idle", "01")
    stubs.session_is(FRESH, "idle")
    ok(q("attach", ref, FRESH))
    ok(q("prompt"))
    expect(sends(stubs), FRESH, "BRIEF.md")
    assert task_doc(queue_dir, ref)["prompted"] is True


@pytest.mark.parametrize("state", ["idle", "unreported"])
def test_prompted_preserves_an_already_briefed_session(stubs, queue_dir, state):
    ref = add_task("briefed", "01")
    stubs.session_is(FRESH, state)
    ok(q("attach", ref, FRESH, "--prompted"))
    ok(q("prompt"))
    assert sends(stubs) == ""
    assert task_doc(queue_dir, ref)["prompted"] is True


def test_unprompted_overrides_a_live_state(stubs, queue_dir):
    ref = add_task("hand-spawned", "01")
    stubs.session_is(WORKING, "idle")
    ok(q("attach", ref, WORKING, "--unprompted"))
    assert task_doc(queue_dir, ref)["prompted"] is False


def test_a_delivered_send_marks_the_task_prompted(stubs, queue_dir):
    ref = add_task("messaged", "01")
    ok(q("attach", ref, FRESH))
    stubs.session_is(FRESH, "idle")
    ok(q("send", ref, "Read the brief in the task directory."))
    assert task_doc(queue_dir, ref)["prompted"] is True

    before = len(stubs.calls("thurbox-cli", "session send"))
    q("prompt")
    assert len(stubs.calls("thurbox-cli", "session send")) == before, "a bare prompt re-briefed it"


def unprompted() -> list:
    return json.loads(q("plan", "--json").stdout).get("unprompted", [])


def age_dispatch(queue_dir, ref: str, minutes: int) -> None:
    path = queue_dir / ref / "task.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["dispatched_at"] = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    write(path, yaml.safe_dump(doc))


def test_a_dispatched_task_never_prompted_is_named_by_plan(stubs, queue_dir):
    ref = add_task("never-briefed", "01")
    ok(q("attach", ref, FRESH))
    assert unprompted() == [], "inside the grace a dispatch is still being prompted"

    age_dispatch(queue_dir, ref, 30)
    rows = unprompted()
    assert [r["task"] for r in rows] == [ref]
    expect(q("plan").out, "unprompted: 1", ref, f"fleet queue prompt {ref}")

    stubs.session_is(FRESH, "idle")
    ok(q("send", ref, "Read the brief."))
    assert unprompted() == []
    refute(q("plan").out, "unprompted:")
