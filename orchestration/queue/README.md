# The task queue

This directory is **yours**. Everything the control plane writes here is
gitignored; the two tracked files are this README and [`POLICY.md`](POLICY.md).
`.gitignore`'s header owns the reason: this repo is public, and your prompts,
your briefs and your workers' results are not a thing to publish.

`POLICY.md` is the standing policy every worker runs under — the pipeline
requirement and the five headings that prove it, squash-merge, who merges, the
gate, one-brief-one-worker, and the result contract. Every `BRIEF.md` the
scaffold writes points at it by absolute path rather than restating it, so it
is written once and cannot drift between briefs. **Task-specific detail still
belongs in the brief**; only the repetition moved.

`../../scripts/queue.sh` owns it. Its header is the full usage; this file is
the layout, so a fresh clone with an empty queue still shows what goes here.

**One queue, one checkout.** This directory belongs to the control plane — the
clone the `fleet` session opens — and is resolved from `queue.sh`'s own
location, never from the shell's cwd. If you landed here in a second clone (the
one workers branch and push from, because the control plane may have no
`origin`), this is not the queue anyone is reading: `../../scripts/queue.sh
root` prints the one in use, `queue.sh topic add` refuses here, and
`../../scripts/webui.sh status` prints the directory the dashboard serves. Set
`FLEET_QUEUE_DIR` to override all of that, verbatim.

## Shape

A prompt opens a **topic** — one unit of intent, usually several units of work
across several repos. The topic decomposes into **tasks**, and each task is a
directory holding everything about it and nothing about any other.

```text
POLICY.md                            standing policy — tracked, read by workers
<topic>/                             e.g. report-status-honestly/
  topic.yaml                         slug, title, when it opened
  PROMPT.md                          the prompt that opened it, VERBATIM
  <NN>-<slug>/                       e.g. 01-drop-idle-default/
    task.yaml                        intent + current state — queue.sh owns it
    BRIEF.md                         the instructions ONE worker reads
    progress.jsonl                   one line per observed transition
    result.md                        what the worker concluded, in its words
.cursor                              the last watch sequence handled
```

Four files per task, because four different things want four different answers:

| File | Answers | Written by |
|---|---|---|
| `task.yaml` + `BRIEF.md` | what is **intended** | the lead, at intake |
| `progress.jsonl` | what has **happened**, and when | `queue.sh watch` |
| `result.md` | what was **concluded** | the worker, when it knows |
| `task.yaml`'s `state` | where it stands now | `queue.sh collect`, then `queue.sh reap` |

A topic view — every task under one heading, plan beside progress beside
outcome — is therefore the directory listing. It needs no field that is not
already here. `../../scripts/webui.sh` serves exactly that view in a browser,
as a reader: it opens these four files and adds nothing to them.

## Three rules worth knowing before you edit anything

**Only `blocked_by` makes a task wait.** It records a kind from a closed set
(`semantic-dependency`, `shared-external-state`, `incompatible-migration`,
`other`) and a reason, and `queue.sh block` refuses one without both. Files two
tasks both expect to change go under `touches`, where `plan` reports them as a
risk beside the ready set and holds nothing up.

**`queue.sh watch` closes nothing.** It folds thurbox's event stream into
`progress.jsonl`. A transition says a turn ended, which is not the claim that a
task finished — only the worker's own `result.md`, read by `queue.sh collect`,
closes anything.

**`collect` checks the artifact it is handed.** A `no-mistakes` pull request
body carries `## Intent`, `## What Changed`, `## Risk Assessment`, `## Testing`
and `## Pipeline`; a task whose PR is missing any of them is reported and left
OPEN, because "open the PR through the pipeline" is an instruction about a
METHOD and a method leaves no trace anyone can read. A check that could not run
— no `gh`, no network — says exactly that and is never counted as either
verdict. `collect --allow-unverified` closes a flagged task once you have read
that pull request yourself.

**`done` is not the end of the record.** `queue.sh reap` — which `collect`
runs for you — asks the forge whether a `done` task's pull request merged and
moves it to `landed` (or, if the pull request closed unmerged, `abandoned`); a
task with no PR artifact goes straight to `landed`. Landing releases the
task's session and worktree and stamps `task.yaml` with a `reaped: {session,
how, at}` receipt, because the id it names no longer resolves to anything.
