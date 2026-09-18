"""§12c: what fleet knows about an agent is asked PER AGENT, not per checkout.

Four things here are facts about an agent — the trust dialog it shows, the
sentence it prints at its limit, where its transcripts are, and which account
it draws on — and `orchestration/agent.conf` held one answer each for the whole
checkout. The case that breaks is the SECOND ACCOUNT of an agent fleet already
knows: it runs under a name of its own, so it matches no built-in row, and the
only override was the checkout's single line. Serving it meant editing that
line before each dispatch and putting it back afterwards, and the fuel gauge
read whichever account the LEAD was signed in to, so every such worker was
reported `undetermined` and had no autopilot at all.

Each test here reaches its fix through the settings file, never by calling the
function: what is being held is that the configuration is WIRED to the code,
which is the half a direct call proves nothing about.

The agent named throughout is `spare`, `LIKE` the one agent this suite's
fixtures run. Nothing here names a real account, vendor or path.
"""

from __future__ import annotations

import json

import pytest
from kit_dispatch import ANSWERING_KEYS, FOLDER_DIALOG, behind_dialog, keys
from kit_refuel import (
    ACCOUNT_QUOTA, CLAUDE_BANNER, account_quota, attach_task, pane, quota_is, restarts, teach, transcript,
)
from queuekit import ok

from harness import PYTHON, REPO, expect, refute, run, write
from harness import run_queue as q

TRUST = REPO / "scripts" / "lib" / "session_trust.py"

SPARE = "cccccccc-0000-0000-0000-00000000000{}"
RESETS = "2026-09-10T02:10:00+00:00"


# --- 1. the trust dialog ------------------------------------------------------


@pytest.fixture
def answering(stubs) -> None:
    stubs.tool("thurbox-cli", ANSWERING_KEYS)


def test_a_second_account_answers_the_dialog_of_the_agent_it_is_like(stubs, answering, isolated_env):
    """`spare` is in no table and has no dialog of its own. `LIKE` gives it one.

    Without the line it is an unknown agent, and an unknown agent is REFUSED
    rather than guessed at — correctly, since a bare Enter into this very
    dialog exits the agent. So the worker sat there, and the operator's way
    round it was to rewrite the checkout's `TRUST_SIGNATURE` per dispatch.
    """
    sid = SPARE.format(1)
    behind_dialog(stubs, sid, FOLDER_DIALOG, agent="spare")

    refused = run([*PYTHON, str(TRUST), sid, "--timeout", "3"])
    assert refused.code == 3, refused.out
    expect(refused.out, "no trust gate is known for 'spare'", "spare.LIKE=")
    assert keys(stubs) == [], "a keystroke went into a dialog fleet could not identify"

    teach(isolated_env, "spare.LIKE=claude")
    done = run([*PYTHON, str(TRUST), sid, "--timeout", "5"])
    assert done.code == 0, done.out
    # Down first: this dialog's default selection is "No, exit". The whole
    # reason the table may not be guessed at is in that one keystroke.
    assert keys(stubs) == [f"session key {sid} down", f"session key {sid} enter"], done.out


def test_a_dialog_named_for_one_agent_leaves_the_checkouts_own_alone(stubs, answering, isolated_env):
    """`<agent>.TRUST_SIGNATURE` outranks the table; the plain one still answers
    for every agent with no line of its own."""
    teach(isolated_env, "TRUST_SIGNATURE=a dialog the whole checkout shows",
          "spare.TRUST_SIGNATURE=is this workspace one you trust", "spare.TRUST_KEYS=enter")
    sid = SPARE.format(2)
    behind_dialog(stubs, sid, "Is this workspace one you trust?\n\n  ❯ Yes\n    No\n", agent="spare")

    done = run([*PYTHON, str(TRUST), sid, "--timeout", "5"])
    assert done.code == 0, done.out
    assert keys(stubs) == [f"session key {sid} enter"], done.out


# --- 2. the limit banner ------------------------------------------------------


