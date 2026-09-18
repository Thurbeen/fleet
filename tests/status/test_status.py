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
from datetime import datetime
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
    auth,
    build_queue,
    claude,
    fetch,
    fuel_of,
    per_account,
    quota_axi,
    records,
    window,
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
           "fleet queue check", "owners missing or not a list")
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


def test_the_record_carries_every_window_shortest_first(stubs):
    """EVERY WINDOW, not only the one that binds. A record that carried the
    binding window alone made the pane flip between the five-hour and the
    seven-day window whenever their percentages crossed. So each window is a
    `window` line — id, percent, reset as epoch seconds, label — shortest window
    first, whatever order quota-axi declared them in (the stand-in declares the
    week first). The binding window is still `limited_by`, and still the reading."""
    stubs.tool("quota-axi", fuel_of(64))
    done = status("--fuel")
    rows = [line.split("\t")[1:] for line in done.stdout.splitlines() if line.startswith("window\t")]
    assert [r[0] for r in rows] == ["five_hour", "seven_day", "model:fable"], done.out
    week = rows[1]
    assert week[1] == "64" and week[3] == "week", week
    want = int(datetime.fromisoformat("2026-03-20T17:59:45.600+00:00").timestamp())
    assert int(week[2]) == want, f"reset is not epoch seconds: {week}"
    expect(done.out, "limited_by\tseven_day")


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


# --- 6e. one reading per ACCOUNT, never per checkout --------------------------
#
# An account is a provider PLUS the environment that selects the credential.
# The screen read one account per provider — whichever the command itself ran
# under — while the workers drew on another, and the two numbers were measured
# an hour apart on 2026-09-18: the screen said 6% remaining and the account
# fleet dispatches on had 70%. The lead read the screen and dispatched nothing.
#
# So these run `fleet status` END TO END against an `agent.conf` carrying a
# dotted `ENV` line, and the stand-in answers a different window per
# environment: a test that called `probe_fuel_all()` itself would prove the
# function works and nothing about whether the command reads the setting.


def two_accounts(tmp_path: Path, stubs, here: int = 64, there: int = 7, extra: str = "") -> Path:
    """An agent.conf naming the checkout's own account and a second login of the
    same agent, and a quota-axi that answers a different window under each."""
    spare = tmp_path / "spare-config"
    conf = tmp_path / "agentconf"
    write(conf / "orchestration" / "agent.conf",
          "AGENT=claude\n"
          "claude-spare.LIKE=claude\n"
          f"claude-spare.ENV=CLAUDE_CONFIG_DIR={spare}\n" + extra)
    stubs.tool("quota-axi", per_account(
        "CLAUDE_CONFIG_DIR", str(spare),
        (auth(("claude", "available")), fetch(claude(here))),
        (auth(("claude", "available")), fetch(claude(there))),
    ))
    return conf


def test_the_screen_reads_every_account_agent_conf_names(tmp_path, stubs):
    """Both accounts, each with its own windows, its own binding window and its
    own reserve verdict — and the identity that tells two readings of one
    provider apart."""
    conf = two_accounts(tmp_path, stubs)
    done = status(FLEET_AGENT_ROOT=str(conf))
    expect(done.out, "2 reading(s) over 2 account(s)",
           "claude  64% remaining", "binding seven_day",
           "claude (claude-spare)  7% remaining", "under the 20% reserve")


def test_an_account_costs_one_quota_axi_call_and_never_one_per_provider(tmp_path, stubs):
    """The pane redraws on a timer, so the reading may not cost a process per
    provider. A second ACCOUNT costs one more call because the environment
    differs, and nothing else multiplies — and the read stays a read."""
    conf = two_accounts(tmp_path, stubs)
    status(FLEET_AGENT_ROOT=str(conf))
    asked = fetches(stubs)
    assert len(asked) == 2, asked
    for call in asked:
        expect(call, "--provider claude", "--no-credential-refresh")
    # The credential is on disk PER ACCOUNT, so discovery is asked per account too.
    assert len(stubs.calls("quota-axi", "auth")) == 2, stubs.calls("quota-axi")


