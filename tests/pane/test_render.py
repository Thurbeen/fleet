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

import re
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


# --- which fleet's queue this is --------------------------------------------
#
# A machine may run several fleets — one per checkout, each with a Mission
# Control of its own (orchestration/fleet.example.conf). So the pane's question
# stopped being "is there a lead" and became "which one are you looking at",
# and the answer is the session list's own selection.


def test_two_leads_with_nothing_selected_are_a_choice_and_not_a_fault():
    """Two fleets is a supported setup, so the pane asks which rather than
    calling one of them a mistake — and never guesses."""
    out = render("44", "--leads", "two-local")
    expect(out, "2 Mission Control sessions here", "/home/operator/fleet-copy", "select")
    refute(out, "Cut the pane back", "remove the one")


def test_two_named_leads_are_offered_by_the_names_their_fleets_chose():
    out = render("44", "--leads", "two-named")
    expect(out, "acme", "lab", "select")


@pytest.mark.parametrize(
    "selected,drawn,hidden",
    [("s1", "Cut the pane back", "Calibrate the lab rig"),
     ("s2", "Calibrate the lab rig", "Cut the pane back")],
    ids=["acme", "lab"],
)
def test_the_selected_lead_is_the_queue_that_is_drawn(selected, drawn, hidden):
    out = render("44", "--leads", "two-named", "--selected", selected)
    expect(out, drawn)
    refute(out, hidden)


def test_the_fleet_that_was_chosen_survives_selecting_a_worker():
    """The common case by far: you pick a fleet once, then spend the day in
    worker sessions. A pane that fell back to the chooser on every one of them
    would be a pane nobody could read."""
    out = render("44", "--leads", "two-named", "--selected", "s2,w9")
    expect(out, "Calibrate the lab rig")
    refute(out, "select one")


def test_one_lead_needs_no_selection_at_all():
    """A machine with one fleet never meets any of this."""
    out = render("44", "--selected", "w9")
    expect(out, "Cut the pane back")


def test_the_frame_names_the_fleet_whose_queue_it_draws():
    named = render("44", "--leads", "two-named", "--selected", "s2", "--frame")
    expect(named, "lab")
    # An unnamed fleet is the only fleet there is, so the title says nothing.
    assert "title:  Fleet queue " in render("44", "--frame"), render("44", "--frame")


# --- every fuel label says what it is ---------------------------------------


def fuel_head(out: str) -> str:
    return next(line for line in out.splitlines() if "fuel" in line)


def row_with(label: str, out: str) -> str:
    return next(line for line in out.splitlines() if label in line)


def test_every_fuel_label_says_what_it_is(wide, narrow):
    """The head row read `⛽ fuel  reserve 20%  1m`: `reserve 20%` is fleet's
    own dispatch floor, a constant and not a reading, and read as "20% fuel
    left"; `1m` was the age of the cached reading with nothing saying so."""
    for width, out in (("44", wide), ("30", narrow)):
        head = fuel_head(out)
        assert "reserve" not in head, f"the fuel head row shows the reserve at {width}\n{out}"
        expect(head, "fuel left")
        # A reading inside the probe's own TTL is the ordinary case: no age.
        assert "ago" not in head, f"a fresh reading carries an age at {width}\n{out}"
        expect(row_with(" week ", out), "low", "resets 4d")
        assert "low" not in row_with(" session ", out), f"a window above the reserve says low at {width}\n{out}"


def test_a_long_label_gives_way_before_the_reset(tmp_path):
    """At 30 a label as long as the pane allows used to push every reset out of
    the block, so the one window under the reserve could not say when it
    comes back. The label gives way first."""
    out = render("30", "--long-label")
    expect(row_with(" week ", out), "resets 4d")
    expect(row_with("40%", out), "resets 2d")
    widest = max(cells(line) for line in out.splitlines())
    assert widest <= 30, f"a row is {widest} columns wide with a long label\n{out}"


def test_two_accounts_of_one_provider_get_two_labelled_bars(tmp_path):
    """A fleet whose `agent.conf` names a second login spends two windows of one
    vendor, and `fleet status --fuel` sends two records naming the same
    provider. Drawn without the account they are one reading with a mistake in
    it: two name rows that both read `claude`, four window rows under them, and
    no way to tell which 70% belongs to the account the workers run on."""
    for width in ("44", "30"):
        out = render(width, "--fuel-accounts")
        expect(out, "claude (claude-spare)")
        # The checkout's own account keeps the bare provider — there is nothing
        # to disambiguate it from until a second account exists, and `refuel`
        # names one account the same way.
        assert out.splitlines()[1].strip() == "claude", out
        # Each account keeps its own binding window and its own verdict: the
        # first is under the reserve and the second is not.
        rows = [line for line in out.splitlines() if " week " in line]
        assert len(rows) == 2, out
        assert "low" in rows[0] and "low" not in rows[1], out
        widest = max(cells(line) for line in out.splitlines())
        assert widest <= int(width), f"a row is {widest} columns wide at {width}\n{out}"


