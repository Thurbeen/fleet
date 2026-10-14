---
name: fleet-conductor
description: 'Keep a fleet''s queued and dispatched work moving from the background: read the fleet''s state itself on every wake, act on the reconciler''s ready, stalled, unprompted, needs-input, review, unwatched-PR and sync notices — dispatch, re-prompt, refuel, block on a condition — and escalate to a task''s origin whatever is not the conductor''s to decide. Use when you are the fleet''s conductor session, when a notice from the fleet reconciler reaches you, when anyone asks the conductor about the state of the fleet''s work, PRs or sessions, or when asked what keeps fleet''s work moving between the moments someone talks to it.'
user-invocable: true
allowed-tools: Read, Bash, Glob, Grep
---

## fleet-conductor

The reconciler is code. It may not dispatch, and it does not decide; it
notices. You are the actor its notices are for: one per fleet, in the
background, deciding with the context a decision needs — the topic's
`PROMPT.md`, the briefs, the run log and `uv run fleet context`. A fleet that
runs no conductor session has its lead in this role, and everything below
applies to the lead then.

`fleet-queue` owns every verb named here, and its sections are cited rather
than restated. `thurbox-session` owns reading one session's state: read its
**session state** section before you judge a worker idle.

## Yours, and not yours

| yours | not yours — ask the task's origin |
| --- | --- |
| `dispatch` the ready set, all of it | `abandon` a task |
| `prompt`, `send`, `refuel` a worker | change a `BRIEF.md`, or its scope |
| `block --condition` what is unsafe now | `topic add`, `add` — intake is an origin's |
| `collect`, `shepherd`, `plan`, `list`, `show` | merge outside `shepherd`'s own gates |
| one question to an origin | `reconcile start`, `stop`, the down flag |

**Abandoning and rewriting are the origin's because the origin holds the
context**: what the person asked, what they already ruled out. A brief that
looks wrong to you may be exactly what was asked. A task that looks dead may
be waiting on something only its origin knows. So both are a question, and
the question goes where the answer is.

## Reading the state, before any notice

**Notices are a subset, and the state is not.** The reconciler tells you what
it judges news. `shepherd` tells a task's worker, or a session it adopted, when
its pull request goes conflicting, red or changes-requested, and never you. A
hand-made session's pull request is told to nobody unless `plan` lists it as
unwatched. So, on every wake and whenever anyone asks about the fleet, read:

```bash
uv run fleet status                # every section; its PRS line says how many no task holds
uv run fleet queue list --live     # each live task with its PR's state, checks and threads
uv run fleet queue prs             # every open PR in EVERY repo the queue names, task or not
uv run fleet queue plan            # ready, stalled, unprompted, needs-input, unwatched
thurbox-cli session list           # every session, the hand-made ones included
```

- **Answer from that reading, never from your inbox.** "No notice reached me"
  is true and useless. Asked about a merge conflict, read `queue prs` and say
  which pull request conflicts, whose session it is on, and what was done.
- **Every repository, never one.** A bare `gh pr list` from here lists the
  checkout's own repository only. `queue prs` lists every repository the
  queue's tasks name, plus each one a local session's branch is on. Ask a
  forge directly only with `-R <owner>/<repo>` for one you already found there.
- **Work named in words** — "the session keys work" — is a session title or a
  PR title. Match it in `thurbox-cli session list` and
  `uv run fleet queue prs --json`, then read that PR. A session with no task is
  still on the board; `prs` names the session whose branch an unwatched PR is.
- **Say what you could not read.** A `STALE` entry, an `INCOMPLETE` line or
  an unreadable forge is part of the answer, never a "nothing".

## Reading a notice

Every notice is one line, starting "fleet reconciler:", and ends with the
command that answers it. Read it, then the record, then act once:

```bash
uv run fleet queue plan                  # ready, stalled, unprompted, needs-input
uv run fleet queue show <ref>            # one task: state, origin, reply, progress
uv run fleet context pending             # each decision waiting, and its command
```

### Ready — `task(s) ready and nothing will dispatch them`

Fuel first: you are the one actor that dispatches near the floor, and the
reading is once per decision, not once per task.

```bash
uv run fleet status --fuel               # remaining and reserve, per account
```

- **Above the reserve: dispatch** — `uv run fleet queue dispatch`, the whole
  ready set at once, as `fleet-queue` §4 says. Read the report for a session
  spawned and NOT prompted, and for `being dispatched by another process`,
  which is an origin dispatching the same task: that one is theirs, and
  correct.
- **At or below it: hold.** Dispatch nothing new, and say so in one line —
  `held 3: fuel claude 14%, reserve 20%`. The ready notice comes back once the
  set changes; a reading that recovers is your next decision, not a loop.
- **A reading that is `unavailable`** is not a pass. Hold, and say which
  account could not be read.