def test_the_record_and_the_json_say_which_account_each_reading_is_for(tmp_path, stubs):
    """`interface/fleet_queue.lua` parses this record and labels its bars from
    it, so the identity has to be on the wire and not only on the screen."""
    conf = two_accounts(tmp_path, stubs)
    blocks = records(status("--fuel", FLEET_AGENT_ROOT=str(conf)).stdout)
    assert [(b["provider"], b["account"], b["remaining"]) for b in blocks] == [
        ("claude", "claude", "64"), ("claude", "claude-spare", "7")], blocks
    doc = json.loads(status("--json", FLEET_AGENT_ROOT=str(conf)).stdout)
    assert [p["account"] for p in doc["fuel"]["providers"]] == ["claude", "claude-spare"]


def test_two_agents_on_one_login_are_one_account(tmp_path, stubs):
    """The account is the ENVIRONMENT, not the agent: an agent that is `LIKE` a
    second-account agent resolves to that same `ENV` and shares its reading
    rather than paying for a second one."""
    conf = two_accounts(tmp_path, stubs, extra="claude-twin.LIKE=claude-spare\n")
    blocks = records(status("--fuel", FLEET_AGENT_ROOT=str(conf)).stdout)
    assert [b["account"] for b in blocks] == ["claude", "claude-spare"], blocks
    assert len(fetches(stubs)) == 2, fetches(stubs)


def test_a_checkout_naming_no_second_account_prints_what_it_always_did(stubs):
    """The tracked `agent.example.conf` names nothing, so a fresh clone has one
    account and nothing to tell it apart from: no `account` field reaches the
    record the pane parses, and the screen's head row is the one it had."""
    stubs.tool("quota-axi", fuel_of(64))
    done = status("--fuel")
    refute(done.out, "account\t")
    assert len(fetches(stubs)) == 1, fetches(stubs)
    expect(status().out, "1 provider(s) — account windows", "  claude  64% remaining")


def test_a_checkout_wide_env_line_moves_the_leads_own_account(tmp_path, stubs):
    """`ENV=` with no agent in front of it is `agent_settings.account_env`'s
    checkout-wide fallback — the same line `task_agent()`'s default resolution
    reads for a dispatch with no `--agent`. `fuel_accounts()`'s checkout entry
    has to resolve through that identical chain: a bare `ENV=` line that moved
    `refuel`'s own account and left the screen reading the raw process
    environment instead would be exactly the disagreement this feature exists
    to end, and it would do it on the SIMPLEST config an operator can write —
    no dotted key, one line."""
    spare = tmp_path / "spare-config"
    conf = tmp_path / "agentconf"
    write(conf / "orchestration" / "agent.conf",
          "AGENT=claude\n"
          f"ENV=CLAUDE_CONFIG_DIR={spare}\n")
    stubs.tool("quota-axi", per_account(
        "CLAUDE_CONFIG_DIR", str(spare),
        (auth(("claude", "available")), fetch(claude(64))),
        (auth(("claude", "available")), fetch(claude(7))),
    ))
    blocks = records(status("--fuel", FLEET_AGENT_ROOT=str(conf)).stdout)
    assert len(blocks) == 1 and blocks[0]["remaining"] == "7", blocks
    assert "account" not in blocks[0], blocks
    assert len(fetches(stubs)) == 1, fetches(stubs)
    done = status(FLEET_AGENT_ROOT=str(conf))
    expect(done.out, "1 provider(s) — account windows", "  claude  7% remaining")
    refute(done.out, "64% remaining")


