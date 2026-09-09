---
# The publish method every task under this policy gets unless `queue.sh add`
# says otherwise, and the words the brief uses to name the tool. `method` is
# one of `no-mistakes`, `pr` or `push` — what a task must PRODUCE — and `how`
# is free text that fleet renders into the brief and never parses.
#
# Delete this block and tasks default to `pr`, which needs no setup: a pull
# request from the task's branch is the whole proof. It says `no-mistakes`
# here because that is what this operator's fleet publishes with, and stating
# it once beats retyping `--publish` per task and forgetting it on one.
publish:
  method: no-mistakes
  how: run `/no-mistakes --yes`
---

# Standing policy for fleet workers

This is the policy every task in every repo runs under. `queue.sh add`'s brief
scaffold points each worker here by absolute path instead of restating it, and
this is the only copy.

If you are a worker: **read this once before you start.** Your brief holds what
is true for your task; this holds what is true for all of them. The brief does
not override it.

Your brief may also point you at an `OPERATOR.md` — the operator's own standing
instructions, which are theirs and not fleet's. That file ADDS to your brief.
It overrides neither the brief nor anything here: where it disagrees with this
policy, this policy is what you follow.

It is tracked, unlike everything else the queue writes here, because it is
standing policy and not one operator's working state — `../../.gitignore`'s
header owns that split. It exists because policy retyped once per brief
drifts: across five hand-written briefs, ~30 lines each were the same copied
paragraphs, and the squash-merge rule had survived into exactly one of the
five.

## One brief, one worker

The brief you were handed is your whole instruction set. Other workers are
running against other briefs at the same time, often in the same repo. That is
normal and expected.

- Do not read another task's brief.
- Do not widen your scope to tidy what another worker is doing.
- Do not wait for them.

If your change conflicts with theirs, an ordinary rebase resolves it. Rebase
onto your base branch before you start.

## Gate locally before you push

Run the repo's own gate and make it green first. In this control plane that is
`./scripts/check.sh`, and `./scripts/check.sh --fix` applies the fixes a check
can apply. Where a repo names a different gate in its `AGENTS.md` or
`CONTRIBUTING.md`, that one is the gate.

CI here only fires on pull requests, so the local run is what catches a break
before anyone else sees it.

## Publish the way your brief says

Your brief's **Publish** line names one of three methods, what it must leave
behind, and what proves it. It is rendered from fleet's own vocabulary, so it
is the authority — this section does not restate it and cannot drift from it.
Where the line names a tool, use that tool. Do not switch methods.

`queue.sh collect` then goes and looks for that artifact: the forge for a pull
request, git for a commit on the base branch. A task whose artifact is not
there, or is not from your branch, **is not closed** — the lead sees it at
collect time and sends you back. So verify your own artifact before you report
done. For a `no-mistakes` task that is one command:

```sh
gh pr view <url> --json headRefOid,body -q \
  '.headRefOid[0:8] as $head
   | ([.body | capture("head_sha\"\\s*:\\s*\"(?<s>[0-9a-f]+)").s] | .[0]) as $attested
   | $head + " is the head; the attestation names "
     + ($attested | if . then .[0:8] else "no attestation found in the body" end)'
```

**Those two must be the same commit.** An attestation is a verdict about the
code the pipeline saw, so one naming any other commit proves nothing about
what would merge, and `collect` holds your task open exactly as it does for a
body with no attestation at all.

They come apart on their own. The pipeline writes the attestation while it
opens the pull request and can then push its own `no-mistakes: apply CI fixes`
commit on top, which leaves the head one commit ahead of what was attested —
this is what happened to #38, #40 and #48. **Run `/no-mistakes --yes` again**
and it re-attests the new head; then run the command above once more before
you write `result.md`. Never hand-edit the body to name the head: an
attestation you typed attests nothing, and it is the one thing in the body a
reader trusts you did not write.

That check exists because the instruction it replaces could not be checked:
"use the pipeline" describes a METHOD, and a method leaves no trace. Two tasks
were once collected as shipped with hand-made pull requests, and nobody noticed
until the operator read the bodies himself. Naming the artifact is that same
requirement written as something a reader can go and verify.

The default for every task here is the frontmatter at the top of this file.

## Do not merge

- **Squash merge only.** It is the only method the remote allows, so the pull
  request title becomes the commit on `main`. Write the title accordingly.
- **You do not merge.** Opening it is where your work ends. `queue.sh
  shepherd` may later merge it for you in the repos its `AUTO_MERGE_REPOS`
  allowlist names, but only once your pull request clears its gates — never
  merge it yourself in the meantime.

## Reporting back — write a file, do not send mail

Your brief names where `result.md` goes — an absolute path, or, if you are
running on a remote host, a path relative to the brief itself. Write it when
you are finished, or when you have concluded you cannot finish, with exactly
this shape:

```markdown
---
outcome: shipped | stuck | failed | not-applicable
artifact: <PR URL, or commit URL for a `push` task, or omit>
---
A short paragraph: what you actually did, and anything the lead must know.
```

`outcome` is one of those four words and nothing else. `artifact` is whatever
your brief's Publish line says it is — a pull request URL for two of the three
methods, a commit URL for `push`; `not-applicable` and `stuck` usually have
none, and that is fine. `shipped` is a claim that the artifact exists, so
report it without one, or with something of the wrong shape, and the lead's
`collect` holds your task open rather than trusting the word alone.

That file is what closes your task. Without it the lead sees only that a turn
ended, which is not the same claim, so a task with no result file stays open
however cleanly your session finished.

Do **not** use `thurbox-cli message send`. It injects into the lead's terminal
and interrupts whoever is talking to it. The lead reads your file on its own
schedule.
