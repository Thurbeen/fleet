<!-- rumdl-disable MD041 -->
<!-- The banner is the first line by design; MD041 wants a level-1 heading. -->

![Fleet: fighter craft over a volcanic canyon, powered by thurbox][banner]

[banner]: media/fleet-banner.jpg

# fleet

A **control plane** for your work across GitHub: one repo that holds the *map* of
your projects and the *orchestration* of AI agent sessions run against them,
using [thurbox](https://github.com/Thurbeen/thurbox). The defining constraint is
that the control plane holds the plan and the log, and never holds the workers'
branches — real work happens in thurbox worker sessions, each in its own git
worktree in a real repo.

What this repo **accumulates** is playbooks and intent — the reusable part, and
the part the template ships. What a running fleet **writes** — run logs, your
project context, the generated map — is local working state and is gitignored,
so it lives in your working copy and is not backed up by this repo. That is a
deliberate trade, and [Staying current](#staying-current) is what it buys.

## Quickstart

**Clone it** — do not use "Use this template", and do not fork; the next section
says why. Then open the clone in your agent CLI and run:

```bash
git clone https://github.com/Thurbeen/fleet.git my-control-plane
cd my-control-plane
git remote rename origin template          # the template you update FROM
gh repo create my-control-plane --private --source=. --remote=origin --push
```

```text
/fleet-onboarding
```

The [onboarding skill](.agents/skills/fleet-onboarding/SKILL.md) does the setup
rather than instructing you through it. It checks the prerequisites up front,
discovers your GitHub username and orgs from your `gh` session and confirms
them in one question, writes `registry/owners.txt`, syncs the registry,
installs the thurbox extension, brings [the monitor](#the-monitor) up, and
verifies each step actually landed. Run it twice and it converges instead of
duplicating.

Then open the `fleet` session in thurbox and give it a goal. That part is
yours.

Requires `gh` (authenticated), `jq`, and `thurbox-cli` **2.19.0 or newer** —
`extension.toml.in` records why the floor sits there. The skill names anything
missing, with its remedy, before it writes a thing.

### By hand

The skill is the easy path, not the only one. It calls two scripts you can run
yourself — to automate the setup, or to debug it when the skill fails:

1. `cp registry/owners.example.txt registry/owners.txt`, then edit it — your
   GitHub username, plus any orgs you belong to, one per line. The example is
   the tracked copy; `owners.txt` is yours and gitignored. The sync refuses to
   run while the file has no active entries, rather than emit an empty map.
2. `./scripts/sync-registry.sh` — writes `registry/repos.generated.yaml` from
   your live `gh` session. Nothing to commit: it is gitignored.
3. `./scripts/install-extension.sh` — renders `extension.toml` and installs the
   thurbox extension.
4. `git remote -v` — confirm `template` points at the fleet template. Without
   it there is nothing to update from.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the gate (`./scripts/check.sh`),
the squash-only merge policy, and the layout conventions.

## Staying current

Your control plane is a **clone** of the template, so it carries the template's
history and the template stays as a second remote. Updating is ordinary git:

```bash
./scripts/update-from-template.sh          # preview: what would change
./scripts/update-from-template.sh --apply  # do it
```

`git pull template main` does the same thing when your instance has nothing of
its own — which, given the split below, is the normal state. The script earns
its place in the cases where a bare pull does not: it refuses safely instead of
leaving you mid-merge, says what it skipped and why, tells you the running
`fleet` session is now holding stale instructions, and handles the one case a
plain pull genuinely cannot — see the note below. `/fleet-update` is the same
thing with an agent reading the report for you.

**If your instance is older than this change**, it has `registry/owners.txt` and
the generated map committed, and the template has just stopped tracking both. A
bare pull sees "you changed it, they deleted it" and stops on a modify/delete
conflict. The script untracks those two paths first — the files stay exactly
where they are on disk — so the update goes through and you keep them. Take the
script before you run it, since your checkout does not have it yet:

```bash
git fetch template
git show template/main:scripts/update-from-template.sh > /tmp/fleet-update.sh
bash /tmp/fleet-update.sh            # preview; add --apply when it looks right
```

### Why a clone, and not the two obvious alternatives

| Shape | Shared history? | Can be private? |
|---|---|---|
| **Use this template** | no — a generated repo has no common ancestor with its source, so `git merge` has nothing to work with | yes |
| **Fork** | yes | **no** — GitHub answers `Public forks can't be made private` (HTTP 422) |
| **Clone, repoint `origin`** | yes | yes |

A control plane is private and needs to update. Only the clone gives both.

What the clone costs, plainly: you lose the one-click **Use this template**
button for a four-line clone-and-repoint, and your `git log` starts with the
template's commits rather than your own. Both are real. Neither is worth giving
up updates for.

### The invariant that keeps it a fast-forward

Your **tracked** tree stays identical to the template's. Nothing a running fleet
writes is tracked — `.gitignore` says which paths and why — so your `main` never
diverges, so a pull is always a clean fast-forward and never a merge that can
conflict on something you care about.

That is why your own playbooks live in `orchestration/playbooks/local/` and your
profile tuning in `orchestration/session-profiles.local.yaml`: both are yours,
both are ignored, and neither puts a commit on `main`.

### If your control plane predates this

An instance made with **Use this template**, or bootstrapped on its own, shares
no commit with the template. Join the two once — this keeps your tree byte for
byte and imports nothing:

```bash
git remote add template https://github.com/Thurbeen/fleet.git
./scripts/update-from-template.sh --adopt
```

From then on it is an ordinary update. The adopt step deliberately does **not**
bring the improvements that already exist upstream; take those when you want
them, for example `git checkout template/main -- scripts/ .agents/skills/`.

## Customizing

Four things are yours to change, in descending order of how likely you are to
want to:

1. **`registry/owners.txt`** — required, and `/fleet-onboarding` writes it for
   you (from the tracked `registry/owners.example.txt`). It is the only edit a
   fresh clone actually needs, and it is gitignored like everything else that is
   yours.
2. **The agent and model** — optional. This is the `fleet` session itself.
3. **What worker sessions start with** — optional. This is everything `fleet`
   spawns.
4. **The name `fleet`** — optional, and leaving it alone is a fine answer.

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
token. That token is what makes the session resume by its own id instead of by
"the last session in this directory".

Editing `agents.toml` after install works too, and takes effect immediately: a
reinstall keeps existing entries, so your edit is not clobbered. But the
manifest is where the entry is *generated* from, and that makes it the durable
place. An `agents.toml` edit lives on one machine and does not survive
`extension uninstall`; a manifest edit is committed, and every install of your
control plane gets it.

### What worker sessions start with

`extension.toml.in` above pins the **lead** — the long-lived `fleet` session.
The workers it spawns are a different question, and their settings live in
`orchestration/session-profiles.yaml`: one named profile per set. That file is
the **template's**, holding the shipped defaults. Yours go in
`orchestration/session-profiles.local.yaml` — copied from the tracked
`.local.example.yaml`, gitignored, and a profile named there replaces the
shipped one of that name wholesale. `session-flags.sh` prints which profiles an
override is shadowing, so precedence is never silent:

```yaml
profiles:
  sweep:
    env:
      MAX_THINKING_TOKENS: "8000"
      BASH_DEFAULT_TIMEOUT_MS: "120000"
```

A profile carries environment (`env:`), and — for a setting that is a command
line flag rather than a variable — the command that launches the agent
(`command:`, `args:`, `reports_as:`). `./scripts/session-flags.sh <profile>`
turns one into the flags `thurbox-cli session create` takes, so a playbook
names a profile and never hand-assembles the flags:

```bash
mapfile -d '' -t flags < <(./scripts/session-flags.sh sweep)
thurbox-cli session create --name 'Add a license header' \
  --repo-path /repos/widgets "${flags[@]}" --json
```

Without a profile a worker inherits whatever environment the thurbox server
happens to have — which is what every fleet worker did before this file
existed, and is still exactly what the shipped `default` profile means.

**Template defaults only in the shipped file** — it is committed, and this is a
public template. The gitignored `.local.yaml` is where anything
environment-specific goes, and anything you would not commit. That is a
convention, not a gate: nothing scans these files for secrets. It needs no
enforcement, because nothing a running fleet writes is tracked, so an instance
has no changes of its own to push. Better still, a worker inherits the server's
environment, so an API key belongs where that process gets it — your shell
profile, your keyring, or the agent's own login — and reaches the worker without
passing through any file here.

`./scripts/check.sh` holds **both** layers to the two rules it does enforce: it
refuses a `THURBOX_*` key (thurbox's own identity variables always win over
`--env`, so setting one would look applied and do nothing) and a `command`
without a `reports_as` to declare what the pane really runs.
`CONTRIBUTING.md` has the reasoning; the file's own header has the schema.

### The name `fleet`

`fleet` names the **session**, not you and not your repo. Your control plane can
be called `mission-control` while the session it runs is still `fleet` — nothing
reads the repo name. Keeping `fleet` is a perfectly good default. Renaming is
optional, not a setup step you have overlooked.

If you do rename it, the name is not in one place. It is in six, and they move
together:

```text
extension.toml.in
  name = "fleet"                (1) the extension id — the argument to every
                                    `thurbox-cli extension ...` command.
                                    scripts/install-extension.sh reads this
                                    value out of the manifest for the hints and
                                    the remedy it prints, so a rename reaches
                                    the script without editing it
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

Only (4) is not a line you edit; it follows from (1). The rest are.

The failure mode is a **partial rename**. Change (1) and the extension installs
as `mission-control`; leave (6) and anything still addressed `--to fleet` either
bounces or lands in an inbox nobody drains, and the lead never sees it. So:

1. Change all six.
2. If the extension is already installed under the old name, uninstall it first:
   `thurbox-cli extension uninstall fleet`. Skip this and you get two registered
   extensions and two self-healing sessions, each faithfully recreating itself.
3. `./scripts/install-extension.sh`
4. `thurbox-cli extension status <newname>` to confirm.

`FLEET.md`'s **filename** is a separate question. The manifest names it four
times — one `[[files]] path` and three `[[symlinks]] target`s — so renaming the
file means editing those four lines too. You do not have to: renaming the
session does not require renaming the file, and `FLEET.md` is a fine name for
the standing context of a session called anything.

**Worker** session names are not among the six and not coupled to them. They are
free-form strings that nothing resolves, and by the convention in
`orchestration/playbooks/` and `orchestration/runs/_TEMPLATE.md` each is an
imperative sentence describing the work — `Document the customization surface` —
carrying neither a `fleet` prefix nor a repo prefix. A rename never reaches
them. The same goes for this README's own prose: it is documentation, so it
follows a rename rather than driving one.

## Layout

`[yours]` marks a path that is **gitignored** — written by your fleet, never by
the template. Everything else is the template's and is tracked; that is the
split [Staying current](#staying-current) depends on, and `.gitignore` gives the
reason for each entry.

```text
registry/
  owners.example.txt       The tracked example. Copy it to owners.txt.
  owners.txt               [yours] The GitHub owners the map covers.
  repos.generated.yaml     [yours] Auto-synced index of every repo. GENERATED —
                           do not hand-edit; ./scripts/sync-registry.sh writes it.
  context/
    _TEMPLATE.md           Copy this to add a project.
    <repo>.md              [yours] Curated notes: purpose, relations, goals.

orchestration/
  queue/
    README.md              The queue's layout, and why it is yours.
    <topic>/               [yours] One unit of intent, from one prompt.
      topic.yaml           Its record.
      PROMPT.md            The prompt that opened it, VERBATIM.
      <NN>-<slug>/         [yours] One unit of work.
        task.yaml          Intent + state. ./scripts/queue.sh owns it.
        BRIEF.md           The instructions ONE worker reads.
        progress.jsonl     One line per observed transition.
        result.md          What that worker concluded, in its words.
  session-profiles.yaml    Named settings a worker session STARTS under —
                           the template's defaults.
  session-profiles.local.example.yaml
                           The tracked example. Copy it to the next line.
  session-profiles.local.yaml
                           [yours] Your overrides, layered over those defaults.
  playbooks/
    _TEMPLATE.md           Copy this to add a reusable orchestration recipe.
    cross-repo-sweep.md    Shipped playbooks. Improve one upstream, not here.
    ship-feature.md
    local/
      README.md            Why this directory exists.
      <name>.md            [yours] Playbooks you write.
  runs/
    _TEMPLATE.md           Copy this per orchestration run.
    <date>-<slug>.md       [yours] Log of one run: goal, sessions, outcomes.
  webui/                   [yours] The monitor's runtime state, created on its
                           first start. Nothing here is edited by hand.
    port  host  url        Where it actually bound. The port is chosen at bind
                           time, so these files are the answer, not a guess.
    pid                    Its supervisor. server.log sits beside it.
    down                   Written by `webui.sh stop`. While it exists, the
                           monitor stays down — across a reboot, and across
                           the next onboarding run.

scripts/
  check.sh                 The whole gate: shell, markdown, YAML, profiles,
                           queue, monitor, skills.
  queue.sh                 The task queue: intake, ordering, dispatch, and both
                           halves of completion. Its header is the full usage.
  queue-selftest.sh        Proves the queue's ordering and wake claims against
                           a throwaway queue. Part of the gate.
  webui.sh                 The monitor's lifecycle: ensure, start, stop,
                           restart, status. Its header is the full usage.
  webui-selftest.sh        Proves the monitor adopts rather than duplicates and
                           that a stop stays stopped. Part of the gate.
  install-extension.sh     Renders extension.toml, installs it, and verifies
                           the live session really opens this clone.
  sync-registry.sh         Regenerates repos.generated.yaml from the GitHub API.
  sync-checkout.sh         Fast-forwards main from YOUR origin when safe.
  update-from-template.sh  Updates this control plane from the TEMPLATE remote.
                           A different remote and a different job — see its header.
  trust-thurbox-dir.sh     Seeds Claude Code workspace trust for a worktree.
  session-flags.sh         Renders one session profile into session-create flags.
  session-trust.sh         Confirms, answers and re-confirms a new session's
                           trust dialog. Run by queue.sh dispatch.
  lib/check_yaml.py        The YAML + registry-shape assertions check.sh runs.
  lib/queue.py             The queue model queue.sh drives.
  lib/webui.py             The read-only web view over that model. Standard
                           library only — no build step, no node_modules.
  lib/session_profiles.py  The profile validation and rendering session-flags.sh
                           runs.

.agents/skills/
  <name>/SKILL.md          Agent skills. ONE tree, agent-agnostic.
    fleet-onboarding/      Fresh clone -> working control plane.
    fleet-update/          Update this control plane from the template.
    thurbox-session/       Spawning and driving worker sessions.
.claude/skills             A committed SYMLINK to .agents/skills.

media/
  fleet-banner.jpg         The banner at the top of this README.

.github/workflows/
  ci.yml                   PR checks feeding a single "All Checks" gate.
                           Every job runs scripts/check.sh.

AGENTS.md                  How an agent should operate inside this repo.
CLAUDE.md                  A two-line pointer that imports AGENTS.md.
FLEET.md                   Standing context for the `fleet` SESSION — what it
                           is for, as opposed to how to work in the checkout.
CONTRIBUTING.md            The gate, the squash-only policy, the conventions.
.gitignore                 The tracked/yours split, with a reason per entry.
```

Skills live in `.agents/skills/` and `.claude/skills` is a symlink to it, so one
copy serves every CLI: Claude Code reads `.claude/skills`, and opencode
auto-discovers the same path. Mirroring the tree into `.opencode/skills` would
register the same skill twice — don't. `./scripts/check.sh skills` fails if the
link is not a symlink, which is what a clone with `core.symlinks=false` leaves
behind.

## The map

`registry/repos.generated.yaml` is a machine view — regenerated from GitHub, so
it never drifts. Everything a human (or an agent) actually needs to *understand*
a project lives in `registry/context/<repo>.md`, which the sync never touches.
That is where judgement goes: what a project is for, how it relates to the
others, what is parked and why.

The sync runs **locally**. It enumerates every repo you can reach using your own
`gh` session and keeps the ones owned by an owner in `registry/owners.txt`, so
there is no cloud PAT and no CI secret to manage. Refresh the map whenever you
like — there is nothing to commit, because the map and the owners file are both
gitignored:

```bash
./scripts/sync-registry.sh
```

Both the map and your context notes therefore live in **one working copy**. If
they matter to you beyond this machine, back that copy up yourself — this repo
does not, by design, and [Staying current](#staying-current) is what that buys.

## Orchestration

The control plane drives [thurbox](https://github.com/Thurbeen/thurbox)
**directly** — it does not depend on any external orchestration skill.

### The run loop

A prompt is not a turn in a conversation. It is a **topic** on disk, which
becomes **tasks** on disk, each carrying its own instructions in its own file —
so nothing is lost to a context reset, and no agent holds every task's detail at
once. `./scripts/queue.sh` owns all six steps.

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
   task, each pointed at its own brief and nothing else.
5. **Read the stream, then read the results.** Record outcomes in a run log from
   `orchestration/runs/_TEMPLATE.md` as they happen. That log is local working
   state, not something this repo keeps for you.
6. **Review the PRs**, then delete each session as it closes out.

Fast-forward a target repo's base branch *before* spawning a worker against it.
A stale local `main` is inherited by the new worktree: the worker does correct
work and its PR arrives conflicting.

### The trust dialog

An agent started in a directory it has not seen asks whether it may work there,
and thurbox mints a fresh worktree per session. A worker therefore sat on that
dialog and `session send` typed the brief straight into it. `dispatch` now runs
`./scripts/session-trust.sh` between creating a session and prompting it: it
**confirms the dialog is on the pane**, answers with the keys that agent needs,
and **confirms it is gone**. If either confirmation fails it sends nothing and
says which sessions were left unprompted, because a session waiting on a dialog
is visible and fixable and one that has been typed into randomly is neither.

Claude Code's dialog defaults to `No, exit`, so a bare Enter dismisses it — the
key sequence is Down then Enter. `codex`, `pi` and `pi-signed` take Enter;
`grok` and `kimi` show nothing inside a git repo; `cursor` and `muse` are not
keystrokes at all but launch flags, which is what the `cursor-trusted` and
`muse-trusted` profiles are for. `scripts/trust-thurbox-dir.sh` still seeds
Claude's trust into `~/.claude.json` and is the right fallback when a dialog
cannot be answered — it is no longer the default, because it writes to a file
the operator owns.

### Ordering: the counterintuitive part

Most work needs no ordering at all. The job is finding the small set that does
and letting everything else go at once — a queue that runs one task at a time is
slower than no queue, because it adds bookkeeping and removes nothing.

So **file or subsystem overlap does not serialize anything.** Two tasks that
both expect to change `src/state.rs` are recorded with `--touches`, reported
side by side in the plan as a risk you are accepting, and dispatched together;
two agents editing one file in two worktrees is an ordinary rebase.

```text
ready: 3 task(s) — every one of them goes out now, there is no concurrency cap
    …/01-drop-idle-default     ~/code/thurbox  fix/drop-idle-default
    …/02-document-the-states   ~/code/thurbox  fix/document-the-states
    …/04-log-state-changes     ~/code/thurbox  fix/log-state-changes
    risk: …/01-drop-idle-default, …/04-log-state-changes all touch src/state.rs
          Overlap is a risk signal, not a reason to wait — dispatch
          them together and let the delivery path reconcile a rebase.

waiting: 1 task(s) — each held by a durable, recorded blocker
    …/03-render-detected-agent
        semantic-dependency on …/01-drop-idle-default: reads the
        detected_agent field 01 introduces
```

What *does* serialize is a recorded blocker, and `queue.sh block` refuses one
that names no kind and no reason:

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
interrupts whoever is talking to the lead at that moment. The property the CLI
calls "immediate" is immediate in exactly the way that hurts.

So completion is split into two things the lead **reads**, on its own cadence:

```text
the WHEN   thurbox-cli watch --json --since <seq>
           One line per transition, resumable by sequence number, so a lead
           that looks away misses nothing and is interrupted by nothing.

the WHAT   the task's result.md, written by the worker when it knew what it
           had concluded:

               ---
               outcome: shipped | stuck | failed | not-applicable
               artifact: <PR url, or omit>
               ---
               What it actually did.
```

**Both halves are needed.** A transition says a turn ended, which is not the
claim that the task finished — an agent reports `done` at the end of every turn
it takes, including the one where it gave up. A lead that reads the stream alone
closes tasks that failed.

`./scripts/queue.sh watch` folds the stream into each task's record and closes
nothing; `./scripts/queue.sh collect` reads the result files and only that
closes a task. `not-applicable` is why a file beats polling `gh pr list`: the
absence of a PR cannot be distinguished from "still working", but a worker
saying so can.

The mailbox still exists and is still the right tool for something genuinely
urgent a human should see now. It is the wrong tool for routine completion.

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
doing without asking the lead and interrupting it. It is a **reader**: it opens
the same files `queue.sh` writes and adds no field of its own.

That is possible because the queue already stores four different things in four
different files, which happen to be exactly the four questions a monitor asks:

| The page shows | It reads | Written by |
|---|---|---|
| the ask | `PROMPT.md` | you, verbatim, at intake |
| the plan | `BRIEF.md` + `task.yaml` | the lead |
| the progress | `progress.jsonl` | `queue.sh watch` |
| the implementation | `result.md` | the worker, in its words |

Topics are **classified** from their tasks' states each time the page is
built — nothing stores a classification, so it cannot go stale:

| Class | Means |
|---|---|
| `attention` | a worker concluded `stuck` or `failed` |
| `running` | at least one task is dispatched |
| `ready` | nothing running, but work has no blocker left |
| `blocked` | every remaining task waits on a recorded blocker |
| `done` | every task concluded |

The ready set's file overlaps appear at the top as the same risk note
`queue.sh plan` prints — a signal, not a hold.

### It displays; it does not control

There is no button that dispatches, cancels or reorders anything. Every route
is a `GET` and every write verb answers `405`. `scripts/queue.sh` stays the one
writer, which keeps the records a single tool's contract, and it is also what
makes a bound socket a modest risk rather than a serious one. Control can be
added later, once the read path has proven itself.

### Always up, unless you ask it down

Two commands that look alike and differ in one way that matters:

| | On a running monitor | After you asked it down |
|---|---|---|
| `ensure` | adopts it, prints the URL | leaves it down |
| `start` | adopts it, prints the URL | **brings it back** |

`stop` writes `orchestration/webui/down` **before** it kills anything. A flag on
disk is what makes "down" mean something: an in-memory stop would be undone by
the next `/fleet-onboarding`, which is a stop that does not stop. So onboarding
calls `ensure`, and only you type `start`.

Across the three ways a server goes away:

```text
a crash        a supervisor loop restarts it — unless the down flag is set,
               in which case that exit was intentional and it stays down.
a reboot       nothing survives one. The next `ensure` brings it back; the
               flag survives, so a monitor you stopped stays stopped.
a second       both `ensure` and `start` ADOPT a live monitor and print its
invocation     URL. Two servers over one queue is what this prevents.
```

If you want it back after a reboot without waiting for a skill run, that is a
login hook of your own calling `ensure` — the command is idempotent and honours
the flag, so it is safe to run on every login.

### Where it binds, and on what port

**`127.0.0.1`, and nothing wider.** This page serves your prompts, your plans
and your workers' output. Anything broader is an explicit `FLEET_WEBUI_HOST`
you set, never a default you discover — and the server says so on stderr when
you do. On a loopback bind it also rejects a request whose `Host` header names
somewhere else, which is what stops a page on another origin pointing your
browser at it.

The port is **chosen at bind time**: `7413` by default, then the next free one
up to `7433`. Two fleets on one machine is ordinary, not exotic, and the second
must not fail to start because the first got there. So nothing hard-codes the
port — read it back:

```bash
./scripts/webui.sh status     # up? on what URL? asked down?
./scripts/webui.sh url        # just the URL, for scripting
cat orchestration/webui/url   # the same answer, from the file it wrote
```

### What it costs to clone

Nothing beyond what the gate already needs: Python's standard library and the
PyYAML that `queue.sh` requires. No build step, no `node_modules`, no CDN — the
page is one file of HTML, CSS and JavaScript the server hands over as it is.
This repo is cloned by people who should not pay a toolchain to look at their
own queue.

`./scripts/check.sh webui` proves the two claims that break silently: that a
second `ensure` adopts rather than starting a twin, and that a stop survives
the next `ensure`.

## Thurbox extension

The repo ships [`extension.toml.in`](extension.toml.in), so thurbox can keep the
control plane running as a first-class session:

```bash
./scripts/install-extension.sh
```

That registers exactly two things:

- **A `fleet` agent** in `agents.toml` — `claude` pinned to Opus, resuming and
  forking by thurbox's session id like the stock `claude` agent.
- **A long-lived `fleet` session**, whose standing context is
  [`FLEET.md`](FLEET.md) (symlinked to `CLAUDE.md` / `AGENTS.md` / `GEMINI.md`
  in the extension home). thurbox **self-heals** it: delete the session and it
  comes back. Because it is a real session it can also be addressed by name —
  though routine worker results arrive as files the lead reads, not as mail
  that interrupts it.

Payload files land in `~/.config/thurbox/extensions/fleet/`; the registry,
playbooks, and run logs stay in your checkout, where they are versioned.

### Why the manifest is a `.in` file

`[[sessions]] repo_path` must be an **absolute path** to your clone.

That used to be true for two reasons. The historical one is gone: older thurbox
did not expand `~` there — `ExtensionDef::resolved_for_home()` substituted the
`{home}` token but never called `expand_tilde`, so a leading tilde was taken
literally and the session landed in a directory named `~`.
[thurbox#782](https://github.com/Thurbeen/thurbox/pull/782) fixed that in
0.174.2, and `min_thurbox_version` is now 2.19.0, so no supported thurbox still
has the bug.

The permanent reason stands on its own: no token spells "my clone". `{home}` is
substituted,
but it resolves to the *extension home*, not your checkout — while the session
must open the checkout, because it needs `registry/` and `orchestration/` in
hand.

A template cannot hardcode a path that exists on one machine. So the manifest
ships as `extension.toml.in` with a `__REPO_PATH__` placeholder, and
`scripts/install-extension.sh` renders it to a gitignored `extension.toml`
carrying your clone's real path.

Do not try to retire the placeholder by installing with `--home <your clone>`.
That does make `{home}` your checkout, but it also moves the extension's whole
payload into your working tree — and `uninstall --purge` refuses only paths
shallower than two components, or `$HOME` itself, so a purge would delete the
clone.

### If you move the clone

Re-running the installer is **not** enough on its own, and this is the one sharp
edge in the whole install path. thurbox finds an extension's session by **name**
and reuses the one it finds; it never compares that session's directory against
the manifest's. So a second install rewrites `extension.toml`, reports success,
and leaves the live session opening the old path — while `extension status`
still calls it healthy, because it checks that the session *exists*, not where
it points.

The installer closes that gap itself: it compares the live session's directory
against your clone and exits non-zero naming the remedy. That remedy deletes the
session and its conversation history, so it is yours to run, not the script's:

```bash
thurbox-cli extension deactivate fleet   # deletes the session
./scripts/install-extension.sh           # respawns it at the new path
```

### Updating it

`./scripts/install-extension.sh` is this extension's real update command. Run it
after any change to `extension.toml.in` or `FLEET.md` — including one that
arrives via `./scripts/update-from-template.sh`.

`thurbox-cli extension update fleet` is not that command. The install stamped
your clone as the extension's `source`, and update re-fetches the **rendered**
`extension.toml` from it — never `extension.toml.in`. So it refreshes to
whatever was last rendered, and fails outright if the gitignored
`extension.toml` was cleaned away. The same applies to the automatic refresh
thurbox runs for stale extensions after you upgrade the binary.

### What the manifest deliberately does not use

thurbox's manifest format offers more than fleet needs. What it uses:
`[[agents]]`, one `[[files]]` payload, three `[[symlinks]]`, one `[[sessions]]`,
and the version/compat declarations. What it declines, and why:

| Feature | Why not |
| --- | --- |
| `[[automations]]` (both the `session_ref` + `prompt` flavour and the headless `command` one) | The two candidates stay manual: the registry sync, because a human should read what changed in the map, and `./scripts/update-from-template.sh`, because an update rewrites the instructions the running `fleet` session is operating on. |
| `[[external_files]]`, `[[agent_patches]]`, `[[config_merges]]` | The three payload kinds that reach outside the extension home into an agent's own config. fleet's skills live in the checkout under `.agents/skills/`, where the session already reads them; a control plane should not edit your agent's global config. |
| `home` / `--home` | Omitted, so the home defaults to `~/.config/thurbox/extensions/fleet`. See above for why pointing it at your clone is a bad trade. |
| `[[files]]` flags — `executable`, `if_absent`, `substitute` | The single payload file is prose: not a script, not a user-edited seed, and it contains no `{home}` to substitute. |

```bash
thurbox-cli extension status fleet     # per-resource health
thurbox-cli extension deactivate fleet # the real off-switch
thurbox-cli extension uninstall fleet  # reverse the install
thurbox-cli extension uninstall fleet --purge  # ... and delete the home dir
```

`--purge` is safe here: the extension home holds only the `FLEET.md` mirror and
its symlinks, all of which the next install lays down again. Your checkout —
the registry, the playbooks, the run logs — is a different directory entirely,
which is the other reason the home is not pointed at it.

## License

MIT — see [LICENSE](LICENSE).
