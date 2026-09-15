"""Claim 16: the tool leaves the lead no reason to work around it.

Hit repeatedly in one orchestration session (2026-09-09), and one wrote a lie
into the records: with `dispatch` all-or-nothing, holding two of five ready
tasks back could only be spelled as a blocker, recorded with the reason
"Operator has not been asked whether to run it at all".

So: a `--brief-file` fills the four standard sections, and one that leaves any
unwritten is refused at `add`, naming them, with nothing created; the check is
structural, so a brief quoting the placeholder still dispatches; a branch no
worktree could be cut for is refused before a task exists; `dispatch` takes
refs; and `block --kind` lists its values in `--help`.
"""

import pytest
from queuekit import PLACEHOLDER, ok

from harness import expect, git, refute, squeezed, write
from harness import run_queue as q

FULL_BRIEF = """\
## What to do

Rewrite the state machine so `idle` means the agent said so.

## Done means

`cargo test` passes and the pull request is open.

## Hard constraints

Do not touch `src/list.rs`; another worker is in it.

## Coordination

`02-document-the-states` reads what you write here.
"""

ODD_BRIEF = """\
Do the thing.

## Background

The reason it matters.

## Hard constraints

None.

## Coordination

None.

## Done means

It is done.
"""

FENCED_BRIEF = """\
Copy this shape:

```markdown
## Done means

not a real heading
```

## Hard constraints

None.

## Coordination

None.

## Done means

The real one.
"""

HALF_BRIEF = """\
## What to do

Rewrite the state machine so `idle` means the agent said so.
"""

QUOTING_BRIEF = f"""\
## What to do

Every section of the scaffold starts as `{PLACEHOLDER}`
and the dispatch precondition used to grep the whole file for that string, so
a brief describing it refused to go out. Compare each section instead.

## Hard constraints

Do not weaken the check. A worker sent a scaffold has nothing to do.

## Coordination

None.

## Done means

This brief, which quotes `{PLACEHOLDER}`, dispatches.
"""

THE_OTHER_THREE = ("Hard constraints", "Coordination", "Done means")


@pytest.fixture
def briefs(tmp_path):
    def brief(name: str, text: str):
        path = tmp_path / f"{name}.md"
        write(path, text)
        return str(path)
    return brief


@pytest.fixture
def etopic(queue_dir, briefs) -> str:
    """A queue of its own: a bare `dispatch` acts on every ready task in it."""
    topic = ok(q("topic", "add", "stop-the-workarounds", "--title", "Stop the lead working around the queue",
                 "--prompt", "three tool defects made the lead hand-repair what the tool should do")).stdout.strip()
    for n, slug, title, text in (("01", "fill-every-section", "Fill every section", FULL_BRIEF),
                                 ("02", "keep-odd-headings", "Keep odd headings", ODD_BRIEF),
                                 ("04", "fenced-body", "Fenced body", FENCED_BRIEF)):
        ok(q("add", topic, slug, "--title", title, "--repo", "/tmp/repo-a", "--branch", f"fix/{slug}",
             "--number", n, "--brief-file", briefs(slug, text)))
    return topic


def add(topic: str, slug: str, number: str, *extra: str, repo: str = "/tmp/repo-a", branch: str | None = None):
    return q("add", topic, slug, "--title", slug.replace("-", " ").capitalize(), "--repo", repo,
             "--branch", branch or f"fix/{slug}", "--number", number, *extra)


def spawns(out: str) -> int:
    return sum("session create" in line for line in out.splitlines())


def test_a_brief_file_with_the_four_headings_fills_the_four_sections(etopic, queue_dir):
    """The whole file used to go into section 0: `## What to do` twice and three
    untouched placeholders, refused by `dispatch`, hand-repaired five times."""
    path = queue_dir / etopic / "01-fill-every-section" / "BRIEF.md"
    raw = path.read_text(encoding="utf-8")
    full = squeezed(path)

    refute(raw, "WRITE THE INSTRUCTIONS HERE")
    assert raw.splitlines().count("## What to do") == 1, raw
    expect(full, "## What to do Rewrite the state machine", "## Hard constraints Do not touch `src/list.rs`",
           "## Done means `cargo test` passes")
    # The skeleton's order, whatever order the file put them in.
    headings = [line for line in raw.splitlines() if line.startswith("## ")]
    assert headings[:4] == ["## What to do", "## Hard constraints", "## Coordination", "## Done means"], headings


