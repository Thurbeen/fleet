"""Claim 13: the status output never contradicts itself.

Five readings that were all wrong on one screen, every one produced by records
a real run wrote: a `landed` task printed with a "held by" line; that blocker
naming an `abandoned` upstream that can never land, without saying so;
`abandoned` beside `shipped` and a pull request URL as though they agreed; a
sweep answering "none open" beside a repo it could not read; and a `queued`
task nobody dispatched, indistinguishable from one queued a minute ago.

The records below ARE those contradictions. A task.yaml is the history of what
happened, and tidying one deletes the evidence, so every claim is about output
— in `queue list|show|plan`, in `fleet status`, and in `fleet status --json`,
which derives its blockers and notes through queue.py exactly as the text does.
"""

import pytest
from kit_display import set_field
from queuekit import ok

from harness import expect, refute, run_fleet
from harness import run_queue as q


@pytest.fixture
def contradictions(queue_dir, tmp_path) -> str:
    readable = tmp_path / "repo-readable"
    readable.mkdir()
    topic = ok(q("topic", "add", "contradictions", "--title", "Records that disagree with themselves",
                 "--prompt", "the status output must not contradict itself")).stdout.strip()
    # 05 names a checkout that is not there: the repo the sweep cannot read.
    for n, slug, title, repo in (
        ("01", "upstream-abandoned", "An upstream closed unmerged", readable),
        ("02", "landed-holder", "A task that already landed", readable),
        ("03", "still-queued", "A task still waiting on that upstream", readable),
        ("04", "never-dispatched", "A task nobody ever sent out", readable),
        ("05", "sweeps-a-missing-repo", "A task whose checkout is gone", tmp_path / "no-such-repo"),
    ):
        ok(q("add", topic, slug, "--title", title, "--repo", str(repo), "--branch", f"fix/{slug}", "--number", n))
    ok(q("block", f"{topic}/02-landed-holder", "--on", f"{topic}/01-upstream-abandoned",
         "--kind", "semantic-dependency", "--why", "reads the field the upstream adds"))
    ok(q("block", f"{topic}/03-still-queued", "--on", f"{topic}/01-upstream-abandoned",
         "--kind", "semantic-dependency", "--why", "needs that same field"))

    # The worker reported it shipped; the forge closed the pull request unmerged.
    set_field(queue_dir, f"{topic}/01-upstream-abandoned", "state", "abandoned")
    set_field(queue_dir, f"{topic}/01-upstream-abandoned", "outcome", "shipped")
    set_field(queue_dir, f"{topic}/01-upstream-abandoned", "artifact", "https://github.com/Thurbeen/thurbox/pull/1091")
    set_field(queue_dir, f"{topic}/02-landed-holder", "state", "landed")
    set_field(queue_dir, f"{topic}/05-sweeps-a-missing-repo", "state", "dispatched")
    return topic


def test_the_queue_views_never_contradict_the_records(contradictions):
    out = q("list").out
    # A landed task never displays a blocker.
    refute(out, "reads the field the upstream adds")
    expect(
        out,
        # A blocker whose upstream can never land is called so, with that upstream's state.
        "UNCLEARABLE", "which is abandoned and can never land",
        "state abandoned disagrees with outcome shipped",
        "no session dispatched",
    )

    # The marker is on the task with no session, and not on one a blocker holds.
    expect(q("show", f"{contradictions}/04-never-dispatched").out, "no session dispatched")
    refute(q("show", f"{contradictions}/03-still-queued").out, "no session dispatched")

    held = q("show", f"{contradictions}/02-landed-holder").out
    refute(held, "HOLDING")
    expect(held, "holds nothing")

    expect(q("plan").out, "UNCLEARABLE")


def test_fleet_status_says_the_same_five_things(contradictions):
    status = run_fleet("status").out
    refute(status, "reads the field the upstream adds")
    expect(status, "UNCLEARABLE", "disagrees with outcome shipped", "no session dispatched")
    # The PR headline cannot read "none open" when a repo went unread: it says
    # the sweep was incomplete, and still names the repo it could not read.
    refute(status, "none open for these tasks")
    expect(status, "INCOMPLETE", "no such directory")


def test_the_machine_readable_reading_derives_the_same_blockers_and_notes(contradictions):
    view = run_fleet("status", "--json").out
    expect(view, '"status": "unclearable"', '"status": "moot"', "disagrees with outcome shipped",
           "no session dispatched")
