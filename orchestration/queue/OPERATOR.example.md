# Your standing instructions to every worker — the tracked EXAMPLE

Copy this file to `orchestration/queue/OPERATOR.md`, delete everything in it,
and write your own. That copy is **yours and gitignored**; this one is tracked
and only documents the format.

`queue.sh add` reads your copy when it scaffolds a `BRIEF.md`. If it exists and
is not empty, every brief written from then on points its worker at it — by
absolute path, or, for a task running on a remote host, by a path relative to
the brief itself — so a preference you write once reaches every task without
being retyped into a brief or edited into a tracked file. If it does not
exist, briefs say nothing about it — a fresh clone behaves exactly as it did
before this file existed.

**Why not `CONSTITUTION.md`,** which is what you probably call it: thurbox
ships a `docs/CONSTITUTION.md` that means something else entirely, and a worker
reading both repos would have to guess which one was meant. This is named for
whose file it is instead. `POLICY.md` beside it is fleet's standing policy;
this is yours.

## What goes in it

Prose, addressed to a worker agent — anything you would otherwise retype into
every brief:

```text
Always use my `xyz` skill when you touch a shell script.
Prefer uv over pip in any Python repo of mine.
Never add a dependency without saying in the PR body why nothing already
present would do.
```

**It is not configuration.** Nothing parses it: no schema, no keys, no
validator, no list of supported settings. Whatever you write is handed to the
worker as prose it reads, so write it the way you would say it.

Keep it short for the same reason a brief is kept short — every worker on every
task loads it. A preference true of one repo is better recorded in that repo's
`registry/context/<repo>.md`.

## What it does not do

Three things it never overrides, because the conflict is real and pretending
otherwise is how a worker gets it wrong:

- **A brief wins over it.** The constitution ADDS to a brief's task-specific
  instructions and never replaces them. If a brief says to use a particular
  tool for this one task, that is what happens here.
- **`POLICY.md` wins over it.** How a task publishes and what proves it,
  squash merge, that the operator merges and the worker does not, the gate
  run before the push, the result file that closes a task. Writing "skip the
  tests" or "just merge it" here does not make those go away — the worker
  follows the policy and your line is the one that loses.
- **It is not a way to reach fleet itself.** It instructs workers. Changing how
  the queue behaves is a change to the code, not a sentence written here.

## Secrets do not go here

Gitignored is not secret. This file sits in a working copy of a public repo,
one `git add -f` away from being committed, and every worker's context ends up
holding a copy of whatever it says. So it is the right place for preferences
and the wrong place for credentials — the same split
`orchestration/session-profiles.yaml`'s header makes. A token, a key or a
password belongs in the environment; name the variable here if a worker needs
to know it exists, never its value.
