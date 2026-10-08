"""preflight: the table is honest, every gap carries a remedy, and the table is data.

Onboarding is the one part of fleet whose failures are invisible to everyone
who already ran it: a table that forgets a tool is fine on the machine it was
written on and costs a new operator the whole setup. So it is driven here
against machines built from stand-ins, where "missing" means missing:

  - every missing REQUIRED dependency is named in one pass and exits 1; a
    recommended, forge or gate gap is reported and never fatal, because a
    local-only fleet runs with no forge CLI and no login at all;
  - a thurbox below the manifest's floor is `stale`, and the floor is read
    from extension.toml.in, which owns it;
  - `--commands` hands over exactly the runnable install lines, for the package
    manager this machine has, and the OS family decides which managers count;
  - neither authentication row is an exit code: `gh auth` is decided per
    ACCOUNT and `glab auth` per HOST;
  - the dependencies are ONE table of records a second module can act on.
"""

from __future__ import annotations

import json
import os

import pytest
from harness import expect, lib, refute, run_fleet, write
from kit import FLOOR, full_machine, git_config, machine, plain, says

WINDOWS = os.name == "nt"


def preflight(path: str, *args: str, **env: str | None):
    return run_fleet("preflight", *args, PATH=path, **env)


# --- 1. the table is honest, and every gap carries a remedy -------------------


def test_a_fully_equipped_machine_exits_0_and_has_nothing_to_install(stubs):
    path = machine(stubs, full_machine())
    done = preflight(path)
    assert done.code == 0, done.out
    expect(done.out, "Every required dependency is present")

    done = preflight(path, "--commands")
    assert (done.code, done.stdout) == (0, ""), done.out


def test_every_missing_required_tool_is_named_in_the_same_pass(stubs):
    """Not the first one it tripped over: an operator should not learn about a
    missing tool one restart at a time."""
    path = machine(stubs, full_machine(), without=("thurbox-cli", "uv"))
    done = preflight(path)
    out = plain(done.out)
    assert done.code == 1, out
    expect(out, "missing  thurbox-cli ", "missing  uv ", "2 required dependencies missing", "install:")

    cmds = preflight(path, "--commands")
    assert cmds.stdout.strip(), "--commands hands over the lines to run"
    refute(cmds.out, "see http")


def test_tier_makes_install_the_required_ones_a_command_and_never_hides_a_required_gap(stubs):
    """A required gap is one whatever the caller asked to see."""
    path = machine(stubs, full_machine(), without=("uv", "quota-axi", "gh"))
    required = preflight(path, "--commands", "--tier", "required").out
    refute(required, "quota-axi", "GitHub.cli", "apt-get install -y gh")

    done = preflight(path, "--tier", "gate")
    assert done.code == 1, done.out
    refute(done.out, "RECOMMENDED")


def test_a_thurbox_below_the_manifests_floor_is_stale_and_fails(stubs):
    tools = full_machine() | {"thurbox-cli": says("thurbox-cli 0.0.1")}
    done = preflight(machine(stubs, tools))
    assert done.code == 1, done.out
    expect(done.out, "stale", FLOOR)


def test_recommended_and_gate_gaps_are_reported_and_never_fatal(stubs):
    path = machine(stubs, full_machine(), without=("quota-axi", "glab", "prek", "lua"))
    done = preflight(path)
    assert done.code == 0, done.out
    expect(done.out, "quota-axi", "refuel", "GATE", "LEAD")


def test_signing_on_with_no_key_is_reported_from_outside_the_checkout(stubs, tmp_path):
    """Signing can be configured for the operator's code tree and nowhere else,
    so committing here works and committing in a sandbox does not. The probe
    asks git from OUTSIDE the checkout, proved by answering only through
    GIT_CONFIG_GLOBAL."""
    path = machine(stubs, full_machine())
    nokey = git_config(tmp_path / "gitconfig-nokey", "[commit]\n\tgpgsign = true\n")
    expect(plain(preflight(path, GIT_CONFIG_GLOBAL=nokey).out),
           "missing  commit signing", "sandbox", "commit.gpgsign false")


