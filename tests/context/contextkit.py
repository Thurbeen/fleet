"""What every context test stands on: a checkout per repository, a queue, and a TOON reader.

The reader is written here from the format and not imported from fleet, so a
test that says "stdout parses as TOON" is a second opinion about the encoder in
`scripts/lib/fleet_platform.py` rather than that encoder agreeing with itself.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from harness import PYTHON, Run, git, run, write

BRIEF = (
    "## What to do\n\nDo the thing.\n\n## Hard constraints\n\nNone.\n\n"
    "## Coordination\n\nNone.\n\n## Done means\n\nIt is done.\n"
)

# `fleet <args>` with a stdin that ends the process the moment anything touches
# it: an AXI never prompts, so no subcommand may read one.
NO_STDIN = """import sys
class NoStdin:
    def __getattr__(self, name):
        sys.stderr.write("stdin was read: " + name + "\\n")
        raise SystemExit(98)
sys.stdin = NoStdin()
from fleet.cli import main
sys.exit(main())
"""


def context(*args: str, **env: str | None) -> Run:
    return run([*PYTHON, "-c", NO_STDIN, "context", *args], **env)


def facts_root() -> Path:
    return Path(os.environ["FLEET_REGISTRY_FILE"]).parent / "facts"


def checkout(root: Path, name: str, origin: str | None) -> Path:
    """A git checkout whose `origin` is `origin`, or none at all."""
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "--initial-branch=main", str(path), cwd=root)
    if origin:
        git("remote", "add", "origin", origin, cwd=path)
    return path


# --- TOON, read back ---------------------------------------------------------

HEADER = re.compile(r"^(?P<key>[A-Za-z_][\w.]*)(?:\[(?P<n>\d+)\](?:\{(?P<fields>[^}]*)\})?)?:(?P<rest>.*)$")


def split_values(text: str) -> list[str]:
    """One row's comma-separated values, honouring TOON's quoting and escapes."""
    values, cur, quoted, i = [], "", False, 0
    started = False
    while i < len(text):
        ch = text[i]
        if quoted:
            if ch == "\\":
                nxt = text[i + 1]
                cur += {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}[nxt]
                i += 2
                continue
            if ch == '"':
                quoted = False
            else:
                cur += ch
        elif ch == '"' and not cur.strip():
            quoted, started, cur = True, True, ""
        elif ch == ",":
            values.append(cur if started else cur.strip())
            cur, started = "", False
        else:
            cur += ch
        i += 1
    assert not quoted, f"unterminated quote in {text!r}"
    values.append(cur if started else cur.strip())
    return values


def parse_toon(text: str) -> dict:
    """A TOON document as a dict, asserting every declared length holds."""
    doc: dict = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        assert line and not line.startswith(" "), f"line {i + 1} is not a top-level key: {line!r}"
        m = HEADER.match(line)
        assert m, f"line {i + 1} is not TOON: {line!r}"
        key, n, fields, rest = m["key"], m["n"], m["fields"], m["rest"]
        assert key not in doc, f"key {key!r} appears twice"
        if n is None:
            assert rest.startswith(" "), f"line {i + 1}: no space after the colon: {line!r}"
            [doc[key]] = split_values(rest[1:])
        elif fields is None:
            values = split_values(rest[1:]) if rest.strip() else []
            assert len(values) == int(n), f"{key} declares {n} values and holds {len(values)}"
            doc[key] = values
        else:
            assert rest == "", f"line {i + 1}: a table header carries nothing after its colon"
            names = fields.split(",")
            rows = []
            for _ in range(int(n)):
                i += 1
                assert i < len(lines) and lines[i].startswith("  "), f"{key} declares {n} rows"
                values = split_values(lines[i][2:])
                assert len(values) == len(names), f"{key} row {lines[i]!r} is not {names}"
                rows.append(dict(zip(names, values, strict=True)))
            doc[key] = rows
        i += 1
    return doc


def read_fact(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), text
    _, front, body = text.split("---", 2)
    return yaml.safe_load(front), body.strip()


def fact_files(repo: str | None = None) -> list[Path]:
    root = facts_root() / repo if repo else facts_root()
    return sorted(root.rglob("*.md")) if root.exists() else []


def plant(repo: str, fid: str, text: str, at: str = "2026-10-01T00:00:00Z", source: str = "agent:test",
          replaces: str | None = None) -> Path:
    """A fact file written the way fleet writes one, for a reader to find."""
    front = {"id": fid, "repo": repo, "at": at, "source": source}
    if replaces:
        front["replaces"] = replaces
    path = facts_root() / repo / f"{fid}.md"
    write(path, "---\n" + yaml.safe_dump(front, sort_keys=False) + "---\n" + text + "\n")
    return path
