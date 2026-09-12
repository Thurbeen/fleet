# AGENTS.md — operating guide for this control plane

This is a **control-plane** repo. When you work here you are helping orchestrate
and map projects, not shipping application code.

This file is the one copy. `CLAUDE.md` beside it is a two-line pointer that
imports it. Edit this file, not the pointer.

## What this repo is

The machinery is tracked; what a running fleet writes is not — this repo is
public, and that content is not something to publish. `.gitignore`'s header
names every path and the reason for each.

- `registry/owners.txt` — the GitHub owners the map covers, one per line.
  `registry/owners.example.txt` is the tracked copy it starts from.
- `registry/repos.generated.yaml` — generated index of every repo under those
  owners. **Never hand-edit it.** Refresh with `./scripts/sync-registry.sh`.
- `registry/context/<repo>.md` — the human-owned truth about a project: what it
  is, how it relates to others, current goals. Read the relevant one before
  reasoning about a project, and keep it short and current.
- `orchestration/session-profiles.yaml` — named default settings a worker
  session STARTS under (`--env`, and `--command` for a setting that is a flag),
  as opposed to where its work goes. `./scripts/session-flags.sh <profile>`
  renders one into `session create` flags. One file, one layer — edit it
  directly. The file's own header owns the rules that keep a profile safe.
- `orchestration/publish.example.conf` and `agent.example.conf` — the two
  settings that keep fleet agnostic about YOUR tools. The first holds the
  default publish method and the free-text command that produces it, plus the
  attestation marker your pipeline emits; `scripts/lib/queue.py` models three
  ARTIFACT SHAPES (`attested`, `pr`, `push`) and no tool names, so a publisher
  fleet has never heard of still works. The second holds which agent your
  workers run, which provider `refuel` gates on, and how that agent says it hit
  a limit. **Both tracked copies name nothing** — `./scripts/check.sh automerge`
  fails one that does — so a fresh clone inherits no operator's pipeline,
  vendor or agent. Copy either to a gitignored `*.conf` beside it to set
  anything. `no-mistakes` is still accepted wherever a method is read and means
  `attested`.
- `orchestration/session-glyphs.example.conf` — the mark fleet's sessions wear
  in the thurbox session list: `📡` on the lead, `🚀` on every worker, under ONE
  `GLYPHS=on|off` setting whose `off` is the one-cell `⌖` and no worker prefix.
  Tracked defaults; copy it to a gitignored `session-glyphs.conf` beside it to
  change anything, since editing a tracked file would leave
  `scripts/sync-checkout.sh` a dirty tree. Two readers and no third:
  `scripts/install-extension.sh` renders the lead's mark into the manifest, and
  `scripts/lib/queue.py` puts the worker's on at dispatch. **Changing it is a
  RENAME of the lead, and installing is not applying one** — see
  `extension.toml.in`'s RENAMING header.
- `orchestration/queue/<topic>/` — the task queue. A prompt becomes a TOPIC
  holding its verbatim `PROMPT.md`; the topic decomposes into task directories,
  each with its own `task.yaml`, `BRIEF.md`, `progress.jsonl` and `result.md`.
  `./scripts/queue.sh` owns it end to end and its header is the full usage.
  Gitignored except the `README.md` that documents the layout, the `POLICY.md`
  every brief points its worker at instead of restating it, and
  `OPERATOR.example.md` — the form of the operator's own `OPERATOR.md`, which
  is theirs, stays ignored, and is pointed at by every brief scaffolded while
  it exists and is not empty. **The queue belongs to the control-plane
  checkout — the clone the Mission Control session opens — and not to whatever
  directory your shell is in**, so a second clone of this repo cannot
  silently fork it: `topic add` and `add` refuse there, everything else
  warns, and `queue.sh root` names the directory in use.
