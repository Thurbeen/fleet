"""Claim 8: `collect` VERIFIES the artifact rather than trusting the worker's word.

A brief that says "open the PR by running `/publish --yes`" states a METHOD,
and a method leaves no trace a checker can read: two tasks were once collected
`shipped` with hand-made pull requests and nothing noticed. So a task declares
an ARTIFACT SHAPE — `attested`, `pr` or `push` — and `collect` goes and looks
for that: the forge for a pull request, git for a commit on the base branch.
The tool rides beside it as `--how`, free text rendered into the brief and
never parsed, which is what keeps fleet agnostic about it.
"""

import shutil

import pytest
import yaml
from queuekit import ok, result

from harness import REPO, expect, git, queue_module, refute, squeezed, write
from harness import run_queue as q

PR = "https://github.com/acme/app/pull/"


def retag(task_yaml, method: str) -> None:
    doc = yaml.safe_load(task_yaml.read_text(encoding="utf-8"))
    doc["publish"]["method"] = method
    write(task_yaml, yaml.safe_dump(doc, sort_keys=False))


def test_collect_verifies_the_artifact_instead_of_trusting_the_worker(first_landed, stubs, queue_dir):
    tasks = queue_dir / first_landed
    stubs.pipeline_pr(1001, "fix/document-the-states")
    stubs.plain_pr(1002, "fix/render-detected-agent",
                   "Rendered detected_agent. Opened with `gh pr create`, which is the thing to catch.")
    result(tasks / "02-document-the-states", "shipped", "Documented the state vocabulary.",
           "https://github.com/Thurbeen/thurbox/pull/1001")
    result(tasks / "03-render-detected-agent", "shipped", "Rendered detected_agent, and opened the PR by hand.",
           "https://github.com/Thurbeen/thurbox/pull/1002")
    # No body file for 1003: the stub answers the way an unreachable API does.
    result(tasks / "04-log-state-changes", "shipped",
           "Logged every state change. The PR body cannot be fetched from here.",
           "https://github.com/Thurbeen/thurbox/pull/1003")

    out = q("collect").out
    expect(
        out,
        # An attested pull request from the task's own branch collects clean, saying so.
        "02-document-the-states", "[publish verified: attested]",
        # One that skipped the pipeline is caught: what the body lacks, that the task
        # was not closed, what would have proved it, and the brief's own words.
        "03-render-detected-agent", "attestation", "NOT CLOSED", "attested", "Its brief said:",
        # An unreachable gh degrades to unknown, and says the check could not run.
        "04-log-state-changes", "could not",
    )
    refute(out, "04-log-state-changes  shipped  https://github.com/Thurbeen/thurbox/pull/1003  [publish verified")

    refute(q("show", f"{first_landed}/03-render-detected-agent").out, "state:       done")
    # No network must not break collect: 04 closes, and says its check did not run.
    expect(q("show", f"{first_landed}/04-log-state-changes").out, "state:       done", "unknown")

    # The lead can close a flagged task deliberately, and the record keeps saying why.
    expect(q("collect", "--allow-unverified").out, "03-render-detected-agent")
    expect(q("show", f"{first_landed}/03-render-detected-agent").out, "missing")


def test_shipped_with_no_pull_request_is_a_claim_and_not_a_skip(topic, queue_dir):
    # `not-applicable` and `stuck` legitimately produce no artifact; `shipped` claims one.
    ok(q("add", topic, "ship-without-proof", "--title", "Ship without proof", "--repo", "/tmp/repo-a",
         "--branch", "fix/ship-without-proof", "--number", "05"))
    result(queue_dir / topic / "05-ship-without-proof", "shipped", "Shipped it, but did not say where.")

    expect(q("collect").out, "05-ship-without-proof", "NOT CLOSED")
    show = q("show", f"{topic}/05-ship-without-proof").out
    refute(show, "state:       done")
    expect(show, "missing")


# --- 8b. the publish METHOD: declared at intake, verified at collect -----------