def test_the_leads_own_agent_naming_its_own_env_is_one_account_not_two(tmp_path, stubs):
    """`AGENT=claude` plus `claude.ENV=...` names the SAME account as the bare
    line above — `claude.ENV` resolves ahead of the checkout-wide line in
    `agent_settings.account_env`'s own order, not beside it. `fuel_accounts()`
    stops revisiting `lead` in its loop for exactly this config: without that,
    the checkout's own entry and this dotted entry would draw the same window
    twice under two different-looking accounts."""
    spare = tmp_path / "spare-config"
    conf = tmp_path / "agentconf"
    write(conf / "orchestration" / "agent.conf",
          "AGENT=claude\n"
          f"claude.ENV=CLAUDE_CONFIG_DIR={spare}\n")
    stubs.tool("quota-axi", per_account(
        "CLAUDE_CONFIG_DIR", str(spare),
        (auth(("claude", "available")), fetch(claude(64))),
        (auth(("claude", "available")), fetch(claude(7))),
    ))
    blocks = records(status("--fuel", FLEET_AGENT_ROOT=str(conf)).stdout)
    assert len(blocks) == 1, blocks
    assert blocks[0]["remaining"] == "7", blocks
    assert "account" not in blocks[0], blocks
    assert len(fetches(stubs)) == 1, fetches(stubs)


def test_a_like_alias_of_the_leads_own_account_is_not_a_second_one(tmp_path, stubs):
    """A `LIKE` chain that ends at `lead`'s own `ENV` line reaches the checkout's
    account by a different name, not a different account. Deduping only
    `agent == lead` by literal name misses this: `claude-alt.LIKE=claude` with
    no `ENV` of its own resolves, through the chain, to the exact same
    environment as the checkout's entry, and used to draw it a second time —
    a second `auth` and `--provider` call, and a false second account on the
    screen for one credential."""
    spare = tmp_path / "spare-config"
    conf = tmp_path / "agentconf"
    write(conf / "orchestration" / "agent.conf",
          "AGENT=claude\n"
          f"claude.ENV=CLAUDE_CONFIG_DIR={spare}\n"
          "claude-alt.LIKE=claude\n")
    stubs.tool("quota-axi", per_account(
        "CLAUDE_CONFIG_DIR", str(spare),
        (auth(("claude", "available")), fetch(claude(64))),
        (auth(("claude", "available")), fetch(claude(7))),
    ))
    blocks = records(status("--fuel", FLEET_AGENT_ROOT=str(conf)).stdout)
    assert len(blocks) == 1, blocks
    assert blocks[0]["remaining"] == "7", blocks
    assert len(fetches(stubs)) == 1, fetches(stubs)
    assert len(stubs.calls("quota-axi", "auth")) == 1, stubs.calls("quota-axi")


def test_a_different_provider_sharing_the_checkouts_own_env_still_gets_its_own_account(tmp_path, stubs):
    """Byte-identical `env` is not enough to call a named entry "the checkout's
    own account under an alias" — `refuel`'s `account_key()` buckets by
    `(provider, env)`, and a DIFFERENT vendor sharing the checkout's own login
    (two agents under one broad line, `codex.ENV=` copying `claude`'s own
    `CLAUDE_CONFIG_DIR` here) is a different key by that same rule, whatever
    login it shares. Skipping it on env alone used to drop this account with no
    record at all whenever it had no credential: the checkout's own "every
    discovered provider" entry never asks about a provider discovery did not
    find, so nothing here ever named `codex` unavailable — the exact silent
    loss naming an account exists to end, reached through the checkout's own
    env instead of a distinct one."""
    spare = tmp_path / "spare-config"
    conf = tmp_path / "agentconf"
    write(conf / "orchestration" / "agent.conf",
          "AGENT=claude\n"
          f"ENV=CLAUDE_CONFIG_DIR={spare}\n"
          f"codex.ENV=CLAUDE_CONFIG_DIR={spare}\n")
    stubs.tool("quota-axi", quota_axi(auth(("claude", "available")), fetch(claude(64))))
    blocks = records(status("--fuel", FLEET_AGENT_ROOT=str(conf)).stdout)
    assert len(blocks) == 2, blocks
    by_account = {b.get("account", ""): b for b in blocks}
    assert "claude" in by_account and "codex" in by_account, blocks
    assert by_account["claude"]["remaining"] == "64", blocks
    assert "unavailable" in by_account["codex"] and "remaining" not in by_account["codex"], blocks
    # One shared environment, so one `auth` call between the two accounts —
    # never a second one just because a second entry now reaches the screen.
    assert len(stubs.calls("quota-axi", "auth")) == 1, stubs.calls("quota-axi")
    done = status(FLEET_AGENT_ROOT=str(conf))
    expect(done.out, "2 reading(s) over 2 account(s)",
           "claude  64% remaining", "codex (codex)  unavailable")


