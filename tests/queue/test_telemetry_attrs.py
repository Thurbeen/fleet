"""A worker's session carries its task into thurbox's own telemetry — where thurbox can take it.

thurbox labels what an agent exports with the session (`thurbox.session.id`),
and a cost or a token count joins a task only if the session also says which
task it serves. `--env OTEL_RESOURCE_ATTRIBUTES=…` would say it by replacing
whatever the operator set for thurbox itself, so fleet waits for a field of
thurbox's own: it passes `--telemetry-attr` when `session create --help` lists
it, and nothing otherwise, so a thurbox without it spawns exactly as before.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from kit_dispatch import ANSWERING_KEYS, next_session
from queuekit import fill_brief, ok

from harness import refute, write
from harness import run_queue as q

SID = "dddddddd-0000-0000-0000-0000000000a1"


@pytest.fixture
def ready(stubs) -> str:
    topic = ok(q("topic", "add", "tag-sessions", "--title", "Tag sessions", "--prompt", "p")).stdout.strip()
    ok(q("add", topic, "one", "--title", "One", "--repo", "/tmp/repo-a", "--branch", "fix/one", "--number", "01"))
    fill_brief(Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "01-one" / "BRIEF.md")
    next_session(stubs, SID)
    stubs.session_is(SID, "idle")
    stubs.tool("thurbox-cli", ANSWERING_KEYS)
    return topic


def creates(stubs) -> list[str]:
    return [c for c in stubs.calls("thurbox-cli", "session create") if "--repo-path" in c]


def test_a_thurbox_that_takes_telemetry_attributes_gets_the_task(ready, stubs):
    write(stubs.root / "help" / "session-create.txt",
          "Options:\n      --telemetry-attr <KEY=VALUE>  an OpenTelemetry resource attribute\n")
    ok(q("dispatch"))
    create = creates(stubs)[0]
    assert (f"--telemetry-attr factory.task.id={ready}/01-one "
            f"--telemetry-attr factory.topic={ready}") in create, create


def test_a_thurbox_without_the_field_spawns_exactly_as_before(ready, stubs):
    ok(q("dispatch"))
    create = creates(stubs)[0]
    refute(create, "--telemetry-attr", "OTEL_RESOURCE_ATTRIBUTES")


def test_a_remote_spawn_carries_no_telemetry_attributes(monkeypatch):
    """A `--host` spawn runs that host's thurbox, which this one's `--help` says nothing about."""
    from harness import lib

    queue = lib("queue.py")
    monkeypatch.setitem(queue._THURBOX_TAKES, queue.TELEMETRY_ATTR_FLAG, True)
    here = queue.Task("t", "01-a", "/nowhere", {"host": None})
    there = queue.Task("t", "01-a", "/nowhere", {"host": "devbox"})
    assert queue.telemetry_attr_flags(here)
    assert queue.telemetry_attr_flags(there) == []
