---
name: review-prs
description: Stand up and drive a maintainer's review session over a repository's open change requests — find what is unreviewed, wait for CI, read the diff against the repo's own house rules, post approve / request-changes / comment to the forge, squash-merge what is genuinely clean, and reconcile the issue tracker behind it — what the merge actually closed, what it only narrowed, what nobody linked. Covers the head-SHA marker that stops re-reviewing the same commit, the skip rules for drafts and bots, verifying a claim on real hardware before approving it, the merge gate, and never closing an issue on a guess. Use when asked to review open PRs or MRs, to review a repository's pull requests continuously, to tidy up or close out the issues after a merge, or when invoked as /review-prs.
user-invocable: true
allowed-tools: Read, Bash, Glob, Grep
---

## review-prs

The maintainer's side of review: somebody else's change request, arriving on a
repository you own, needing a verdict. It runs on a cadence, because a change
request that waits a day for a first opinion is the expensive kind of waiting.

### What this is not, and what to use instead

Four things in reach review code, and picking the wrong one wastes the pass:

| Want | Use |
|---|---|
| a verdict posted on someone's open change request, repeatedly, as maintainer | **this skill** |
| bugs in the diff you are writing right now, in your own tree | `/code-review` |
| your own branch taken through a gate and published | the `publish` skill |
| a guided document a human reads, annotates and approves | `thurview` |

The distinguishing property is **inbound and recurring**. The others are things
you run once, on your own work.

### Both forges

A change request is a pull request on GitHub and a merge request on GitLab, and
this repository does not assume which — `scripts/lib/forge.py` is the seam that
keeps it that way, and a self-hosted GitLab is the ordinary case rather than a
special one. Every call below is given in both CLIs; `-R https://<host>/<group>/<project>`
is what points `glab` at an instance that is not gitlab.com.

| Ask | GitHub | GitLab |
|---|---|---|
| what is open | `gh pr list --repo <owner/repo> --state open --json number,title,author,isDraft,headRefOid,reviews` | `glab mr list -R <project url> -F json` |
| did CI conclude | `gh pr checks <n> --repo <owner/repo>` | `glab ci status -R <project url> -b <branch>` |
| the change | `gh pr view <n> …` / `gh pr diff <n> …` | `glab mr view <n> -R <project url> -F json` / `glab mr diff <n> -R <project url>` |
| the verdict | `gh pr review <n> --approve / --request-changes / --comment` | `glab mr approve <n>` / `glab mr note <n> -m …` |

Identify a repository by **host plus path** — `github.com/owner/repo` — because
a bare `owner/repo` names two different repositories once two forges are in
play.

## 1. Give it its own session, and its own worktree

```bash
thurbox-cli session create \
  --name "$(uv run fleet session-name review 'Review open change requests on <project>')" \
  --repo-path <the repo> --worktree-branch review-prs --base-branch main \
  --on-existing adopt --parent <the lead's uuid> --json
```

**The name is rendered, never typed.** The reviewer wears a mark in the session
list the way the lead and every worker do, and which mark is a setting
(`orchestration/session-glyphs.example.conf`) — so no file spells it and
`GLYPHS=off` leaves this session the plain title above. Run that substitution
from the control-plane checkout, which is where `uv run fleet` resolves.

It REFUSES a title thurbox would reject and one the 64-byte cap would cut,
rather than handing over a name that is wrong in a way nobody sees. The refusal
ends with a `Try this title:` line carrying one that does render — take that
rather than inventing another — or, where none can be derived from what you
typed, with `Reword the title.` A title over the cap usually gets the second:
no WORD is ever dropped to fit it, because what runs over is the end, and the
end is what tells two sessions apart. `$(...)` swallows the exit code, so a
`--name ''` refusal from thurbox means read the stderr above it: that is this
command's message, not a thurbox bug.

