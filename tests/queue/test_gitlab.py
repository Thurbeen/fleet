"""§14, GITLAB: the second REAL adapter, over recorded `glab` output.

§13 proves the seam with a forge that exists only in its tests. This proves the
adapter fleet actually ships for GitLab, and it is a different claim: the fake
forge answers whatever fleet asks, while `glab` answers what GitLab decided to
answer, in GitLab's own words and shapes.

So the fixtures matter more than the code. `tests/fixtures/glab/` is real `glab`
1.117.0 output, and its README says which command produced each file and which
two answers are behind authentication and therefore CONSTRUCTED rather than
recorded. A fake `glab` replays them; nothing reaches a network, and `gh` is a
tripwire, because a GitLab merge request must never be asked about with `gh`.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from kit_forges import GLAB_FIXTURES, FAKE_GLAB, GlabStore, tripwire
from queuekit import ok, result

from harness import Run, expect, git, lib, queue_module, refute, run, write
from harness import run_queue as q

MR = "https://gitlab.example.com/acme/group/widgets/-/merge_requests/"
DEFAULT_HOSTS = ("gitlab.com", "www.gitlab.com")


@pytest.fixture
def glab(tmp_path, stubs, monkeypatch) -> GlabStore:
    store = GlabStore(tmp_path / "gitlab")
    stubs.tool("glab", FAKE_GLAB)
    stubs.tool("gh", tripwire("gh: a GitLab merge request must never be asked about with gh"))
    monkeypatch.setenv("FAKE_GLAB_DIR", str(store.root))
    return store


@pytest.fixture
def forge(glab, monkeypatch):
    monkeypatch.delenv("GITLAB_HOST", raising=False)
    mod = lib("forge.py")
    mod.reset()
    return mod


@pytest.fixture
def glrepo(tmp_path) -> Path:
    repo = tmp_path / "gitlab-repo"
    git("init", "-q", "-b", "main", str(repo))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=repo)
    git("remote", "add", "origin", "https://gitlab.example.com/acme/group/widgets.git", cwd=repo)
    yield repo
    worktrees = Path(os.environ["XDG_DATA_HOME"]) / "fleet" / "worktrees"
    for wt in worktrees.glob("*__*"):
        git("worktree", "remove", "--force", str(wt), cwd=repo)


# --- 14a. the recorded shapes parse, and the fork is read as not ours -------------


def test_the_recorded_merge_request_parses_and_a_fork_is_not_ours(forge, glab):
    """A fork's merge request is read as NOT ours, commits come back oldest-first out
    of a newest-first answer, and glab's two-stream error is read from the stream
    that carries the reason."""
    glab.replay_recordings()
    gl = forge.GitLabForge()

    ref = gl.parse_change_url("https://gitlab.com/gitlab-org/cli/-/merge_requests/3877")
    assert ref is not None, "a /-/merge_requests/ URL on gitlab.com is a change request"
    cr, why = gl.get(ref)
    assert why == "", "glab answers for it"
    assert cr.head_sha == "c152195ba6b110064690fca331b186c55a674fdf"
    assert cr.head_branch == "patch-1"
    assert cr.base_branch == "main"
    assert cr.state == "open", "GitLab's `opened` is fleet's `open`"
    assert cr.head_is_ours is False, "a merge request from a FORK is not ours"
    assert "another project on gitlab.com" in cr.head_location
    assert [c.verdict for c in cr.checks] == ["failed"], "its failed pipeline is one failed check"
    assert cr.mergeable == "", "an undocumented detailed_merge_status is not read as mergeable"
    headlines = [c.headline for c in cr.commits]
    assert headlines[0] == "chore(lint): add comment volume and overlap scripts"
    assert headlines[-1] == "refactor: fix gocritic findings and delete comments that restate the code"

    # The recorded error: the reason is on stdout, and stderr's first line is a box.
    gone, why = gl.get(forge.ChangeRef(ref.repo, 999999, "https://gitlab.com/gitlab-org/cli/-/merge_requests/999999"))
    assert gone is None, "a merge request that is not there is a reason, not an exception"
    assert "404 Not Found" in why
    assert "ERROR" not in why


def test_hosts_a_self_hosted_instance_and_its_subgroup_paths(forge, glab, monkeypatch):
    """A self-hosted instance round-trips: `GITLAB_HOST`, a subgroup path, and a
    checkout's origin in every spelling git accepts."""
    gl = forge.GitLabForge()
    assert gl.parse_change_url("https://github.com/Thurbeen/fleet/pull/1") is None
    assert gl.parse_change_url("https://gitlab.com/project/-/merge_requests/1") is None, \
        "a single-segment path, which GitLab has no such thing as"
    assert gl.parse_change_url(f"{MR}9") is None, "an unconfigured self-hosted host is not ours"

    monkeypatch.setenv("GITLAB_HOST", "https://gitlab.example.com/")
    selfhosted = forge.GitLabForge()
    ref = selfhosted.parse_change_url(f"{MR}301")
    assert ref is not None, "GITLAB_HOST configures a self-hosted instance, scheme and all"
    assert ref.repo.path == "acme/group/widgets"
    assert ref.repo.owner == "acme"
    for remote in ("https://gitlab.example.com/acme/group/widgets.git",
                   "git@gitlab.example.com:acme/group/widgets.git",
                   "ssh://git@gitlab.example.com/acme/group/widgets"):
        assert selfhosted.repo_from_remote(remote) == forge.RepoId("gitlab.example.com", "acme/group/widgets"), remote


