"""`fleet add-owner`: the path for what the operator gains AFTER the first run.

Onboarding is a first run and converges on a re-run, but what actually happens
later had no path at all: the operator gains an owner, a repository or a whole
`gh` account, and the map has to catch up. That meant hand-editing
registry/owners.txt and remembering which command to re-run, and nothing told
them what a newly authenticated account even reaches.

Everything runs against a COPY in a sandbox root: add-owner writes
registry/owners.txt and syncs, which OVERWRITES registry/repos.generated.yaml.
"""

from __future__ import annotations

import pytest
from harness import expect, refute, run_fleet
from synckit import GH, GLAB, fixture, hosts_json, repo_json, run_module, sandbox

# The operator's file as it actually looks: a comment header that documents the
# format for whoever edits it by hand, then entries in the order the map is
# emitted in. Both survive every write.
OWNERS = "# GitHub owners the map covers, one per line.\n# `#` starts a comment; blank lines are ignored.\nocto\nacme-org\n"


@pytest.fixture
def root(tmp_path, stubs):
    root = sandbox(tmp_path / "checkout", OWNERS)
    fixture(stubs, "repos-octo.json", repo_json("octo", "own-repo") + repo_json("octo", "second-repo")
            + repo_json("acme-org", "tool"))
    fixture(stubs, "repos-worky.json", repo_json("employer-org", "work-thing"))
    fixture(stubs, "orgs-octo.txt", "acme-org\n")
    fixture(stubs, "orgs-worky.txt", "employer-org\n")
    # Only `octo` to begin with, the machine before `gh auth login` for the
    # second account; and after, `worky` reaches an owner the map never heard of.
    fixture(stubs, "hosts-before.json", hosts_json(("octo", "success")))
    fixture(stubs, "hosts.json", hosts_json(("octo", "success"), ("worky", "success")))
    stubs.tool("gh", GH)
    # An authenticated GitLab instance, which must change what is reported and
    # never what is written.
    stubs.tool("glab", GLAB)
    # The machine that already has owners and a map, the only one this is for.
    assert run_module(root, "sync_registry.py", HOSTS_FIXTURE="hosts-before.json").code == 0
    assert (root / "registry" / "repos.generated.yaml").is_file()
    return root


def add_owner(root, *args, **env):
    return run_module(root, "add_owner.py", *args, **env)


def owners_file(root) -> str:
    return (root / "registry" / "owners.txt").read_text(encoding="utf-8")


def summary_of(out: str) -> str:
    return "\n".join(line for line in out.splitlines() if "not in registry/owners.txt" in line)


def test_what_is_new_is_named_by_the_account_that_reaches_it(root):
    done = add_owner(root)
    assert done.code == 0, done.out
    # A newly authenticated account's OWN login is an owner the map does not
    # cover either, so the answer is two, not one.
    expect(done.out, "worky", "employer-org", "2 owners", "fleet add-owner")


def test_the_summary_offers_only_owners_the_file_does_not_hold(root):
    """The summary line is what an operator acts on, so it is asserted alone."""
    summary = summary_of(add_owner(root).out)
    expect(summary, "employer-org", "worky")
    refute(summary, "acme-org", "octo")


def test_an_authenticated_gitlab_host_is_evidence_and_never_an_owner(root):
    done = add_owner(root)
    expect(done.out, "gitlab.example.com")
    refute(summary_of(done.out), "gitlab.example.com")


def test_adding_every_new_owner_reports_what_moved_and_not_the_map(root, stubs):
    # A repository disappears at the same time, so both directions are seen.
    fixture(stubs, "repos-octo.json", repo_json("octo", "own-repo") + repo_json("acme-org", "tool"))

    done = add_owner(root, "--all")

    assert done.code == 0, done.out
    expect(done.out, "employer-org", "across 3 owners (was 3 repos across 2 owners)",
           "employer-org/work-thing", "octo/second-repo")
    refute(done.out, "pushed_at")


def test_the_file_keeps_its_header_and_its_order_and_the_map_carries_the_new_owner(root):
    """Its order is the order the map is emitted in: appending is right, reshuffling is churn."""
    assert add_owner(root, "--all").code == 0
    text = owners_file(root)
    expect(text, "# GitHub owners the map covers")
    entries = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert entries == ["octo", "acme-org", "worky", "employer-org"]
    expect((root / "registry" / "repos.generated.yaml").read_text(encoding="utf-8"), "  - name: employer-org")


def test_a_file_with_no_final_newline_does_not_get_an_owner_glued_on(root):
    (root / "registry" / "owners.txt").write_bytes(b"octo\nacme-org")
    assert add_owner(root, "employer-org").code == 0
    assert owners_file(root).splitlines() == ["octo", "acme-org", "employer-org"]


def test_a_second_run_has_nothing_to_offer(root):
    assert add_owner(root, "--all").code == 0
    done = add_owner(root)
    assert done.code == 0
    expect(done.out, "Nothing new")
    refute(done.out, "not in registry/owners.txt:")


def test_a_duplicate_is_refused_and_the_file_is_not_touched(root):
    before = owners_file(root)
    done = add_owner(root, "acme-org")
    assert done.code == 1
    expect(done.out, "acme-org")
    assert owners_file(root) == before

    done = add_owner(root, "employer-org", "employer-org")
    assert done.code == 1
    expect(done.out, "named twice")
    assert owners_file(root) == before


def test_what_counts_as_an_owner_is_not_widened(root):
    """A GitLab group path, a host-qualified name or a URL is not a GitHub owner,
    and a file that accepted one would warn `no accessible repos` forever."""
    before = owners_file(root)
    done = add_owner(root, "group/subgroup")
    assert done.code == 1
    expect(done.out, "GitHub owner")
    refute(done.out, "MAP CHANGED")
    assert add_owner(root, "gitlab.example.com/group").code == 1
    assert owners_file(root) == before


def test_a_clone_with_no_owners_file_is_refused_and_pointed_at_onboarding(tmp_path, stubs):
    stubs.tool("gh", GH)
    bare = sandbox(tmp_path / "bare", None)
    done = add_owner(bare)
    assert done.code == 1
    expect(done.out, "discover-owners")


def test_all_with_no_account_that_answered_is_refused(root, stubs):
    """Nothing was compared against anything, so an empty answer is not agreement."""
    fixture(stubs, "hosts.json", '{"hosts":{"github.com":[]}}')
    fixture(stubs, "mode", "dead\n")
    before = owners_file(root)

    done = add_owner(root, "--all")

    assert done.code == 1
    expect(done.out, "No gh account answered")
    refute(done.out, "Nothing new")
    assert owners_file(root) == before


def test_a_usage_error_exits_2_and_the_fleet_group_is_wired():
    assert run_fleet("add-owner", "--bogus").code == 2
    done = run_fleet("add-owner", "--help")
    assert done.code == 0
    expect(done.stdout, "--all")
