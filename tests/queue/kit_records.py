"""Recorded forge answers for the tasks `collect --allow-unverified` used to close (§22, §23).

A `gh` that answers only from a store under the stub root: `pr view <url>` from
`prs/<owner>_<repo>_<n>.json`, and `api <path>` from `api/<path with / as __>.json`,
with a 404 exactly as GitHub gives one for anything else. The attestation marker
is this suite's publish.conf's, not any operator's pipeline's.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness import write

GH = r'''
import json
import os
import re
import sys
from pathlib import Path

D = Path(os.environ["FLEET_STUB_ROOT"]) / "records"
argv = sys.argv[1:]


def served(path):
    try:
        return (D / path).read_text(encoding="utf-8")
    except OSError:
        return None


def refuse(message):
    sys.stderr.write(message + "\n")
    raise SystemExit(1)


if argv[:2] == ["pr", "view"]:
    m = re.match(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)$", argv[2])
    doc = served("prs/%s_%s_%s.json" % m.groups()) if m else None
    if doc is None:
        refuse("GraphQL: Could not resolve to a PullRequest: %s" % argv[2])
    print(json.loads(doc)["state"] if "-q" in argv else doc)
    raise SystemExit(0)

if argv[:1] == ["api"]:
    rest = argv[1:]
    if rest[:1] == ["--hostname"]:
        rest = rest[2:]
    doc = served("api/%s.json" % rest[0].replace("/", "__"))
    if doc is None:
        # What GitHub really answers for a review asked for under a pull request
        # it is not on: a 404, and nothing about where it is.
        sys.stdout.write('{"message":"Not Found","status":"404"}')
        refuse("gh: Not Found (HTTP 404)")
    print(doc)
    raise SystemExit(0)

refuse("unknown command: gh %s" % " ".join(argv))
'''

ME = "Fleet-Operator"


def attestation(attested: str, steps=("review", "test", "push")) -> str:
    payload = json.dumps({"head_sha": attested, "steps": [{"step": s, "status": "completed"} for s in steps]})
    return f"<!-- fleet-attestation:v1 {payload} -->\n\nShipped it."


class Records:
    def __init__(self, root: Path):
        self.root = root

    def pr(self, repo: str, n: int, state: str, branch: str, head: str, attested: str | None = None,
           body: str | None = None) -> None:
        doc = {"state": state, "headRefName": branch, "headRefOid": head,
               "body": body or (attestation(attested) if attested else "Opened by hand."), "commits": []}
        write(self.root / "prs" / f"{repo.replace('/', '_')}_{n}.json", json.dumps(doc))

    def api(self, path: str, doc: dict) -> None:
        write(self.root / "api" / (path.replace("/", "__") + ".json"), json.dumps(doc))


def seed(records: Records) -> None:
    """The forge's side of the fifteen, as it answered, and two for the claims after them."""
    pr = records.pr
    # Merged, each with an attestation for a commit that is no longer the head.
    pr("Thurbeen/fleet", 48, "MERGED", "feat/reconciler-loop",
       "a2b520cd945bf0ec8990810d058610a3203dbc4f", "3dea99ac714e24fb2f7c3baf91df642bbb011ca5")
    pr("Thurbeen/fleet", 51, "MERGED", "feat/emoji-session-glyphs",
       "55d8b791b14766c162f86f82ecdc6211e9ced802", "dde3ef1ab0fafbc02a866c0dca0a68ed04eeb128")
    pr("Thurbeen/fleet", 58, "MERGED", "docs/prune-the-rationale",
       "eb807947893ff67c0772066d7081a34ab08b04e8", "cbd9481d069fd933256e926064c62b61ad4dfb21")
    # Open, from a fork's branch the task never had.
    pr("kunchenguid/firstmate", 3779, "OPEN", "thurbox-native",
       "f68d7c6026f8070d8d4dc6e7a76a33f524d95a67", "f68d7c6026f8070d8d4dc6e7a76a33f524d95a67")
    # The pull requests the ten notes sit on. 1114 was closed without merging.
    for n in (1107, 1108, 1115, 1116, 1117):
        pr("Thurbeen/thurbox", n, "MERGED", f"contrib/pr-{n}", f"{n:040d}")
    pr("Thurbeen/thurbox", 1114, "CLOSED", "contrib/pr-1114", f"{1114:040d}")
    # Stale and still open; and merged from a branch that is not the task's.
    pr("Thurbeen/fleet", 60, "OPEN", "feat/stale-open", f"{60:040d}", "f" * 40)
    pr("Thurbeen/fleet", 61, "MERGED", "feat/theirs", f"{61:040d}", f"{61:040d}")

    records.api("user", {"login": ME})
    for n, rid in ((1107, 5186731434), (1108, 5187155662), (1117, 5189498328),
                   (1116, 5189500334), (1115, 5189502936), (1114, 5189496505)):
        records.api(f"repos/Thurbeen/thurbox/pulls/{n}/reviews/{rid}", {
            "id": rid, "user": {"login": ME},
            "html_url": f"https://github.com/Thurbeen/thurbox/pull/{n}#pullrequestreview-{rid}",
            "pull_request_url": f"https://api.github.com/repos/Thurbeen/thurbox/pulls/{n}",
        })
    for n, cid in ((1108, 5646922086), (1107, 5646949262), (1117, 5651202185)):
        records.api(f"repos/Thurbeen/thurbox/issues/comments/{cid}", {
            "id": cid, "user": {"login": ME},
            "html_url": f"https://github.com/Thurbeen/thurbox/pull/{n}#issuecomment-{cid}",
            "issue_url": f"https://api.github.com/repos/Thurbeen/thurbox/issues/{n}",
        })
    # Somebody else's review, on the very pull request a task targets.
    records.api("repos/Thurbeen/thurbox/pulls/1107/reviews/7000001", {
        "id": 7000001, "user": {"login": "stranger"},
        "html_url": "https://github.com/Thurbeen/thurbox/pull/1107#pullrequestreview-7000001",
        "pull_request_url": "https://api.github.com/repos/Thurbeen/thurbox/pulls/1107",
    })


