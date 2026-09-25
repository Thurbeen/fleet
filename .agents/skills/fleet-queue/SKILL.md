---
name: fleet-queue
description: Turn a prompt into durable task records, dispatch every independent task at once, and learn what finished by reading a stream and a file instead of being interrupted. Use whenever the control plane is given work — especially work spanning several projects, several tasks, or several merges at the same time — whenever you are asked what is in flight, blocked or waiting, and for any of the queue's own verbs: topic add, add, plan, block, dispatch, prompt, send, attach, watch, collect, shepherd, reap, reviewed, abandon, refuel, list, show, archive, or the reconcile loop that runs them.
user-invocable: true
allowed-tools: Read, Edit, Write, Bash, Glob, Grep
---

## fleet-queue

A prompt is not a turn in this conversation. It is a **topic** on disk that
becomes **tasks** on disk, each with its own brief. `uv run fleet queue` owns
all of it: `scripts/lib/queue.py`'s docstring is the model, and `--help` on any
verb is that verb's flags. This skill is how to think while driving it.

`thurbox-session` is how ONE session is spawned, prompted and cleaned up, and
all of it still applies. This skill is the layer above: what work exists, what
order it goes in, and how you find out it finished. Where they disagree about
completion, this one wins: **workers write result files; they do not send mail.**

## The loop

1. `topic add` — the prompt, verbatim.
2. `add` one task per unit of work; `--touches` what each expects to change.
3. Write each `BRIEF.md`.
4. `block` only what a concrete condition makes unsafe to run in parallel.
5. `plan`, read the ready set, then `dispatch` — all of it, at once. Check the
   report for any session spawned but NOT prompted.
6. `watch` on your own cadence; `collect` when a result is waiting.
7. `shepherd` — as reflexively as `collect`, which tells you to. A pull
   request goes bad long after the worker that wrote it stopped.
8. `refuel` when a worker has been `working` far too long, or the operator
   says the fleet hit a limit. It reads the account's fuel first.
9. Or run none of 6–8 by hand: `uv run fleet reconcile ensure` keeps them
   ticking (§5d).
10. `plan` again. Review the pull requests; the operator merges every one
    `shepherd` did not. Sessions release themselves once their pull requests
    land. Write your judgement into the run log.

## Which checkout you are in

**The queue lives in the CONTROL PLANE's checkout** — the clone the Mission
Control session opens — and nowhere else. A second clone is normal (a control
plane with no `origin` of its own needs one workers can push from), and a
topic opened there is a second queue the TUI pane is right not to show. One
machine may also run several fleets, each a checkout with a Mission Control and
a queue of its own (`orchestration/fleet.example.conf`); another fleet's queue
is not yours to write, read or reason about.

```bash
uv run fleet queue root      # the queue this invocation would use, absolute
```

If that is not the control plane's checkout, go there. `topic add` and `add`
refuse elsewhere, naming both paths; everything else warns. `FLEET_QUEUE_DIR`
overrides all of it, verbatim, for a harness pointing at a throwaway queue.

## 1. Intake — a prompt becomes a topic

Do this before anything else with a new ask, including one that looks like a
single task and one phrased as a question — *find out why X* is a brief, not
an investigation you run here. A topic with one task costs nothing; a task
with no topic costs you the prompt.

```bash
uv run fleet queue topic add report-status-honestly \
  --title 'Make thurbox report agent status honestly' \
  --prompt-file -    <<'EOF'
<the ask, exactly as it arrived — do not summarise it>
EOF
```

Store the prompt verbatim: your summary is a lossy copy made when you
understood it least. `topic add` also opens this run's log (**The run log**,
below); do not make a second one.

Then decompose. A topic is the unit of **intent**; a task is the unit of
**work** — one branch, one thing a single worker can finish and validate on its
own. A task is still one unit when it **spans repositories** (`--add-repo`).

```bash
uv run fleet queue add report-status-honestly drop-idle-default \
  --title 'Stop defaulting an unreported session to idle' \
  --repo /home/you/code/thurbox \
  --branch fix/drop-idle-default \
  --touches src/state.rs,src/session.rs
```

`--touches` is the paths you expect the task to change: a **risk signal that
gets reported**, never a reason to hold anything back (§3).

Two things `add` refuses up front, because both used to fail at `dispatch`
and leave a task `queued` with a `task.yaml` to hand-edit:

- **`--branch` must not exist yet.** thurbox's `--worktree-branch` only ever
  CREATES a branch. A repo this machine cannot read is not asked, so a
  `--host` task still finds out at dispatch.
