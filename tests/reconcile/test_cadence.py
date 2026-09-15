"""It folds continuously, polls on its own clocks, and is not a writer.

`watch` runs back to back; `collect`, `shepherd` and `refuel` each run on an
interval of their own. And over the whole run the things it asked the queue to
do are exactly the reconciling verbs plus the read-only `plan`.

WHY `plan` IS IN THAT SET AND `dispatch` NEVER CAN BE. The set is the list of
things the loop is allowed to want. `plan` READS: it prints the ready set and
moves nothing, so the loop can tell the lead there is something to decide.
`dispatch`, `add`, `block`, `archive` and `reap` change what runs, and a loop
that could call one would be a second control plane with no operator in it. A
read the loop already needs may join the set; nothing that acts ever may.
"""

from __future__ import annotations

from harness import refute
from reconcilekit import wait_for


def test_the_first_pass_catches_up_on_every_periodic_verb(recon):
    recon("ensure")
    for verb in ("collect", "shepherd", "refuel"):
        assert wait_for(lambda v=verb: recon.count(v) >= 1, 15), f"the first pass ran no {verb}: {recon.calls()}"


def test_the_cadences_are_four_clocks_and_the_loop_writes_no_record(recon):
    recon("ensure")
    assert wait_for(lambda: recon.count("watch") >= 3), "watch runs back to back — the fold is continuous"
    assert wait_for(lambda: recon.count("collect") >= 3, 25), "collect runs on its own interval, repeatedly"

    def four_clocks() -> bool:
        return recon.count("watch") > recon.count("collect") >= recon.count("shepherd")

    assert wait_for(four_clocks, 30), (
        f"watch={recon.count('watch')} collect={recon.count('collect')} shepherd={recon.count('shepherd')}"
    )

    recon("status")
    recon("ensure")
    verbs = sorted({line.split(" ")[0] for line in recon.calls()})
    assert verbs == ["collect", "plan", "refuel", "root", "shepherd", "watch"], verbs
    assert not any(recon.queue.iterdir()), "it wrote nothing into the queue directory"


def test_a_watch_that_moved_nothing_is_not_logged_but_ten_moved_is(recon):
    """"10 task(s) moved" contains "0 task(s) moved"; an unanchored match would
    swallow a pass that moved real tasks as the nothing-happened case."""
    recon("ensure")
    assert wait_for(lambda: recon.count("watch") >= 2)
    refute(recon.log(), "task(s) moved")

    recon.moved(10)
    assert wait_for(lambda: "10 task(s) moved" in recon.log(), 10), recon.log()