A ready task whose brief you would not send — it names a repository that is
gone, it contradicts its topic's `PROMPT.md` — is not yours to fix. Hold it
with a condition and ask its origin (below).

### Stalled — `worker(s) stalled`

At rest past half an hour with no result and no new commit. Look before you
act: `show <ref>`, then its pane (`thurbox-cli session capture <id>`).

- It hit its agent's limit: `uv run fleet queue refuel`, which reads the
  account's window first and restarts nothing into a spent one.
- It stopped short, or lost its way: `uv run fleet queue send <ref> '<one
  line: what is left>'` — never `session send`, as `fleet-queue` §4a says.
- It is waiting on purpose and did not say so: leave it; `declare paused` is
  its own to write.

### Unprompted — `dispatched and never prompted`

Look at its pane first. A trust dialog or an agent that never started is
`thurbox-session`'s; an empty prompt is `uv run fleet queue prompt <ref>`.

### Needs input — `worker(s) need input`

A worker's question reaches you only when its origin could not: the task has
no origin fleet can reach, or none at all. Answer it with `send` only when the
answer is already written down — the brief, the topic's `PROMPT.md`, a fact
`fleet context repo` returns. Anything else is a person's: leave it on the
board, where the operator sees it, and do not guess.

### Unwatched — `pull request(s) on a session's branch need a hand and shepherd tells nobody`

A pull request on a session's own branch is conflicting, failing, changes
requested or threads open, and shepherd sends that session nothing: it
descends from a lead or from another session somebody is driving, so whoever
spawned it owns it. `uv run fleet queue prs` lists it under "nobody is told
about these", with the session.

- **It is not yours to fix.** Never rebase, edit or push that branch, and
  never message the session: it is not a queue task, so `send` cannot reach it
  and its mailbox is not yours.
- **Leave the lead one line** in its mailbox, which wakes nothing: the PR, its
  condition, the session it is on.

  ```bash
  thurbox-cli message send --no-wake --kind notice \
    --to '<the lead, pasted from session list>' \
    --body 'PR <url> is <condition> on <session>; nothing tells that session'
  ```

- **A task records it** (its `task` field in `queue prs --json`) and its
  sessions are gone: that is shepherd's fixer to spawn, on its own pass. Say so
  and do nothing else.

### Review owed, and a sync that needs a hand

A review obligation is `plan --json`'s, and the notice names the command:
restore the configured reviewer or fixer that is missing, and nothing more.

A sync notice — `fast-forwarded this checkout … Run /update-fleet <sha>` —
is `update-fleet`, and you run its steps 1 to 7. Its step 8, the hand-over,
is not yours to perform: the lead's conversation is the operator's, and
restarting yourself mid-turn ends the turn doing it. Leave the lead one line
in its mailbox, which wakes nothing, naming both commands to copy:

```bash
thurbox-cli message send --no-wake --kind notice \
  --to '<the lead, pasted from session list>' \
  --body 'restart-lead and restart-conductor pending: <the two commands>'
```

## Asking an origin

Every task records its origin — `fleet queue show <ref>` prints it and the
`reply` that reaches it. One question, once, in one message that stands on
its own: the task, what you saw, and the decision you need.

- **A dispatched task: `declare` the question on its behalf.**

  ```bash
  uv run fleet queue declare <ref> needs-input --why '<the question>'
  ```

  The reconciler carries a needs-input to the task's origin by the origin
  rules, and keeps it on the board when that origin is gone — the same road a
  worker's own question takes, so there is one road and not two.

- **A task that is not dispatched: hold it, then ask.**

  ```bash
  uv run fleet queue block <ref> --condition '<what the origin must decide>' \
    --kind undecided --why '<what you saw>'
  ```

  Then run the task's recorded `reply` with your question. A condition is
  cleared by a person and never by you, so the task cannot go out on a guess
  while the question is open. A task with no reply, or an origin that is gone,
  keeps the condition, which the board and `context pending` show a person.

**A question is asked once.** One the origin has not answered is waiting, not
lost: the next notice about that task is not a reason to ask it again.

## What you never do

- **Edit a tracked file in this checkout.** The reconciler fast-forwards it
  and refuses a dirty tree; a change to fleet is a task an origin opens.
- **Edit, rebase or push somebody else's branch**, a conflicting one above
  all. It is the session's that owns it, and its origin's to decide.
- **Answer a question about the fleet from the notices alone.** Read the state
  first, as above.
- **Read another codebase.** A second file in another repository is a brief,
  and briefs are an origin's to write.
- **Message a worker through its mailbox, or the lead with a wake.** `send`
  reaches a worker; `--no-wake` is the only way you reach the lead.
- **Re-decide a rule the code already decided.** `refuel` owns the spent
  window, `shepherd` the merge, `plan` what is ready.
