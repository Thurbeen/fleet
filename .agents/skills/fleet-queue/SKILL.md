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

**The queue lives in the CONTROL PLANE's checkout** — the clone the `mission
control` session opens — and nowhere else. A second clone of this repo is
normal: a control plane with no `origin` of its own needs one that workers can
branch and push from. Opening a topic there gives you a whole second queue the
monitor is
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

### `--host` — running a task on another machine

`add --host <name>` takes a name from thurbox's `hosts.toml` and moves the
worker there: the agent, its tmux window and its git worktree all live on that
machine, and only the TUI stays here. **Omit it and nothing changes** — a task
with no host takes the same path it always did.

```bash
./scripts/queue.sh add report-status-honestly build-the-arm-image \
  --title 'Build the arm64 image' \
  --host devbox \
  --repo /srv/code/thurbox \        # ON DEVBOX. Not a path here.
  --branch fix/build-the-arm-image
```

**`--repo` is a path on the host.** Nothing local reads it, so a path that
happens to exist on your machine tells you nothing about whether it exists on
theirs — `dispatch` asks the host, and refuses when the answer is no.

Three things follow, and each of them is a refusal you will meet rather than a
rule to remember:

| what | when | what you get |
|---|---|---|
| the host must be known | `add` | the name is checked against `hosts.toml`, and the refusal lists the hosts that do exist |
| POSIX hosts only | `add` | a host with a non-`tmux` `multiplexer` is how `hosts.toml` spells a Windows host, and is refused by name. Every remote command fleet runs is POSIX shell |
| session sharing must be on | `add` | `share_sessions = false` switches off the delegation that lets `session capture` see that pane, so the trust dialog could not be answered and the worker would stall unread |

**Credentials are never moved.** The host needs its OWN GitHub credentials to
clone, fetch and push; yours are not inherited and nothing sends them. Probe 2
below asks whether the host has any and refuses the dispatch when it does not.
Forwarding your SSH agent also fixes it and forwards every key that agent holds
— your call to make on that machine, not something a dispatch makes for you.

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
holds everything true of every task: publish the way the brief's Publish line
says and verify your own artifact, squash merge, the operator merges and you
do not, gate
locally first, one brief per worker, and the result contract. Retyping any of
it is how it drifts — it measurably did, across five briefs written by hand.
Task-specific detail still belongs here in full; long briefs are why workers
get it right on the first pass. Only the repetition moved.

If a rule turns out to be standing after all, put it in POLICY.md rather than
in the brief you happen to be writing.

**And do not restate the operator's preferences either.** If
`orchestration/queue/OPERATOR.md` exists, the scaffold points every brief at it
as well — that file is the operator's, not yours, so a preference they have
already written there is already delivered. When they tell you a preference
that is true of every task rather than this one, the answer is to offer to put
it in that file, not to copy it into the brief in hand.

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
        held by semantic-dependency on .../01-drop-idle-default (queued):
        reads the detected_agent field 01 introduces
```

The upstream's own state rides along in that line — `(queued)` here — because a
blocker on a `stuck`, `failed` or `abandoned` upstream can never clear, and the
line says so as `UNCLEARABLE` instead of reading like an ordinary wait.
`scripts/lib/queue.py`'s `blocker_line` is what every surface prints this from.

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

A blocker clears only when the task it names has **landed** — concluded AND its
artifact merged (§5b). A session that stopped does not clear it, `done` with an
open pull request does not, and neither does an abandoned task.

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

### A remote task is probed before it is spawned

A task with a `--host` gets three questions asked of that host first, in this
order, and one NO stops that task where it stands — still `queued`, so fixing
the host and re-running `dispatch` sends it:

```text
    reachable   it answers ssh, and answers as a POSIX shell
    forge       it has GitHub credentials of its own — an ssh key, or a gh login
    repo        --repo is a git checkout at that path ON THAT MACHINE
```