- **`--title` becomes the worker's session NAME.** thurbox makes a path
  segment of it: no `/`, `\`, `..`, leading `.`, and a 64-byte cap, judged on
  the RENDERED name with its glyph.

### `--add-dir` — a directory the worker only reads

`add --add-dir <path>` attaches another directory to the session as it is:
**no worktree, no branch, nothing to publish.** Repeatable, attached in the
order written. Use it for a sibling repository or docs tree the work has to
match. Nothing below `dispatch` verifies, lands or reaps one; the brief tells
the worker so. A second repository the worker must **commit** in is
`--add-repo`, because a commit fleet does not verify is the failure
verification exists to stop.

### `--add-repo` — a second repository the worker commits in

`add --add-repo <path>` or `--add-repo <path>@<base>` gives the task a second
repository with **its own worktree, on the same `--branch`**. Repeatable;
thurbox owns the `PATH@BASE` syntax and gets your string verbatim.

**The artifact model goes plural with it, and that is the whole cost.** The
publish method stays one per task, and everything downstream handles N:

| | with one repository | with N |
|---|---|---|
| the record | `artifact:` is a URL | `artifact:` is a list of `{repo, url}` |
| `result.md` | `artifact: <url>` | `artifacts:` — one line per repository path |
| `collect` | closes when the artifact verifies | closes only when **every** one does, naming the ones that did not |
| `reap` | `landed` when the artifact merged | `landed` only when **every** one merged |
| `shepherd` | watches that repository | watches every repository the task names |

A blocker still clears on `landed` only, so half a task merged releases no
dependent. The worker is launched in a per-session symlink workspace with
each repository a subdirectory, which the brief says; it keys its `artifacts:`
block by the absolute paths. A GitHub primary with a GitLab `--add-repo` is an
ordinary task: `scripts/lib/forge.py` verifies each artifact by the forge that
holds it. `note`, `served` and `none` stay single however many repositories
the worker had open.

### `--host` — running a task on another machine

`add --host <name>` takes a name from thurbox's `hosts.toml` (in the directory
`uv run fleet paths thurbox-config` prints) and moves the worker there: the
agent, its multiplexer window and its worktree all live on that machine, and
only the TUI stays here. Omit it and nothing changes.

```bash
uv run fleet queue add report-status-honestly build-the-arm-image \
  --title 'Build the arm64 image' \
  --host devbox \
  --repo /srv/code/thurbox \        # ON DEVBOX. Not a path here.
  --branch fix/build-the-arm-image
```

**`--repo` is a path on the host.** Nothing local reads it; `dispatch` asks
the host. `add` refuses a host that is not in `hosts.toml` (listing the ones
that are), one whose `multiplexer` fleet cannot speak (`tmux` is POSIX,
`psmux` is native Windows spoken to in PowerShell), and one with
`share_sessions = false`, since that switches off the delegation that lets
the trust dialog be answered.

**Credentials are never moved.** The host needs its OWN credentials for the
forge that repository lives on; the `forge` probe (§4) refuses a dispatch when
it has none. Forwarding your SSH agent fixes it and forwards every key the
agent holds — your call on that machine, never a dispatch's.

## 2. Write the brief

`add` scaffolds `BRIEF.md` with the repo, the branch, the pointers to
`PROMPT.md`, the standing policy and the result contract already filled in,
plus four sections:

| section | what goes in it |
|---|---|
| `What to do` | the goal, and every task-specific detail the worker cannot read off the repo |
| `Hard constraints` | what it must not do, and the concrete failure each constraint prevents |
| `Coordination` | the other tasks in flight it has to know about — `None.` when there are none |
| `Done means` | the checks that pass and the artifact that exists when the task is over |

Each arrives as `<!-- WRITE THE INSTRUCTIONS HERE -->`. Replace every one:
**`dispatch` refuses a task that still carries one**, naming the sections. The
check compares each section against what the scaffold wrote, so a brief that
quotes the placeholder while talking about it goes out.

`add --brief-file <file>` fills them from a file: its `## ` headings fill the
section each names, a heading that is not one of the four is kept as content,
and a body with no headings goes into `What to do`. **Write all four
headings**: `add` refuses a file that leaves one unwritten and creates
nothing. `None.` is a complete section.

Write it as if the reader knows nothing, because it does: workers share no
context with you and none with each other.

### The style contract

A brief is read once, by a worker with no context and a token budget.

- **Cut invented headings and rhetorical contrast.** A fifth heading is
  content that belongs under one of the four. `X, not Y` earns its place only
  where the reader would otherwise believe Y. Seven briefs written before this
  rule carried 33 of those and 20 invented headings, and none of it told a
  worker anything.
- **Cut persuasion.** The worker follows the brief; it does not have to be
  convinced that the task matters, that a decision was weighed, or that
  something is settled.
- **Keep every measured fact.** Counts, paths, sizes, exact values, command
  names, and the specific past failure a constraint exists to prevent. One
  brief carries a `20G` figure and the failure it came from, and those two
  facts are why its worker chooses the right gate. A brief is too long when it
  repeats itself or argues, never for being specific.
- **Do not restate standing policy.** The scaffold points the worker at
  `orchestration/queue/POLICY.md` — publish and verify, squash merge, who
  merges, the gate, one brief per worker, the result contract. Retyping it is
  how it drifts, and it measurably did across five hand-written briefs. A rule
  that turns out to be standing goes in POLICY.md, not in the brief in hand.
