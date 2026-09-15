"""Claim 9, the shepherd's gates: where it may merge, and what counts as passed.

9i. WHERE fleet may merge used to be a literal in `scripts/lib/queue.py`, so
    the only way to name a repository was a commit in this public repo, and
    every clone inherited the last operator's list. It is now
    `orchestration/auto-merge.conf`: the operator's, gitignored, absent by
    default, matched HOST-QUALIFIED — and the tracked copy names nothing.
9j. A check is judged by its LATEST run. GitHub's statusCheckRollup keeps
    superseded runs: a title edit re-ran `PR Title`, the rollup held a FAILURE
    under two later SUCCESSes, and the shepherd sent fixers at two healthy PRs.
9k. The attestation has two shapes, JSON inside the HTML comment and a marker
    followed by a fenced block, and one rule over both: a verdict naming a
    commit that is not the head authorises nothing.
"""

import shutil
from pathlib import Path

import pytest
from kit_shepherd import Shep, repo, result
from queuekit import ok

from harness import REPO, expect, git, queue_module, refute, write
from harness import run_queue as q

UNVETTED = "Reviewed, tested, linted, and opened through the pipeline.\n"


@pytest.fixture
def shep(stubs) -> Shep:
    return Shep(stubs)


@pytest.fixture
def ttopic(shep, tmp_path, queue_dir) -> str:
    """A repository the conf file names: one attested PR a task records, one unvetted PR nobody does."""
    srepo = repo(tmp_path / "shepherd-repo")
    topic = ok(q("topic", "add", "thurbox-allowlist", "--title", "Auto-merge from the conf file",
                 "--prompt", "a named repo merges on fleet own gates, and only host-qualified")).stdout.strip()
    ok(q("add", topic, "attested", "--title", "A PR the pipeline vetted", "--repo", str(srepo),
         "--branch", "tbx/attested", "--number", "01"))
    result(queue_dir / topic / "01-attested", "https://github.com/Thurbeen/thurbox/pull/201")
    git("branch", "tbx/attested", cwd=srepo)
    shep.perm("maintainer", "admin")
    shep.pr(201, owner="Thurbeen/thurbox", headRefName="tbx/attested")
    # Green in every way the forge can see, nothing vetted its head, and no task records it.
    shep.pr(202, owner="Thurbeen/thurbox", headRefName="tbx/unvetted", body=UNVETTED)
    # The artifact reaches the record through `collect`, and the shepherd derives the repository from it.
    ok(q("collect"))
    return topic


def settings_root(where: Path, conf: str | None) -> str:
    (where / "orchestration").mkdir(parents=True)
    if conf is not None:
        write(where / "orchestration" / "auto-merge.conf", conf)
    return str(where)


def test_a_named_repository_merges_on_fleets_own_gates_and_no_looser(ttopic, shep):
    out = q("shepherd", "--topic", ttopic).out
    expect(out, "Thurbeen/thurbox")
    assert 201 in shep.merged(), out
    expect(shep.gh_log(), "pr merge https://github.com/Thurbeen/thurbox/pull/201 --squash --delete-branch")
    # Naming a repository adds a REPOSITORY and not a looser rule.
    assert 202 not in shep.merged(), out
    expect(out, "the body carries no attestation")


def test_a_bare_slug_is_refused_in_the_file_and_in_the_environment(ttopic, tmp_path):
    bare = settings_root(tmp_path / "automerge-bare", "# a slug, which names no forge\nThurbeen/thurbox\n")
    out = q("shepherd", "--topic", ttopic, "--dry-run", FLEET_AUTO_MERGE_ROOT=bare).out
    expect(out, "must name its forge")
    refute(out, "would-merge")

    # The environment REPLACES the file, so this also proves the file did not leak past it.
    out = q("shepherd", "--topic", ttopic, "--dry-run", FLEET_AUTO_MERGE_REPOS="Thurbeen/thurbox").out
    expect(out, "must name its forge")
    refute(out, "would-merge")


def test_with_no_list_at_all_fleet_merges_nothing_and_names_the_file(ttopic, tmp_path):
    empty = settings_root(tmp_path / "automerge-none", None)
    out = q("shepherd", "--topic", ttopic, "--dry-run", FLEET_AUTO_MERGE_ROOT=empty).out
    expect(out, "Fleet merges NOTHING", "orchestration/auto-merge.conf")
    refute(out, "would-merge")


def test_the_copy_this_repo_ships_names_no_repository(tmp_path, monkeypatch):
    """A public, agnostic repo hands a fresh clone no merge rights over anybody's repositories."""
    tracked = REPO / "orchestration" / "auto-merge.example.conf"
    entries = [line.split("#", 1)[0].strip() for line in tracked.read_text(encoding="utf-8").splitlines()]
    assert not any(entries), entries

    clone = tmp_path / "fresh-clone"
    (clone / "orchestration").mkdir(parents=True)
    shutil.copy(tracked, clone / "orchestration" / tracked.name)
    monkeypatch.setenv("FLEET_AUTO_MERGE_ROOT", str(clone))
    shipped = queue_module(
        "import forge\n"
        "root = q.checkout_root()\n"
        "path = q.auto_merge_conf_path(root)\n"
        "repos = sorted(q.auto_merge_repos(root))\n"
        "print('entries=' + (' '.join(repos) or 'none'))\n"
        "print('unqualified=' + (' '.join(r for r in repos if forge.RepoId.parse(r) is None) or 'none'))\n"
        "print('reading=' + ('operator' if path.endswith(q.AUTO_MERGE_CONF) else 'tracked'))\n"
    )
    expect(shipped, "reading=tracked", "entries=none", "unqualified=none")