def test_a_checkout_wide_env_line_does_not_hand_every_mentioned_agent_an_account(tmp_path, stubs):
    """A bare `ENV=` line is the checkout's own default, and `agent_settings.value()`
    falls back to it for ANY agent `agent.conf` mentions — even one named only
    for an unrelated setting like `LIMIT_BANNER`. `fuel_accounts()`'s loop has
    to tell "this agent names its own account" from "this agent inherits the
    checkout's default like everyone else" using `named()`, never `value()`:
    the latter would turn a checkout-wide `ENV=` line into one phantom account
    per unrelated agent name, each paying its own `quota-axi auth` call and,
    once unmatched, showing a false failed account nobody configured."""
    spare = tmp_path / "spare-config"
    conf = tmp_path / "agentconf"
    write(conf / "orchestration" / "agent.conf",
          "AGENT=claude\n"
          f"ENV=CLAUDE_CONFIG_DIR={spare}\n"
          "opencode.LIMIT_BANNER=out of tokens\n")
    stubs.tool("quota-axi", per_account(
        "CLAUDE_CONFIG_DIR", str(spare),
        (auth(("claude", "available")), fetch(claude(64))),
        (auth(("claude", "available")), fetch(claude(7))),
    ))
    blocks = records(status("--fuel", FLEET_AGENT_ROOT=str(conf)).stdout)
    assert len(blocks) == 1, blocks
    assert len(fetches(stubs)) == 1, fetches(stubs)
    refute(status(FLEET_AGENT_ROOT=str(conf)).out, "opencode")


def test_a_named_account_whose_agent_equals_its_own_provider_still_gets_a_label(tmp_path, stubs):
    """`claude.ENV=...` with no `AGENT=` line gives the agent named `claude`
    a provider ALSO named `claude` — `fuel_agent()`'s own fallback makes an
    agent with no `LIKE` and no `AGENT_PROVIDERS` pin its own provider. Telling
    the two readings apart cannot key off `account != provider`: here it is
    exactly equal for the named account and empty for the checkout's, so only
    the explicit `checkout` flag on each record tells `fuel_label()` which one
    is which."""
    spare = tmp_path / "spare-config"
    conf = tmp_path / "agentconf"
    write(conf / "orchestration" / "agent.conf",
          f"claude.ENV=CLAUDE_CONFIG_DIR={spare}\n")
    stubs.tool("quota-axi", per_account(
        "CLAUDE_CONFIG_DIR", str(spare),
        (auth(("claude", "available")), fetch(claude(64))),
        (auth(("claude", "available")), fetch(claude(7))),
    ))
    blocks = records(status("--fuel", FLEET_AGENT_ROOT=str(conf)).stdout)
    assert [(b["provider"], b.get("account", ""), b["checkout"]) for b in blocks] == [
        ("claude", "", "1"), ("claude", "claude", "0")], blocks
    done = status(FLEET_AGENT_ROOT=str(conf))
    expect(done.out, "2 reading(s) over 2 account(s)",
           "  claude  64% remaining", "  claude (claude)  7% remaining")