- **Nor the operator's preferences.** If `orchestration/queue/OPERATOR.md`
  exists, every brief already points at it. A preference true of every task
  belongs there — offer to put it there, do not copy it into a brief.

## 3. Order — the part that is counterintuitive

`uv run fleet queue plan` answers two questions and refuses to blur them:

```text
ready: 3 task(s) — every one of them goes out now, there is no concurrency cap
    report-status-honestly/01-drop-idle-default   ...  fix/drop-idle-default
    report-status-honestly/02-document-the-states ...  fix/document-the-states
    report-status-honestly/04-log-state-changes   ...  fix/log-state-changes
    risk: .../01-drop-idle-default, .../04-log-state-changes all touch src/state.rs
          Overlap is a risk signal, not a reason to wait — dispatch
          them together and let the delivery path reconcile a rebase.

waiting: 2 task(s) — each held by a durable, recorded blocker
    report-status-honestly/03-render-detected-agent
        held by semantic-dependency on .../01-drop-idle-default (queued):
        reads the detected_agent field 01 introduces
    report-status-honestly/05-read-the-tenant
        held by missing-credential outside the queue (az is authenticated for
        the billing tenant) — only `block --clear` releases it: the brief's first
        instruction reads Azure and `az account show` fails
```

The upstream's state rides along (`(queued)`) because a blocker on a `stuck`,
`failed` or `abandoned` upstream can never clear, and the line then says
`UNCLEARABLE`.

**The value is in that first block being big.** Most work needs no ordering;
the job is finding the small set that does. A queue that runs one task at a
time is slower than no queue. So **serialize only for a concrete condition
that makes independent progress unsafe:**

```bash
uv run fleet queue block report-status-honestly/03-render-detected-agent \
  --on report-status-honestly/01-drop-idle-default \
  --kind semantic-dependency \
  --why 'reads the detected_agent field 01 introduces'
```

`--kind` is a closed set (`block --help`): `semantic-dependency` (this task
consumes what the other introduces), `shared-external-state`,
`incompatible-migration`, `other`. "They edit the same file" is **not on it**:
`block` refuses it and points at `--touches`; two agents editing one file in
two worktrees is an ordinary rebase. `block <ref> --on <ref> --clear` removes
one blocker; a task can carry several, so clearing names which.

A blocker clears only when the task it names has **landed** — concluded AND
merged (§5b). A stopped session does not clear it, `done` with an open pull
request does not, and neither does an abandoned task.

### When the thing holding a task is not a task — `--condition`

On 2026-09-11 a task whose brief's first instruction read Azure sat with `az`
unauthenticated: nothing could be written down, so `plan` called it ready, the
reconciler woke the lead to dispatch it, and the only honest answer was to
refuse in conversation and leave the record silent. A condition blocker is
what that needed:

```bash
uv run fleet queue block vending-machine-egress-resume/01-vm-identity-reconciliation \
  --condition 'az is authenticated for the billing tenant' \
  --kind missing-credential \
  --why 'the first instruction in the brief reads Azure, and az account show fails'
```

Its kinds are their own closed set — `missing-credential`,
`awaiting-approval`, `closed-window`, `broken-dependency`, `undecided`,
`other` — because the four above describe a relationship between tasks and a
condition is not one. Reach for it when the reason is durable and nameable.
"I have not authorized this yet" is a fact about this moment: leave that task
out of the dispatch by naming refs (§4), which records nothing.

**Nothing clears a condition but you.** No timer, no `collect`, no `reap` and
no later dispatch releases it — a condition that expired on its own would put
back the silence it was recorded to break:

```bash
uv run fleet queue block <ref> --clear --condition 'az is authenticated for the billing tenant'
```

The cost is that a stale one holds a task forever, which is why `--why` is
required and why `plan`, `list`, `show`, `fleet status` and the pane all show
it — the pane draws it `⊘` rather than `↳`, because the wait has no actor but
you.

### When a task will never run — `abandon`

A condition that will never be true — the work shipped some other way, the
plan changed — is not a wait, and clearing it would make the task
dispatchable. Retire it:

```bash
uv run fleet queue abandon <ref>... --why 'superseded: shipped as one PR'
uv run fleet queue abandon --topic <topic> --why '...'   # every task not landed or abandoned
```

`abandoned` is the terminal state a pull request closed unmerged already
reaches (§5b); `--why` goes on the record. All or nothing: it refuses `landed`
always, and a `dispatched` task whose session thurbox still lists unless you
pass `--force`. It never touches a session; `reap` releases that one once its
agent is at rest. A blocker naming an abandoned task **stays blocked**;
`abandon` prints every dependant with both ways out — `block --clear --on` if
it can run without the work, `abandon` if it cannot.

## 4. Dispatch — the whole ready set, in one go

