"""`gh`: one pull request per number, and an unreachable API for any other.

Stands in for `gh pr view <url> --json body,headRefOid,headRefName,state,commits`
(collect's publish check) and `gh pr view <url> --json state` (reap's landing
check). A pull request with no body file is one the API cannot be reached for;
one with no state file is OPEN, which is what a pull request is until something
changes it. `pr list` answers an empty list: a repo it CAN read and that has
nothing open is what makes an incomplete sweep distinguishable from an empty one.
"""

import sys

from fleet_stubs import called, pr_json, read


def main() -> int:
    root = called("gh")
    args = sys.argv[1:]
    if args[:2] == ["pr", "list"]:
        print("[]")
        return 0
    url = next((a for a in args if a.startswith("http")), "")
    want = args[args.index("--json") + 1] if "--json" in args[:-1] else "body"
    n = url.rsplit("/", 1)[-1]
    if not (root / "pr-bodies" / f"{n}.md").is_file():
        sys.stderr.write("could not resolve host: api.github.com\n")
        return 1
    if "headRef" in want:
        print(pr_json.render(n, root))
    elif "state" in want:
        print(read(root / "pr-states" / f"{n}.state").strip() or "OPEN")
    else:
        sys.stdout.write(read(root / "pr-bodies" / f"{n}.md"))
    return 0
