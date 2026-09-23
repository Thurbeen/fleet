"""A record a hand broke costs the reader one sentence, never the whole pass.

Every record is a file, and a file gets edited: a blocker entry typed without
its `task:` key, a task.yaml saved mid-edit, a result.md whose frontmatter a
worker mangled or wrote in the wrong encoding. None of those is fleet's own
doing, and all of them were answered the same way — a parser's traceback out
of `list`, `plan`, `check` or `collect`, exit 1, and everything else that
command would have done left undone. `collect` is the one that hurts: the
reconciler runs it every two minutes, and one worker's broken result.md ended
every pass before any other task's result was read.

So: the file is NAMED, in one sentence, and the rest of the queue goes on.
"""

from __future__ import annotations

import json

import yaml
from queuekit import ok, result

from harness import expect, refute, run_fleet, write
from harness import run_queue as q


def test_a_blocker_naming_neither_task_nor_condition_is_reported(topic, queue_dir):
    path = queue_dir / topic / "03-render-detected-agent" / "task.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["blocked_by"] = [{"kind": "semantic-dependency", "why": "the task key was lost in a hand edit",
                          "recorded": "2026-09-23T09:00:00+00:00"}]
    write(path, yaml.safe_dump(doc, sort_keys=False))

    # Every reader draws the task, says the entry holds it and can never clear.
    out = ok(q("list")).out
    refute(out, "Traceback")
    expect(out, "03-render-detected-agent", "UNCLEARABLE", "neither a task nor a condition")
    plan = ok(q("plan", "--json"))
    assert f"{topic}/03-render-detected-agent" not in json.loads(plan.stdout)["ready"]
    expect(ok(q("plan")).out, "03-render-detected-agent", "UNCLEARABLE")
    expect(ok(q("show", f"{topic}/03-render-detected-agent")).out, "UNCLEARABLE")

    # And `check` is where the entry is named as the defect it is.
    check = q("check")
    assert check.code != 0, check.out
    expect(check.out, "03-render-detected-agent", "does not exist")


def test_a_record_that_is_not_yaml_is_named_and_not_a_traceback(topic, queue_dir):
    path = queue_dir / topic / "02-document-the-states" / "task.yaml"
    write(path, "id: 02-document-the-states\nstate: [broken\n")

    for verb in (["list"], ["plan"], ["check"], ["show", f"{topic}/01-drop-idle-default"]):
        run_ = q(*verb)
        assert run_.code == 2, f"{verb}: exit {run_.code}\n{run_.out}"
        refute(run_.out, "Traceback")
        expect(run_.out, "queue:", "02-document-the-states", "task.yaml")

    # The status screen loses its queue section and nothing else.
    status = run_fleet("status").out
    refute(status, "Traceback")
    expect(status, "QUEUE", "unavailable", "02-document-the-states", "CHECKOUT")
    expect(run_fleet("status", "--records").out, "02-document-the-states", "task.yaml")


def test_a_result_that_cannot_be_read_holds_its_own_task_and_no_other(topic, queue_dir):
    tasks = queue_dir / topic
    write(tasks / "01-drop-idle-default" / "result.md",
          "---\noutcome: [unclosed\n---\nA frontmatter a hand mangled.\n")
    # Frontmatter that reads, and a body a Windows console wrote in its own code page.
    (tasks / "02-document-the-states" / "result.md").write_bytes(
        b"---\noutcome: not-applicable\n---\nbytes \xff\xfe that are not UTF-8\n"
    )
    result(tasks / "03-render-detected-agent", "not-applicable", "Nothing to do here after all.")

    out = q("collect")
    assert out.code == 0, out.out
    refute(out.out, "Traceback")
    expect(out.out, "01-drop-idle-default", "frontmatter is not YAML",
           "02-document-the-states  not-applicable", "03-render-detected-agent  not-applicable",
           "collect: 2 result(s) read")
    expect(q("show", f"{topic}/01-drop-idle-default").out, "state:       queued")
    # Concluded, and landed on the same pass: nothing was produced, so there is nothing to wait for.
    expect(q("show", f"{topic}/02-document-the-states").out, "state:       landed")