def test_an_unrecognised_heading_is_content_kept_where_it_was(etopic, queue_dir):
    """Dropping it loses what the lead wrote; promoting it is the invented headings
    BRIEF_SECTIONS exists to stop."""
    odd = squeezed(queue_dir / etopic / "02-keep-odd-headings" / "BRIEF.md")
    expect(odd, "Do the thing. ## Background The reason it matters.", "## Done means It is done.")
    refute(odd, f"## Done means {PLACEHOLDER}")


def test_a_heading_inside_a_fence_is_quoted_text_and_not_a_section(etopic, queue_dir):
    fenced = squeezed(queue_dir / etopic / "04-fenced-body" / "BRIEF.md")
    expect(fenced, "```markdown ## Done means not a real heading ```", "## Done means The real one.")


def test_a_headingless_body_fills_only_what_to_do_and_is_refused_for_the_rest(etopic, briefs):
    out = add(etopic, "headingless-body", "03", "--brief-file",
              briefs("flat", "Just do it, there is nothing else to say.\n"))
    assert out.code != 0, out.out
    expect(out.out, *THE_OTHER_THREE)
    refute(out.out, "What to do")


# --- 16b. dispatch takes refs, so holding one back needs no fake blocker -------


def test_dispatch_takes_refs_and_a_bare_dispatch_still_sends_the_whole_ready_set(etopic, briefs, queue_dir):
    for n, slug in (("10", "goes-out-alone"), ("11", "goes-out-together"), ("12", "waits-for-real")):
        ok(add(etopic, slug, n, "--brief-file", briefs("full", FULL_BRIEF)))
    ok(q("block", f"{etopic}/12-waits-for-real", "--on", f"{etopic}/10-goes-out-alone",
         "--kind", "semantic-dependency", "--why", "reads the field 10 introduces"))

    out = q("dispatch", f"{etopic}/10-goes-out-alone", "--dry-run").out
    assert spawns(out) == 1, out
    refute(out, "11-goes-out-together")
    # The rest were left queued with nothing recording the choice, and it says so.
    expect(out, "no ref is the norm")

    out = q("dispatch", f"{etopic}/10-goes-out-alone", f"{etopic}/11-goes-out-together", "--dry-run").out
    assert spawns(out) == 2, out

    out = q("dispatch", f"{etopic}/12-waits-for-real", "--dry-run")
    assert out.code != 0, out.out
    expect(out.out, "12-waits-for-real", "reads the field 10 introduces")

    out = q("dispatch", f"{etopic}/99-no-such-task", "--dry-run")
    assert out.code != 0, out.out
    expect(out.out, "no such task")

    # No ref still refuses an unwritten brief before it sends any.
    ok(add(etopic, "written-later", "13"))
    out = q("dispatch", "--dry-run")
    assert out.code != 0, out.out
    expect(out.out, "13-written-later")

    write(queue_dir / etopic / "13-written-later" / "BRIEF.md", "Written now.\n")
    out = q("dispatch", "--dry-run").out
    # 01, 02, 04, 10, 11 and 13: every ready task, in the words that make it the norm.
    assert spawns(out) == 6, out
    expect(out, "no concurrency cap")
    refute(out, "no ref is the norm")


# --- 16c. block --kind lists its own valid values in --help --------------------


