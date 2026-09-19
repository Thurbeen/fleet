# The task queue

This directory is **yours**. Everything the control plane writes here is
gitignored; the three tracked files are this README, [`POLICY.md`](POLICY.md)
and [`OPERATOR.example.md`](OPERATOR.example.md). `.gitignore`'s header owns
the reason: this repo is public, and your prompts, your briefs and your
workers' results are not a thing to publish.

`POLICY.md` is the standing policy every worker runs under — publish the way
your brief says and verify your own artifact, squash-merge, who merges, the
gate, one-brief-one-worker, and the result contract. Its YAML frontmatter holds
this operator's default publish method, the one thing in the file fleet parses.
Every `BRIEF.md` the scaffold writes points at it — by absolute path, or, for a
task running on a remote host, by a path relative to the brief itself — rather
than restating it, so it is written once and cannot drift between briefs.
**Task-specific detail still belongs in the brief**; only the repetition
moved.

`OPERATOR.md` is its counterpart with the ownership reversed: fleet owns the
policy, **you** own that file, and it is gitignored like the rest of this
directory. Write your standing preferences into it — "always use my `xyz`
skill" — and every brief scaffolded afterwards points its worker there too. No
such file, no pointer. Copy `OPERATOR.example.md` to start; its header holds
the format and the precedence.

`uv run fleet queue` owns it, and `../../scripts/lib/queue.py`'s docstring and
`uv run fleet queue --help` are the full usage; this file is the layout, so a
fresh clone with an empty queue still shows what goes here.

**One queue, one checkout.** This directory belongs to the control plane — the
clone the `fleet` session opens — and is resolved from the checkout `queue.py`
lives in, never from the shell's cwd. If you landed here in a second clone (the
one workers branch and push from, because the control plane may have no
`origin`), this is not the queue anyone is reading: `uv run fleet queue root`
prints the one in use and `fleet queue topic add` refuses here. Set
`FLEET_QUEUE_DIR` to override all of that, verbatim.

## Shape

A prompt opens a **topic** — one unit of intent, usually several units of work
across several repos. The topic decomposes into **tasks**, and each task is a
directory holding everything about it and nothing about any other.

```text
POLICY.md                            standing policy — tracked, read by workers
OPERATOR.example.md                  the form of the file below — tracked
OPERATOR.md                          your standing instructions — yours, ignored
<topic>/                             e.g. report-status-honestly/
  topic.yaml                         slug, title, when it opened, whether archived
  PROMPT.md                          the prompt that opened it, VERBATIM
  <NN>-<slug>/                       e.g. 01-drop-idle-default/
    task.yaml                        intent + current state — the queue owns it
    BRIEF.md                         the instructions ONE worker reads
    progress.jsonl                   one line per observed transition
    result.md                        what the worker concluded, in its words
```

There is no queue-wide cursor. `watch` resumes each task from the highest
sequence number in that task's own `progress.jsonl`, so one task's events can
never consume another's — the bug that left 19 of 20 timelines empty.

Four files per task, because four different things want four different answers:

| File | Answers | Written by |
|---|---|---|
| `task.yaml` + `BRIEF.md` | what is **intended** | the lead, at intake |
| `progress.jsonl` | what has **happened**, and when | `fleet queue watch` |
| `result.md` | what was **concluded** | the worker, when it knows |
| `task.yaml`'s `state` | where it stands now | `fleet queue collect`, then `fleet queue reap` |

A topic view — every task under one heading, plan beside progress beside
outcome — is therefore the directory listing. It needs no field that is not
already here. `interface/fleet_queue.lua` draws exactly that view in a thurbox
column, as a reader: it opens these four files and adds nothing to them.

## Three rules worth knowing before you edit anything

**Only `blocked_by` makes a task wait**, and every entry there names EITHER a
`task:` — the wait that ends when that task lands — OR a `condition:` outside
the queue, which nothing clears but `block --clear` naming it back. Never both:
`fleet queue check` reports an entry that carries the two. Each form takes a
kind from its own closed set plus a reason, and `fleet queue block` refuses one
without both; `block --help` lists the kinds of each. Files two tasks both
expect to change go under `touches`, where `plan` reports them as a risk beside
the ready set and holds nothing up.

**`fleet queue watch` closes nothing.** It folds thurbox's event stream into
`progress.jsonl`. A transition says a turn ended, which is not the claim that a
task finished — only the worker's own `result.md`, read by `fleet queue
collect`, closes anything.

**`collect` checks the artifact it is handed.** Each task declares a publish
METHOD — `attested`, `pr`, `push`, `note` or `none` — naming what it must
produce, and `collect` goes and looks: the forge for a change request from that
task's own branch or its recorded `target` (carrying an attestation for its
head, for that method), the forge again for a note on that target written by
the account fleet runs as, git for a commit that reached the base branch, and
nowhere at all for `none`. A change request the forge reports merged closes its
task whatever its attestation said, and keeps that as a note. **A task that
spans repositories — `add --add-repo` — is asked once per repository**, records
one artifact for each, and is closed only when every one of them verifies; the
report names the ones that did not. A task whose artifact is not
there is reported and left OPEN, because "use the pipeline" is an instruction
about a method and a method leaves no trace anyone can read. A check that could
not run — no forge CLI, no network, a base branch this machine cannot see — says
exactly that and is never counted as either verdict. `collect
--allow-unverified` closes a flagged task once you have read that artifact
yourself. `fleet queue show` prints the method, the verdict and the publish
state that came of it; the tool itself is `publish.how`, free text fleet
renders into the brief and never parses.

**`done` is not the end of the record.** `fleet queue reap` — which `collect`
runs for you — asks the forge whether a `done` task's change request merged and
moves it to `landed` (or, if it closed unmerged, `abandoned`); a task with no
change-request artifact goes straight to `landed`. A task that spans
repositories reaches `landed` only when EVERY one of its change requests
merged, because a blocker clears on `landed` and half a task on `main` would
release a dependent onto code that is not there. Landing releases the
task's session and worktree and stamps `task.yaml` with a `reaped: {session,
how, at}` receipt, because the id it names no longer resolves to anything.
