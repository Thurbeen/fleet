"""The queue probe: what the pane reads, in the one line-per-record format it parses.

The pane runs it through `sh -c` on POSIX and `cmd /C` on Windows, so it is
Python and not a shell script. It spells every outcome it can tell apart on
stdout and exits 0, because the pane reads only a probe that could not RUN, or
said nothing, as a failure.

    R <root>   E <what went wrong>   A <archived topics>   T <slug> <title>
    K <id> <state> <title> <outcome> <artifact> <blockers> <brief> <events>
      <result> <branch> <moved-at> <publish-method> <publish-state> <publish-at>
"""

from __future__ import annotations

import os
from pathlib import Path

from harness import PYTHON, REPO, run, write
from harness import run_queue as q

PROBE = REPO / "scripts" / "lib" / "pane_probe.py"


def ok(done):
    assert done.code == 0, done.out
    return done


def probe(**env) -> list[list[str]]:
    done = ok(run([*PYTHON, str(PROBE)], **env))
    return [line.split("\t") for line in done.stdout.splitlines()]


def test_every_live_task_is_one_record_and_an_archived_topic_only_a_count(tmp_path):
    topic = ok(q("topic", "add", "probe", "--title", "Probe the queue", "--prompt", "p")).stdout.strip()
    for n, slug in (("01", "first"), ("02", "second")):
        ok(q("add", topic, slug, "--title", f"The {slug} task", "--repo", str(tmp_path / "repo"),
             "--branch", f"fix/{slug}", "--touches", slug, "--number", n))
    ok(q("block", f"{topic}/02-second", "--on", f"{topic}/01-first", "--kind", "semantic-dependency", "--why", "w"))
    ok(q("block", f"{topic}/02-second", "--condition", "az login: for, the|tenant",
         "--kind", "missing-credential", "--why", "w"))
    root = Path(os.environ["FLEET_QUEUE_DIR"])
    first = root / topic / "01-first"
    write(first / "progress.jsonl", "{}\n{}\n")
    write(first / "result.md", "---\noutcome: shipped\n---\ndone\n")

    done = ok(q("topic", "add", "old", "--title", "Finished long ago", "--prompt", "p")).stdout.strip()
    with open(root / done / "topic.yaml", "a", encoding="utf-8") as fh:
        fh.write("archived: '2026-09-01T00:00:00+00:00'\n")

    records = probe()
    assert records[0] == ["R", str(root)]
    assert ["T", topic, "Probe the queue"] in records
    assert not any(r[0] == "T" and r[1] == done for r in records)
    assert records[-1] == ["A", "1"]

    tasks = {r[1]: r for r in records if r[0] == "K"}
    assert all(len(r) == 15 for r in tasks.values())
    one = tasks["01-first"]
    assert one[2:4] == ["queued", "The first task"]
    assert one[6:10] == ["", "1", "2", "1"]
    assert one[10] == "fix/first"
    assert int(one[11]) > 0
    two = tasks["02-second"]
    assert two[6] == f"{topic}/01-first|semantic-dependency,!az login: for  the tenant|missing-credential"
    assert two[8:10] == ["0", "0"]


def test_a_queue_that_is_not_there_is_an_E_record_and_exit_0(tmp_path):
    assert probe(FLEET_QUEUE_DIR=str(tmp_path / "nowhere"))[0][0] == "E"