@pytest.fixture
def spare_worker(stubs, isolated_env) -> str:
    """One task running `spare`, `working` for two hours, its pane on a limit banner."""
    topic = ok(q("topic", "add", "spare-account", "--title", "A worker on a second account",
                 "--prompt", "the second account of the agent this fleet runs")).stdout.strip()
    sid = SPARE.format(3)
    ok(q("add", topic, "on-spare", "--title", "Task on the spare account", "--repo", "/tmp/repo-a",
         "--branch", "fix/on-spare", "--number", "01", "--agent", "spare"))
    ok(q("attach", f"{topic}/01-on-spare", sid))
    stubs.session_is(sid, "working", 7200, agent="spare")
    pane(stubs, sid, f"● Now I will run the gate.\n\n{CLAUDE_BANNER}\n")
    return topic


def test_the_limit_banner_of_a_second_account_comes_from_the_row_it_is_like(spare_worker, stubs, isolated_env):
    """The banner is the same sentence — it is the same agent — but the name it
    runs under is not in the table, so nothing matched it before."""
    quota_is(stubs, 62, RESETS)

    out = q("refuel", "--dry-run").out
    expect(out, "undetermined", "has not watched `spare` hit a limit")
    refute(out, "would restart")

    teach(isolated_env, "spare.LIKE=claude")
    out = q("refuel", "--dry-run").out
    expect(out, "would restart", "the pane ends on the agent's own banner")
    assert restarts(stubs) == [], "a dry run restarted a session"


# --- 3. the transcript, read under the AGENT's account and not the lead's -----


def test_the_transcript_is_read_under_the_agents_own_account(spare_worker, stubs, isolated_env, tmp_path):
    """`transcript_root()` used to resolve `CLAUDE_CONFIG_DIR` out of `os.environ`.

    That is the LEAD's environment — a Mission Control session on the main
    account — so the answer about a worker came from a process that is not the
    worker. Here the lead's own variable points somewhere with no record at
    all, and the agent's `ENV` line points at the records that exist.
    """
    quota_is(stubs, 62, RESETS)
    sid = SPARE.format(3)
    pane(stubs, sid, None)  # nothing on the pane: the transcript is the only evidence
    spare = tmp_path / "spare-account"
    (spare / "projects" / "-tmp-repo-a").mkdir(parents=True)
    transcript(spare / "projects" / "-tmp-repo-a" / f"agent-{sid}.jsonl", "five_hour", CLAUDE_BANNER)
    # THE TRIPWIRE: the lead's own directory holds a record for the same session
    # whose last turn is an ORDINARY one. Reading it says the worker is merely
    # slow, which is the wrong answer arrived at from the wrong process.
    lead_home = tmp_path / "lead-account"
    (lead_home / "projects" / "-tmp-repo-a").mkdir(parents=True)
    write(lead_home / "projects" / "-tmp-repo-a" / f"agent-{sid}.jsonl",
          json.dumps({"type": "assistant", "timestamp": "2026-09-08T19:00:00.000Z",
                      "message": {"role": "assistant", "content": [{"type": "text", "text": "on it"}]}}) + "\n")

    teach(isolated_env, "spare.LIKE=claude")
    out = q("refuel", "--dry-run", CLAUDE_CONFIG_DIR=str(lead_home)).out
    refute(out, "its last turn is an ordinary one", "would restart")

    teach(isolated_env, f"spare.ENV=CLAUDE_CONFIG_DIR={spare}")
    out = q("refuel", "--dry-run", CLAUDE_CONFIG_DIR=str(lead_home)).out
    expect(out, "would restart", "five_hour", "rejected")


# --- 4. two accounts on one provider, each judged by its own window -----------


@pytest.fixture
def two_accounts(stubs, isolated_env, tmp_path) -> str:
    """Two workers, one provider, two accounts: `01` on the checkout's agent, `02` on `spare`."""
    topic = ok(q("topic", "add", "two-accounts", "--title", "Two accounts, one provider",
                 "--prompt", "one window each")).stdout.strip()
    attach_task(topic, "on-main", "01", SPARE.format(4))
    ok(q("add", topic, "on-spare", "--title", "Task on the spare account", "--repo", "/tmp/repo-a",
         "--branch", "fix/on-spare", "--number", "02", "--agent", "spare"))
    ok(q("attach", f"{topic}/02-on-spare", SPARE.format(5)))
    for n, agent in ((4, "claude"), (5, "spare")):
        stubs.session_is(SPARE.format(n), "working", 7200, agent=agent)
        pane(stubs, SPARE.format(n), f"● Now I will run the gate.\n\n{CLAUDE_BANNER}\n")
    stubs.tool("quota-axi", ACCOUNT_QUOTA)
    # The window each account holds, keyed by the directory that selects it.
    account_quota(stubs, "default", 62, RESETS)
    account_quota(stubs, "spare-account", 0, RESETS)
    teach(isolated_env, "spare.LIKE=claude", f"spare.ENV=CLAUDE_CONFIG_DIR={tmp_path / 'spare-account'}")
    return topic