TB = "https://github.com/Thurbeen/thurbox/pull/"

# (number, slug, recorded method, recorded how, branch, artifact, re-added as, target)
#
# An empty recorded method is a record from before `publish` existed, read as this
# suite's default (`attested`). `-` under re-added is a record that already declared
# the right shape, so there is nothing to re-add.
FIFTEEN = (
    ("01", "review-1107", "push", "posts a review; there is nothing to commit", "review/pr-1107",
     f"{TB}1107#pullrequestreview-5186731434", "note", f"{TB}1107"),
    ("02", "review-1108", "push", "posts a review; there is nothing to commit", "review/pr-1108",
     f"{TB}1108#pullrequestreview-5187155662", "note", f"{TB}1108"),
    ("03", "review-1117", "push", "posts a review; nothing to commit", "review/open-pr-1117",
     f"{TB}1117#pullrequestreview-5189498328", "note", f"{TB}1117"),
    ("04", "review-1116", "push", "posts a review; nothing to commit", "review/open-pr-1116",
     f"{TB}1116#pullrequestreview-5189500334", "note", f"{TB}1116"),
    ("05", "review-1115", "push", "posts a review; nothing to commit", "review/open-pr-1115",
     f"{TB}1115#pullrequestreview-5189502936", "note", f"{TB}1115"),
    ("06", "review-1114", "push", "posts a review; nothing to commit", "review/open-pr-1114",
     f"{TB}1114#pullrequestreview-5189496505", "note", f"{TB}1114"),
    ("07", "win-clipboard-test", "push", "posts evidence as a PR comment; nothing to commit", "lab/win-1108-repro",
     f"{TB}1108#issuecomment-5646922086", "note", f"{TB}1108"),
    ("08", "deb-tmux-race", "push", "posts evidence as a PR comment; nothing to commit", "lab/deb-1107-repro",
     f"{TB}1107#issuecomment-5646949262", "note", f"{TB}1107"),
    ("09", "explain-thurbox", "push", "publishes a thurview document; there is nothing to commit",
     "docs/thurview-explainer", "http://docs.example.test:35547/review/f90d2474-48ad-4e64-8beb-3c8aa4d6e998",
     "none", ""),
    ("10", "prove-and-propose", "push", "posts test evidence on #1117 and drafts a proposal; nothing to commit",
     "lab/nvim-editor-flow", f"{TB}1117#issuecomment-5651202185", "note", f"{TB}1117"),
    ("11", "emoji-session-glyphs", "", "", "feat/emoji-session-glyphs",
     "https://github.com/Thurbeen/fleet/pull/51", "-", ""),
    ("12", "cut-defensive-prose", "no-mistakes", "run the repo's own publish command", "docs/prune-the-rationale",
     "https://github.com/Thurbeen/fleet/pull/58", "-", ""),
    ("13", "reconciler-loop", "", "", "feat/reconciler-loop", "https://github.com/Thurbeen/fleet/pull/48", "-", ""),
    ("14", "green-the-pr", "pr", "", "fix/3779-green", "https://github.com/kunchenguid/firstmate/pull/3779",
     "pr", "https://github.com/kunchenguid/firstmate/pull/3779"),
    ("15", "survey-existing", "", "", "research/build-or-adopt", "", "none", ""),
)
