"""Placing the pane is safe, or it does not happen.

Idempotent, backed up, refused outright on a layout it cannot recognise, and
the block it writes carries the `panels.shown` guard and the slot the PANE
declares. Nothing here touches the operator's layout.lua: every edit is to a
copy of scripts/fixtures/layout/stock.lua.
"""

from __future__ import annotations

import os
import shutil

import pytest
from panekit import CENTER, PANE, REAL_LUA, STOCK, lua_stand_in, slot, thurbox

from harness import PYTHON, REPO, expect, refute, run

PLACE = REPO / "scripts" / "lib" / "place_pane.py"
SLOT = slot()


def place(*args: str, path: str, **env):
    return run([*PYTHON, str(PLACE), *args], PATH=path, **env)


def line_of(text: str, needle: str) -> int:
    return next(i for i, line in enumerate(text.splitlines()) if needle in line)


@pytest.fixture
def path(stubs) -> str:
    """No thurbox on this PATH: placement works from --layout alone, which is
    also what proves it never needs the operator's real interface directory."""
    lua_stand_in(stubs)
    return str(stubs.bin)


@pytest.fixture
def layout(tmp_path):
    lay = tmp_path / "right" / "layout.lua"
    lay.parent.mkdir()
    shutil.copy(STOCK, lay)
    return lay


def test_the_stock_layout_fixture_exists_and_the_pane_declares_a_slot():
    assert STOCK.is_file()
    assert SLOT


def test_an_unplaced_pane_is_exit_1_from_check_and_says_what_that_costs(path, layout):
    done = place("--check", "--layout", str(layout), path=path)
    assert done.code == 1, done.out
    expect(done.out, "draws nothing")


def test_dry_run_names_the_file_it_would_edit_and_changes_nothing(path, layout):
    done = place("--dry-run", "--layout", str(layout), path=path)
    assert done.code == 0, done.out
    expect(done.out, str(layout))
    assert layout.read_bytes() == STOCK.read_bytes()


def test_placing_it_says_which_side_and_check_then_agrees(path, layout):
    done = place("--layout", str(layout), path=path)
    assert done.code == 0, done.out
    expect(done.out, "right of the terminal")
    assert place("--check", "--layout", str(layout), path=path).code == 0


def test_the_default_places_the_column_right_of_the_terminal(path, layout):
    """RIGHT means after the centre column, which is the whole recommendation."""
    assert place("--layout", str(layout), path=path).code == 0
    text = layout.read_text(encoding="utf-8")
    assert line_of(text, f'slot = "{SLOT}"') > line_of(text, CENTER)


def test_the_block_carries_the_guard_and_the_slot_the_pane_declares(path, layout):
    assert place("--layout", str(layout), path=path).code == 0
    expect(layout.read_text(encoding="utf-8"), f'panels.shown("{SLOT}")', f'slot = "{SLOT}"')


def test_the_slot_has_one_spelling_and_it_is_the_panes(path, tmp_path):
    """A writer carrying a slot name of its own would carve a column a renamed
    pane never fills, which is the rename that half-lands and looks installed."""
    renamed = tmp_path / "renamed_queue.lua"
    renamed.write_text(
        PANE.read_text(encoding="utf-8").replace(f'local SLOT = "{SLOT}"', 'local SLOT = "renamedqueue"'),
        encoding="utf-8",
    )
    lay = tmp_path / "renamed" / "layout.lua"
    lay.parent.mkdir()
    shutil.copy(STOCK, lay)
    done = place("--pane", str(renamed), "--layout", str(lay), path=path)
    assert done.code == 0, done.out
    text = lay.read_text(encoding="utf-8")
    expect(text, 'slot = "renamedqueue"')
    refute(text, f'slot = "{SLOT}"')


def test_a_second_run_changes_nothing_byte_for_byte(path, layout):
    assert place("--layout", str(layout), path=path).code == 0
    before = layout.read_bytes()
    done = place("--layout", str(layout), path=path)
    assert done.code == 0, done.out
    expect(done.out, "Already placed")
    assert layout.read_bytes() == before


