"""Claim 12: `refuel` asks the account's window first, then restarts the session that ran dry.

A worker that hits its agent's token limit does not fail — it sits. thurbox
goes on reporting the last state its hook saw, so the session reads `working`
forever and neither `watch` nor `collect` nor `reap` ever touches it.

The ACCOUNT window outranks every per-session reading: it is the operator's
subscription, shared by the lead and every worker, so a restart while it is
spent resumes, hits the same wall and burns the reset. With fuel, a stale
`working` on its own is a SLOW worker; one whose pane or transcript carries the
agent's own limit signal is restarted through dispatch's handoff, capped and
recorded. The lead is refused, a quota that cannot be read is `undetermined`,
and nothing here writes `state` or `outcome`.
"""

import pytest
from kit_refuel import (
    CLAUDE_BANNER, attach_task, no_quota, one_refuel_just_now, pane, quota_is, restarts, rewind_refuels, sends,
    transcript,
)
from queuekit import ok

from harness import expect, refute
from harness import run_queue as q

DRY = "aaaaaaa1-0000-0000-0000-000000000001"
SLOW = "aaaaaaa2-0000-0000-0000-000000000002"
BUSY = "aaaaaaa3-0000-0000-0000-000000000003"
TRANSCRIBED = "aaaaaaa4-0000-0000-0000-000000000004"
RESETS = "2026-09-09T02:10:00+00:00"


@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    home = tmp_path / "claude-config"
    (home / "projects" / "-tmp-repo-a").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


@pytest.fixture
def ran_dry(stubs, claude_home) -> str:
    """`01` sits on a limit banner, `02` is merely slow, `03` never went stale."""
    topic = ok(q("topic", "add", "ran-dry", "--title", "Workers that ran dry",
                 "--prompt", "restart the sessions that hit the token limit")).stdout.strip()
    attach_task(topic, "ran-dry", "01", DRY)
    attach_task(topic, "just-slow", "02", SLOW)
    attach_task(topic, "busy", "03", BUSY)
    pane(stubs, DRY, f"● Now I will run the gate.\n\n{CLAUDE_BANNER}\n/upgrade to increase your usage limit\n")
    # All three `working` for two hours by the hook's own clock; only the first
    # has anything on its pane to say why.
    stubs.session_is(DRY, "working", 7200)
    stubs.session_is(SLOW, "working", 7200)
    stubs.session_is(BUSY, "working", 90)
    return topic


def test_a_spent_account_window_restarts_nothing(ran_dry, stubs):
    quota_is(stubs, 0, RESETS)
    out = q("refuel").out
    # It stops the whole sweep, says when the window comes back from quota-axi's
    # own resetsAt, and says the fleet waits on the window and not on a session.
    expect(out, "account", RESETS, "waiting on the window")
    assert restarts(stubs, DRY) == [], "restarted while the fuel is gone — even the one with the banner"


def test_with_fuel_only_the_session_on_its_limit_banner_is_restarted(ran_dry, stubs, queue_dir):
    quota_is(stubs, 62, RESETS)
    task = queue_dir / ran_dry / "01-ran-dry"

    out = q("refuel", "--dry-run").out
    expect(out, "would restart", "01-ran-dry")
    assert restarts(stubs) == [], "a dry run restarted a session"
    refute((task / "task.yaml").read_text(encoding="utf-8"), "refuels")

    out = q("refuel").out
    expect(out, "restarted")
    assert restarts(stubs, DRY), "the session that ran dry was not restarted"
    # A stale working state on its own is a SLOW worker, not a dry one.
    expect(out, "02-just-slow")
    assert restarts(stubs, SLOW) == [], "a slow worker was restarted"
    assert restarts(stubs, BUSY) == [], "a session still reporting fresh was restarted"

    # The handoff is dispatch's: the worker is pointed back at its own brief's
    # absolute path, and told to continue where it stopped.
    expect(sends(stubs), str(task / "BRIEF.md"), "continue")

    state = q("show", f"{ran_dry}/01-ran-dry").out
    # Recorded, and still exactly as dispatched: a restart is not a completion.
    expect(state, "refuelled:", "state:       dispatched")
    refute(state, "outcome:     shipped")


