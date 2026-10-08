"""It wakes the lead on the transition, and not on the pass.

A task whose blocker clears is ready and has no actor: the loop may not
dispatch, and the lead only acts when spoken to. What is proved here is the
shape that makes the fix survivable: one line when the ready set becomes
non-empty or grows, silence while it stays the same, no typing into a lead
mid-turn (the wake waits, it is not lost), and no send and no error storm when
there is no lead session at all.

A lead mid-turn is not left with nothing, though: the same line goes into its
thurbox mailbox with `--no-wake`, which nothing types and nothing pushes into
its conversation, so the lead or the operator reads it whenever they look.
"""

from __future__ import annotations

from harness import expect, lib
from reconcilekit import wait_for


def test_the_lead_is_woken_once_per_transition(recon, stubs):
    def sends() -> list[str]:
        return stubs.calls("thurbox-cli", "session send")

    def collects_pass(n: int) -> None:
        start = recon.count("collect")
        assert wait_for(lambda: recon.count("collect") >= start + n, 30)

    recon("ensure")
    collects_pass(1)
    assert sends() == [], "an empty ready set wakes nobody"

    recon.ready("alpha/01-first")
    assert wait_for(lambda: len(sends()) >= 1), f"ready work never reached the lead\n{recon.log()}"
    woke = sends()[0]
    expect(woke, "alpha/01-first")
    assert "uv run fleet queue dispatch" in woke, f"the wake carries the command that sends it: {woke}"
    assert "\n" not in woke, "the wake is one line, not a report"

    collects_pass(3)
    assert len(sends()) == 1, "it stays quiet while the same set stays ready"

    recon.ready("alpha/01-first", "beta/02-second")
    assert wait_for(lambda: len(sends()) >= 2), "a growing ready set is a fresh transition"
    expect(sends()[-1], "beta/02-second", "alpha/01-first")

    recon.lead("working")
    held = len(sends())
    recon.ready("alpha/01-first", "beta/02-second", "gamma/03-third")
    collects_pass(3)
    assert len(sends()) == held, "a lead mid-turn is not interrupted"
    expect(recon.log(), "the wake waits")

    recon.lead("idle")
    assert wait_for(lambda: len(sends()) >= held + 1), "the held wake lands once the lead is at rest"
    expect(sends()[-1], "gamma/03-third")

    recon.lead(None)
    held = len(sends())
    watched = recon.count("watch")
    recon.ready("alpha/01-first", "beta/02-second", "gamma/03-third", "delta/04-fourth")
    collects_pass(3)
    assert len(sends()) == held, "no lead session means no send"
    assert recon.count("watch") > watched, "and the loop keeps folding regardless"
    assert recon.log().count("no session named") <= 1, "an absent lead is reported once, not once per pass"


def test_a_lead_mid_turn_finds_the_notice_in_its_inbox(recon, stubs):
    def posts() -> list[str]:
        return stubs.calls("thurbox-cli", "message send")

    def inbox() -> list[dict]:
        return stubs.inbox("lead-uuid")

    def collects_pass(n: int) -> None:
        start = recon.count("collect")
        assert wait_for(lambda: recon.count("collect") >= start + n, 30)

    recon.lead("working")
    recon("ensure")
    recon.ready("alpha/01-first")
    assert wait_for(lambda: len(inbox()) >= 1), f"the busy lead's inbox stayed empty\n{recon.log()}"
    note = inbox()[0]
    expect(note["body"], "alpha/01-first", "uv run fleet queue dispatch")
    assert not note["woke"], "a note to a lead mid-turn must not wake it"
    assert all("--no-wake" in p for p in posts()), f"every post is silent: {posts()}"
    assert stubs.calls("thurbox-cli", "session send") == [], "nothing is typed mid-turn"

    collects_pass(3)
    assert len(inbox()) == 1, "the same ready set is posted once, not once per pass"

    recon.ready("alpha/01-first", "beta/02-second")
    assert wait_for(lambda: len(inbox()) >= 2), "a growing ready set is a fresh note"
    expect(inbox()[-1]["body"], "beta/02-second")

    recon.lead("idle")
    assert wait_for(lambda: stubs.calls("thurbox-cli", "session send")), "the wake still lands at rest"


def test_a_thurbox_without_an_inbox_keeps_todays_wait(recon, stubs):
    (stubs.root / "no-inbox").write_text("", encoding="utf-8")
    recon.lead("working")
    recon("ensure")
    recon.ready("alpha/01-first")
    assert wait_for(lambda: "the wake waits" in recon.log()), recon.log()
    assert stubs.calls("thurbox-cli", "session send") == [], "nothing is typed mid-turn"

    recon.lead("idle")
    assert wait_for(lambda: stubs.calls("thurbox-cli", "session send")), "the wake lands at rest"


def test_remembering_what_was_said_never_makes_the_runtime_directory(isolated_env):
    """The state file goes INTO the loop's runtime directory and never creates it.

    Creating it is how a deleted directory came back: the loop checks it is
    still there at the top of every pass, and a notify that made it again in
    the middle of one left an orphaned loop ticking forever."""
    mod = lib("notify_lead.py")
    gone = isolated_env / "reconcile-that-was-deleted"

    mod.write_state(str(gone), ["alpha/01-first"], "")

    assert not gone.exists(), "notify made the runtime directory again"


def test_a_lead_with_something_typed_is_never_typed_into(recon, stubs):
    """The operator's "c" and the notice became one line: "cfleet reconciler: …".
    An at-rest lead whose input line holds anything gets the mailbox instead,
    and the wake lands once the line is empty again."""
    recon.composer("c")
    recon("ensure")
    recon.ready("alpha/01-first")
    assert wait_for(lambda: len(stubs.inbox("lead-uuid")) >= 1), f"the notice went nowhere\n{recon.log()}"
    assert not stubs.inbox("lead-uuid")[0]["woke"]
    assert stubs.calls("thurbox-cli", "session send") == [], "typed into a line the operator was writing"
    assert wait_for(lambda: "not provably empty" in recon.log()), recon.log()

    recon.composer("")
    assert wait_for(lambda: stubs.calls("thurbox-cli", "session send")), "the wake lands on an empty line"
    expect(stubs.calls("thurbox-cli", "session send")[0], "alpha/01-first")


def test_a_composer_nothing_can_read_gets_the_mailbox_and_never_the_keyboard(recon, stubs):
    """A thurbox whose capture reports no cursor cannot say the line is empty."""
    recon.composer(None)
    recon("ensure")
    recon.ready("alpha/01-first")
    assert wait_for(lambda: len(stubs.inbox("lead-uuid")) >= 1), f"the notice went nowhere\n{recon.log()}"
    start = recon.count("collect")
    assert wait_for(lambda: recon.count("collect") >= start + 3, 30)
    assert stubs.calls("thurbox-cli", "session send") == []
    assert len(stubs.inbox("lead-uuid")) == 1, "posted once, not once per pass"
