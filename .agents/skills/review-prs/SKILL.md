---
name: review-prs
description: Stand up and drive a maintainer's review session over a repository's open change requests — find what is unreviewed, wait for CI, read the diff against the repo's own house rules, post approve / request-changes / comment to the forge, squash-merge what is genuinely clean, and reconcile the issue tracker behind it — what the merge actually closed, what it only narrowed, what nobody linked. Covers the head-SHA marker that stops re-reviewing the same commit, the skip rules for drafts and bots, verifying a claim on real hardware before approving it, the merge gate, and never closing an issue on a guess. Use when asked to review open PRs or MRs, to review a repository's pull requests continuously, to tidy up or close out the issues after a merge, or when invoked as /review-prs.
user-invocable: true
allowed-tools: Read, Bash, Glob, Grep
---

## review-prs

The maintainer's side of review: somebody else's change request, arriving on
a repository you own, needing a verdict. It runs on a cadence, because a
change request that waits a day for a first opinion is the expensive kind of
waiting. The distinguishing property is **inbound and recurring**: bugs in
the diff you are writing is `/code-review`, your own branch through a gate is
the `publish` skill, and a guided document a human annotates is `thurview`.

## The loop

1. Confirm the forge CLI is on `PATH`, and that you are in the review
   worktree on its own branch (§1).
2. List what is open; skip drafts, bots, and any head SHA you already
   reviewed (§2).
3. For each one left: wait for CI to conclude — leave it if anything is
   pending (§3).
4. Read the diff, the body, and the repo's own rules for the paths touched
   (§4).
5. Verify the load-bearing claims, on real hardware where that is what it
   takes (§5).
6. Post approve / request-changes / comment, opening with the next step (§6).
7. Merge the approved ones that clear every gate — squash only (§7).
8. Reconcile the tracker against what the forge actually closed (§8).
9. Report one line per change request and per issue you touched (§9).

### Both forges

A change request is a pull request on GitHub and a merge request on GitLab;
`scripts/lib/forge.py` is the seam that keeps fleet from assuming which, and a
self-hosted GitLab is the ordinary case. `-R https://<host>/<group>/<project>`
points `glab` at an instance that is not gitlab.com. Identify a repository by
**host plus path** (`github.com/owner/repo`), because a bare `owner/repo`
names two repositories once two forges are in play.

| Ask | GitHub | GitLab |
|---|---|---|
| what is open | `gh pr list --repo <owner/repo> --state open --json number,title,author,isDraft,headRefOid,reviews` | `glab mr list -R <project url> -F json` |
| did CI conclude | `gh pr checks <n> --repo <owner/repo>` | `glab ci status -R <project url> -b <branch>` |
| the change | `gh pr view <n> …` / `gh pr diff <n> …` | `glab mr view <n> -R <project url> -F json` / `glab mr diff <n> -R <project url>` |
| the verdict | `gh pr review <n> --approve / --request-changes / --comment` | `glab mr approve <n>` / `glab mr note <n> -m …` |

## 1. Give it its own session, and its own worktree

```bash
thurbox-cli session create \
  --name "$(uv run fleet session-name review 'Review open change requests on <project>')" \
  --repo-path <the repo> --worktree-branch review-prs --base-branch main \
  --on-existing adopt --parent <the lead's uuid> --json
```

**The name is rendered, never typed**: which mark a session wears is a
setting, and `uv run fleet session-name --help` owns why and what it refuses
(a title thurbox would reject, or one the 64-byte cap would cut — take the
`Try this title:` line it offers). Run the substitution from the control-plane
checkout. `$(...)` swallows its exit code, so a `--name ''` refusal from
thurbox means read the stderr above it.

**`<project>` is the bare project name — the one place this skill does NOT
identify a repository by host plus path.** A session name becomes a path
segment in thurbox, so `github.com/owner/repo` in that title spawns nothing.
The mark and the words before `<project>` cost 36 of the 64 bytes, so a
project name over 28 characters is refused (33 with `GLYPHS=off`); shorten
the TITLE then (`Review requests on <project>`), never the project, which is
what tells two reviewers apart. The full identity is on `--repo-path`.

