"""Claim 14: a finished topic archives itself, and leaves every default view.

The queue reached 24 topics with 27 of its 30 tasks `landed`, and the two
topics with live work were buried under twenty-two finished ones. Archiving is
a FLAG and a FILTER — nothing is moved, deleted or rewritten, because the queue
is gitignored and nothing backs it up.

One predicate underneath: a topic is archivable when every task is `landed` or
`abandoned`. `stuck` and `failed` are the worker's own verdicts, whose sessions
are kept as evidence, so a topic holding either stays in front of the operator.
The automatic sweep, the manual command and the `add` clear all ask the same
question, and every reader answers it out of the topic file alone.
"""


import os
from pathlib import Path

import pytest
from kit_display import set_state_line
from queuekit import deletions, ok, result

from harness import PYTHON, REPO, expect, refute, run, run_fleet, write
from harness import run_queue as q


def landed_task(stubs, queue_dir, topic: str, number: str, slug: str, pr: int) -> None:
    """A task that really landed, by the only path that produces one: the worker's
    result, a pipeline-compliant pull request, and the forge saying merged."""
    ok(q("add", topic, slug, "--title", slug, "--repo", "/tmp/repo-a", "--branch", f"fix/{slug}",
         "--number", number))
    stubs.pipeline_pr(pr, f"fix/{slug}")
    stubs.pr_state(pr, "MERGED")
    result(queue_dir / topic / f"{number}-{slug}", "shipped", "Landed.", f"https://github.com/Thurbeen/thurbox/pull/{pr}")


def topic_add(slug: str, title: str, prompt: str) -> str:
    return ok(q("topic", "add", slug, "--title", title, "--prompt", prompt)).stdout.strip()


@pytest.fixture
def three_topics(stubs, queue_dir) -> dict:
    all_landed = topic_add("all-landed", "Every task merged", "a topic whose work is entirely on main")
    landed_task(stubs, queue_dir, all_landed, "01", "first-half", 2001)
    landed_task(stubs, queue_dir, all_landed, "02", "second-half", 2002)

    half_live = topic_add("half-live", "One merged, one still out", "a topic with a worker still running in it")
    landed_task(stubs, queue_dir, half_live, "01", "merged-part", 2003)
    ok(q("add", half_live, "running-part", "--title", "running part", "--repo", "/tmp/repo-a",
         "--branch", "fix/running-part", "--number", "02"))
    set_state_line(queue_dir / half_live / "02-running-part" / "task.yaml", "dispatched")

    gave_up = topic_add("gave-up", "One merged, one given up on", "a topic a worker could not finish")
    landed_task(stubs, queue_dir, gave_up, "01", "done-part", 2004)
    ok(q("add", gave_up, "broken-part", "--title", "broken part", "--repo", "/tmp/repo-a",
         "--branch", "fix/broken-part", "--number", "02"))
    result(queue_dir / gave_up / "02-broken-part", "failed",
           "Could not make the migration work; the session is the evidence.")
    return {"all": all_landed, "live": half_live, "gave_up": gave_up}


@pytest.fixture
def archived(three_topics) -> dict:
    """The sweep that writes the flag is the one that moves the last task into a
    terminal state — `collect`, through the landing sweep `reap` owns."""
    three_topics["collect"] = q("collect").out
    return three_topics


def topic_yaml(queue_dir, topic: str) -> str:
    return (queue_dir / topic / "topic.yaml").read_text(encoding="utf-8")


def test_a_topic_whose_every_task_landed_archives_itself(archived, queue_dir):
    out = archived["collect"]
    expect(out, "all-landed", "archived")
    refute(out, "half-live      ")

    # The flag lands in topic.yaml, beside everything it already carried.
    expect(topic_yaml(queue_dir, archived["all"]), "archived:", "title: Every task merged")
    refute(topic_yaml(queue_dir, archived["live"]), "archived:")
    # `failed` is not terminal here.
    refute(topic_yaml(queue_dir, archived["gave_up"]), "archived:")


