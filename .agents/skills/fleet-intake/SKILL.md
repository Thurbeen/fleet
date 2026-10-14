---
name: fleet-intake
description: 'Hand fleet a piece of work from any conversation: open a topic, decompose it into tasks, write each brief, dispatch your own first ready set when fuel is clearly above the reserve, and go back to your channel — the conductor keeps it moving and you are told the outcome. Use whenever a conversation that is not the conductor takes a request for work in a repository fleet covers: Mission Control, a second terminal, a chat front end, any origin.'
user-invocable: true
allowed-tools: Read, Edit, Write, Bash, Glob, Grep
---

## fleet-intake

**An origin is any conversation that takes a request and hands it to fleet.**
There can be many: Mission Control, a second terminal, a chat thread served
by an agent. Each opens its own topic, writes its own briefs, dispatches its
own first ready set and goes back to its channel. It does not wait for the
work to finish. Keeping the work moving after that is the conductor's
(`fleet-conductor`), and in a fleet that runs no conductor session it is the
lead's.

`fleet-queue` §1–§4 own the mechanics of every step below — intake, the
brief, the order, the dispatch — and are cited rather than restated. This is
what is an ORIGIN's in each.

## 1. A request becomes a topic

```bash
uv run fleet queue topic add <slug> --prompt-file <file>    # the request, verbatim
uv run fleet queue add <topic> <slug> --repo <path> --branch <branch> \
  --title '<title>' --brief-file <file>                     # one per unit of work
```

**Every task you add is yours by default.** Inside thurbox, `add` records the
session it runs in as the task's `origin` (`thurbox:<session id>`) and a
`--reply` that reaches it, so a worker's question comes to you, and so does
the task's outcome. An origin fleet cannot name for itself — a chat thread
behind a relay — passes `--origin` and `--reply` explicitly;
`orchestration/owners.example.conf` says how such an origin is reached.

When the tasks span several owners or repositories and would start more than
two workers, confirm the subset with the person in one line before the first
dispatch.

## 2. Write each brief

Workers share no context with you or with each other, so each `BRIEF.md`
states the goal, the constraints and what done looks like, from scratch —
`fleet-queue` §2 is the contract, and `dispatch` refuses the scaffold's
placeholder. **The brief is the one thing about a task only you can change.**
The conductor will not: it asks you instead.

## 3. Dispatch your own first ready set — or leave it for the conductor

The 20% fuel reserve is checked by whoever dispatches, and `dispatch` reads no
fuel on purpose. With several origins and a conductor dispatching at once,
each could read 25% and together spend past the floor. So **an origin
dispatches only while the reading is CLEARLY above the reserve**, and the
conductor is the one actor that dispatches near it:

```bash
uv run fleet status --fuel                 # remaining and reserve, per account
uv run fleet queue plan                    # what is ready
uv run fleet queue dispatch --origin "thurbox:$THURBOX_SESSION"
```

**Clearly above is ten points over the reserve or more** — 30% with the
default 20% — on every account the tasks will spend, read fresh. A reading
below that, a `stale` one, or an `unavailable` one is not clearly above:
dispatch nothing, and say so in one line. The tasks stay `queued`; the
reconciler tells the conductor they are ready, and it decides once, with one
fuel reading, rather than several origins deciding at once.

**`--origin` is not optional here.** A bare `dispatch` sends every topic's
ready work, other origins' included. Your own origin string is what `add`
recorded — `thurbox:<your session id>` inside thurbox, or the `--origin` you
passed — and `fleet queue find --origin <it>` lists your tasks.
`--topic <slug>` scopes to one topic instead.

A task another actor is already sending prints `being dispatched by another
process` and writes nothing: that is the dispatch lock, and correct.

## 4. Go back to your channel

Tell the person what was accepted — the topic, its tasks, what went out and
what was left for the conductor — in a line or two, and stop. What happens
next reaches you without your asking:

- **A worker's question** arrives through the `--reply` route. Answer it with
  `uv run fleet queue send <ref> '<answer>'`, which reaches the worker without
  interrupting it.
- **The conductor's question** — a task it would not dispatch, a brief that
  looks wrong, a task that looks dead — arrives the same way. Abandoning and
  rewriting are yours to decide: `fleet queue abandon <ref>`, or edit the
  brief and clear the condition it was held on
  (`fleet queue block <ref> --condition '<it>' --clear`).
- **The outcome** arrives as one line when `collect` closes the task `shipped`
  and again when `reap` moves it to `landed` or `abandoned`. That is when the
  person hears back.

## What an origin does not do

- **Keep the work moving.** Ready, stalled and unprompted notices are the
  conductor's; you are not told them and need not watch for them.
- **Dispatch another origin's tasks**, or bare `dispatch` the whole queue.
- **Dispatch at or near the floor.** That decision is the conductor's.
- **Edit fleet itself.** A change to this repository is a task like any
  other, on its own worktree; the checkout fleet runs from is never edited in
  place.
