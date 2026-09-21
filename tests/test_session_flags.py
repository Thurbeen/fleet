"""`fleet session-flags`: one session profile, rendered into `session create` flags.

The CLI the retired shell script had: the `default` profile
when none is named, a named one, `--check` to validate every profile, and flags
NUL-separated, because a `--arg` value is often a whole command line. It reads
`orchestration/session-profiles.yaml` from the checkout whatever the caller's
directory is. The two-argument form the gate calls,
`session_profiles.py <path> --check|<profile>`, still works.
"""

import yaml

from harness import PYTHON, REPO, expect, run, run_fleet, write


def flags(out: str) -> list[str]:
    return [f for f in out.split("\0") if f]


def test_a_named_profile_renders_nul_separated_flags_from_any_directory(tmp_path):
    done = run_fleet("session-flags", "sweep", cwd=tmp_path)
    assert done.code == 0, done.out
    rendered = flags(done.stdout)
    assert "--env" in rendered, rendered
    assert "MAX_THINKING_TOKENS=8000" in rendered, rendered


def test_no_profile_named_is_the_default_one(tmp_path):
    implicit = run_fleet("session-flags", cwd=tmp_path)
    explicit = run_fleet("session-flags", "default", cwd=tmp_path)
    assert implicit.code == 0, implicit.out
    assert (implicit.code, implicit.stdout) == (explicit.code, explicit.stdout)


def test_check_validates_every_profile(tmp_path):
    done = run_fleet("session-flags", "--check", cwd=tmp_path)
    assert done.code == 0, done.out
    assert done.stdout.startswith("profiles ok: "), done.out


def test_a_profile_that_does_not_exist_is_an_error_naming_the_ones_that_do(tmp_path):
    done = run_fleet("session-flags", "no-such-profile", cwd=tmp_path)
    assert done.code == 1, done.out
    assert "no profile 'no-such-profile'" in done.stderr
    assert "sweep" in done.stderr


def test_an_unknown_option_is_a_usage_error(tmp_path):
    done = run_fleet("session-flags", "--no-such-option", cwd=tmp_path)
    assert done.code == 2, done.out
    assert "unknown option" in done.stderr


def test_help_prints_the_usage(tmp_path):
    done = run_fleet("session-flags", "--help", cwd=tmp_path)
    assert done.code == 0, done.out
    assert "fleet session-flags" in done.stdout
    assert "--check" in done.stdout


def test_the_two_argument_form_the_gate_calls_still_works():
    done = run([*PYTHON, "scripts/lib/session_profiles.py", "orchestration/session-profiles.yaml", "--check"], cwd=REPO)
    assert done.code == 0, done.out
    assert done.stdout.startswith("profiles ok: "), done.out


def _profiles(tmp_path, body: str):
    path = tmp_path / "profiles.yaml"
    path.write_text("profiles:\n" + body, encoding="utf-8")
    return path


def _flags_of(path, name: str):
    return run([*PYTHON, "scripts/lib/session_profiles.py", str(path), name], cwd=REPO)


def _check_of(path):
    return run([*PYTHON, "scripts/lib/session_profiles.py", str(path), "--check"], cwd=REPO)


def test_command_with_uncovered_renders_without_reports_as(tmp_path):
    """The declaration that the session will be uncovered, instead of a family
    thurbox does not ship hooks for. Silence is still refused; this is not it."""
    path = _profiles(tmp_path, """
  raw:
    command: cursor-agent
    args: ["--trust"]
    uncovered: true
""")
    checked = _check_of(path)
    assert checked.code == 0, checked.out
    done = _flags_of(path, "raw")
    assert done.code == 0, done.out
    rendered = flags(done.stdout)
    assert rendered == ["--command", "cursor-agent", "--arg", "--trust"], rendered
    assert "--reports-as" not in rendered
    assert "uncovered" in done.stderr
    assert "watch" in done.stderr and "refuel" in done.stderr and "reap" in done.stderr


def test_command_without_reports_as_or_uncovered_is_still_refused(tmp_path):
    """Rule 2 still catches silence. Dropping reports_as is not how a command
    profile becomes valid."""
    path = _profiles(tmp_path, """
  silent:
    command: cursor-agent
""")
    done = _check_of(path)
    assert done.code == 1, done.out
    assert "command needs reports_as or uncovered" in done.stderr, done.out


def test_command_cannot_carry_both_reports_as_and_uncovered(tmp_path):
    path = _profiles(tmp_path, """
  both:
    command: cursor-agent
    reports_as: cursor
    uncovered: true
""")
    done = _check_of(path)
    assert done.code == 1, done.out
    assert "both reports_as and uncovered" in done.stderr, done.out