**`<project>` is the bare project name, and it is the one place this skill does
NOT identify a repository by host plus path.** A session name becomes a path
segment in thurbox, so a `/` in it is refused — `github.com/owner/repo` in that
title spawns nothing. It is also what keeps the title inside the cap: the mark
and the words before `<project>` cost 36 of the 64 bytes, so a project name
over 28 characters is refused, or 33 with `GLYPHS=off`, which drops the mark.
Shorten the TITLE then
(`Review requests on <project>`), never the project, which is the part that
tells two reviewers apart.
The full identity is already on `--repo-path`, and every
forge call below takes it in its own form; the name only has to tell one
reviewer from another in the session list.

**Its own worktree is not optional.** A reviewer checks out other people's head
commits to try things. Doing that in the main checkout leaves the operator's
working tree on a stranger's branch, and a later fast-forward refuses a tree it
cannot recognise.

`adopt` because this recurs; **read `created` before you send anything** —
`false` means the reviewer is already running and a prompt would interrupt it
mid-pass. **`adopt` matches on the NAME, so a reviewer created before the mark
existed is not the one it finds.** The first spawn after that tries to make a
SECOND reviewer, which asks for the `review-prs` worktree the first one is
holding, and git refuses it. Rename the old one rather than deleting it — it
keeps both its conversation and that worktree, and rename takes the name, the
uuid or an id prefix:

```bash
thurbox-cli session rename '<its current name>' \
  "$(uv run fleet session-name review 'Review open change requests on <project>')"
```

`--on-existing replace` is NOT the way out: it matches the same name `adopt`
does, so it tears down nothing and the worktree still blocks the spawn.
A hand-spawned session still has its agent's trust dialog in front of it:
`uv run fleet session-trust <uuid>` answers it, and `session send` before that
types the prompt into the dialog. `.agents/skills/thurbox-session/` owns both.

Three rules the session lives under, all three from getting them wrong:

- Never `cd` to the main checkout.
- Never leave the worktree on a branch other than its own.
- Never use bare `git stash` — that stack is shared with everyone else in the
  repository, and a reviewer has no business pushing onto it.

**A spawned session does not inherit your interactive shell's PATH.** Check
`command -v gh` (or `glab`) first and put the directory it lives in on `PATH`
before anything else; the failure otherwise looks like "no open pull requests".

Then send one line pointing at this file, and set the cadence. In Claude Code
that is its `/loop`:

```text
/loop 15m Review open change requests on <owner/repo> — follow .agents/skills/review-prs/SKILL.md
```

15 minutes fits CI: shorter and most ticks find a run still going, much longer
and a green change request sits.

## 2. Find work — and know what "already handled" means

Skip, in this order:

| Skip | Why |
|---|---|
| drafts | the author is not asking yet |
| bot authors — Renovate, Dependabot, any `is_bot` | a dependency bump's verdict is its CI run |
| **anything whose CURRENT head SHA already carries your review** | already handled |

**That last rule is the whole reason the cadence is cheap, and it is keyed on
the head SHA rather than on the change request.** A review is about the code it
saw. When the author pushes, the SHA moves, the marker stops matching, and the
change request comes back into the queue by itself. Keying on the number would
review each one exactly once and then go blind to every revision; keying on
"has any review at all" does the same thing.

## 3. Wait for CI. Always

**A review posted before the run finishes is a review of half the evidence**,
and it has to be retracted or amended when a check goes red. If anything is
still pending, leave it and pick it up next tick — that is what the cadence is
for. Say "checks still running" in the tick report, so a quiet tick is legible.

Checks that are `SKIPPED` are concluded. Checks that never started are not.

## 4. Read the change, and read the house rules with it

The diff alone does not tell you whether a change is acceptable *here*. Read
what the repository says about itself, **for the paths this change touches**:
its own review rules where it has them, its `CLAUDE.md` / `AGENTS.md` /
`CONTRIBUTING.md`, and any convention the touched subsystem documents in its
own header. A repo that writes its rules down is telling you what review should
check; checking something else is noise.

