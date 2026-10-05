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

![Architecture diagram. You prompt Mission Control, the lead session, which
reads the registry and dispatches tasks into the queue. The queue gives each
worker a brief and gets back a result, and the queue pane shows it to you.
Workers open pull or merge requests on the forge. The reconciler updates the
queue, checks and merges on the forge, and wakes the lead. Below, the loop:
intake, brief, dispatch, watch, collect, shepherd, merge, reap, plus
refuel.](docs/fleet-architecture.svg)

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
2. Run `/fleet-onboarding`. It finds your repositories, puts the queue pane on
   screen and starts the reconciler, and asks where the choice is yours.
3. Type a goal into Mission Control in plain words.

`F3` shows and hides the queue pane.

![The queue pane in a thurbox column beside the session list: the account's
remaining quota as a bar, then running tasks grouped under their topics, one
row each.](media/fleet-queue-pane.gif)

## Read more

- [`AGENTS.md`](AGENTS.md): the operating guide — what is in this repository
  and how the queue runs.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): the gate, review rules and squash-only
  merging.
- [`.agents/skills/`](.agents/skills/): one skill per job — onboarding, the
  queue, the pane, updates and more.

## License

MIT. See [LICENSE](LICENSE).
