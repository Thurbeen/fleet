"""A worker that wrote `stuck` or `failed` and whose change request merged anyway lands.

The worker's verdict is a claim about the moment it stopped; the forge saying
every artifact merged is a fact about main afterwards, and it is the same fact
that lands a `done` task. Two tasks sat `stuck` for a day and for two days
after their pull requests merged — one because its worker would not write an
attestation, one pending a decision somebody then made on the pull request —
holding their topics on the board until a person rewrote their result.md.

Only EVERY artifact merged moves one. Still open, merged in one repository of
two, or no artifact named at all, and the task stays where the worker left it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from queuekit import ok, result, result_artifacts

from harness import expect, refute, write
from harness import run_queue as q

PR = "https://github.com/Thurbeen/thurbox/pull/"
SID = "5b0c7d2e-0000-4000-8000-0000000000bb"


@pytest.fixture
def queue_dir(isolated_env) -> Path:
    return Path(os.environ["FLEET_QUEUE_DIR"])


def doc(queue_dir: Path, ref: str) -> dict:
    return yaml.safe_load((queue_dir / ref / "task.yaml").read_text(encoding="utf-8"))


def topic(slug: str) -> str:
    return ok(q("topic", "add", slug, "--title", slug, "--prompt", "p")).stdout.strip()


def task(top: str, slug: str, n: str, *extra: str) -> str:
    return ok(q("add", top, slug, "--title", slug, "--repo", "/tmp/repo-a", "--branch", f"fix/{slug}",
                "--publish", "pr", "--number", n, *extra, THURBOX_SESSION=None)).stdout.strip()


@pytest.mark.parametrize("outcome", ["stuck", "failed"])
def test_a_merged_artifact_lands_a_task_its_worker_gave_up_on(queue_dir, stubs, outcome):
    top = topic(f"gave-up-{outcome}")
    ref = task(top, "gave-up", "01")
    waiting = task(top, "waits", "02")
    ok(q("block", waiting, "--on", ref, "--kind", "semantic-dependency", "--why", "reads what 01 adds"))
    stubs.plain_pr(51, "fix/gave-up")
    result(queue_dir / ref, outcome, "Opened it; it needs a decision I cannot make.", f"{PR}51")

    ok(q("collect"))
    assert doc(queue_dir, ref)["state"] == outcome
    expect(ok(q("show", waiting)).out, f"({outcome})")

    stubs.pr_state(51, "MERGED")
    out = ok(q("collect")).out
    expect(out, ref, "landed")
    record = doc(queue_dir, ref)
    assert record["state"] == "landed", record
    # The worker's word stays on the record: it is what it said, not what happened.
    assert record["outcome"] == outcome
    progress = (queue_dir / ref / "progress.jsonl").read_text(encoding="utf-8")
    expect(progress, f"the worker said {outcome}; every artifact merged")
    expect(ok(q("show", waiting)).out, "cleared:")


def test_its_topic_archives_beside_an_abandoned_sibling(queue_dir, stubs):
    top = topic("one-left")
    ref = task(top, "stuck-one", "01")
    sibling = task(top, "given-up", "02")
    ok(q("abandon", sibling, "--why", "superseded"))
    stubs.plain_pr(52, "fix/stuck-one")
    result(queue_dir / ref, "stuck", "Waiting on the operator.", f"{PR}52")
    ok(q("collect"))
    refute((queue_dir / top / "topic.yaml").read_text(encoding="utf-8"), "archived:")

    stubs.pr_state(52, "MERGED")
    ok(q("collect"))
    expect((queue_dir / top / "topic.yaml").read_text(encoding="utf-8"), "archived:")


def test_the_origin_is_told_it_landed_once(queue_dir, stubs):
    Path(os.environ["FLEET_RECONCILE_DIR"]).mkdir(parents=True, exist_ok=True)
    write(stubs.root / "sessions" / f"{SID}.json",
          json.dumps({"name": "Asker", "id": SID, "state": "idle", "backend_type": "local:tmux"}) + "\n")
    stubs.composer(SID, "")
    top = topic("told-stuck")
    ref = task(top, "told", "01", "--origin", f"thurbox:{SID}")
    stubs.plain_pr(53, "fix/told")
    result(queue_dir / ref, "stuck", "Could not attest.", f"{PR}53")
    ok(q("collect"))
    stubs.pr_state(53, "MERGED")
    ok(q("collect"))
    ok(q("collect"))
    ok(q("reap"))
    told = [c for c in stubs.calls("thurbox-cli", "session send") if c.split()[3] == SID]
    assert len(told) == 1, told
    expect(told[0], f"fleet: {ref} landed", f"{PR}53")


@pytest.mark.parametrize("state", ["OPEN", "CLOSED"])
def test_a_change_request_that_did_not_merge_leaves_it_stuck(queue_dir, stubs, state):
    top = topic(f"still-{state.lower()}")
    ref = task(top, "still", "01")
    stubs.plain_pr(54, "fix/still")
    result(queue_dir / ref, "stuck", "Opened it.", f"{PR}54")
    ok(q("collect"))
    stubs.pr_state(54, state)
    ok(q("collect"))
    assert doc(queue_dir, ref)["state"] == "stuck"


def test_a_stuck_task_that_named_no_artifact_stays_stuck(queue_dir, stubs):
    top = topic("named-nothing")
    ref = task(top, "nothing", "01")
    result(queue_dir / ref, "stuck", "Could not start.")
    ok(q("collect"))
    ok(q("collect"))
    assert doc(queue_dir, ref)["state"] == "stuck"


def test_merged_in_one_repository_of_two_stays_stuck(queue_dir, stubs):
    top = topic("half-merged")
    ref = task(top, "spans", "01", "--add-repo", "/tmp/repo-b")
    stubs.plain_pr(701, "fix/spans")
    stubs.plain_pr(702, "fix/spans")
    result_artifacts(queue_dir / ref, "stuck", "One of two needs a decision.", {
        "/tmp/repo-a": "https://github.com/acme/first/pull/701",
        "/tmp/repo-b": "https://github.com/acme/second/pull/702",
    })
    ok(q("collect"))
    stubs.pr_state(701, "MERGED")
    ok(q("collect"))
    assert doc(queue_dir, ref)["state"] == "stuck"

    stubs.pr_state(702, "MERGED")
    ok(q("collect"))
    assert doc(queue_dir, ref)["state"] == "landed"


def test_a_repository_the_stuck_result_names_nothing_for_holds_it(queue_dir, stubs):
    top = topic("half-named")
    ref = task(top, "spans", "01", "--add-repo", "/tmp/repo-b")
    stubs.plain_pr(703, "fix/spans")
    stubs.pr_state(703, "MERGED")
    result_artifacts(queue_dir / ref, "stuck", "Only got one open.", {
        "/tmp/repo-a": "https://github.com/acme/first/pull/703",
    })
    ok(q("collect"))
    ok(q("collect"))
    assert doc(queue_dir, ref)["state"] == "stuck"
