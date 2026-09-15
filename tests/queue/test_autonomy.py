"""§22: a finished task closes itself, without `--allow-unverified`.

Fifteen records in an operator's own queue were closed by `collect
--allow-unverified`, or were held by it still. Each is copied: the fields
`collect` reads, its result's frontmatter, and what the forge really answered for
its artifact. They are four kinds of the same failure:

  ten are a NOTE — a review or a comment — declared `push` because no other shape
      existed, so no commit URL could ever have proved them
  three are a pull request the forge reports MERGED whose attestation had gone
      stale, held open by a verdict that ran before landing could
  one worked on an EXISTING pull request from a fork, whose head is not the
      scaffolding branch fleet gave the task
  one is a document off the forge, and one a survey in a repository with no
      remote: nothing fleet can ask anybody about

Every record is driven TWICE. As recorded, the three merged ones close by
themselves and the rest stay held, because what they declare is still unproven
and closing them would take the worker's own URL as proof of itself. As `add`
now records the same task — `note` with its `--target`, `pr` with its `--target`,
`none` for what has nothing to check — each closes by itself.

`gh` serves the recorded answers, and `glab` is a TRIPWIRE: nothing about
github.com may be asked through GitLab's CLI. `GITLAB_HOST` is set so that
building the registry asks `glab` nothing either.

§23: a stuck task whose worker later shipped is read again. A worker wrote
`outcome: stuck` when its shell died mid-pipeline; the pull request had merged,
and the worker later rewrote result.md as `shipped`. `collect` skipped every
concluded task, so it never read that file again and `reap` kept the session
forever. A worker's `stuck` or `failed` is its own verdict, so the evidence that
overturns it is the same worker's file saying something else — only a CHANGED
outcome, and a `shipped` still has to pass the publish check.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from kit_forges import tripwire
from kit_records import FIFTEEN, GH, TB, Records, attestation, seed
from queuekit import ok, result

from harness import Run, expect, refute, squeezed
from harness import run_queue as q

MERGED = ("11-emoji-session-glyphs", "12-cut-defensive-prose", "13-reconciler-loop")
HELD_AS_RECORDED = ("01-review-1107", "02-review-1108", "03-review-1117", "04-review-1116", "05-review-1115",
                    "06-review-1114", "07-win-clipboard-test", "08-deb-tmux-race", "09-explain-thurbox",
                    "10-prove-and-propose", "14-green-the-pr", "15-survey-existing")
NOTES = ("01-review-1107", "02-review-1108", "03-review-1117", "04-review-1116", "05-review-1115",
         "06-review-1114", "07-win-clipboard-test", "08-deb-tmux-race", "10-prove-and-propose")
FLEET = "https://github.com/Thurbeen/fleet/pull/"


class Autonomy:
    def __init__(self, queue: Path, records: Records):
        self.queue, self.records = queue, records

    def q(self, *args: str) -> Run:
        return q(*args, FLEET_QUEUE_DIR=str(self.queue), GITLAB_HOST="gitlab.invalid")

    def show(self, ref: str) -> str:
        return self.q("show", ref).out

    def publish_field(self, ref: str, field: str) -> str:
        doc = yaml.safe_load((self.queue / ref / "task.yaml").read_text(encoding="utf-8"))
        return str((doc.get("publish") or {}).get(field) or "")


@pytest.fixture
def aq(tmp_path, stubs):
    stubs.tool("gh", GH)
    stubs.tool("glab", tripwire("glab: nothing about github.com may be asked through glab"))
    records = Records(stubs.root / "records")
    seed(records)
    yield Autonomy(tmp_path / "queue-autonomy", records)
    assert stubs.calls("glab") == [], "something about github.com was asked through glab"


@pytest.fixture
def fifteen(aq):
    """Both topics, and the one `collect` that reads them all."""
    rtopic = ok(aq.q("topic", "add", "as-recorded", "--title", "The fifteen, as their records say",
                     "--prompt", "fleet should be more autonomous on closing tasks")).stdout.strip()
    itopic = ok(aq.q("topic", "add", "as-added", "--title", "The fifteen, as add now records them",
                     "--prompt", "fleet should be more autonomous on closing tasks")).stdout.strip()
    for n, slug, method, how, branch, artifact, again, target in FIFTEEN:
        ok(aq.q("add", rtopic, slug, "--repo", "/tmp/repo-records", "--branch", branch, "--number", n))
        record = aq.queue / rtopic / f"{n}-{slug}" / "task.yaml"
        doc = yaml.safe_load(record.read_text(encoding="utf-8"))
        if method:
            doc["publish"] = {"method": method, "how": how or None}
        else:
            doc.pop("publish")
        record.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8", newline="\n")
        dirs = [aq.queue / rtopic / f"{n}-{slug}"]
        if again != "-":
            extra = ["--publish", again] + (["--target", target] if target else [])
            ok(aq.q("add", itopic, slug, "--repo", "/tmp/repo-records", "--branch", branch, "--number", n, *extra))
            dirs.append(aq.queue / itopic / f"{n}-{slug}")
        for d in dirs:
            result(d, "shipped", "Shipped it.", artifact or None)
    return rtopic, itopic, aq.q("collect").out


def test_as_recorded_merged_pull_requests_close_and_the_rest_stay_held(aq, fifteen):
    rtopic, _, out = fifteen
    for t in MERGED:
        # A merged pull request closes its task, stale attestation and all — and the
        # stale attestation is noted on the publish block, not held on.
        expect(aq.show(f"{rtopic}/{t}"), "state:       landed")
        expect(aq.publish_field(f"{rtopic}/{t}", "attestation"), "no longer what would merge")
    refute(out, "closed by --allow-unverified")

    # The other twelve declare something still not true of them; the refusal names
    # the shape that WOULD prove each.
    for t in HELD_AS_RECORDED:
        expect(aq.show(f"{rtopic}/{t}"), "published:   unverified")
    expect(aq.show(f"{rtopic}/01-review-1107"), "--publish note --target")
    expect(aq.show(f"{rtopic}/14-green-the-pr"), "add --target")


def test_as_add_now_records_them_every_one_closes_itself(aq, fifteen):
    _, itopic, out = fifteen
    for t in NOTES:
        s = aq.show(f"{itopic}/{t}")
        # Verified by the forge, and landed at once: a note has no change request of its own.
        expect(s, "checked:     passed", "state:       landed")
    expect(out, "[publish verified: note]")

    for t in ("09-explain-thurbox", "15-survey-existing"):
        # Closed with nothing claimed as checked.
        expect(aq.show(f"{itopic}/{t}"), "checked:     skipped", "state:       landed")
    expect(aq.show(f"{itopic}/09-explain-thurbox"), "artifact:    http://docs.example.test:35547/review/")
    expect(out, "[publish not checked: none]")

    # The fork's pull request, added with its target, is verified and waits for its merge.
    expect(aq.show(f"{itopic}/14-green-the-pr"), "checked:     passed", "state:       done")

    refute("\n".join(line for line in out.splitlines() if "NOT CLOSED" in line), f"{itopic}/")
    refute(out, "closed by --allow-unverified")


def test_nothing_lets_a_workers_url_prove_itself(aq):
    ctopic = ok(aq.q("topic", "add", "no-trust-manufactured",
                     "--prompt", "a note must be ours, and on the target")).stdout.strip()

    def claimed(n: str, slug: str, branch: str, artifact: str, *publish: str) -> None:
        ok(aq.q("add", ctopic, slug, "--branch", branch, "--repo", "/tmp/repo-records", "--number", n, *publish))
        result(aq.queue / ctopic / f"{n}-{slug}", "shipped", "Done.", artifact)

    note_on_1107 = ("--publish", "note", "--target", f"{TB}1107")
    claimed("01", "somebody-elses", "review/a", f"{TB}1107#pullrequestreview-7000001", *note_on_1107)
    claimed("02", "ours-elsewhere", "review/b", f"{TB}1108#pullrequestreview-5187155662", *note_on_1107)
    claimed("03", "no-such-note", "review/c", f"{TB}1107#pullrequestreview-7000002", *note_on_1107)
    claimed("04", "stale-open", "feat/stale-open", f"{FLEET}60", "--publish", "attested")
    claimed("05", "merged-elsewhere", "feat/mine", f"{FLEET}61", "--publish", "attested")
    aq.q("collect")

    # A pasted link to somebody else's review, naming who really wrote it.
    expect(aq.show(f"{ctopic}/01-somebody-elses"), "published:   unverified", "stranger")
    # Our own review on a pull request the task does not target, naming the target.
    expect(aq.show(f"{ctopic}/02-ours-elsewhere"), "published:   unverified", "target")
    # A note the forge cannot find is could-not-check, never passed or missing.
    expect(aq.show(f"{ctopic}/03-no-such-note"), "checked:     unknown")
    # A stale attestation on a pull request still OPEN is still held.
    expect(aq.show(f"{ctopic}/04-stale-open"), "published:   unverified")
    # A merged pull request from another branch still proves nothing.
    expect(aq.show(f"{ctopic}/05-merged-elsewhere"), "published:   unverified")

    user = aq.records.root / "api" / "user.json"
    user.rename(user.with_suffix(".away"))
    claimed("06", "who-am-i", "review/d", f"{TB}1107#pullrequestreview-5186731434", *note_on_1107)
    aq.q("collect")
    user.with_suffix(".away").rename(user)
    # A forge that will not say who fleet runs as is could-not-check.
    expect(aq.show(f"{ctopic}/06-who-am-i"), "checked:     unknown")

    # And --allow-unverified is still the deliberate escape hatch.
    expect(aq.q("collect", "--allow-unverified").out, "closed by --allow-unverified")


def test_intake_no_longer_invites_the_workaround(aq, fifteen):
    _, itopic, _ = fifteen

    def refused(*args: str) -> str:
        done = aq.q("add", itopic, *args)
        assert done.code != 0, done.out
        return done.out

    expect(refused("untargeted", "--repo", "/tmp/repo-records", "--branch", "review/e", "--publish", "note"),
           "--target")
    # A push task with a change request to work on is told what fits instead.
    expect(refused("push-at-a-pr", "--repo", "/tmp/repo-records", "--branch", "review/f", "--publish", "push",
                   "--target", f"{TB}1107"), "--publish note")
    expect(refused("pr-at-an-issue", "--repo", "/tmp/repo-records", "--branch", "review/g", "--publish", "pr",
                   "--target", "https://github.com/Thurbeen/thurbox/issues/12"), "issue")
    expect(refused("nonsense", "--repo", "/tmp/repo-records", "--branch", "review/h", "--publish", "note",
                   "--target", "the one from yesterday"), "target")

    expect(squeezed(aq.queue / itopic / "01-review-1107" / "BRIEF.md"),
           "**Publish.** `note`", f"**Target.** {TB}1107")
    expect(squeezed(aq.queue / itopic / "09-explain-thurbox" / "BRIEF.md"), "**Publish.** `none`")

    expect(aq.q("add", "--help").out, "note", "none", "--target")
    expect(aq.q("check").out, "queue check: ok")


# --- 23. a stuck task whose worker later shipped is read again ---------------------


def test_a_stuck_task_whose_worker_later_shipped_is_read_again(aq, stubs):
    head = "1126" * 10
    aq.records.pr("Thurbeen/thurbox", 1126, "MERGED", "fix/1119", head,
                  body=attestation(head, steps=("push",)).replace("Shipped it.", "Fixes #1119."))
    stopic = ok(aq.q("topic", "add", "stuck-then-shipped",
                     "--prompt", "a stuck task whose PR later merged can never close")).stdout.strip()
    for n, slug, branch in (("01", "fix-1119", "fix/1119"), ("02", "still-unproven", "feat/stale-open"),
                            ("03", "failed-then-moot", "fix/moot")):
        ok(aq.q("add", stopic, slug, "--repo", "/tmp/repo-records", "--branch", branch, "--number", n,
                "--publish", "attested"))
        sid = f"23232323-0000-0000-0000-0000000000{n}"
        ok(aq.q("attach", f"{stopic}/{n}-{slug}", sid))
        stubs.session_is(sid, "idle")

    def verdict(task: str, outcome: str, note: str, artifact: str | None = None) -> None:
        result(aq.queue / stopic / task, outcome, note, artifact)

    def deletions() -> str:
        return "\n".join(stubs.calls("thurbox-cli", "session delete"))

    verdict("01-fix-1119", "stuck", "My shell died mid-pipeline.")
    verdict("02-still-unproven", "stuck", "Could not get the pipeline green.")
    verdict("03-failed-then-moot", "failed", "The reproduction would not build.")
    aq.q("collect")
    expect(aq.show(f"{stopic}/01-fix-1119"), "state:       stuck")
    refute(deletions(), "23232323")  # its session is kept as the evidence

    # The same verdict, written again, is nothing new: no second conclusion.
    verdict("01-fix-1119", "stuck", "Still stuck, and saying so twice.")
    refute(aq.q("collect").out, f"{stopic}/01-fix-1119  stuck")

    # The worker recovers and says what really happened.
    verdict("01-fix-1119", "shipped", "It had merged after all.", f"{TB}1126")
    verdict("02-still-unproven", "shipped", "Shipped, I think.", f"{FLEET}60")
    verdict("03-failed-then-moot", "not-applicable", "Upstream already fixed it.")
    out = aq.q("collect").out

    expect(out, "[publish verified: attested]")
    # It lands like any other shipped task, and reap releases the session it used to keep.
    expect(aq.show(f"{stopic}/01-fix-1119"), "state:       landed")
    expect(deletions(), "session delete 23232323-0000-0000-0000-000000000001 --force")

    # Rewritten as shipped with nothing proving it: stays stuck, told the claim is unproven.
    expect(aq.show(f"{stopic}/02-still-unproven"), "state:       stuck", "published:   unverified")
    refute(deletions(), "23232323-0000-0000-0000-000000000002")

    expect(aq.show(f"{stopic}/03-failed-then-moot"), "state:       landed")
