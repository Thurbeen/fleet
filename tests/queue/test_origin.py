"""A task's `origin` and `reply`: two opaque strings, and what fleet does with each.

`--origin` says who owns the task's conversation and `--reply` is the command a
worker runs to speak to them. Fleet parses neither, so the values here are
illustrations of one convention and nothing in fleet's code knows them.
`find` is what another program asks, so its shape is held here.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from queuekit import ok

from harness import expect, refute, run_queue as q

ORIGIN = "slack:C123/1700000000.000100"
REPLY = "vega reply C123/1700000000.000100"


def add(topic: str, slug: str, *extra: str) -> str:
    return ok(q("add", topic, slug, "--title", slug, "--repo", "/tmp/repo-a", "--branch", f"fix/{slug}",
                "--publish", "none", *extra)).stdout.strip()


def topic() -> str:
    return ok(q("topic", "add", "asked", "--title", "Asked", "--prompt", "p")).stdout.strip()


def record(queue_dir: Path, ref: str) -> dict:
    return yaml.safe_load((queue_dir / ref / "task.yaml").read_text(encoding="utf-8"))


def mark(queue_dir: Path, ref: str, **fields) -> None:
    path = queue_dir / ref / "task.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc.update(fields)
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")


def find(origin: str) -> list:
    done = ok(q("find", "--origin", origin, "--json"))
    return json.loads(done.stdout)


def test_both_fields_are_stored_shown_and_checked(queue_dir):
    t = topic()
    ref = add(t, "owned", "--origin", f"  {ORIGIN} ", "--reply", REPLY)
    doc = record(queue_dir, ref)
    assert (doc["origin"], doc["reply"]) == (ORIGIN, REPLY)
    expect(ok(q("show", ref)).stdout, f"origin:      {ORIGIN}", f"reply:       {REPLY}")
    ok(q("check"))


def test_a_task_with_neither_is_unchanged(queue_dir):
    t = topic()
    ref = add(t, "plain")
    doc = record(queue_dir, ref)
    assert doc["origin"] is None
    assert doc["reply"] is None
    out = ok(q("show", ref)).stdout
    refute(out, "origin:", "reply:")
    refute((queue_dir / ref / "BRIEF.md").read_text(encoding="utf-8"), "Replying to the requester")


def test_an_old_record_without_the_keys_loads(queue_dir):
    t = topic()
    ref = add(t, "old")
    path = queue_dir / ref / "task.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc.pop("origin"), doc.pop("reply")
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    ok(q("show", ref))
    ok(q("check"))
    assert find(ORIGIN) == []


def test_check_refuses_an_origin_that_is_not_text(queue_dir):
    t = topic()
    ref = add(t, "bad")
    mark(queue_dir, ref, origin=["slack"])
    done = q("check")
    assert done.code != 0
    expect(done.stdout + done.stderr, f"{ref}: origin ['slack'] is not text")


def test_the_brief_section_appears_only_when_reply_is_set(queue_dir):
    t = topic()
    with_reply = add(t, "talks", "--origin", ORIGIN, "--reply", REPLY)
    origin_only = add(t, "silent", "--origin", ORIGIN)
    brief = (queue_dir / with_reply / "BRIEF.md").read_text(encoding="utf-8")
    expect(brief, "## Replying to the requester", f"`{REPLY} '<text>'`", "--file <path>",
           "your verdict, once `result.md` is written", "the pull request URL",
           "BEFORE you run `fleet queue declare ... needs-input`")
    refute((queue_dir / origin_only / "BRIEF.md").read_text(encoding="utf-8"), "Replying to the requester")


def test_find_is_a_stable_list_of_live_tasks_of_exactly_that_origin(queue_dir):
    t = topic()
    live = add(t, "live", "--origin", ORIGIN)
    remote = add(t, "remote", "--origin", ORIGIN)
    add(t, "unspawned", "--origin", ORIGIN)
    other = add(t, "other", "--origin", ORIGIN + "9")
    for ref, sid in ((live, "sess-live"), (other, "sess-other")):
        mark(queue_dir, ref, session=sid, state="dispatched")
    mark(queue_dir, remote, session="sess-remote", state="shipped", host="devbox")
    assert find(ORIGIN) == [
        {"ref": live, "state": "dispatched", "session": "sess-live", "host": None},
        {"ref": remote, "state": "shipped", "session": "sess-remote", "host": "devbox"},
    ]


def test_find_is_empty_on_nothing_and_skips_terminal_and_reaped_tasks(queue_dir):
    assert find(ORIGIN) == []
    t = topic()
    landed = add(t, "landed", "--origin", ORIGIN)
    gone = add(t, "gone", "--origin", ORIGIN)
    reaped = add(t, "reaped", "--origin", ORIGIN)
    mark(queue_dir, landed, session="s1", state="landed")
    mark(queue_dir, gone, session="s2", state="abandoned")
    mark(queue_dir, reaped, session="s3", state="shipped", reaped={"session": "s3", "how": "deleted"})
    done = q("find", "--origin", ORIGIN, "--json")
    assert done.code == 0
    assert json.loads(done.stdout) == []