def test_two_accounts_on_one_provider_are_read_and_judged_separately(two_accounts, stubs):
    """One reading per ACCOUNT, not one per provider and certainly not one per pass.

    Before, `refuel` took a single provider for the whole sweep and reported
    every task whose agent was not that name `undetermined` — so a worker on a
    second login was never judged at all, whatever its own window said.
    """
    out = q("refuel", "--dry-run").out
    # Two account lines, and the spare one is named by the agent that holds it.
    expect(out, "account claude", "claude (spare)", "62% remaining", "0% remaining")
    # The main account has fuel and that worker is on its limit banner.
    expect(out, "would restart")
    # The spare account's window is spent, so its worker is left where it is.
    expect(out, "02-on-spare", "window is spent")
    refute(out, "02-on-spare                                     would restart")
    assert restarts(stubs) == []


def test_one_reading_per_account_and_not_one_per_task(two_accounts, stubs):
    """The cost this keeps fixed: two tasks on one account are still one call.

    A reading per task would burn the window it reports — the same reason
    `probe_fuel_all` asks quota-axi for every provider in one invocation.
    """
    attach_task(two_accounts, "also-on-main", "03", SPARE.format(7))
    stubs.session_is(SPARE.format(7), "working", 7200)
    q("refuel", "--dry-run")
    calls = stubs.calls("quota-axi", "--provider")
    assert len(calls) == 2, f"three tasks, two accounts, and {len(calls)} readings: {calls}"


# --- the checkout that has none of this --------------------------------------


def test_a_checkout_with_no_per_agent_line_reads_what_it_always_read(stubs, isolated_env, queue_dir):
    """The whole mechanism is absent unless a dotted line is written."""
    topic = ok(q("topic", "add", "plain", "--title", "No per-agent settings at all",
                 "--prompt", "a checkout as it was")).stdout.strip()
    sid = SPARE.format(6)
    attach_task(topic, "plain", "01", sid)
    stubs.session_is(sid, "working", 7200)
    pane(stubs, sid, f"● Now I will run the gate.\n\n{CLAUDE_BANNER}\n")
    quota_is(stubs, 62, RESETS)

    conf = (isolated_env / "settings" / "orchestration" / "agent.conf").read_text(encoding="utf-8")
    assert "." not in "".join(line.partition("=")[0] for line in conf.splitlines()), conf
    out = q("refuel", "--dry-run").out
    expect(out, "account claude", "62% remaining", "would restart")


def test_a_fresh_clone_resolves_nothing_for_any_agent(tmp_path):
    """A root holding the tracked example and nothing else describes no agent.

    That the example names none is `tests/settings/test_automerge.py`'s rule;
    this is the other half — that reading it back answers empty rather than
    something the file merely documents in a comment.
    """
    text = (REPO / "orchestration" / "agent.example.conf").read_text(encoding="utf-8")
    (tmp_path / "orchestration").mkdir()
    write(tmp_path / "orchestration" / "agent.example.conf", text)
    read = (
        "import json\nimport sys\n"
        "sys.path.insert(0, 'scripts/lib')\n"
        "import agent_settings as a\n"
        "conf = a.conf(sys.argv[1])\n"
        "print(json.dumps([a.agents(conf), a.account_env('any-agent', conf),\n"
        "                  a.value('LIMIT_BANNER', 'any-agent', conf)]))\n"
    )
    out = run([*PYTHON, "-c", read, str(tmp_path)], cwd=REPO)
    assert out.code == 0, out.out
    assert json.loads(out.stdout) == [[], {}, ""], out.out