def test_every_default_view_drops_it_and_says_how_many_it_dropped(archived):
    out = q("list").out
    refute(out, "all-landed")
    # A queue that looks small is worse than one that looks long.
    expect(out, "1 archived topic(s)", "half-live", "gave-up")

    out = q("list", "--archived").out
    expect(out, "all-landed")
    refute(out, "half-live")

    expect(q("list", "--all").out, "all-landed", "half-live")

    # Reached by name, with its whole record, and no unarchiving first.
    expect(q("show", f"{archived['all']}/01-first-half").out, "01-first-half", "state:       landed")


@pytest.fixture
def unparseable(archived, queue_dir) -> dict:
    """Not read, not merely not shown: a finished topic costs one read of
    topic.yaml, so a task file that cannot be parsed must reach no default view."""
    write(queue_dir / archived["all"] / "01-first-half" / "task.yaml", "a: b: c\n")
    return archived


def test_an_archived_topics_task_files_are_never_opened(unparseable):
    out = q("list").out
    expect(out, "1 archived topic(s)", "half-live")

    out = run_fleet("status").out
    expect(out, "1 archived topic(s)")
    refute(out, "all-landed")

    out = run_fleet("status", "--json").out
    expect(out, '"archived": 1')
    refute(out, '"all-landed"')

    # The other half of "not read": `list --archived` opens the set on demand.
    expect(q("list", "--archived").out, "all-landed")


def test_the_panes_probe_counts_the_archived_topic_the_same_way(unparseable):
    """The TUI pane is the third reader, and the only one that is not Python: a
    pane that disagreed with `list` would be a second opinion about a model it
    does not own. Its probe is the module the pane's command line runs, on
    POSIX and on Windows alike."""
    pane = (REPO / "interface" / "fleet_queue.lua").read_text(encoding="utf-8")
    assert "scripts/lib/pane_probe.py" in pane, "the pane runs its queue probe from scripts/lib/pane_probe.py"
    out = run([*PYTHON, str(REPO / "scripts" / "lib" / "pane_probe.py")]).stdout
    expect(out, "A\t1", "half-live")
    refute(out, "all-landed")


def test_archive_refuses_unfinished_work_and_unarchive_brings_a_topic_back(archived, queue_dir):
    out = q("archive", archived["live"])
    assert out.code != 0, out.out
    # Naming the task holding it, with the state that made it non-terminal.
    expect(out.out, "not finished", "02-running-part", "dispatched")

    out = q("archive", archived["gave_up"])
    assert out.code != 0, out.out
    expect(out.out, "02-broken-part")

    ok(q("unarchive", archived["all"]))
    refute(topic_yaml(queue_dir, archived["all"]), "archived:")
    expect(q("list").out, "all-landed")
    ok(q("archive", archived["all"]))
    expect(q("list").out, "1 archived topic(s)")


def test_a_topic_that_grows_a_new_task_is_live_again(archived, queue_dir):
    """An operator who did not notice the flag would otherwise dispatch into a
    topic no default view draws."""
    ok(q("add", archived["all"], "third-half", "--title", "third half", "--repo", "/tmp/repo-a",
         "--branch", "fix/third-half", "--number", "03"))
    refute(topic_yaml(queue_dir, archived["all"]), "archived:")
    out = q("list").out
    expect(out, "all-landed")
    refute(out, "archived topic(s)")
    expect(q("check").out, "ok")


def test_archiving_does_not_narrow_what_the_shepherd_watches(archived, queue_dir):
    """The shepherd derives its repositories from the tasks the queue holds. One
    that only saw live topics would stop watching a repository the moment its
    last topic finished — exactly when a stray pull request has nobody looking."""
    ok(q("add", archived["all"], "third-half", "--title", "third half", "--repo", "/tmp/repo-a",
         "--branch", "fix/third-half", "--number", "03"))
    set_state_line(queue_dir / archived["all"] / "03-third-half" / "task.yaml", "landed")
    ok(q("archive", archived["all"]))
    expect(q("list").out, "1 archived topic(s)")

    refute(q("shepherd", "--ref", f"{archived['all']}/01-first-half", "--dry-run").out, "no such task")


