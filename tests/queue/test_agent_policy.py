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
from kit_dispatch import ANSWERING_KEYS, next_session
from kit_forges import FakeForgeStore
from queuekit import ok

from harness import git, run_queue as q

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
    out = q("dispatch", **policy_env(forge_store, f"{QUALIFIED_REPO}=alpha")).out
    assert "--host task cannot be checked against agent policy" in out, out


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
    out = q("dispatch", **env).out
    assert "NOT SPAWNED" in out, out
    assert "beta" in out
    creates = stubs.calls("thurbox-cli", "session create")
    assert len(creates) == 2, creates
    assert all("fix/ok" in c or "fix/also-ok" in c for c in creates), creates


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
