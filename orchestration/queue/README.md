# The task queue

This directory is **yours**, not the template's. Everything the control plane
writes here is gitignored — this README is the only tracked file, exactly as
`../playbooks/local/` works. `.gitignore`'s header owns the reason: an instance
whose tracked tree matches the template's can always `git pull` a fast-forward.

`../../scripts/queue.sh` owns it. Its header is the full usage; this file is
the layout, so a fresh clone with an empty queue still shows what goes here.

## Shape

A prompt opens a **topic** — one unit of intent, usually several units of work
across several repos. The topic decomposes into **tasks**, and each task is a
directory holding everything about it and nothing about any other.

```text
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
| `task.yaml`'s `state` | where it stands now | `queue.sh collect` |

A topic view — every task under one heading, plan beside progress beside
outcome — is therefore the directory listing. It needs no field that is not
already here.

## Two rules worth knowing before you edit anything

**Only `blocked_by` makes a task wait.** It records a kind from a closed set
(`semantic-dependency`, `shared-external-state`, `incompatible-migration`,
`other`) and a reason, and `queue.sh block` refuses one without both. Files two
tasks both expect to change go under `touches`, where `plan` reports them as a
risk beside the ready set and holds nothing up.

**`queue.sh watch` closes nothing.** It folds thurbox's event stream into
`progress.jsonl`. A transition says a turn ended, which is not the claim that a
task finished — only the worker's own `result.md`, read by `queue.sh collect`,
closes anything.