def test_a_pipeline_is_a_check_only_for_the_head_it_ran_on(forge, glab, monkeypatch):
    """Each case is written into the fake CLI's store and read back through `get`,
    so the interface under test is the one the queue calls."""
    monkeypatch.setenv("GITLAB_HOST", "https://gitlab.example.com/")
    selfhosted = forge.GitLabForge()
    ref = selfhosted.parse_change_url(f"{MR}301")
    recorded = json.loads((GLAB_FIXTURES / "mr-view.json").read_text(encoding="utf-8"))

    def verdicts(pipeline):
        doc = dict(recorded, iid=401, web_url=f"{MR}401", source_project_id=42, target_project_id=42,
                   head_pipeline=pipeline)
        write(glab.root / "mrs" / "401.json", json.dumps(doc))
        got, why = selfhosted.get(forge.ChangeRef(ref.repo, 401, doc["web_url"]))
        assert why == ""
        return [c.verdict for c in got.checks]

    head = recorded["sha"]
    assert verdicts({"id": 1, "sha": "0" * 40, "status": "success"}) == [], \
        "a pipeline for a commit that is no longer the head is no check at all"
    assert verdicts(None) == [], "a merge request with no pipeline at all is no check either"
    for said, want in (("success", "passed"), ("skipped", "passed"), ("failed", "failed"),
                       ("canceled", "cancelled"), ("running", "pending"),
                       ("manual", "pending"), ("created", "pending")):
        assert verdicts({"id": 1, "sha": head, "status": said}) == [want], f"pipeline {said}"


def test_a_note_is_asked_of_the_instance_its_url_names(forge, glab, monkeypatch):
    """A comment on a merge request or an issue, and the account glab is logged in
    as THERE. The note and user answers are CONSTRUCTED from GitLab's REST
    documentation, behind authentication like the project and members ones."""
    monkeypatch.setenv("GITLAB_HOST", "https://gitlab.example.com/")
    selfhosted = forge.GitLabForge()
    glab.api("note", {"id": 77, "author": {"id": 7, "username": "operator"},
                      "noteable_type": "MergeRequest", "noteable_iid": 301, "body": "Reviewed."})
    glab.api("user", {"id": 7, "username": "operator"})

    nref = selfhosted.parse_note_url(f"{MR}301#note_77")
    assert nref is not None, "a #note_ URL on a merge request is a note"
    assert (nref.target.number, nref.target.kind) == (301, "change")
    note, why = selfhosted.note(nref)
    assert why == ""
    assert note.author == "operator"
    assert (note.target.number, note.target.kind) == (301, "change")
    assert selfhosted.whoami("gitlab.example.com") == ("operator", "")
    iref = selfhosted.parse_note_url("https://gitlab.example.com/acme/group/widgets/-/issues/12#note_78")
    assert (iref.target.number, iref.target.kind) == (12, "issue")
    assert selfhosted.parse_target_url("https://gitlab.example.com/acme/group/widgets/-/issues/12").kind == "issue"
    expect(glab.calls(), "api user --hostname gitlab.example.com")