def test_a_named_account_with_no_credential_stays_visible(tmp_path, stubs):
    """`quota-axi auth` naming nothing under a second account is a fact fleet
    obeys — `account_providers()` refuses to probe an account with no
    credential, for the reason its own docstring gives. Obeying it used to mean
    the account's whole record vanished from the screen and the `--fuel` wire,
    with the section's failure count read as if the account never existed. A
    vanished record cannot be told apart from an account nobody dispatches
    against; this one is dispatched against constantly, and staying silent
    about it is the exact failure `fleet status` exists to end."""
    spare = tmp_path / "spare-config"
    conf = tmp_path / "agentconf"
    write(conf / "orchestration" / "agent.conf",
          "AGENT=claude\n"
          "claude-spare.LIKE=claude\n"
          f"claude-spare.ENV=CLAUDE_CONFIG_DIR={spare}\n")
    stubs.tool("quota-axi", per_account(
        "CLAUDE_CONFIG_DIR", str(spare),
        (auth(("claude", "available")), fetch(claude(64))),
        (auth(), fetch()),
    ))
    blocks = records(status("--fuel", FLEET_AGENT_ROOT=str(conf)).stdout)
    assert [b["provider"] for b in blocks] == ["claude", "claude"], blocks
    assert [b["account"] for b in blocks] == ["claude", "claude-spare"], blocks
    assert "unavailable" in blocks[1] and "remaining" not in blocks[1], blocks
    # Named nothing means fleet trusts it and never probes it with a fetch.
    assert len(fetches(stubs)) == 1, fetches(stubs)
    assert len(stubs.calls("quota-axi", "auth")) == 2, stubs.calls("quota-axi")
    done = status(FLEET_AGENT_ROOT=str(conf))
    expect(done.out, "2 reading(s) over 2 account(s)",
           "  claude  64% remaining",
           "claude (claude-spare)  unavailable")


def test_the_checkouts_own_account_having_no_credential_does_not_hide_behind_anothers_reading(tmp_path, stubs):
    """`fuel_read([])` used to run anyway for the checkout's OWN account
    whenever discovery found no credential there, and the call contributed to
    neither `records` nor `failures` — an extra empty quota-axi process, and,
    with a second account that DID have a reading, that reading rendered as
    if it were the checkout's own: one record reaching the section, so
    `fuel_named()` saw one distinct account and dropped the very field that
    would have named it. The checkout's own account with nothing to read has
    to stay on screen as its own unavailable line, exactly like a named
    account with no credential does — and it takes the SAME fallback
    `authenticated_providers()` always applied for a single-account fleet
    (`AGENT=claude` names the guess), never a shortcut that skips the fetch:
    `probe_fuel()` (`refuel`'s own reading of this exact account) takes that
    fallback too, and skipping it here would have the screen call this
    account unavailable for a reason the gate would not agree with."""
    spare = tmp_path / "spare-config"
    conf = tmp_path / "agentconf"
    write(conf / "orchestration" / "agent.conf",
          "AGENT=claude\n"
          "claude-spare.LIKE=claude\n"
          f"claude-spare.ENV=CLAUDE_CONFIG_DIR={spare}\n")
    stubs.tool("quota-axi", per_account(
        "CLAUDE_CONFIG_DIR", str(spare),
        (auth(), fetch()),
        (auth(("claude", "available")), fetch(claude(70))),
    ))
    blocks = records(status("--fuel", FLEET_AGENT_ROOT=str(conf)).stdout)
    assert len(blocks) == 2, blocks
    by_account = {b.get("account", ""): b for b in blocks}
    assert "claude" in by_account and "claude-spare" in by_account, blocks
    assert "unavailable" in by_account["claude"] and "remaining" not in by_account["claude"], blocks
    assert by_account["claude-spare"]["remaining"] == "70", blocks
    # Two distinct environments, so two `auth` calls and two fetches — the
    # checkout's own guessed "claude" from `AGENT=claude` fetches like any
    # named account's guess would, and comes back naming no provider.
    assert len(stubs.calls("quota-axi", "auth")) == 2, stubs.calls("quota-axi")
    assert len(fetches(stubs)) == 2, fetches(stubs)
    done = status(FLEET_AGENT_ROOT=str(conf))
    expect(done.out, "2 reading(s) over 2 account(s)",
           "claude  unavailable — quota-axi reported no claude provider",
           "claude (claude-spare)  70% remaining")