The report names the probe that failed. This exists because a remote worker
that starts and then fails at its first `git` call looks exactly like an agent
bug and is not one — and finding that out costs you a pane on another machine.

Then the brief, PROMPT.md, POLICY.md and (when the operator has one) OPERATOR.md
are each **copied to the host**, into the worktree thurbox made there, because
the absolute paths a local worker is handed are not on that filesystem. Each
canonical copy stays here and is still what `check` validates and `dispatch`
refuses when the brief is unwritten; what lands on the host is a copy, made
after that refusal has already had its say. A remote worker is told to write
`result.md` beside the brief it is reading, and to delete all of these copies
before it commits.

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
       Reads `thurbox-cli watch` — the event stream — resuming each task
       from its own progress.jsonl, so a restart misses nothing and one
       task's events never consume another's. Closes NOTHING.

WHAT   ./scripts/queue.sh collect
       Reads the result.md each worker wrote. Only this closes a task, and
       it verifies that task's artifact before it does.
```

And then a third thing, which happens LATER and is not a completion at all:

```text
RELEASE ./scripts/queue.sh reap [--dry-run]
        Asks the forge whether each concluded task's pull request merged,
        moves the ones that did to `landed`, and deletes those sessions and
        their worktrees. A `push` task has nothing left to ask — its commit
        was already confirmed on the base branch before `collect` closed it —
        so it lands in this same pass. `collect` runs it for you — see §5b.
```

**A remote task completes the same way.** `collect` fetches that worker's
`result.md` off its host over ssh and writes it into the task's own, then reads
it like any other. Everything downstream sees a local file and never learns
which machine wrote it — which is the point, and why a remote worker still does
not send mail.

### `collect` verifies the artifact — you do not have to take the worker on trust

A worker that reports `shipped` with a URL is making two claims, and the second
one used to go unchecked: that it published the way it was told to. Twice it
had not, both were reported to the operator as shipped, and he found it by
reading the bodies himself. "Use the pipeline" describes a METHOD, and a method
leaves no trace — so a task declares instead what its publish must LEAVE
BEHIND, and `collect` goes and looks for that:

| `--publish` | the worker produces | what collect asks |
|---|---|---|
| `no-mistakes` | a PR through the pipeline | the forge: a PR from this task's branch, its body carrying a `no-mistakes` attestation for the commit that would merge |
| `pr` | a PR by any means at all | the forge: a PR from this task's branch, open or merged |
| `push` | a commit on the base branch | git: that commit is an ancestor of `origin/<base>` |

`--how` is the other half and it is FREE TEXT — "run `/no-mistakes --yes`", "run
`/publish`", "use `make release`". It is rendered into the brief's Publish line
and **nothing ever parses it**, which is exactly what lets a task name a
publisher fleet has never heard of. Fleet knows the artifact's shape; your words
tell the worker how to make one.

You rarely type either. `orchestration/queue/POLICY.md`'s YAML frontmatter holds
this operator's default (`no-mistakes`, `run /no-mistakes --yes`), and every
task takes it unless `add` says otherwise — because a `--publish` forgotten on
one task would downgrade that task's verification in silence.

```text
    topic/02-document-the-states  shipped  https://…/pull/1001  [publish verified: no-mistakes]
    topic/03-render-detected-agent: NOT CLOSED — nothing proves this task published