**Its own worktree is not optional.** A reviewer checks out other people's
head commits to try things, and doing that in the main checkout leaves the
operator's tree on a stranger's branch. Three rules the session lives under:
never `cd` to the main checkout; never leave the worktree on a branch other
than its own; never use bare `git stash`, whose stack is shared with everyone
else in the repository.

`adopt` because this recurs; **read `created` before you send anything** —
`false` means the reviewer is already mid-pass. **`adopt` matches on the
NAME**, so a reviewer running under another name (created before the mark
existed, or under another glyph setting) is not the one it finds, and the
spawn tries to make a SECOND reviewer, which asks for the `review-prs`
worktree the first one holds, and git refuses. Rename the old one BEFORE you
spawn; it keeps its conversation and the worktree:

```bash
thurbox-cli session rename '<its current name>' \
  "$(uv run fleet session-name review 'Review open change requests on <project>')"
```

`--on-existing replace` matches the same name `adopt` does and is not the way
out. A hand-spawned session asks its agent's trust question, which
`uv run fleet session-trust <uuid>` answers (`thurbox-session` §1b).

**A spawned session does not inherit your interactive shell's PATH.** Check
`command -v gh` (or `glab`) first and put its directory on `PATH`; the failure
otherwise looks like "no open pull requests".

Then send one line pointing at this file, and set the cadence. In Claude Code
that is `/loop`:

```text
/loop 15m Review open change requests on <owner/repo> — follow .agents/skills/review-prs/SKILL.md
```

15 minutes fits CI: shorter and most ticks find a run still going, much
longer and a green change request sits.

## 2. Find work — and know what "already handled" means

Skip, in this order: **drafts** (the author is not asking yet), **bot
authors** — Renovate, Dependabot, any `is_bot` (a dependency bump's verdict
is its CI run), and **anything whose CURRENT head SHA already carries your
review**.

That last rule is why the cadence is cheap, and it is keyed on the head SHA:
a review is about the code it saw. When the author pushes, the SHA moves and
the change request comes back into the queue by itself. Keying on the number
would review each one once and then go blind to every revision.

## 3. Wait for CI. Always

A review posted before the run finishes is a review of half the evidence and
has to be retracted when a check goes red. If anything is still pending,
leave it for the next tick and say "checks still running" in the tick report.
Checks that are `SKIPPED` are concluded; checks that never started are not.

## 4. Read the change, and read the house rules with it

The diff alone does not tell you whether a change is acceptable *here*. Read
what the repository says about itself **for the paths this change touches**:
its own review rules, its `CLAUDE.md` / `AGENTS.md` / `CONTRIBUTING.md`, and
any convention the touched subsystem documents in its header. Checking
something else is noise.

**Verify load-bearing claims rather than believing them.** "This now costs
two subprocesses instead of nine" is testable in a minute, and finding the
one place a claim does not hold is worth more than ten style notes. **Prefer
one real defect to a list of nits**: a review that opens with four naming
preferences buries the bug, and the author reads the first two.

## 5. When a verdict needs hardware you do not have

A Windows-only path, a second forge, another architecture: reach for the real
thing rather than approving on the author's word — a host the operator has
told you about, driven as `thurbox-session` §1a describes, or the repo's own
CI where the run covers it. **Do the check first and post one review carrying
its result**: two reviews on one SHA read as indecision. If neither is
available, say so in the review — the platform you could not reach and what
you would have run. An honest gap is fine to post; a silent one is not.

## 6. Post it

Whatever standing writing rules your agent loads govern every word and
outrank this section. What review itself requires where those are silent:
**open with the next step** (who must act, in the first line); **one point
per comment, anchored to its line**; **mark non-blocking as non-blocking**,
so `request-changes` means one thing; **three to five lines**.

**When a review is genuinely uncertain, post nothing and say so in the tick
report.** An unreviewed change request is a known state; a confidently wrong
approval is the one failure here that costs more than doing nothing.

## 7. Merge what is clean

