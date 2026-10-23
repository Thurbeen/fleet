<!-- rumdl-disable MD041 -->
<!-- The banner is the first line by design; MD041 wants a level-1 heading. -->

![Fleet: operators at consoles in a red mission control room, a squadron of fighter craft above them, powered by thurbox][banner]

[banner]: media/fleet-banner.jpg

# fleet

fleet is a control plane over [thurbox](https://github.com/Thurbeen/thurbox),
a terminal UI for agent sessions. It maps your repositories and runs a task
queue: you give it a goal, it splits the goal into tasks, and it runs one worker
session per task in the real repository. You get back pull or merge requests to
review.

Any conversation can hand fleet work; Mission Control, the lead session, is the
usual one. The conversation that asked is told each task's outcome. An optional
conductor session (`CONDUCTOR=on` in `orchestration/fleet.conf`) keeps queued
work moving in the background; without one, the lead does that too.

![Animated architecture diagram. You prompt Mission Control, the lead
session and one origin of many, which reads the registry and opens a topic of
tasks in the queue. Dispatch gives each worker, a thurbox session in its own
worktree, a brief; the worker writes back a result and opens a pull or merge
request on the forge, which is optional. The Kanban board shows the queue and
fuel from quota-axi. The reconciler collects, shepherds and refuels; it tells
an origin its tasks' outcomes and questions, and tells the conductor what is
ready, stalled or needs a sync. The opt-in conductor dispatches, re-prompts
and refuels; without one, the lead does. Below, the loop: prompt, topic,
tasks, dispatch, watch, collect, shepherd, merge, reap, plus refuel, served to
reviewed, and local-only publishing.](docs/fleet-architecture.svg)

## Release channels

**v1.0.0 is the frozen Python release.** `main` remains the stable v1
channel and the repository's default branch. The installers below, existing
clones, `sync-checkout`, and the reconciler's automatic sync stay on v1.
There is no automatic migration or replacement of v1 data or configuration.
Keep `main` on Python v1; never merge the Rust replacement into this channel
or change the default branch to v2. This also protects older installations
that have no release-channel guard.

**v2.0.0 is in development (beta), not a stable release.** The beta design
contains only Task and its task controller; forge,
ChangeRequest and conductor functionality are deferred. Rust development
targets `v2-beta`; [the Rust replacement PR](https://github.com/Thurbeen/fleet/pull/308)
targets that branch. It must not merge into `main`. The v2 CLI, resource
store and configuration differ from v1; v1 queues and configuration are not
inputs to v2. Testing beta is an explicit opt-in in a separate directory,
with separate configuration and data. Keep the v1 checkout and its running
services intact.

Until that PR lands, `v2-beta` holds the Python baseline for its review.
To try the current Rust candidate explicitly, clone the staging branch and
fetch the PR head as shown below. Install the toolchain specified by the
candidate's `rust-toolchain.toml` through rustup before building:

```text
git clone --branch v2-beta --single-branch https://github.com/Thurbeen/fleet.git fleet-v2-beta
cd fleet-v2-beta
git fetch origin pull/308/head
git checkout --detach FETCH_HEAD
cargo build --release --locked -p fleet-cli --bin fleet
```

Run the binary directly; do not replace a v1 command on PATH or run the beta
against a v1 directory. On Linux:

```sh
./target/release/fleet --help
```

On Windows PowerShell:

```powershell
.\target\release\fleet.exe --help
```

The PR head is a changing development candidate. Read its README before
configuring or running a Task. Building beta does not install it,
start it, or migrate v1. Return to v1 by continuing to use the original
checkout; no branch switch or reinstall is needed.

## Kanban dashboard

See the whole fleet without leaving thurbox. **Alt+K** opens six columns of
live queue records, from waiting tasks to working sessions, open change
requests, reader replies and work that needs your attention.

![Animated demo in thurbox: Mission Control beside the session list, Alt+K
opens the Kanban board, filters work that needs attention, searches a topic,
then Enter on a running card opens its worker session and Ctrl+X returns to
Mission Control.](media/fleet-board.gif)

The demo uses thurbox's **Doom** theme at 1920×1080. It shows the session list
on either side of the full-screen board, using synthetic sessions and records.
[Open the full-size animation](media/fleet-board.gif).

Kanban replaces the narrow fleet pane as the main queue view. The board
follows your light or dark theme and fits smaller terminals with
two bands of columns. Click cards or use arrows; Enter takes you to a live
worker or its queue record. Press **t** to search topics and **f** to show
fuel, which stays hidden by default. Filters and navigation are read-only.

## Requirements

- [thurbox](https://github.com/Thurbeen/thurbox), with tmux (psmux on Windows).
- git.
- An agent CLI that thurbox runs, such as Claude Code or Codex.
- Optional: `gh` or `glab`, to work with GitHub or GitLab.

The installer gets uv itself. `uv run fleet preflight` lists anything missing.

## Install

```bash
curl -LsSf https://raw.githubusercontent.com/Thurbeen/fleet/main/install.sh | sh
```

On Windows, type this at the PowerShell prompt:

```powershell
irm https://raw.githubusercontent.com/Thurbeen/fleet/main/install.ps1 | iex
```

Pick a clone location you will keep: the extension records the path. Running
the installer again is safe.

## First use

1. Open thurbox and start the Mission Control session.
2. Run `/fleet-onboarding`. It builds the repository map if you work on a
   forge, installs the Kanban dashboard and starts the reconciler. Where the
   choice is yours, it asks.
3. Type a goal into Mission Control in plain words.

`Alt+K` opens the full-screen Kanban board over your layout. Arrow keys and
clicks select cards; Enter focuses a live worker, or opens its queue record
when no session remains. `d` always
opens the record. Click the topic chip or press `t` to search topic names and
slugs in the bottom detail area while the board stays visible. Select with
Enter or a click. Esc cancels; All topics clears the
filter. Agent, needs-me and landed chips filter or fold the view. A coloured
dot per pull request — from the shepherd's last pass — sits on each card's
publish line, and sessions the shepherd adopted get a row of their own. Fuel is
hidden by default; click its chip or press `f` for compact gauges in two
columns. Esc returns from the record, then closes the board.

Running several fleets, here or on other hosts? Press `h` to cycle the fleet
filter: this fleet (the default), all of them, or one peer or host. Other
fleets' cards are read-only, and the header names any host that stopped
answering. thurbox's session list finds the other fleets; `uv run fleet peers`
shows what the board sees.

The board uses six columns, arranged in two bands below 180 terminal columns.
Recent landed work (the last 24 hours) starts folded. Links
use thurbox’s normal handling. Drag-selection in the overlay awaits thurbox
kernel support. It reads records and focuses sessions; it never
dispatches, merges or changes the queue. Ctrl+H/Ctrl+L remain thurbox’s native
pane cycle; card navigation uses arrows until [thurbox #1358](https://github.com/Thurbeen/thurbox/issues/1358).

The optional legacy column is installed alongside the dashboard for
compatibility. Onboarding still offers to place that column; choose **Skip**
to use only Kanban, which needs no layout slot. If your layout shows the
column, press **F3** to hide it. **Alt+K** is the dashboard shortcut, and
**Ctrl+X** takes you back to Mission Control from any session, a worker's
terminal included. With several fleets on one machine it reaches the one that
ran `uv run fleet install-extension` last.

## Read more

- [`AGENTS.md`](AGENTS.md): the operating guide — what is in this repository
  and how the queue runs.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): the gate, review rules and squash-only
  merging.
- [`.agents/skills/`](.agents/skills/): one skill per job — onboarding, the
  queue, the pane, updates and more.

## License

MIT. See [LICENSE](LICENSE).
