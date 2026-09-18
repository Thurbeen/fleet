"""A dead pane is evidence even when its last hook says nothing at all."""

import json

import pytest
import yaml
from kit_refuel import attach_task, pane, quota_is, restarts, sends
from queuekit import ok

from harness import expect, refute, write
from harness import run_queue as q

SID = "dddddddd-0000-0000-0000-000000000001"
RESETS = "2026-09-09T02:10:00+00:00"


@pytest.fixture
def dead(stubs):
    topic = ok(q("topic", "add", "dead-pane", "--title", "Recover a dead pane",
                 "--prompt", "restart a worker whose process exited")).stdout.strip()
    attach_task(topic, "worker", "01", SID)
    stubs.session_is(SID, "uncovered")
    path = stubs.root / "sessions" / f"{SID}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.update(hook_state=None, hook_state_age_secs=None, hook_corroboration="dead")
    write(path, json.dumps(doc))
    # No prose banner: the path must consume the structured multiplexer probe.
    pane(stubs, SID, "")
    quota_is(stubs, 62, RESETS)
    return f"{topic}/01-worker"


def test_dead_pane_is_restarted_without_a_hook_or_limit_banner(dead, stubs, queue_dir):
    record = queue_dir / dead / "task.yaml"
    before = yaml.safe_load(record.read_text(encoding="utf-8"))
    out = ok(q("refuel", dead)).out
    expect(out, "restarted", "dead pane")
    assert len(restarts(stubs, SID)) == 1
    calls = stubs.calls("thurbox-cli")
    assert calls.index(f"thurbox-cli session stop {SID}") < calls.index(f"thurbox-cli session start {SID}")
    expect(sends(stubs), str(queue_dir / dead / "BRIEF.md"), "continue")
    after = yaml.safe_load(record.read_text(encoding="utf-8"))
    assert after["state"] == before["state"] == "dispatched"
    assert after.get("outcome") == before.get("outcome")
    assert len(after["refuels"]) == 1
    assert after["refuels"][0]["prompted"] is True
    expect(after["refuels"][0]["why"], "dead pane")


def test_dead_pane_consumes_the_cap_even_when_the_previous_restart_was_just_now(dead, stubs):
    for _ in range(3):
        expect(ok(q("refuel", dead)).out, "restarted", "dead pane")
    expect(ok(q("refuel", dead)).out, "dead pane after 3 restart(s)", "human decides")
    assert len(restarts(stubs)) == 3


@pytest.mark.parametrize("percent", [0, None])
def test_dead_pane_waits_on_spent_or_unreadable_quota_before_reading_the_session(dead, stubs, percent):
    if percent is None:
        (stubs.root / "quota.json").unlink()
    else:
        quota_is(stubs, percent, RESETS)
    before = stubs.calls("thurbox-cli", "session get")
    out = ok(q("refuel", dead)).out
    expect(out, "spent" if percent == 0 else "undetermined")
    assert stubs.calls("thurbox-cli", "session get") == before
    assert stubs.calls("thurbox-cli", "session stop") == []
    assert restarts(stubs) == []


def test_dead_pane_dry_run_changes_nothing(dead, stubs, queue_dir):
    record = queue_dir / dead / "task.yaml"
    before = record.read_bytes()
    expect(ok(q("refuel", dead, "--dry-run")).out, "would restart", "dead pane")
    assert record.read_bytes() == before
    assert stubs.calls("thurbox-cli", "session stop") == []
    assert restarts(stubs) == []


@pytest.mark.parametrize("corroboration", [None, "agent", "unknown", "shell"])
def test_uncovered_is_not_dead_and_old_pane_text_is_not_evidence(dead, stubs, corroboration):
    path = stubs.root / "sessions" / f"{SID}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["hook_corroboration"] = corroboration
    write(path, json.dumps(doc))
    pane(stubs, SID, "Pane is dead (status 1, yesterday)\nNow working normally\n")
    refute(ok(q("refuel", dead)).out, "restarted")
    assert restarts(stubs) == []


