"""What `gh pr view --json body,headRefOid,headRefName,state,commits` answers with.

Assembled from the fixture files a test wrote for that number. A pull request
with no commits file gets an EMPTY list, which is what the real API gives for
one `gh` could not enumerate — and which no message may read anything into.

    python3 pr_json.py <number> <root>
"""

import json
import sys
from pathlib import Path


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def render(n: str, root: Path) -> str:
    # One `<oid> <headline>` per line, oldest first, the way `gh` orders them.
    commits = [
        {"oid": line.split(" ", 1)[0], "messageHeadline": line.split(" ", 1)[1]}
        for line in read(root / "pr-commits" / f"{n}.txt").splitlines()
        if " " in line
    ]
    return json.dumps({
        "body": read(root / "pr-bodies" / f"{n}.md"),
        "state": read(root / "pr-states" / f"{n}.state").strip() or "OPEN",
        "headRefName": read(root / "pr-heads" / f"{n}.branch").strip(),
        "headRefOid": read(root / "pr-heads" / f"{n}.sha").strip(),
        "commits": commits,
    })


if __name__ == "__main__":
    print(render(sys.argv[1], Path(sys.argv[2])))
