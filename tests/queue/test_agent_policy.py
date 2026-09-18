"""Agent policy: which agents may serve which repositories.

A rule maps a host-qualified repository prefix to an ordered list of agents.
The first is the default; the rest are allowed only when named explicitly.
A task whose repository cannot be determined fails closed when any policy is
in force.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from kit_dispatch import ANSWERING_KEYS, next_session
from kit_forges import FakeForgeStore
from kit_refuel import ACCOUNT_QUOTA, CLAUDE_BANNER, account_quota, pane, restarts, teach
from kit_shepherd import Shep, repo
from kit_shepherd import result as shipped
from queuekit import ok

from harness import expect, git, refute
from harness import run_queue as q

FORGE = "forge.test:8443"
OWNER = "acme"
REPO_NAME = "widgets"
QUALIFIED = f"{FORGE}/{OWNER}"
QUALIFIED_REPO = f"{FORGE}/{OWNER}/{REPO_NAME}"


@pytest.fixture
def forge_store(tmp_path) -> FakeForgeStore:
    return FakeForgeStore(tmp_path / "fake-forge")


@pytest.fixture
def checkout(tmp_path, forge_store) -> Path:
    """A real git checkout whose origin points at the fake forge."""
    repo = tmp_path / "repo"
    git("init", "-q", "-b", "main", str(repo))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=repo)
    git("remote", "add", "origin", f"https://{FORGE}/{OWNER}/{REPO_NAME}.git", cwd=repo)
    return repo


def policy_env(store: FakeForgeStore, policy: str) -> dict[str, str]:
    return {
        "FLEET_AGENT_POLICY": policy,
        "FLEET_FORGE_PLUGINS": str(store.plugin),
        "FAKE_FORGE_DIR": str(store.root),
    }


def fill_brief(path: Path) -> None:
    placeholder = "<!-- WRITE THE INSTRUCTIONS HERE -->"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            placeholder,
            "Make the change, open a pull request, and write the result file below.",
        ),
        encoding="utf-8",
    )


def test_single_agent_rule_rejects_foreign_agent_on_add(checkout, forge_store):
    topic = q(
        "topic", "add", "agent-policy", "--title", "Agent policy",
        "--prompt", "enforce agent policy",
    ).stdout.strip()
    done = q(
        "add", topic, "use-wrong-agent", "--title", "Wrong agent",
        "--repo", str(checkout), "--branch", "fix/wrong", "--number", "01",
        "--agent", "not-allowed",
        **policy_env(forge_store, f"{QUALIFIED_REPO}=allowed-agent"),
    )
    assert done.code != 0, done.out
    assert "not-allowed" in done.out
    assert "allowed-agent" in done.out


def test_list_policy_defaults_first_and_allows_second_explicitly(checkout, forge_store, stubs):
    topic = q(
        "topic", "add", "agent-list", "--title", "Agent list",
        "--prompt", "list policy",
    ).stdout.strip()
    env = policy_env(forge_store, f"{QUALIFIED_REPO}=alpha, beta")
    ok(q(
        "add", topic, "default-agent", "--title", "Default agent",
        "--repo", str(checkout), "--branch", "fix/default", "--number", "01",
        **env,
    ))
    ok(q(
        "add", topic, "explicit-agent", "--title", "Explicit agent",
        "--repo", str(checkout), "--branch", "fix/explicit", "--number", "02",
        "--agent", "beta",
        **env,
    ))
    queue_dir = Path(os.environ["FLEET_QUEUE_DIR"])
    fill_brief(queue_dir / topic / "01-default-agent" / "BRIEF.md")
    fill_brief(queue_dir / topic / "02-explicit-agent" / "BRIEF.md")

    sid = "a1111111-1111-1111-1111-111111111111"
    next_session(stubs, sid)
    stubs.tool("thurbox-cli", ANSWERING_KEYS)

    q("dispatch", **env)
    creates = [c for c in stubs.calls("thurbox-cli", "session create") if "--repo-path" in c]
    default = [c for c in creates if "fix/default" in c][0]
    explicit = [c for c in creates if "fix/explicit" in c][0]
    assert "--agent alpha" in default, default
    assert "--agent beta" in explicit, explicit


def test_no_policy_keeps_todays_behaviour(checkout, forge_store, stubs):
    topic = q(
        "topic", "add", "no-policy", "--title", "No policy",
        "--prompt", "no agent policy",
    ).stdout.strip()
    ok(q(
        "add", topic, "any-agent", "--title", "Any agent",
        "--repo", str(checkout), "--branch", "fix/any", "--number", "01",
        "--agent", "anything",
        **policy_env(forge_store, ""),
    ))
    fill_brief(Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "01-any-agent" / "BRIEF.md")
    sid = "b2222222-2222-2222-2222-222222222222"
    next_session(stubs, sid)
    stubs.tool("thurbox-cli", ANSWERING_KEYS)
    q("dispatch", **policy_env(forge_store, ""))
    create = [c for c in stubs.calls("thurbox-cli", "session create") if "fix/any" in c][0]
    assert "--agent anything" in create, create


def test_host_task_fails_closed_when_policy_exists(forge_store):
    topic = q(
        "topic", "add", "host-policy", "--title", "Host policy",
        "--prompt", "host task under policy",
    ).stdout.strip()
    ok(q(
        "add", topic, "remote", "--title", "Remote",
        "--repo", "/remote/repo", "--branch", "fix/remote", "--number", "01",
        "--host", "devbox",
    ))
    fill_brief(Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "01-remote" / "BRIEF.md")
    done = q("dispatch", **policy_env(forge_store, f"{QUALIFIED_REPO}=alpha"))
    assert "--host task cannot be checked against agent policy" in done.out, done.out
    # A refused task is not a silent no-op: a script or hook reading the exit
    # code must see that the batch did not go out cleanly.
    assert done.code != 0, done.out


def test_bare_owner_repo_entry_is_ignored_with_message(checkout, forge_store):
    topic = q(
        "topic", "add", "ignore-bare", "--title", "Ignore bare",
        "--prompt", "ignore bare owner",
    ).stdout.strip()
    done = q(
        "add", topic, "bare", "--title", "Bare",
        "--repo", str(checkout), "--branch", "fix/bare", "--number", "01",
        "--agent", "anything",
        **policy_env(forge_store, f"{OWNER}/{REPO_NAME}=alpha"),
    )
    assert done.code == 0, done.out
    assert "ignoring" in done.stderr.lower(), done.stderr


def test_entry_with_no_agents_is_ignored_with_message(checkout, forge_store):
    topic = q(
        "topic", "add", "ignore-empty", "--title", "Ignore empty",
        "--prompt", "ignore empty agents",
    ).stdout.strip()
    done = q(
        "add", topic, "empty", "--title", "Empty",
        "--repo", str(checkout), "--branch", "fix/empty", "--number", "01",
        "--agent", "anything",
        **policy_env(forge_store, f"{QUALIFIED_REPO}"),
    )
    assert done.code == 0, done.out
    assert "ignoring" in done.stderr.lower(), done.stderr


def test_profile_with_command_fails_closed_on_covered_repo(checkout, forge_store, stubs):
    topic = q(
        "topic", "add", "command-profile", "--title", "Command profile",
        "--prompt", "profile with command",
    ).stdout.strip()
    ok(q(
        "add", topic, "commanded", "--title", "Commanded",
        "--repo", str(checkout), "--branch", "fix/command", "--number", "01",
        "--profile", "cursor-trusted",
        **policy_env(forge_store, f"{QUALIFIED_REPO}=alpha"),
    ))
    fill_brief(Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "01-commanded" / "BRIEF.md")
    out = q(
        "dispatch", **policy_env(forge_store, f"{QUALIFIED_REPO}=alpha"),
    ).out
    assert "profile carries a custom `command`" in out, out


def test_repo_rule_overrides_owner_rule(checkout, forge_store, stubs):
    topic = q(
        "topic", "add", "override", "--title", "Override",
        "--prompt", "repo overrides owner",
    ).stdout.strip()
    env = policy_env(forge_store, f"{QUALIFIED}=owner-agent {QUALIFIED_REPO}=repo-agent")
    ok(q(
        "add", topic, "repo-rule", "--title", "Repo rule",
        "--repo", str(checkout), "--branch", "fix/repo", "--number", "01",
        **env,
    ))
    fill_brief(Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "01-repo-rule" / "BRIEF.md")
    sid = "c3333333-3333-3333-3333-333333333333"
    next_session(stubs, sid)
    stubs.tool("thurbox-cli", ANSWERING_KEYS)
    q("dispatch", **env)
    create = [c for c in stubs.calls("thurbox-cli", "session create") if "fix/repo" in c][0]
    assert "--agent repo-agent" in create, create


def test_different_case_owner_still_matches(checkout, forge_store, stubs):
    git("remote", "set-url", "origin", f"https://{FORGE}/{OWNER.upper()}/{REPO_NAME}.git", cwd=checkout)
    topic = q(
        "topic", "add", "casefold", "--title", "Casefold",
        "--prompt", "case insensitive matching",
    ).stdout.strip()
    env = policy_env(forge_store, f"{QUALIFIED}=case-agent")
    ok(q(
        "add", topic, "cased", "--title", "Cased",
        "--repo", str(checkout), "--branch", "fix/case", "--number", "01",
        **env,
    ))
    fill_brief(Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "01-cased" / "BRIEF.md")
    sid = "d4444444-4444-4444-4444-444444444444"
    next_session(stubs, sid)
    stubs.tool("thurbox-cli", ANSWERING_KEYS)
    q("dispatch", **env)
    create = [c for c in stubs.calls("thurbox-cli", "session create") if "fix/case" in c][0]
    assert "--agent case-agent" in create, create


def test_recorded_agent_caught_on_dispatch_when_policy_appears(checkout, forge_store, stubs):
    topic = q(
        "topic", "add", "recorded", "--title", "Recorded agent",
        "--prompt", "agent recorded before policy",
    ).stdout.strip()
    ok(q(
        "add", topic, "old", "--title", "Old",
        "--repo", str(checkout), "--branch", "fix/old", "--number", "01",
        "--agent", "old-agent",
        **policy_env(forge_store, ""),
    ))
    fill_brief(Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "01-old" / "BRIEF.md")
    out = q("dispatch", **policy_env(forge_store, f"{QUALIFIED_REPO}=new-agent")).out
    assert "task records agent" in out, out
    assert "old-agent" in out
    assert "new-agent" in out


def test_env_policy_allows_spaces_around_equals(checkout, forge_store):
    topic = q(
        "topic", "add", "spaces", "--title", "Spaces",
        "--prompt", "spaces around equals",
    ).stdout.strip()
    env = policy_env(forge_store, f"{QUALIFIED_REPO} = alpha, beta")
    ok(q(
        "add", topic, "allowed", "--title", "Allowed",
        "--repo", str(checkout), "--branch", "fix/allowed", "--number", "01",
        "--agent", "beta",
        **env,
    ))
    done = q(
        "add", topic, "disallowed", "--title", "Disallowed",
        "--repo", str(checkout), "--branch", "fix/disallowed", "--number", "02",
        "--agent", "gamma",
        **env,
    )
    assert done.code != 0, done.out
    assert "gamma" in done.out
    assert "alpha" in done.out


def test_env_policy_allows_spaces_on_both_sides_of_comma(checkout, forge_store):
    # A space BEFORE the comma, not just after: `re.sub(r",\s+", ",", ...)`
    # only ever normalised the space that follows a comma, so "alpha , beta"
    # left a dangling `,beta` behind, split by the entry splitter into an
    # entry with no `=`, and dropped with a stderr message rather than kept
    # as the second allowed agent.
    topic = q(
        "topic", "add", "comma-spaces", "--title", "Comma spaces",
        "--prompt", "spaces surround the comma too",
    ).stdout.strip()
    env = policy_env(forge_store, f"{QUALIFIED_REPO} = alpha , beta")
    ok(q(
        "add", topic, "second-agent", "--title", "Second agent",
        "--repo", str(checkout), "--branch", "fix/second", "--number", "01",
        "--agent", "beta",
        **env,
    ))


def test_empty_env_policy_replaces_file(checkout, forge_store):
    policy_root = Path(os.environ["FLEET_AGENT_POLICY_ROOT"]) / "orchestration"
    policy_root.mkdir(parents=True, exist_ok=True)
    (policy_root / "agent-policy.conf").write_text(
        f"{QUALIFIED_REPO}=restricted-agent\n", encoding="utf-8"
    )
    topic = q(
        "topic", "add", "empty-env", "--title", "Empty env",
        "--prompt", "empty env replaces file",
    ).stdout.strip()
    env = policy_env(forge_store, "")
    ok(q(
        "add", topic, "free", "--title", "Free",
        "--repo", str(checkout), "--branch", "fix/free", "--number", "01",
        "--agent", "anything",
        **env,
    ))


def test_dispatch_reports_policy_refusal_per_task_and_continues_batch(checkout, forge_store, stubs):
    topic = q(
        "topic", "add", "batch", "--title", "Batch",
        "--prompt", "batch policy refusal",
    ).stdout.strip()
    env = policy_env(forge_store, f"{QUALIFIED_REPO}=alpha")
    ok(q(
        "add", topic, "ok", "--title", "ok", "--repo", str(checkout),
        "--branch", "fix/ok", "--number", "01", "--agent", "alpha",
        **env,
    ))
    fill_brief(Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "01-ok" / "BRIEF.md")
    # The bad task records an agent that the policy forbids, as if the policy
    # appeared after the task was added.
    ok(q(
        "add", topic, "bad", "--title", "bad", "--repo", str(checkout),
        "--branch", "fix/bad", "--number", "02",
        **env,
    ))
    bad_task = Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "02-bad" / "task.yaml"
    bad_task.write_text(
        bad_task.read_text(encoding="utf-8").replace("agent: null", "agent: beta"),
        encoding="utf-8",
    )
    fill_brief(Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "02-bad" / "BRIEF.md")
    ok(q(
        "add", topic, "also-ok", "--title", "also-ok", "--repo", str(checkout),
        "--branch", "fix/also-ok", "--number", "03", "--agent", "alpha",
        **env,
    ))
    fill_brief(Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "03-also-ok" / "BRIEF.md")
    sid = "e5555555-5555-5555-5555-555555555555"
    next_session(stubs, sid)
    stubs.tool("thurbox-cli", ANSWERING_KEYS)
    done = q("dispatch", **env)
    assert "NOT SPAWNED" in done.out, done.out
    assert "beta" in done.out
    # `spawn_commands` used to bake the ref into the refusal text itself, and
    # `cmd_dispatch` prefixed it a second time, so the ref appeared twice on
    # the same line.
    assert done.out.count("02-bad") == 1, done.out
    # A batch with a refused task is not a clean run: something reading the
    # exit code must be able to tell the difference.
    assert done.code != 0, done.out
    creates = stubs.calls("thurbox-cli", "session create")
    assert len(creates) == 2, creates
    assert all("fix/ok" in c or "fix/also-ok" in c for c in creates), creates


def test_dispatch_dry_run_reports_policy_refusal_per_task_and_continues_batch(checkout, forge_store):
    topic = q(
        "topic", "add", "dry-batch", "--title", "Dry batch",
        "--prompt", "dry-run policy refusal",
    ).stdout.strip()
    env = policy_env(forge_store, f"{QUALIFIED_REPO}=alpha")
    ok(q(
        "add", topic, "ok", "--title", "ok", "--repo", str(checkout),
        "--branch", "fix/ok", "--number", "01", "--agent", "alpha",
        **env,
    ))
    fill_brief(Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "01-ok" / "BRIEF.md")
    # As above, an agent recorded before the policy existed.
    ok(q(
        "add", topic, "bad", "--title", "bad", "--repo", str(checkout),
        "--branch", "fix/bad", "--number", "02",
        **env,
    ))
    bad_task = Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "02-bad" / "task.yaml"
    bad_task.write_text(
        bad_task.read_text(encoding="utf-8").replace("agent: null", "agent: beta"),
        encoding="utf-8",
    )
    fill_brief(Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "02-bad" / "BRIEF.md")
    ok(q(
        "add", topic, "also-ok", "--title", "also-ok", "--repo", str(checkout),
        "--branch", "fix/also-ok", "--number", "03", "--agent", "alpha",
        **env,
    ))
    fill_brief(Path(os.environ["FLEET_QUEUE_DIR"]) / topic / "03-also-ok" / "BRIEF.md")

    # `--dry-run` used to call `spawn_commands` with no `try` at all, so it
    # died on the first refused task instead of showing the whole batch.
    done = q("dispatch", "--dry-run", **env)
    assert done.out.count("thurbox-cli session create") == 2, done.out
    assert "01-ok" in done.out, done.out
    assert "03-also-ok" in done.out, done.out
    assert "02-bad: NOT SPAWNED" in done.out, done.out
    assert done.out.count("02-bad") == 1, done.out
    assert "beta" in done.out, done.out
    assert done.code != 0, done.out


def test_prompt_reports_policy_refusal_per_task_and_continues_batch(checkout, forge_store, stubs):
    topic = q(
        "topic", "add", "retry-prompt", "--title", "Retry prompt",
        "--prompt", "prompt retries under a tightened policy",
    ).stdout.strip()
    open_env = policy_env(forge_store, "")
    ok(q(
        "add", topic, "bad", "--title", "bad", "--repo", str(checkout),
        "--branch", "fix/bad", "--number", "01", "--agent", "beta",
        **open_env,
    ))
    ok(q(
        "add", topic, "good", "--title", "good", "--repo", str(checkout),
        "--branch", "fix/good", "--number", "02", "--agent", "alpha",
        **open_env,
    ))
    queue_dir = Path(os.environ["FLEET_QUEUE_DIR"])
    fill_brief(queue_dir / topic / "01-bad" / "BRIEF.md")
    fill_brief(queue_dir / topic / "02-good" / "BRIEF.md")

    # Both tasks are already `dispatched, prompted: false` — as if their
    # sessions were created and are stuck behind a trust dialog, exactly what
    # `fleet queue prompt` exists to retry. The policy then tightens between
    # the dispatch and the retry, which `cmd_prompt` used to have no `try`
    # around at all: it died on the first refused task and never reached the
    # session that was still fine, leaving it stuck with no way to recover
    # except a hand-edit.
    sid = "f6666666-6666-6666-6666-666666666666"
    for slug in ("01-bad", "02-good"):
        task_file = queue_dir / topic / slug / "task.yaml"
        task_file.write_text(
            task_file.read_text(encoding="utf-8")
            .replace("state: queued", "state: dispatched")
            .replace("session: null", f"session: {sid}"),
            encoding="utf-8",
        )

    stubs.tool("thurbox-cli", ANSWERING_KEYS)
    stubs.session_is(sid, "idle")

    restricted = policy_env(forge_store, f"{QUALIFIED_REPO}=alpha")
    done = q("prompt", **restricted)
    assert "01-bad  NOT PROMPTED" in done.out, done.out
    assert "beta" in done.out, done.out
    assert "02-good  prompted" in done.out, done.out
    assert done.code != 0, done.out
    sends = "\n".join(stubs.calls("thurbox-cli", "session send"))
    assert sid in sends, sends


def test_isolation_from_checkout_agent_policy_conf(checkout, forge_store):
    # A restrictive file in the isolated settings directory is ignored when
    # FLEET_AGENT_POLICY is the empty string, proving the variable replaces
    # the file and tests do not fall back to the checkout.
    policy_root = Path(os.environ["FLEET_AGENT_POLICY_ROOT"]) / "orchestration"
    policy_root.mkdir(parents=True, exist_ok=True)
    (policy_root / "agent-policy.conf").write_text(
        f"{QUALIFIED_REPO}=restricted-agent\n", encoding="utf-8"
    )
    topic = q(
        "topic", "add", "isolated", "--title", "Isolated",
        "--prompt", "isolated from checkout",
    ).stdout.strip()
    env = policy_env(forge_store, "")
    ok(q(
        "add", topic, "free", "--title", "Free",
        "--repo", str(checkout), "--branch", "fix/free", "--number", "01",
        "--agent", "anything",
        **env,
    ))


# --- #117: the policy is one answer, and every reader takes the same one -------
#
# `spawn_commands` resolved the policy's default agent and `task_agent` did
# not, so a task with no `--agent` on a covered repository was SPAWNED as one
# agent and JUDGED as another. Everything downstream of `task_agent` — the
# quota window, the limit-banner row, the transcript directory — was then
# another account's. The fix is the maintainer's second option: dispatch
# records the agent it resolved, and `task_agent` falls back to the policy for
# a record written before it did.


def covered(store: FakeForgeStore, agents: str = "alpha") -> dict[str, str]:
    return policy_env(store, f"{QUALIFIED_REPO}={agents}")


def test_dispatch_records_the_agent_it_resolved(checkout, forge_store, stubs, queue_dir):
    """The record, and not each reader's own derivation, is where the answer lives.

    `add` wrote only an agent the operator had NAMED, so a task that named
    none left `agent: null` behind and every later pass re-derived one. The
    spawn's answer is now on the record, which is what makes it EVIDENCE about
    a session that is running rather than a guess about one.
    """
    topic = ok(q(
        "topic", "add", "record-agent", "--title", "Record the resolved agent",
        "--prompt", "dispatch writes what it spawned",
    )).stdout.strip()
    env = covered(forge_store, "alpha, beta")
    ok(q(
        "add", topic, "no-agent", "--title", "No agent named",
        "--repo", str(checkout), "--branch", "fix/no-agent", "--number", "01",
        **env,
    ))
    task_file = queue_dir / topic / "01-no-agent" / "task.yaml"
    assert yaml.safe_load(task_file.read_text(encoding="utf-8"))["agent"] is None
    fill_brief(queue_dir / topic / "01-no-agent" / "BRIEF.md")

    next_session(stubs, "aa111111-1111-1111-1111-111111111111")
    stubs.tool("thurbox-cli", ANSWERING_KEYS)
    q("dispatch", **env)

    create = [c for c in stubs.calls("thurbox-cli", "session create") if "fix/no-agent" in c][0]
    assert "--agent alpha" in create, create
    assert yaml.safe_load(task_file.read_text(encoding="utf-8"))["agent"] == "alpha", task_file


def test_refuel_reads_the_account_of_the_agent_the_policy_names(
    checkout, forge_store, stubs, isolated_env, tmp_path, queue_dir
):
    """The ordering trap: a task dispatched BEFORE the recording existed.

    Its record carries no agent at all, so the answer has to come from the
    policy — the same place the spawn took it from. The two accounts here are
    the evidence that it did: one window has fuel and the other is spent, and
    `refuel` reads whichever belongs to the agent it thinks the task runs.
    Before this change it read the checkout's `AGENT` and restarted a worker
    whose own window was empty.
    """
    topic = ok(q(
        "topic", "add", "old-record", "--title", "A record written before the fix",
        "--prompt", "judged by the agent it was spawned as",
    )).stdout.strip()
    env = covered(forge_store, "spare")
    ok(q(
        "add", topic, "in-flight", "--title", "Already in flight",
        "--repo", str(checkout), "--branch", "fix/in-flight", "--number", "01",
        **env,
    ))
    # `attach` and not `dispatch`: the session was bound to the task by the
    # code that wrote no agent, which is exactly the record this has to answer
    # for. Nothing repairs it, so the fallback is the whole of the answer.
    sid = "bb222222-2222-2222-2222-222222222222"
    ok(q("attach", f"{topic}/01-in-flight", sid))
    task_file = queue_dir / topic / "01-in-flight" / "task.yaml"
    assert yaml.safe_load(task_file.read_text(encoding="utf-8"))["agent"] is None

    stubs.session_is(sid, "working", 7200, agent="spare")
    pane(stubs, sid, f"● Now I will run the gate.\n\n{CLAUDE_BANNER}\n")
    teach(isolated_env, "spare.LIKE=claude",
          f"spare.ENV=CLAUDE_CONFIG_DIR={tmp_path / 'spare-account'}")
    stubs.tool("quota-axi", ACCOUNT_QUOTA)
    # THE TRIPWIRE: the account the LEAD is signed in to has fuel. Reading it
    # for this worker is the wrong answer arrived at from the wrong account,
    # and it is the answer the old code gave.
    lead_home = tmp_path / "lead-account"
    account_quota(stubs, "lead-account", 62, "2026-09-10T02:10:00+00:00")
    account_quota(stubs, "spare-account", 0, "2026-09-10T02:10:00+00:00")

    out = q("refuel", "--dry-run", CLAUDE_CONFIG_DIR=str(lead_home), **env).out
    expect(out, "claude (spare)", "0% remaining", "window is spent")
    refute(out, "would restart", "62% remaining")
    assert restarts(stubs) == [], stubs.calls("thurbox-cli", "session restart")


FIXER_PR = "https://github.com/Thurbeen/fleet/pull/101"


def conflicting_task(tmp_path, stubs, queue_dir, slug: str, env: dict,
                     agent: str = "", profile: str = "") -> tuple[Shep, Path, str]:
    """A shipped task on a GitHub checkout whose pull request has gone CONFLICTING.

    The state every fixer question starts from: the worker finished, the task
    closed, and the next shepherd pass is the one that decides whether an
    agent is put back on that branch. The origin is spelled `https://`, the
    form `install.sh` clones by and therefore the form the checkout a control
    plane serves actually carries — the GitHub adapter could not read it at
    all (#119), so no rule here would match a checkout without it.
    """
    shep = Shep(stubs)
    srepo = repo(tmp_path / f"{slug}-repo")
    git("remote", "add", "origin", "https://github.com/Thurbeen/fleet.git", cwd=srepo)
    topic = ok(q(
        "topic", "add", slug, "--title", "A fixer under a policy",
        "--prompt", "the fixer is a worker and the policy governs it",
    )).stdout.strip()
    named = ["--agent", agent] if agent else []
    named += ["--profile", profile] if profile else []
    ok(q(
        "add", topic, "conflicting", "--title", "A PR that conflicts",
        "--repo", str(srepo), "--branch", "fix/conflicting", "--number", "01",
        *named, **env,
    ))
    shipped(queue_dir / topic / "01-conflicting", FIXER_PR)
    git("branch", "fix/conflicting", cwd=srepo)
    ok(q("collect", **env))
    shep.perm("maintainer", "admin")
    shep.pr(101, mergeable="CONFLICTING", headRefName="fix/conflicting")
    return shep, srepo, topic


def test_the_fixer_spawns_the_agent_the_policy_names(tmp_path, stubs, queue_dir):
    """#116's half of it: `shepherd`'s fixer is a worker, so it runs the task's agent.

    It open-coded `task.doc.get("agent") or configured_agent()` and asked the
    policy nothing, so a repository whose policy `dispatch` enforces had a
    second door that `shepherd` walked through unattended. This narrows that
    to the agent it SPAWNS; the refusal is the test below.
    """
    env = {"FLEET_AGENT_POLICY": "github.com/Thurbeen/fleet=alpha"}
    shep, _, topic = conflicting_task(tmp_path, stubs, queue_dir, "fixer-policy", env)
    out = q("shepherd", "--topic", topic, **env).out

    created = "\n".join(shep.creates("01-conflicting"))
    assert created, out + shep.tbx_log()
    assert "--agent alpha" in created, created + "\n----\n" + out


def test_the_fixer_is_refused_when_the_policy_no_longer_clears_its_agent(
    tmp_path, stubs, queue_dir
):
    """#116's other half, and the one with no dispatch to fail.

    The policy is a file an operator edits, and narrowing it after a task was
    dispatched leaves a record naming an agent that may no longer serve this
    repository. `dispatch` refuses that outright; the fixer used to spawn it
    anyway, unattended, because nobody is watching a reconciler's pass. So the
    refusal has to be READABLE — a row in the report and a record on the task
    — and it has to survive a dry run, which is where an operator looks first.
    """
    dispatched = {"FLEET_AGENT_POLICY": "github.com/Thurbeen/fleet=alpha"}
    shep, _, topic = conflicting_task(
        tmp_path, stubs, queue_dir, "fixer-narrowed", dispatched, agent="alpha")
    # THE EDIT: `alpha` is no longer allowed to serve this repository.
    narrowed = {"FLEET_AGENT_POLICY": "github.com/Thurbeen/fleet=beta"}

    task_file = queue_dir / topic / "01-conflicting" / "task.yaml"
    was = yaml.safe_load(task_file.read_text(encoding="utf-8"))["state"]

    dry = q("shepherd", "--topic", topic, "--dry-run", **narrowed).out
    expect(dry, "policy-refused", "no fixer sent", "'alpha'", "beta")
    refute(dry, "would-dispatch")
    # A dry run writes nothing, including this.
    assert "shepherd" not in yaml.safe_load(task_file.read_text(encoding="utf-8"))

    out = q("shepherd", "--topic", topic, **narrowed).out
    expect(out, "policy-refused", "conflicting")
    # THE WIRING: no session was created, by either route.
    assert shep.creates("01-conflicting") == [], out + "\n----\n" + shep.tbx_log()
    refute(shep.tbx_log(), "session send")
    # And the record a person reads afterwards says which agent and which rule.
    rec = yaml.safe_load(task_file.read_text(encoding="utf-8"))["shepherd"]
    assert "alpha" in rec["refused"] and "beta" in rec["refused"], rec
    assert rec["pr"] == FIXER_PR and "session" not in rec, rec
    # The refusal is not an outcome: `collect` closes tasks and `shepherd`
    # watches, so the state this leaves behind is the one it found.
    assert yaml.safe_load(task_file.read_text(encoding="utf-8"))["state"] == was
    # And a person asking about the task is told the refusal, not told that a
    # fixer went out: `fixer None sent` was this line's old answer.
    shown = ok(q("show", f"{topic}/01-conflicting", **narrowed)).stdout
    expect(shown, "no fixer sent", "allows only beta")
    refute(shown, "fixer None")

    # TOLD ONCE. The reconciler comes round every fifteen minutes, and a
    # refusal that stands is not news on the second pass.
    q("shepherd", "--topic", topic, **narrowed)
    events = (queue_dir / topic / "01-conflicting" / "progress.jsonl").read_text(encoding="utf-8")
    assert events.count('"refused"') == 1, events

    # A pull request that drifts underneath a standing refusal is news, and
    # the record follows it rather than freezing on what the first pass saw.
    shep.update(101, mergeable="MERGEABLE", statusCheckRollup=[
        {"__typename": "CheckRun", "name": "CI", "status": "COMPLETED", "conclusion": "FAILURE"}])
    out = q("shepherd", "--topic", topic, **narrowed).out
    expect(out, "policy-refused", "checks-failed")
    rec = yaml.safe_load(task_file.read_text(encoding="utf-8"))["shepherd"]
    assert rec["condition"] == "checks-failed", rec


def test_the_fixer_fails_closed_when_the_policy_cannot_reach_the_repository(
    tmp_path, stubs, queue_dir
):
    """The second state `dispatch` already refuses on, and the fixer did not.

    A policy is in force and the repository behind the checkout cannot be
    read, so which agents may serve it is unknown — not "none of them apply".
    Falling through to the checkout's own `AGENT` there is the silent wrong
    answer `task_agent_reason` exists to stop, and spawning on it would be
    that answer acted upon.
    """
    env = {"FLEET_AGENT_POLICY": "github.com/Thurbeen/fleet=alpha"}
    shep, srepo, topic = conflicting_task(
        tmp_path, stubs, queue_dir, "fixer-unreadable", env)
    # The checkout loses the remote the rule is matched against. The forge
    # still lists the pull request — the artifact names the repository — so
    # this is a shepherd pass that sees the PR and cannot see the rule.
    git("remote", "remove", "origin", cwd=srepo)

    out = q("shepherd", "--topic", topic, **env).out
    expect(out, "policy-refused", "agent policy", "cannot be checked")
    assert shep.creates("01-conflicting") == [], out + "\n----\n" + shep.tbx_log()


def test_a_command_profile_gets_no_fixer_on_a_covered_repository(
    tmp_path, stubs, queue_dir
):
    """The case with no agent to judge, and it is refused rather than waved past.

    thurbox refuses `--agent` beside a profile's own `command`, so this spawn
    names no agent and `task_agent` says "". That is not "no agent, no rule to
    break": fleet cannot tell which agent a free command launches, so it
    cannot tell the policy is kept. `dispatch` has always said so, and the
    fixer now says the same thing rather than reading the empty answer as
    permission.
    """
    env = {"FLEET_AGENT_POLICY": "github.com/Thurbeen/fleet=alpha"}
    shep, _, topic = conflicting_task(
        tmp_path, stubs, queue_dir, "fixer-commanded", env, profile="cursor-trusted")

    out = q("shepherd", "--topic", topic, **env).out
    expect(out, "policy-refused", "custom `command`", "alpha")
    assert shep.creates("01-conflicting") == [], out + "\n----\n" + shep.tbx_log()


def test_a_refusal_never_overwrites_the_record_of_a_fixer_in_flight(
    tmp_path, stubs, queue_dir
):
    """`--force` is the door, and the record is the only handle on a live fixer.

    `--force` skips the in-flight check on purpose, so a refusal reached under
    it would be the one thing allowed to write over a record naming a session
    that is out there working. That id is how every later pass finds it; drop
    it and the next unforced pass reads "nothing is outstanding" and sends a
    second fixer at the same pull request.
    """
    wide = {"FLEET_AGENT_POLICY": "github.com/Thurbeen/fleet=alpha"}
    shep, _, topic = conflicting_task(
        tmp_path, stubs, queue_dir, "fixer-forced", wide, agent="alpha")
    ok(q("shepherd", "--topic", topic, **wide))
    task_file = queue_dir / topic / "01-conflicting" / "task.yaml"
    sent = yaml.safe_load(task_file.read_text(encoding="utf-8"))["shepherd"]["session"]
    assert sent, task_file.read_text(encoding="utf-8")

    narrowed = {"FLEET_AGENT_POLICY": "github.com/Thurbeen/fleet=beta"}
    out = q("shepherd", "--topic", topic, "--force", **narrowed).out
    expect(out, "policy-refused")
    rec = yaml.safe_load(task_file.read_text(encoding="utf-8"))["shepherd"]
    assert rec["session"] == sent, rec

    # And the proof of what keeping it is for: with the policy widened again,
    # the pass that follows sees the fixer it already has, not a vacancy.
    again = q("shepherd", "--topic", topic, **wide).out
    expect(again, "in-flight")
    assert len(shep.creates("01-conflicting")) == 1, shep.tbx_log()


def test_a_host_task_keeps_the_refusal_that_says_where_its_checkout_is(
    tmp_path, stubs, queue_dir
):
    """A policy must not blunt a refusal that was already more precise.

    A fixer never goes out for a `--host` task — its checkout is on the other
    machine and spawning there is not implemented — and that sentence is the
    one an operator can act on. The policy's own answer for a host is true and
    vaguer, and it would have replaced the better one the moment anybody wrote
    a first rule, for tasks no rule covers.
    """
    topic = ok(q(
        "topic", "add", "host-fixer", "--title", "A fixer for a remote task",
        "--prompt", "the checkout is on another machine",
    )).stdout.strip()
    # Before the shepherd's own thurbox stub takes over: `add --host` asks
    # thurbox where its hosts.toml is, and that stub answers only the calls a
    # shepherd pass makes.
    ok(q(
        "add", topic, "remote", "--title", "A PR from a remote worker",
        "--repo", "/remote/repo", "--branch", "fix/remote", "--number", "01",
        "--host", "devbox",
    ))
    shep = Shep(stubs)
    shipped(queue_dir / topic / "01-remote", FIXER_PR)
    ok(q("collect"))
    shep.perm("maintainer", "admin")
    shep.pr(101, mergeable="CONFLICTING", headRefName="fix/remote")

    out = q("shepherd", "--topic", topic,
            FLEET_AGENT_POLICY="github.com/Thurbeen/fleet=alpha").out
    expect(out, "not-dispatched", "host devbox", "spawned there too")
    refute(out, "policy-refused")
    assert shep.creates("01-remote") == [], out + "\n----\n" + shep.tbx_log()


def github_checkout(path: Path) -> Path:
    """A checkout of a GitHub repository, cloned the way `install.sh` clones."""
    repo = path / "gh-repo"
    git("init", "-q", "-b", "main", str(repo))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=repo)
    git("remote", "add", "origin", "https://github.com/Thurbeen/fleet.git", cwd=repo)
    return repo


def test_policy_covers_a_github_checkout_cloned_over_https(tmp_path, stubs, queue_dir):
    """The one remote form this control plane's own checkout carries (#119).

    Every rule here is matched against a repository read out of a CHECKOUT,
    and the GitHub adapter parsed only the scp spelling — so a policy covering
    a repository cloned by `install.sh` covered nothing, silently on the read
    path and loudly at dispatch. Driven through `add` and `dispatch` rather
    than through the parser, because the parser was never the thing in doubt:
    what was in doubt is whether the policy reaches a real checkout.
    """
    srepo = github_checkout(tmp_path)
    env = {"FLEET_AGENT_POLICY": "github.com/Thurbeen/fleet=alpha"}
    topic = ok(q(
        "topic", "add", "https-origin", "--title", "An https origin",
        "--prompt", "the policy covers an https checkout",
    )).stdout.strip()

    # The louder half is `add`, which took an agent the policy forbids and
    # said nothing: the rule was never found, so there was nothing to break.
    done = q(
        "add", topic, "forbidden", "--title", "Forbidden agent",
        "--repo", str(srepo), "--branch", "fix/forbidden", "--number", "01",
        "--agent", "forbidden-agent", **env,
    )
    assert done.code != 0, done.out
    expect(done.out, "forbidden-agent", "alpha")

    ok(q(
        "add", topic, "covered", "--title", "Covered by the policy",
        "--repo", str(srepo), "--branch", "fix/covered", "--number", "02",
        **env,
    ))
    fill_brief(queue_dir / topic / "02-covered" / "BRIEF.md")
    sid = "c1111111-cccc-cccc-cccc-cccccccccccc"
    next_session(stubs, sid)
    stubs.session_is(sid, "idle")
    stubs.tool("thurbox-cli", ANSWERING_KEYS)
    # And the quieter half, which used to refuse outright: `NOT SPAWNED —
    # cannot read origin for '…', so agent policy cannot be checked.`
    ok(q("dispatch", **env))
    create = [c for c in stubs.calls("thurbox-cli", "session create") if "fix/covered" in c][0]
    assert "--agent alpha" in create, create


def test_refuel_says_undetermined_when_the_policy_cannot_name_an_agent(
    checkout, forge_store, stubs, isolated_env, tmp_path, queue_dir
):
    """Failing to READ the policy is not the same as no policy covering the repo.

    `dispatch` has always failed closed here. The read path fell through to
    the checkout's own `AGENT` instead and said nothing about it, so `refuel`
    measured a window belonging to an account this worker never drew on and
    would have restarted it against that reading.
    """
    topic = ok(q(
        "topic", "add", "unreadable", "--title", "An unreadable origin",
        "--prompt", "the policy cannot be resolved",
    )).stdout.strip()
    env = covered(forge_store, "spare")
    ok(q(
        "add", topic, "orphan", "--title", "Orphaned checkout",
        "--repo", str(checkout), "--branch", "fix/orphan", "--number", "01",
        **env,
    ))
    sid = "0a111111-0000-0000-0000-000000000001"
    ok(q("attach", f"{topic}/01-orphan", sid))
    # The checkout moves out from under the task — or its `origin` is a URL no
    # adapter claims, which is the same state and the commoner one.
    git("remote", "remove", "origin", cwd=checkout)

    stubs.session_is(sid, "working", 7200, agent="spare")
    pane(stubs, sid, f"● Working.\n\n{CLAUDE_BANNER}\n")
    teach(isolated_env, "spare.LIKE=claude",
          f"spare.ENV=CLAUDE_CONFIG_DIR={tmp_path / 'spare-account'}")
    stubs.tool("quota-axi", ACCOUNT_QUOTA)
    # The lead's own account has fuel. Reading it here is the old answer.
    account_quota(stubs, "lead-account", 75, "2026-09-10T02:10:00+00:00")
    out = q("refuel", "--dry-run",
            CLAUDE_CONFIG_DIR=str(tmp_path / "lead-account"), **env).out

    expect(out, "undetermined", "no repository can be read from `origin`")
    refute(out, "75% remaining", "would restart", "(claude)")
    assert restarts(stubs) == [], stubs.calls("thurbox-cli", "session restart")


def test_a_command_profile_records_no_agent(checkout, forge_store, stubs, queue_dir):
    """"" is a record and not an omission, so nothing writes a guess over it.

    A profile carrying `command` replaces `--agent` — thurbox refuses both —
    so the spawn names no agent at all. `dispatch` recorded one anyway, taken
    from a resolution the command line never saw, and `show` then stated it,
    `refuel` took that agent's account for a session that is something else,
    and a later edit to `AGENT=` could no longer reach the task.
    """
    topic = ok(q(
        "topic", "add", "command-record", "--title", "A command profile",
        "--prompt", "a free command names no agent",
    )).stdout.strip()
    # No policy: one covering the repository refuses a `--command` profile
    # outright, which is a different (and already tested) answer.
    env = policy_env(forge_store, "")
    ok(q(
        "add", topic, "commanded", "--title", "Commanded",
        "--repo", str(checkout), "--branch", "fix/commanded", "--number", "01",
        "--profile", "cursor-trusted", **env,
    ))
    fill_brief(queue_dir / topic / "01-commanded" / "BRIEF.md")
    sid = "0b222222-0000-0000-0000-000000000002"
    next_session(stubs, sid)
    stubs.session_is(sid, "idle")
    stubs.tool("thurbox-cli", ANSWERING_KEYS)
    ok(q("dispatch", **env))

    create = [c for c in stubs.calls("thurbox-cli", "session create") if "fix/commanded" in c][0]
    assert "--command cursor-agent" in create, create
    assert "--agent" not in create, create
    task_file = queue_dir / topic / "01-commanded" / "task.yaml"
    assert yaml.safe_load(task_file.read_text(encoding="utf-8"))["agent"] is None, task_file


def test_a_refusal_names_dispatch_rather_than_the_operator(
    checkout, forge_store, stubs, queue_dir
):
    """The same field, two writers, and only one of them the operator.

    `agent: alpha` used to be sayable only by `add --agent`, so the refusal
    told the operator what they had asked for and left them to change it. A
    dispatch's own answer now lands in the same field, and the old sentence
    then blamed them for a word they never typed and pointed at a record that
    is no longer the live fact: the session is already running as that agent.
    """
    topic = ok(q(
        "topic", "add", "narrowed", "--title", "A narrowed policy",
        "--prompt", "the refusal says who chose the agent",
    )).stdout.strip()
    at_dispatch = covered(forge_store, "alpha, beta")
    ok(q(
        "add", topic, "running", "--title", "Already running",
        "--repo", str(checkout), "--branch", "fix/running", "--number", "01",
        **at_dispatch,
    ))
    fill_brief(queue_dir / topic / "01-running" / "BRIEF.md")
    sid = "0c333333-0000-0000-0000-000000000003"
    next_session(stubs, sid)
    stubs.session_is(sid, "idle")
    stubs.tool("thurbox-cli", ANSWERING_KEYS)
    ok(q("dispatch", **at_dispatch))
    task_file = queue_dir / topic / "01-running" / "task.yaml"
    assert yaml.safe_load(task_file.read_text(encoding="utf-8"))["agent"] == "alpha"

    # The session is up and stuck behind its trust dialog, which is the state
    # `fleet queue prompt` exists to retry — and the state in which a refusal
    # is read by a person deciding what to do next.
    task_file.write_text(
        task_file.read_text(encoding="utf-8").replace("prompted: true", "prompted: false"),
        encoding="utf-8",
    )
    stubs.session_is(sid, "idle")

    done = q("prompt", **covered(forge_store, "beta"))
    assert done.code != 0, done.out
    expect(done.out, "NOT PROMPTED", "dispatch resolved agent 'alpha'",
           "already running as that agent", "cancel the session")
    refute(done.out, "task records agent")


def test_refuel_judges_by_the_policy_the_spawn_ran_under(
    checkout, forge_store, stubs, isolated_env, tmp_path, queue_dir
):
    """The whole reason the record exists, and the one thing re-derivation cannot do.

    A policy is a file an operator edits, and editing it does not reach back
    into a session that is already running. Re-resolving the policy on every
    read answers with TODAY's rule about a worker started under yesterday's,
    so `refuel` reads the wrong account's window and restarts — or declines to
    restart — on a measurement of somebody else's quota.

    Both agents resolve to the same provider and differ only in the account
    they draw on, so the single line this asserts on is decided by nothing
    except which of the two `refuel` believes the task runs.
    """
    topic = ok(q(
        "topic", "add", "policy-edited", "--title", "A policy edited mid-flight",
        "--prompt", "the record outlives the rule that made it",
    )).stdout.strip()
    at_dispatch = covered(forge_store, "alpha")
    ok(q(
        "add", topic, "in-flight", "--title", "Dispatched under alpha",
        "--repo", str(checkout), "--branch", "fix/in-flight", "--number", "01",
        **at_dispatch,
    ))
    fill_brief(queue_dir / topic / "01-in-flight" / "BRIEF.md")
    sid = "0d444444-0000-0000-0000-000000000004"
    next_session(stubs, sid)
    stubs.session_is(sid, "idle")
    stubs.tool("thurbox-cli", ANSWERING_KEYS)
    ok(q("dispatch", **at_dispatch))
    create = [c for c in stubs.calls("thurbox-cli", "session create") if "fix/in-flight" in c][0]
    assert "--agent alpha" in create, create

    stubs.session_is(sid, "working", 7200, agent="alpha")
    pane(stubs, sid, f"● Working.\n\n{CLAUDE_BANNER}\n")
    teach(isolated_env,
          "alpha.LIKE=claude", f"alpha.ENV=CLAUDE_CONFIG_DIR={tmp_path / 'alpha-account'}",
          "beta.LIKE=claude", f"beta.ENV=CLAUDE_CONFIG_DIR={tmp_path / 'beta-account'}")
    stubs.tool("quota-axi", ACCOUNT_QUOTA)
    # `alpha`, which this worker is running as, is out of fuel; `beta`, which
    # the policy would name if it were asked again, has plenty.
    account_quota(stubs, "alpha-account", 0, "2026-09-10T02:10:00+00:00")
    account_quota(stubs, "beta-account", 62, "2026-09-10T02:10:00+00:00")

    # THE EDIT: the operator narrows the policy while the worker is at work.
    out = q("refuel", "--dry-run",
            CLAUDE_CONFIG_DIR=str(tmp_path / "lead-account"),
            **covered(forge_store, "beta")).out

    expect(out, "claude (alpha)", "0% remaining", "window is spent")
    refute(out, "(beta)", "62% remaining", "would restart")
    assert restarts(stubs) == [], stubs.calls("thurbox-cli", "session restart")
