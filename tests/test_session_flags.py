"""`fleet session-flags`: one session profile, rendered into `session create` flags.

The CLI the retired shell script had: the `default` profile
when none is named, a named one, `--check` to validate every profile, and flags
NUL-separated, because a `--arg` value is often a whole command line. It reads
`orchestration/session-profiles.yaml` from the checkout whatever the caller's
directory is. The two-argument form the gate calls,
`session_profiles.py <path> --check|<profile>`, still works.
"""

from harness import PYTHON, REPO, run, run_fleet


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
