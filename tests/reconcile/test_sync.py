"""It keeps the checkout current, and tells a lead it left behind — once.

`sync-checkout` used to run only at SessionStart, and one lead conversation
lasted sixteen days: a fix merged on origin sat unpulled on the control plane
for five of them, while the lead kept tripping over the bug it fixed. So the
loop fast-forwards the checkout on a clock of its own, through the same
`sync-checkout` and every refusal it already has, and when what arrived needs
a hand — new instructions, a manifest to re-render, its own code — it tells the
lead in one line, by the same rules as the ready notice.

Every case runs against a throwaway origin on disk, so nothing reaches a network.
"""

from __future__ import annotations

from pathlib import Path

from harness import expect, git, write
from reconcilekit import wait_for


def checkout(root: Path) -> Path:
    """A bare origin and a clone of it on `main`, as the control plane is."""
    origin, work = root / "origin.git", root / "work"
    root.mkdir(parents=True, exist_ok=True)
    git("init", "--quiet", "--bare", "--initial-branch=main", str(origin), cwd=root)
    git("clone", "--quiet", str(origin), str(work), cwd=root)
    write(work / "README.md", "seed\n")
    git("add", "README.md", cwd=work)
    git("commit", "--quiet", "-m", "seed", cwd=work)
    git("push", "--quiet", "origin", "main", cwd=work)
    git("remote", "set-head", "origin", "main", cwd=work)
    git("clone", "--quiet", str(origin), str(root / "upstream"), cwd=root)
    return work


def land(root: Path, file: str) -> str:
    """A commit on origin touching `file`, as a merged pull request is; its sha."""
    up = root / "upstream"
    git("pull", "--quiet", "--ff-only", cwd=up)
    path = up / file
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("incoming\n")
    git("add", file, cwd=up)
    git("commit", "--quiet", "-m", f"incoming: {file}", cwd=up)
    git("push", "--quiet", "origin", "main", cwd=up)
    return head(up)


def head(work: Path) -> str:
    return git("rev-parse", "HEAD", cwd=work).strip()


def syncing(monkeypatch, tmp_path: Path) -> Path:
    work = checkout(tmp_path / "plane")
    monkeypatch.setenv("FLEET_RECONCILE_SYNC_DIR", str(work))
    monkeypatch.setenv("FLEET_RECONCILE_SYNC_SECS", "2")
    return work


def passes(recon, n: int) -> None:
    start = recon.count("collect")
    assert wait_for(lambda: recon.count("collect") >= start + n, 30)


def test_new_instructions_are_pulled_and_the_lead_is_told_once(recon, stubs, monkeypatch, tmp_path):
    def sends() -> list[str]:
        return [s for s in stubs.calls("thurbox-cli", "session send") if "update-fleet" in s]

    work = syncing(monkeypatch, tmp_path)
    before = head(work)
    after = land(tmp_path / "plane", "FLEET.md")

    recon("ensure")
    assert wait_for(lambda: head(work) == after), f"the checkout was never fast-forwarded\n{recon.log()}"
    assert wait_for(lambda: len(sends()) >= 1), f"the stale lead was never told\n{recon.log()}"
    told = sends()[0]
    expect(told, "FLEET.md", "restart-lead", before[:7], "/update-fleet")
    assert "\n" not in told, "the notice is one line, not the sync's report"

    passes(recon, 3)
    assert len(sends()) == 1, "it is said once, not once per pass"

    quiet = land(tmp_path / "plane", "docs/unrelated.md")
    assert wait_for(lambda: head(work) == quiet), recon.log()
    passes(recon, 2)
    assert len(sends()) == 1, "a sync that needs no hand tells nobody"

    land(tmp_path / "plane", ".agents/skills/fleet-queue/SKILL.md")
    assert wait_for(lambda: len(sends()) >= 2), "new instructions after the last notice are news again"
    expect(sends()[-1], ".agents/skills/fleet-queue/SKILL.md")


def test_a_lead_mid_turn_finds_the_notice_in_its_inbox_and_is_told_once_at_rest(recon, stubs, monkeypatch, tmp_path):
    """`--no-wake` only enqueues: nothing delivers it. So the inbox note is where
    a busy lead can look, and the notice still waits to be typed once it is at
    rest — posted once, typed once, then forgotten."""
    def typed() -> list[str]:
        return [s for s in stubs.calls("thurbox-cli", "session send") if "update-fleet" in s]

    work = syncing(monkeypatch, tmp_path)
    recon.lead("working")
    after = land(tmp_path / "plane", "scripts/lib/reconcile.py")

    recon("ensure")
    assert wait_for(lambda: head(work) == after), recon.log()
    assert wait_for(lambda: stubs.inbox("lead-uuid")), f"the busy lead's inbox stayed empty\n{recon.log()}"
    note = stubs.inbox("lead-uuid")[0]
    expect(note["body"], "restart-reconciler", "scripts/lib/reconcile.py", "/update-fleet")
    assert not note["woke"], "a note to a lead mid-turn must not wake it"
    passes(recon, 2)
    assert len(stubs.inbox("lead-uuid")) == 1 and not typed(), "posted once, and nothing typed mid-turn"

    recon.lead("idle")
    assert wait_for(lambda: typed()), f"the notice was posted and then dropped\n{recon.log()}"
    expect(typed()[0], "restart-reconciler", "/update-fleet")
    passes(recon, 3)
    assert len(typed()) == 1 and len(stubs.inbox("lead-uuid")) == 1, "typed once, never posted again"


def test_a_dirty_tree_is_left_alone_and_said_once(recon, stubs, monkeypatch, tmp_path):
    """Every refusal `sync-checkout` has is the loop's too: an operator trying a
    worker's files live in the checkout is a normal state, not one to undo."""
    work = syncing(monkeypatch, tmp_path)
    before = head(work)
    land(tmp_path / "plane", "AGENTS.md")
    with open(work / "README.md", "a", encoding="utf-8", newline="\n") as fh:
        fh.write("trying a worker's change live\n")

    recon("ensure")
    assert wait_for(lambda: "the tree is dirty" in recon.log()), recon.log()
    passes(recon, 3)

    assert head(work) == before, "a dirty tree is never fast-forwarded"
    assert (work / "README.md").read_text(encoding="utf-8").endswith("live\n"), "and the edit is untouched"
    assert recon.log().count("the tree is dirty") == 1, "a standing refusal is logged once, not once per pass"
    assert not stubs.inbox("lead-uuid") and not stubs.calls("thurbox-cli", "session send"), "nobody is told"


def test_status_says_the_sync_clock_and_zero_turns_it_off(recon, monkeypatch, tmp_path):
    monkeypatch.setenv("FLEET_RECONCILE_SYNC_SECS", "0")
    expect(recon("status").out, "sync      off")
    syncing(monkeypatch, tmp_path)
    expect(recon("status").out, "sync      every 2s")