def test_the_transcript_is_the_precise_source(ran_dry, stubs, claude_home):
    quota_is(stubs, 62, RESETS)
    attach_task(ran_dry, "from-transcript", "04", TRANSCRIBED)
    stubs.session_is(TRANSCRIBED, "working", 7200)
    transcript(claude_home / "projects" / "-tmp-repo-a" / f"agent-{TRANSCRIBED}.jsonl", "five_hour", CLAUDE_BANNER)

    out = q("refuel", f"{ran_dry}/04-from-transcript", "--dry-run").out
    # Read, keyed by agent_session_id, and naming the window that rejected the turn.
    expect(out, "transcript", "five_hour", "would restart")
    # A single ref narrows the sweep; the default is every recorded session.
    refute(out, "01-ran-dry")


def test_the_leads_own_session_is_refused_by_name(ran_dry, stubs):
    quota_is(stubs, 62, RESETS)
    out = q("refuel", "--dry-run", THURBOX_SESSION=DRY).out
    expect(out, "lead")
    refute(out, "would restart  aaaaaaa1")


def test_the_cap_holds_and_a_restart_moments_ago_is_given_a_moment(ran_dry, stubs, queue_dir):
    """Receipts are rewound between passes because a restart seconds ago is not a
    second wedge: a `working` state from BEFORE the last restart is evidence from
    before it. Three hours back is a session that came up and ran dry again."""
    quota_is(stubs, 62, RESETS)
    record = queue_dir / ran_dry / "01-ran-dry" / "task.yaml"

    ok(q("refuel"))
    for _ in range(4):
        q("refuel")
        rewind_refuels(record)
    out = q("refuel").out
    expect(out, "the cap is 3", "ran dry again")
    assert len(restarts(stubs, DRY)) == 3, stubs.calls("thurbox-cli", "session restart")

    # The same session, with one receipt stamped just now, is left to come back up.
    one_refuel_just_now(record)
    before = len(restarts(stubs))
    expect(q("refuel").out, "give it a moment")
    assert len(restarts(stubs)) == before, "restarted again on evidence from before that restart"


def test_a_quota_that_cannot_be_read_is_undetermined_and_restarts_nothing(ran_dry, stubs):
    no_quota(stubs)
    out = q("refuel").out
    expect(out, "undetermined")
    refute(out, "restarted")


# --- 12b. the AGENT seam, driven by a second agent -----------------------------
#
# An interface is worth what a SECOND implementation driven through it is
# worth (scripts/lib/forge.py's standard). So the sweep runs again for `nova`,
# which has no entry in AGENT_LIMIT_SIGNALS: everything fleet needs comes from
# the operator's agent.conf. THE TRIPWIRE IS THE POINT: no CLAUDE_CONFIG_DIR, no
# claude banner, and a quota document for another provider — a built-in
# `claude` reached for anywhere answers with the wrong window or none.

NOVA_DRY = "bbbbbbb1-0000-0000-0000-000000000001"
NOVA_SLOW = "bbbbbbb2-0000-0000-0000-000000000002"
NOVA_TRANSCRIBED = "bbbbbbb3-0000-0000-0000-000000000003"
NOVA_BANNER = "quota exhausted for this workspace"
NOVA_RESETS = "2026-09-09T04:00:00+00:00"


