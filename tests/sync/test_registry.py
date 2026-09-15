"""`fleet sync-registry`: the map covers every `gh` account, not just the active one.

A machine with more than one `gh` login reaches a DIFFERENT set of repositories
per login: one sees the personal org, another the employer's, a third a
client's. `user/repos` answers for whichever account is ACTIVE, so a single
pass wrote a map missing every owner the other logins reach, and its only
symptom was the `no accessible repos` warning a MISTYPED owner produces. The
fix has to keep that warning meaning exactly one thing.

Run against a COPY of the module in a sandbox root: it resolves its paths from
where it lives and OVERWRITES registry/repos.generated.yaml, so driving the real
one would rewrite the operator's map with fixture data.
"""

from __future__ import annotations

import pytest
from harness import expect, lib, refute, run_fleet
from synckit import GH, fixture, hosts_json, repo_json, run_module, sandbox

REACHED = ("octo", "acme-org", "client-org", "employer-org")


@pytest.fixture
def root(tmp_path, stubs):
    # `typo-owner` is listed and no account reaches it: the typo signal.
    root = sandbox(tmp_path / "checkout", "# fixture\nocto\nacme-org\nclient-org\nemployer-org\ntypo-owner\n")
    # Disjoint per account, except acme-org/shared, which two of them see.
    fixture(stubs, "repos-octo.json", repo_json("octo", "own-repo") + repo_json("acme-org", "tool")
            + repo_json("acme-org", "shared"))
    fixture(stubs, "repos-client.json", repo_json("client-org", "client-thing") + repo_json("acme-org", "shared"))
    fixture(stubs, "repos-worky.json", repo_json("employer-org", "work-thing"))
    # Five logins, three shapes: three that work, one expired, and one that
    # reports success but whose token cannot be read back.
    fixture(stubs, "hosts.json", hosts_json(("octo", "success"), ("client", "success"), ("worky", "success"),
                                            ("tokenless", "success"), ("expired", "timeout")))
    stubs.tool("gh", GH)
    return root


def the_map(root) -> str:
    return (root / "registry" / "repos.generated.yaml").read_text(encoding="utf-8")


def test_every_owner_any_account_reaches_is_in_a_map_of_the_shape_the_control_plane_reads(root):
    done = run_module(root, "sync_registry.py")
    assert done.code == 0, done.out

    lines = the_map(root).splitlines()
    for owner in REACHED:
        assert f"  - name: {owner}" in lines, f"owner {owner} missing\n{done.out}"
    # The gate no longer validates the operator's own map, so this is where the
    # generator and the validator are held to one shape.
    _summary, problems = lib("check_yaml.py").registry_problems(str(root / "registry" / "repos.generated.yaml"))
    assert problems == [], problems


def test_no_accessible_repos_means_only_an_owner_no_account_reaches(root):
    done = run_module(root, "sync_registry.py")
    expect(done.out, "no accessible repos for owner 'typo-owner'")
    refute(done.out, *(f"no accessible repos for owner '{owner}'" for owner in REACHED))


def test_a_repo_two_accounts_reach_is_one_repo_and_the_totals_count_the_merged_set(root):
    run_module(root, "sync_registry.py")
    text = the_map(root)
    assert text.splitlines().count("    - name: shared") == 1
    expect(text, "repos: 5", "owners: 4")


def test_asking_every_account_never_switches_the_active_one(root, stubs):
    run_module(root, "sync_registry.py")
    assert stubs.calls("gh", "api"), "the accounts were asked"
    assert not [c for c in stubs.calls("gh") if "switch" in c]


def test_a_login_that_no_longer_works_is_named_and_costs_only_its_own_repos(root):
    done = run_module(root, "sync_registry.py")
    expect(done.out, "expired", "tokenless", f"wrote {root / 'registry' / 'repos.generated.yaml'}")


def test_a_gh_with_no_account_list_still_syncs_from_the_active_account(root, stubs):
    """That fallback keeps the floor where it was: "gh exists and is authenticated"."""
    fixture(stubs, "mode", "old\n")
    done = run_module(root, "sync_registry.py")
    assert done.code == 0, done.out
    expect(the_map(root), "repos: 3")
    expect(done.out, "no accessible repos for owner 'employer-org'")


def test_a_token_in_the_environment_is_the_one_account_asked(root, stubs):
    """It overrides every stored account anyway, so enumerating them would
    describe repositories the sync cannot reach."""
    done = run_module(root, "sync_registry.py", GH_TOKEN="tok-client")
    assert done.code == 0, done.out
    expect(the_map(root), "repos: 2")
    assert stubs.calls("gh", "auth") == []


def test_every_account_failing_refuses_and_leaves_the_map_untouched(root, stubs):
    """Every listing failing is not a thinner map but no answer at all, and
    writing `owners: []` over the operator's only index would erase it."""
    assert run_module(root, "sync_registry.py").code == 0
    before = the_map(root)
    fixture(stubs, "mode", "dead\n")

    done = run_module(root, "sync_registry.py")

    assert done.code == 1, done.out
    assert the_map(root) == before
    refute(done.out, "wrote ")


def test_a_missing_or_empty_owners_file_is_refused_with_the_remedy(tmp_path, stubs):
    stubs.tool("gh", GH)
    bare = sandbox(tmp_path / "bare", None)
    done = run_module(bare, "sync_registry.py")
    assert done.code == 1
    expect(done.out, "cp registry/owners.example.txt registry/owners.txt")

    empty = sandbox(tmp_path / "empty", "# nothing yet\n\n")
    done = run_module(empty, "sync_registry.py")
    assert done.code == 1
    expect(done.out, "no owners configured")


def test_the_fleet_group_is_wired_to_it():
    done = run_fleet("sync-registry", "--help")
    assert done.code == 0, done.out
    expect(done.stdout, "registry/repos.generated.yaml")