def test_two_providers_under_one_shared_environment_cost_one_auth_and_fetch_pair(tmp_path, stubs):
    """An `ENV` line need not be a vendor-specific `*_CONFIG_DIR` — a broader
    one, such as `HOME=`, moves every vendor's credential at once, so two
    agents naming two different providers under the SAME `ENV` line are two
    ACCOUNTS (different provider) but one ENVIRONMENT. `fuel_accounts()`'s own
    dedup keys on `(provider, env)` and never merges these two entries, so the
    saving has to happen in `probe_fuel_all()`: one `auth` and one
    `--provider claude,codex` fetch for the shared environment, never a pair
    per account sharing it — the same "asked once, split after" a checkout
    that named a second LOGIN already gets, applied here to two vendors
    sharing one login instead."""
    shared = str(tmp_path / "worker2-home")
    conf = tmp_path / "agentconf"
    write(conf / "orchestration" / "agent.conf",
          "AGENT=lead\n"
          f"claude.ENV=HOME={shared}\n"
          f"codex.ENV=HOME={shared}\n")
    here_auth, here_fetch = auth(), fetch()
    there_auth = auth(("claude", "available"), ("codex", "available"))
    there_fetch = fetch(
        claude(55),
        {"provider": "codex", "windows": [window("five_hour", 33, "2026-03-15T20:00:00.000Z")],
         "state": {"status": "ok", "stale": False}},
    )
    stubs.tool("quota-axi", (
        "import json, os, sys\n"
        f"SHARED = {shared!r}\n"
        f"HERE_AUTH = {json.dumps(here_auth)!r}\n"
        f"HERE_FETCH = {json.dumps(here_fetch)!r}\n"
        f"THERE_AUTH = {json.dumps(there_auth)!r}\n"
        f"THERE_FETCH = {json.dumps(there_fetch)!r}\n"
        "shared = os.environ.get('HOME') == SHARED\n"
        "auth_mode = sys.argv[1:2] == ['auth']\n"
        "doc = (THERE_AUTH if auth_mode else THERE_FETCH) if shared else (HERE_AUTH if auth_mode else HERE_FETCH)\n"
        "sys.stdout.write(doc)\n"
    ))
    done = status(FLEET_AGENT_ROOT=str(conf))
    expect(done.out, "claude (claude)  55% remaining", "codex (codex)  33% remaining")
    calls = stubs.calls("quota-axi")
    auth_calls = [c for c in calls if c.startswith("quota-axi auth")]
    fetch_calls = fetches(stubs)
    # Two DISTINCT ENVIRONMENTS — the checkout's own (unset, and itself
    # unavailable) and the one `claude` and `codex` share — cost one `auth`
    # each, never one per account.
    assert len(auth_calls) == 2, calls
    # The checkout's own guessed fallback is its own environment and pays its
    # own fetch; the shared environment's two providers still cost exactly
    # ONE fetch between them, never two.
    assert len(fetch_calls) == 2, calls
    assert any("claude,codex" in c or "codex,claude" in c for c in fetch_calls), fetch_calls


