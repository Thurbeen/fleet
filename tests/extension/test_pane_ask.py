"""The first-run ask: whether to put the queue pane on screen, once, ever.

`fleet pane-ask` says `ask` once, remembers a yes and a no in gitignored state
in the checkout, places the pane only on the yes, and never asks an operator
who already placed it. FLEET.md is what tells the lead to run it, because a
hook would be one agent's.
"""

from __future__ import annotations

import os

import pytest
from panekit import STOCK, clone, lua_stand_in, slot, thurbox

from harness import PYTHON, expect, refute, run

SLOT = slot()


@pytest.fixture
def fleet(tmp_path):
    return clone(tmp_path)


@pytest.fixture
def ui(stubs):
    lua_stand_in(stubs)
    return thurbox(stubs)


def ask(fleet, *args: str, **env):
    return run([*PYTHON, str(fleet / "scripts" / "lib" / "pane_ask.py"), *args], cwd=fleet, **env)


def state(fleet):
    return fleet / "orchestration" / "first-run" / "pane"


def test_a_first_session_is_told_to_ask_and_nothing_is_recorded(fleet, ui):
    done = ask(fleet)
    assert done.code == 0, done.out
    assert done.stdout.startswith("ask")
    expect(done.out, "fleet pane-ask yes")
    assert not state(fleet).exists()


def test_a_no_is_recorded_places_nothing_and_prints_the_way_back(fleet, ui):
    done = ask(fleet, "no")
    assert done.code == 0, done.out
    expect(done.out, "uv run fleet place-pane")
    expect(state(fleet).read_text(encoding="utf-8"), "no")
    assert ui.read_bytes() == STOCK.read_bytes()


def test_after_an_answer_the_next_session_skips_without_the_way_back(fleet, ui):
    assert ask(fleet, "no").code == 0
    done = ask(fleet)
    expect(done.out, "skip")
    refute(done.out, "place-pane")


def test_the_answer_is_gitignored(fleet, ui):
    assert ask(fleet, "no").code == 0
    assert not run(["git", "status", "--porcelain"], cwd=fleet).stdout


def test_a_yes_places_the_pane_and_is_remembered(fleet, ui):
    done = ask(fleet, "yes")
    assert done.code == 0, done.out
    expect(ui.read_text(encoding="utf-8"), f'slot = "{SLOT}"')
    expect(state(fleet).read_text(encoding="utf-8"), "yes")
    expect(ask(fleet).out, "skip")


def test_a_layout_that_already_places_the_pane_is_never_asked_about(fleet, ui):
    """A machine that placed it by hand or through onboarding never had an answer recorded."""
    assert ask(fleet, "yes").code == 0
    state(fleet).unlink()
    done = ask(fleet)
    assert done.stdout.startswith("skip"), done.out
    expect(state(fleet).read_text(encoding="utf-8"), "placed")


def test_with_no_thurbox_nothing_is_asked_and_nothing_recorded(fleet, stubs):
    done = ask(fleet, PATH=str(stubs.bin))
    assert done.code == 0, done.out
    assert done.stdout.startswith("skip"), done.out
    assert not state(fleet).exists()


def test_a_yes_on_a_layout_it_refuses_is_still_an_answer(fleet, stubs, tmp_path):
    ui = thurbox(stubs, layout=None)
    ui.write_text('return function(ctx)\n  return { children = { { slot = "center" } } }\nend\n', encoding="utf-8")
    done = ask(fleet, "yes", PATH=os.pathsep.join([str(stubs.bin), os.environ["PATH"]]))
    assert done.code == 3, done.out
    expect(done.out, "The answer is recorded")
    expect(state(fleet).read_text(encoding="utf-8"), "yes")


def test_an_answer_it_does_not_know_is_a_usage_error(fleet, ui):
    assert ask(fleet, "maybe").code == 2
    assert ask(fleet, "yes", "--up").code == 2
