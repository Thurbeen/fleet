<!-- rumdl-disable MD041 -->
<!-- The banner is the first line by design; MD041 wants a level-1 heading. -->

![Fleet: fighter craft over a volcanic canyon, powered by thurbox][banner]

[banner]: media/fleet-banner.jpg

# fleet

A **control plane** for your work across GitHub: one repo that holds the *map* of
your projects and the *orchestration* of AI agent sessions run against them,
using [thurbox](https://github.com/Thurbeen/thurbox). It holds the plan and the
log and never the workers' branches — real work happens in thurbox worker
sessions, each in its own git worktree in a real repo.

What this repo accumulates is the machinery and the playbooks. What a running
fleet writes — run logs, project context, the generated map, the queue — is
working state and is gitignored, so it lives in your working copy and is not
backed up here. This repo is public, and that content is not something to
publish; `.gitignore`'s header gives the reason for each entry.

## Quickstart

Clone it, then open the clone in your agent CLI and run the onboarding skill:

```bash
git clone https://github.com/Thurbeen/fleet.git
cd fleet
```

```text
/fleet-onboarding
```

The [onboarding skill](.agents/skills/fleet-onboarding/SKILL.md) does the setup
rather than instructing you through it: prerequisites, your GitHub owners
(discovered from your `gh` session and confirmed in one question), the registry
sync, the thurbox extension, and [the monitor](#the-monitor) — verifying each
step. Run it twice and it converges.

Then open the `fleet` session in thurbox and give it a goal.

Requires `gh` (authenticated), `jq`, and `thurbox-cli` **2.19.0 or newer** —
`extension.toml.in` records why the floor sits there. The skill names anything
missing, with its remedy, before it writes a thing.

### By hand

The skill calls two scripts you can run yourself, to automate the setup or to
debug it when the skill fails:

1. `cp registry/owners.example.txt registry/owners.txt`, then edit it — your
   GitHub username, plus any orgs, one per line. The sync refuses to run while
   the file has no active entries rather than emit an empty map.
2. `./scripts/sync-registry.sh` — writes `registry/repos.generated.yaml` from
   your live `gh` session.
3. `./scripts/install-extension.sh` — renders `extension.toml` and installs the
   thurbox extension.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the gate (`./scripts/check.sh`),
the squash-only merge policy, and the layout conventions.

## Pulling changes in

`./scripts/sync-checkout.sh` fast-forwards this checkout from `origin`, and the
`SessionStart` hook in `.claude/settings.json` runs it so every session starts
from an up-to-date base. It only ever fast-forwards, never rebases or resets,
and reports rather than acts on a dirty tree, a feature branch, or a divergence.

**When a sync brings new instructions, the running `fleet` session does not get
them.** It froze `FLEET.md` and every skill it had loaded at launch, and nothing
reloads them from disk — so the new bytes are on disk and the lead is still
running the old copies. This is equally true of a plain `git pull`, which is why
the sync says so on the syncs where it matters:

```text
restart-lead: yes — FLEET.md .agents/skills/fleet-queue/SKILL.md
```

Replace the agent to act on it. A restart resumes the conversation, so the old
copy is still in its history; for an instruction change that has to win, start a
fresh session instead:

```bash
thurbox-cli session restart fleet
thurbox-cli session delete fleet    # the extension self-heals it
```

It reports `reinstall-extension: yes` on the same basis when `extension.toml.in`
or `FLEET.md` moves — the installed extension no longer matches the manifest it
was rendered from, and `./scripts/install-extension.sh` is the fix.

## Customizing

Four things are yours to change, in descending order of how likely you are to
want to:

1. **`registry/owners.txt`** — required, and the only edit a fresh clone needs.
   `/fleet-onboarding` writes it for you from the tracked
   `registry/owners.example.txt`.
2. **The agent and model** — the `fleet` session itself.
3. **What worker sessions start with** — everything `fleet` spawns.
4. **The name `fleet`** — leaving it alone is a fine answer.

### The agent and model

`extension.toml.in`'s `[[agents]]` block pins the model:

```toml
[[agents]]
name = "fleet"
command = "claude"
args = ["--model", "claude-opus-4-8"]
```

Edit that block and re-run `./scripts/install-extension.sh`. `command` can point
at a different CLI entirely — `codex`, say — as long as the `resume_args`,
`fork_args`, and `new_session_args` beneath it still carry thurbox's `{id}`
token, which is what makes a session resume by its own id rather than by "the
last session in this directory".

Editing `agents.toml` after install also works and takes effect immediately; a
reinstall keeps existing entries, so it is not clobbered. But that edit lives on
one machine and does not survive `extension uninstall`, while the manifest is
committed and reaches every install of your control plane.

### What worker sessions start with

The manifest pins the **lead**. The workers it spawns take their settings from
`orchestration/session-profiles.yaml` — one named profile per set, one tracked
layer, carrying environment (`env:`) and, for a setting that is a command-line
flag rather than a variable, the command that launches the agent (`command:`,
`args:`, `reports_as:`):

```yaml
profiles:
  sweep:
    env:
      MAX_THINKING_TOKENS: "8000"
      BASH_DEFAULT_TIMEOUT_MS: "120000"
```

`./scripts/session-flags.sh <profile>` renders one into the flags `thurbox-cli
session create` takes, so a playbook names a profile instead of hand-assembling
them:

```bash
mapfile -d '' -t flags < <(./scripts/session-flags.sh sweep)
thurbox-cli session create --name 'Add a license header' \
  --repo-path /repos/widgets "${flags[@]}" --json
```

Without a profile a worker inherits whatever environment the thurbox server
has, which is what the shipped `default` profile means.

**No secrets in it** — it is committed, and this repo is public. That is a
convention, not a gate: nothing scans the file for secrets, and none is needed,
because a worker inherits the environment of the thurbox server that spawns it.
An API key belongs where that process gets it — your shell profile, your
keyring, the agent's own login — and reaches the worker without passing through
any file here.

`./scripts/check.sh` does enforce two rules: no `THURBOX_*` key (those identity
variables always win over `--env`, so setting one would look applied and do
nothing) and no `command` without a `reports_as` to declare what the pane really
runs. The file's own header has the schema; [`CONTRIBUTING.md`](CONTRIBUTING.md)
has the reasoning.

### The name `fleet`

`fleet` names the **session**. Your control plane can be called
`mission-control` while the session it runs is still `fleet` — nothing reads the
repo name.

If you do rename it, the name is in six places and they move together:

```text
extension.toml.in
  name = "fleet"                (1) the extension id — the argument to every
                                    `thurbox-cli extension ...` command, and
                                    the value install-extension.sh reads out
                                    for its hints and remedies
  [[agents]] name = "fleet"     (2) the agent id, registered in agents.toml
  [[sessions]] name  = "fleet"  (3) the session's name
  [[sessions]] agent = "fleet"      ... bound to the agent in (2)

derived, not edited
  ~/.config/thurbox/extensions/fleet/
                                (4) the extension home. The manifest has no
                                    `home` key, so thurbox derives it from (1)

prose that has to agree
  FLEET.md                      (5) the [[files]] payload, whose text opens
                                    "You are the **fleet** session"
  --to fleet / --for fleet      (6) the mailbox address, used for anything
                                    urgent enough to interrupt the lead. In
                                    FLEET.md, in this README, and in
                                    extension.toml.in's header comment
```

A **partial rename** is the failure mode: change (1) and the extension installs
as `mission-control`, but leave (6) and anything still addressed `--to fleet`
either bounces or lands in an inbox nobody drains. So:

1. Change all six.
2. Uninstall first if it is already installed under the old name —
   `thurbox-cli extension uninstall fleet`. Skip this and you get two registered
   extensions and two self-healing sessions, each recreating itself.
3. `./scripts/install-extension.sh`
4. `thurbox-cli extension status <newname>` to confirm.

`FLEET.md`'s **filename** is separate: the manifest names it four times (one
`[[files]] path`, three `[[symlinks]] target`s), so renaming the file means
editing those four lines too. Renaming the session does not require it.

**Worker** session names are not among the six. They are free-form strings that
nothing resolves — by the convention in `orchestration/playbooks/` each is an
imperative sentence describing the work, with no `fleet` prefix and no repo
prefix.

## Where things live

Four top-level directories, and each file's own header is the authority on it:

- **`registry/`** — the map. `owners.txt` lists the GitHub owners it covers;
  `repos.generated.yaml` is written by `./scripts/sync-registry.sh` and is never
  hand-edited; `context/<repo>.md` is the human judgement about one project.
- **`orchestration/`** — the running control plane. `queue/` is the task queue
  ([its `README.md`](orchestration/queue/README.md) has the layout),
  `playbooks/` reusable recipes, `runs/` a log per run,
  `session-profiles.yaml` what a worker starts under, `webui/` the monitor's
  runtime state.
- **`scripts/`** — the tooling. Every script's header is its full usage;
  `check.sh` is the whole gate, and `fleet-status.sh` answers "where are we?"
  in one read-only call — queue, sessions, PRs, monitor, checkout.
- **`.agents/skills/`** — agent skills, one agent-agnostic tree.
  `.claude/skills` is a committed symlink to it, so Claude Code and opencode
  both load the one copy. Never add a second copy under `.claude/`.

**The machinery is tracked; what a running fleet writes is not.** Your owners
file, your map, your context notes, your queue, your run logs and the monitor's
state are all gitignored — this repo is public, and none of that is something to
publish. `.gitignore`'s header names every path and the reason for each, and is
the file to read before adding one.

## The map

`registry/repos.generated.yaml` is a machine view, regenerated from GitHub so it
never drifts. What a human or an agent needs to *understand* a project lives in
`registry/context/<repo>.md`, which the sync never touches: what it is for, how
it relates to the others, what is parked and why.

The sync runs **locally**, enumerating every repo your own `gh` session can
reach and keeping the ones owned by an owner in `registry/owners.txt` — no cloud
PAT, no CI secret. Refresh it whenever you like; there is nothing to commit:

```bash
./scripts/sync-registry.sh
```

Both the map and your context notes live in one working copy. If they matter to
you beyond this machine, back that copy up yourself — this repo does not, by
design: it is public, and an index of every repo you can reach is not a thing to
publish.

## Orchestration

The control plane drives [thurbox](https://github.com/Thurbeen/thurbox)
**directly** — it does not depend on any external orchestration skill.

### The run loop

A prompt is a **topic** on disk, which becomes **tasks** on disk, each carrying
its own instructions in its own file — so nothing is lost to a context reset and
no agent holds every task's detail at once. `./scripts/queue.sh` owns all seven
steps and its header is the full usage.

```bash
./scripts/queue.sh topic add report-status-honestly \
  --title 'Make thurbox report agent status honestly' --prompt-file -
./scripts/queue.sh add report-status-honestly drop-idle-default \
  --title 'Stop defaulting an unreported session to idle' \
  --repo ~/code/thurbox --branch fix/drop-idle-default --touches src/state.rs
# write orchestration/queue/report-status-honestly/01-drop-idle-default/BRIEF.md
./scripts/queue.sh plan          # what goes out now, what waits, and why
./scripts/queue.sh dispatch      # all of the ready set, in one go
./scripts/queue.sh watch         # fold the event stream in; close nothing
./scripts/queue.sh collect       # read the results; close what is done
```

1. **Intake.** The prompt is kept verbatim and decomposed into tasks — one repo,
   one branch, one thing a single worker can finish and validate on its own.
2. **Write each `BRIEF.md`.** Workers share no context with the lead and none
   with each other, so each brief states the goal, the constraints, and what
   "done" looks like, from scratch. `dispatch` refuses an unwritten one.
3. **Order — and mostly, do not.** See below.
4. **Dispatch the whole ready set at once.** One invocation, one session per
   task, each pointed at its own brief and nothing else. `dispatch` runs
   `./scripts/session-trust.sh` between creating a session and prompting it: it
   confirms the agent's trust dialog is on the pane, answers it, and confirms it
   is gone. Sending a brief into that dialog is how every fleet-spawned worker
   used to break; if either confirmation fails, nothing is typed and the report
   names the sessions left unprompted.
5. **Read the stream, then read the results.** Record outcomes in a run log from
   `orchestration/runs/_TEMPLATE.md` as they happen.
6. **`./scripts/queue.sh shepherd`**, run as reflexively as `collect` — the pull
   request outlives the task, and `collect` names `shepherd` whenever it closed
   one that left a PR open. It dispatches a fixer for a PR that conflicts,
   fails a check, or was reviewed with changes requested, and squash-merges one
   that clears all its gates in the allowlisted repos.
7. **Review the PRs**, and merge every one `shepherd` did not. Sessions release
   themselves: once the forge says a task's pull request merged, the task moves
   to `landed` and `collect` reaps its session and worktree — never one thurbox
   says is working, and never one a worker gave up in.
   `./scripts/queue.sh reap --dry-run` says what it would do.

Fast-forward a target repo's base branch *before* spawning a worker against it.
A stale local `main` is inherited by the new worktree: the worker does correct
work and its PR arrives conflicting.

### Ordering: the counterintuitive part

Most work needs no ordering. The job is finding the small set that does and
letting everything else go at once — a queue that runs one task at a time is
slower than no queue, because it adds bookkeeping and removes nothing.

**File or subsystem overlap does not serialize anything.** Two tasks that both
expect to change `src/state.rs` are recorded with `--touches`, reported side by
side in `plan` as a risk you are accepting, and dispatched together; two agents
editing one file in two worktrees is an ordinary rebase.

What does serialize is a recorded blocker, and `queue.sh block` refuses one that
names no kind and no reason:

```bash
./scripts/queue.sh block <ref> --on <ref> \
  --kind semantic-dependency --why 'reads the field the other one introduces'
```

`--kind` is a closed set — `semantic-dependency`, `shared-external-state`,
`incompatible-migration`, `other` — and "they edit the same file" is not on it
and cannot be spelled as one. A blocker clears only when the task it names is
genuinely done; a session that merely stopped does not clear it.

### How completion arrives

A worker does **not** mail its result. `thurbox-cli message send` wakes its
recipient — it injects into the lead's terminal, so a worker reporting in
interrupts whoever is talking to the lead at that moment.

So completion is two things the lead **reads**, on its own cadence: the event
stream says *when* a turn ended, and the task's `result.md`, written by the
worker, says *what* it concluded.

```markdown
---
outcome: shipped | stuck | failed | not-applicable
artifact: <PR url, or omit>
---
What it actually did.
```

**Both halves are needed.** An agent reports `done` at the end of every turn it
takes, including the one where it gave up, so a lead that reads the stream alone
closes tasks that failed. `./scripts/queue.sh watch` folds the stream into each
task's record and **closes nothing**; `./scripts/queue.sh collect` reads the
result files and only that closes a task. `not-applicable` is why a file beats
polling `gh pr list`: the absence of a PR cannot be distinguished from "still
working", but a worker saying so can.

The mailbox is still right for something genuinely urgent a human should see
now, and wrong for routine completion.

See [`.agents/skills/fleet-queue/SKILL.md`](.agents/skills/fleet-queue/SKILL.md)
for the queue's driving surface,
[`orchestration/playbooks/_TEMPLATE.md`](orchestration/playbooks/_TEMPLATE.md)
for the anatomy of a playbook, [`AGENTS.md`](AGENTS.md) for how an agent should
operate inside this repo, and
[`.agents/skills/thurbox-session/SKILL.md`](.agents/skills/thurbox-session/SKILL.md)
for one session's mechanics: spawning, prompting, state, cleanup.

## The monitor

```bash
./scripts/webui.sh ensure     # onboarding runs this for you
```

A local web page over `orchestration/queue`, so you can see what the fleet is
doing without asking the lead and interrupting it. It **reads** the same files
`queue.sh` writes and adds no field of its own — the ask from `PROMPT.md`, the
plan from `BRIEF.md` and `task.yaml`, the progress from `progress.jsonl`, the
outcome from `result.md` — so it cannot disagree with `queue.sh list`. Topics
are classified from their tasks' states each time the page is built, so a
classification cannot go stale:

| Class | Means |
|---|---|
| `attention` | a worker concluded `stuck` or `failed` |
| `running` | at least one task is dispatched |
| `ready` | nothing running, but work has no blocker left |
| `blocked` | every remaining task waits on a recorded blocker |
| `done` | every task concluded |

**It displays; it does not control.** No route dispatches, cancels or reorders
anything: every route is a `GET` and every write verb answers `405`, so
`scripts/queue.sh` stays the one writer.

`./scripts/webui.sh` owns the lifecycle and its header is the full usage. The
one thing to know first is that `ensure` and `start` differ in a single way:

| | On a running monitor | After you asked it down |
|---|---|---|
| `ensure` | adopts it, prints the URL | leaves it down |
| `start` | adopts it, prints the URL | **brings it back** |

`stop` writes `orchestration/webui/down` before it kills anything, and that flag
on disk is what makes "down" mean something across a restart, a reboot and the
next `/fleet-onboarding` — which calls `ensure`, so only you type `start`.

It binds **`127.0.0.1`**, and anything wider is an explicit `FLEET_WEBUI_HOST`
you set rather than a default you discover; on a loopback bind it also rejects a
request whose `Host` header names somewhere else. The port is chosen at bind
time — `7413`, then the next free one up to `7433`, so a second fleet on one
machine starts — which means nothing hard-codes it. Read it back:

```bash
./scripts/webui.sh status     # up? on what URL? asked down?
./scripts/webui.sh url        # just the URL, for scripting
cat orchestration/webui/url   # the same answer, from the file it wrote
```

It costs nothing beyond what the gate already needs: Python's standard library
and the PyYAML `queue.sh` requires. No build step, no `node_modules`, no CDN —
the page is one file of HTML, CSS and JavaScript, plus two local assets (the
banner crop and a vendored display font,
[`media/fonts/README.md`](media/fonts/README.md)) served from a hard-coded
whitelist. `./scripts/check.sh webui` proves the two claims that break silently:
that a second `ensure` adopts rather than starting a twin, and that a stop
survives the next `ensure`.

## Thurbox extension

The repo ships [`extension.toml.in`](extension.toml.in), so thurbox can keep the
control plane running as a first-class session:

```bash
./scripts/install-extension.sh
```

That registers two things: a **`fleet` agent** in `agents.toml` — `claude`
pinned to Opus, resuming and forking by thurbox's session id — and a long-lived
**`fleet` session** whose standing context is [`FLEET.md`](FLEET.md) (symlinked
as `CLAUDE.md` / `AGENTS.md` / `GEMINI.md` in the extension home). thurbox
self-heals the session: delete it and it comes back.

Payload files land in `~/.config/thurbox/extensions/fleet/`; the registry,
playbooks, and run logs stay in your checkout.

The manifest ships as `extension.toml.in` because `[[sessions]] repo_path` must
be an absolute path to your clone and no token spells that — `{home}` resolves
to the extension home, not your checkout — and a committed file cannot hardcode
a path that exists on one machine. `install-extension.sh` renders the
`__REPO_PATH__` placeholder into a gitignored `extension.toml`.
`extension.toml.in`'s own header carries the full reasoning, including why
installing with `--home <your clone>` is a trap and what the manifest
deliberately leaves out.

Two consequences of that file being generated:

- **Re-running the installer does not move the session if the clone moved.**
  thurbox finds an extension's session by **name** and never repoints it, so a
  second install rewrites the manifest, reports success, and leaves the live
  session opening the old path — with `extension status` still calling it
  healthy, because it checks that the session exists, not where it points. The
  installer detects the mismatch and exits non-zero naming the remedy, which
  deletes the session and its history and so is yours to run:

  ```bash
  thurbox-cli extension deactivate fleet   # deletes the session
  ./scripts/install-extension.sh           # respawns it at the new path
  ```

- **`thurbox-cli extension update fleet` is not this extension's update
  command.** The install stamped your clone as the extension's `source`, and
  update re-reads the **rendered** `extension.toml` from it, never the `.in`
  file — so it refreshes to whatever was last rendered and fails outright if the
  gitignored file was cleaned away. `./scripts/install-extension.sh` is the real
  update command; run it after any change to `extension.toml.in` or `FLEET.md`,
  including one that arrives with a `git pull` — `./scripts/sync-checkout.sh`
  reports that as `reinstall-extension: yes`.

```bash
thurbox-cli extension status fleet     # per-resource health
thurbox-cli extension deactivate fleet # the real off-switch
thurbox-cli extension uninstall fleet  # reverse the install
thurbox-cli extension uninstall fleet --purge  # ... and delete the home dir
```

`--purge` is safe here: the extension home holds only the `FLEET.md` mirror and
its symlinks, which the next install lays down again. Your checkout is a
different directory entirely.

## License

MIT — see [LICENSE](LICENSE).