- `scripts/lib/forge.py` — the FORGE seam. Everything fleet knows about a
  change request — a pull request on GitHub, a merge request on GitLab — it
  asks this module for; `scripts/lib/queue.py` runs no forge CLI itself and
  builds no forge URL. TWO implementations ship — GitHub through `gh`, GitLab
  through `glab` — and each is a CONFIGURATION and not an assumption, so a
  self-hosted instance is the ordinary case and not a special one. **Which
  hosts the GitLab adapter owns is READ OFF THE MACHINE**, from `glab auth
  status`; `configured_hosts`' own docstring owns that rule and its two
  overrides, and the file's header owns the interface and how to add a third.
  Two things follow: a repository is identified by HOST plus path
  (`github.com/Thurbeen/fleet`), because a bare `owner/repo` names two
  different repositories once two forges exist; and **the seam is driven, not
  asserted** — `queue-selftest.sh` §13 and §14 run `collect`, the landing check
  and `shepherd` through a second forge with `gh` on PATH as a tripwire. That
  is the bar every other seam here is judged against.
- `orchestration/reconcile/` — the reconciler's runtime state: its supervisor's
  pid, the heartbeat proving its loop is ticking, its log, the advisory `nudge`
  flag, the `down` flag, and `notified.json` — which ready tasks the lead has
  already been woken about, so a transition is told once. That last one is
  runtime state and not a record for the same reason as all the others: "the
  lead has been told" is true of one machine's loop and one conversation, and
  writing it onto a task would make the loop a second writer over the queue.
  Written by `./scripts/reconcile.sh` and created on first start. The loop's
  code is tracked; nothing it writes is.
- `interface/fleet_queue.lua` — the TUI queue pane, and the fleet's only live
  view of the queue, drawn in a thurbox column over the same records
  `queue.sh list` reads. `scripts/install-extension.sh` installs it
  with `thurbox-cli plugin install`; the file's own header owns the view.
  **Placing it is a guarded block in the user's `layout.lua`, and
  `./scripts/place-pane.sh` writes that block — only ever after the operator
  was ASKED and said yes** — because a pane no arrangement places loads, lists,
  and draws nothing. It refuses a layout it cannot recognise, backs the file up,
  re-reads its own edit with `lua`, and verifies with `thurbox-cli plugin
  check`; the fleet-pane skill's §4 owns the ask. **The first Mission Control
  session asks it, once per checkout, ever**: FLEET.md has the lead run
  `./scripts/pane-ask.sh`, which keeps the answer in the gitignored
  `orchestration/first-run/` and never asks where the pane is already placed —
  a script and not a hook, because a hook would be one agent's.
  `./scripts/pane-selftest.sh` renders it offline — no thurbox, no queue, no
  session; `check.sh pane` runs it.
  `.agents/skills/fleet-pane/` is the driving surface for all of it: install,
  verify, place, hide, remove, diagnose.
- `orchestration/playbooks/<name>.md` — reusable recipes for running thurbox.
  All tracked; write new ones here, from `_TEMPLATE.md`.
- `orchestration/runs/<date>-<topic>.md` — a log per orchestration run, one
  per topic. **The queue writes it**: `topic add` opens it from `_TEMPLATE.md`
  and the loop's own commands rewrite a fenced block of facts inside it.
  Everything outside that fence is the lead's judgement and nothing ever
  overwrites it. Gitignored, like everything a run produces; the template is
  the one tracked file there.
- `install.sh` — the one-liner (`curl … | sh`), in POSIX sh: clone or
  fast-forward the checkout, `preflight.sh`, `install-extension.sh`, and
  nothing else. It installs no dependency, places no pane, and refuses rather
  than overwrites an existing checkout; its header owns where the clone goes and
  why that choice is sticky. `./scripts/install-selftest.sh` drives it and
  `pane-ask.sh` end to end; `check.sh install` runs it.
