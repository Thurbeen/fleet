# Standing policy for fleet workers

This is the policy every task in every repo runs under. `queue.sh add`'s brief
scaffold points each worker here by absolute path instead of restating it, and
this is the only copy.

If you are a worker: **read this once before you start.** Your brief holds what
is true for your task; this holds what is true for all of them. The brief does
not override it.

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

## Open the pull request through the pipeline

**Open every pull request by running the `no-mistakes` skill with `--yes`**
(`/no-mistakes --yes`). Not `gh pr create`, and not the web UI. The pipeline is
the review, the tests, the lint, the push and the pull request in one pass; a
pull request that skipped it has had none of them.

The proof is the body it writes. A `no-mistakes` pull request carries all five
of these headings:

```text
## Intent
## What Changed
## Risk Assessment
## Testing
## Pipeline
```

`queue.sh collect` fetches your pull request body and looks for exactly those
five. A task whose body is missing any of them **is not closed** — the lead
sees it at collect time and sends you back to redo the push. So verify your own
pull request body before you report done.

That check exists because the instruction it replaces could not be checked:
"use the pipeline" describes a METHOD, and a method leaves no trace. Two tasks
were once collected as shipped with hand-made pull requests, and nobody noticed
until the operator read the bodies himself.

## Do not merge

- **Squash merge only.** It is the only method the remote allows, so the pull
  request title becomes the commit on `main`. Write the title accordingly.
- **You do not merge.** The operator reviews and merges every pull request
  himself. Opening it is where your work ends.

## Reporting back — write a file, do not send mail

Your brief names an absolute path for `result.md`. Write it when you are
finished, or when you have concluded you cannot finish, with exactly this
shape:

```markdown
---
outcome: shipped | stuck | failed | not-applicable
artifact: <PR url, or omit>
---
A short paragraph: what you actually did, and anything the lead must know.
```

`outcome` is one of those four words and nothing else. `artifact` is the pull
request URL when there is one; `not-applicable` and `stuck` usually have none,
and that is fine. `shipped` is a claim that a pull request exists, so report it
without one, or with something that is not a pull request URL, and the lead's
`collect` holds your task open rather than trusting the word alone.

That file is what closes your task. Without it the lead sees only that a turn
ended, which is not the same claim, so a task with no result file stays open
however cleanly your session finished.

Do **not** use `thurbox-cli message send`. It injects into the lead's terminal
and interrupts whoever is talking to it. The lead reads your file on its own
schedule.
