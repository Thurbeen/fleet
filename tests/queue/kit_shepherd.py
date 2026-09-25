"""The shepherd's forge and thurbox, and the pull requests it is shown.

The shepherd asks the FORGE for every open pull request, so its `gh` answers
`pr list` out of one JSON document per pull request under `shep/gh/`, and it
refuses every subcommand it was not taught — an unexpected reach for
`pr close` fails the test rather than passing quietly. Its `thurbox-cli`
REGISTERS each session it creates, idle and reporting, so the trust step
confirms with no keystroke and a second pass can see the fixer it sent.

Both stand in through `Stubs.tool`, for the one test that installs them; the
canned stubs answer every other test.
"""

import json
from pathlib import Path

from fleet_stubs import attest
from queuekit import ok

from harness import Stubs, git, write
from harness import run_queue as q

GREEN = {"__typename": "CheckRun", "name": "CI", "status": "COMPLETED", "conclusion": "SUCCESS"}

GH = r'''
import json, os, sys
from pathlib import Path

shep = Path(os.environ["FLEET_STUB_ROOT"]) / "shep"
args = sys.argv[1:]
if os.environ.get("SHEP_GH_DOWN"):
    sys.stderr.write("gh: could not connect to github.com\n")
    raise SystemExit(1)
# Who has push access, which is what "opened by the repository owner" means
# once the owner is an organisation and the author is a person in it.
if args[:1] == ["api"]:
    login = args[1].removesuffix("/permission").rsplit("/", 1)[-1]
    perm = shep / "perms" / login
    level = perm.read_text(encoding="utf-8").strip() if perm.is_file() else "none"
    print(json.dumps({"permission": level}))
    raise SystemExit(0)
docs = sorted((shep / "gh").glob("*.json"))
if args[:2] == ["pr", "list"]:
    repo = args[args.index("--repo") + 1] if "--repo" in args else ""
    found = []
    for f in docs:
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("state") == "OPEN" and d["url"].split("/pull/")[0][len("https://github.com/"):] == repo:
            found.append(d)
    print(json.dumps(found))
    raise SystemExit(0)
n = args[2].rstrip("/").rsplit("/", 1)[-1] if len(args) > 2 else ""
if args[:2] == ["pr", "view"]:
    if os.environ.get("SHEP_VIEW_DOWN") == n:
        sys.stderr.write("gh: could not read pull request state\n")
        raise SystemExit(1)
    doc = shep / "gh" / f"{n}.json"
    if not doc.is_file():
        sys.stderr.write(f"gh: no pull request {n}\n")
        raise SystemExit(1)
    d = json.loads(doc.read_text(encoding="utf-8"))
    if args[-2:] == ["-q", ".body"]:
        print(d["body"])
    elif args[-2:] == ["-q", ".state"]:
        print(d["state"])
    else:
        sys.stdout.write(doc.read_text(encoding="utf-8"))
elif args[:2] == ["pr", "merge"]:
    if os.environ.get("SHEP_MERGE_REJECTED") == n:
        sys.stderr.write("gh: merge rejected\n")
        raise SystemExit(1)
    doc = shep / "gh" / f"{n}.json"
    d = json.loads(doc.read_text(encoding="utf-8"))
    d["state"] = "MERGED"
    doc.write_text(json.dumps(d), encoding="utf-8")
    with open(shep / "merged", "a", encoding="utf-8") as fh:
        fh.write(n + "\n")
    if os.environ.get("SHEP_MERGE_CLEANUP_ERROR") == n:
        sys.stderr.write("gh: could not delete local branch checked out in a worktree\n")
        raise SystemExit(1)
    print("merged")
else:
    sys.stderr.write(f"gh: the shepherd is not allowed to run '{' '.join(args[:2])}'\n")
    raise SystemExit(1)
'''