- `scripts/preflight.sh` — every dependency fleet needs, in one pass, in three
  tiers (required / recommended / gate), each row carrying what breaks without
  it and the command that installs it. It probes and prints; installing is the
  operator's, which is what `--commands` is for. `scripts/discover-owners.sh`
  is its counterpart for the one input the map needs: it reads every `gh`
  account, the git config and the remotes of the clones already on the disk,
  and prints owner candidates with the evidence for each. Both write nothing.
  EVERY `gh` ACCOUNT, not just the active one, there and in
  `scripts/sync-registry.sh` — a machine with several logins reaches a
  different set of repositories per login. `scripts/lib/gh-accounts.sh` is the
  seam every reader goes through and its header owns the mechanism; the one
  thing to know here is that it reads each login's token BY NAME and never
  switches the account the operator's `gh` is pointing at. **Neither CLI's own status
  command answers the question preflight has**, so both authentication rows go
  through a seam instead: `gh auth` is decided per ACCOUNT, and `glab auth` per
  HOST through `scripts/lib/glab-hosts.sh` — a bare `glab auth status` is
  all-or-nothing across every instance glab has configured, so it called a
  self-hosted-only setup broken, which the forge seam says is the ordinary one.
- `scripts/add-owner.sh` — the incremental half, for what the operator gains
  AFTER a first run: an owner, a repo, or a whole account. It names the owners
  the current `gh` accounts reach that `registry/owners.txt` does not list,
  grouped by the account that reaches them; with `--all` or a named list it
  APPENDS them — header and order kept, a duplicate refused — then syncs and
  reports what moved rather than the whole map. It logs nobody in, and a GitLab
  host is reported as evidence and never as an owner. The fleet-onboarding
  skill's **Re-running** section owns the ask that goes with it.
- `.agents/skills/<name>/SKILL.md` — agent skills, in one agent-agnostic tree.
  `.claude/skills` is a **symlink** to it, so Claude Code and opencode (which
  auto-discovers `.claude/skills`) both load the same copy. Never add a second
  copy under `.claude/`, and do not mirror into `.opencode/skills` — that
  registers the same skill twice. Five skills live there: `fleet-queue` (the
  queue: intake, ordering, dispatch, and the two halves of completion),
  `thurbox-session` (driving one worker session), `fleet-onboarding` (a fresh
  clone to a working control plane: dependencies, owners, registry, extension,
  the pane on screen, the loop up),
  `fleet-pane` (getting the TUI queue pane onto a screen, and diagnosing one
  that is installed and drawing nothing), and `update-fleet` (a working control
  plane that is BEHIND origin, and the consequences of the sync that
  `scripts/sync-checkout.sh` only ever reports).

## Orchestration model

