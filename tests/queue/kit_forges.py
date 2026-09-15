"""The forges a queue test drives fleet through that are not the `gh` stub.

    FAKE_FORGE   a second implementation of scripts/lib/forge.py's interface, loaded
                 through FLEET_FORGE_PLUGINS: files on disk, no network, no CLI (§13)
    FAKE_GLAB    a `glab` that replays recorded GitLab answers from files (§14)
    TRIPWIRE     a CLI that must not be asked anything; the stub's call log is the
                 evidence, and the failing answer is what an unreachable one gives

Each lives here and not in the stub package because it is one section's world:
every other queue test expects `gh` to answer and `glab` to hold no instance.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness import REPO, write

GLAB_FIXTURES = REPO / "tests" / "fixtures" / "glab"


def tripwire(message: str) -> str:
    """A tool that answers nothing: the stub has already logged the call."""
    return f"import sys\nsys.stderr.write({message!r} + '\\n')\nraise SystemExit(1)\n"


def attested_body(sha: str) -> str:
    """The body the pipeline writes: an attestation naming THIS head, and the five sections."""
    steps = [{"step": s, "status": "completed"}
             for s in ("intent", "rebase", "review", "test", "document", "lint", "push")]
    steps += [{"step": "pr", "status": "running"}, {"step": "ci", "status": "pending"}]
    payload = json.dumps({"head_sha": sha, "steps": steps})
    return f"<!-- fleet-attestation:v1 {payload} -->\n\n" + "\n".join(
        f"## {h}\nx\n" for h in ("Intent", "What Changed", "Risk Assessment", "Testing", "Pipeline")
    )


# --- §13: a forge that is not GitHub --------------------------------------------

# It answers the questions in scripts/lib/forge.py's header and knows nothing
# else — if it had to grow a field to keep the queue working, the seam would be
# in the wrong place. Deliberately shaped like the forge fleet does NOT run on:
# self-hosted with a port, `/-/merge_requests/<n>`, and told it cannot squash.
FAKE_FORGE = '''\
"""A forge that is not GitHub: files on disk, no network, no CLI."""

import json
import os
import re

import fleet_forge as fg

HOST = "forge.test:8443"
URL_RE = re.compile(r"^https://" + re.escape(HOST) + r"/(.+?)/-/merge_requests/(\\d+)$")
REMOTE_RE = re.compile(r"^https://" + re.escape(HOST) + r"/(.+?)(?:\\.git)?/?$")


def _dir():
    return os.environ["FAKE_FORGE_DIR"]


def _down():
    return os.path.exists(os.path.join(_dir(), "down"))


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _docs():
    crs = os.path.join(_dir(), "crs")
    return [_load(os.path.join(crs, n)) for n in sorted(os.listdir(crs)) if n.endswith(".json")]


class FakeForge(fg.Forge):
    name = "fake"
    hosts = (HOST,)

    @property
    def merge_methods(self):
        return tuple(_load(os.path.join(_dir(), "merge-methods.json")))

    def parse_change_url(self, url):
        m = URL_RE.match((url or "").strip())
        if not m:
            return None
        return fg.ChangeRef(fg.RepoId(HOST, m.group(1)), int(m.group(2)), m.group(0))

    def repo_from_remote(self, remote_url):
        m = REMOTE_RE.match((remote_url or "").strip())
        return fg.RepoId(HOST, m.group(1)) if m else None

    def _find(self, ref):
        if _down():
            return None, "the fake forge is unreachable"
        for d in _docs():
            if d["number"] == ref.number and d["repo"] == ref.repo.path:
                return d, ""
        return None, f"no change request {ref.number} on {ref.repo}"

    def get(self, ref):
        d, why = self._find(ref)
        return (None, why) if why else (self._change_request(d, ref.repo), "")

    def state(self, ref):
        d, why = self._find(ref)
        return (None, why) if why else (d.get("state", "open"), "")

    def open_change_requests(self, repo):
        if _down():
            return [], "the fake forge is unreachable"
        return [self._change_request(d, repo) for d in _docs()
                if d["repo"] == repo.path and d.get("state", "open") == "open"], ""

    def open_change_requests_in_checkout(self, path):
        repo = self.repo_from_remote(fg._git_remote(path))
        if repo is None:
            return [], "not a checkout of a fake-forge repository"
        return self.open_change_requests(repo)

    def _change_request(self, d, repo):
        n = d["number"]
        return fg.ChangeRequest(
            ref=fg.ChangeRef(repo, n, f"https://{HOST}/{repo.path}/-/merge_requests/{n}"),
            title=d.get("title", ""),
            state=d.get("state", "open"),
            body=d.get("body", ""),
            head_branch=d.get("head_branch", ""),
            base_branch=d.get("base_branch", "main"),
            head_sha=d.get("head_sha", ""),
            author=d.get("author", ""),
            mergeable=d.get("mergeable", "mergeable"),
            checks=[fg.Check(c[0], c[1]) for c in d.get("checks", [])],
            commits=[fg.Commit(c[0], c[1]) for c in d.get("commits", [])],
            head_is_ours=d.get("head_is_ours", True),
            head_location=d.get("head_location", ""),
        )

    def can_push(self, repo, login):
        if _down():
            return False, "the fake forge is unreachable"
        ok = os.path.exists(os.path.join(_dir(), "push", login))
        return ok, f"{login} {'may' if ok else 'may not'} push to {repo}"

    def parse_target_url(self, url):
        m = re.match(r"^https://" + re.escape(HOST) + r"/(.+?)/-/(merge_requests|issues)/(\\d+)$",
                     (url or "").strip())
        if not m:
            return None
        kind = "change" if m.group(2) == "merge_requests" else "issue"
        return fg.Target(fg.RepoId(HOST, m.group(1)), int(m.group(3)), kind, m.group(0))

    def parse_note_url(self, url):
        base, _, fragment = (url or "").strip().partition("#note_")
        target = self.parse_target_url(base)
        if target is None or not fragment.isdigit():
            return None
        return fg.NoteRef(target, int(fragment), url.strip())

    def note(self, ref):
        if _down():
            return None, "the fake forge is unreachable"
        path = os.path.join(_dir(), "notes", "%d.json" % ref.id)
        if not os.path.exists(path):
            return None, f"no note {ref.id} on {ref.target.repo}"
        d = _load(path)
        on = fg.Target(ref.target.repo, d["on"], d.get("kind", "change"), "")
        return fg.Note(ref=ref, author=d.get("author", ""), target=on), ""

    def whoami(self, host):
        if _down():
            return "", "the fake forge is unreachable"
        return "operator", ""

    def describe_merge(self, method, delete_branch):
        return f"fake forge: {method}" + (" and delete the branch" if delete_branch else "")

    def merge(self, cr, method, delete_branch):
        if method not in self.merge_methods:
            return False, f"this project forbids {method} merges"
        with open(os.path.join(_dir(), "merged.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{cr.number} {method}\\n")
        return True, f"{method}-merged on the fake forge"


def forges():
    return [FakeForge()]
'''


class FakeForgeStore:
    """The fake forge's files: change requests, notes, who may push, what merged."""

    def __init__(self, root: Path):
        self.root = root
        for d in ("crs", "push", "notes"):
            (root / d).mkdir(parents=True, exist_ok=True)
        write(root / "merged.log", "")
        self.merge_methods("squash")
        self.plugin = root / "forge_plugin.py"
        write(self.plugin, FAKE_FORGE)

    def merge_methods(self, *methods: str) -> None:
        write(self.root / "merge-methods.json", json.dumps(list(methods)) + "\n")

    def cr(self, n: int, **fields) -> None:
        """One change request, attested for its own head unless a field says otherwise."""
        sha = f"{n:040d}"
        doc = {"number": n, "repo": "acme/widgets", "state": "open", "body": attested_body(sha),
               "title": f"change {n}", "base_branch": "main", "head_sha": sha,
               "author": "operator", "mergeable": "mergeable", "checks": [["gate", "passed"]]}
        doc.update(fields)
        write(self.root / "crs" / f"{n}.json", json.dumps(doc))

    def note(self, note_id: int, on: int, author: str = "operator") -> None:
        write(self.root / "notes" / f"{note_id}.json", json.dumps({"author": author, "on": on}) + "\n")

    def may_push(self, login: str) -> None:
        write(self.root / "push" / login, "")

    def merged(self) -> list[str]:
        return (self.root / "merged.log").read_text(encoding="utf-8").splitlines()