```

Three answers, and the third is not the second:

| the check says | what collect does |
|---|---|
| the artifact is there | closes the task, marked verified |
| it is not, or not from this branch | **leaves the task OPEN** and says so, loudly |
| could not run | closes the task, and says the check could not run |

"Could not run" is `gh` absent, no network, a pull request it cannot read, or a
base branch this machine cannot see. That must never read as a pass or a fail —
CI and an offline laptop both still have to collect. `queue.sh show <ref>`
prints the method and the verdict, so both survive the scrollback.

**The head-branch check is the one a worker cannot write for itself.** Whatever
the body says, "this pull request comes from this task's branch" is a fact of
the forge — which closes the hole that reading prose never could: a worker
pasting somebody else's good pull request.

When a task is held open: read the artifact, then send that worker back to
publish again and collect again. If you have read it yourself and judged it good
as it stands, `collect --allow-unverified` closes it and records that you did.

### 5b. `reap` — a session lives until its work lands, and not one turn longer

Four worker sessions once accumulated on one machine. Three had merged pull
requests; the oldest had been idle for fifteen hours and its worktree held
twenty gigabytes. The loop already said "delete each session as it closes out"
— documented, manual, and therefore never done.

**The gate is the merge, not the conclusion, and that distinction was expensive
to learn.** For the two methods that end in a pull request, `outcome: shipped`
only means one is OPEN. Twice, a pull request collected as `shipped` turned out
to have been opened by hand rather than through the pipeline; the fix was a
follow-up to a session that was still alive, which cost a message. Reaping at
collect time would have made the same fix cost a re-spawn: a new worktree, a
cold agent, the brief read from nothing. A `push` task has no such gap —
`collect` refuses to conclude it `shipped` until it has already asked git
whether the commit reached the base branch (above, "`collect` verifies the
artifact"), so by the time one sits in `done` its work is already confirmed on
`main`, and reap's own pass promotes it to `landed` in that same run with
nothing left to ask the forge.

So a task gets a state AFTER `done`:

| state | means | its session |
|---|---|---|
| `done` | the worker concluded; its pull request is open, or its already-confirmed `push` commit is about to be promoted by this same `collect` run | **kept** — the cheap way to fix what review finds |
| `landed` | the pull request merged, the pushed commit reached the base branch, or there was never an artifact | released |
| `abandoned` | the pull request was closed unmerged | released; the work is NOT on main |
| `stuck` / `failed` | the worker gave up | **kept** — that session is the evidence, and you decide |

`landed` comes from asking `gh`, never from a worker claiming it, so it works
long after the session is gone. **Blockers clear on `landed`**, not on `done`
— a dependent task waits for the code to actually be on `main`, which is the
same bug in its other form: a task collected `shipped` once released its
dependents while its pull request sat unreviewed.

```text
    topic/01-drop-idle-default   landed     https://…/pull/999 is merged
    topic/01-drop-idle-default   reaped     11111111-…  (idle)
    topic/02-document-the-states kept       thurbox says `working`; only idle, done, stopped are reaped
    topic/06-investigate-crash   kept       the worker's own verdict is `failed` — its session is the evidence
```

Before it deletes anything it asks `thurbox-cli session get --json` and reads
the word. `idle`, `done` and `stopped` are the only three it acts on:
`running`, `uncovered` and `unreported` are not the agent saying it is at rest
(`thurbox-session` §4a), and treating them as `idle` kills live work. Deletion
is `session delete <id> --force`, because a plain delete only soft-deletes the
row and leaves the TUI to reap the window and worktrees on a sync that, run
headless, never comes — and freeing the disk is the whole point. The record
keeps a receipt, so `list` and `show` stop naming an id that no longer
resolves.

**`collect` runs the reap itself**, and that is deliberate: the failure being
fixed is exactly "a documented manual step that never ran", so the release
belongs in the command you already run rather than in one more you have to
remember. Its gate is not collect's — nothing collected a moment ago has a
merged pull request — so it can only ever act on work from an earlier pass.
`collect --no-reap` records what landed and touches no session;
`queue.sh reap --dry-run` says what it would do and writes nothing. Reach for
the dry run first whenever you are unsure.

It only ever considers sessions THIS QUEUE recorded. Your own session and
anything spawned by hand are not in the records; the lead's is refused by name
as well.

**A remote session is asked about its HOST before its state**, and this is the
one place where reading thurbox's word is not enough. thurbox has an
`unreachable` state and its CLI never says it — that word reaches the interface
and nothing else. `session get --json` on a session whose machine has gone away
answers with the state that was LATCHED before it went, so a worker that last
reported `idle` still reads `idle` hours later, and `idle` is reapable. So the
host is probed first, and a session it cannot reach is kept:

```text
    topic/22-build-on-devbox     kept       unreachable: host devbox — No route to host
