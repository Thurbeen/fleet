---
name: fleet-queue
description: Turn a prompt into durable task records, dispatch every independent task at once, and learn what finished by reading a stream and a file instead of being interrupted. Use whenever the control plane is given work — especially work spanning several projects, several tasks, or several merges at the same time — and whenever you are asked what is in flight.
user-invocable: true
allowed-tools: Read, Edit, Write, Bash, Glob, Grep
---

## fleet-queue

A prompt is not a turn in this conversation. It is a **topic** on disk, which
becomes **tasks** on disk, each carrying its own instructions in its own file.
`./scripts/queue.sh` owns all of it and its header is the full usage; this skill
is how to think while driving it.

Three things it buys, and they are the three the control plane could not do
before:

- **Nothing is lost to a context reset.** The prompt is kept verbatim, the plan
  is a file, and the record survives you.
- **Independent work goes out all at once.** Not one at a time, not capped.
- **Your context stays clean.** You read a line per task. Each worker reads one
  brief and never sees another.

### Where it sits next to `thurbox-session`

`thurbox-session` is how ONE session is spawned, prompted and cleaned up, and
all of it still applies — `--on-existing`, session profiles, trust, multi-repo,
the state vocabulary. This skill is the layer above: what work exists, what
order it goes in, and how you find out it finished. When the two disagree about
completion, this one wins: **workers write result files, they do not send mail.**

## 1. Intake — a prompt becomes a topic

Do this before doing anything else with a new ask, including one that looks
like a single task. A topic with one task costs nothing; a task with no topic
costs you the prompt.

```bash
./scripts/queue.sh topic add report-status-honestly \
  --title 'Make thurbox report agent status honestly' \
  --prompt-file -    <<'EOF'
<the ask, exactly as it arrived — do not summarise it>
EOF
```

The prompt is stored verbatim on purpose. Your summary of it is a lossy copy
made at the moment you understood it least.

Then decompose. A topic is the unit of **intent**; a task is the unit of
**work** — one repo, one branch, one thing a single worker can finish and
validate on its own. The decomposition is the judgement call, and it is yours.

```bash
./scripts/queue.sh add report-status-honestly drop-idle-default \
  --title 'Stop defaulting an unreported session to idle' \
  --repo /home/you/code/thurbox \
  --branch fix/drop-idle-default \
  --touches src/state.rs,src/session.rs
```

`--touches` is the paths you expect the task to change. It is a **risk signal
that gets reported**, never a reason to hold anything back — see §3.

## 2. Write the brief

`add` scaffolds `BRIEF.md` with the repo, the branch, the pointer back to
PROMPT.md and the result contract already filled in, and one placeholder:

```markdown
<!-- WRITE THE INSTRUCTIONS HERE -->
```

Replace it. **`dispatch` refuses a task that still carries it**, because a
worker sent an unwritten brief has nothing to do and will invent something.

Write it as if the reader knows nothing, because it does: workers share no
context with you and none with each other. State the goal, the constraints, and
what "done" looks like, from scratch. The scaffold already tells the worker that
other tasks are running beside it, not to read their briefs, and not to wait for
them.

## 3. Order — the part that is counterintuitive

Run `./scripts/queue.sh plan`. It answers two questions and refuses to blur them.

```text
ready: 3 task(s) — every one of them goes out now, there is no concurrency cap
    report-status-honestly/01-drop-idle-default   ...  fix/drop-idle-default
    report-status-honestly/02-document-the-states ...  fix/document-the-states
    report-status-honestly/04-log-state-changes   ...  fix/log-state-changes
    risk: .../01-drop-idle-default, .../04-log-state-changes all touch src/state.rs
          Overlap is a risk signal, not a reason to wait — dispatch
          them together and let the delivery path reconcile a rebase.

waiting: 1 task(s) — each held by a durable, recorded blocker
    report-status-honestly/03-render-detected-agent
        semantic-dependency on .../01-drop-idle-default: reads the
        detected_agent field 01 introduces
```

**The whole value is in that first block being big.** Most work needs no
ordering at all; the job is finding the small set that does and letting
everything else go at once. A queue that runs one task at a time is slower than
no queue, because it adds bookkeeping and removes nothing.

So: **serialize only for a concrete condition that makes independent progress
unsafe.**

```bash
./scripts/queue.sh block report-status-honestly/03-render-detected-agent \
  --on report-status-honestly/01-drop-idle-default \
  --kind semantic-dependency \
  --why 'reads the detected_agent field 01 introduces'
```

`--kind` is a closed set, and it is the doctrine written down:

| kind | when |
|---|---|
| `semantic-dependency` | this task consumes something the other introduces |
| `shared-external-state` | both mutate the same external state |
| `incompatible-migration` | the two migrations cannot be in flight together |
| `other` | another concrete condition — say what it is in `--why` |

"They edit the same file" is **not on that list** and cannot be spelled as one.
`block` refuses it and points you at `--touches`. Two agents editing one file in
two worktrees is an ordinary rebase, not a hazard; treating it as one is how a
controller ends up slower than no controller.

A blocker clears only when the task it names is genuinely `done` — a session
that stopped does not clear it, and neither does an abandoned task.

## 4. Dispatch — the whole ready set, in one go

```bash
./scripts/queue.sh dispatch --dry-run   # read the spawn commands first
./scripts/queue.sh dispatch
```

