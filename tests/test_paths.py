"""`fleet paths`: where the files fleet names live, on THIS machine.

A skill, a hook or a message that tells an operator to open thurbox's hooks
file cannot spell `~/.config/thurbox/hooks/claude.json`: on native Windows
thurbox keeps it under `%APPDATA%`. So they name the path through this
command, which asks `fleet_platform` exactly as fleet's own code does.
"""

from pathlib import Path

from harness import REPO, run_fleet

NAMES = ("checkout", "thurbox-config", "thurbox-hooks", "fleet-data", "claude-settings")


def listing(out: str) -> dict[str, str]:
    return dict(line.split("\t", 1) for line in out.splitlines())


def test_every_path_is_listed_by_name(tmp_path):
    done = run_fleet("paths", THURBOX_CONFIG_DIR=str(tmp_path / "thurbox"))
    assert done.code == 0, done.out
    paths = listing(done.stdout)
    assert tuple(paths) == NAMES
    assert Path(paths["checkout"]) == REPO
    assert Path(paths["thurbox-config"]) == tmp_path / "thurbox"
    assert Path(paths["thurbox-hooks"]) == tmp_path / "thurbox" / "hooks" / "claude.json"


def test_one_name_prints_only_its_path(tmp_path):
    done = run_fleet("paths", "thurbox-hooks", cwd=tmp_path, THURBOX_CONFIG_DIR=str(tmp_path / "t"))
    assert done.code == 0, done.out
    assert done.stdout == str(tmp_path / "t" / "hooks" / "claude.json") + "\n"


def test_the_paths_are_the_ones_fleet_platform_answers(isolated_env):
    paths = listing(run_fleet("paths", THURBOX_CONFIG_DIR=None).stdout)
    # isolated_env pins XDG_CONFIG_HOME and XDG_DATA_HOME on every OS.
    assert Path(paths["thurbox-config"]) == isolated_env / "home" / ".config" / "thurbox"
    assert Path(paths["fleet-data"]) == isolated_env / "home" / ".local" / "share" / "fleet"


def test_claude_codes_user_settings_follow_its_own_variable(tmp_path, isolated_env):
    """Where `fleet install` merges the reconciler's Stop nudge: Claude Code's
    user settings, which thurbox leaves alone, unlike its own hooks file."""
    pinned = run_fleet("paths", "claude-settings", CLAUDE_CONFIG_DIR=str(tmp_path / "claude"))
    assert Path(pinned.stdout.strip()) == tmp_path / "claude" / "settings.json", pinned.out
    default = run_fleet("paths", "claude-settings", CLAUDE_CONFIG_DIR=None)
    assert Path(default.stdout.strip()) == isolated_env / "home" / ".claude" / "settings.json", default.out


def test_an_unknown_name_is_a_usage_error():
    done = run_fleet("paths", "no-such-path")
    assert done.code == 2, done.out
    assert "no-such-path" in done.stderr
    for name in NAMES:
        assert name in done.stderr