```bash
uv run fleet queue dispatch --dry-run   # read the spawn commands first
uv run fleet queue dispatch
```

One invocation spawns every ready task with `--on-existing fail` (a twin would
break by-name addressing for both, permanently), `--parent $THURBOX_SESSION`
(except a `--host` task: thurbox refuses a parent on another machine, so a
remote worker is enumerated by its task record instead), and the task's
profile from `orchestration/session-profiles.yaml` or the operator's
gitignored `session-profiles.local.yaml` beside it — where a task that wants
another model or thinking budget gets a profile of its own. Each worker is
sent one line pointing at the absolute path of its brief; nothing is copied
into its worktree, so nothing lands in its pull request.

If a spawn fails, the others still go. Re-run `dispatch`; the ones already out
are not spawned twice. A session you spawned by hand for a task is bound to it
with `fleet queue attach`.

### Naming refs, and the one thing they are for

```bash
uv run fleet queue dispatch report-status-honestly/01-drop-idle-default
```

Refs launch exactly those tasks and refuse one that is not ready. **Bare
`dispatch` is the norm.** Refs are for the case that is neither ready nor
blocked: the operator has not authorized a task yet. Writing that into the
record as a blocker is what this exists to stop — one was once recorded as
"Operator has not been asked whether to run it at all" and held its task until
someone deleted it by hand. Refs record nothing; a task left out is still
`queued` and the next bare `dispatch` sends it. Do not drip-feed: anything you
could write down belongs in `block`.

### A remote task is probed before it is spawned

Three questions of the host, in order; one NO leaves that task `queued` with
the probe named, so fixing the host and re-running `dispatch` sends it:

```text
    reachable   it answers ssh, in the shell its multiplexer says it speaks
    repo        --repo is a git checkout at that path ON THAT MACHINE
    forge       it has credentials of its own for the forge THAT repo's `origin`
                names — an ssh key, or a `gh` / `glab` login
```

The repo comes before its forge because which forge to prove a credential
against is a fact about that checkout's `origin`. A remote worker that starts
and then fails at its first `git` call looks exactly like an agent bug.

Then the brief, `PROMPT.md`, `POLICY.md` and (when it exists) `OPERATOR.md`
are copied into the worktree thurbox made there, since the absolute paths a
local worker is handed are not on that filesystem. The canonical copies stay
here. A remote worker writes `result.md` beside the brief it is reading and
deletes all of these copies before it commits.

### The trust dialog, handled here rather than remembered

Every spawn runs `scripts/lib/session_trust.py` in-process between `session
create` and the first `session send`. An agent started in a fresh worktree asks
whether it may work there, and sending the brief while that dialog is up types
the brief INTO it — which is how every fleet-spawned worker used to break. It
confirms the dialog is there, answers with that agent's keys, and confirms it
is gone; `thurbox-session` §1b has the per-agent table and the two agents
whose trust is a launch flag rather than a keystroke. When it cannot confirm,
**nothing is typed and the task is left unprompted**:

```text
    prove-the-queue/01-write-alpha  -> 31b68505-…  NOT PROMPTED
        session-trust: no trust dialog seen in 20s and claude has not reported.

1 session(s) exist but were NOT prompted. Look at the pane, then retry the
handoff — nothing was typed into them:
    uv run fleet queue prompt
```

Look at the pane (`thurbox-cli session capture <uuid>`), then `fleet queue
prompt`.

### 4a. Course-correcting a worker — `send`, and never `session send`

```bash
uv run fleet queue send <ref> 'Also update the changelog before you open the PR.'
```

**One line.** `session send` types the text and presses Enter, so a second
line fires the agent on the first; `send` refuses a newline and tells you to
point at a file. It answers the trust dialog first, exactly as dispatch does,
and a send into a session that has gone away is `NOT DELIVERED`, on the
record.

`thurbox-cli session send` leaves no trace, and the one honest signal is that
you know WHEN YOU SENT (`thurbox-session` §4c). So `send` writes the instant
down with the branch head as a baseline, and `list` and `show` compare against
two things a worker cannot fake: the branch head (read from the task's own
`repo`, so it works with the session reaped; `not checked` for a `--host`
task or a repo git cannot read) and a transition in `progress.jsonl` DATED
after the message.

```text
    02-worker-liveness  dispatched  /home/…/fleet  cccccccc-…
        messaged 12m ago · committed 4m ago
        messaged 12m ago · no transition since; no commit since
        messaged 12m ago — NOT DELIVERED: session-trust: no such session
```

**Read the middle line as a fact and nothing more.** "Nothing has moved" is an
observation; "the worker is stuck" is a guess, and the queue makes no guesses
about sessions. A worker that has not answered yet and one that never got the
message read the same, which is why `NOT DELIVERED` is a separate line. A
concluded task drops the line from `list` and keeps it in `show`. Nothing here
writes `state` or `outcome`.

## 5. Learn what happened — read, do not be interrupted