One invocation spawns every ready task. It passes `--on-existing fail` (a twin
would break by-name addressing for both, permanently), `--parent
$THURBOX_SESSION` so `session list --parent` enumerates your workers, and the
task's session profile from `./scripts/session-flags.sh`. Each worker is sent
one line pointing at the absolute path of its own brief — nothing is copied into
its worktree, so nothing can land in its PR.

If a spawn fails, the others still go. Re-run `dispatch`; the ones already out
are no longer `queued` and are not spawned twice.

### The trust dialog, handled here rather than remembered

Every spawn runs `./scripts/session-trust.sh` between `session create` and the
first `session send`. An agent started in a fresh worktree asks whether it may
work there, and sending the brief while that dialog is up types the brief INTO
the dialog — which is how every fleet-spawned worker used to break.

The script confirms the dialog is really there before sending a key, answers
with the sequence that agent needs (Claude's default selection is **`No, exit`**
— a bare Enter dismisses it), and confirms the dialog is gone. `thurbox-session`
§1b has the per-agent table and the config-seeding fallback.

When it cannot confirm, **nothing is typed and the task is left unprompted**:

```text
    prove-the-queue/01-write-alpha  -> 31b68505-…  NOT PROMPTED
        session-trust: no trust dialog seen in 20s and claude has not reported.

1 session(s) exist but were NOT prompted. Look at the pane, then retry the
handoff — nothing was typed into them:
    ./scripts/queue.sh prompt
```

Look at the pane (`thurbox-cli session capture <uuid>`), then `queue.sh prompt`
to retry the handoff. `cursor` and `muse` are not answered by a keystroke at
all — they take a launch flag, so spawn them under the `cursor-trusted` /
`muse-trusted` profiles instead.

## 5. Learn what happened — read, do not be interrupted

This is the part the captain changed, and the reason matters. `thurbox-cli
message send` is exact, but it **wakes** the recipient: an arriving worker
message injects into your terminal and interrupts whoever is talking to you.
So the queue splits completion into two things you READ:

```text
WHEN   ./scripts/queue.sh watch --for-secs 60
       Reads `thurbox-cli watch` — the event stream — from a saved cursor, so
       a restart misses nothing. Folds each transition into the task's
       progress.jsonl. Closes NOTHING.

WHAT   ./scripts/queue.sh collect
       Reads the result.md each worker wrote. Only this closes a task.
```

**Never treat a transition as a completion.** `watch` will tell you a task's
turn ended with no result file — that is a worker that stopped, hit an
approval, or crashed, and it is emphatically not a finished task. Closing it
would mark failed work as shipped. Go and look at the pane (`thurbox-cli
session capture`), or read `thurbox-session`'s state table before you judge it.

Run `watch` when you choose: between turns, when the operator asks, before a
`plan`. There is no cadence you owe it — the cursor means a long gap costs you
nothing but the wait.

After `collect`, run `plan` again. A blocker may have cleared, and the tasks it
was holding go out immediately.

## 6. The views, and keeping your context clean

```bash
./scripts/queue.sh list              # a line per task, grouped by topic
./scripts/queue.sh list --topic X    # one topic
./scripts/queue.sh show <ref>        # one task's whole record
```

`list` is what you read when someone asks what is in flight. **Do not read the
briefs.** They are each written for one worker and reading five of them is
exactly the mixing-up the queue exists to prevent. `show` when you need one.

A ref is `<topic>/<task>`, or a bare task id when only one topic has it.

**The operator has a third view you should point them at rather than narrate
into.** `./scripts/webui.sh ensure` serves the same records on localhost as a
page: topics classified by what their tasks are doing, each with its plan,
progress and outcome. It is a reader over these files, so it never disagrees
with `list`, and it lets someone watch a run without asking you and
interrupting whatever you are doing. When they ask "what is in flight" for the
third time, give them the URL.

It **displays and does not control** — there is no route that dispatches,
cancels or reorders. You remain the only thing that writes here. And a stop is
durable: `webui.sh stop` writes a flag that `ensure` honours forever after, so
do not clear it on their behalf.

## 7. Where this lives, and what that costs

Everything under `orchestration/queue/` is gitignored instance data — your
prompts, your briefs, your results. The machinery is tracked; the queue is not.
That split is what keeps `./scripts/update-from-template.sh` a fast-forward, and
it means **the repo does not back your queue up**. Say that plainly when someone
assumes otherwise. `.gitignore`'s header owns the full reasoning.

`orchestration/webui/` is the same kind of thing for the monitor: the port it
chose, its pid, its log and its down flag, all gitignored, all this instance's.

`./scripts/check.sh queue` validates your records and re-proves the ordering and
wake claims against a throwaway queue. It runs in the gate, so a change that
quietly makes the queue serialize by default fails there rather than in a run
six weeks later.

## The loop

1. `topic add` — the prompt, verbatim.
2. `add` one task per unit of work; `--touches` what each expects to change.
3. Write each `BRIEF.md`.
4. `block` only what a concrete condition makes unsafe to run in parallel.
5. `plan`, read the ready set, then `dispatch` — all of it, at once. Check the
   report for any session that was spawned but NOT prompted.
6. `watch` on your own cadence; `collect` when a result is waiting.
7. `plan` again. Review the PRs. `thurbox-cli session delete <uuid> --force` as
   each closes out, and record the run in `orchestration/runs/` as it happens.