# --- §14: GitLab, over recorded `glab` output -----------------------------------

# A `glab` that reads files instead of an instance. It knows only the verbs the
# adapter uses, and is deliberately literal about the two things recorded output
# taught: `--jq .state` prints a bare word, and a failure puts its reason on
# STDOUT as JSON with a decorated box on stderr. Its store is FAKE_GLAB_DIR.
FAKE_GLAB = r'''
import json
import os
import sys

D = os.environ["FAKE_GLAB_DIR"]
argv = sys.argv[1:]
with open(os.path.join(D, "calls.log"), "a", encoding="utf-8") as fh:
    fh.write(" ".join(argv) + "\n")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def flag(name, default=None):
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def emit(doc):
    sys.stdout.write(json.dumps(doc) + "\n")
    raise SystemExit(0)


def refuse(message, recorded=None):
    # A refusal is two streams, and which one carries the REASON is the thing
    # an adapter gets wrong. `recorded` replays the pair exactly as glab wrote it.
    if recorded and os.path.exists(recorded + ".json"):
        sys.stdout.write(read(recorded + ".json"))
        sys.stderr.write(read(recorded + ".stderr"))
        raise SystemExit(1)
    sys.stdout.write(json.dumps({"error": {"message": message}}) + "\n")
    sys.stderr.write("\n          \n   ERROR  \n          \n  %s\n\n" % message)
    raise SystemExit(1)


def load(number):
    path = os.path.join(D, "mrs", "%s.json" % number)
    return json.loads(read(path)) if os.path.exists(path) else None


def opened():
    mrs = os.path.join(D, "mrs")
    docs = [json.loads(read(os.path.join(mrs, n))) for n in sorted(os.listdir(mrs)) if n.endswith(".json")]
    return [d for d in docs if d.get("state") == "opened"]


if argv[:2] == ["mr", "view"]:
    doc = load(argv[2])
    if doc is None:
        refuse("failed to get merge request %s: 404 Not Found" % argv[2],
               recorded=os.path.join(D, "missing") if argv[2] == "999999" else None)
    if flag("--jq") == ".state":
        sys.stdout.write(str(doc.get("state") or "") + "\n")
        raise SystemExit(0)
    emit(doc)

if argv[:2] == ["mr", "list"]:
    emit([] if int(flag("--page", "1")) > 1 else opened())

if argv[:2] == ["mr", "merge"]:
    if os.path.exists(os.path.join(D, "merge-refused")):
        refuse("405 Method Not Allowed")
    with open(os.path.join(D, "merged.log"), "a", encoding="utf-8") as fh:
        fh.write(" ".join(argv) + "\n")
    sys.stdout.write("Merged!\n")
    raise SystemExit(0)

if argv[:2] == ["auth", "status"]:
    # The recording is replayed only when the store holds one, so a store
    # without it is a machine with NO GitLab configured. Exit 1 either way,
    # which is what glab does whenever any one instance has no token.
    served = os.path.join(D, "auth-status.stderr")
    if not os.path.exists(served):
        refuse("no GitLab instance is configured")
    sys.stderr.write(read(served))
    raise SystemExit(1)

if argv[:1] == ["api"]:
    path = argv[1].split("?")[0]
    if path.endswith("/commits"):
        name = "commits"
    elif "/notes/" in path:
        name = "note"
    elif path == "user":
        name = "user"
    elif "/members/all" in path:
        name = "members"
    elif path.count("/") == 1:
        name = "project"
    else:
        refuse("404 Not Found")
    served = os.path.join(D, "api", name + ".json")
    if not os.path.exists(served):
        refuse("404 Not Found")
    sys.stdout.write(read(served))
    raise SystemExit(0)

refuse("unknown command: %s" % " ".join(argv))
'''

