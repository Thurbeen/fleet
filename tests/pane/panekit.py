"""What the pane and extension tests share.

The pane's slot as the pane declares it, the stock layout.lua fixture, a
`thurbox-cli` that answers about a layout.lua in the stub root instead of the
operator's, a `lua` stand-in, and a throwaway fleet checkout for the scripts
that write into the checkout they belong to.

WHY thurbox-cli IS A STAND-IN. A throwaway HOME does not isolate it: the real
one still reaches the real thurbox configuration, so an `extension install`
from a test would register a Mission Control session on the operator's own
machine. This one logs every call instead, and the layout.lua it names is a
copy of scripts/fixtures/layout/stock.lua.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from harness import REPO, git

PANE = REPO / "interface" / "fleet_queue.lua"
STOCK = REPO / "scripts" / "fixtures" / "layout" / "stock.lua"
CENTER = 'columns[#columns + 1] = { slot = "center" }'
REAL_LUA = shutil.which("lua")

# `plugin check` goes green only when a LIVE line of the layout carves the slot,
# which is the one verdict the real command gives that matters here.
THURBOX = r'''
import os, re, sys
from pathlib import Path
root = Path(os.environ["FLEET_STUB_ROOT"])
args = sys.argv[1:]
verb = " ".join(args[:2])
if verb == "extension install":
    print("installed fleet")
elif verb == "session list":
    listing = root / "session-list.json"
    sys.stdout.write(listing.read_text(encoding="utf-8") if listing.exists() else "[]\n")
elif verb == "plugin install":
    # thurbox's own rule: it overwrites only the bytes it last delivered. A
    # destination holding anything else is `kept` — exit 0, nothing written —
    # and one it never recorded is refused.
    dest = root / "ui" / args[args.index("--as") + 1]
    record = root / "delivered" / args[args.index("--as") + 1]
    if not Path(args[2]).is_file() or (root / "refuse-install" / Path(args[2]).name).exists():
        print("error: could not read " + args[2])
        raise SystemExit(1)
    payload = Path(args[2]).read_bytes()
    current = dest.read_bytes() if dest.exists() else None
    last = record.read_bytes() if record.exists() else None
    if current is not None and last is None:
        print("error: " + args[args.index("--as") + 1] + " already exists and is not managed here")
        raise SystemExit(1)
    if current is None and last is not None:
        outcome = "deleted"
    elif current is not None and current not in (payload, last):
        outcome = "kept"
    else:
        outcome = "installed" if current is None else "updated"
        for path in (dest, record):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
    print(outcome + " " + args[2])
elif verb == "plugin list":
    # A file under the interface with no delivery record is one thurbox never installed.
    import json
    ui = root / "ui"
    print(json.dumps({"files": [
        {"file": path.relative_to(ui).as_posix(),
         "installed_from": "somewhere" if (root / "delivered" / path.relative_to(ui)).exists() else None}
        for path in sorted(ui.rglob("*")) if path.is_file()
    ]}))
elif verb == "plugin remove":
    # Forgets the record; takes back only a file still what was delivered.
    dest = root / "ui" / args[2]
    record = root / "delivered" / args[2]
    if not record.exists():
        print("error: " + args[2] + " is not installed")
        raise SystemExit(1)
    if dest.exists() and dest.read_bytes() == record.read_bytes():
        dest.unlink()
    record.unlink()
elif verb == "plugin dir":
    print(root / "ui")
elif verb == "plugin check":
    layout = root / "ui" / "layout.lua"
    text = layout.read_text(encoding="utf-8") if layout.exists() else ""
    live = [line for line in text.splitlines() if not re.match(r"\s*--", line)]
    if any('slot = "fleetqueue"' in line for line in live):
        # Chords claimed twice are reported only once every pane is placed.
        claims = root / "check-claims.txt"
        if claims.exists():
            sys.stdout.write(claims.read_text(encoding="utf-8"))
        print("✓ loads — fleetqueue")
    else:
        print('✗ nothing places slot "fleetqueue"')
        raise SystemExit(1)
'''


def slot(pane: Path = PANE) -> str:
    found = re.search(r'^local SLOT = "(.*)"$', pane.read_text(encoding="utf-8"), re.M)
    assert found, f"no SLOT in {pane}"
    return found.group(1)


def thurbox(stubs, layout: Path | None = STOCK) -> Path:
    """Stand in for thurbox-cli, with `layout` copied in as its layout.lua; returns that file."""
    stubs.tool("thurbox-cli", THURBOX)
    ui = stubs.root / "ui" / "layout.lua"
    ui.parent.mkdir(parents=True, exist_ok=True)
    if layout is not None:
        shutil.copy(layout, ui)
    return ui


def sessions(stubs, text: str) -> None:
    """What `thurbox-cli session list --json` answers."""
    (stubs.root / "session-list.json").write_text(text, encoding="utf-8")


def lua_stand_in(stubs, exit_code: int | None = None) -> None:
    """A `lua` in the test's own bin: the real one when there is one, or a fixed exit code."""
    if exit_code is not None:
        stubs.tool("lua", f"raise SystemExit({exit_code})\n")
    elif REAL_LUA:
        stubs.tool(
            "lua",
            "import subprocess, sys\n"
            f"raise SystemExit(subprocess.run([{REAL_LUA!r}, *sys.argv[1:]]).returncode)\n",
        )


def clone(where: Path) -> Path:
    """A throwaway fleet checkout holding what the extension, the pane and the two asks read.

    Committed, so `git status` can say whether anything they wrote is tracked.
    """
    dest = where / "fleet"
    for rel in (".gitignore", "AGENTS.md", "FLEET.md", "extension.toml.in", "interface/fleet_queue.lua", "interface/fleet_board.lua", "interface/fleet_reader.lua", "interface/fleet_kanban.lua", "interface/fleet_lead.lua"):
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, dest / rel)
    for pattern, sub in (("*.example.conf", "orchestration"), ("*.py", "scripts/lib")):
        (dest / sub).mkdir(parents=True, exist_ok=True)
        for src in (REPO / sub).glob(pattern):
            shutil.copy(src, dest / sub / src.name)
    git("init", "-q", cwd=dest)
    git("add", "-A", cwd=dest)
    git("commit", "-qm", "fixture: the checkout under test", cwd=dest)
    return dest