# --- 14b. the whole queue, driven through the GitLab adapter ----------------------


class GitLabQueue:
    def __init__(self, root: Path, store: GlabStore, repo: Path, **env: str | None):
        self.queue, self.store, self.repo = root, store, repo
        self.env = {"FLEET_QUEUE_DIR": str(root), "FAKE_GLAB_DIR": str(store.root), **env}
        self.topic = ""

    def q(self, *args: str) -> Run:
        return q(*args, **self.env)

    def shipped(self, n: str, slug: str, number: int, branch: str | None = None) -> None:
        ok(self.q("add", self.topic, slug, "--title", f"A change that is {slug}", "--repo", str(self.repo),
                  "--branch", branch or f"fix/{slug}", "--number", n))
        result(self.queue / self.topic / f"{n}-{slug}", "shipped", "Shipped it.", f"{MR}{number}")


@pytest.fixture
def gitlab_queue(tmp_path, glab, glrepo, stubs) -> GitLabQueue:
    gq = GitLabQueue(tmp_path / "queue-gitlab", glab, glrepo, GITLAB_HOST="gitlab.example.com",
                     FLEET_AUTO_MERGE_REPOS="gitlab.example.com/acme/group/widgets")
    gq.topic = ok(gq.q("topic", "add", "on-gitlab", "--title", "Work on a self-hosted GitLab",
                       "--prompt", "fleet must work on GitLab too")).stdout.strip()
    for n, slug, number in (("01", "landed", 301), ("02", "conflicting", 302),
                            ("03", "green", 303), ("04", "foreign", 304)):
        gq.shipped(n, slug, number)
    for slug in ("landed", "conflicting", "green", "foreign"):
        git("branch", f"fix/{slug}", cwd=glrepo)
    glab.mr(301, source_branch="fix/landed")
    glab.mr(302, source_branch="fix/conflicting", has_conflicts=True, detailed_merge_status="conflict")
    glab.mr(303, source_branch="fix/green")
    glab.mr(304, source_branch="fix/foreign", source_project_id=99)
    return gq


def test_the_whole_queue_runs_through_the_gitlab_adapter(gitlab_queue, glab, stubs):
    gq = gitlab_queue
    sid = "bbbbbbbb-0000-0000-0000-000000000001"
    stubs.session_is(sid, "idle")
    ok(gq.q("attach", f"{gq.topic}/01-landed", sid))

    out = gq.q("collect").out
    expect(out, "01-landed", "merge_requests/301")
    refute(out, "reaped")

    glab.set_state(301, "merged")
    expect(gq.q("reap").out, "landed", "reaped")

    dry = gq.q("shepherd", "--topic", gq.topic, "--dry-run").out
    expect(dry, "acme/group/widgets on gitlab.example.com", "glab mr merge --yes --auto-merge=false --squash")

    out = gq.q("shepherd", "--topic", gq.topic).out
    merged = "\n".join(glab.merged())
    expect(merged, "mr merge 303")
    # The merge names the exact head it checked, so a race cannot slip in.
    expect(merged, "--sha 0000000000000000000000000000000000000303")
    expect(out, "conflicts with main", "left-alone")
    refute(merged, "mr merge 304")

    # Every call carried the host: a slug would have reached gitlab.com, and
    # `RepoId` is host plus path for this reason.
    refute(glab.calls(), "gitlab.com")
    expect(glab.calls(), "-R https://gitlab.example.com/acme/group/widgets")
    assert stubs.calls("gh") == [], "a code path ran `gh` against a GitLab merge request"


