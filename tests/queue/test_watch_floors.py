"""Claim 15: no transition is lost between watch runs.

The bug this proves gone: 19 of 20 live tasks had an EMPTY progress.jsonl while
the stream still held their transitions. `watch` kept one queue-wide `.cursor`
and advanced it over every event it read, folded or not, while deciding what
to fold from a map of sessions snapshotted before the stream was opened:

  (a) a task dispatched WHILE a watch was streaming was not in that map, so
      its transitions were skipped and the shared cursor written past them;
  (b) a watch that died part-way through a batch had written no cursor, so
      the next one replayed and re-appended what it had already folded.

The floor is now per task, derived from the task's OWN progress.jsonl, so a
task advances only over the events it folded, and a crash costs neither a skip
nor a duplicate. The stream is a recorded file behind `FLEET_QUEUE_WATCH_CMD`,
an argv list split with shell quoting and handed to no shell.
"""

import json
import shlex
import sys
from pathlib import Path

import pytest
import yaml
from queuekit import ok

from harness import REPO, expect, stream_event, write
from harness import run_queue as q

SA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SB = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
SC = "cccccccc-cccc-cccc-cccc-cccccccccccc"

REPLAY = "import sys\nsys.stdout.write(open(sys.argv[1], encoding='utf-8').read())\n"


class Stream:
    def __init__(self, where: Path):
        self.where = where
        self.seed = where / "seed.jsonl"
        self.events = where / "events.jsonl"
        self.replay = where / "replay.py"
        write(self.seed, "")
        write(self.events, "")
        write(self.replay, REPLAY)

    def command(self, path: Path) -> str:
        return shlex.join([sys.executable, str(self.replay), str(path)])

    def add(self, *events: tuple[int, str, str]) -> None:
        with open(self.events, "a", encoding="utf-8", newline="\n") as fh:
            fh.writelines(json.dumps(stream_event(seq, session, state, at=1788793000000)) + "\n"
                          for seq, session, state in events)


@pytest.fixture
def stream(tmp_path, monkeypatch) -> Stream:
    s = Stream(tmp_path / "capture")
    monkeypatch.setenv("FLEET_QUEUE_WATCH_CMD", s.command(s.seed))
    return s


@pytest.fixture
def ctopic(stream) -> str:
    topic = ok(q("topic", "add", "capture-every-transition",
                 "--prompt", "prove no transition is lost between watch runs")).stdout.strip()
    ok(q("add", topic, "task-a", "--title", "task a", "--repo", "/tmp/repo-a", "--branch", "fix/a", "--number", "01"))
    ok(q("add", topic, "task-b", "--title", "task b", "--repo", "/tmp/repo-b", "--branch", "fix/b", "--number", "02"))
    ok(q("attach", f"{topic}/01-task-a", SA))
    return topic


def held(queue_dir: Path, topic: str, ref: str) -> list[int] | str:
    """The sequence numbers a record holds, in order: a SKIP and a DUPLICATE both show."""
    path = queue_dir / topic / ref / "progress.jsonl"
    if not path.exists():
        return "<missing>"
    return [json.loads(line)["seq"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def watch(stream: Stream, path: Path | None = None, **env):
    return q("watch", "--for-secs", "0", FLEET_QUEUE_WATCH_CMD=stream.command(path or stream.events), **env)


@pytest.fixture
def mid_window(ctopic, stream, queue_dir) -> str:
    """(a) One batch, two tasks, and the second dispatched INSIDE the watch window.

    The stream command itself does that attach: the only honest way to write
    "a dispatch landed while the stream was open". The nested attach reads the
    seed stream, so task b's floor starts where its session did.
    """
    stream.add((101, SA, "working"), (102, SB, "idle"), (103, SA, "working"))
    script = stream.where / "attach_then_read.py"
    write(script, (
        "import os, subprocess, sys\n"
        f"env = dict(os.environ, FLEET_QUEUE_WATCH_CMD={stream.command(stream.seed)!r})\n"
        f"subprocess.run([sys.executable, {str(REPO / 'scripts' / 'lib' / 'queue.py')!r}, 'attach',"
        f" {ctopic + '/02-task-b'!r}, {SB!r}], env=env, capture_output=True)\n"
        f"sys.stdout.write(open({str(stream.events)!r}, encoding='utf-8').read())\n"
    ))
    q("watch", "--for-secs", "0", FLEET_QUEUE_WATCH_CMD=shlex.join([sys.executable, str(script)]))
    watch(stream)
    return ctopic


def test_a_task_dispatched_mid_window_keeps_its_transitions(mid_window, queue_dir):
    # A batch carrying two tasks leaves the first one complete...
    assert held(queue_dir, mid_window, "01-task-a") == [101, 103]
    # ...and the one dispatched mid-window keeps its transition too.
    assert held(queue_dir, mid_window, "02-task-b") == [102]


def test_events_that_arrive_with_no_watch_running_are_folded_by_the_next(mid_window, stream, queue_dir):
    stream.add((104, SA, "done"), (105, SB, "done"))
    watch(stream)
    assert held(queue_dir, mid_window, "01-task-a") == [101, 103, 104]
    # For every task in the gap, not just the first.
    assert held(queue_dir, mid_window, "02-task-b") == [102, 105]


def test_an_interrupted_watch_skips_nothing_and_replays_nothing(mid_window, stream, queue_dir, tmp_path):
    """task b's record is replaced by a DIRECTORY, so the append fails for real —
    no permission trick that a run as root would sail straight through."""
    stream.add((104, SA, "done"), (105, SB, "done"))
    watch(stream)
    stream.add((106, SA, "working"), (107, SB, "working"), (108, SA, "idle"))

    progress = queue_dir / mid_window / "02-task-b" / "progress.jsonl"
    saved = tmp_path / "b-progress.saved"
    progress.rename(saved)
    progress.mkdir()
    stopped = watch(stream)
    assert stopped.code != 0, "a watch that cannot write a record stops instead of walking past it\n" + stopped.out
    expect(stopped.out, "02-task-b")
    progress.rmdir()
    saved.rename(progress)

    watch(stream)
    assert held(queue_dir, mid_window, "01-task-a") == [101, 103, 104, 106, 108]
    assert held(queue_dir, mid_window, "02-task-b") == [102, 105, 107]


def test_a_record_from_before_per_task_floors_resumes_at_the_retired_cursor(mid_window, stream, queue_dir):
    """The legacy value is 109 and an event at 109 is offered with it: a fallback
    of 0 would fold that one too, and a fallback of "now" would fold neither."""
    ok(q("add", mid_window, "task-c", "--title", "task c", "--repo", "/tmp/repo-c", "--branch", "fix/c",
         "--number", "03"))
    ok(q("attach", f"{mid_window}/03-task-c", SC))
    record = queue_dir / mid_window / "03-task-c" / "task.yaml"
    text = record.read_text(encoding="utf-8")
    assert yaml.safe_load(text).get("watch_from") is not None, text
    write(record, "".join(line for line in text.splitlines(keepends=True) if not line.startswith("watch_from:")))
    write(queue_dir / ".cursor", "109\n")
    stream.add((109, SC, "idle"), (110, SC, "working"))

    watch(stream)
    assert held(queue_dir, mid_window, "03-task-c") == [110]
