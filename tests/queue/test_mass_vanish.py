"""An outage is not a mass ending: thurbox losing many sessions at once retires nothing.

`collect` retires a dispatched task as `session gone` once thurbox has not
listed its session for ten minutes. That is right for one worker whose
multiplexer died, and wrong for a thurbox restart, a multiplexer crash or an
unreachable machine: the list still READS cleanly, only without most of the
sessions, and ten minutes later every task on that machine would end in words
that claim each one was gone. So a look in which at least MASS_VANISH_MIN
tracked sessions are missing, and they are more than half of that machine's,
retires none and names the count for a person to look at.
"""

import os
from pathlib import Path

import pytest
import yaml
from queuekit import ok

from harness import expect, refute, run_queue_batch
from harness import run_queue as q

TOPIC = "survive-an-outage"
SIDS = [f"{n}{n}{n}{n}{n}{n}{n}{n}-aaaa-4aaa-8aaa-{n * 12}" for n in "123456"]
REMOTE = [f"{n}{n}{n}{n}{n}{n}{n}{n}-bbbb-4bbb-8bbb-{n * 12}" for n in "789abc"]


def record(queue_dir: Path, ref: str) -> dict:
    return yaml.safe_load((queue_dir / ref / "task.yaml").read_text(encoding="utf-8"))


def backdate(queue_dir: Path, ref: str) -> None:
    path = queue_dir / ref / "task.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["vanished"]["since"] = "2020-01-01T00:00:00+00:00"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def gone(stubs, sid: str) -> None:
    os.remove(stubs.root / "sessions" / f"{sid}.json")


def dispatch_six(stubs, sids: list[str], host: str | None = None, first: int = 1) -> list[str]:
    """Six dispatched tasks, each attached to its own live session."""
    commands, refs = [], []
    for i, sid in enumerate(sids, start=first):
        slug = f"task-{i:02d}"
        extra = ["--host", host, "--repo", "/srv/code/app"] if host else ["--repo", "/tmp/repo-a"]
        commands.append(["add", TOPIC, slug, "--title", f"Task {i}", *extra,
                         "--branch", f"fix/{slug}", "--number", f"{i:02d}"])
        commands.append(["attach", f"{TOPIC}/{i:02d}-{slug}", sid])
        refs.append(f"{TOPIC}/{i:02d}-{slug}")
        stubs.session_is(sid, "working")
    ok(run_queue_batch(commands))
    return refs


@pytest.fixture
def six(stubs) -> list[str]:
    ok(q("topic", "add", TOPIC, "--title", "Survive an outage", "--prompt", "do not retire on a restart"))
    return dispatch_six(stubs, SIDS)


def look_twice(queue_dir: Path, refs: list[str]) -> str:
    """A first look, then a second one past the grace for every task that was marked."""
    ok(q("collect"))
    for ref in refs:
        if "vanished" in record(queue_dir, ref):
            backdate(queue_dir, ref)
    return ok(q("collect")).out


def test_five_of_six_gone_at_once_retires_none_and_says_so(six, stubs, queue_dir):
    for sid in SIDS[1:]:
        gone(stubs, sid)

    out = look_twice(queue_dir, six)
    expect(out, "5 of 6", "a person should look")
    refute(out, "session gone", "abandoned")
    for ref in six:
        assert record(queue_dir, ref)["state"] == "dispatched"


def test_one_of_six_gone_is_retired_as_before(six, stubs, queue_dir):
    gone(stubs, SIDS[0])

    out = look_twice(queue_dir, six)
    expect(out, "session gone")
    refute(out, "a person should look")
    doc = record(queue_dir, six[0])
    assert doc["state"] == "abandoned"
    assert doc["abandoned"]["how"] == "session-gone"
    for ref in six[1:]:
        assert record(queue_dir, ref)["state"] == "dispatched"


def test_the_outage_clears_and_nothing_was_retired(six, stubs, queue_dir):
    for sid in SIDS[1:]:
        gone(stubs, sid)
    look_twice(queue_dir, six)

    for sid in SIDS:
        stubs.session_is(sid, "working")
    out = ok(q("collect")).out
    refute(out, "session gone", "a person should look", "not listed")
    for ref in six:
        doc = record(queue_dir, ref)
        assert doc["state"] == "dispatched"
        assert "vanished" not in doc, "an outage starts no clock"


def test_an_outage_is_counted_per_machine(six, stubs, queue_dir):
    """devbox losing all six of its sessions holds devbox's tasks; the one local
    session that is really gone still ends, because this machine had no outage."""
    remote = dispatch_six(stubs, REMOTE, host="devbox", first=7)
    for sid in REMOTE:
        gone(stubs, sid)
    gone(stubs, SIDS[0])

    out = look_twice(queue_dir, six + remote)
    expect(out, "devbox", "6 of 6", "a person should look")
    assert record(queue_dir, six[0])["state"] == "abandoned"
    for ref in six[1:] + remote:
        assert record(queue_dir, ref)["state"] == "dispatched"