def test_block_help_lists_every_kind(etopic, briefs):
    """The set only ever appeared in the refusal after a wrong guess. COLUMNS keeps
    argparse from wrapping a hyphenated choice across two lines."""
    out = q("block", "--help", COLUMNS="200").out
    expect(out, "semantic-dependency", "shared-external-state", "incompatible-migration", "other",
           # Why --clear still names a blocker with --on.
           "more than one")

    for n, slug in (("10", "goes-out-alone"), ("11", "goes-out-together")):
        ok(add(etopic, slug, n, "--brief-file", briefs("full", FULL_BRIEF)))
    out = q("block", f"{etopic}/11-goes-out-together", "--on", f"{etopic}/10-goes-out-alone",
            "--kind", "file-overlap", "--why", "both edit one file")
    assert out.code != 0, out.out
    # Still refused with the guidance, and still says where file overlap belongs.
    expect(out.out, "semantic-dependency", "--touches")


# --- 16d. the intake path refuses at `add`, where the repair is one edit -------
#
# Each is something `add` already knew and `dispatch` was left to discover,
# which cost a round-trip per task. `add` with NO --brief-file is untouched: the
# "scaffold it, I will write it" path, with `dispatch` as its backstop.


def test_a_brief_file_that_leaves_a_section_unwritten_is_refused_at_add(etopic, tmp_path, queue_dir):
    half = tmp_path / "half.md"
    write(half, HALF_BRIEF)
    out = add(etopic, "half-written", "20", "--brief-file", str(half))
    assert out.code != 0, out.out
    expect(out.out, *THE_OTHER_THREE)
    refute(out.out, "What to do")
    # Nothing left behind, so the repair is one edit and one re-run.
    assert not (queue_dir / etopic / "20-half-written").exists()

    write(half, HALF_BRIEF + "\n## Hard constraints\n\nNone.\n\n## Coordination\n\nNone.\n\n"
                             "## Done means\n\n`cargo test` passes.\n")
    ok(add(etopic, "half-written", "20", "--brief-file", str(half)))
    # And it dispatches in one step, with nothing hand-repaired between.
    ok(q("dispatch", f"{etopic}/20-half-written", "--dry-run"))


def test_the_scaffold_path_is_untouched_and_its_backstop_names_the_sections(etopic):
    ok(add(etopic, "scaffold-me", "21"))
    out = q("dispatch", f"{etopic}/21-scaffold-me", "--dry-run")
    assert out.code != 0, out.out
    expect(out.out, "What to do", *THE_OTHER_THREE)


def test_a_brief_that_quotes_the_placeholder_is_a_written_brief(etopic, briefs):
    """The substring check that could not tell the two apart is why a task about the
    scaffold had to have the quotation cut out of its brief before it could go."""
    ok(add(etopic, "quotes-the-scaffold", "22", "--brief-file", briefs("quoting", QUOTING_BRIEF)))
    ok(q("dispatch", f"{etopic}/22-quotes-the-scaffold", "--dry-run"))


def test_a_branch_no_worktree_could_be_cut_for_is_refused_at_add(etopic, tmp_path):
    """`--worktree-branch` only ever CREATES a branch. `--branch main --base main`
    was accepted and then died at spawn, leaving the operator editing task.yaml."""
    out = add(etopic, "branch-is-base", "23", "--base", "main", branch="main")
    assert out.code != 0, out.out
    expect(out.out, "main", "worktree")

    # ANY branch already in a repo this machine can read is the same failure.
    repo = tmp_path / "branch-repo"
    git("init", "-q", "-b", "main", str(repo))
    git("commit", "-q", "--allow-empty", "-m", "base", cwd=repo)
    git("branch", "fix/left-behind", cwd=repo)
    out = add(etopic, "branch-exists", "24", "--base", "main", repo=str(repo), branch="fix/left-behind")
    assert out.code != 0, out.out
    # Named, quoting the failure the spawn would have died with.
    expect(out.out, "fix/left-behind", "already exists")

    # The ordinary case is not made slower or louder.
    ok(add(etopic, "branch-is-new", "25", "--base", "main", repo=str(repo), branch="fix/is-new"))
    # A repo this machine has not got is left to dispatch, which is every --host task.
    ok(add(etopic, "repo-not-here", "26", "--base", "main"))
