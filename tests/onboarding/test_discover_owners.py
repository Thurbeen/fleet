"""discover-owners: three sources, every gh login, and GitLab is not one of them.

registry/owners.txt is the one input the map needs, and it is already written
down on the machine: in every `gh` login, in the git config, and in the remotes
of the clones on disk. Discovery reads all three and prints candidates with
their evidence. The clone scan matches an ssh host ALIAS, because matching the
literal `github.com` found none of the clones on the machine it was written on,
and a GitLab remote is evidence and never an owner: owners.txt is read by `gh`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from harness import expect, lib, refute, run_fleet, write
from kit import GIT, between, machine


def discover(path: str, home: Path, *roots: Path, **env: str | None):
    return run_fleet("discover-owners", *map(str, roots), PATH=path, HOME=str(home), USERPROFILE=str(home), **env)


@pytest.fixture
def home(tmp_path) -> Path:
    """A HOME of its own, whose git config names the account only by its noreply address."""
    home = tmp_path / "home"
    write(home / ".gitconfig", "[user]\n\temail = 4242+octo@users.noreply.github.com\n")
    return home


@pytest.fixture
def tree(tmp_path) -> Path:
    """Every remote shape that matters, and two checkouts nobody works in."""
    tree = tmp_path / "clones"

    def origin(where: str, url: str, extra: str = "") -> None:
        write(tree / where / ".git" / "config", f'[remote "origin"]\n\turl = {url}\n{extra}')

    origin("a", "git@github-perso:aliased-owner/thing.git")
    origin("b", "https://github.com/plain-owner/thing.git")
    origin("c", "git@gitlab.example.com:group/thing.git")
    origin("d", "git@github-perso:aliased-owner/other.git")
    # A fork: only origin names an owner the operator has repos under.
    origin("e", "git@github.com:fork-owner/linux.git",
           '[remote "upstream"]\n\turl = https://github.com/upstream-owner/linux.git\n')
    # GitHub's SSH-over-HTTPS workaround: the `:443` is a PORT.
    origin("f", "ssh://git@ssh.github.com:443/porty-owner/thing.git")
    origin(".vim/plugged/vim-thing", "https://github.com/plugin-author/vim-thing.git")
    origin("g/node_modules/pkg", "https://github.com/npm-author/pkg.git")
    return tree


GH_ACTIVE = """
import sys
a = sys.argv[1:]
if a[:1] == ["auth"]:
    raise SystemExit(0)
if a[:2] == ["api", "user"]:
    print("octo")
elif a[:2] == ["api", "user/orgs"]:
    print("acme")
    print("beta")
"""


# --- 2. three sources, and GitLab is not one of them --------------------------


def test_gh_the_git_config_and_every_remote_shape_name_their_owners(stubs, home, tree):
    done = discover(machine(stubs, {"git": GIT, "gh": GH_ACTIVE}), home, tree)
    assert done.code == 0, done.out
    expect(done.out, "octo", "acme", "gh account")
    expect(done.out, "aliased-owner", "local clones (2)", "plain-owner", "porty-owner", "fork-owner")
    expect(done.out, "GITLAB CHECKOUTS", "gitlab.example.com/group")

    # What an operator copies into owners.txt is asserted against that section
    # alone: the GitLab evidence below it names the same strings on purpose.
    candidates = between(done.stdout, "CANDIDATE OWNERS", "GITLAB CHECKOUTS")
    refute(candidates, "443", "upstream-owner", "plugin-author", "npm-author", "group")


def test_no_gh_at_all_still_discovers_from_the_git_config_and_says_so(stubs, home, tree):
    done = discover(machine(stubs, {"git": GIT}), home, tree)
    assert done.code == 0, done.out
    expect(done.out, "octo", "gh auth login")


def test_a_machine_that_names_nothing_invents_no_owner_and_is_not_a_failure(stubs, tmp_path):
    """The map is optional, so a local-only machine with no forge anywhere is an
    answer: nothing is offered, the remedy is named, and it exits 0."""
    bare = tmp_path / "emptyhome"
    (tmp_path / "noclones").mkdir()
    bare.mkdir()
    done = discover(machine(stubs, {"git": GIT}), bare, tmp_path / "noclones")
    assert done.code == 0, done.out
    expect(done.out, "No candidate owners found", "owners.example.txt", "optional")
    refute(done.out, "CANDIDATE OWNERS")


def test_a_usage_error_exits_2(stubs, home):
    assert discover(machine(stubs, {"git": GIT}), home, Path("--bogus")).code == 2
    done = run_fleet("discover-owners", "--help")
    assert done.code == 0
    expect(done.stdout, "registry/owners.txt")


def test_the_default_roots_are_the_checkouts_parent_and_code_directories_under_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    roots = lib("discover_owners.py").default_roots()
    assert roots[0] == Path(__file__).resolve().parents[2].parent
    assert tmp_path / "code" in roots and tmp_path / "src" in roots


# --- 5. every login is asked, not only the active one --------------------------

# `sso` authenticates in gh's listing and the API refuses its token; `solo`
# answers fully and belongs to no org. Plain `auth status` exits 1, which is
# what gh does when any account has issues.
GH_LOGINS = """
import os, sys
from pathlib import Path
a = sys.argv[1:]
listing = Path(os.environ["FLEET_STUB_ROOT"]) / "hosts.json"
token = os.environ.get("GH_TOKEN", "tok-octo")
if a[:2] == ["auth", "status"]:
    if "--json" in a and listing.exists():
        sys.stdout.write(listing.read_text(encoding="utf-8"))
    else:
        sys.stderr.write("unknown flag: --json\\n" if "--json" in a else "expired: authentication failed\\n")
        raise SystemExit(1 if "--json" in a else (0 if not listing.exists() else 1))
