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
is how to think while driving it. Three things it buys: nothing is lost to a
context reset, independent work goes out all at once, and your context stays
clean — you read a line per task, and each worker reads one brief.

### Where it sits next to `thurbox-session`

`thurbox-session` is how ONE session is spawned, prompted and cleaned up, and
all of it still applies — `--on-existing`, session profiles, trust, multi-repo,
the state vocabulary. This skill is the layer above: what work exists, what
order it goes in, and how you find out it finished. When the two disagree about
completion, this one wins: **workers write result files, they do not send mail.**

### Which checkout you are in

**The queue lives in the CONTROL PLANE's checkout** — the clone the `fleet`
session opens — and nowhere else. A second clone of this repo is normal: a
control plane with no `origin` of its own needs one that workers can branch and
push from. Opening a topic there gives you a whole second queue the monitor is
right not to show.

Ask the tooling rather than the shell prompt:

```bash
./scripts/queue.sh root      # the queue this invocation would use, absolute
./scripts/webui.sh status    # the queue the dashboard is serving
```

Those two must name the same directory. If they do not, go to the one
`queue.sh root` reports as the control plane and work there. `topic add` and
`add` refuse outside it anyway, naming both paths, and every other command
warns — but the two lines above answer it before you type anything.
`FLEET_QUEUE_DIR` overrides all of it, verbatim and unguarded, for a harness
pointing at a throwaway queue.

## 1. Intake — a prompt becomes a topic

Do this before doing anything else with a new ask, including one that looks
like a single task, and including one phrased as a question rather than a
change — *find out why X* is a brief, not an investigation you run here. A
topic with one task costs nothing; a task with no topic costs you the prompt.

```bash
./scripts/queue.sh topic add report-status-honestly \
  --title 'Make thurbox report agent status honestly' \
  --prompt-file -    <<'EOF'
<the ask, exactly as it arrived — do not summarise it>
EOF
```

Store the prompt verbatim: your summary of it is a lossy copy made at the moment
you understood it least.

Then decompose. A topic is the unit of **intent**; a task is the unit of
**work** — one repo, one branch, one thing a single worker can finish and
validate on its own. The decomposition is yours.

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
PROMPT.md, the pointer to standing policy and the result contract already
filled in, plus four empty sections. That outline is the whole structure of a
brief; you supply content for it.

| section | what goes in it |
|---|---|
| `What to do` | the goal, and every task-specific detail the worker cannot read off the repo |
| `Hard constraints` | what it must not do, and the concrete failure each constraint prevents |
| `Coordination` | the other tasks in flight it has to know about — write `None.` when there are none |
| `Done means` | the checks that pass and the artifact that exists when the task is over |

Each arrives as the same placeholder:

```markdown
<!-- WRITE THE INSTRUCTIONS HERE -->
```

Replace every one of them. **`dispatch` refuses a task that still carries one**,
so a half-written brief is stopped as firmly as a blank one.

Write it as if the reader knows nothing, because it does: workers share no
context with you and none with each other. State the goal, the constraints, and
what "done" looks like, from scratch.

### The style contract

A brief is read once, by a worker with no context and a token budget. Two
kinds of writing inflate one without informing it, and a third looks like
padding and is the reason the worker gets it right on the first pass.

**Cut invented headings and rhetorical contrast.** A fifth heading means
content that belongs under one of the four. Inside a section, `X, not Y` — and
`is not`, `That is …`, `deliberately`, `on purpose` — earns its place only
where the reader would otherwise believe Y. Seven briefs written before this
rule ran to 1191 lines and carried 33 `X, not Y`s, 18 bare `is not`s and 20
invented headings; several of the headings were themselves the construction
("The lever, and it is the repo's own rule"). None of it told a worker
anything.

**Cut persuasion.** The worker follows the brief; it does not have to be
convinced. Drop the sentence explaining why the task is worth doing, the one
saying a decision was weighed carefully, and the one reassuring the reader
that something is settled. "Serialize with `queue.sh block`" carries
everything that "Serialize with `queue.sh block` — this is deliberate and the
right call" carries.

**Keep every measured fact.** Counts, file paths, sizes, exact token and
version values, command names, and the specific past failure a constraint
exists to prevent. One brief carries a `20G` figure and the failure it came
from, and those two facts are why its worker chooses the correct gate over the
obvious wrong one. **Deleting evidence to shorten a brief is the failure to
fear here**: it spends the thing that buys one-pass quality in order to buy
tokens. A brief is too long when it repeats itself or argues. It is never too
long for being specific.

Applied while writing: after each sentence, ask whether it states a fact the
worker will act on. If it names a number, a path, a command or a failure, keep
it. If it exists to frame, justify or reassure, delete it.

**Do not restate standing policy in a brief.** The scaffold already points the
worker at `orchestration/queue/POLICY.md`, by absolute path, and that file
holds everything true of every task: open the PR with `/no-mistakes --yes` and
what proves you did, squash merge, the operator merges and you do not, gate
locally first, one brief per worker, and the result contract. Retyping any of
it is how it drifts — it measurably did, across five briefs written by hand.
Task-specific detail still belongs here in full; long briefs are why workers
get it right on the first pass. Only the repetition moved.

If a rule turns out to be standing after all, put it in POLICY.md rather than
in the brief you happen to be writing.

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

