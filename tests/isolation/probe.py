"""Focused child contract; run explicitly from the poisoned-checkout test.

This file is not named test_*.py: its host and checkout poison come from the
parent, so running it alone in the ordinary suite would prove nothing extra.
"""

import os
from pathlib import Path

from harness import REPO, queue_module, run_fleet, run_queue


def test_the_actual_fixture_replaces_every_operator_root(isolated_env):
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
                "FLEET_QUEUE_DIR", "FLEET_REGISTRY_FILE", "FLEET_RUNS_DIR",
                "FLEET_RECONCILE_DIR", "FLEET_SHEPHERD_DIR", "FLEET_PEERS_DIR", "FLEET_VOICE_CONF",
                "FLEET_AUTO_MERGE_ROOT", "FLEET_PUBLISH_ROOT", "FLEET_AGENT_ROOT", "FLEET_AGENT_POLICY_ROOT",
                "FLEET_GLYPH_ROOT", "FLEET_NAME_ROOT", "FLEET_PROFILES_ROOT", "FLEET_PEERS_ROOT", "FLEET_OWNERS_ROOT",
                "FLEET_RECONCILE_CONF_ROOT"):
        path = Path(os.environ[key])
        assert path.is_relative_to(isolated_env), f"{key} escaped the fixture: {path}"
        assert not path.is_relative_to(REPO), f"{key} reads the poisoned checkout"
    assert list(Path(os.environ["FLEET_QUEUE_DIR"]).iterdir()) == []
    assert not Path(os.environ["FLEET_REGISTRY_FILE"]).exists()
    for key in ("THURBOX_SESSION", "THURBOX_CONFIG_DIR", "GH_TOKEN", "GITHUB_TOKEN", "GITLAB_TOKEN",
                "GH_HOST", "GITLAB_HOST", "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL", "GIT_DIR", "TMUX", "TMUX_PANE"):
        assert key not in os.environ, f"{key} reached the child"


def test_default_queue_readers_use_no_checkout_setting():
    # These readers must resolve the actual fixture, not an explicit root or
    # per-test override which would hide a fall back to the poisoned checkout.
    out = queue_module('''
assert q.auto_merge_repos() == set()
assert q.agent_policy() == {}
assert q.integration_branches() == {}
assert q.publish_conf()["METHOD"] == "pr"
assert q.publish_conf()["HOW"] == ""
assert q.agent_conf()["AGENT"] == ""
assert q.reconcile_conf() == {}
assert q.session_ending(False) == "delete"
print("isolated defaults")
''')
    assert out.strip() == "isolated defaults", out
    done = run_queue("check")
    assert done.code == 0, done.out
    assert "operator-private" not in done.out, done.out
    profile = run_fleet("session-flags")
    assert (profile.code, profile.stdout.strip()) == (0, ""), profile.out