def test_a_check_is_judged_by_its_latest_run(ttopic, shep):
    def run(conclusion, at, name="PR Title"):
        if conclusion is None:
            return {"__typename": "CheckRun", "name": name, "status": "IN_PROGRESS", "conclusion": "",
                    "startedAt": at, "completedAt": None}
        return {"__typename": "CheckRun", "name": name, "status": "COMPLETED", "conclusion": conclusion,
                "startedAt": at, "completedAt": at}

    # The real rollup, out of order the way nothing promises it is not.
    shep.pr(203, owner="Thurbeen/thurbox", headRefName="tbx/reruns-203", statusCheckRollup=[
        run("SUCCESS", "2026-09-13T06:32:55Z"),
        run("FAILURE", "2026-09-13T06:06:55Z"),
        run("SUCCESS", "2026-09-13T06:22:30Z"),
        {"__typename": "StatusContext", "context": "ci/legacy", "state": "ERROR", "createdAt": "2026-09-13T06:00:00Z"},
        {"__typename": "StatusContext", "context": "ci/legacy", "state": "SUCCESS",
         "createdAt": "2026-09-13T06:10:00Z"},
    ])
    shep.pr(204, owner="Thurbeen/thurbox", headRefName="tbx/reruns-204",
            statusCheckRollup=[run("FAILURE", "2026-09-13T06:22:30Z"), run("SUCCESS", "2026-09-13T06:06:55Z")])
    shep.pr(205, owner="Thurbeen/thurbox", headRefName="tbx/reruns-205",
            statusCheckRollup=[run("SUCCESS", "2026-09-13T06:06:55Z"), run(None, "2026-09-13T06:22:30Z")])

    lines = q("shepherd", "--topic", ttopic, "--dry-run").out.splitlines()

    def row(n: int) -> str:
        return "\n".join(line for i, line in enumerate(lines) if any(f"pull/{n}" in x for x in lines[max(0, i - 1):i + 1]))

    # An older FAILURE under a newer SUCCESS is a pass, for a CheckRun and a StatusContext alike.
    refute(row(203), "checks-failed")
    expect(row(203), "ready: ")
    # An older SUCCESS under a newer FAILURE is still a failure.
    expect(row(204), "checks-failed")
    # A newer run still in progress is pending, never the older run's verdict.
    expect(row(205), "checks still running")
    refute(row(205), "ready: ")


FENCED = r'''
import json

HEAD = "a" * 40
EARLIER = "b" * 40
PASSED = [
    {"name": "review", "status": "passed", "rounds": 2, "findings": 3, "fixed": 3},
    {"name": "check", "status": "passed", "command": "./scripts/check.sh"},
    {"name": "push", "status": "passed"},
    {"name": "ci", "status": "passed", "conclusion": "success"},
]


def body(head=HEAD, verdict="passed", steps=None):
    doc = {
        "schema": "publish-attestation/v1", "head_sha": head, "base": "main", "base_sha": "c" * 40,
        "repository": "Thurbeen/fleet", "attested_at": "2026-09-15T10:00:00Z", "gate_source": ".publish.yaml",
        "steps": PASSED if steps is None else steps, "verdict": verdict,
    }
    return ("Removes the thing.\n\n<!-- publish-attestation/v1 -->\n```json\n" + json.dumps(doc, indent=2)
            + "\n```\n<!-- /publish-attestation -->\n")


def say(label, ok_why):
    ok, why = ok_why
    print(f"{label}={'yes' if ok else 'no'}: {why}")


say("current", q.attestation_verdict(body(), HEAD))
say("stale", q.attestation_verdict(body(head=EARLIER), HEAD))
say("blocked", q.attestation_verdict(body(verdict="blocked"), HEAD))
say("skipped", q.attestation_verdict(body(steps=PASSED[:2] + [
    {"name": "ci", "status": "skipped", "reason": "pipeline still running"}]), HEAD))
say("notapplicable", q.attestation_verdict(body(steps=PASSED[:3] + [
    {"name": "ci", "status": "not-applicable", "reason": "this repository runs no pipeline"}]), HEAD))
say("noverdict", q.attestation_verdict(body().replace('"verdict": "passed"', '"verdict": ""'), HEAD))
'''


def test_the_fenced_attestation_shape_is_read_under_the_same_rule(tmp_path, monkeypatch):
    """The block is the shape the publish skill's reference specifies, built here
    rather than pasted, so the claim is about the format and not one PR."""
    root = tmp_path / "attest-shapes"
    write(root / "orchestration" / "publish.conf",
          "METHOD=attested\nHOW=run the publish skill\nATTESTATION_MARKER=publish-attestation/v1\n")
    monkeypatch.setenv("FLEET_PUBLISH_ROOT", str(root))
    out = queue_module(FENCED)
    expect(out,
           # Its own shape is read, and its head is this head; an earlier head authorises nothing.
           "current=yes", "stale=no",
           # A blocked verdict is refused, and a skipped step blocks, which is why the field exists.
           "blocked=no", "skipped=no",
           # A not-applicable step does not, and an empty verdict proves nothing.
           "notapplicable=yes", "noverdict=no")


def test_the_older_inline_shape_is_still_read():
    # A pipeline that already emitted the old shape is not a pipeline this change breaks.
    out = queue_module(
        "import json\n"
        "HEAD = 'd' * 40\n"
        "steps = [{'step': s, 'status': 'completed'} for s in ('review', 'test', 'push')]\n"
        "payload = json.dumps({'head_sha': HEAD, 'steps': steps})\n"
        "ok, why = q.attestation_verdict('<!-- fleet-attestation:v1 %s -->\\n' % payload, HEAD)\n"
        "print(f\"inline={'yes' if ok else 'no'}: {why}\")\n"
    )
    expect(out, "inline=yes")