# CONSTRUCTED, and labelled: `GET /projects/:id` and `/members/all` are behind
# authentication, so these carry the field names from GitLab's REST API
# documentation and values this test chooses. tests/fixtures/glab/README.md says so too.
PROJECT = {"id": 42, "path_with_namespace": "acme/group/widgets", "squash_option": "default_on"}
MEMBERS = [{"id": 7, "username": "operator", "access_level": 40}]


class GlabStore:
    """The fake `glab`'s files: merge requests, API answers, and its two logs."""

    def __init__(self, root: Path):
        self.root = root
        for d in ("mrs", "api"):
            (root / d).mkdir(parents=True, exist_ok=True)
        for log in ("calls.log", "merged.log"):
            write(root / log, "")
        self.project(squash_option="default_on")
        write(root / "api" / "members.json", json.dumps(MEMBERS) + "\n")

    def replay_recordings(self) -> None:
        """The recorded fork merge request under its own number, and the recorded refusal
        for merge request 999999, which is the number it was recorded against."""
        for src, dest in (("mr-view.json", "mrs/3877.json"), ("mr-commits.json", "api/commits.json"),
                          ("mr-view-missing.json", "missing.json"), ("mr-view-missing.stderr", "missing.stderr")):
            write(self.root / dest, (GLAB_FIXTURES / src).read_text(encoding="utf-8"))

    def serve_auth_status(self) -> None:
        write(self.root / "auth-status.stderr", (GLAB_FIXTURES / "auth-status.stderr").read_text(encoding="utf-8"))

    def project(self, **fields) -> None:
        write(self.root / "api" / "project.json", json.dumps({**PROJECT, **fields}) + "\n")

    def api(self, name: str, doc) -> None:
        write(self.root / "api" / f"{name}.json", json.dumps(doc))

    def mr(self, number: int, **fields) -> None:
        """A merge request DERIVED FROM THE RECORDED ONE: the recorded object with named
        fields overwritten, so each keeps the real shape and only the facts under test
        are this test's invention."""
        doc = json.loads((GLAB_FIXTURES / "mr-view.json").read_text(encoding="utf-8"))
        sha = f"{number:040d}"
        doc.update({
            "iid": number, "id": 900000 + number,
            "web_url": f"https://gitlab.example.com/acme/group/widgets/-/merge_requests/{number}",
            "project_id": 42, "source_project_id": 42, "target_project_id": 42,
            "state": "opened", "draft": False, "sha": sha, "title": f"change {number}",
            "target_branch": "main", "has_conflicts": False, "detailed_merge_status": "mergeable",
            "author": {"id": 7, "username": "operator", "name": "operator", "state": "active"},
            "description": attested_body(sha),
            "head_pipeline": {"id": 5000 + number, "name": "", "sha": sha, "status": "success"},
        })
        doc.update(fields)
        write(self.root / "mrs" / f"{number}.json", json.dumps(doc))

    def set_state(self, number: int, state: str) -> None:
        path = self.root / "mrs" / f"{number}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["state"] = state
        write(path, json.dumps(doc))

    def calls(self) -> str:
        return (self.root / "calls.log").read_text(encoding="utf-8")

    def merged(self) -> list[str]:
        return (self.root / "merged.log").read_text(encoding="utf-8").splitlines()
