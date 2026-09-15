"""§20: a wait on a CONDITION — recordable, visible, and cleared only by hand.

A task was ready by every record fleet keeps and unrunnable in fact: its brief's
first instruction read a cloud account whose CLI was not authenticated. `block`
took only `--on <ref>`, so there was nothing to write down; `plan` listed the task
as ready and the reconciler typed its one line into the lead's terminal telling
it to dispatch.

A condition can be RECORDED, with a kind from its own closed set and a reason. It
shows up everywhere work in flight is reported and never in `ready`. NOTHING
CLEARS IT BUT A HAND. And the line the reconciler types does not count it. The
task-to-task form is asserted in the same queue, because it shares the code.
"""

from __future__ import annotations

import json

import pytest
import yaml
from queuekit import ok, result

from harness import REPO, PYTHON, expect, refute, run, write
from harness import run_fleet as fleet
from harness import run_queue as q

AZ = "az is authenticated for the billing tenant"


@pytest.fixture
def xtopic(tmp_path, queue_dir) -> str:
    repo = tmp_path / "repo-conditions"
    repo.mkdir()
    topic = ok(q("topic", "add", "vending-machine-egress", "--title", "Resume the vending machine egress",
                 "--prompt", "reconcile the VM identities and resume egress")).stdout.strip()
    for n, slug, title in (("01", "vm-identity", "Reconcile the VM identities against Azure"),
                           ("02", "document-the-tables", "Write the identity tables down"),
                           ("03", "terraform-the-vms", "Terraform the three VMs"),
                           ("04", "after-a-dead-end", "A task waiting on one that never lands"),
                           ("05", "the-dead-end", "The task that gets abandoned")):
        ok(q("add", topic, slug, "--title", title, "--repo", str(repo), "--branch", f"fix/{slug}", "--number", n))
    return topic


def refused(run_) -> str:
    assert run_.code != 0, run_.out
    refute(run_.out, "Traceback")
    return run_.out


def test_a_new_form_is_not_a_new_way_to_spell_the_old_lie(xtopic):
    task = f"{xtopic}/01-vm-identity"
    out = refused(q("block", task, "--condition", AZ))
    # No kind and no reason: the condition kinds, not the task ones, and the only release.
    expect(out, "--kind", "missing-credential", "--clear")

    # An unset shell variable is the ordinary way a blank arrives: argparse is
    # satisfied, and a blank condition is still a wait nobody named.
    for blank in ("", "   "):
        expect(refused(q("block", task, "--condition", blank, "--kind", "missing-credential",
                         "--why", "az is not authenticated")), "--condition")
        refused(q("block", task, "--clear", "--condition", blank))

    # File overlap is not a condition kind either, and the refusal still points at --touches.
    expect(refused(q("block", task, "--condition", "az is authenticated", "--kind", "file-overlap",
                     "--why", "both edit main.tf")), "--kind", "--touches")
    # The two closed sets stay two, in both directions.
    expect(refused(q("block", task, "--condition", "az is authenticated", "--kind", "semantic-dependency",
                     "--why", "reads Azure")), "missing-credential")
    expect(refused(q("block", f"{xtopic}/03-terraform-the-vms", "--on", task, "--kind", "missing-credential",
                     "--why", "az is not authenticated")), "semantic-dependency")
    # A blocker names a task or a condition, never both.
    expect(refused(q("block", task, "--on", f"{xtopic}/02-document-the-tables", "--condition",
                     "az is authenticated", "--kind", "other", "--why", "both")), "not allowed with argument")


