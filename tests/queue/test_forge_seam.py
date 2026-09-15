"""§13, THE SEAM: the whole queue driven by a forge that is not GitHub.

A seam with one implementation is a claim. This is the second implementation: a
forge with no network, no `gh` and no GitHub anywhere in it, loaded through
FLEET_FORGE_PLUGINS, that `collect`, `reap`'s landing check and `shepherd` are
driven all the way through.

It is also the regression test. `gh` here is a TRIPWIRE, not a stub, so any code
that reaches around `scripts/lib/forge.py` and runs `gh` directly shows up by
name instead of quietly working on the operator's machine and nowhere else.

Beyond "the calls go through the interface" it proves: a self-hosted host with
a PORT round-trips (`forge.test:8443/acme/widgets` is not `github.com/acme/widgets`);
a `/-/merge_requests/<n>` URL is a change request; AUTO_MERGE_REPOS is matched
host-qualified; a repository is discovered from a checkout's `origin` through
the same seam; and a forge that cannot perform fleet's merge method SAYS SO.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from kit_forges import FakeForgeStore, tripwire
from queuekit import ok, result

from harness import Run, expect, git, refute
from harness import run_queue as q

S = "aaaaaaaa-0000-0000-0000-000000000001"
MR = "https://forge.test:8443/acme/widgets/-/merge_requests/"
TASKS = (("01", "landed", 201), ("02", "conflicting", 202), ("03", "green", 203),
         ("04", "foreign", 204), ("06", "cancelled-check", 206))


class Seam:
    def __init__(self, root: Path, store: FakeForgeStore, repo: Path):
        self.root, self.store, self.repo = root, store, repo
        self.queue = root / "queue-fake"
        self.topic = ""

    def q(self, *args: str, **env: str | None) -> Run:
        env = {"FLEET_QUEUE_DIR": str(self.queue), "FLEET_FORGE_PLUGINS": str(self.store.plugin),
               "FLEET_AUTO_MERGE_REPOS": "forge.test:8443/acme/widgets",
               "FAKE_FORGE_DIR": str(self.store.root), **env}
        return q(*args, **env)

    def task(self, tid: str) -> Path:
        return self.queue / self.topic / tid


@pytest.fixture
def seam(tmp_path, stubs) -> Seam:
    stubs.tool("gh", tripwire("gh: nothing in the fake-forge section may reach GitHub"))
    store = FakeForgeStore(tmp_path / "fake-forge")
    repo = tmp_path / "fake-forge" / "repo"
    git("init", "-q", "-b", "main", str(repo))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=repo)
    # The repository this checkout belongs to, discovered through the seam rather
    # than recorded anywhere: no task below names it.
    git("remote", "add", "origin", "https://forge.test:8443/acme/widgets.git", cwd=repo)
    store.may_push("operator")  # `stranger` has no file, so cannot

    s = Seam(tmp_path, store, repo)
    s.topic = ok(s.q("topic", "add", "on-another-forge", "--title", "Work on a forge that is not GitHub",
                     "--prompt", "fleet must not assume GitHub")).stdout.strip()
    for n, slug, num in TASKS:
        ok(s.q("add", s.topic, slug, "--title", f"A change that is {slug}", "--repo", str(repo),
               "--branch", f"fix/{slug}", "--number", n))
        result(s.task(f"{n}-{slug}"), "shipped", "Shipped it.", f"{MR}{num}")
    # Each branch only appears once its task is `add`ed: `add` refuses a branch that
    # already exists, and each task stands in for one whose worker pushed its branch.
    for _, slug, _ in TASKS:
        git("branch", f"fix/{slug}", cwd=repo)

    store.cr(201, head_branch="fix/landed")
    store.cr(202, head_branch="fix/conflicting", mergeable="conflicting")
    store.cr(203, head_branch="fix/green")
    store.cr(204, head_branch="fix/foreign", head_is_ours=False, head_location="a stranger's fork")
    store.cr(206, head_branch="fix/cancelled-check", checks=[["gate", "cancelled"]])
    yield s
    # The fixer shepherd dispatches gets a real worktree; take it back off the repo.
    worktrees = Path(os.environ["XDG_DATA_HOME"]) / "fleet" / "worktrees"
    for wt in worktrees.glob(f"{s.topic}__*"):
        git("worktree", "remove", "--force", str(wt), cwd=repo)


def no_gh(stubs) -> None:
    assert stubs.calls("gh") == [], "a code path ran `gh` while a different forge was configured"


def test_collect_reads_the_change_request_through_the_seam(seam, stubs):
    """13a. A change request nobody can read is `unknown`, never `missing` — the
    fourth word has to survive the seam, or an unreachable forge holds tasks open
    on evidence nobody has."""
    stubs.session_is(S, "idle")
    ok(seam.q("attach", f"{seam.topic}/01-landed", S))

    out = seam.q("collect").out
    # A publish claim verified on a forge that is not GitHub, read off a /-/merge_requests/ URL.
    expect(out, "01-landed", "merge_requests/201")
    refute(out, "reaped")  # its change request is open

    ok(seam.q("add", seam.topic, "unreadable", "--title", "One the forge cannot answer for",
              "--repo", str(seam.repo), "--branch", "fix/unreadable", "--number", "05"))
    result(seam.task("05-unreadable"), "shipped", "Shipped it; the forge cannot be asked about it from here.",
           f"{MR}999")
    # Degrades to unchecked, and says what the forge said.
    expect(seam.q("collect").out, "unchecked", "no change request 999")
    no_gh(stubs)


def test_the_landing_check_asks_the_same_seam(seam, stubs):
    """13b."""
    stubs.session_is(S, "idle")
    ok(seam.q("attach", f"{seam.topic}/01-landed", S))
    ok(seam.q("collect"))

    refute(seam.q("reap", "--dry-run").out, "would be landed")

    seam.store.cr(201, head_branch="fix/landed", state="merged")
    # A merge on the fake forge lands the task and releases the session that produced it.
    expect(seam.q("reap").out, "landed", "reaped")
    expect("\n".join(stubs.calls("thurbox-cli", "session delete")), S)
    no_gh(stubs)


def test_shepherd_all_the_way_through(seam, stubs):
    """13c. A CANCELLED check is not a FAILED one: the shepherd's long-standing
    reading, kept through the seam now that fleet-status reads the same field."""
    ok(seam.q("collect"))

    dry = seam.q("shepherd", "--topic", seam.topic, "--dry-run").out
    # The self-hosted repository, port and all, and the merge in the fake forge's own words.
    expect(dry, "acme/widgets on forge.test:8443", "fake forge: squash")
    assert seam.store.merged() == [], "a dry run merged something on the fake forge"

    out = seam.q("shepherd", "--topic", seam.topic).out
    assert "203 squash" in seam.store.merged(), out
    # A conflicting one gets a fixer, in the base branch's own terms, and it is dispatched.
    expect(out, "conflicts with main", "dispatched:")
    # One whose head is not ours is left alone, named as where the forge said it lives.
    expect(out, "left-alone", "a stranger's fork")
    assert not [m for m in seam.store.merged() if m.startswith("204 ")], "a stranger's change request merged"
    expect(out, "undetermined: checks still running: gate")
    assert not [m for m in seam.store.merged() if m.startswith("206")]
    no_gh(stubs)


def test_a_forge_that_cannot_do_fleets_merge_method_says_so(seam, stubs):
    """13d. Fleet merges by squash because that is the only method its own remotes
    allow, and a project on another forge can forbid exactly that. The refusal has
    to be said BEFORE the merge rather than after one that quietly used another."""
    ok(seam.q("collect"))
    seam.store.merge_methods("merge")
    seam.store.cr(205, head_branch="fix/green")

    out = seam.q("shepherd", "--topic", seam.topic).out
    expect(out, "cannot merge by squash")
    assert seam.store.merged() == [], out
    no_gh(stubs)


def test_the_allowlist_is_host_qualified_and_a_bare_slug_is_no_match(seam, stubs):
    """13e."""
    ok(seam.q("collect"))
    out = seam.q("shepherd", "--topic", seam.topic, "--dry-run", FLEET_AUTO_MERGE_REPOS="acme/widgets").out
    expect(out, "must name its forge")
    refute(out, "would-merge")
    no_gh(stubs)


def test_a_note_goes_through_the_seam_and_never_links_a_pull_request(seam, stubs):
    """13g. A `note` task's artifact is a review or comment ON a change request, and
    its URL names that change request. Read as the task's OWN pull request it would
    hand the shepherd a method that asks for no attestation, and an unattested pull
    request would merge because somebody reviewed it."""
    seam.store.note(1, on=207)
    seam.store.cr(207, head_branch="someone/else", body="Opened by hand.")
    ok(seam.q("add", seam.topic, "review-207", "--title", "Review change 207", "--repo", str(seam.repo),
              "--branch", "review/207", "--number", "07", "--publish", "note", "--target", f"{MR}207"))
    result(seam.task("07-review-207"), "shipped", "Reviewed it.", f"{MR}207#note_1")

    expect(seam.q("collect").out, "[publish verified: note]")
    seam.q("shepherd", "--topic", seam.topic)
    assert not [m for m in seam.store.merged() if m.startswith("207 ")], "merged on a review's account"
    no_gh(stubs)