**The value is in that first block being big.** Most work needs no ordering; the
job is finding the small set that does and letting everything else go at once. A
queue that runs one task at a time is slower than no queue, because it adds
bookkeeping and removes nothing. So **serialize only for a concrete condition
that makes independent progress unsafe.**

```bash
./scripts/queue.sh block report-status-honestly/03-render-detected-agent \
  --on report-status-honestly/01-drop-idle-default \
  --kind semantic-dependency \
  --why 'reads the detected_agent field 01 introduces'
```

`--kind` is a closed set:

| kind | when |
|---|---|
| `semantic-dependency` | this task consumes something the other introduces |
| `shared-external-state` | both mutate the same external state |
| `incompatible-migration` | the two migrations cannot be in flight together |
| `other` | another concrete condition — say what it is in `--why` |

"They edit the same file" is **not on that list** and cannot be spelled as one.
`block` refuses it and points you at `--touches`; two agents editing one file in
two worktrees is an ordinary rebase.

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
the dialog — which is how every fleet-spawned worker used to break. The script
confirms the dialog is really there before sending a key, answers with the
sequence that agent needs (Claude's default selection is **`No, exit`**, so a
bare Enter dismisses it), and confirms the dialog is gone. `thurbox-session` §1b
has the per-agent table and the config-seeding fallback.

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

`thurbox-cli message send` is exact, but it **wakes** the recipient: an arriving
worker message injects into your terminal and interrupts whoever is talking to
you. So the queue splits completion into two things you READ:

```text
WHEN   ./scripts/queue.sh watch --for-secs 60
       Reads `thurbox-cli watch` — the event stream — from a saved cursor, so
       a restart misses nothing. Folds each transition into the task's
       progress.jsonl. Closes NOTHING.

WHAT   ./scripts/queue.sh collect
       Reads the result.md each worker wrote. Only this closes a task, and
       it verifies that task's artifact before it does.
```

### `collect` verifies the artifact — you do not have to take the PR on trust

A worker that reports `shipped` with a pull request URL is making two claims,
and the second one used to go unchecked: that the pull request came through the
`no-mistakes` pipeline. Two did not, both were reported to the operator as
shipped, and he found it by reading the bodies himself. "Use the pipeline"
describes a METHOD, and a method leaves no trace — so `collect` checks the
trace the pipeline does leave, the five headings in the body:

```text
## Intent   ## What Changed   ## Risk Assessment   ## Testing   ## Pipeline
```

```text
    topic/02-document-the-states  shipped  https://…/pull/1001  [pipeline verified]
    topic/03-render-detected-agent: NOT CLOSED — its pull request skipped the pipeline
```

Three answers, and the third is not the second:

| the check says | what collect does |
|---|---|
| all five present | closes the task, marked verified |
| a heading missing | **leaves the task OPEN** and says so, loudly |
| could not run | closes the task, and says the check could not run |

"Could not run" is `gh` absent, no network, or a pull request it cannot read.
That must never read as a pass or a fail — CI and an offline laptop both still
have to collect. `queue.sh show <ref>` prints the verdict, so it survives the
scrollback.

When a task is held open: read the pull request, then send that worker back to
re-open it with `/no-mistakes --yes` and collect again. If you have read it
yourself and judged it good as it stands, `collect --allow-unverified` closes
it and records that you did.

**Never treat a transition as a completion.** `watch` will tell you a task's
turn ended with no result file — a worker that stopped, hit an approval, or
crashed. Closing it would mark failed work as shipped. Look at the pane
(`thurbox-cli session capture`) or read `thurbox-session`'s state table first.

Run `watch` when you choose: between turns, when the operator asks, before a
`plan`. The cursor means a long gap costs you nothing but the wait.

After `collect`, run `plan` again. A blocker may have cleared, and the tasks it
was holding go out immediately.

## 6. The views, and keeping your context clean

```bash
./scripts/queue.sh list              # a line per task, grouped by topic
./scripts/queue.sh list --topic X    # one topic
./scripts/queue.sh show <ref>        # one task's whole record
```

`list` is what you read when someone asks what is in flight. **Do not read the
briefs.** Each is written for one worker, and reading five of them is exactly
the mixing-up the queue exists to prevent. `show` when you need one.

A ref is `<topic>/<task>`, or a bare task id when only one topic has it.

**The operator has a third view: point them at it rather than narrating into
it.** `./scripts/webui.sh ensure` serves the same records on localhost — topics
classified by what their tasks are doing, each with its plan, progress and
outcome. It is a reader over these files, so it never disagrees with `list`, and
it lets someone watch a run without interrupting you. When they ask "what is in
flight" for the third time, give them the URL.

It **displays and does not control** — no route dispatches, cancels or reorders,
and you remain the only thing that writes here. A stop is durable: `webui.sh
stop` writes a flag that `ensure` honours forever after, so do not clear it on
their behalf.

## 7. Where this lives, and what that costs

Everything under `orchestration/queue/` is gitignored working state — your
prompts, your briefs, your results — as is `orchestration/webui/` for the
monitor. `README.md` and `POLICY.md` are the two exceptions: standing
documentation, not one operator's data, which is exactly why every brief can
point at the policy instead of carrying a copy. The machinery is tracked; the
queue is not, because this repo is public and none of that belongs in it. It
also means **the repo does not back your queue up**. Say that plainly when
someone assumes otherwise; `.gitignore`'s header owns the full reasoning.

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