**Verify load-bearing claims rather than believing them.** A body that says
"this now costs two subprocesses instead of nine" is a claim you can test in a
minute, and one that says "this cannot happen on Windows" is worth more than
the sentence. A cheap experiment beats prose on faith, and finding the one
place a claim does not hold is worth more than ten style notes.

**Prefer one real defect to a list of nits.** A review that opens with four
naming preferences buries the bug at the bottom, and the author reads the first
two.

## 5. When a verdict needs hardware you do not have

Some claims cannot be checked from the machine the session runs on — a
Windows-only path, a second forge, another architecture. Reach for the real
thing rather than approving on the author's word:

- a host the operator has told you about, driven exactly as
  `.agents/skills/thurbox-session/` describes for a remote worker;
- the repo's own CI, where the run already covers it.

**Do the check first and post one review carrying its result**, rather than a
provisional verdict amended later — two reviews on one SHA read as indecision,
and the second is the only one anybody reads.

If neither is available, say so in the review: name the platform you could not
reach and what you would have run. An honest gap is a fine thing to post; a
silent one is not.

## 6. Post it

Whatever standing writing rules your agent loads govern every word, and they
outrank this section. What review itself requires, wherever those are silent:

- **Open with the next step** — a plain first line naming who must act, so the
  author reads one line and knows whether the change request is theirs again.
- **One point per comment, anchored to its line.** A wall of prose at the top
  of the diff is not review.
- **Mark anything non-blocking as non-blocking**, so `request-changes` means
  one thing.
- **Three to five lines.** A verdict that needs more is usually two verdicts.

**When a review is genuinely uncertain, post nothing and say so in the tick
report.** An unreviewed change request is a known state. A confidently wrong
approval is not, and it is the one failure here that costs more than doing
nothing at all.

## 7. Merge what is clean

The operator asked this session to review a named repository, and that ask is
the authorisation to merge there — it is not
`orchestration/auto-merge.conf`, which is the list for the UNATTENDED path
(`fleet queue shepherd`) and says nothing about a session a person started.
**Squash is the only method fleet merges by**, so the title becomes the commit
on the base branch; write it accordingly. A project that forbids squash — a
GitLab project can, with `squash_option: never` — is one you report and leave.

Merge only when every one of these holds:

| Gate | Why it is not optional |
|---|---|
| you just approved it, on this head SHA | merging something you did not review is not review |
| every check concluded and passed | a pending check is not a passing one |
| the forge reports it mergeable | a conflicting change needs its author |
| the head branch is **in the repository**, not a fork | the one claim about a change request a stranger cannot write for themselves |
| whoever opened it can push there | anyone with read access can open one between two existing branches |
| an attestation naming the current head, where the repo expects one | a verdict is about the code it saw; one from an earlier push is stale |

Those are `fleet queue shepherd`'s gates, deliberately — AGENTS.md's
orchestration section argues them, and there is no reason for a second, weaker
set. Anything failing one of them gets the review and no merge.

**Never merge on a tick where CI was still running when you looked.** Re-read
the checks immediately before merging, not at the top of the pass: a run can go
red between reading the diff and pressing the button.

## 8. Reconcile the tracker

A merge changes the issue tracker, and the forge does only the part it was
asked for in exactly the syntax it wanted. This section is the pass that
follows every merge — yours, or one that landed between ticks — because every
case below is something a merge caused.

**Scope: the issues the change requests in front of you touch** — the ones a
body names, and the ones you can see a merged change fixed. Not a triage sweep
over every open issue. That is a different job on a different cadence, and on a
public repository a reviewer forming opinions about strangers' unrelated
reports is a way to be wrong in public. The boundary is a decision, not an
omission.

An issue comment is writing you publish, so §6's first paragraph governs it
exactly as it governs a review.