```

That is a temporary outage, not a finished worker. Nothing is deleted, nothing
is recorded, and the next pass reaps it if the host comes back.

**Never treat a transition as a completion.** `watch` will tell you a task's
turn ended with no result file — a worker that stopped, hit an approval, or
crashed. Closing it would mark failed work as shipped. Look at the pane
(`thurbox-cli session capture`) or read `thurbox-session`'s state table first.

Run `watch` when you choose: between turns, when the operator asks, before a
`plan`. Each task resumes from its own record, so a long gap — or a run that
died half way, or a task dispatched while the stream was already open — costs
you nothing but the wait.

After `collect`, run `plan` again. A blocker may have cleared, and the tasks it
was holding go out immediately.

### 5c. `refuel` — the account's fuel first, then the workers that ran dry

A worker that hits its agent's token limit **does not fail — it sits.** The hook
that would have said `idle` never fires, so thurbox reports `working` for as
long as you leave it there: `watch` folds no transition, `collect` finds no
result, `reap` sees a task that is not finished. Nothing in the loop notices.

```bash
./scripts/queue.sh refuel --dry-run     # what it would restart, writing nothing
./scripts/queue.sh refuel               # every recorded session
./scripts/queue.sh refuel <ref>         # just that task's
```

**It asks the ACCOUNT before it looks at a single session, and that order is the
whole point.** The quota window it reads is the operator's own subscription —
the lead and every worker draw on it. It is the same reading `fleet-status.sh`
shows as `FUEL`, through the same function (`fleet_status.probe_fuel`), so the
gauge and this command can never disagree; it covers the `claude` account, and
a task running another agent is reported undetermined rather than guessed at. So while it is spent,
every session is stuck for the same reason, and restarting them is worse than
useless: each one resumes, hits the same wall within seconds, and burns the
reset it was waiting for. Three concurrent pipeline runs did exactly that on
2026-08-29 and lost every step in flight.

```text
    account claude     spent        0% remaining — five_hour resets 2026-09-09T02:10:00+00:00
      The account window is SPENT … The fleet is waiting on the window, not on
      any session … Nothing is touched until it comes back.