def test_check_catches_a_topic_archived_over_live_work(archived, queue_dir):
    """Nothing fleet does can produce one — the sweep refuses it and `add` clears
    the flag — but a hand-edited topic.yaml can, and that is work nothing draws."""
    path = queue_dir / archived["live"] / "topic.yaml"
    write(path, path.read_text(encoding="utf-8") + "archived: '2026-01-01T00:00:00+00:00'\n")
    out = q("check")
    assert out.code != 0, out.out
    expect(out.out, "half-live", "02-running-part", "unarchive")


# --- and the one reader archiving must not hide a topic from ------------------
#
# Three landed tasks with live sessions had accumulated behind the flag, every
# one of them `idle` or `done` and reapable for hours, with `reap --dry-run`
# reporting `would release 0 session(s)`. Releasing them took that machine's
# disk from 83% to 51% — 67 GB, because each session holds a git worktree.

LATE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture
def stranded(stubs, queue_dir) -> str:
    """A topic whose only task landed while its worker was still `working`.

    Both halves are right and they happen in the SAME pass: `collect` closes
    the task, the landing sweep promotes it, the topic has nothing unfinished
    left in it so it archives, and the session is not at rest yet so it is
    kept. A worker that has just written `result.md` is exactly a worker that
    is `working` for a moment longer, which makes this the common case.
    """
    topic = topic_add("late-worker", "Merged before its worker went quiet",
                      "the window between a result being written and the agent going idle")
    landed_task(stubs, queue_dir, topic, "01", "wrote-and-kept-working", 2101)
    ok(q("attach", f"{topic}/01-wrote-and-kept-working", LATE))
    stubs.session_is(LATE, "working")
    out = ok(q("collect")).out
    expect(out, "01-wrote-and-kept-working", "kept", "archived")
    refute(deletions(stubs), LATE)
    return topic


def test_reap_revisits_a_session_it_kept_after_the_topic_archived(stranded, stubs):
    """A keep is a promise to look again, and archiving used to guarantee there
    was no later pass: the session, and the worktree under it, were stranded
    for good."""
    stubs.session_is(LATE, "idle")
    expect(q("reap", "--dry-run").out, "01-wrote-and-kept-working", "would reap")
    refute(deletions(stubs), LATE)

    expect(q("reap").out, "01-wrote-and-kept-working", "reaped")
    expect(deletions(stubs), "--force", LATE)

    # The operator's view of a finished queue is untouched by any of it.
    out = q("list").out
    expect(out, "1 archived topic(s)")
    refute(out, "late-worker")

    # Idempotent: the record no longer names a session, so there is nothing left
    # to say about it.
    refute(q("reap").out, "01-wrote-and-kept-working")


def test_a_worker_still_holding_an_archived_topic_is_still_kept(stranded, stubs):
    """Widening what `reap` can SEE must not widen what it will ACT on. The
    keep is the behaviour being preserved, not the one being traded away."""
    out = q("reap").out
    expect(out, "01-wrote-and-kept-working", "kept", "working")
    refute(deletions(stubs), LATE)


def test_the_collect_the_loop_actually_runs_revisits_it_too(stranded, stubs):
    """`reap` is wired into `collect`, and `collect` is what the reconciler runs
    on a timer. A fix that only reached the hand-run command would still need
    somebody to remember it."""
    stubs.session_is(LATE, "idle")
    expect(ok(q("collect")).out, "01-wrote-and-kept-working", "reaped")
    expect(deletions(stubs), "--force", LATE)


def test_a_finished_topics_run_log_is_refreshed_and_never_reopened(stranded, stubs):
    """A finished topic's log is history. `collect` reads every topic now, as
    `shepherd` always has, so one the operator deleted must not come back from
    the template on the next pass — and a checkout that gains a
    `FLEET_RUNS_DIR` late must not fill it with a template per topic it has
    ever finished."""
    log = next(Path(os.environ["FLEET_RUNS_DIR"]).glob(f"*-{stranded}.md"))
    # It carries the keep, written while the topic was still live.
    expect(log.read_text(encoding="utf-8"), "01-wrote-and-kept-working")
    log.unlink()

    stubs.session_is(LATE, "idle")
    out = ok(q("collect")).out
    expect(out, "reaped")
    refute(out, "run log")
    assert not log.exists(), f"{log.name} was scaffolded again for an archived topic"