def test_a_gap_is_the_install_line_for_this_machines_os_family(stubs):
    done = preflight(machine(stubs, full_machine(), without=("gh",)), "--commands")
    want = "winget install --id GitHub.cli -e" if WINDOWS else "sudo apt-get install -y gh"
    assert want in done.stdout.splitlines(), done.out


def test_a_usage_error_exits_2(stubs):
    path = machine(stubs, full_machine())
    assert preflight(path, "--tier", "everything").code == 2
    assert preflight(path, "--bogus").code == 2
    done = preflight(path, "--help")
    assert done.code == 0
    expect(done.stdout, "--commands", "--tier")


# --- 6. the glab row is per HOST, and a self-hosted instance is ordinary ------

# glab 1.117.0's own shape: `--hostname` answers for one instance, `--all` lists
# every configured one at column 0 and exits non-zero when ANY of them fails,
# and a bare `auth status` answers for GITLAB_HOST when that is set.
GLAB_PER_HOST = """
import os, sys
a = sys.argv[1:]
if a[:3] == ["config", "get", "host"]:
    # glab's own lookup: the environment, then the config, then gitlab.com.
    print(os.environ.get("GITLAB_HOST") or os.environ.get("GLAB_DEFAULT") or "gitlab.com")
    raise SystemExit(0)
if a[:2] != ["auth", "status"]:
    print("glab 1.117.0")
    raise SystemExit(0)
hosts = os.environ.get("GLAB_HOSTS", "").split()
ok = os.environ.get("GLAB_OK", "").split()
if "--all" in a or "-a" in a:
    bad = 0
    for h in hosts:
        print(h)
        if h in ok:
            print(f"  \\u2713 Logged in to {h} as someone")
        else:
            print(f"  x {h}: API call failed: 401")
            bad = 1
    raise SystemExit(bad)
host = a[a.index("--hostname") + 1] if "--hostname" in a else os.environ.get("GITLAB_HOST", "gitlab.com")
raise SystemExit(0 if host in ok else 1)
"""

SELF_HOSTED = "gitlab.example.com"


@pytest.fixture
def glab_machine(stubs) -> str:
    return machine(stubs, full_machine() | {"glab": GLAB_PER_HOST})


def forge_tier(path: str, **env: str | None) -> str:
    return plain(preflight(path, "--tier", "forge", **env).out)


def test_a_credential_on_one_configured_host_is_not_reported_missing(glab_machine):
    """The case from the field: a credential for the self-hosted instance, none
    for gitlab.com, and no GITLAB_HOST naming either."""
    out = forge_tier(glab_machine, GLAB_HOSTS=f"gitlab.com {SELF_HOSTED}", GLAB_OK=SELF_HOSTED)
    refute(out, "missing  glab auth")
    expect(out, SELF_HOSTED)


def test_gitlab_host_decides_which_instance_has_to_work_in_both_directions(glab_machine):
    """GITLAB_HOST is glab's own variable for which instance to talk to, so a
    credential for some OTHER instance is not the one the forge seam uses."""
    hosts = f"gitlab.com {SELF_HOSTED}"
    out = forge_tier(glab_machine, GITLAB_HOST="gitlab.com", GLAB_HOSTS=hosts, GLAB_OK=SELF_HOSTED)
    expect(out, "missing  glab auth", "glab auth login --hostname gitlab.com")

    out = forge_tier(glab_machine, GITLAB_HOST=SELF_HOSTED, GLAB_HOSTS=hosts, GLAB_OK=SELF_HOSTED)
    refute(out, "missing  glab auth")