def test_deliberately_stopped_session_is_not_recovered(dead, stubs):
    path = stubs.root / "sessions" / f"{SID}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.update(stopped=True, state="stopped")
    write(path, json.dumps(doc))
    expect(ok(q("refuel", dead)).out, "deliberately stopped")
    assert restarts(stubs) == []


def process_census(stubs, script):
    # The same fixture drives POSIX ps and Windows CIM through their real seam.
    for tool in ("ps", "powershell"):
        stubs.tool(tool, "import json, sys\nfrom fleet_stubs import root, read\n" + script +
                   "\nrows = [dict(ProcessId=200 + n, CommandLine=row) for n, row in enumerate(rows)]"
                   "\nprint(json.dumps(rows) if 'powershell' in sys.argv[0] else "
                   "'\\n'.join(str(row['ProcessId']) + ' ' + row['CommandLine'] for row in rows))\n")


def test_restart_waits_for_the_old_holder_after_stop_before_start(dead, stubs):
    process_census(stubs, f'''
where = root()
counter = where / "census-count"
n = int(read(counter) or "0") + 1
counter.write_text(str(n), encoding="utf-8")
rows = ["agent --resume agent-{SID}"] if n < 4 else []
calls = read(where / "calls.log")
if n in (2, 3):
    assert "session stop {SID}" in calls
    assert "session start {SID}" not in calls
''')
    expect(ok(q("refuel", dead)).out, "restarted")
    assert int((stubs.root / "census-count").read_text(encoding="utf-8")) == 4
    assert len(restarts(stubs)) == 1


def test_process_probe_failure_does_not_stop_or_start(dead, stubs, queue_dir):
    for tool in ("ps", "powershell"):
        stubs.tool(tool, "raise SystemExit(1)")
    expect(ok(q("refuel", dead)).out, "NOT RESTARTED", "cannot read processes")
    assert stubs.calls("thurbox-cli", "session stop") == []
    assert restarts(stubs) == []
    doc = yaml.safe_load((queue_dir / dead / "task.yaml").read_text(encoding="utf-8"))
    assert "refuels" not in doc


def test_an_unreadable_census_does_not_spend_the_cap(dead, stubs, queue_dir):
    for tool in ("ps", "powershell"):
        stubs.tool(tool, "raise SystemExit(1)")
    for _ in range(4):
        expect(ok(q("refuel", dead)).out, "NOT RESTARTED", "cannot read processes")
    refute(ok(q("refuel", dead)).out, "human decides")
    assert "refuels" not in yaml.safe_load((queue_dir / dead / "task.yaml").read_text(encoding="utf-8"))
    assert stubs.calls("thurbox-cli", "session stop") == []


def test_a_holder_that_survives_stop_is_never_resumed_on_top_of(dead, stubs, queue_dir):
    process_census(stubs, f'rows = ["agent --resume agent-{SID}"]')
    expect(ok(q("refuel", dead)).out, "NOT RESTARTED", "session parked", "still in use")
    assert len(stubs.calls("thurbox-cli", "session stop")) == 1
    assert restarts(stubs) == []
    assert sends(stubs) == ""
    doc = yaml.safe_load((queue_dir / dead / "task.yaml").read_text(encoding="utf-8"))
    assert len(doc["refuels"]) == 1
    assert doc["refuels"][0]["prompted"] is False
    assert doc["state"] == "dispatched"
    expect(doc["refuels"][0]["park"], "session parked", "still in use")


def test_a_parked_restart_is_reported_as_refuel_not_a_person(dead, stubs, queue_dir):
    """The park is the one state this design hands to a human: it has to stay
    on the receipt, or the next pass calls it a deliberate stop."""
    process_census(stubs, f'rows = ["agent --resume agent-{SID}"]')
    expect(ok(q("refuel", dead)).out, "NOT RESTARTED", "session parked")
    path = stubs.root / "sessions" / f"{SID}.json"
    session = json.loads(path.read_text(encoding="utf-8"))
    session.update(stopped=True, state="stopped")
    write(path, json.dumps(session))
    out = ok(q("refuel", dead)).out
    expect(out, "session parked", "still in use")
    refute(out, "deliberately stopped")
    assert stubs.calls("thurbox-cli", "session start") == []
    assert len(stubs.calls("thurbox-cli", "session stop")) == 1