`thurbox-cli message send` **wakes** the recipient: an arriving worker message
injects into your terminal and interrupts whoever is talking to you. So
completion is two things you READ:

```text
WHEN   uv run fleet queue watch --for-secs 60
       Reads `thurbox-cli watch` — the event stream — resuming each task
       from its own progress.jsonl, so a restart misses nothing and one
       task's events never consume another's. Closes NOTHING.

WHAT   uv run fleet queue collect
       Reads the result.md each worker wrote. Only this closes a task, and
       it verifies that task's artifact before it does.
```

And a third thing, which happens LATER and is not a completion:

```text
RELEASE uv run fleet queue reap [--dry-run]
        Asks the forge whether each concluded task's pull request merged,
        moves the ones that did to `landed`, and deletes those sessions and
        their worktrees. `collect` runs it for you — §5b.

        uv run fleet queue reviewed <ref>
        The one release no forge can authorise: a `served` task's document
        is waiting on a READER — §5b.
```

A remote task completes the same way: `collect` fetches its `result.md` over
ssh into the task's own, and everything downstream sees a local file.

### `collect` verifies the artifact — you do not have to take the worker on trust

A worker reporting `shipped` claims two things, and the second is that it
published the way it was told. Twice it had not, and both were reported to
the operator as shipped. A method leaves no trace, so a task declares what its
publish must LEAVE BEHIND, and `collect` goes and looks:

| `--publish` | the worker produces | what collect asks |
|---|---|---|
| `attested` | a PR carrying an attestation | the forge: a PR from this task's branch, its body attesting the commit that would merge |
| `pr` | a PR by any means | the forge: a PR from this task's branch, open or merged |
| `push` | a commit on the base branch | git: that commit is an ancestor of `origin/<base>` |
| `note` | a review or comment on the task's `--target` | the forge: the note exists, was written by the account fleet runs as, and sits on that target |
| `served` | a document served to a READER expected to answer it | nothing about the document — but the task stands `open`, holding the session that can answer, until `fleet queue reviewed <ref>` |
| `none` | nothing fleet can check and nobody waiting — an issue filed, a machine swept | nothing: the URL is recorded, the task closes, nothing calls it verified |

**Pick the shape of the deliverable, not the nearest one that exists.** A task
that reviews or comments on a change request is `--publish note --target
<url>` — never `push` with "nothing to commit", which ten tasks once had to be
closed by hand for. A task that pushes to a pull request it did not open is
`--publish pr --target <that pull request>`. `add` refuses a `note` with no
target, a `push` with one, and a pull-request method aimed at an issue.

**A deliverable somebody will READ is `served`, never `none`.** The two look
alike, and the difference is that a reader is expected to ANSWER a served
document, so the worker's session has to still be there when they do. `none`
closed five such tasks the moment the document was served and reaped every
session, and every reader who sent one back was told nobody was listening.