THURBOX = r'''
import json, os, sys
from pathlib import Path

root = Path(os.environ["FLEET_STUB_ROOT"])
sessions = root / "sessions"
args = sys.argv[1:]
verb = args[:2]
if verb == ["session", "get"]:
    record = sessions / f"{args[2]}.json"
    if not record.is_file():
        sys.stderr.write(f"no such session: {args[2]}\n")
        raise SystemExit(1)
    sys.stdout.write(record.read_text(encoding="utf-8"))
elif verb == ["session", "list"]:
    print(json.dumps([json.loads(f.read_text(encoding="utf-8")) for f in sorted(sessions.glob("*.json"))]))
elif verb == ["session", "create"]:
    counter = root / "shep" / "creates"
    n = int(counter.read_text(encoding="utf-8")) + 1 if counter.is_file() else 1
    counter.write_text(str(n), encoding="utf-8")
    sid = f"f1xe4000-0000-0000-0000-00000000000{n}"
    sessions.mkdir(parents=True, exist_ok=True)
    # Up and reporting, so the trust step confirms with no keystroke.
    (sessions / f"{sid}.json").write_text(
        json.dumps({"id": sid, "state": "idle", "agent": "claude", "hook_reported": True}) + "\n",
        encoding="utf-8")
    print(json.dumps({"id": sid, "created": True}))
elif verb == ["session", "capture"]:
    print(json.dumps({"output": ""}))
'''

# The many-repo forge: one repository holding exactly gh's list limit.
GH_MANY = r'''
import json, sys

args = sys.argv[1:]
if args[:2] == ["pr", "list"]:
    print(json.dumps([
        {"number": i, "state": "OPEN", "isDraft": False,
         "url": "https://github.com/many-owner/many-repo/pull/%d" % i,
         "mergeable": "MERGEABLE", "reviewDecision": "", "statusCheckRollup": [],
         "body": "", "headRefName": "branch-%d" % i, "baseRefName": "main",
         "headRefOid": "0" * 40, "author": {"login": "someone", "is_bot": False},
         "headRepositoryOwner": {"login": "many-owner"}, "isCrossRepository": False}
        for i in range(1000)
    ]))
    raise SystemExit(0)
sys.stderr.write(f"gh: the many-repo stub is not allowed to run '{' '.join(args[:2])}'\n")
raise SystemExit(1)
'''

BUSY = "99999999-9999-9999-9999-999999999999"
GONE = "88888888-8888-8888-8888-888888888888"

# The shepherd topic's tasks: number, slug, the pull request its worker reported.
TASKS = (("01", "conflicting", 101), ("02", "green", 102), ("03", "skipped", 103), ("04", "elsewhere", 104),
         ("05", "busy", 105), ("06", "unrun", 106), ("07", "gone", 107), ("08", "second", 113))


class Shep:
    """One test's shepherd: its forge, its sessions, and what it did."""

    def __init__(self, stubs: Stubs):
        self.stubs = stubs
        self.root = stubs.root / "shep"
        (self.root / "gh").mkdir(parents=True, exist_ok=True)
        stubs.tool("gh", GH)
        stubs.tool("thurbox-cli", THURBOX)

    def pr(self, n: int, owner: str = "Thurbeen/fleet", sha: str | None = None, body: str | None = None,
           **kw) -> None:
        sha = sha or f"{n:040d}"
        doc = {
            "number": n, "state": "OPEN", "title": f"PR {n}", "isDraft": False,
            "url": f"https://github.com/{owner}/pull/{n}",
            "mergeable": "MERGEABLE", "reviewDecision": "", "statusCheckRollup": [GREEN],
            "body": attest.body(sha) if body is None else body,
            "headRefName": "fix/x", "baseRefName": "main", "headRefOid": sha,
            "author": {"login": "maintainer", "is_bot": False},
            "headRepositoryOwner": {"login": owner.split("/")[0]},
            "isCrossRepository": False,
        }
        doc.update(kw)
        write(self.root / "gh" / f"{n}.json", json.dumps(doc))

    def update(self, n: int, **kw) -> None:
        path = self.root / "gh" / f"{n}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc.update(kw)
        write(path, json.dumps(doc))

    def perm(self, login: str, level: str) -> None:
        write(self.root / "perms" / login, level + "\n")

    def session(self, sid: str, state: str) -> None:
        write(self.stubs.root / "sessions" / f"{sid}.json",
              json.dumps({"id": sid, "state": state, "agent": "claude", "hook_reported": True}) + "\n")

    def merged(self) -> list[int]:
        try:
            return [int(n) for n in (self.root / "merged").read_text(encoding="utf-8").split()]
        except OSError:
            return []

    def creates(self, task: str = "") -> list[str]:
        return [c for c in self.stubs.calls("thurbox-cli", "session create") if f"__{task}" in c]

    def gh_log(self) -> str:
        return "\n".join(self.stubs.calls("gh"))

    def tbx_log(self) -> str:
        return "\n".join(self.stubs.calls("thurbox-cli"))