```

A quota that could not be read is `undetermined` — never a pass, never a
failure, and nothing is acted on. That is the common case, not an edge one: the
vendor's own quota endpoint rate-limits, and quota-axi says `stale` rather than
serving old numbers as current. Read the `retry after` it prints and run it
again; do not work around it.

**With fuel in the account, one wedged session is a conjunction**, because
either half alone gets it wrong:

| half | read from | on its own it means |
|---|---|---|
| the state is stale | `hook_state: working` with `hook_state_age_secs` past 30 min | a SLOW worker — and slow is not dry |
| the agent says so | its limit banner on the pane, or the `rate_limit` record in its transcript | a limit it may already have come back from |

The transcript outranks the pane wherever it can be read: keyed by
`agent_session_id`, it is the same event recorded rather than rendered, and it
names the window that rejected the turn and when that window resets. `session
get --json` carries no usage field at all — do not look for one.

The restart is `session restart` (kills the window, re-spawns with `--resume`,
so the conversation and the brief survive) followed by dispatch's own handoff:
`session-trust.sh` first, because a re-spawned agent in a worktree can ask the
trust question again and sending into that dialog types the prompt INTO it.
Every restart is recorded on the task and **capped at three** — a session that
runs dry, resumes and runs dry again is a task too big for its window, and a
fourth restart is a loop rather than a recovery. A `working` state that was
reported BEFORE the last restart is evidence from before it, so a second pass
minutes later gives the re-spawned agent a moment instead of spending the cap
on one wedge.

**A restart is neither a completion nor a failure.** `refuel` writes no `state`
and no `outcome`; `collect` stays the only thing that closes a task. The lead's
own session is refused by name, and a remote task's pane and transcript are on
its host, so that one is reported `undetermined` rather than guessed at.

## 5a. Shepherd the pull requests — the fourth thing

A task closes when its worker writes `result.md`. **The pull request it named
goes on living.** In one day this control plane lost three round trips to that
gap: #14 went `CONFLICTING` the moment #13 merged and nothing noticed; #11 and
#12 were opened outside the pipeline and nobody saw for hours; a pipeline
review finding sat in a PR body until a human read it out. Every one was a
person noticing something a machine could have.

```bash
./scripts/queue.sh shepherd --dry-run   # what it would dispatch and merge
./scripts/queue.sh shepherd             # do it
```

**It asks the forge, not the records.** A task records ONE `artifact` — the
first pull request its worker reported. #25 was a *second* pull request from a
task whose artifact still pointed at the already-merged #23, so a shepherd
reading artifacts could not see it and the unattended pass would never have
merged it; a PR opened outside the queue was invisible the same way. So it runs
`gh pr list --state open` against every repo the queue's tasks name, and each
open pull request gets exactly one of these:

| What `gh` says | What happens |
|---|---|
| the head branch is in someone else's fork | reported, never merged, **never given an agent** |
| `mergeable: CONFLICTING` | a fixer is dispatched to rebase |
| a check failed | a fixer is dispatched to fix it |
| `reviewDecision: CHANGES_REQUESTED` | a fixer is dispatched to address it |
| no attestation for this head commit | a fixer is dispatched to re-run `/no-mistakes --yes` |
| attested, checks green, `MERGEABLE`, ours | **squash-merged**, in the allowlisted repos only |
| anything it could not read | reported, and otherwise left alone |

A PR is tied back to a task by its recorded `artifact` or by its **head
branch** matching the task's. One that matches neither is still classified and
still merged — it simply has no session to send a fixer into, and the output
names it as belonging to no task rather than passing over it in silence.

**A remote task's pull request is classified and merged like any other, and its
fixer is withheld.** The fixer needs a checkout of the PR's head branch, and a
remote task's checkout is on its host; spawning there is not yet built. The
shepherd says so by name rather than reporting the host's repo as "not a git
checkout", which is true and sends you looking in the wrong place. Send the fix
into that worker's own session while it is still alive — which is exactly what
§5b keeps it alive for.

**Dispatching the fixer is the point.** A status report would have saved none
of those three round trips, because noticing was never the expensive part. The
fixer gets a written brief of its own — the condition, which PR merged
underneath it and what that deleted, and that the fix updates the PR **in
place** — and it lands on a checkout of the branch that already exists, so the
push reaches the pull request that is already open.

Three things it will not do, and they are what make it safe to run:

- **It will not dispatch twice for one pull request.** The fixer it sent is
  recorded on the task under `shepherd`; a second pass checks that session's
  liveness, not whether the condition still matches — a PR can drift to a
  different condition while the fixer is mid-fix, and that drift never reads
  as nobody being on it. A liveness check that comes back unknown is left
  alone rather than guessed. `--force` overrides, once you have decided the
  first one is not coming back.
- **It will not interrupt a working session.** A PR whose own worker is
  `working` or `blocked` is left alone. So is one whose state is merely
  *observed* — `running`, `uncovered`, `unreported` are not the agent saying it
  is at rest (`thurbox-session` §4a).
- **It will not guess.** No `gh`, no network, no thurbox: it says what it could
  not determine and carries on. A PR it could not read is never called broken
  and never called ready.

**On merging, which is the part that runs unattended.** `Thurbeen/fleet` is
public and has a fork, so "merge every open PR on a timer" has to survive a
stranger opening one. Fleet merges only in the repos on `AUTO_MERGE_REPOS` in
`scripts/lib/queue.py` — `Thurbeen/fleet` — and only when **all** of these
hold:

- **The head branch is in that repository**, not a fork. A stranger cannot
  create a branch here, so this is the one claim about a pull request that
  whoever opened it cannot write for themselves.
- **Whoever opened it can push there.** Anyone with read access can open a
  pull request between two branches that already exist, and the body would
  then be theirs to write.
- **A `no-mistakes` attestation naming its CURRENT head commit.** Not the five
  `## ` headings — those are text anyone can paste, so counting them let a
  body authorise its own merge. The attestation is an HTML comment carrying
  the commit the pipeline ran on and a status per step; one from an earlier
  push is refused, because a verdict is about the code it saw.
