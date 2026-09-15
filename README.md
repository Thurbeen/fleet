<!-- rumdl-disable MD041 -->
<!-- The banner is the first line by design; MD041 wants a level-1 heading. -->

![Fleet: operators at consoles in a red mission control room, a squadron of fighter craft above them, powered by thurbox][banner]

[banner]: media/fleet-banner.jpg

# fleet

fleet is a control plane for AI coding agents working across your GitHub and
GitLab repositories. You give it a goal. It splits the goal into tasks, runs
one agent session per task in the real repository, and hands you back pull
requests or merge requests to review.

It runs on [thurbox](https://github.com/Thurbeen/thurbox), a terminal UI for
agent sessions. This repository holds the machinery. The plan, the log and your
map of projects live in your clone and are never committed. The code changes
live on branches in the repositories they belong to.

## How it fits together

![Architecture diagram. You prompt Mission Control, the lead session, which
reads the registry and dispatches tasks into the queue. The queue gives each
worker a brief and gets back a result, and the queue pane shows it to you.
Workers open pull or merge requests on the forge. The reconciler updates the
queue, checks and merges on the forge, and wakes the lead. Below, the loop:
intake, brief, dispatch, watch, collect, shepherd, merge, reap, plus
refuel.](docs/fleet-architecture.svg)

Each box in the diagram:

- **You.** You give prompts, watch the pane, review change
  requests, and merge the ones fleet did not.
- **Mission Control.** The lead agent session. thurbox runs it for the `fleet`
  extension, opened on your clone, with [`FLEET.md`](FLEET.md) as its standing
  context. It turns your prompt into tasks, writes a brief for each one, and
  dispatches them. Only the lead dispatches tasks.
- **Queue.** Plain files under `orchestration/queue/`.
  [`scripts/queue.sh`](scripts/queue.sh) (or `uv run fleet queue`, the same
  command) is the only thing that writes them. A topic keeps your prompt word
  for word. Each task in it has four files: `task.yaml` (what is intended and
  where it stands), `BRIEF.md` (the worker's instructions), `progress.jsonl`
  (what happened) and `result.md` (what the worker concluded).
- **Workers.** `dispatch` starts one thurbox session per task, each in its own
  git worktree on a new branch of the target repository. A task can name a
  `--host`, and its session then runs on that machine over ssh. Workers share
  no context with the lead or with each other. Each one reads its brief,
  publishes its work, and writes `result.md`.
- **Forge.** Every question about a change request goes through
  [`scripts/lib/forge.py`](scripts/lib/forge.py): GitHub through `gh`, GitLab
  through `glab`. A repository is named by host and path, such as
  `github.com/you/app`, so self-hosted instances work the same way.
- **Reconciler.** [`scripts/reconcile.sh`](scripts/reconcile.sh) is a
  supervised loop that you start and stop. It runs the queue's `watch`,
  `collect`, `shepherd` and `refuel` on their own intervals. When a task
  becomes ready, it types one line into the lead's terminal. It never
  dispatches, and it changes the queue only through `queue.sh`.
- **Queue pane.** [`interface/fleet_queue.lua`](interface/fleet_queue.lua)
  draws the queue and your remaining agent quota in a thurbox column. It reads
  the same files and writes nothing.
- **Registry and settings.** `registry/owners.txt` lists the GitHub owners you
  work under. [`scripts/sync-registry.sh`](scripts/sync-registry.sh) turns it
  into a map of every repository, and `registry/context/<repo>.md` holds your
  notes on each project. The `orchestration/*.conf` files hold your publish,
  auto-merge and agent settings.

The loop along the bottom, one step at a time:

1. **intake**: the prompt becomes a topic, and the topic becomes tasks.
2. **brief**: the lead writes each task's `BRIEF.md` from scratch.
3. **dispatch**: every task with no blocker goes out at once.
4. **watch**: thurbox's event stream is folded into `progress.jsonl`.
5. **collect**: reads `result.md` and checks the artifact it names. Only this
   closes a task.
6. **shepherd**: looks at every open change request, sends a fixer to one
   that conflicts, fails its checks or has changes requested, and merges where
   you allowed it.
7. **merge**: a squash merge, by `shepherd` or by you.
8. **reap**: once the forge reports the merge, the task is `landed` and its
   session is deleted.

Beside the loop, **refuel** restarts a worker that stopped at its agent's token
limit, but only while the account still has quota.

## Quick start

This section is deliberately short. The
[onboarding skill](.agents/skills/fleet-onboarding/SKILL.md) owns setup and
checks each step as it goes.

1. Install. The one-line installer clones fleet, checks its dependencies and
   installs the thurbox extension:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/Thurbeen/fleet/main/install.sh | sh
   ```

   Pick a clone location you will keep: the extension records the path.
   `./scripts/preflight.sh` lists what fleet needs and what is missing.

2. Open thurbox, start the Mission Control session, and run:

   ```text
   /fleet-onboarding
   ```

   It finds your GitHub owners, builds the repository map, puts the queue pane
   on screen and starts the reconciler. Where the choice is yours, it asks.

## Day to day

**Give fleet a prompt.** Type what you want into the Mission Control session,
in plain words. The lead opens a topic, writes the briefs, and dispatches.

**Watch the pane.** `F3` shows and hides it. For a one-shot summary of quota,
queue, sessions, pull requests and checkout, run `./scripts/fleet-status.sh`.

![The queue pane in a thurbox column beside the session list: the account's
remaining quota as a bar, then running tasks grouped under their topics, one
row each.](media/fleet-queue-pane.gif)

**What happens by itself** while the reconciler is up:

- finished tasks are collected, and their artifacts checked against the forge;
- change requests that conflict, fail checks or get changes requested are
  handed to a fixer;
- attested, green change requests in repositories you allowed are
  squash-merged;
- merged work is marked `landed`, its session is removed, and tasks waiting on
  it become ready;
- the lead is told when there is something new to dispatch;
- a topic whose tasks have all landed or been abandoned is archived.

**What needs you:**

- **Merges `shepherd` did not take.** `orchestration/auto-merge.conf` names the
  repositories fleet may merge in. A fresh clone names none. Everything else,
  including change requests with no attestation and anything from a fork, is
  yours to review and merge.
- **Conditions.** A task can wait on something the queue cannot see, such as a
  login, an approval or a decision. Only a person clears that. Tell the lead
  when it holds.
- **Stuck or failed tasks.** Their sessions are kept so you can look at what
  happened and decide.
- **The reconciler itself.** `./scripts/reconcile.sh stop` keeps it down, even
  across a reboot, until you run `start`.
- **Updates.** After pulling a change to `FLEET.md`, `AGENTS.md` or the skills,
  the running lead has stale instructions. The
  [update-fleet skill](.agents/skills/update-fleet/SKILL.md) handles the
  hand-over.

## Concepts

| Term | Meaning |
| --- | --- |
| topic | One prompt, stored word for word, and the tasks it became. |
| task | One repository, one branch, one piece of work a single worker can finish and check. |
| brief | A task's `BRIEF.md`: goal, constraints and what "done" means, written for a worker that knows nothing else. |
| result | A task's `result.md`, written by its worker: an outcome (`shipped`, `stuck`, `failed` or `not-applicable`), the artifact, and a short note. |
| shipped / landed | `shipped` is the worker saying the artifact exists; once `collect` has checked that artifact, the task is `done` and its session is kept for review fixes. `landed` means the forge reports the change merged. Tasks blocked on it are released only then. |
| shepherd | The pass that keeps open change requests moving: fixers for broken ones, merges where allowed. |
| refuel | The pass that restarts workers stopped at a token limit, once quota allows. |
| reconciler | The supervised loop that runs `watch`, `collect`, `shepherd` and `refuel` so nobody has to remember to. |
| attestation | A JSON block in a change request's body that names the commit a publish pipeline checked. It counts only when it names the current head. fleet publishes its own changes with the [`publish`](https://github.com/LeTuR/publish) skill. |
| forge | Where change requests live: GitHub or GitLab, reached through `scripts/lib/forge.py`. |

## Where things live

The machinery is tracked. Everything a running fleet writes is gitignored,
because this repository is public; back up your clone if that content matters.
[`.gitignore`](.gitignore)'s header gives the reason for each entry.

| Path | Tracked | What it is |
| --- | --- | --- |
| [`FLEET.md`](FLEET.md) | yes | Standing context for the Mission Control session. |
| [`extension.toml.in`](extension.toml.in) | yes | The thurbox extension manifest, rendered to a gitignored `extension.toml` by `scripts/install-extension.sh`. |
| [`scripts/`](scripts/) | yes | Every command; each script's header is its full usage. [`check.sh`](scripts/check.sh) is the gate. |
| [`interface/fleet_queue.lua`](interface/fleet_queue.lua) | yes | The queue pane. |
| [`.agents/skills/`](.agents/skills/) | yes | Agent skills; `.claude/skills` is a symlink to this directory. |
| [`orchestration/queue/`](orchestration/queue/README.md) | README, `POLICY.md` and `OPERATOR.example.md` only | Topics and tasks. |
| `orchestration/queue/OPERATOR.md` | no | Your standing instructions to every worker; [`OPERATOR.example.md`](orchestration/queue/OPERATOR.example.md) is the form. |
| `orchestration/runs/` | [`_TEMPLATE.md`](orchestration/runs/_TEMPLATE.md) only | One log per topic. |
| [`orchestration/playbooks/`](orchestration/playbooks/) | yes | Reusable recipes for running thurbox. |
| [`orchestration/session-profiles.yaml`](orchestration/session-profiles.yaml) | yes | Named settings a worker session starts with. |
| `orchestration/reconcile/` | no | The reconciler's pid, heartbeat, log and flags. |
| `orchestration/publish.conf` | no | How tasks publish; [`publish.example.conf`](orchestration/publish.example.conf) is the form. |
| `orchestration/auto-merge.conf` | no | Repositories fleet may merge in; [`auto-merge.example.conf`](orchestration/auto-merge.example.conf) names none. |
| `orchestration/agent.conf` | no | Which agent workers run; [`agent.example.conf`](orchestration/agent.example.conf) is the form. |
| `orchestration/voice.conf`, `session-glyphs.conf` | no | What the lead calls you, and the marks on session names; [`voice.example.conf`](orchestration/voice.example.conf) and [`session-glyphs.example.conf`](orchestration/session-glyphs.example.conf) hold the defaults. |
| `registry/owners.txt` | no | GitHub owners the map covers; [`owners.example.txt`](registry/owners.example.txt) is the form. |
| `registry/repos.generated.yaml` | no | Generated repository map. Never edit it by hand. |
| `registry/context/<repo>.md` | [`_TEMPLATE.md`](registry/context/_TEMPLATE.md) only | Your notes on each project. |

## More

- [`AGENTS.md`](AGENTS.md): how an agent works inside this repository, and why
  the queue runs the way it does.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): the gate, review rules and squash-only
  merging.
- The skills:
  [fleet-onboarding](.agents/skills/fleet-onboarding/SKILL.md) (set up),
  [fleet-queue](.agents/skills/fleet-queue/SKILL.md) (run the queue),
  [thurbox-session](.agents/skills/thurbox-session/SKILL.md) (drive one
  worker),
  [fleet-pane](.agents/skills/fleet-pane/SKILL.md) (the pane) and
  [update-fleet](.agents/skills/update-fleet/SKILL.md) (catch up with
  `main`).

## License

MIT. See [LICENSE](LICENSE).