def test_a_project_that_forbids_squash_is_a_refusal_and_not_a_crash(gitlab_queue, glab, stubs):
    """14c. GitLab's `squash` is a flag on the merge, and `squash_option: never` is
    the PROJECT setting that forbids it: §13d's mismatch, per project, and still a
    sentence fleet records rather than a merge by whatever method is allowed."""
    gq = gitlab_queue
    ok(gq.q("collect"))
    glab.project(squash_option="never")
    glab.mr(305, source_branch="fix/green")

    out = gq.q("shepherd", "--topic", gq.topic).out
    expect(out, "squash_option: never")
    assert glab.merged() == [], out
    assert stubs.calls("gh") == []


# --- 14d. the remote-host probe asks the REPOSITORY's forge, not github.com -------

PROBE_GIT = """
import os, sys
from pathlib import Path
origin = Path(os.environ["FLEET_STUB_ROOT"]) / "probe-origin"
if not origin.exists():
    raise SystemExit(1)
sys.stdout.write(origin.read_text(encoding="utf-8"))
"""

PROBE_SSH = """
import os, sys
from pathlib import Path
banner = Path(os.environ["FLEET_STUB_ROOT"]) / "probe-banner"
if banner.exists():
    sys.stdout.write(banner.read_text(encoding="utf-8"))
raise SystemExit(1)
"""


@pytest.mark.skipif(os.name == "nt" or not shutil.which("sh"),
                    reason="the probe is POSIX shell that runs on a remote POSIX host; this machine has no sh")
def test_the_remote_host_probe_asks_the_repositorys_own_forge(stubs, tmp_path):
    """The credential probe is plain shell run on somebody else's machine, so it is
    run exactly as that machine runs it, with `git` and `ssh` stood in for. It used
    to name github.com flatly, which passes on a host that cannot reach the GitLab
    instance the checkout actually pushes to."""
    probe = tmp_path / "probe.sh"
    write(probe, queue_module("sys.stdout.write(q.forge_probe('/srv/code/app'))\n"))
    stubs.tool("git", PROBE_GIT)
    stubs.tool("ssh", PROBE_SSH)

    def probe_says(origin: str, banner: str) -> str:
        (stubs.root / "calls.log").unlink(missing_ok=True)
        write(stubs.root / "probe-origin", origin + "\n")
        write(stubs.root / "probe-banner", banner + "\n")
        return run(["sh", str(probe)]).out

    expect(probe_says("git@gitlab.example.com:acme/group/widgets.git", "Welcome to GitLab, @operator!"),
           "gitlab.example.com with an ssh key")
    expect("\n".join(stubs.calls("ssh")), "git@gitlab.example.com")
    probe_says("ssh://git@gitlab.example.com:2222/acme/widgets.git", "Welcome to GitLab, @operator!")
    expect("\n".join(stubs.calls("ssh")), "-p 2222")
    expect(probe_says("git@github.com:Thurbeen/fleet.git", "Hi operator! You've successfully authenticated"),
           "github.com with an ssh key")
    expect(probe_says("", "Welcome to GitLab, @operator!"), "has no readable")


# --- 14e. which hosts are GitLab: DISCOVERED, not waited for ----------------------
#
# Everything above sets `GITLAB_HOST`, and that is what hid the defect: nothing on
# a real machine exports it. With it unset, a merge request on a self-hosted
# instance was on no configured forge, so `shepherd` never listed it, `collect`
# could not verify a publish there, and `reap` never landed the task — its
# session and worktree leaked once per task. `glab auth status` already prints
# every instance the operator logged their CLI in to; `forge.configured_hosts`
# reads it, and tests/fixtures/glab/auth-status.stderr is that report, recorded
# and stripped of the operator's own names.


def test_a_self_hosted_instance_glab_holds_is_ours_with_no_gitlab_host(forge, glab):
    glab.serve_auth_status()
    found = forge.GitLabForge()
    assert forge.configured_hosts("glab") == ["gitlab.com", "gitlab.example.com"]
    assert found.owns_host("gitlab.example.com") is True
    assert found.parse_change_url(f"{MR}301") is not None
    assert found.owns_host("gitlab.nowhere.example") is False, "an instance nothing here holds"