@pytest.fixture
def push_repo(tmp_path) -> dict:
    """A real repository with a bare `origin`, because "the commit reached the
    base branch" is a fact of git and stubbing git would prove nothing."""
    origin, work = tmp_path / "push-origin", tmp_path / "push-work"
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("init", "-q", "-b", "main", str(work))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=work)
    git("remote", "add", "origin", str(origin), cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    landed = git("rev-parse", "HEAD", cwd=work).strip()
    git("checkout", "-q", "-b", "aside", cwd=work)
    git("commit", "-q", "--allow-empty", "-m", "never pushed", cwd=work)
    aside = git("rev-parse", "HEAD", cwd=work).strip()
    git("checkout", "-q", "main", cwd=work)
    return {"work": str(work), "landed": landed, "aside": aside}


@pytest.fixture
def ptopic(push_repo) -> str:
    topic = ok(q("topic", "add", "publish-methods", "--title", "How a task publishes",
                 "--prompt", "be agnostic about the tool; verify the artifact")).stdout.strip()
    ok(q("add", topic, "pipeline-task", "--title", "Publish through the pipeline",
         "--repo", "/tmp/repo-a", "--branch", "fix/pipeline-task", "--number", "01"))
    ok(q("add", topic, "pr-task", "--title", "Publish as a plain pull request",
         "--repo", "/tmp/repo-a", "--branch", "fix/pr-task", "--number", "02",
         "--publish", "pr", "--how", "run the operator xyz skill"))
    ok(q("add", topic, "push-task", "--title", "Publish straight onto the base branch",
         "--repo", push_repo["work"], "--branch", "fix/push-task", "--base", "main", "--number", "03",
         "--publish", "push"))
    return topic


def test_the_declared_method_reaches_the_brief(ptopic, queue_dir):
    b = squeezed(queue_dir / ptopic / "01-pipeline-task" / "BRIEF.md")
    # No --publish takes the operator's default, and names the tool in their words.
    expect(b, "**Publish.** `attested`", "Here that means: run `/publish --yes`.")

    b = squeezed(queue_dir / ptopic / "02-pr-task" / "BRIEF.md")
    expect(b, "**Publish.** `pr`", "operator xyz skill")
    refute(b, "`attested`")

    b = squeezed(queue_dir / ptopic / "03-push-task" / "BRIEF.md")
    expect(b, "**Publish.** `push`", "commit URL for a")
    refute(b, "Here that means")


def test_each_method_is_proven_where_its_artifact_lives(ptopic, push_repo, stubs, queue_dir):
    # A `pr` task: a pull request from ITS OWN branch — the one claim about a
    # pull request a worker cannot write into its own result.md.
    stubs.plain_pr(1010, "fix/pr-task")
    result(queue_dir / ptopic / "02-pr-task", "shipped",
           "Opened it with the operator's own skill, which fleet knows nothing about.", PR + "1010")
    # A `push` task: git says the commit is on the base branch.
    result(queue_dir / ptopic / "03-push-task", "shipped",
           "Committed onto main and pushed it; there is no pull request.",
           f"https://github.com/acme/app/commit/{push_repo['landed']}")

    expect(q("collect", "--no-reap").out, "[publish verified: pr]", "[publish verified: push]")
    expect(q("show", f"{ptopic}/02-pr-task").out, "publish:     pr", "published:   open")
    # A push task's publish state is terminal, not awaiting a review.
    expect(q("show", f"{ptopic}/03-push-task").out, "published:   pushed")
    expect(q("list", "--topic", ptopic).out, "pushed")


def test_a_claim_that_does_not_hold_up_is_held_open(ptopic, push_repo, stubs, queue_dir):
    ok(q("add", ptopic, "pr-elsewhere", "--title", "Paste somebody else good PR", "--repo", "/tmp/repo-a",
         "--branch", "fix/pr-elsewhere", "--number", "04", "--publish", "pr"))
    stubs.plain_pr(1011, "fix/somebody-elses-work")
    result(queue_dir / ptopic / "04-pr-elsewhere", "shipped",
           "Here is a pull request. It is green. It is not mine.", PR + "1011")

    ok(q("add", ptopic, "stale-attestation", "--title", "Push again after the pipeline ran",
         "--repo", "/tmp/repo-a", "--branch", "fix/stale-attestation", "--number", "05"))
    stubs.pipeline_pr(1012, "fix/stale-attestation", "f" * 40)
    result(queue_dir / ptopic / "05-stale-attestation", "shipped",
           "The pipeline ran, and then I pushed one more commit.", PR + "1012")

    ok(q("add", ptopic, "push-astray", "--title", "Push a commit that never landed",
         "--repo", push_repo["work"], "--branch", "fix/push-astray", "--base", "main", "--number", "06",
         "--publish", "push"))
    result(queue_dir / ptopic / "06-push-astray", "shipped", "Committed it. It is not on main.",
           f"https://github.com/acme/app/commit/{push_repo['aside']}")

    expect(
        q("collect", "--no-reap").out,
        # A pull request from another branch, naming the branch it came from.
        "04-pr-elsewhere", "fix/somebody-elses-work",
        # An attestation for an earlier head.
        "05-stale-attestation",
        # A commit that never reached the base branch, and where it looked.
        "06-push-astray", "origin/main",
    )
    for task in ("04-pr-elsewhere", "05-stale-attestation", "06-push-astray"):
        show = q("show", f"{ptopic}/{task}").out
        refute(show, "state:       done")
        expect(show, "published:   unverified")


def test_a_check_that_cannot_run_is_neither_a_pass_nor_a_failure(ptopic, queue_dir):
    sha = "0123456789abcdef0123456789abcdef01234567"
    ok(q("add", ptopic, "push-elsewhere", "--title", "Push on a machine that is not this one",
         "--repo", "/srv/code/app", "--host", "devbox", "--branch", "fix/push-elsewhere", "--base", "main",
         "--number", "07", "--publish", "push"))
    result(queue_dir / ptopic / "07-push-elsewhere", "shipped", "Pushed it on devbox.",
           f"https://github.com/acme/app/commit/{sha}")
    ok(q("add", ptopic, "push-unreadable", "--title", "Push into a repo this machine has not got",
         "--repo", "/tmp/not-a-checkout", "--branch", "fix/push-unreadable", "--base", "main",
         "--number", "08", "--publish", "push"))
    result(queue_dir / ptopic / "08-push-unreadable", "shipped", "Pushed it somewhere this machine cannot see.",
           f"https://github.com/acme/app/commit/{sha}")

    expect(q("collect", "--no-reap").out, "the base branch is on host devbox", "could not be read")
    for task in ("07-push-elsewhere", "08-push-unreadable"):
        show = q("show", f"{ptopic}/{task}").out
        # It still closes — a check that could not run must not break collect.
        refute(show, "state:       queued")
        expect(show, "published:   unknown")


def test_a_record_from_before_the_publish_block_keeps_the_operators_default(ptopic, stubs, queue_dir):
    # A `pr` reading would pass this pull request; the operator's `attested` holds it.
    ok(q("add", ptopic, "legacy-record", "--title", "A task from before the field existed",
         "--repo", "/tmp/repo-a", "--branch", "fix/legacy-record", "--number", "09"))
    record = queue_dir / ptopic / "09-legacy-record" / "task.yaml"
    doc = yaml.safe_load(record.read_text(encoding="utf-8"))
    doc.pop("publish")
    write(record, yaml.safe_dump(doc, sort_keys=False))
    stubs.plain_pr(1013, "fix/legacy-record")
    result(queue_dir / ptopic / "09-legacy-record", "shipped",
           "Opened it by hand, exactly as the two tasks that started all this did.", PR + "1013")

    expect(q("collect", "--no-reap").out, "09-legacy-record", "attestation")
    show = q("show", f"{ptopic}/09-legacy-record").out
    refute(show, "state:       done")
    expect(show, "publish:     attested")
    expect(q("check").out, "ok")


def test_the_retired_no_mistakes_spelling_still_validates(tmp_path):
    # Records are never rewritten once archived, so a check that refused the old
    # word would turn the gate red for good. Its own queue: the second record is
    # invalid on purpose.
    aliasq = str(tmp_path / "alias-queue")
    ok(q("topic", "add", "renamed", "--prompt", "records from before the method was renamed",
         FLEET_QUEUE_DIR=aliasq))
    ok(q("add", "renamed", "legacy-word", "--title", "Dispatched when the method was no-mistakes",
         "--repo", "/tmp/repo-a", "--branch", "fix/legacy-word", "--publish", "attested", FLEET_QUEUE_DIR=aliasq))
    retag(tmp_path / "alias-queue" / "renamed" / "01-legacy-word" / "task.yaml", "no-mistakes")

    out = q("check", FLEET_QUEUE_DIR=aliasq).out
    expect(out, "queue check: ok")
    refute(out, "is not one of")

    ok(q("add", "renamed", "bogus-word", "--title", "A method nothing defines", "--repo", "/tmp/repo-a",
         "--branch", "fix/bogus-word", "--publish", "attested", FLEET_QUEUE_DIR=aliasq))
    retag(tmp_path / "alias-queue" / "renamed" / "02-bogus-word" / "task.yaml", "carrier-pigeon")

    out = q("check", FLEET_QUEUE_DIR=aliasq).out
    expect(out, "publish method 'carrier-pigeon' is not one of")
    refute(out, "'no-mistakes'")


def test_collect_with_no_gh_on_path_is_unchecked_and_not_failed(tmp_path):
    # An offline laptop and a CI runner with no `gh` still have to collect.
    nothing = tmp_path / "empty-path"
    nothing.mkdir()
    env = {"PATH": str(nothing), "FLEET_QUEUE_DIR": str(tmp_path / "nogh-queue")}
    ok(q("topic", "add", "offline", "--prompt", "collect on a machine with no forge to ask", **env))
    ok(q("add", "offline", "unreachable", "--title", "Publish with nothing to ask about it",
         "--repo", "/tmp/repo-a", "--branch", "fix/unreachable", "--publish", "pr", **env))
    result(tmp_path / "nogh-queue" / "offline" / "01-unreachable", "shipped",
           "Opened it. This machine has no gh.", PR + "1")

    expect(q("collect", "--no-reap", **env).out, "unchecked")
    expect(q("show", "offline/01-unreachable", **env).out, "state:       done", "gh not found")


def test_a_fresh_clone_defaults_to_pr_and_names_no_tool(tmp_path):
    # No `publish:` frontmatter in POLICY.md and no publish.conf: what a fresh
    # clone of a public repo is. The tracked example answers `pr`, and no tool is
    # named anywhere, because naming one would ship somebody else's pipeline.
    bare = tmp_path / "bare-clone"
    (bare / "scripts" / "lib").mkdir(parents=True)
    for lib in (REPO / "scripts" / "lib").glob("*.py"):
        shutil.copy(lib, bare / "scripts" / "lib" / lib.name)
    write(bare / "orchestration" / "queue" / "POLICY.md",
          "# Standing policy for fleet workers\n\nNo frontmatter here, which is what every clone starts with.\n")
    shutil.copy(REPO / "orchestration" / "publish.example.conf", bare / "orchestration" / "publish.example.conf")
    bare_queue = tmp_path / "bare-queue"

    def bq(*args):
        return q(*args, script=bare / "scripts" / "lib" / "queue.py", FLEET_QUEUE_DIR=str(bare_queue),
                 FLEET_PUBLISH_ROOT=str(bare), FLEET_AGENT_ROOT=str(bare))

    ok(bq("topic", "add", "unconfigured", "--prompt", "a clone nobody has configured"))
    ok(bq("add", "unconfigured", "first-task", "--title", "The first task of a fresh clone",
          "--repo", "/tmp/repo-a", "--branch", "fix/first-task"))
    b = squeezed(bare_queue / "unconfigured" / "01-first-task" / "BRIEF.md")
    expect(b, "**Publish.** `pr`")
    refute(b, "Here that means")

    # THE CLAIM THE AGNOSTICISM RESTS ON: the copy this repo SHIPS names no tool.
    shipped = queue_module(
        'conf = q.read_kv_conf("orchestration/publish.example.conf")\n'
        'print("method=" + (conf.get("METHOD") or "none"))\n'
        'print("how=" + (conf.get("HOW") or "none"))\n'
        'print("methods=" + " ".join(sorted(q.PUBLISH_METHODS)))\n'
    )
    expect(shipped, "method=pr", "how=none", "methods=attested none note pr push served")

    # The retired spelling still loads, and leaves every other method alone.
    aliases = queue_module('print("alias=" + str(q.publish_method("no-mistakes")))\n'
                           'print("kept=" + str(q.publish_method("pr")))\n')
    expect(aliases, "alias=attested", "kept=pr")

    # The helper is not the claim; the READERS are. This clone's default is `pr`,
    # so a reader that skipped the alias would downgrade the check here.
    ok(bq("add", "unconfigured", "said-old-word", "--title", "Added with the retired word",
          "--repo", "/tmp/repo-a", "--branch", "fix/said-old-word", "--publish", "no-mistakes", "--number", "02"))
    refute((bare_queue / "unconfigured" / "02-said-old-word" / "task.yaml").read_text(encoding="utf-8"),
           "no-mistakes")
    ok(bq("add", "unconfigured", "recorded-old-word", "--title", "Recorded before the rename",
          "--repo", "/tmp/repo-a", "--branch", "fix/recorded-old-word", "--publish", "attested", "--number", "03"))
    retag(bare_queue / "unconfigured" / "03-recorded-old-word" / "task.yaml", "no-mistakes")
    expect(bq("show", "unconfigured/03-recorded-old-word").out, "publish:     attested")


def test_a_stale_attestation_the_pipeline_caused_itself_says_so(ptopic, stubs, queue_dir):
    # `attested` writes the attestation while it opens the pull request and can
    # then push its own CI fixes on top, which reads at collect time exactly like
    # a worker force-pushing over the pipeline. One is fixed by running the tool
    # again and the other is not. Both are still REFUSED; only the wording differs.
    attested = "a" * 40
    ok(q("add", ptopic, "pipeline-pushed-after", "--title", "Let the pipeline push its own CI fix after it attested",
         "--repo", "/tmp/repo-a", "--branch", "fix/pipeline-pushed-after", "--number", "10"))
    stubs.pipeline_pr(1014, "fix/pipeline-pushed-after", attested)
    stubs.pr_history(1014, f"{attested} chore: publish document - Sync the docs",
                     f"{1014:040d} publish: apply CI fixes")
    result(queue_dir / ptopic / "10-pipeline-pushed-after", "shipped",
           "Ran the pipeline. It attested, opened the PR, and then pushed a CI fix.", PR + "1014")

    ok(q("add", ptopic, "pushed-over-pipeline", "--title", "Push over the pipeline by hand",
         "--repo", "/tmp/repo-a", "--branch", "fix/pushed-over-pipeline", "--number", "11"))
    stubs.pipeline_pr(1015, "fix/pushed-over-pipeline", attested)
    stubs.pr_history(1015, f"{attested} chore: publish document - Sync the docs",
                     f"{1015:040d} fix: one more thing I thought of")
    result(queue_dir / ptopic / "11-pushed-over-pipeline", "shipped",
           "Ran the pipeline, then remembered one more thing and pushed it.", PR + "1015")

    ok(q("add", ptopic, "stale-attestation", "--title", "Push again after the pipeline ran",
         "--repo", "/tmp/repo-a", "--branch", "fix/stale-attestation", "--number", "05"))
    stubs.pipeline_pr(1012, "fix/stale-attestation", "f" * 40)
    result(queue_dir / ptopic / "05-stale-attestation", "shipped",
           "The pipeline ran, and then I pushed one more commit.", PR + "1012")

    expect(
        q("collect", "--no-reap").out,
        "10-pipeline-pushed-after",
        # What moved the head, the commit that did it, and the one thing that fixes it.
        "the pipeline pushed that head itself", "publish: apply CI fixes", "publish command again",
    )
    show = q("show", f"{ptopic}/10-pipeline-pushed-after").out
    refute(show, "state:       done")
    expect(show, "the pipeline pushed that head itself")

    show = q("show", f"{ptopic}/11-pushed-over-pipeline").out
    expect(show, "no longer what would merge")
    refute(show, "the pipeline pushed that head itself")

    # Nor is one whose commits the forge would not enumerate.
    refute(q("show", f"{ptopic}/05-stale-attestation").out, "the pipeline pushed that head itself")