def test_every_account_failing_for_a_different_reason_keeps_every_reason(tmp_path, stubs):
    """A single-account fleet collapsing its one failure to the section's own
    `unavailable` line is the exact rendering this feature must never disturb
    — the hard constraint that a checkout naming no dotted `ENV` key prints
    byte-identically to before. Generalising that collapse to "every account
    failed" reintroduces the same silent loss naming an account exists to
    end, just moved up a level: with two accounts failing for two DIFFERENT
    reasons, reporting only the first would hide the second's reason behind
    the first's, exactly as an unlabelled second reading used to hide behind
    the first's numbers. Two or more accounts must always keep every one of
    their own records, however many of them failed."""
    broken = tmp_path / "broken-config"
    conf = tmp_path / "agentconf"
    write(conf / "orchestration" / "agent.conf",
          "AGENT=claude\n"
          "claude-broken.LIKE=claude\n"
          f"claude-broken.ENV=CLAUDE_CONFIG_DIR={broken}\n")
    # BOTH environments' `auth` reads fine and names a real credential — the
    # fetch itself is what fails, and fails for a DIFFERENT reason each time,
    # so both readings land in the top-level `failures` list rather than one
    # of them going through `fuel_record()`'s own "provider not in the doc"
    # branch, which never touches `failures` at all.
    stubs.tool("quota-axi", (
        "import os, sys\n"
        f"BROKEN = {str(broken)!r}\n"
        "there = os.environ.get('CLAUDE_CONFIG_DIR') == BROKEN\n"
        "auth_mode = sys.argv[1:2] == ['auth']\n"
        "if auth_mode:\n"
        "    sys.stdout.write("
        "'{\"generatedAt\": \"2026-03-15T16:42:00.000Z\", \"schemaVersion\": 1, "
        "\"auth\": [{\"provider\": \"claude\", \"sources\": "
        "[{\"source\": \"oauth-file\", \"status\": \"available\"}]}]}')\n"
        "else:\n"
        "    sys.stderr.write('quota-axi: rate limited\\n' if there else "
        "'quota-axi: network unreachable\\n')\n"
        "    sys.exit(1)\n"
    ))
    blocks = records(status("--fuel", FLEET_AGENT_ROOT=str(conf)).stdout)
    assert len(blocks) == 2, blocks
    by_account = {b.get("account", ""): b for b in blocks}
    assert "claude" in by_account and "claude-broken" in by_account, blocks
    assert "network unreachable" in by_account["claude"]["unavailable"], blocks
    assert "rate limited" in by_account["claude-broken"]["unavailable"], blocks
    done = status(FLEET_AGENT_ROOT=str(conf))
    assert "unavailable —" not in done.out.split("FUEL", 1)[1].splitlines()[0], done.out
    expect(done.out, "2 reading(s) over 2 account(s)",
           "claude  unavailable — quota-axi exited 1: quota-axi: network unreachable",
           "claude (claude-broken)  unavailable — quota-axi exited 1: quota-axi: rate limited")


def test_the_not_discovered_hint_names_only_the_account_whose_auth_failed(tmp_path, stubs):
    """One account's `quota-axi auth` being unreadable is a fact about THAT
    account. `sec['discovery']` records only the first such failure across all
    accounts, but the "not discovered" hint line built from it has to name
    just the records that actually took the checkout-wide fallback — an
    account whose own discovery succeeded is not "read alone" just because a
    sibling account's discovery broke."""
    spare = tmp_path / "spare-config"
    conf = tmp_path / "agentconf"
    write(conf / "orchestration" / "agent.conf",
          "AGENT=claude\n"
          "claude-spare.LIKE=claude\n"
          f"claude-spare.ENV=CLAUDE_CONFIG_DIR={spare}\n")
    stubs.tool("quota-axi", (
        "import os, sys\n"
        f"if os.environ.get('CLAUDE_CONFIG_DIR') == {str(spare)!r} and sys.argv[1:2] == ['auth']:\n"
        "    sys.exit(1)\n"
        f"AUTH = {json.dumps(auth(('claude', 'available')))!r}\n"
        f"FETCH = {json.dumps(fetch(claude(64)))!r}\n"
        "sys.stdout.write(AUTH if sys.argv[1:2] == ['auth'] else FETCH)\n"
    ))
    done = status(FLEET_AGENT_ROOT=str(conf))
    expect(done.out, "read claude (claude-spare) alone")
    refute(done.out, "read claude, claude (claude-spare) alone",
           "read claude (claude-spare), claude alone")


# --- 7. it reads, and only reads ----------------------------------------------


def snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_not_a_byte_of_the_queue_is_touched(queue, stubbed):
    before = snapshot(queue)
    status()
    status("--json")
    assert snapshot(queue) == before

