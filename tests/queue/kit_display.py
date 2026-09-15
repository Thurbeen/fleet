"""Records that reach a state a real run only reaches through the forge.

Every helper here edits a THROWAWAY record in the test's own queue. The fix
for a contradiction is always in what the surfaces say about a record, never in
the record, so these exist to write the contradiction down and nothing else.
"""

import re
from pathlib import Path

import yaml

from harness import write


def set_field(queue_dir: Path, ref: str, key: str, value: str) -> None:
    """Rewrite one top-level field of `<ref>/task.yaml`."""
    path = queue_dir / ref / "task.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc[key] = value
    write(path, yaml.safe_dump(doc, sort_keys=False, default_flow_style=False))


def set_state_line(path: Path, state: str) -> None:
    """`sed -i 's/^state: .*/state: <state>/'`: the line, and nothing else about the file."""
    write(path, re.sub(r"(?m)^state: .*$", f"state: {state}", path.read_text(encoding="utf-8")))


def rows(out: str, slug: str, after: int = 2) -> str:
    """`grep -A<after> <slug>`: every line naming the slug and the lines under it."""
    lines = out.splitlines()
    picked: list[str] = []
    for i, line in enumerate(lines):
        if slug in line:
            picked.extend(lines[i:i + after + 1])
    return "\n".join(picked)