def repo(path: Path, *branches: str) -> Path:
    """A real repository, because a fixer's checkout is a real git worktree."""
    path.mkdir(parents=True)
    git("init", "-q", "-b", "main", str(path))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=path)
    for branch in branches:
        git("branch", branch, cwd=path)
    return path


def result(task_dir: Path, artifact: str) -> None:
    write(task_dir / "result.md", f"---\noutcome: shipped\nartifact: {artifact}\n---\nShipped it.\n")


def seed(shep: Shep, srepo: Path, queue_dir: Path) -> str:
    """The shepherd topic, collected, with every pull request the forge knows about."""
    topic = ok(q("topic", "add", "shepherd-cases", "--title", "The PRs, after the work",
                 "--prompt", "watch every open PR and dispatch a fixer when one goes bad")).stdout.strip()
    for n, slug, pr in TASKS:
        ok(q("add", topic, slug, "--title", f"A PR that is {slug}", "--repo", str(srepo),
             "--branch", f"fix/{slug}", "--number", n))
        owner = "someone-else/their-repo" if slug == "elsewhere" else "Thurbeen/fleet"
        result(queue_dir / topic / f"{n}-{slug}", f"https://github.com/{owner}/pull/{pr}")
    # The branches appear only NOW, the order the real thing happens in: `add`
    # refuses a branch that already exists, and the worker's spawn creates it.
    for br in ("conflicting", "green", "skipped", "elsewhere", "busy", "unrun", "gone", "second", "prose-only"):
        git("branch", f"fix/{br}", cwd=srepo)
    ok(q("collect"))

    shep.perm("maintainer", "admin")
    shep.pr(101, mergeable="CONFLICTING", headRefName="fix/conflicting")
    shep.pr(102, headRefName="fix/green")
    # Green in every way GitHub can see, and opened outside the pipeline.
    shep.pr(103, headRefName="fix/skipped", body="Fixed it.\n")
    shep.pr(104, owner="someone-else/their-repo", headRefName="fix/elsewhere")
    # Green in the only sense GitHub can offer before its checks exist.
    shep.pr(106, headRefName="fix/unrun", statusCheckRollup=[])
    shep.pr(107, mergeable="CONFLICTING", headRefName="fix/gone")
    shep.pr(105, mergeable="CONFLICTING", headRefName="fix/busy")
    # The ones the forge knows about and the task records do not.
    # 108: no task recorded it, and it is perfect.
    shep.pr(108, headRefName="fix/nobody-sent-me")
    # 109: a stranger's, from a fork, green and attested for its own head.
    shep.pr(109, headRefName="patch-1", author={"login": "stranger", "is_bot": False},
            headRepositoryOwner={"login": "stranger"}, isCrossRepository=True)
    # 110: attested for the sha BEFORE the last push.
    shep.pr(110, headRefName="fix/stale", body=attest.body("f" * 40))
    # 111: the prose anyone can type, and no attestation at all.
    shep.pr(111, headRefName="fix/prose-only", body="Reviewed, tested, linted, and opened through the pipeline.\n")
    # 112: the branch IS ours, and the author cannot push here.
    shep.pr(112, headRefName="fix/green", author={"login": "stranger", "is_bot": False})
    # The #25 case: a SECOND pull request from a task whose artifact already merged.
    shep.pr(113, state="MERGED", headRefName="fix/second")
    shep.pr(114, mergeable="CONFLICTING", headRefName="fix/second")

    # 05's worker is still mid-turn; 07's was cleaned up and its id answers nobody.
    shep.session(BUSY, "working")
    ok(q("attach", f"{topic}/05-busy", BUSY))
    ok(q("attach", f"{topic}/07-gone", GONE))
    return topic
