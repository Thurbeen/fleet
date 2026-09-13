"""Claims 1, 2, 5 and 6: what goes out, what waits, and what may hold it.

1. Independent work dispatches AT ONCE, with no concurrency cap.
2. File overlap does NOT serialize: it is reported as a risk and not a reason.
5. A blocker with no category and no reason is refused, so "these touch the
   same file" cannot be smuggled in as a dependency.
6. A task whose BRIEF.md was never written does not go out — and the brief it
   refuses is a SKELETON, whose every section starts unwritten, so a
   half-written brief is refused too and not just a blank one.
"""

import json

from queuekit import TOPIC, TOPIC_PROMPT, TOPIC_TITLE

from harness import expect, refute
from harness import run_queue as q


def test_topic_add_returns_a_topic_id_and_only_that():
    # stdout is the VALUE and stderr is the note, so a caller can capture the id.
    added = q("topic", "add", TOPIC, "--title", TOPIC_TITLE, "--prompt", TOPIC_PROMPT)
    assert added.code == 0, added.out
    assert added.stdout.strip() == TOPIC, added.out


def test_a_blocker_with_no_reason_is_refused(topic):
    r = q("block", f"{topic}/03-render-detected-agent", "--on", f"{topic}/01-drop-idle-default")
    assert r.code != 0, r.out
    expect(r.out, "--kind")


def test_file_overlap_cannot_be_spelled_as_a_blocker_kind(topic):
    r = q("block", f"{topic}/04-log-state-changes", "--on", f"{topic}/01-drop-idle-default",
          "--kind", "file-overlap", "--why", "both edit src/state.rs")
    assert r.code != 0, r.out
    # And the refusal says where overlap belongs instead.
    expect(r.out, "semantic-dependency", "--touches")


def test_a_blocker_that_closes_a_cycle_is_refused(blocked):
    r = q("block", f"{blocked}/01-drop-idle-default", "--on", f"{blocked}/03-render-detected-agent",
          "--kind", "semantic-dependency", "--why", "closes a loop")
    assert r.code != 0, r.out
    expect(r.out, "cycle")


def test_the_first_plan_readies_every_independent_task_despite_the_overlap(blocked):
    plan = q("plan").out
    expect(
        plan,
        "01-drop-idle-default", "02-document-the-states", "04-log-state-changes", "ready: 3",
        # 01 and 04 are reported as an overlap risk, which refuses to be a reason to wait.
        "src/state.rs", "not a reason to wait",
        # 03 waits, and its blocker is durable and stated.
        "waiting: 1", "reads the detected_agent field",
    )
    assert len(json.loads(q("plan", "--json").stdout)["ready"]) == 3


def test_the_scaffold_emits_every_section_unwritten(blocked, queue_dir):
    raw = (queue_dir / blocked / "01-drop-idle-default" / "BRIEF.md").read_text(encoding="utf-8")
    expect(raw, "## What to do", "## Hard constraints", "## Coordination", "## Done means")
    assert raw.count("WRITE THE INSTRUCTIONS HERE") == 4, raw


def test_a_task_with_an_unwritten_brief_does_not_go_out(blocked):
    r = q("dispatch", "--dry-run")
    assert r.code != 0, r.out
    expect(r.out, "BRIEF.md")


def test_one_dispatch_launches_the_whole_ready_set(briefed):
    out = q("dispatch", "--dry-run").out
    spawns = [line for line in out.splitlines() if "session create" in line]
    assert len(spawns) == 3, out
    refute(out, "03-render-detected-agent")
    expect(
        out,
        "no concurrency cap",
        # Each worker is pointed at its own brief and nothing else.
        "BRIEF.md and do what it says",
        # Every spawn answers the trust dialog, and before it prompts.
        "session-trust.sh", "trust dialog first",
    )
