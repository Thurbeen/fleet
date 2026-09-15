"""`fleet status` degrades rather than fails, and keeps thurbox's words.

A status command is read by an agent about to decide something, so its two
failure modes are both silent and both expensive:

  1. IT DIES WHEN A PROBE DIES. No network, no `gh`, no thurbox: each must cost
     exactly its own section. A status command that exits non-zero because one
     probe failed is worse than none, because the lead learns nothing at all.
  2. IT FLATTENS THE STATE VOCABULARY. `idle`, `running`, `uncovered` and
     `unreported` are four different facts, and reporting any of the last three
     as `idle` reports a worker mid-turn as finished.

And a third promise: it READS. It dispatches nothing and touches no byte of the
queue. Every probe is a stand-in, so nothing reaches thurbox or GitHub.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest
from harness import expect, lib, refute, run_fleet, write
from statuskit import (
    GLORBNAK,
    MANY,
    MUTE,
    PRS,
    SEAM,
    SESSIONS,
    STALE,
    answer,
    build_queue,
    fuel_of,
    records,
)

SECTIONS = ("FUEL", "QUEUE", "SESSIONS", "PRS", "CHECKOUT")


def status(*args: str, **env):
    return run_fleet("status", *args, **env)


@pytest.fixture
def queue(tmp_path) -> Path:
    build_queue(tmp_path)
    return Path(os.environ["FLEET_QUEUE_DIR"])


@pytest.fixture
def bare(tmp_path) -> str:
    """A PATH holding no tool at all: thurbox-cli, gh and quota-axi really are absent."""
    empty = tmp_path / "bin-bare"
    empty.mkdir()
    return str(empty)


@pytest.fixture
def stubbed(stubs) -> None:
    """thurbox-cli with two sessions in words other than idle, gh with an open PR, one fuel reading."""
    stubs.tool("thurbox-cli", answer(json.dumps(SESSIONS)))
    stubs.tool("gh", answer(json.dumps(PRS)))
    stubs.tool("quota-axi", fuel_of(64))


# --- 1. every probe missing, and it still answers -----------------------------


def test_every_probe_missing_costs_only_its_own_section(queue, bare):
    done = status(PATH=bare)
    assert done.code == 0, done.out
    expect(done.out, *SECTIONS)
    expect(done.out, "thurbox-cli not found", "gh not found", "quota-axi not found")
    # The queue survives the others failing.
    expect(done.out, "01-dispatched-task")
    # A blocked task says what holds it and why, in the recorded words; a ready
    # task is called ready; file overlap is a risk, not a blocker.
    expect(done.out, "semantic-dependency", "consumes the flag", "ready", "FLEET.md")


# --- 2. --json degrades in the same shape -------------------------------------


def test_json_has_every_section_and_marks_the_missing_ones(queue, bare):
    done = status("--json", PATH=bare)
    assert done.code == 0, done.out
    doc = json.loads(done.stdout)
    for key in ("fuel", "queue", "sessions", "prs", "checkout"):
        assert key in doc and "unavailable" in doc[key], key
    assert doc["sessions"]["unavailable"] and doc["prs"]["unavailable"] and doc["fuel"]["unavailable"]
    assert doc["fuel"]["providers"] == [], "an unreadable fuel section names no provider"
    assert doc["queue"]["unavailable"] is None, "the queue is on disk and readable"
    assert len(doc["queue"]["topics"][0]["tasks"]) == 3


# --- 3. a queue directory that is not there -----------------------------------


def test_a_missing_queue_directory_does_not_fail_the_command(tmp_path, bare):
    done = status(PATH=bare, FLEET_QUEUE_DIR=str(tmp_path / "no-such-queue"))
    assert done.code == 0, done.out
    expect(done.out, "CHECKOUT")


# --- 3b. the operator's records are validated here, and not in the gate -------


def test_records_are_validated_by_a_flag_and_never_by_the_screen(queue, bare):
    """The gate reads no operator state, so `--records` is the only place a
    malformed record or a wrongly shaped map is reported. It is a flag because
    validating opens every record, which the screen promises never to do."""
    refute(status(PATH=bare).out, "RECORDS")
    done = status("--records", PATH=bare)
    expect(done.out, "queue: ok — 1 topic(s), 3 task(s)", "registry: ok — not synced yet")


def test_broken_records_cost_lines_and_never_the_exit_code(tmp_path, bare):
    broken = tmp_path / "broken-queue"
    write(broken / "bad" / "topic.yaml", "slug: bad\ntitle: A bad topic\n")
    write(broken / "bad" / "01-bad" / "task.yaml", (
        "id: 01-bad\ntopic: bad\ntitle: A bad record\nstate: half-done\nrepo: /nowhere\nbranch: bad/branch\n"
        "blocked_by:\n  - {task: bad/42-gone, kind: semantic-dependency, why: made up for this test}\n"
    ))
    write(tmp_path / "bad-registry.yaml", "owners: not-a-list\n")

    done = status("--records", PATH=bare, FLEET_QUEUE_DIR=str(broken),
                  FLEET_REGISTRY_FILE=str(tmp_path / "bad-registry.yaml"))

    assert done.code == 0, done.out
    expect(done.out, "state 'half-done' is not one of", "bad/42-gone, which does not exist", "no BRIEF.md",
           "queue.sh check", "owners missing or not a list")
    expect(status("--records", "--json", PATH=bare, FLEET_QUEUE_DIR=str(broken)).out, "is not one of")


def test_a_registry_map_of_the_right_shape_reads_as_ok(tmp_path, bare):
    write(tmp_path / "good-registry.yaml", "owners:\n  - name: octo\n    repos: []\ntotals:\n  repos: 0\n")
    done = status("--records", PATH=bare, FLEET_REGISTRY_FILE=str(tmp_path / "good-registry.yaml"))
    expect(done.out, "registry: ok — 0 repos across 1 owners")


# --- 4. the state vocabulary is not flattened ---------------------------------


def test_thurboxs_own_word_for_a_session_survives_the_trip(queue, stubbed):
    """THE CLAIM THIS FILE EXISTS FOR: printing `uncovered` or `unreported` as
    `idle` tells the lead a worker mid-turn has finished."""
    done = status()
    expect(done.out, "uncovered")
    refute(done.out, "idle", "unknown")


# --- 5. it reports the artifact, with its checks ------------------------------


def test_an_open_pr_is_reported_against_its_task_with_its_checks(queue, stubbed):
    expect(status().out, "Thurbeen/fleet#13", "passing")


# --- 6. fuel is measured, never projected -------------------------------------


def test_fuel_is_what_was_measured_and_no_forecast_reaches_the_screen(stubbed):
    """quota-axi hands back measurements AND forecasts in one document. Fleet
    carries no forecast, and `resetsAt`, which says when a spent window comes
    back, must reach the screen."""
    done = status()
    expect(done.out, "64% remaining", "reserve 20%", "binding seven_day", "five_hour", "model:fable",
           "resets 2026-03-20T17:59:45.600Z")
    refute(done.out, "2026-03-19T03:43:45.600Z", "298906", "-8.2", "1.295")


def test_under_the_reserve_the_reading_is_still_just_the_reading(stubs):
    stubs.tool("quota-axi", fuel_of(8))
    expect(status().out, "8% remaining", "under the 20% reserve")


def test_a_cached_reading_is_reported_with_its_age_and_why_it_is_stale(stubs):
    stubs.tool("quota-axi", STALE)
    done = status()
    expect(done.out, "74% remaining", "stale", "last refreshed 2026-09-08T21:28:34.926Z", "rate limited")
    refute(done.out, "0% remaining")


def test_a_provider_with_no_window_is_absent_and_never_a_zero(stubs):
    stubs.tool("quota-axi", MUTE)
    done = status()
    expect(done.out, "unavailable — auth_required; Claude sign-in required")
    refute(done.out, "0% remaining")


# --- 6b. `--fuel` is the same reading, in one record and at one probe's cost ---


def test_fuel_flag_is_the_fuel_section_alone_and_spends_no_other_probe(stubs):
    """The TUI pane draws this reading and can afford neither `--json` (every
    section, so a `gh pr list` per repo in flight) nor a JSON parser."""
    stubs.tool("quota-axi", fuel_of(64))
    for tool in ("gh", "thurbox-cli"):
        stubs.tool(tool, "raise SystemExit(1)")

    done = status("--fuel")

    assert done.code == 0, done.out
    assert stubs.calls("gh") == [] and stubs.calls("thurbox-cli") == []
    expect(done.out, "remaining\t64", "reserve\t20", "limited_by\tseven_day",
           "resets_at\t2026-03-20T17:59:45.600Z", "state\tfresh")
    refute(done.out, "2026-03-19T03:43:45.600Z", "298906")
    # A pane has no `os` and cannot parse an instant, so the age has to be subtractable.
    assert abs(time.time() - int(records(done.stdout)[0]["read_at"])) < 300


def test_an_unreadable_fuel_record_is_a_reason_and_carries_no_number(stubs):
    stubs.tool("quota-axi", MUTE)
    done = status("--fuel", FLEET_FUEL_PROVIDER="claude")
    expect(done.out, "unavailable\tauth_required; Claude sign-in required", "reserve\t20")
    refute(done.out, "remaining\t")


def test_the_record_and_its_json_are_one_reading(stubs):
    stubs.tool("quota-axi", fuel_of(64))
    blocks = records(status("--fuel").stdout)
    doc = json.loads(status("--fuel", "--json").stdout)
    assert len(blocks) == len(doc["providers"])
    for fields, rec in zip(blocks, doc["providers"], strict=True):
        assert fields["provider"] == rec["provider"]
        assert fields["remaining"] == str(rec["remaining"])
        assert fields["reserve"] == str(rec["reserve"])


# --- 6c. one reading per subscription the operator actually has ----------------


def fetches(stubs) -> list[str]:
    return [c for c in stubs.calls("quota-axi") if not c.startswith("quota-axi auth")]


def test_every_credentialed_subscription_is_read_in_one_fetch_and_none_is_averaged(stubs):
    """Which providers are asked is decided by the credentials on disk; it stays
    one fetch because the pane redraws on a timer; a failed provider carries its
    own reason and no number, and nothing is summed or reduced to the lowest."""
    stubs.tool("quota-axi", MANY)
    done = status()
    expect(done.out, "claude  64% remaining", "zai  7% remaining",
           "codex  unavailable — auth_required; Codex sign-in required", "under the 20% reserve")
    refute(done.out, "codex  0% remaining")
    asked = fetches(stubs)
    assert len(asked) == 1, asked
    expect(asked[0], "--provider claude,codex,zai")
    refute(asked[0], "cursor", "grok")


def test_fuel_flag_carries_one_record_per_provider_from_one_fetch(stubs):
    stubs.tool("quota-axi", MANY)
    blocks = records(status("--fuel").stdout)
    by = {b["provider"]: b for b in blocks}
    assert [b["provider"] for b in blocks] == ["claude", "codex", "zai"]
    assert by["claude"]["remaining"] == "64" and by["zai"]["remaining"] == "7"
    assert "remaining" not in by["codex"] and by["codex"]["unavailable"].startswith("auth_required")
    for b in blocks:
        assert b["reserve"] == "20" and b["read_at"] == blocks[0]["read_at"]
    assert len(fetches(stubs)) == 1


# --- 6d. WHICH provider is the operator's setting, never a name in the code ---


def first_provider(text: str) -> str:
    return records(text)[0]["provider"]


def test_the_provider_follows_the_operators_setting(tmp_path, stubs, monkeypatch):
    """The same stub read over three answers to "which provider", and the reading
    follows each: the agent named in agent.conf, FUEL_PROVIDER over it, and on an
    unconfigured clone whichever provider holds a credential."""
    stubs.tool("quota-axi", SEAM)
    conf = tmp_path / "agentconf"
    agent_conf = conf / "orchestration" / "agent.conf"

    write(agent_conf, "AGENT=nova\n")
    done = status("--fuel", FLEET_AGENT_ROOT=str(conf))
    assert first_provider(done.stdout) == "nova"
    expect(done.out, "provider\tzai")

    write(agent_conf, "AGENT=nova\nFUEL_PROVIDER=zai\n")
    assert first_provider(status("--fuel", FLEET_AGENT_ROOT=str(conf)).stdout) == "zai"
    # The gate `refuel` calls reads ONE provider, and with none passed it is the operator's.
    monkeypatch.setenv("FLEET_AGENT_ROOT", str(conf))
    assert lib("fleet_status.py").probe_fuel()["provider"] == "zai"

    agent_conf.unlink()
    done = status("--fuel", FLEET_AGENT_ROOT=str(conf))
    expect(done.out, "provider\tnova")
    refute(done.out, "claude")


def test_the_sole_credential_is_read_by_discovery_whatever_its_name(tmp_path, stubs):
    """A fallback literal would behave like discovery on a machine whose one
    credential is `claude`, so the credential here has a name nothing ships."""
    stubs.tool("quota-axi", GLORBNAK)
    (tmp_path / "agentconf" / "orchestration").mkdir(parents=True)
    expect(status("--fuel", FLEET_AGENT_ROOT=str(tmp_path / "agentconf")).out, "provider\tglorbnak")


# --- 7. it reads, and only reads ----------------------------------------------


def snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_not_a_byte_of_the_queue_is_touched(queue, stubbed):
    before = snapshot(queue)
    status()
    status("--json")
    assert snapshot(queue) == before