elif a[:2] == ["auth", "token"]:
    user = a[a.index("--user") + 1]
    if user not in ("octo", "client", "worky", "sso", "solo"):
        raise SystemExit(1)
    print("tok-" + user)
elif a[:2] == ["api", "user"]:
    who = {"tok-octo": "octo", "tok-client": "client", "tok-worky": "worky", "tok-solo": "solo"}.get(token)
    if not who:
        sys.stderr.write("HTTP 401: SAML enforcement\\n")
        raise SystemExit(1)
    print(who)
elif a[:2] == ["api", "user/orgs"]:
    org = {"tok-octo": "acme-org", "tok-client": "client-org", "tok-worky": "employer-org"}.get(token)
    if org:
        print(org)
else:
    sys.stderr.write("unexpected gh call\\n")
    raise SystemExit(9)
"""


def five_logins(stubs) -> None:
    entries = [("octo", "success"), ("client", "success"), ("worky", "success"),
               ("sso", "success"), ("solo", "success"), ("expired", "timeout")]
    (stubs.root / "hosts.json").write_text(json.dumps({"hosts": {"github.com": [
        {"state": s, "active": i == 0, "host": "github.com", "login": login} for i, (login, s) in enumerate(entries)
    ]}}), encoding="utf-8")


def test_every_login_and_the_orgs_only_it_can_see_are_candidates(stubs, home, tmp_path):
    five_logins(stubs)
    (tmp_path / "noclones").mkdir()
    done = discover(machine(stubs, {"git": GIT, "gh": GH_LOGINS}), home, tmp_path / "noclones")
    assert done.code == 0, done.out
    candidates = between(done.stdout, "CANDIDATE OWNERS")
    expect(candidates, "octo", "worky", "client", "acme-org", "employer-org", "client-org")

    # A lapsed login is reported and never offered.
    refute(candidates, "expired")
    expect(done.out, "expired")
    # A login gh calls healthy whose token the API refuses is NAMED: one
    # silently missing from the list is an owner never offered.
    expect(done.out, "gh account 'sso' could not say who it is")
    refute(candidates, "sso")
    # The scope advice is about a machine where NO login returned an org; a
    # login that belongs to none is fine.
    expect(candidates, "solo")
    refute(done.out, "missing a scope")


def test_a_gh_too_old_for_json_asks_the_active_session_alone(stubs, home, tmp_path):
    (tmp_path / "noclones").mkdir()
    done = discover(machine(stubs, {"git": GIT, "gh": GH_LOGINS}), home, tmp_path / "noclones")
    assert done.code == 0, done.out
    expect(done.out, "octo", "acme-org")