def test_no_gitlab_credential_anywhere_is_missing_and_never_fatal(glab_machine):
    """Per host must not make the row unfailable, and glab is a FORGE row: a
    fleet whose work is all on GitHub, or on no forge, needs no GitLab credential."""
    expect(forge_tier(glab_machine, GLAB_HOSTS="gitlab.com", GLAB_OK=""), "missing  glab auth")
    assert preflight(glab_machine, GLAB_HOSTS="gitlab.com", GLAB_OK="").code == 0


def test_glab_defaulting_to_a_host_with_no_credential_is_a_lead_gap_with_its_fix(glab_machine):
    """Outside a repository glab talks to its DEFAULT host, which is gitlab.com
    unless told otherwise — so a machine whose one credential is for a
    self-hosted instance got a 401 from every glab call a lead made there."""
    out = lead_tier(glab_machine, GLAB_HOSTS=SELF_HOSTED, GLAB_OK=SELF_HOSTED)
    expect(out, "missing  glab host", "gitlab.com", f"glab config set host {SELF_HOSTED} --global")
    assert preflight(glab_machine, GLAB_HOSTS=SELF_HOSTED, GLAB_OK=SELF_HOSTED).code == 0

    for told in ({"GLAB_DEFAULT": SELF_HOSTED}, {"GITLAB_HOST": SELF_HOSTED}):
        refute(lead_tier(glab_machine, GLAB_HOSTS=SELF_HOSTED, GLAB_OK=SELF_HOSTED, **told), "missing  glab host")
    # Its own credential for the default host is the other way to be right.
    hosts = f"gitlab.com {SELF_HOSTED}"
    refute(lead_tier(glab_machine, GLAB_HOSTS=hosts, GLAB_OK=hosts), "missing  glab host")


# --- 7. the gh row is per ACCOUNT, and one expired token is not the answer -----

# Plain `auth status` exits 1, which is what gh does when ANY account has
# issues. A row that gated on it would report every healthy login as missing.
GH_PER_ACCOUNT = """
import os, sys
from pathlib import Path
a = sys.argv[1:]
listing = Path(os.environ["FLEET_STUB_ROOT"]) / "hosts.json"
if a[:1] == ["--version"]:
    print("gh version 2.100.0")
elif a[:2] == ["auth", "status"]:
    if "--json" in a and listing.exists():
        sys.stdout.write(listing.read_text(encoding="utf-8"))
    elif "--json" in a:
        sys.stderr.write("unknown flag: --json\\n")
        raise SystemExit(1)
    else:
        raise SystemExit(0 if os.environ.get("GH_ACTIVE_OK") else 1)
elif a[:2] == ["api", "user"]:
    if not os.environ.get("GH_ACTIVE_OK") and not listing.exists():
        raise SystemExit(1)
    print("octo")
else:
    sys.stderr.write("unexpected gh call\\n")
    raise SystemExit(9)
"""


def logins(stubs, *entries: tuple[str, str]) -> None:
    (stubs.root / "hosts.json").write_text(json.dumps({"hosts": {"github.com": [
        {"state": state, "active": i == 0, "host": "github.com", "login": login}
        for i, (login, state) in enumerate(entries)
    ]}}), encoding="utf-8")


def test_one_expired_token_among_working_logins_is_not_a_failed_preflight(stubs):
    logins(stubs, ("octo", "success"), ("client", "success"), ("worky", "success"), ("expired", "timeout"))
    path = machine(stubs, full_machine() | {"gh": GH_PER_ACCOUNT})
    assert preflight(path).code == 0
    out = plain(preflight(path, "--tier", "forge").out)
    refute(out, "missing  gh auth")
    expect(out, "3 of 4 accounts", "octo")
    # The login that did not authenticate is named, so a thinner answer is never silent.
    expect(out, "expired")


def test_not_one_account_authenticating_is_a_forge_gap_and_never_fatal(stubs):
    """Still reported with its remedy — just not a reason fleet cannot run."""
    logins(stubs, ("octo", "timeout"), ("worky", "timeout"))
    path = machine(stubs, full_machine() | {"gh": GH_PER_ACCOUNT})
    expect(plain(preflight(path, "--tier", "forge").out), "missing  gh auth", "gh auth login")
    assert preflight(path).code == 0