The operator's ask to review a named repository is the authorisation to merge
there — not `orchestration/auto-merge.conf`, which is the list for the
UNATTENDED path (`fleet queue shepherd`). **Squash is the only method fleet
merges by**, so the title becomes the commit; a project that forbids squash
(GitLab's `squash_option: never`) is one you report and leave.

Merge only when every one of these holds — they are `shepherd`'s gates, and
there is no reason for a second, weaker set:

| Gate | Why it is not optional |
|---|---|
| you just approved it, on this head SHA | merging something you did not review is not review |
| every check concluded and passed | a pending check is not a passing one |
| the forge reports it mergeable | a conflicting change needs its author |
| the head branch is **in the repository**, not a fork | the one claim a stranger cannot write for themselves |
| whoever opened it can push there | anyone with read access can open one between two existing branches |
| an attestation naming the current head, where the repo expects one | a verdict is about the code it saw |

**Never merge on a tick where CI was still running when you looked.** Re-read
the checks immediately before merging: a run can go red between reading the
diff and pressing the button.

## 8. Reconcile the tracker

A merge changes the issue tracker, and the forge does only the part it was
asked for in exactly the syntax it wanted. **Scope: the issues the change
requests in front of you touch** — the ones a body names, and the ones you can
see a merged change fixed. Not a triage sweep: on a public repository a
reviewer forming opinions about strangers' unrelated reports is a way to be
wrong in public. An issue comment is writing you publish, so §6's first
paragraph governs it.

| Ask | GitHub | GitLab |
|---|---|---|
| what the forge actually linked | `gh pr view <n> --repo <owner/repo> --json closingIssuesReferences` | `glab api projects/:fullpath/merge_requests/<n>/closes_issues` |
| the issue's state | `gh issue view <n> --repo <owner/repo> --json state,stateReason,title` | `glab issue view <n> -R <project url> -F json` |
| say something on it | `gh issue comment <n> --repo <owner/repo> -b …` | `glab issue note <n> -R <project url> -m …` |
| close it, with the reason | `gh issue close <n> --repo <owner/repo> -c …` | `glab issue note <n> …`, then `glab issue close <n> -R <project url>` |
| narrow it | `gh issue edit <n> --repo <owner/repo> --title …` | `glab issue update <n> -R <project url> -t …` |

`glab api` reads the project out of the worktree the session is in; `glab
issue close` carries no comment, so the note goes first.

**Never close an issue on a guess, and never close one no merged change
addressed.** Where you are unsure, comment and leave it open — a comment is
always the safe move and a close never is. **Reopening is not yours either.**
Four things go wrong, all measured on the repositories this skill reviews:

1. **The forge linked less than the body claimed.** Pull request #118 said
   `Closes #117 and #119`; GitHub linked only #117, and #119 — fixed and
   merged — stayed open until a person closed it. GitHub wants its own keyword
   per number, GitLab's pattern is its own, so the lesson is "ask the forge
   what it did", not "learn GitHub's syntax". Where one did not close and the
   fix is in, close it with a comment naming the change request; a fixed bug
   left open reads as a live bug.
2. **A change request that narrows rather than closes.** That same pull
   request said "#116 is narrowed, not closed". Comment with what is done and
   what remains; close nothing — the remainder is the part nobody notices is
   missing.
3. **An issue an earlier merge already half-answered.** thurbox #1175
   described two families of one leak, and #1163 had killed one before anybody
   picked it up. Comment saying which half is fixed and retitle to the
   remainder; **leave the original body alone**, because its measurements are
   the evidence.
4. **Nobody linked it at all.** When you can see a merged change closes an
   open issue, say so on the issue with the evidence and close it. When you
   only suspect it, comment and leave it open.

## 9. Report the tick

One line per change request with its verdict, or a plain statement that there
was nothing new. Name anything you deliberately left: waiting on CI, not
confident, a merge gate failed and which. Say what moved on the tracker in
the same shape — one line per issue, what you did and why, including one you
looked at and left open. **A tick that reviewed nothing is a normal tick**:
say so in one line and stop.
