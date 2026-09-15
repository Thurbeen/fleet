"""Put the fleet queue pane's column into the operator's thurbox layout.lua —
on their word, with a backup, and refusing rather than guessing.

WHY IT EXISTS. Installing the pane and SEEING it are two different things:
`thurbox-cli plugin install` succeeds, `plugin list` shows it, and the pane
draws nothing until an arrangement places its slot. The block is never applied
unasked: onboarding and the first Mission Control session ASK, and this is what
runs when the answer is yes.

WHAT KEEPS IT SAFE, in the order it matters:

  1. It refuses what it cannot read: no `columns` list it recognises, or none
     of the helpers the block calls — it prints the block and stops.
  2. It is idempotent. A layout that already carves the slot in a LIVE line is
     left exactly as it is, whatever else the operator has done to it. A
     mention inside a Lua comment is not a placement.
  3. It backs up first, to layout.lua.bak-<timestamp> beside the original.
  4. It re-reads the result with `lua` and RESTORES the backup if the file no
     longer parses, so a bad edit cannot survive this.
  5. It verifies with `thurbox-cli plugin check`, the one command that can tell
     a placed pane from a loaded one.

THE BLOCK carries the `panels.shown` guard and not only the slot: without it
the column is carved every frame, the pane's F3 flips a state nothing reads,
and the column opens and never closes. THE SLOT IS READ FROM THE PANE, never
spelled here. THE FILE is the one `thurbox-cli plugin dir` names, never a
literal path, and its line endings are kept.

Usage:
  uv run fleet place-pane              # place it to the RIGHT of the terminal
  uv run fleet place-pane --left       # between the session list and the terminal
  uv run fleet place-pane --dry-run    # print the file, the anchor and the block
  uv run fleet place-pane --check      # is it placed? changes nothing
  uv run fleet place-pane --layout P   # a layout.lua somewhere else
  uv run fleet place-pane --pane P     # read the slot from another pane file

Exit: 0 placed (or already placed), 1 not placed (--check), 2 usage or no
layout file, 3 refused — the layout is not one this block would work in.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
PANE = os.path.join(REPO_ROOT, "interface", "fleet_queue.lua")

# The column's share and its floor. A queue column narrower than this draws task
# titles one word wide; the pane degrades to 30 and no further.
PCT = 30
MIN = 34

# The centre pane is the one column every stock layout has and the one that is
# never dropped, so "right of the terminal" and "left of it" are measured
# against it. Nothing here parses Lua: a layout rearranged past recognition is
# refused rather than rewritten.
CENTER = 'columns[#columns + 1] = { slot = "center" }'

USAGE = "usage: fleet place-pane [--left|--right] [--dry-run|--check] [--layout PATH] [--pane PATH]"


class Stop(Exception):
    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def slot_of(pane: str) -> str:
    try:
        with open(pane, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        raise Stop(f"{pane} is missing; there is no pane to place") from None
    found = re.search(r'^local SLOT = "(.*)"$', text, re.M)
    if not found:
        raise Stop(f"could not read the slot name from {pane}")
    return found.group(1)


def guard(slot: str) -> list[str]:
    """The three lines that place the slot, guarded like the session column."""
    return [
        f'if panels.shown("{slot}") and filled(ctx, "{slot}") then',
        f'  columns[#columns + 1] = {{ slot = "{slot}", pct = {PCT}, min = {MIN} }}',
        "end",
    ]


def block(slot: str, indent: str) -> list[str]:
    lines = [
        "-- fleet's queue pane. Guarded like the session column: panels.shown is",
        "-- what the pane's F-key toggles, and without it the column is carved",
        "-- every frame, so the key flips a state nothing reads and the pane",
        "-- opens and never closes. Added by `fleet place-pane`.",
        *guard(slot),
    ]
    return [indent + line for line in lines]


def layout_path(given: str | None) -> str:
    if given:
        return given
    cli = shutil.which("thurbox-cli")
    if not cli:
        raise Stop("thurbox-cli not found, so the interface directory is unknown; pass --layout PATH")
    done = subprocess.run([cli, "plugin", "dir", "--text"], capture_output=True, encoding="utf-8", errors="replace")
    lines = done.stdout.splitlines()
    ui = lines[0].strip() if lines else ""
    if not ui:
        raise Stop("thurbox-cli could not name its interface directory")
    return os.path.join(ui, "layout.lua")


def read_layout(layout: str) -> str:
    if not os.path.isfile(layout):
        raise Stop(f"no layout.lua at {layout} — thurbox writes one on first run, so start it once first")
    with open(layout, encoding="utf-8", newline="") as fh:
        return fh.read()


def is_placed(text: str, slot: str) -> bool:
    """Any LIVE mention of the slot: the operator may have placed it differently,
    and owning their arrangement means not correcting it."""
    needle = f'slot = "{slot}"'
    return any(needle in line for line in text.splitlines() if not re.match(r"\s*--", line))


def not_placed(layout: str, slot: str) -> str:
    return (
        f'not placed: nothing in {layout} carves a column for slot "{slot}", so the pane\n'
        "loads, lists, and draws nothing."
    )


def check(layout: str | None = None, pane: str = PANE) -> tuple[int, str]:
    """`--check`'s exit code and its words, for a caller that decides on them."""
    try:
        slot = slot_of(pane)
        layout = layout_path(layout)
        text = read_layout(layout)
    except Stop as stop:
        return stop.code, f"error: {stop}"
    if is_placed(text, slot):
        return 0, f'placed: {layout} already carves a column for slot "{slot}"'
    return 1, not_placed(layout, slot)