@pytest.fixture
def nova_root(tmp_path, monkeypatch):
    """`TRUST_SIGNATURE` and `TRUST_KEYS` are there because the handoff needs them:
    session_trust refuses an agent whose dialog it has not watched, so an
    untaught agent would be restarted and never told its brief."""
    root = tmp_path / "nova-conf"
    transcripts = tmp_path / "nova-transcripts"
    transcripts.mkdir()
    (root / "orchestration").mkdir(parents=True)
    (root / "orchestration" / "agent.conf").write_text(
        f"AGENT=nova\nFUEL_PROVIDER=nova\nLIMIT_BANNER={NOVA_BANNER}\nTRANSCRIPT_DIR={transcripts}\n"
        "TRUST_SIGNATURE=do you trust the contents of this workspace\nTRUST_KEYS=enter\n",
        encoding="utf-8", newline="\n")
    monkeypatch.setenv("FLEET_AGENT_ROOT", str(root))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return root


@pytest.fixture
def nova_dry(nova_root, stubs) -> str:
    topic = ok(q("topic", "add", "nova-dry", "--title", "A second agent runs dry",
                 "--prompt", "the same sweep, for an agent fleet has no built-in signal for")).stdout.strip()
    attach_task(topic, "ran-dry", "01", NOVA_DRY, branch="fix/nova")
    attach_task(topic, "just-slow", "02", NOVA_SLOW, branch="fix/nova-slow")
    # The operator's sentence, not one this repo wrote down.
    pane(stubs, NOVA_DRY, f"> running the gate\n\n{NOVA_BANNER} — try again after 02:00\n")
    stubs.session_is(NOVA_DRY, "working", 7200, agent="nova")
    stubs.session_is(NOVA_SLOW, "working", 7200, agent="nova")
    return topic


def test_a_second_agent_is_gated_on_the_provider_the_operator_named(nova_dry, stubs):
    quota_is(stubs, 0, NOVA_RESETS, provider="nova")
    out = q("refuel").out
    expect(out, "nova", "waiting on the window")
    assert restarts(stubs, NOVA_DRY) == []

    # A document naming ONLY the first agent's provider is not a reading of this one.
    quota_is(stubs, 62, NOVA_RESETS, provider="claude")
    expect(q("refuel").out, "undetermined")
    assert restarts(stubs, NOVA_DRY) == []


def test_the_banner_the_operator_configured_marks_the_dry_session(nova_dry, stubs, queue_dir):
    quota_is(stubs, 62, NOVA_RESETS, provider="nova")
    out = q("refuel").out
    expect(out, "restarted", "02-just-slow")
    assert restarts(stubs, NOVA_DRY), "the session carrying the configured banner was not restarted"
    assert restarts(stubs, NOVA_SLOW) == [], "a stale working state is still only a slow worker"
    expect(sends(stubs), str(queue_dir / nova_dry / "01-ran-dry" / "BRIEF.md"))


def test_the_transcript_is_read_where_the_operator_said_it_is(nova_dry, nova_root, stubs, tmp_path):
    """`TRANSCRIPT_DIR` is the directory itself: the records sit directly in it."""
    quota_is(stubs, 62, NOVA_RESETS, provider="nova")
    pane(stubs, NOVA_DRY, None)
    attach_task(nova_dry, "from-transcript", "03", NOVA_TRANSCRIBED, branch="fix/nova-tr")
    stubs.session_is(NOVA_TRANSCRIBED, "working", 7200, agent="nova")
    transcript(tmp_path / "nova-transcripts" / f"agent-{NOVA_TRANSCRIBED}.jsonl", "workspace_day",
               f"{NOVA_BANNER} — try again after 02:00")

    out = q("refuel", f"{nova_dry}/03-from-transcript", "--dry-run").out
    # On the record, not on a pane.
    expect(out, "transcript", "workspace_day", "would restart")

    # An agent with NO entry and NO settings: fleet says it does not know.
    (nova_root / "orchestration" / "agent.conf").write_text("AGENT=unheard\n", encoding="utf-8", newline="\n")
    out = q("refuel", f"{nova_dry}/03-from-transcript", "--dry-run").out
    expect(out, "undetermined")
    refute(out, "would restart")