| Ask | GitHub | GitLab |
|---|---|---|
| what the forge actually linked | `gh pr view <n> --repo <owner/repo> --json closingIssuesReferences` | `glab api projects/:fullpath/merge_requests/<n>/closes_issues` |
| the issue's state | `gh issue view <n> --repo <owner/repo> --json state,stateReason,title` | `glab issue view <n> -R <project url> -F json` |
| say something on it | `gh issue comment <n> --repo <owner/repo> -b …` | `glab issue note <n> -R <project url> -m …` |
| close it, with the reason | `gh issue close <n> --repo <owner/repo> -c …` | `glab issue note <n> …`, then `glab issue close <n> -R <project url>` |
| narrow it | `gh issue edit <n> --repo <owner/repo> --title …` | `glab issue update <n> -R <project url> -t …` |

`glab api` reads the project out of the worktree the session is in, which §1
guarantees; `glab issue close` carries no comment, so the note goes first and
the close second, never the other way round.

**Never close an issue on a guess, and never close one no merged change
addressed.** That rule outranks all four cases below. It is §6's "post nothing
when you are genuinely uncertain", applied to the tracker: an open issue is a
known state, and a wrongly closed one is a bug nobody is looking for any more.
Where you are unsure, comment and leave it open — a comment is always the safe
move and a close never is. **Reopening is not yours either.** An issue somebody
closed is a decision you did not make.

Four things go wrong, all four measured on the repositories this skill already
reviews:

**1. The forge linked less than the body claimed.** Pull request #118 said
`Closes #117 and #119`. GitHub linked only #117, and #119 — a bug that was
fixed and merged — stayed open until a person closed it by hand. GitHub wants
its own keyword per number, `Closes #117, closes #119`, and "and #119" is not
one. GitLab's pattern is its own — one keyword takes a list — which is why the
lesson is "ask what the forge did", not "learn GitHub's syntax". **So after a
merge, ask the forge which issues it actually closed rather than reading the
body and believing it.** Where one did not close and the fix really is in,
close it with a comment naming the change request that fixed it and why it did
not close itself. A fixed bug left open is read as a live bug.

**2. A change request that narrows rather than closes.** That same pull request
said "#116 is narrowed, not closed" and named the half it left behind. Read
that and leave #116 open: comment with what is now done and what remains, and
close nothing. Treating "narrows" as "closes" loses the remainder silently, and
the remainder is the part nobody notices is missing.

**3. An issue an earlier merge already half-answered.** thurbox #1175 described
two families of one leak, and #1163 had killed one of them before anybody
picked the issue up. The move then was a comment saying which half was already
fixed and a retitle to the remainder, not a close. **Narrow by comment and
title; leave the original body alone**, because its measurements are the
evidence somebody gathered and a rewrite is not a replacement for them.

**4. Nobody linked it at all.** A change request can fix an issue without ever
mentioning it. When you can see that a merged change closes an open one, say so
on the issue with the evidence — the change request, and the part of it that
does the work — and close it. When you only suspect it, comment and leave it
open.

## 9. Report the tick

One line per change request with its verdict, or a plain statement that there
was nothing new. Name anything you deliberately left: one waiting on CI, one
you were not confident about, one whose merge gate failed and on which gate.

**Say what moved on the tracker in the same shape**: one line per issue — its
number, what you did to it (commented, closed, retitled) and why. An issue you
looked at and deliberately left open is a line too, naming what you were not
sure about. A tick that touched no issue needs no tracker line.

**A tick that reviewed nothing is a normal tick.** Say so in one line and stop;
do not manufacture a finding to justify the pass.

## The loop

1. Confirm the forge CLI is on `PATH`, and that you are in the review worktree
   on its own branch.
2. List what is open; skip drafts, bots, and any head SHA you already reviewed.
3. For each one left: wait for CI to conclude — leave it if anything is pending.
4. Read the diff, the body, and the repo's own rules for the paths touched.
5. Verify the load-bearing claims, on real hardware where that is what it takes.
6. Post approve / request-changes / comment, opening with the next step.
7. Merge the approved ones that clear every gate in §7 — squash only.
8. Reconcile the tracker against what the forge actually closed — comment where
   you are unsure, and close nothing on a guess.
9. Report one line per change request and per issue you touched, and say plainly
   when there was nothing new.
