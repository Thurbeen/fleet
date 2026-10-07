---
name: review-prs
description: Drive a maintainer's recurring review session — pick unreviewed open PRs or MRs, wait for CI, load the installed thurview-pr-review skill for review and posting, follow requests until merge, apply fleet's squash-merge gates and auto-merge.conf, and reconcile the issue tracker. Use when asked to review a repository's open change requests continuously, to reconcile issues after merges, or when invoked as /review-prs.
user-invocable: true
allowed-tools: Read, Bash, Glob, Grep
---

## review-prs

The maintainer's side of review: somebody else's change request, arriving on
a repository you own, needing a verdict. It runs on a cadence, because a
change request that waits a day for a first opinion is the expensive kind of
waiting. **Fleet picks, waits, merges and records; the installed
`thurview-pr-review` skill reviews and posts.** Load that skill for every PR
or MR this session picks up. Do not reproduce its review format here.

## The loop

1. Confirm the forge CLI is on `PATH`, and that you are in the review
   worktree on its own branch (§1).
2. List what is open; skip drafts, bots, and any head SHA you already
   reviewed (§2).
3. For each one left: wait for CI to conclude — leave it if anything is
   pending (§3).
4. Load `thurview-pr-review`, passing the repository's rules and verification
   constraints (§4–§6). It owns review and posting.
5. Keep each picked request under that skill's follow flow until it merges,
   closes or is explicitly stopped (§6).
6. On each tick, reconsider CI and fleet's merge gates for followed requests.
7. Merge only where allowed and every gate clears — squash only (§7).
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

Review posting on either forge belongs to the installed `thurview-pr-review`
skill and its own forge adapter, not fleet's `scripts/lib/forge.py`.

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
/loop 15m Review open change requests on <host/path> — read <absolute path to this SKILL.md> and load the installed thurview-pr-review skill for each picked PR or MR
```

15 minutes fits CI: shorter and most ticks find a run still going, much
longer and a green change request sits.

## 2. Find work — and know what "already handled" means

Skip, in this order: **drafts** (the author is not asking yet), **bot
authors** — Renovate, Dependabot, any `is_bot` (a dependency bump's verdict
is its CI run), and **anything whose CURRENT head SHA already carries your
review**.

Read the reviewed-head marker through the installed skill's status command;
do not invent another marker or parse another review format. The head-SHA
rule skips a redundant review pass, **not following or merge checks** on a
request already picked up. Keep that watch set until merge, closure or an
explicit stop. The installed skill owns what to review after each push.

## 3. Wait for CI. Always

A review posted before the run finishes is a review of half the evidence and
has to be retracted when a check goes red. If anything is still pending,
leave it for the next tick and say "checks still running" in the tick report.
Checks that are `SKIPPED` are concluded; checks that never started are not.

## 4. Give the review the repository's own rules

The diff alone does not tell you whether a change is acceptable *here*. Read
what the repository says about itself **for the paths this change touches**:
its own review rules, its `CLAUDE.md` / `AGENTS.md` / `CONTRIBUTING.md`, and
any convention the touched subsystem documents in its header. Checking
something else is noise.

Pass those rules into the installed review flow. Its evidence and findings
contracts govern the review; this file adds no second format.

## 5. When a verdict needs hardware you do not have

A Windows-only path, a second forge, another architecture: reach for the real
thing rather than approving on the author's word — a host the operator has
told you about, driven as `thurbox-session` §1a describes, or the repo's own
CI where the run covers it. **Do the check first and post one review carrying
its result**: two reviews on one SHA read as indecision. If neither is
available, give the installed skill the platform you could not reach and what
you would have run. Do not call that claim verified or merge on its strength.

## 6. Load thurview-pr-review and follow each request

Load and run the **installed `thurview-pr-review` skill** on each picked PR
or MR URL. Follow its complete publishing preflight, review and posting flow.
It owns the summary, finding threads, published page link and re-review on
push; fleet adds none of those itself. Apply the operator's writing and
sharing rules before it posts on their behalf.

**Missing skill or publish config:** report what is missing and the relevant
setup instructions the skill or CLI prints; do not fall back to a different
format, a hand-written comment or a private live URL. If the skill is absent,
report that `thurview skill` must list `thurview-pr-review` before proceeding.
Leave that request unreviewed and do not merge it; continue independent work.

Keep following every picked request until merge, closure or an explicit stop;
do not use `--once` to discard that duty. Interleave the skill's bounded waits
and passes across the watch set so one open request does not prevent the
session from picking up another. CI still governs each new head (§3). A head
already reviewed needs no new pass, but remains eligible for merge-gate checks.
Respect the installed skill's stopped state; do not restart it on a guess.

## 7. Merge what is clean

Merging is fleet's decision after the installed review pass, never an action
of `thurview-pr-review`. Read `orchestration/auto-merge.conf` in the
control-plane checkout (the tracked example names none); the repository must
be allowed there. Honor any stricter operator rule, including manual UI
validation or a repository they merge themselves. **Squash only**, so the
title becomes the commit; a project that forbids squash (GitLab's
`squash_option: never`) is one you report and leave.

Merge only when every one of these holds — they are `shepherd`'s gates, and
there is no reason for a second, weaker set:

| Gate | Why it is not optional |
|---|---|
| the installed review reports safe to merge on this head, with no open findings | an older verdict or unanswered finding cannot authorize this head |
| any forge-required maintainer approval is present | the skill's confidence does not cast a forge approval |
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
wrong in public. An issue comment is writing you publish, so the operator's
standing writing rules govern it.

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