def place(side: str, dry: bool, check_only: bool, layout: str | None, pane: str) -> int:
    slot = slot_of(pane)
    layout = layout_path(layout)
    text = read_layout(layout)

    if is_placed(text, slot):
        if check_only or dry:
            print(f'placed: {layout} already carves a column for slot "{slot}"')
        else:
            print(f'Already placed — {layout} carves a column for slot "{slot}", and nothing was changed.')
        return 0
    if check_only:
        print(not_placed(layout, slot))
        return 1

    # The block calls `panels.shown` and `filled(ctx, ...)`, and Lua resolves
    # globals at CALL time — so a layout defining neither still parses after the
    # edit and dies at the next launch. Both are part of "a layout this recognises".
    lines = text.splitlines(keepends=True)
    anchor = next((i for i, line in enumerate(lines) if CENTER in line), None)
    unrecognised = ""
    if anchor is None:
        unrecognised = 'no line placing the "center" slot in a columns list'
    elif "panels.shown(" not in text:
        unrecognised = "no call to panels.shown(), which the block's guard needs"
    elif "filled(ctx," not in text:
        unrecognised = "no filled(ctx, ...) helper, which the block calls"
    if unrecognised:
        print(f"Refusing to edit {layout}: it has {unrecognised}.", file=sys.stderr)
        print("That is the only shape this recognises. Your arrangement is yours — add", file=sys.stderr)
        print("this block beside the other side columns yourself:\n", file=sys.stderr)
        print("\n".join(block(slot, "  ")), file=sys.stderr)
        return 3

    indent = re.match(r"[ \t]*", lines[anchor]).group(0)
    eol = "\r\n" if lines[anchor].endswith("\r\n") else "\n"
    if side == "right":
        at, where = anchor + 1, "right of the terminal"
    else:
        at, where = anchor, "left of the terminal, beside the session list"

    if dry:
        print(f"would place the queue pane {where}\n\n  file:   {layout}\n  line:   after line {at}\n")
        print("\n".join(block(slot, indent)))
        return 0

    if not lines[anchor].endswith("\n"):
        lines[anchor] += eol
    edited = "".join(lines[:at]) + "".join(line + eol for line in block(slot, indent)) + "".join(lines[at:])

    backup = f"{layout}.bak-{time.strftime('%Y%m%d%H%M%S')}"
    try:
        shutil.copy2(layout, backup)
    except OSError:
        raise Stop(f"could not back up {layout}") from None
    # In place, so a link to the file sees the edit.
    with open(layout, "w", encoding="utf-8", newline="") as fh:
        fh.write(edited)

    # A Lua file that no longer parses is a black screen at the next launch, and
    # it would be THIS edit's doing — so it is read back before anyone lives with it.
    lua = shutil.which("lua")
    if lua:
        reread = subprocess.run(
            [lua, "-e", 'assert(loadfile(os.getenv("LAYOUT_PATH")))'],
            env=dict(os.environ, LAYOUT_PATH=layout), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if reread.returncode:
            shutil.copyfile(backup, layout)
            raise Stop(f"the edited layout no longer parses as Lua; restored {backup}", 3)

    print(f"Placed the queue pane {where}.\n\n  file:   {layout}\n  backup: {backup}\n")

    # The verdict that matters. Its failure is reported, never swallowed — but
    # the edit stands, since a check failing for an unrelated pane is not a
    # reason to undo this one.
    cli = shutil.which("thurbox-cli")
    if cli:
        done = subprocess.run([cli, "plugin", "check", "--text"], capture_output=True, encoding="utf-8", errors="replace")
        report = (done.stdout + done.stderr).rstrip("\n")
        if done.returncode == 0:
            print(f"{report}\n\nthurbox-cli plugin check is green. Press F3 in thurbox.")
        else:
            print(f"{report}\n", file=sys.stderr)
            print(
                "thurbox-cli plugin check is not green. The block is in, so read\n"
                "what it says above: it names the pane it is unhappy about, which\n"
                "may not be this one.",
                file=sys.stderr,
            )
    return 0


def main(argv: list[str]) -> int:
    side, dry, check_only, layout, pane = "right", False, False, None, PANE
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg in ("--left", "--right"):
            side = arg[2:]
        elif arg == "--dry-run":
            dry = True
        elif arg == "--check":
            check_only = True
        elif arg in ("--layout", "--pane") and args:
            if arg == "--layout":
                layout = args.pop(0)
            else:
                pane = args.pop(0)
        elif arg in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            print(USAGE, file=sys.stderr)
            return 2
    try:
        return place(side, dry, check_only, layout, pane)
    except Stop as stop:
        print(f"error: {stop}", file=sys.stderr)
        return stop.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