def test_the_checkout_flag_decides_the_parentheses_not_a_name_coincidence():
    """Two records where comparing `account` against `provider` gets the
    parentheses backwards: the checkout's own account (`checkout\\t1`) is
    named `lead` while its provider is `codex` — an operator's `AGENT=` and
    `FUEL_PROVIDER` commonly disagree like this — and a NAMED account
    (`checkout\\t0`) is called `codex`, the same as its own vendor. A pane
    that decided by name equality would print `codex (lead)` for the
    checkout's own bar and a bare `codex` for the named one — exactly
    backwards from what `fleet status`'s own labelling prints for the same
    two records."""
    for width in ("44", "30"):
        out = render(width, "--fuel-name-collision")
        refute(out, "codex (lead)")
        expect(out, "codex (codex)")
        widest = max(cells(line) for line in out.splitlines())
        assert widest <= int(width), f"a row is {widest} columns wide at {width}\n{out}"


def test_an_unavailable_account_still_draws_its_own_row_beside_a_reading_one():
    """The row loop used to filter itself down to the same `shown` set the bar
    layout is sized against — records with a `remaining` and no `unavailable`
    — so an account with neither simply never appeared: not a blank line, no
    row at all, invisible beside a sibling account's bar. The `--fuel` text
    this pane parses already names such an account `unavailable`; the pane
    has to say the same thing, not fewer accounts than it read."""
    for width in ("44", "30"):
        out = render(width, "--fuel-mixed-availability")
        # The `unavailable` word itself is a flush-right note, dropped under
        # the same "fewer than four columns left, so drop it" rule `stale`
        # already follows — the reason line beneath it is what proves the
        # account's row survived at every width, narrow ones included.
        expect(out, "claude", "64", "claude (claude-spare)", "quota-axi named no claude")
        widest = max(cells(line) for line in out.splitlines())
        assert widest <= int(width), f"a row is {widest} columns wide at {width}\n{out}"


def test_a_failed_provider_beside_its_own_accounts_reading_still_stays_dropped():
    """FLEET.md still documents this exact case — one account, one provider
    reading fine and a second failing — as the pane leaving the failed one
    out entirely, never drawing it bar-less. Making a whole ACCOUNT with
    nothing to read visible must not also surface a PROVIDER going quiet
    beside a sibling that shares its own account's reading."""
    for width in ("44", "30"):
        out = render(width, "--fuel-partial-provider")
        expect(out, "claude", "64")
        refute(out, "auth_required", "Codex sign-in required", "codex")


def test_the_checkouts_own_account_with_no_credential_is_named_by_its_agent_not_left_blank():
    """`fuel_label` (`fleet_status.py`) makes the checkout's own agent name the
    fallback label when there is no provider at all, never a bare blank in
    front of `unavailable`. `fuel_name` has to fall back the same way, since
    the screen and the pane must name that one account the same word."""
    for width in ("44", "30"):
        out = render(width, "--fuel-checkout-no-credential")
        expect(out, "claude (claude-spare)", "70")
        row = row_with("unavailable", out)
        assert row.strip().startswith("claude"), (
            f"the checkout's own unavailable row has no name at width {width}:\n{row!r}"
        )


def test_a_single_account_fleet_whose_whole_reading_failed_does_not_prefix_the_reason():
    """The block-level failure path used to prefix the reason by `fuel_name`.
    That helper no longer returns `""` — a nameless checkout falls back to
    `?` — so a single-account fleet with nothing to read gained `? — ` in
    front of `quota-axi is not installed`, the one rendering that path is
    meant to keep byte-identical to before accounts were named."""
    for width in ("44", "30"):
        out = render(width, "--fuel-unreadable")
        expect(out, "unavailable", "quota-axi is not installed")
        refute(out, "? —")
        row = row_with("quota-axi is not installed", out)
        assert row.strip() == "quota-axi is not installed", (
            f"the whole-reading failure reason is prefixed at width {width}:\n{row!r}"
        )


def test_a_multi_account_fleet_whose_every_account_failed_keeps_the_account_in_the_reason():
    """`#shown == 0` is also a two-account fleet with nothing readable, and
    it still draws one reason from `fuel[1]`. Prefixing that line by the
    provider alone drops `(account)`; prefixing by `fuel_name` keeps it,
    while the provider guard still keeps `?` off a nameless checkout."""
    for width in ("44", "30"):
        out = render(width, "--fuel-accounts-unreadable")
        expect(out, "unavailable", "claude (claude-spare)")
        refute(out, "? —")
        row = row_with("claude (claude-spare)", out)
        assert row.strip().startswith("claude (claude-spare) —"), (
            f"the named account is missing from the reason at width {width}:\n{row!r}"
        )


def test_an_overdue_reading_says_how_old_it_is():
    """A reading older than the TTL means the refresh is not happening, and then
    its age is the most important thing on the row, said in words."""
    expect(fuel_head(render("44", "--fuel-read", "900")), "read 15m ago")
    expect(fuel_head(render("30", "--fuel-read", "900")), "read 15m ago")


# --- the hide hint is a button, and the pane has a way back -----------------


