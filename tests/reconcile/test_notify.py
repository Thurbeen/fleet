"""It wakes the lead on the transition, and not on the pass.

A task whose blocker clears is ready and has no actor: the loop may not
dispatch, and the lead only acts when spoken to. What is proved here is the
shape that makes the fix survivable: one line when the ready set becomes
non-empty or grows, silence while it stays the same, no typing into a lead
mid-turn (the wake waits, it is not lost), and no send and no error storm when
there is no lead session at all.
"""

from __future__ import annotations

import re

from harness import expect
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
    assert re.search(r"queue(\.sh)? dispatch", woke), f"the wake carries the command that sends it: {woke}"
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
