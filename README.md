<!-- rumdl-disable MD041 -->
<!-- The banner is the first line by design; MD041 wants a level-1 heading. -->

![Fleet: operators at consoles in a red mission control room, a squadron of fighter craft above them, powered by thurbox][banner]

[banner]: media/fleet-banner.jpg

# fleet

A **control plane** for your work across GitHub and GitLab. You hand it a goal;
it splits the goal into tasks, runs an AI agent session on each one in a real
repo, and gives you back change requests — pull requests, merge requests — to
review.

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
you. You watch it happen in the queue pane.

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
rather than instructing you through it — seven steps: dependencies, this
checkout, your GitHub owners, the repo map, the thurbox extension, the queue
pane on screen, and the reconciler. It verifies each one and names anything
missing with its remedy before it writes a thing. Run it twice and it
converges.

Four of those steps ask you something, and only four. Whether to install the
dependencies that are missing; which of the owners it found on your machine the
map should cover; where the queue pane goes (a column on the right, by
default); and whether to bring the reconciler up. It reads your `gh` session,
your git config and the remotes of the clones you already have, so the owners
step is a list to confirm rather than one to type.

What it needs, and what it will tell you itself:

```bash
./scripts/preflight.sh            # every dependency, in three tiers, with why
./scripts/preflight.sh --commands # exactly what to run for the ones missing
```

`git`, `gh` (authenticated), `jq`, `python3` with PyYAML and `thurbox-cli`
**2.19.0 or newer** are required; `quota-axi` and `glab` are recommended, and
each names what degrades without it. `gh` is not optional even on a GitLab-only
fleet — it is what builds the repo map.

That done, open the Mission Control session in thurbox and give it a goal.

## Watching it

**The queue pane** is the live view: the queue in a thurbox column, so you do
not leave the terminal for it, with a bar for each subscription's fuel above
it. Onboarding installs it and `F3` opens and closes it. It **displays and
does not control** — `./scripts/queue.sh` stays the only thing that writes.

It reads the same four files per task and no fifth: the plan, the progress, the
outcome, and the change request. A column is narrow, so it draws only what you
would act on from a glance and leaves the rest to `./scripts/queue.sh show`.

![The queue pane in a thurbox column beside the session list: the account's fuel
drawn as a labelled bar with its reserve above the queue, then six running
topics with each task collapsed to one row — its ordinal, title and age, landed
and abandoned tasks folded into a muted count. Beside it the session list holds
one mission-control lead and the workers it spawned as its children, each
marked with an emoji](media/fleet-queue-pane.gif)

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
  requests, checkout.

## License

MIT — see [LICENSE](LICENSE).
