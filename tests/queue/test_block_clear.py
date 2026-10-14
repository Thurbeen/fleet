"""#260: `block --clear --on` releases a blocker whose task record is gone.

A blocker naming a task whose directory was deleted or renamed never clears by
itself — `blocker_cleared` reads an unresolvable ref as "not landed" — so
`--clear` naming it back is the only release. It used to resolve `--on` first
and refuse with `no such task`, which left hand-editing `task.yaml` as the only
way out.
"""

import shutil

from harness import expect, refute
from harness import run_queue as q

DEPENDANT = "03-render-detected-agent"
UPSTREAM = "01-drop-idle-default"


def test_a_blocker_on_a_vanished_task_is_cleared_by_naming_it_back(blocked, queue_dir):
    shutil.rmtree(queue_dir / blocked / UPSTREAM)
    expect(q("plan").out, "waiting: 1")

    r = q("block", f"{blocked}/{DEPENDANT}", "--clear", "--on", f"{blocked}/{UPSTREAM}")
    assert r.code == 0, r.out
    expect(r.out, "1 blocker(s) cleared")

    plan = q("plan").out
    expect(plan, DEPENDANT, "waiting: 0")
    refute(plan, "reads the detected_agent field")


def test_adding_a_blocker_on_a_task_that_does_not_exist_is_still_refused(topic):
    r = q("block", f"{topic}/{DEPENDANT}", "--on", f"{topic}/99-never-was",
          "--kind", "semantic-dependency", "--why", "a typo")
    assert r.code != 0, r.out
    expect(r.out, "no such task")