This repo drives [thurbox](https://github.com/Thurbeen/thurbox) **directly**. Do
not invoke an external `orchestrate` skill or any other outside orchestration
workflow — the control plane is self-contained.

The loop, driven by `./scripts/queue.sh`, whose header is its full usage and
whose rules `.agents/skills/fleet-queue/` owns. What follows is the index, not
a second copy — read the skill before you run any of it:

1. **Intake.** A prompt becomes a topic, kept verbatim, decomposed into tasks —
   one repo, one branch, one thing a single worker can finish and validate.
2. **Write each `BRIEF.md`.** Workers share no context with you and none with
   each other, so each brief states the goal, the constraints, and what "done"
   looks like, from scratch. `dispatch` refuses a brief that is still the
   scaffold's placeholder.
3. **Order, then dispatch the whole ready set at once.** File or subsystem
   overlap is a RISK SIGNAL that gets reported rather than held back; serialize
   only for a concrete condition that makes independent progress unsafe, and
   record it with `queue.sh block`. **A blocker names a task or a CONDITION**,
   and only a person clears the second kind — nothing releases a task on a
   guess. A queue that runs one task at a time is slower than no queue at all.
4. Each worker targets a real repo and its own git worktree — the control plane
   holds the plan and the log, never the workers' branches. `dispatch` gets each
   new session past its agent's trust dialog before it sends the brief
   (`./scripts/session-trust.sh`), because sending one into that dialog is how
   every fleet-spawned worker used to break. A task may name a `--host` and run
   on that machine instead, probed first and carried by ssh, so that completion
   stays one model.
5. **Completion is two things you read, never something that interrupts you.**
   `queue.sh watch` folds thurbox's event stream into each task's record and
   closes nothing; `queue.sh collect` reads the `result.md` the worker wrote,
   and only that closes a task. A turn ending is not a task finishing. The run
   log refreshes its own facts as this happens, which leaves you the half no
   record can hold: the goal in your words, the decisions, what went wrong.
   Write those in while you still know them.
6. **Release is a third thing, and it is not manual.** `shipped` means the
   artifact exists and the session is kept, because it is the cheap way to fix
   what review finds. Only the FORGE saying it merged moves a task to `landed`,
   and `queue.sh reap` — which `collect` runs itself — deletes the session
   then. It never touches one that is working, blocked, or was given up in:
   that session is the evidence. Blockers clear on `landed`, and a topic whose
   every task is terminal archives itself.
7. **The change request outlives the task, so `queue.sh shepherd` is a fourth
   thing, run as reflexively as `collect`.** It asks the FORGE for every open
   change request on the repos the queue names, not the tasks' recorded
   artifacts, and merges only where the operator's own
   `orchestration/auto-merge.conf` says it may — the tracked example names
   NONE, so a fresh clone of this public repo merges nowhere. Squash is the
   only method it merges by. `--dry-run` first.
8. **A worker that hits its agent's token limit does not fail — it sits, and
   nothing above ever notices.** `queue.sh refuel` is a fifth thing: the
   account's shared quota window first, and a restart only when a stale
   `working` is paired with the agent's own limit signal.
9. Review the change requests; the operator merges every one `shepherd` did
   not, and everything after that is `reap`'s.

**Nothing above happens because somebody remembered to run it.**
`./scripts/reconcile.sh` is a supervised loop — `ensure` / `start` / `stop` /
`status`, a supervisor pid, a log and a durable `down` flag, under the rule
that `ensure` honours the flag and `start` clears it. It consumes `queue.sh
watch` continuously and calls `collect`,
`shepherd` and `refuel` on separate intervals; its header argues every number
and is the full usage. Four things about it are load-bearing:

- **It writes no record.** Every effect on the queue goes through
  `./scripts/queue.sh`, which
  stays the only writer over the records; its own runtime directory above holds
  the rest. It calls exactly `watch`, `collect`,
  `shepherd`, `refuel` and the read-only `plan`, and
  `scripts/reconcile-selftest.sh` asserts that the set is those five and argues
  in place why a READ may join it while `dispatch` never may.
- **It reconciles; it does not decide.** No dispatch, no cancel, no reorder,
  and it does not re-decide `refuel`'s rule about a spent quota window.
- **It tells the lead when the ready set grows, which is the one thing it says
  out loud.** A task whose blocker clears is ready and has no actor: the loop
  may not dispatch, and the lead only acts when spoken to — on 2026-09-10 that
  cost six and a half hours. So after `collect` it reads `plan` and, when the
  ready set has grown, types one line into the lead's terminal naming what is
  ready and the command that sends it. Once per transition, never into a lead
  mid-turn, and silent when no lead session is running.
  `scripts/lib/notify_lead.py` owns those three rules. Notifying is not
  deciding: nothing moves, and the choice is still the lead's. **It says
  exactly what `plan` says is ready and derives nothing**, which is how a
  condition-held task stays out of the line: `is_ready` never clears a
  condition, so the one reading carries the answer and there is no second
  opinion here to keep in step.
- **`nudge` is the accelerator and never the guarantee.** A worker's Claude
  Code `Stop` hook can call `./scripts/reconcile.sh nudge` to bring the
  periodic pass forward; a worker that died on a token limit fires no hook at
  all, which is why the timer is what the design rests on. `reconcile.sh hook`
  PRINTS the block rather than installing it — that file
  (`~/.config/thurbox/hooks/claude.json`) is thurbox's, and a thurbox update
  rewrites it. `nudge` runs no queue command, so a worker firing it can never
  collect or reap itself.

`.agents/skills/fleet-queue/` is the driving surface for 1–3 and 5–8, and
`.agents/skills/thurbox-session/` for one session: spawning, prompting, cleanup.
Use both. In particular, read the latter's **session state** section before you
judge whether a worker is still working: `idle` means the agent said it is at
rest, and `running`, `uncovered` and `unreported` each mean something else. Its
§1c and §1d cover the two choices every spawn makes — what a name collision
means, and what settings the agent starts with.

## Keeping the map honest

- After adding, renaming, or archiving a repo — or after editing
  `registry/owners.txt` — run `./scripts/sync-registry.sh` locally. Never edit
  the generated YAML directly. There is nothing to push: both files are
  gitignored.
- When you learn something durable about a project (its purpose shifted, a new
  dependency between repos, a parked goal), update its
  `registry/context/<repo>.md`. That file is where judgement lives.

## Gates

CI only runs on pull requests, and routine control-plane changes go straight to
`main`. So gate locally before you push:

```bash
./scripts/check.sh          # every check
./scripts/check.sh --fix    # same, applying the fixes a check can apply
```

That one script is the whole gate, and its header names every check it runs. CI
runs it, the prek hooks run it, and `.no-mistakes.yaml` points its `lint`
command at it, so a green local run and a green pull request mean the same
thing.

Changes that open a pull request land by **squash merge** — the only merge
method the remote allows — so the pull request title becomes the commit on
`main`. `CONTRIBUTING.md` owns that process.

`extension.toml` is generated by `./scripts/install-extension.sh` and gitignored.
Edit `extension.toml.in` instead, and re-run the installer. Two consequences to
know before you debug the extension:

- **Re-installing does not move the Mission Control session.** thurbox reuses
  an extension's session by name and never repoints it, so after the clone
  moves, a re-install rewrites the manifest and changes nothing that runs. The
  installer detects the mismatch and exits non-zero naming the remedy
  (`thurbox-cli extension deactivate fleet`, which deletes the session, then
  install again). `extension status` will not catch it — it checks that the
  session exists, not where it points.
- **`thurbox-cli extension update fleet` re-reads the *rendered* file**, not
  `extension.toml.in`, because the install stamped this clone as the extension's
  `source`. So it refreshes to whatever was last rendered, and fails outright if
  `extension.toml` was cleaned away. `./scripts/install-extension.sh` is this
  extension's real update command.
- **The lead SESSION is Mission Control; the EXTENSION is still `fleet`**,
  which is why every command above still takes `fleet`. The extension registers
  **no agent of its own**: the lead binds to a stock thurbox agent — `AGENT` in
  `orchestration/agent.conf`, rendered into the manifest, else thurbox's own
  `claude` — so it inherits the hook settings that let it report state and
  whatever model that agent defaults to.
  `extension.toml.in`'s no-`[[agents]]` note owns why. A
  glyph is part of the session name because thurbox has no per-session icon
  field, and WHICH glyph is a setting (see the glyph bullet above), so the
  mailbox address must be **pasted** out of `thurbox-cli session list`, not
  typed — no keyboard has either glyph, and nothing here spells the full name
  except the rendered manifest. `extension.toml.in`'s RENAMING header owns the
  split and the sequence for renaming either, including which one costs the lead
  its conversation.

## Pulling changes in

`./scripts/sync-checkout.sh` fast-forwards this checkout from `origin`, and the
`SessionStart` hook runs it. It only ever fast-forwards and refuses rather than
forces on a dirty tree, a feature branch, or a divergence.

**After a sync that touched `FLEET.md`, `AGENTS.md` or `.agents/skills/`, the
running Mission Control session is holding stale instructions** — it froze
them at launch and nothing reloads them from disk. This is equally true of a
plain `git pull`. The sync script says so when it happens; act on it rather
than assuming the new instructions reached the lead.

`.agents/skills/update-fleet/` drives that whole update — the sync, then only
the pieces it left stale (extension manifest, queue pane, registry,
reconciler), then the lead hand-over the sync can only report. It is the
counterpart to `fleet-onboarding`: that one builds a fleet, this one catches a
working one up.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in
this project. Do not repeat what the codebase already shows; point to the
authoritative file or command instead. Prefer rewriting or pruning existing
entries over appending new ones. When updating this file, preserve this bar for
all agents and keep entries concise.