**Those words are SHAPES and none is a tool.** `--how` is free text ("run
`/publish`", "use `make release`"), rendered into the brief's Publish line and
never parsed, which is what lets a task name a publisher fleet has never
heard of. The retired `no-mistakes` spelling still reads as `attested` for old
records; use `attested` for new tasks. `orchestration/publish.conf` holds the
operator's default (the tracked `publish.example.conf` ships `pr`). An explicit
`--publish` matching that default keeps its `HOW` command, so a code task can
name its required artifact shape without losing the command its worker must
run. A different method drops that command unless `--how` supplies one. What
an attestation LOOKS like is theirs too: `ATTESTATION_MARKER` in that file.

```text
    topic/02-document-the-states  shipped  https://…/pull/1001  [publish verified: attested]
    topic/03-render-detected-agent: NOT CLOSED — nothing proves this task published
```

| the check says | what collect does |
|---|---|
| the artifact is there | closes the task, marked verified |
| it is not, or not from this branch | **leaves the task OPEN** and says so |
| could not run | closes the task, and says the check could not run |

"Could not run" is the forge CLI absent, no network, a change request it
cannot read, or a base branch this machine cannot see; it must never read as a
pass or a fail, because an offline laptop still has to collect. `show <ref>`
prints the method and the verdict. A task spanning repositories is verified
once per repository, and one unverified repository holds the whole task open,
named.

The head-branch check is the one a worker cannot write for itself: "this
change request comes from this task's branch" is the forge's fact, which
closes the hole a worker pasting somebody else's good pull request would
open. A change request the forge reports **merged** closes its task even with
a stale attestation — whoever merged it answered "may this merge" — and the
stale one is kept on the record as a note, never a hold.

**A worker's `stuck` or `failed` is not the last word.** `collect` keeps
reading those tasks' `result.md` and acts when the outcome CHANGES: a worker
whose shell died mid-pipeline wrote `stuck`, recovered, and rewrote it
`shipped` with a pull request that had merged all along. The rewrite is read
like any first result.

When a task is held open: read the artifact, then send the worker back to
publish again. If you have read it and judged it good as it stands, `collect
--allow-unverified` closes it and records that you did. A task that keeps
needing it was declared the wrong shape.

### 5a. Shepherd the pull requests — the fourth thing

A task closes when its worker writes `result.md`. **The pull request goes on
living**: it turns `CONFLICTING` when the one under it merges, its checks
fail, a review lands, and none of that reaches the task.

```bash
uv run fleet queue shepherd --dry-run   # what it would dispatch and merge
uv run fleet queue shepherd             # do it; --topic / --ref narrow the repos, --no-merge holds the merge
```

**It asks the forge, not the records.** A task records ONE `artifact`, the
first pull request its worker reported; #25 was a *second* pull request from a
task whose artifact still pointed at the merged #23, and a shepherd reading
artifacts could never have seen it. So it asks for every open change request
on every repo the queue's tasks name — each `--add-repo` included — and each
gets exactly one of these:

| What the forge says | What happens |
|---|---|
| the head branch is in someone else's fork | reported, never merged, **never given an agent** |
| `mergeable: CONFLICTING` | a fixer is dispatched to rebase |
| a check failed | a fixer is dispatched to fix it |
| `reviewDecision: CHANGES_REQUESTED` | a fixer is dispatched to address it |
| an `attested` task's PR with no attestation for this head | a fixer is dispatched to publish again, naming that task's own command |
| attested, checks green, `MERGEABLE`, ours | **squash-merged**, in the allowlisted repos only |
| checks green, `MERGEABLE`, ours, nothing attested it | recorded `green`, reported `ready to merge — not attested; yours`, never merged by fleet |
| anything it could not read | reported, left alone |

A PR is tied to a task by its recorded `artifact` (for `pr` and `attested`
tasks only) or by its head branch; one matching neither is still classified
and merged, and named as belonging to no task. A remote task's PR is
classified and merged like any other, and its fixer is withheld, because the
fixer needs a checkout of the head branch and that one is on the host — send
the fix into that worker's own session, which §5b keeps alive for this.

**Dispatching the fixer is the point.** It gets a brief of its own — the
condition, which PR merged underneath it and what that deleted, and that the
fix updates the PR **in place** — on a checkout of the branch that already
exists. Three things it will not do:

- **Dispatch twice for one pull request.** The fixer is recorded on the task
  under `shepherd`; a second pass checks that session's liveness, not whether
  the condition still matches, since a PR can drift to another condition while
  the fixer is mid-fix. `--force` overrides once you have decided the first
  one is not coming back.
- **Interrupt a working session.** A PR whose own worker is `working` or
  `blocked` is left alone, and so is one merely *observed* (`running`,
  `uncovered`, `unreported` — `thurbox-session` §4a).
- **Guess.** No forge, no network, no thurbox: it says what it could not
  determine and carries on.

**Merging, the part that runs unattended.** A public repo has forks, so
"merge every open PR on a timer" has to survive a stranger opening one. Fleet
merges only in the repositories the operator named in
`orchestration/auto-merge.conf` — gitignored, absent by default, read every
pass; the tracked `auto-merge.example.conf` names NOTHING and owns the gates a
merge still clears (head branch in the repository, the opener can push there,
an attestation naming the CURRENT head, every check concluded and passed,
`MERGEABLE`). Entries are host-qualified. `FLEET_AUTO_MERGE_REPOS` in the
environment REPLACES the file. Everywhere outside the list it reports `ready
to merge` and stops.

`shepherd` is a sibling of `collect`, not part of it: `collect` reads local
files and works offline, and folding a session-spawning, forge-calling side
effect into it would make it fail for unrelated reasons. `collect` names it
whenever it closed a task that left a PR open; `shepherd --json` is the seam.

### 5b. `reap` — a session lives until its work lands, and not one turn longer

**The gate is the merge, not the conclusion.** `outcome: shipped` means a
change request is OPEN, and the session that opened it is the cheap way to
fix what review finds — reaping at collect time makes that fix cost a
re-spawn, a cold agent and the brief read from nothing. A `push` task has no
such gap: `collect` already asked git whether the commit reached the base
branch, so reap promotes it in the same pass.

| state | means | its session |
|---|---|---|
| `done` | the worker concluded; its change request is open, its served document awaits its reader, or its confirmed `push` commit is about to be promoted | **kept** |
| `landed` | the change request merged, the commit reached the base branch, or there was never an artifact | released |
| `abandoned` | the change request was closed unmerged, or you ran `abandon` (§3) | released once at rest; the work is NOT on main |
| `stuck` / `failed` | the worker gave up | **kept** — the session is the evidence, unless the worker rewrites its `result.md` with an outcome `collect` proves (§5) |

**A `served` task is the one `landed` cannot be asked of.** Fleet cannot poll
a server it did not start, and an idle session proves nothing — waiting for
feedback is what an agent at rest looks like. So it stands `open` with its
session kept, `reap` prints the remedy under it on every pass, and you close
it once the reader is done:

```bash
uv run fleet queue reviewed <ref> [--why …]
```

It is refused on a task that has not concluded. A `served` task that reports
`shipped` with no URL is held open like any unproven claim, and one that
produced nothing (`not-applicable`) lands at once — a wait on a reader who was
given no document is a wait nothing could end.

`landed` comes from asking the forge, never from a worker claiming it.
**Blockers clear on `landed`, not on `done`**: a task collected `shipped` once
released its dependents while its change request sat unreviewed. For a task
spanning repositories the words fold most-blocking first: one unreadable
leaves the task where it is, one open holds it, one closed unmerged makes the
task `abandoned`, and `landed` needs all of them.

```text
    topic/01-drop-idle-default   landed     https://…/pull/999 is merged
    topic/01-drop-idle-default   reaped     11111111-…  (idle)
    topic/02-document-the-states kept       thurbox says `working`; only idle, done, stopped are reaped
    topic/06-investigate-crash   kept       the worker's own verdict is `failed` — its session is the evidence
```

Before deleting anything it asks `thurbox-cli session get --json`. `idle`,
`done` and `stopped` are the only words it acts on: `running`, `uncovered`
and `unreported` are not the agent saying it is at rest, and treating them as
`idle` kills live work. Deletion is `session delete --force`
(`thurbox-session` §5 has why); the record keeps a receipt. A fixer's
checkout is git's, not thurbox's, so it is removed separately with `git
worktree remove` and no `--force` — one holding uncommitted work is kept and
reported.

**`collect` runs the reap itself.** Its gate is not collect's — nothing
collected a moment ago has merged — so it only acts on earlier work. `collect
--no-reap` records what landed and touches no session; `reap --dry-run`
writes nothing. It only considers sessions THIS QUEUE recorded, refuses the
lead's by name, and reads archived topics too (§6), because a keep is a
promise to look again.

**A remote session is asked about its HOST before its state.** thurbox has an
`unreachable` state its CLI never prints: `session get --json` on a session
whose machine has gone away answers with the state LATCHED before it went, so
a worker that last reported `idle` still reads `idle` hours later. The host is
probed first, and a session it cannot reach is kept:

```text
    topic/22-build-on-devbox     kept       unreachable: host devbox — No route to host
```

**Never treat a transition as a completion.** `watch` will tell you a turn
ended with no result file — a worker that stopped, hit an approval, or
crashed. Closing it would mark failed work as shipped. Look at the pane
(`thurbox-cli session capture`) or `thurbox-session`'s state table first.

Run `watch` when you choose; each task resumes from its own record, so a long
gap costs nothing but the wait. After `collect`, run `plan` again: a blocker
may have cleared.

### 5c. `refuel` — the account's fuel first, then the workers that ran dry

A worker that hits its agent's token limit **does not fail — it sits.** The
hook that would have said `idle` never fires, so thurbox reports `working`
for as long as you leave it: `watch` folds no transition, `collect` finds no
result, `reap` sees an unfinished task.

```bash
uv run fleet queue refuel --dry-run     # what it would restart, writing nothing
uv run fleet queue refuel               # every recorded session
uv run fleet queue refuel <ref>         # just that task's
```

**It asks the ACCOUNT before it looks at a single session.** That window is
a subscription every session on the account draws on: while it is spent,
restarting is worse than useless — each resumes, hits the same wall within
seconds, and burns the reset it was waiting for. Three concurrent pipeline
runs did that on 2026-08-29 and lost every step in flight.

It reads ONE WINDOW PER ACCOUNT the pass touches (`fleet_status.probe_fuel`).
An account is a PROVIDER — from the task's agent, or pinned by `FUEL_PROVIDER`
in `orchestration/agent.conf` — plus the environment that selects it, from
that agent's `ENV` line there. Two workers on one provider and two logins get
two readings; a task whose provider cannot be worked out is `undetermined`.
`fleet status`'s `FUEL` section reads every authenticated provider, so the two
can legitimately disagree. A quota that could not be read is `undetermined`
and nothing is acted on — the common case, since the vendor's endpoint
rate-limits and quota-axi says `stale` rather than serving old numbers; read
the `retry after` it prints and run it again.

**A dead pane is recovered outright.** `session get --json` reports
`hook_corroboration: dead` when the pane's command exited while
`remain-on-exit` kept the frame — what a failed `--resume` leaves. Do not
parse `session capture` for `Pane is dead`: that string wraps and survives in
scrollback. `session list` does not probe, so its `hook_corroboration` is
`null` ("not checked").

**With fuel in the account, one wedged live session is a conjunction**,
because either half alone gets it wrong: `hook_state: working` with
`hook_state_age_secs` past 30 min (alone: a SLOW worker) AND the agent's own
limit signal — its banner on the pane, or the `rate_limit` record in its
transcript, which outranks the pane wherever it can be read (alone: a limit it
may have come back from). `session get --json` carries no usage field.

The restart is `session stop`, a wait until no process holds the conversation
id, then `session start` (an in-place `session restart` re-spawns while the
old process may still hold the conversation, and the agent exits 1 with
`Session ID … is already in use`). A holder that survives the wait leaves the
session parked, recorded as `park` on the `refuels` receipt. Then dispatch's
own handoff, `session_trust.py` first. Every restart is recorded and **capped
at three**: a session that runs dry, resumes and runs dry again is a loop. A
`working` reported BEFORE the last restart is evidence from before it, so the
next pass gives the re-spawned agent a moment instead of spending the cap.

**A restart is neither a completion nor a failure.** `refuel` writes no
`state` and no `outcome`. The lead's own session is refused by name, and a
remote task's pane and transcript are on its host, so it is `undetermined`.

### 5d. `fleet reconcile` — the loop that runs 5, 5a and 5c for you

Everything in §5 is something you have to remember. On 2026-09-08 nobody did,
for one session: 19 of 20 progress timelines empty, three merges unnoticed
for forty minutes, and six workers sitting at a token limit that the OPERATOR
spotted.

```sh
uv run fleet reconcile ensure     # start it unless it is running or asked down
uv run fleet reconcile status     # ticking? since when? on what queue?
uv run fleet reconcile logs       # what it has been doing
uv run fleet reconcile stop       # durably down; only `start` brings it back
```

`AGENTS.md`'s reconciler section owns what it may and may not do, and
`scripts/lib/reconcile.py`'s docstring argues each interval. Two things are
about you:

- **It will type one line at you, and only ever this one:** that N tasks are
  ready and nothing will dispatch them. Treat it as `plan` already run —
  `dispatch`. Once per transition, never mid-turn.
- **Run the commands anyway when you want an answer NOW.** `collect` is
  idempotent; the loop only means you are rarely the first to notice.

## The run log — the queue writes the facts, you write the judgement

One log per topic, opened by `topic add`, refreshed by `dispatch`, `collect`
and `shepherd`. It exists because it used to not: two consecutive runs went
unrecorded while the instruction to keep one was there both times, so the gap
was the tool's.

```text
<!-- fleet:facts -->     everything between the fences is GENERATED — the task
   …                     table, what waited on what, what overlapped anyway,
<!-- fleet:facts:end -->  and a timeline from the records' own timestamps
```

Outside the fence is yours and nothing rewrites it: **Goal** in your own
words, **Decisions worth keeping**, **What went wrong**, **Outcome**. Write
into it while you still know it. The block is rewritten, not appended;
`uv run fleet queue run [<topic>]` is that refresh made explicit; delete the
fence and the log is yours entirely. Run logs are gitignored, so machine paths
and session ids are fine in them; `_TEMPLATE.md` beside them is tracked.

## 6. The views, and keeping your context clean

```bash
uv run fleet queue list              # a line per task, grouped by topic
uv run fleet queue list --topic X    # one topic, archived or not
uv run fleet queue list --archived   # only the topics the default view hides
uv run fleet queue list --all        # both
uv run fleet queue show <ref>        # one task's whole record, archived or not
```

`list` is what you read when someone asks what is in flight. **Do not read
the briefs.** Each is written for one worker, and reading five is the
mixing-up the queue exists to prevent. A ref is `<topic>/<task>`, or a bare
task id when only one topic has it.

### Finished topics archive themselves

A topic whose every task reached `landed` or `abandoned` gets an `archived`
timestamp in `topic.yaml`, written by the landing sweep. Archived topics leave
`list`, `fleet status` and the pane, each of which still prints how many it
hides. Nothing is moved: `show <ref>` reaches an archived task. `stuck` and
`failed` are not terminal for this — those sessions are evidence the operator
has to see. `reap` reads archived topics regardless, because a task that
lands while its worker is still `working` is kept for a later pass.

```bash
uv run fleet queue archive <topic>    # early — refuses if any task is live
uv run fleet queue unarchive <topic>  # put it back in every view
```

`add` onto an archived topic un-archives it, and so does `collect` when a
record written back over a landing reopens a task there.

**The operator has their own view: point them at it.** The TUI queue pane
(`F3`) draws the same records, so it never disagrees with `list`, and it lets
someone watch a run without interrupting you. It displays and does not
control: no key dispatches, cancels or reorders, and you remain the only
writer.

## 7. Where this lives, and what that costs

Everything under `orchestration/queue/` is gitignored working state, because
this repo is public. `README.md`, `POLICY.md` and `OPERATOR.example.md` are
the exceptions: standing documentation, which is why every brief can point at
the policy instead of carrying a copy. **The repo does not back your queue
up**; say so when someone assumes otherwise.

`uv run fleet check queue` re-proves the ordering and wake claims against a
throwaway queue, and reads none of your records — the gate reads no operator
state. `uv run fleet status --records` validates them, and `uv run fleet queue
check` lists every problem.
