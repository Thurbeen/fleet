"""Which `gh` logins and which GitLab instances this machine holds.

`scripts/lib/gh_accounts.py` asks per ACCOUNT and `scripts/lib/glab_hosts.py`
per HOST, because each CLI's own status command is all-or-nothing: one lapsed
login among three, or one unused instance beside the one the work lives on,
turned a working setup into a reported failure. Both read and never write, and
nothing here switches the account the operator's `gh` points at.
"""

import json
import os

from harness import lib

HOSTS = {"hosts": {
    "github.com": [
        {"state": "success", "active": True, "host": "github.com", "login": "octo"},
        {"state": "success", "active": False, "host": "github.com", "login": "client"},
        {"state": "timeout", "active": False, "host": "github.com", "login": "expired"},
    ],
    "ghe.example.com": [{"state": "success", "active": True, "host": "ghe.example.com", "login": "elsewhere"}],
}}

# `auth status --json` is the account list and `auth token --user` hands one
# over by name. `api` answers with the token it was called under, so a test can
# see which account asked.
GH = """
import os, sys
from pathlib import Path
root = Path(os.environ["FLEET_STUB_ROOT"])
args = sys.argv[1:]
if args[:2] == ["auth", "status"] and "--json" in args:
    listing = root / "hosts.json"
    if not listing.exists():
        sys.stderr.write("unknown flag: --json\\n")
        raise SystemExit(1)
    sys.stdout.write(listing.read_text(encoding="utf-8"))
elif args[:2] == ["auth", "token"]:
    user = args[args.index("--user") + 1]
    if user not in ("octo", "client"):
        raise SystemExit(1)
    print("tok-" + user)
elif args[:1] == ["api"]:
    print(os.environ.get("GH_TOKEN", "<active>"))
else:
    sys.stderr.write("unexpected gh call\\n")
    raise SystemExit(9)
"""

# What glab 1.117.0 prints: each configured instance at column 0, its findings
# indented under it, and a non-zero exit when any one of them failed.
GLAB = """
import os, sys
from pathlib import Path
root = Path(os.environ["FLEET_STUB_ROOT"])
hosts = ["gitlab.com", "gitlab.example.com"]
ok = ["gitlab.example.com"]
args = sys.argv[1:]
if args[:2] != ["auth", "status"]:
    raise SystemExit(9)
if "--all" in args:
    for h in hosts:
        print(h)
        print(f"  ✓ Logged in to {h} as someone" if h in ok else f"  x {h}: API call failed: 401")
    sys.stderr.write("  ERROR: one or more instances failed\\nhint\\n")
    raise SystemExit(1)
host = args[args.index("--hostname") + 1]
raise SystemExit(0 if host in ok else 1)
"""


def gh_with_accounts(stubs):
    stubs.tool("gh", GH)
    (stubs.root / "hosts.json").write_text(json.dumps(HOSTS), encoding="utf-8")


def test_every_working_login_is_listed_in_ghs_order_and_a_lapsed_one_is_named(stubs, capsys):
    gh_with_accounts(stubs)
    assert lib("gh_accounts.py").accounts("github.com") == ["octo", "client"]
    assert "warning: gh account 'expired' on github.com is timeout — skipped" in capsys.readouterr().err


def test_no_readable_list_is_an_empty_answer_and_never_a_failure(stubs, monkeypatch, tmp_path):
    gh_with_accounts(stubs)
    accounts = lib("gh_accounts.py").accounts

    assert accounts("gitlab.example.com") == [], "a host gh holds nothing for"

    # A token in the environment overrides every stored account, so listing
    # them would describe repositories the caller cannot reach.
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        with monkeypatch.context() as m:
            m.setenv(var, "from-the-environment")
            assert accounts("github.com") == []
    assert stubs.calls("gh", "auth status") == ["gh auth status --json hosts"]

    # An older gh with no `auth status --json`.
    (stubs.root / "hosts.json").unlink()
    assert accounts("github.com") == []

    # No gh on PATH at all.
    empty = tmp_path / "no-tools"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert accounts("github.com") == []


def test_a_token_is_read_by_name_and_nothing_switches_the_active_account(stubs):
    gh_with_accounts(stubs)
    gh = lib("gh_accounts.py")
    assert gh.account_token("github.com", "client") == "tok-client"
    assert gh.account_token("github.com", "expired") == ""
    assert "gh auth token --hostname github.com --user client" in stubs.calls("gh")
    assert not [c for c in stubs.calls("gh") if "switch" in c]


def test_a_token_lives_exactly_as_long_as_its_one_call(stubs):
    gh_with_accounts(stubs)
    gh = lib("gh_accounts.py")
    assert gh.api_as("tok-client", "user").stdout.strip() == "tok-client"
    assert "GH_TOKEN" not in os.environ
    # An empty token is the active session, which is the fallback path.
    assert gh.api_as("", "user").stdout.strip() == "<active>"


def test_every_configured_instance_is_listed_even_when_one_fails(stubs):
    stubs.tool("glab", GLAB)
    # Findings are indented and a stray unindented word is not a hostname.
    assert lib("glab_hosts.py").hosts() == ["gitlab.com", "gitlab.example.com"]


def test_each_instance_is_answered_for_alone(stubs):
    stubs.tool("glab", GLAB)
    glab = lib("glab_hosts.py")
    assert glab.host_ok("gitlab.example.com") is True
    assert glab.host_ok("gitlab.com") is False


def test_no_glab_is_no_hosts_and_no_credential(monkeypatch, tmp_path):
    empty = tmp_path / "no-tools"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    glab = lib("glab_hosts.py")
    assert glab.hosts() == []
    assert glab.host_ok("gitlab.com") is False