def frame_line(out: str, key: str) -> str:
    return next((line for line in out.splitlines() if line.startswith(f"{key}:")), "")


def test_the_hide_hint_is_a_button_and_a_pill_opens_the_pane_again():
    """The pane cannot hold focus, so the hint that names its chord is a click
    target, and since a closed column draws nothing to click, the action band
    carries a pill that opens it."""
    frame = render("44", "--frame")
    # A chip like the agent pane's tabs beside it: ` Label · Key `, the chord
    # spelled the way the action band spells it, filled while the column is
    # open and brighter under the pointer.
    assert frame_line(frame, "top_right") == "top_right:  Fleet · F3 ", frame
    assert frame_line(render("44", "--frame", "--chord", "f5"), "top_right") == "top_right:  Fleet · F5 "
    assert frame_line(render("44", "--frame", "--chord", "ctrl+shift+t"), "top_right") == "top_right:  Fleet · ^⇧T "
    assert frame_line(frame, "top_right_style") == "top_right_style: fg=inverted_fg bg=accent bold", frame
    hovered = render("44", "--frame", "--hover", "hide")
    assert frame_line(hovered, "top_right_style") == "top_right_style: fg=inverted_fg bg=accent_bright bold", hovered
    assert "F3" not in frame_line(frame, "title"), f"the chord is spelled a second time in the title\n{frame}"
    expect(render("44", "--click", "F3"), "toggled fleetqueue")
    expect(render("30", "--click", "F3"), "toggled fleetqueue")
    expect(render("44", "--pills"), "pill fleetqueue.toggle Fleet")


# --- a queue longer than the pane scrolls, and says where it is -------------


def long(*args: str) -> str:
    """Forty running tasks in a pane twenty-four rows tall."""
    return render("44", "--long", "40", "--height", "24", *args)


def position(out: str) -> tuple[int, int, int] | None:
    """The frame's bottom border, as (first, last, total), or None when it shows no position."""
    found = re.search(r"^bottom_right: *(\d+)-(\d+) of (\d+) *$", out, re.MULTILINE)
    return tuple(int(n) for n in found.groups()) if found else None


def test_a_click_on_a_task_row_moves_nothing_and_the_root_takes_the_wheel():
    """A row with no link lands on the pane's root identity, which is there for
    the wheel: the kernel records a pane's own rect as a wheel target only for a
    focusable pane, and this one is not, so an identity on the root is what
    makes the whole pane scroll."""
    clicked = long("--frame", "--click", "Long task number 3")
    expect(clicked, "nothing toggled", "bottom_right:  1-")
    root = frame_line(long("--frame"), "root").removeprefix("root:").strip()
    assert root, f"the pane's root carries no identity\n{long('--frame')}"


def test_a_list_that_does_not_fit_says_where_it_is_and_marks_what_is_hidden():
    first, last, total = position(long("--frame")) or (0, 0, 0)
    assert first == 1 and total > last > 0, long("--frame")
    expect(long(), "↓")
    assert "↑" not in long(), "a list at its top marks something above"
    assert "of" not in frame_line(render("44", "--frame"), "bottom_right"), "a list that fits shows a position"


def test_the_wheel_moves_the_window_and_clamps_at_both_ends():
    _, last, _ = position(long("--frame"))
    wfirst, wlast, _ = position(long("--frame", "--wheel", "2"))
    assert wfirst > 1 and wlast > last, long("--frame", "--wheel", "2")
    expect(long("--wheel", "2"), "↑")

    bfirst, blast, btotal = position(long("--frame", "--wheel", "999"))
    assert blast == btotal, long("--frame", "--wheel", "999")
    assert "↓" not in long("--wheel", "999"), "the bottom of the list marks something below"
    _, ulast, _ = position(long("--frame", "--wheel", "999", "--wheel", "-1"))
    assert ulast < btotal, "one tick up from the bottom did not move at once"
    tfirst, _, _ = position(long("--frame", "--wheel", "3", "--wheel", "-999"))
    assert tfirst == 1, "the list does not clamp at the top"


def test_fuel_and_the_counters_stay_above_the_window_when_scrolled():
    scrolled = long("--wheel", "999").splitlines()
    expect("\n".join(scrolled[:2]), "fuel left")
    after_rule = scrolled[next(i for i, line in enumerate(scrolled) if line.startswith("─")) + 1:]
    expect("\n".join(after_rule[:2]), "running")


def test_the_marks_and_the_page_actions_page_the_window():
    bfirst, _, _ = position(long("--frame", "--wheel", "999"))
    assert position(long("--frame", "--click", "below"))[0] > 1, "clicking the mark below did not page down"
    assert position(long("--frame", "--wheel", "999", "--click", "above"))[0] < bfirst, "clicking above did not page up"
    assert position(long("--frame", "--action", "fleetqueue.page_down"))[0] > 1, "page_down did not move"
    assert position(long("--frame", "--wheel", "999", "--action", "fleetqueue.page_up"))[0] < bfirst, \
        "page_up did not move"
    widest = max(cells(line) for line in long("--wheel", "5").splitlines())
    assert widest <= 44, f"a scrolled render is {widest} columns wide"
