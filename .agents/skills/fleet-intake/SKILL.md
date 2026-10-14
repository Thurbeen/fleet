---
name: fleet-intake
description: 'Hand fleet a piece of work from any conversation: open a topic, decompose it into tasks, write each brief, dispatch your own first ready set when fuel is clearly above the reserve, and go back to your channel — the conductor keeps it moving and you are told the outcome. The one entry point for work: use whenever any session that is not the conductor wants work done in a repository fleet covers — Mission Control, a second terminal, a discussion session, a worker that needs more done, a chat front end — from any directory, instead of spawning a worker with thurbox-cli.'
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

**This is the one way work enters fleet, from any session.** Mission Control
is one origin among many, not the place work has to start. Anything that ends
in a pull request, a push, a note or a served document is a task: a session
that spawns its worker by hand with `thurbox-cli session create` instead
leaves fleet tracking nothing — no task, no outcome back to the origin, no
reap when it merges, nobody watching when it goes CONFLICTING. Hand-spawning
is for a session with no deliverable — a discussion, an investigation that
ends in a conversation — or for a project fleet does not cover, one neither in
its registry nor a local repository it works in; `thurbox-session` is the
reference for that, and for reading one session's state.

`fleet-queue` §1–§4 own the mechanics of every step below — intake, the
brief, the order, the dispatch — and are cited rather than restated. This is
what is an ORIGIN's in each.

## 1. A request becomes a topic

```bash
uv run --project <checkout> fleet queue topic add <slug> --prompt-file <file>   # the request, verbatim
uv run --project <checkout> fleet queue add <topic> <slug> --repo <path> --branch <branch> \
  --title '<title>' --brief-file <file>                                         # one per unit of work
```

**`<checkout>` is the control-plane checkout — the directory Mission Control
opens — and the records always go there, whatever directory you run from.**
In Mission Control's own session `<checkout>` is your working directory and
`uv run fleet queue …` is the same command. Anywhere else, name it:

- **A fleet worker** has it in its brief: every **Project memory** and
  **Waiting on purpose** line is `uv run --project <checkout> fleet …`.
- **Any other session** — a discussion, a worktree of fleet itself, another
  repository — reads it off the lead: the `cwd` of the Mission Control row in
  `thurbox-cli session list --json`, the session fleet's own guard resolves
  the control plane from. Where fleet linked its skills into
  `~/.agents/skills`, the directory two levels above this skill's resolved
  path is the same checkout.

`topic add` and `add` refuse only when run from ANOTHER checkout's copy of
fleet — a worktree of this repository — because records written there would
be a second queue nobody reads. The refusal prints the `uv run --project`
command to use instead; it is never a reason to spawn the worker yourself.
Pass `--repo` as an absolute path: a worker is spawned from the record, long
after your shell's directory stops meaning anything. Every `uv run fleet …`
below takes the same `--project <checkout>` outside Mission Control.

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
- **Spawn the worker yourself.** Work with a deliverable is a task, from
  whichever session wants it, however small; `thurbox-cli session create` is
  for sessions with none.
- **Edit fleet itself.** A change to this repository is a task like any
  other, on its own worktree; the checkout fleet runs from is never edited in
  place.