def test_a_gh_too_old_for_json_passes_on_the_active_session(stubs):
    """The fallback the seam documents: no account list means the ACTIVE
    session is asked alone."""
    path = machine(stubs, full_machine() | {"gh": GH_PER_ACCOUNT})
    assert preflight(path, GH_ACTIVE_OK="1").code == 0
    out = plain(preflight(path, "--tier", "forge", GH_ACTIVE_OK="1").out)
    refute(out, "missing  gh auth")
    expect(out, "octo")


# --- 8. the lead tier: what workers and the loop need from this machine -------

# ssh-keygen's half of `git commit -S` with `gpg.format = ssh`: git hands it a
# file to sign and reads `<file>.sig` back. The failing one says what an
# unreachable agent says.
SSH_KEYGEN_SIGNS = """
import sys
from pathlib import Path
Path(sys.argv[-1] + ".sig").write_text(
    "-----BEGIN SSH SIGNATURE-----\\nU1NIU0lH\\n-----END SSH SIGNATURE-----\\n", encoding="utf-8")
"""
SSH_KEYGEN_NO_AGENT = """
import sys
sys.stderr.write("Couldn't sign message: agent refused operation\\n")
raise SystemExit(255)
"""


def lead_tier(path: str, **env: str | None) -> str:
    return plain(preflight(path, "--tier", "lead", **env).out)


def ssh_signing(tmp_path, path: str, name: str) -> str:
    """A global git config that signs every commit through that ssh-keygen."""
    keygen = os.path.join(path, "ssh-keygen" + (".exe" if WINDOWS else ""))
    return git_config(tmp_path / f"gitconfig-{name}", (
        "[commit]\n\tgpgsign = true\n[gpg]\n\tformat = ssh\n"
        f"[gpg \"ssh\"]\n\tprogram = {keygen.replace(chr(92), '/')}\n"
        "[user]\n\tsigningkey = ~/.ssh/k.pub\n"
    ))


def test_a_signing_key_its_agent_cannot_reach_is_reported_with_gits_own_words(stubs, tmp_path):
    """The configuration was fine and every worker's commit still failed: the
    agent holding the key was gone after a crash or a reboot. Only signing
    something, with no terminal to prompt on, tells the two apart."""
    path = machine(stubs, full_machine() | {"ssh-keygen": SSH_KEYGEN_NO_AGENT})
    done = preflight(path, GIT_CONFIG_GLOBAL=ssh_signing(tmp_path, path, "no-agent"))
    out = plain(done.out)
    expect(out, "missing  commit signing", "agent refused operation", "SSH_AUTH_SOCK")
    assert done.code == 0, "a lead gap is reported and never fatal"

    path = machine(stubs, full_machine() | {"ssh-keygen": SSH_KEYGEN_SIGNS})
    out = lead_tier(path, GIT_CONFIG_GLOBAL=ssh_signing(tmp_path, path, "signs"))
    expect(out, "commit signing")
    refute(out, "missing  commit signing")


def test_refuel_with_no_agent_to_read_is_a_lead_gap_naming_agent_conf(stubs, tmp_path):
    """No AGENT, no FUEL_PROVIDER and no policy: every worker dispatched under
    thurbox's own default is `undetermined` to refuel, which restarts nothing."""
    path = machine(stubs, full_machine())
    out = lead_tier(path)
    expect(out, "missing  refuel agent", "agent.conf", "AGENT=")

    named = tmp_path / "named"
    write(named / "orchestration" / "agent.conf", "AGENT=some-agent\n")
    refute(lead_tier(path, FLEET_AGENT_ROOT=str(named)), "missing  refuel agent")
    refute(lead_tier(path, FLEET_AGENT_POLICY="github.com/acme=some-agent"), "missing  refuel agent")