def test_the_edit_leaves_a_backup_beside_the_original(path, layout):
    assert place("--layout", str(layout), path=path).code == 0
    backups = list(layout.parent.glob("layout.lua.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == STOCK.read_bytes()


def test_left_places_the_column_before_the_terminal(path, layout):
    done = place("--left", "--layout", str(layout), path=path)
    assert done.code == 0, done.out
    text = layout.read_text(encoding="utf-8")
    assert line_of(text, f'slot = "{SLOT}"') < line_of(text, CENTER)


def test_an_unrecognised_arrangement_is_refused_and_the_block_printed_instead(path, tmp_path):
    odd = tmp_path / "odd" / "layout.lua"
    odd.parent.mkdir()
    odd.write_text('return function(ctx)\n  return { children = { { slot = "center" } } }\nend\n', encoding="utf-8")
    before = odd.read_bytes()
    done = place("--layout", str(odd), path=path)
    assert done.code == 3, done.out
    expect(done.out, f'panels.shown("{SLOT}")')
    assert odd.read_bytes() == before


def test_a_missing_layout_exits_2_and_says_thurbox_writes_one(path, tmp_path):
    done = place("--layout", str(tmp_path / "nope" / "layout.lua"), path=path)
    assert done.code == 2, done.out
    expect(done.out, "no layout.lua")


def test_a_layout_missing_the_helpers_the_block_calls_is_refused(path, tmp_path):
    """Lua resolves globals at CALL time, so the edited file would parse cleanly,
    survive the re-read, and take the whole interface down at the next launch."""
    trimmed = tmp_path / "trimmed" / "layout.lua"
    trimmed.parent.mkdir()
    trimmed.write_text(
        'return function(ctx)\n  local columns = {}\n  columns[#columns + 1] = { slot = "center" }\n'
        "  return { columns = columns }\nend\n",
        encoding="utf-8",
    )
    before = trimmed.read_bytes()
    done = place("--layout", str(trimmed), path=path)
    assert done.code == 3, done.out
    expect(done.out, "panels.shown()")
    assert trimmed.read_bytes() == before


def test_a_commented_out_block_is_not_a_placement(path, layout, tmp_path):
    """Commenting the block out to see whether it broke the interface is how an
    operator ends up here; "already placed" would leave the pane invisible and
    call it success."""
    assert place("--layout", str(layout), path=path).code == 0
    commented = tmp_path / "commented" / "layout.lua"
    commented.parent.mkdir()
    commented.write_text(
        "".join(
            f"-- {line}" if f'slot = "{SLOT}"' in line else line
            for line in layout.read_text(encoding="utf-8").splitlines(keepends=True)
        ),
        encoding="utf-8",
    )
    done = place("--check", "--layout", str(commented), path=path)
    assert done.code == 1, done.out
    expect(done.out, "draws nothing")
    assert place("--layout", str(commented), path=path).code == 0
    live = [
        line for line in commented.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--") and f'slot = "{SLOT}"' in line
    ]
    assert len(live) == 1


@pytest.mark.skipif(not REAL_LUA, reason="no lua on PATH to read the edited layout back")
def test_the_edited_layout_still_parses_as_lua(path, layout):
    assert place("--layout", str(layout), path=path).code == 0
    done = run([REAL_LUA, "-e", 'assert(loadfile(os.getenv("LAYOUT_PATH")))'], LAYOUT_PATH=str(layout))
    assert done.code == 0, done.out


def test_an_edit_that_no_longer_parses_is_restored_from_the_backup(stubs, layout):
    """A Lua file that no longer parses is a black screen at the next launch."""
    lua_stand_in(stubs, exit_code=1)
    done = place("--layout", str(layout), path=str(stubs.bin))
    assert done.code == 3, done.out
    expect(done.out, "no longer parses", "restored")
    assert layout.read_bytes() == STOCK.read_bytes()


def test_a_crlf_layout_keeps_its_line_endings(path, tmp_path):
    lay = tmp_path / "crlf" / "layout.lua"
    lay.parent.mkdir()
    lay.write_bytes(STOCK.read_bytes().replace(b"\n", b"\r\n"))
    assert place("--layout", str(lay), path=path).code == 0
    body = lay.read_bytes()
    assert body.count(b"\n") == body.count(b"\r\n")
    assert f'slot = "{SLOT}"'.encode() in body


def test_the_layout_is_the_one_thurbox_names_and_plugin_check_is_asked(stubs):
    """Never a literal path: `thurbox-cli plugin dir` names the interface directory."""
    ui = thurbox(stubs)
    lua_stand_in(stubs)
    done = place(path=os.pathsep.join([str(stubs.bin), os.environ["PATH"]]))
    assert done.code == 0, done.out
    expect(ui.read_text(encoding="utf-8"), f'slot = "{SLOT}"')
    assert stubs.calls("thurbox-cli", "plugin dir")
    assert stubs.calls("thurbox-cli", "plugin check")
    expect(done.out, "plugin check is green")


def test_without_thurbox_or_a_layout_path_it_exits_2(path):
    done = place(path=path)
    assert done.code == 2, done.out
    expect(done.out, "thurbox-cli not found", "--layout")