def test_uncovered_without_command_is_refused(tmp_path):
    path = _profiles(tmp_path, """
  empty:
    uncovered: true
""")
    done = _check_of(path)
    assert done.code == 1, done.out
    assert "uncovered without command" in done.stderr, done.out


def test_uncovered_must_be_the_boolean_true(tmp_path):
    path = _profiles(tmp_path, """
  denied:
    command: cursor-agent
    uncovered: false
""")
    done = _check_of(path)
    assert done.code == 1, done.out
    assert "uncovered: expected true" in done.stderr, done.out


def test_cursor_trusted_is_an_uncovered_command_profile(tmp_path):
    """The live profile that thurbox refuses --reports-as for: no family, so
    no flag, and the renderer says so on stderr."""
    done = run_fleet("session-flags", "cursor-trusted", cwd=tmp_path)
    assert done.code == 0, done.out
    rendered = flags(done.stdout)
    assert rendered == ["--command", "cursor-agent", "--arg", "--trust"], rendered
    assert "--reports-as" not in rendered
    assert "uncovered" in done.stderr


# --- the operator's overlay: a profile of their own, in no tracked file --------
#
# `session-profiles.yaml` is tracked, and `fleet sync-checkout` refuses to
# fast-forward a dirty tree, so a profile written THERE stops the checkout
# updating itself. `session-profiles.local.yaml` beside it is gitignored and
# adds to it by profile name. `FLEET_PROFILES_ROOT` relocates it, which is how
# the harness keeps the operator's own copy out of every test.


def _overlay(root, body: str):
    write(root / "orchestration" / "session-profiles.local.yaml", "profiles:\n" + body)
    return str(root)


def test_the_overlay_adds_a_profile_of_the_operators_own(tmp_path):
    root = _overlay(tmp_path, "  deep:\n    env:\n      ANTHROPIC_MODEL: some-model\n")
    done = run_fleet("session-flags", "deep", cwd=tmp_path, FLEET_PROFILES_ROOT=root)
    assert done.code == 0, done.out
    assert flags(done.stdout) == ["--env", "ANTHROPIC_MODEL=some-model"]
    # The tracked profiles are all still there beside it.
    sweep = run_fleet("session-flags", "sweep", cwd=tmp_path, FLEET_PROFILES_ROOT=root)
    assert "MAX_THINKING_TOKENS=8000" in flags(sweep.stdout), sweep.out


def test_an_overlay_profile_replaces_the_tracked_one_of_the_same_name_whole(tmp_path):
    root = _overlay(tmp_path, "  sweep:\n    env:\n      MAX_THINKING_TOKENS: \"2000\"\n")
    done = run_fleet("session-flags", "sweep", cwd=tmp_path, FLEET_PROFILES_ROOT=root)
    assert done.code == 0, done.out
    # Whole, not merged key by key: the tracked BASH_DEFAULT_TIMEOUT_MS is gone.
    assert flags(done.stdout) == ["--env", "MAX_THINKING_TOKENS=2000"]


def test_check_validates_the_overlay_and_names_it(tmp_path):
    root = _overlay(tmp_path, "  bad:\n    env:\n      THURBOX_SESSION: x\n")
    done = run_fleet("session-flags", "--check", cwd=tmp_path, FLEET_PROFILES_ROOT=root)
    assert done.code == 1, done.out
    expect(done.stderr, "session-profiles.local.yaml", "THURBOX_SESSION")


def test_no_overlay_renders_exactly_what_the_tracked_file_alone_does(tmp_path):
    """The compatibility promise: a checkout without the new file is unchanged."""
    tracked = REPO / "orchestration" / "session-profiles.yaml"
    names = list(yaml.safe_load(tracked.read_text(encoding="utf-8"))["profiles"])
    for name in names:
        alone = _flags_of(tracked, name)
        default = run_fleet("session-flags", name, cwd=tmp_path, FLEET_PROFILES_ROOT=str(tmp_path))
        assert (default.code, default.stdout) == (alone.code, alone.stdout), name
    check = run_fleet("session-flags", "--check", cwd=tmp_path, FLEET_PROFILES_ROOT=str(tmp_path))
    assert check.stdout == f"profiles ok: {len(names)} in orchestration/session-profiles.yaml\n", check.out


def test_the_gate_form_reads_the_named_file_and_never_the_overlay(tmp_path):
    root = _overlay(tmp_path, "  bad:\n    env:\n      THURBOX_SESSION: x\n")
    done = run([*PYTHON, "scripts/lib/session_profiles.py", "orchestration/session-profiles.yaml", "--check"],
               cwd=REPO, FLEET_PROFILES_ROOT=root)
    assert done.code == 0, done.out