def test_a_queue_fleet_cannot_write_is_a_lead_gap(stubs, tmp_path):
    """A sandbox that denies fleet's own writes failed `collect` and `dispatch`
    one call at a time. Probed by writing, since only the write is the answer;
    a file standing where the directory should be denies it on every OS."""
    path = machine(stubs, full_machine())
    blocked = tmp_path / "not-a-directory"
    write(blocked, "")
    out = lead_tier(path, FLEET_QUEUE_DIR=str(blocked))
    expect(out, "missing  fleet writes", str(blocked))
    refute(lead_tier(path), "missing  fleet writes")


def test_commands_never_prints_a_lead_rows_remedy(stubs):
    """A lead row is the operator's own configuration, with placeholders in it:
    a line for them to read, never one to hand to a shell unread."""
    path = machine(stubs, full_machine())
    expect(lead_tier(path), "missing  refuel agent")
    done = preflight(path, "--commands")
    assert (done.code, done.stdout) == (0, ""), done.out


# --- the table is data a second module can act on -----------------------------


@pytest.fixture
def pf():
    return lib("preflight.py")


def family(monkeypatch, pf, name: str) -> None:
    """Take that OS family's branch through the seam, whatever runs the test."""
    monkeypatch.setattr(pf.fleet_platform, "install_family", lambda: name)


@pytest.mark.parametrize("name", ["posix", "windows"])
def test_the_table_is_one_record_per_dependency_with_a_route_for_each(pf, monkeypatch, name):
    family(monkeypatch, pf, name)
    table = pf.dependencies()
    names = [d.name for d in table]
    assert len(names) == len(set(names)), names
    for d in table:
        assert d.tier in pf.TIERS and d.why, d
        assert set(d.packages) <= set(pf.MANAGERS), d
        if d.manual:
            # Not a package: the operator runs it themselves.
            assert not d.packages and not d.installer, d
        else:
            assert d.packages or d.installer.get(name) or d.see, d
    assert {"git", "uv", "thurbox-cli"} <= {d.name for d in table if d.tier == "required"}
    assert {"quota-axi"} <= {d.name for d in table if d.tier == "recommended"}
    # No forge is required: each is its own optional row, CLI and login alike.
    assert {d.name for d in table if d.tier == "forge"} == {"gh", "gh auth", "glab", "glab auth"}
    assert {"lua", "prek"} <= {d.name for d in table if d.tier == "gate"}
    assert {"commit signing", "refuel agent", "glab host", "fleet writes"} == {
        d.name for d in table if d.tier == "lead"}
    # Retired by the port: Python and PyYAML come with uv, and nothing runs bash, jq or shellcheck.
    assert not {"python3", "PyYAML", "jq", "shellcheck", "bash"} & set(names)
    assert next(d for d in table if d.name == "thurbox-cli").floor == FLOOR


def test_the_multiplexer_is_psmux_on_windows_and_tmux_elsewhere(pf, monkeypatch):
    family(monkeypatch, pf, "windows")
    names = {d.name for d in pf.dependencies()}
    assert "psmux" in names and "tmux" not in names
    family(monkeypatch, pf, "posix")
    table = {d.name: d for d in pf.dependencies()}
    assert "tmux" in table and "psmux" not in table
    assert table["tmux"].tier == "required" and table["tmux"].floor == "3.2"


def test_windows_picks_winget_and_posix_the_first_manager_in_its_fixed_order(pf, monkeypatch, stubs):
    monkeypatch.setenv("PATH", machine(stubs, {"winget": says(""), "brew": says(""), "dnf": says("")}))
    family(monkeypatch, pf, "windows")
    assert pf.package_manager() == "winget"
    family(monkeypatch, pf, "posix")
    assert pf.package_manager() == "dnf", "a distro's own manager before brew"

    monkeypatch.setenv("PATH", machine(stubs, {"brew": says("")}, without=("winget", "dnf")))
    assert pf.package_manager() == "brew"
    family(monkeypatch, pf, "windows")
    assert pf.package_manager() is None, "brew is not a Windows manager"


