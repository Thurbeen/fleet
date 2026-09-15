"""The TUI queue pane is a GLANCE.

The pane's failure mode is not a crash, it is a wall: every fact at equal
weight, and no grep can see a row. So this renders the pane with
scripts/lib/pane_harness.lua against a queue built to be the reported screen —
a running topic that also holds a merged task, a blocker chain with a cleared
edge, tasks nothing has emitted an event for, a settled topic — and asserts the
rules the redesign is: no row with nothing on it, one row per task, finished
work weighs less, the two tallies agree, the two kinds of wait do not look
alike, and the publish row says the next move. At 44 columns and again at 30,
because the pane routinely gets thirty.

Needs `lua` (5.4 or newer, for `utf8.codes`); every test here skips without one.
"""

from __future__ import annotations

import unicodedata

import pytest
from panekit import REAL_LUA

from harness import REPO, expect, refute, run

pytestmark = pytest.mark.skipif(not REAL_LUA, reason="no lua on PATH: the pane is rendered by lua")


def render(*args: str) -> str:
    done = run([REAL_LUA, "scripts/lib/pane_harness.lua", *args], cwd=REPO)
    assert done.code == 0 and done.stdout.strip(), f"the pane did not render at {args}:\n{done.out}"
    return done.stdout


@pytest.fixture
def wide() -> str:
    return render("44")


@pytest.fixture
def narrow() -> str:
    return render("30")


def cells(line: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in line)


def test_no_row_carries_no_information(wide):
    refute(wide, "✓ 01-declare-publish-method", "events")
    assert "   brief" not in wide.splitlines(), "a row says only that a brief exists"
    # `shipped` under a task drawn with the `done` glyph is the same fact twice.
    refute(wide, "shipped")


def test_what_still_has_to_be_there_is_there(wide):
    expect(wide, "↳ 02-shepherd-records-publish", "no brief", "uncollected", "#47", "green")


def test_a_condition_outside_the_queue_looks_unlike_a_wait_on_a_task(wide):
    """`↳` ends when the named task lands; `⊘` ends only when somebody runs
    `block --clear`, so the operator reading the row is the actor."""
    expect(wide, "⊘ az login: for the tenant", "credential")
    refute(wide, "↳ az login")
    # The writer's YAML quoting is not drawn.
    refute(wide, "⊘ 'az login")


def test_the_publish_row_says_the_next_move(wide):
    expect(wide, "open — review", "!52", "green — yours to merge")
    # The method gives way to the note, not the reverse.
    refute(wide, "attested · #47")
    # A blocker under a task that is running holds nothing.
    refute(wide, "publish-agnostic/03-draw")


def test_one_row_per_task(wide):
    expect(wide, "01 Cut the pane back")
    refute(wide, "01-declutter-the-pane")
    # 27 rather than 26 since the fuel block draws every window: the fixture's
    # account holds two, and the detail row that named only the binding one is
    # gone, so the block costs one row more for the same reading.
    rows = [line for line in wide.splitlines() if line]
    assert len(rows) <= 27, f"the whole queue costs {len(rows)} rows\n{wide}"


def test_finished_work_weighs_less(wide):
    refute(wide, "Declare the publish method on the task")
    expect(wide, "1 landed")
    marks = render("44", "--marks")
    expect(marks, "B  ◐ 01 Cut the pane back")
    refute(marks, "B  ● 01 Remove the web monitor")


def test_the_two_tallies_agree(wide):
    expect(wide, "3 running", "3 topics")


def test_it_still_degrades_to_30_columns(narrow):
    widest = max(cells(line) for line in narrow.splitlines())
    assert widest <= 30, f"a row is {widest} columns wide in a 30-column pane\n{narrow}"
    expect(narrow, "Cut the pane back", "↳ 02-shepherd", "⊘ az login", "open — review")
    refute(narrow, "✓ 01-declare")


def row_of(label: str, out: str) -> int | None:
    """The index of the first row that carries `label`, or None."""
    return next((i for i, line in enumerate(out.splitlines()) if label in line), None)


def test_every_fuel_window_is_drawn_all_the_time(wide, narrow):
    """The block drew only the window that binds right now, so on an account with
    a five-hour and a seven-day window the row flipped between the two whenever
    their percentages crossed. It draws every window the reading carries, in the
    record's order (shortest first), and marks the binding one with a theme role
    instead of hiding the others."""
    for width, out in (("44", wide), ("30", narrow)):
        short, long = row_of(" session ", out), row_of(" week ", out)
        assert short is not None and long is not None and short < long, \
            f"both windows are drawn at {width}, shortest first\n{out}"
        expect(out, "62%", "18%")
    session = next(line for line in wide.splitlines() if " session " in line)
    week = next(line for line in wide.splitlines() if " week " in line)
    expect(session, "3h")
    expect(week, "4d")
    accent = render("44", "--accent")
    assert next(line for line in accent.splitlines() if " week " in line).startswith("A "), accent
    assert not next(line for line in accent.splitlines() if " session " in line).startswith("A "), accent


def test_a_remote_lead_listed_first_does_not_take_the_pane():
    """One remote lead beside one local one is an ordinary setup."""
    out = render("44", "--leads", "remote-first")
    expect(out, "Cut the pane back")
    refute(out, "the queue is empty", "Mission Control")


def test_two_local_leads_are_named_as_a_problem():
    out = render("44", "--leads", "two-local")
    expect(out, "2 Mission Control sessions here")
    refute(out, "Cut the pane back")