def test_gitlab_host_still_decides_when_it_is_set(forge, glab, monkeypatch):
    glab.serve_auth_status()
    monkeypatch.setenv("GITLAB_HOST", "https://gitlab.other.example/")
    named = forge.GitLabForge()
    assert named.owns_host("gitlab.other.example") is True, "scheme and all"
    assert named.owns_host("gitlab.example.com") is False, "an instance glab holds is not ours while it is set"


def test_discovery_is_never_a_requirement(forge, stubs, monkeypatch, tmp_path):
    """No glab, a configuration it cannot read, or a report that is no host list
    each leave the adapter exactly where it was: gitlab.com and nothing else."""
    empty = tmp_path / "no-cli"
    empty.mkdir()
    with monkeypatch.context() as m:
        m.setenv("PATH", str(empty))
        forge.reset()
        assert forge.GitLabForge().hosts == DEFAULT_HOSTS, "a machine with no glab at all"

    stubs.tool("glab", "import sys\nsys.stderr.write('failed to parse config.yml: yaml: line 3: "
                       "could not find expected key\\n')\nraise SystemExit(1)\n")
    forge.reset()
    assert forge.GitLabForge().hosts == DEFAULT_HOSTS, "a configuration glab cannot read"

    stubs.tool("glab", "print('Logged in somewhere, probably')\n")
    forge.reset()
    assert forge.GitLabForge().hosts == DEFAULT_HOSTS, "a glab that answers no host list"


def test_a_glab_too_old_for_all_is_asked_again_without_it(forge, stubs):
    """An old `glab` refuses the whole command over `--all`; the bare form answers
    the same on a machine with no git context. Without the second try an older
    CLI discovers nothing."""
    stubs.tool("glab", f"""
import sys
if "--all" in sys.argv:
    sys.stderr.write("unknown flag: --all\\n")
    raise SystemExit(1)
with open({str(GLAB_FIXTURES / "auth-status.stderr")!r}, encoding="utf-8") as fh:
    sys.stderr.write(fh.read())
raise SystemExit(1)
""")
    forge.reset()
    assert forge.GitLabForge().owns_host("gitlab.example.com") is True


def test_the_whole_loop_runs_on_a_discovered_host(tmp_path, glab, glrepo, stubs):
    """With no GITLAB_HOST anywhere. The merge set is host-qualified and discovery
    adds nothing to it: a green, attested, mergeable merge request opened by
    someone who can push is still only REPORTED on a discovered host."""
    glab.serve_auth_status()
    gq = GitLabQueue(tmp_path / "queue-discovered", glab, glrepo, GITLAB_HOST=None)
    gq.topic = ok(gq.q("topic", "add", "discovered", "--title", "Work on an instance glab already holds",
                       "--prompt", "no GITLAB_HOST is exported anywhere")).stdout.strip()
    gq.shipped("01", "shipped", 306, branch="fix/discovered")
    glab.mr(306, source_branch="fix/discovered")
    sid = "cccccccc-0000-0000-0000-000000000001"
    stubs.session_is(sid, "idle")
    ok(gq.q("attach", f"{gq.topic}/01-shipped", sid))

    # `publish verified` is printed only when the check PASSED, which needs a forge
    # that owns the host; undiscovered, it degrades to `publish unchecked`.
    out = gq.q("collect").out
    expect(out, "publish verified")
    refute(out, "publish unchecked", "reaped")

    out = gq.q("shepherd").out
    expect(out, "acme/group/widgets on gitlab.example.com",
           "fleet does not merge in acme/group/widgets on gitlab.example.com")
    limited = "\n".join(line for line in out.splitlines() if "Merging is limited to" in line)
    expect(limited, "Merging is limited to")
    refute(limited, "gitlab.example.com")
    assert glab.merged() == [], out

    glab.set_state(306, "merged")
    # Reap lands it, and releases the session that used to leak with it.
    expect(gq.q("reap").out, "landed", "reaped")
    assert stubs.calls("gh") == [], "a code path ran `gh` while the host came from glab"
