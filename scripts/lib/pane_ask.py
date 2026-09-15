"""Ask the operator whether to put the queue pane on their screen — once, ever.

WHY IT EXISTS. The install puts the pane plugin in and never places it: placing
is an edit to the operator's own layout.lua, and that edit waits for a yes.
The first Mission Control session asks for it, and this is what it runs.
FLEET.md tells the lead to, under every agent, rather than a hook one agent has.

WHY ONCE, EVER. The answer lives in `orchestration/first-run/pane`, gitignored
beside the rest of fleet's runtime state, and not in the lead's conversation,
which a restart discards. A layout that already places the pane — by
`fleet place-pane`, by onboarding, by hand — is never asked about, and is
recorded as `placed`.

IT NEVER PLACES WITHOUT A YES. `yes` is the only verb that edits anything, and
it does so through `fleet place-pane`, whose docstring owns what makes that
edit safe.

Usage:
  uv run fleet pane-ask              # `ask`, or `skip: <why>`
  uv run fleet pane-ask yes          # they said yes: place it right of the terminal
  uv run fleet pane-ask yes --left   # ... between the session list and the terminal
  uv run fleet pane-ask no           # they said no: remember it

The first word of the output is the answer. Exit: 0, or 2 on a usage error;
`yes` exits with place-pane's own code when that could not place it.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
STATE = os.path.join(REPO_ROOT, "orchestration", "first-run", "pane")

ASK = """\
ask: the queue pane is installed and not on screen, and nobody has asked.
Ask the operator once — put the queue pane on your screen?
  right of the terminal (recommended)   uv run fleet pane-ask yes
  left of the terminal                  uv run fleet pane-ask yes --left
  not now                               uv run fleet pane-ask no
Run the line for their answer. Never place it without a yes."""


def _load_sibling(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fleet_platform = _load_sibling("fleet_platform", "fleet_platform.py")
place_pane = _load_sibling("fleet_place_pane", "place_pane.py")


def record(answer: str) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    fleet_platform.write_record(STATE, f"{answer} {date.today().isoformat()}\n")


def main(argv: list[str]) -> int:
    verb = argv[0] if argv else ""
    if verb == "":
        if os.path.isfile(STATE):
            with open(STATE, encoding="utf-8") as fh:
                words = fh.read().split()
            answer, when = (words + ["?", "?"])[:2]
            print(f"skip: already answered ({answer}, {when})")
            return 0
        code, report = place_pane.check()
        if code == 0:
            record("placed")
            print(f"skip: already on screen — {report}")
        elif code == 1:
            print(ASK)
        else:
            # No thurbox to name the layout, or no layout yet: nothing could be
            # placed on a yes, so nothing is asked and nothing recorded.
            print(f"skip: cannot tell whether the pane is placed — {report.splitlines()[0]}")
        return 0
    if verb == "yes":
        if len(argv) > 2 or argv[1:] not in ([], ["--left"], ["--right"]):
            print("usage: fleet pane-ask yes [--left|--right]", file=sys.stderr)
            return 2
        code = place_pane.main(argv[1:])
        if code == 0:
            record("yes")
        elif code == 3:
            # Refused: the layout is not one place-pane can edit, and it printed
            # the block for the operator to add. The question was still answered.
            record("yes")
            print("\nThe answer is recorded; the block above is yours to add.")
        return code
    if verb == "no":
        record("no")
        print("Recorded. Nothing will ask about the pane again.")
        print("To put it on screen later: uv run fleet place-pane (--left for the other side)")
        return 0
    if verb in ("-h", "--help"):
        print(__doc__)
        return 0
    print("usage: fleet pane-ask [yes [--left|--right] | no]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