@pytest.fixture
def held(xtopic, queue_dir) -> str:
    out = ok(q("block", f"{xtopic}/01-vm-identity", "--condition", AZ, "--kind", "missing-credential",
               "--why", "the brief's first instruction reads Azure and az account show fails")).out
    # Recording one says who releases it, since nothing else will.
    expect(out, "--clear")
    ok(q("block", f"{xtopic}/03-terraform-the-vms", "--on", f"{xtopic}/02-document-the-tables",
         "--kind", "semantic-dependency", "--why", "terraforms the identities 02 writes down"))
    ok(q("block", f"{xtopic}/04-after-a-dead-end", "--on", f"{xtopic}/05-the-dead-end",
         "--kind", "semantic-dependency", "--why", "consumes what 05 was going to add"))
    record = queue_dir / xtopic / "05-the-dead-end" / "task.yaml"
    doc = yaml.safe_load(record.read_text(encoding="utf-8"))
    doc["state"] = "abandoned"
    write(record, yaml.safe_dump(doc, sort_keys=False, default_flow_style=False))
    return xtopic


def test_a_condition_shows_up_everywhere_and_never_in_ready(held):
    plan = q("plan").out
    expect(plan, "01-vm-identity", "waiting: 3", "ready: 1", AZ, "az account show fails", "only `block --clear`")
    refute(plan, f"on {AZ}")
    assert json.loads(q("plan", "--json").stdout)["ready"] == [f"{held}/02-document-the-tables"]

    out = q("list").out
    expect(out, "01-vm-identity                     waiting", AZ)
    expect(q("show", f"{held}/01-vm-identity").out, AZ, "missing-credential")

    expect(fleet("status").out, AZ)
    # The machine-readable reading calls it a wait on something outside, never cleared.
    expect(fleet("status", "--json").out, '"status": "outside"', '"cleared": false')
    expect(q("check").out, "ok")

    # The existing form, unchanged, in the same queue.
    expect(plan, "UNCLEARABLE", "which is abandoned")


def test_nothing_but_a_hand_clears_a_condition(held, stubs, queue_dir, tmp_path):
    """`collect` and `reap` move tasks without being told which, and they run here
    with the condition standing. One that expired because some other task landed
    would put back exactly the silence this is about."""
    sid = "44444444-4444-4444-4444-444444444444"
    stubs.session_is(sid, "idle")
    ok(q("attach", f"{held}/02-document-the-tables", sid))
    stubs.pipeline_pr(777, "fix/document-the-tables")
    result(queue_dir / held / "02-document-the-tables", "shipped", "Wrote the identity tables down.",
           "https://github.com/Thurbeen/fleet/pull/777")
    expect(q("collect").out, "shipped")
    stubs.pr_state(777, "MERGED")
    expect(q("reap").out, "landed")

    plan = q("plan").out
    # The task blocker cleared on the LAND; the condition did not, so ready grew by one.
    expect(plan, "03-terraform-the-vms", AZ, "ready: 1")
    refute(plan, f"    {held}/01-vm-identity  ")

    # 20a. THE INCIDENT, as the case it came from: one task ready, one held by a
    # condition, and the loop says ONE. `notify_lead` reads `plan --json`'s ready set
    # and nothing else, but the line is the claim that was false, so it is asserted.
    write(stubs.root / "sessions" / "lead-1.json", '{"id":"lead-1","name":"Gate Control","state":"idle"}\n')
    log = run([*PYTHON, str(REPO / "scripts" / "lib" / "notify_lead.py"), "--state-dir", str(tmp_path / "notify")],
              stdin=q("plan", "--json").stdout, FLEET_LEAD_SESSION="Gate Control").out
    woke = "\n".join(stubs.calls("thurbox-cli", "session send"))
    expect(log, "1 task(s) ready")
    expect(woke, "03-terraform-the-vms", "1 task(s) ready and nothing will dispatch")
    refute(woke, "01-vm-identity")
    (stubs.root / "sessions" / "lead-1.json").unlink()

    # 20b. And a hand is what releases it — naming the condition it holds.
    out = refused(q("block", f"{held}/01-vm-identity", "--clear", "--condition", "some other condition"))
    expect(out, "records no condition", AZ)
    expect(ok(q("block", f"{held}/01-vm-identity", "--clear", "--condition", AZ)).out, "1 blocker(s) cleared")

    plan = q("plan").out
    expect(plan, "01-vm-identity", "ready: 2")
    refute(plan, AZ)