- **Every check concluded and passed, and GitHub says `MERGEABLE`.**

A PR failing any of them is not merged, and one that is not ours is not given
an agent either. Everywhere outside the allowlist it reports `ready to merge`
and stops, which is what every repo did before that list existed.

**Run it the way you run `collect`.** It is a sibling and not part of it —
`collect` reads local files and works with the network down, and folding a
session-spawning, GitHub-calling side effect into it would make it fail for
reasons unrelated to what it was asked. So `collect` names it whenever it
closed a task that left a PR open, and `shepherd --json` is the seam anything
else reads it through.

## 6. The views, and keeping your context clean

```bash
./scripts/queue.sh list              # a line per task, grouped by topic
./scripts/queue.sh list --topic X    # one topic, archived or not
./scripts/queue.sh list --archived   # only the topics the default view hides
./scripts/queue.sh list --all        # both
./scripts/queue.sh show <ref>        # one task's whole record, archived or not
```

`list` is what you read when someone asks what is in flight. **Do not read the
briefs.** Each is written for one worker, and reading five of them is exactly
the mixing-up the queue exists to prevent. `show` when you need one.

A ref is `<topic>/<task>`, or a bare task id when only one topic has it.

### Finished topics archive themselves

A topic whose every task reached `landed` or `abandoned` gets an `archived`
timestamp in its `topic.yaml`, written by the landing sweep `collect` and
`reap` run. Archived topics **leave every default view** — `list`,
`fleet-status.sh`, the monitor and the TUI pane — and each of those still
prints how many it is hiding, so a short queue is never mistaken for an idle
one. Nothing is moved or deleted: it is a flag and a filter, and `show <ref>`
reaches an archived task with no unarchiving first.

`stuck` and `failed` are **not** terminal for this. Those sessions are kept as
evidence (§5b) and the operator has to see them, so one of either keeps the
whole topic in view.

```bash
./scripts/queue.sh archive <topic>    # early — refuses if any task is live
./scripts/queue.sh unarchive <topic>  # put it back in every view
```

`queue.sh add` onto an archived topic un-archives it, so you can never dispatch
into a topic no view draws.

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
monitor. `README.md`, `POLICY.md` and `OPERATOR.example.md` are the three
exceptions: standing documentation, not one operator's data, which is exactly
why every brief can point at the policy instead of carrying a copy. The
operator's own `OPERATOR.md` is ignored with the rest — theirs to write, read by
every worker whose brief was scaffolded while it existed. The machinery is tracked; the
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
7. `shepherd` — as reflexively as `collect`, and it is what `collect` tells you
   to do. A PR goes bad long after the worker that wrote it stopped.
8. `refuel` when a worker has been `working` far too long, or when the operator
   says the fleet has hit a limit. It reads the account's fuel first and
   restarts nothing while that is spent.
9. `plan` again. Review the PRs; the operator merges every one `shepherd`
   did not. Sessions release
   themselves once their pull requests land — `collect` reaps, `reap
   --dry-run` shows you what it would do — and you record the run in
   `orchestration/runs/` as it happens.
