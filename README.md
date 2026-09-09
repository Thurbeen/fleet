<!-- rumdl-disable MD041 -->
<!-- The banner is the first line by design; MD041 wants a level-1 heading. -->

![Fleet: operators at consoles in a red mission control room, a squadron of fighter craft above them, powered by thurbox][banner]

[banner]: media/fleet-banner.jpg

# fleet

A **control plane** for your work across GitHub. You hand it a goal; it splits
the goal into tasks, runs an AI agent session on each one in a real repo, and
gives you back pull requests to review.

It is one repo holding two things: a **map** of your projects, and the
**orchestration** of the agent sessions run against them, using
[thurbox](https://github.com/Thurbeen/thurbox). It keeps the plan and the log
and never the work itself — every branch lives in a worker's own git worktree,
in the repo that work belongs to.

## How it works

```text
      your prompt
           │
           ▼
        ┌───────┐        one topic on disk, your words kept verbatim
        │ topic │
        └───┬───┘
      ┌─────┼─────┐      one repo, one branch, one brief each
      ▼     ▼     ▼
    task  task  task
      │     │     │
      ▼     ▼     ▼      a thurbox worker session per task,
   worker worker worker  each in its own git worktree
      │     │     │
      ▼     ▼     ▼
     PR    PR    PR      you review; you merge
```

Independent tasks all go out at once — that is the point of it. Workers share
no context with you and none with each other, so each gets a brief written from
scratch, and each reports back by writing a file rather than by interrupting
you. You watch it happen in the monitor or the queue pane.

## Setup

```bash
git clone https://github.com/Thurbeen/fleet.git
cd fleet
```

Then open the clone in your agent CLI and run:

```text
/fleet-onboarding
```

The [onboarding skill](.agents/skills/fleet-onboarding/SKILL.md) does the setup
rather than instructing you through it — prerequisites, your GitHub owners, the
repo map, the thurbox extension, the queue pane and the monitor — verifying
each step and naming anything missing with its remedy before it writes a thing.
Run it twice and it converges. It hands you a guarded block to add yourself:
the pane's slot in your thurbox `layout.lua`.

Requires `gh` (authenticated), `jq`, and `thurbox-cli` **2.19.0 or newer**.

That done, open the Mission Control session in thurbox and give it a goal.

## Watching it

Two views of the same queue. Onboarding starts both, and both **display and do
not control** — `./scripts/queue.sh` stays the only thing that writes:

- **The monitor**, a local web page. `./scripts/webui.sh status` prints its
  URL; `./scripts/webui.sh stop` takes it down for good, `start` brings it
  back.
- **The queue pane**, the same view in a thurbox column, so you do not leave
  the terminal for it, with a bar for each subscription's fuel above the
  queue. `F3` opens and closes it.

Each shows four things per task and no fifth: the plan, the progress, the
outcome, and the pull request.

![The queue pane in a thurbox column beside the session list: two workers
running, the topics they are working on grouped above the finished ones, and
each task showing its plan, progress and outcome](media/fleet-queue-pane.gif)

## Your working copy

The machinery is tracked; what a running fleet writes is not. Your owners file,
your generated map, your project notes, your queue and your run logs live in
your working copy and are gitignored — this repo is public, and none of that is
something to publish, so back that copy up yourself if it matters beyond this
machine. `.gitignore`'s header names every path and the reason for each.

## More

- [`AGENTS.md`](AGENTS.md) — how an agent should operate inside this repo, and
  the reasoning behind how the queue runs.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — the gate (`./scripts/check.sh`), the
  squash-only merge policy, the layout conventions.
- Every script's header is its own full usage. `./scripts/fleet-status.sh`
  answers "where are we?" in one read-only call — fuel, queue, sessions, pull
  requests, monitor, checkout.

## License

MIT — see [LICENSE](LICENSE).