def test_each_manager_gets_its_own_argv(pf, monkeypatch):
    family(monkeypatch, pf, "posix")
    gh = next(d for d in pf.dependencies() if d.name == "gh")
    assert pf.install_plan(gh, "apt").argv == ("sudo", "apt-get", "install", "-y", "gh")
    assert pf.install_plan(gh, "dnf").argv == ("sudo", "dnf", "install", "-y", "gh")
    assert pf.install_plan(gh, "pacman").argv == ("sudo", "pacman", "-S", "--needed", "github-cli")
    assert pf.install_plan(gh, "brew").argv == ("brew", "install", "gh")
    assert pf.install_plan(gh, "winget").argv == ("winget", "install", "--id", "GitHub.cli", "-e")
    assert pf.install_plan(gh, "winget").text == "winget install --id GitHub.cli -e"


def test_thurbox_prefers_its_official_installer_and_records_winget_as_the_alternative(pf, monkeypatch):
    family(monkeypatch, pf, "windows")
    thurbox = next(d for d in pf.dependencies() if d.name == "thurbox-cli")
    on_windows = pf.install_plan(thurbox, "winget")
    assert on_windows.argv[0] == "powershell" and "install.ps1" in on_windows.text
    assert thurbox.alternatives == {"winget": "Thurbeen.thurbox"}
    family(monkeypatch, pf, "posix")
    assert pf.install_plan(thurbox, "brew").argv == ("brew", "install", "thurbeen/thurbox/thurbox")
    for manager in ("apt", "dnf", "pacman", None):
        plan = pf.install_plan(thurbox, manager)
        assert plan.argv[:2] == ("sh", "-c") and "install.sh" in plan.text


def test_no_manager_falls_back_to_the_tools_own_installer_or_to_nothing(pf, monkeypatch):
    family(monkeypatch, pf, "posix")
    table = {d.name: d for d in pf.dependencies()}
    assert "astral.sh/uv/install.sh" in pf.install_plan(table["uv"], None).text
    assert pf.install_plan(table["gh"], None) is None
    assert pf.install_plan(table["gh auth"], "apt") is None, "a login is not a package"
    family(monkeypatch, pf, "windows")
    assert "astral.sh/uv/install.ps1" in pf.install_plan(table["uv"], None).text


@pytest.mark.parametrize(("name", "manager", "want"), [
    ("windows", "winget", "winget install --id GitHub.cli -e"),
    ("posix", "apt-get", "sudo apt-get install -y gh"),
])
def test_commands_prints_the_install_line_for_that_family(pf, monkeypatch, stubs, capsys, name, manager, want):
    monkeypatch.setenv("PATH", machine(stubs, full_machine() | {manager: says("")}, without=("gh",)))
    family(monkeypatch, pf, name)
    # A forge gap is a line to run and never a failed preflight.
    assert pf.main(["--commands"]) == 0
    assert want in capsys.readouterr().out.splitlines()


def test_missing_returns_the_failing_records_limited_to_tiers(pf, monkeypatch, stubs):
    monkeypatch.setenv("PATH", machine(stubs, full_machine() | {"tmux": says("tmux 3.1")}, without=("gh", "lua")))
    family(monkeypatch, pf, "posix")
    gaps = {f.dependency.name: f for f in pf.missing()}
    assert {"gh", "lua", "tmux"} <= set(gaps), gaps
    assert gaps["tmux"].state == "stale"
    assert "gh auth" not in gaps, "a login is only probed once its tool is there"
    assert {f.dependency.name for f in pf.missing(["required"])} == {"tmux"}
    assert {f.dependency.name for f in pf.missing(["forge"])} == {"gh"}
