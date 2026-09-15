"""The shared topic, taken exactly as far as each test needs it and no further.

    topic  ->  blocked  ->  briefed  ->  attached  ->  first_landed
    (four     (03 waits    (every      (01 and 02    (01 merged,
     tasks)    on 01)       brief       have a        reaped, and
                            written)    session)      03 released)
"""

import os
from pathlib import Path

import pytest
from queuekit import (
    AGENT_CONF, AUTO_MERGE_CONF, HOSTS_TOML, PUBLISH_CONF, S1, S2, TASKS, TOPIC, TOPIC_PROMPT,
    TOPIC_TITLE, fill_brief, ok, result,
)

from harness import run_queue as q
from harness import stream_event, write


@pytest.fixture(autouse=True)
def settings(isolated_env) -> None:
    orchestration = isolated_env / "settings" / "orchestration"
    write(orchestration / "publish.conf", PUBLISH_CONF)
    write(orchestration / "agent.conf", AGENT_CONF)
    write(orchestration / "auto-merge.conf", AUTO_MERGE_CONF)
    write(isolated_env / "stubs" / "hosts.toml", HOSTS_TOML)


@pytest.fixture
def queue_dir(isolated_env) -> Path:
    return Path(os.environ["FLEET_QUEUE_DIR"])


@pytest.fixture
def topic() -> str:
    topic = ok(q("topic", "add", TOPIC, "--title", TOPIC_TITLE, "--prompt", TOPIC_PROMPT)).stdout.strip()
    for n, slug, title, repo, touches in TASKS:
        ok(q("add", topic, slug, "--title", title, "--repo", repo, "--branch", f"fix/{slug}",
             "--touches", touches, "--number", n))
    return topic


@pytest.fixture
def blocked(topic) -> str:
    ok(q("block", f"{topic}/03-render-detected-agent", "--on", f"{topic}/01-drop-idle-default",
         "--kind", "semantic-dependency", "--why", "reads the detected_agent field 01 introduces"))
    return topic


@pytest.fixture
def briefed(blocked, queue_dir) -> str:
    for n, slug, *_ in TASKS:
        fill_brief(queue_dir / blocked / f"{n}-{slug}" / "BRIEF.md")
    return blocked


@pytest.fixture
def attached(briefed, stubs) -> str:
    """01 and 02 went out. The stream is already at seq 100 when they attach."""
    stubs.stream(stream_event(100, S1, "working", event="present"),
                 stream_event(100, S2, "working", event="present"))
    ok(q("attach", f"{briefed}/01-drop-idle-default", S1))
    ok(q("attach", f"{briefed}/02-document-the-states", S2))
    return briefed


@pytest.fixture
def first_landed(attached, stubs, queue_dir) -> str:
    """01's pull request merged and its session was released; 02 is still working."""
    stubs.session_is(S1, "idle")
    stubs.session_is(S2, "working")
    stubs.pipeline_pr(999, "fix/drop-idle-default")
    result(queue_dir / attached / "01-drop-idle-default", "shipped",
           "Dropped the idle default; an unreported session now reads `unreported`.",
           "https://github.com/Thurbeen/thurbox/pull/999")
    ok(q("collect"))
    stubs.pr_state(999, "MERGED")
    ok(q("reap"))
    return attached
