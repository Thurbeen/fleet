# AGENTS.md — operating guide for this control plane

This is a **control-plane** repo. When you work here you are helping orchestrate
and map projects, not shipping application code.

This file is the one copy. `CLAUDE.md` beside it is a pointer that imports it,
and imports the gitignored `FLEET.rendered.md` beside it too — which is how the
lead's standing context reaches a session whose working directory is this
checkout, since nothing reads the copy the extension lays down under its own
home. Edit this file or `FLEET.md`, never the pointer and never the rendered
copy.

**A change to fleet itself gets its own session.** Mission Control does not
edit this checkout in place: it holds the queue's records, the registry map and
the reconciler's runtime state, and `uv run fleet sync-checkout` refuses to
fast-forward a dirty tree — so editing here leaves the machine that dispatches
work unable to update itself. It dispatches a worker onto its own worktree
instead, which is why you are reading this. `FLEET.md`'s **What you delegate**
owns the rule and the one thing that stays inline: what a run WRITES, never
what fleet IS.

## What this repo is

The machinery is tracked; what a running fleet writes is not — this repo is
public, and that content is not something to publish. `.gitignore`'s header
names every path and the reason for each.

- `pyproject.toml`, `uv.lock` and `fleet/` — fleet is a uv project, and
  `uv run fleet <group> …` is its one command on Linux and native Windows;
  `uv run fleet --help` lists every group. `fleet/cli.py` loads the
  `scripts/lib/` module a group names, by path, and each module's docstring
  owns its usage and its rationale. uv brings Python and PyYAML from `uv.lock`,
  so there is no system `python3` or PyYAML to install, and no bash to run.
  `tests/test_cli.py` holds the command, and `fleet check cli` runs it.
- `scripts/lib/agent_settings.py` — the AGENT seam, and the one reader of
  `orchestration/agent.conf`. Everything fleet knows about an agent — its trust
  dialog, its limit signal, where its transcripts are, which account it draws
  on — is a fact about that agent, so every one of those is sayable about ONE:
  put the agent in front of the key (`<agent>.LIMIT_BANNER=`), the way
  `AGENT_PROVIDERS` already puts it in front of a provider. Two keys are new:
  `<agent>.LIKE=` says this agent IS that one under another account, which is
  what hands it the built-in row of an agent fleet has WATCHED rather than a
  guessed one; `<agent>.ENV=` is that account, as the environment variables
  that select it, and it is ONE record read by both things that need it — the
  directory an agent's transcripts are in, and the environment `quota-axi` is
  run under to read that account's window. **Resolution is the agent's own
  line, then its `LIKE`'s, then the checkout-wide setting**, so a checkout with
  no dotted line behaves exactly as it did and the tracked example still names
  nothing. The module's docstring owns the rest.
- `scripts/lib/fleet_platform.py` — the PLATFORM seam: every place fleet
  behaves differently on POSIX and native Windows (thurbox's config directory,
  fleet's data directory, how a record reaches the disk, a lock, a detached
  spawn, whether a pid is alive) is one function with both branches inside it,
  so no caller reads `os.name`. Records are UTF-8 with LF on every OS: write
  them through it and read them with `encoding="utf-8"`.
  `tests/test_platform.py` proves each branch on the OS that takes it. **Name a
  thurbox path through `uv run fleet paths`** (`thurbox-config`,
  `thurbox-hooks`, `fleet-data`) rather than spelling `~/.config/thurbox`:
  thurbox keeps it under `%APPDATA%` on native Windows.
- `tests/` — the pytest suite, one area per subsystem, running on Linux and
  natively on Windows. `tests/harness.py` owns the two things every test
  stands on: `isolated_env` — a throwaway HOME, git config and `FLEET_*` roots
  — and the stub `gh`, `thurbox-cli`, `ssh`, `glab` and `quota-axi`, a package
  in `tests/stubs` installed as real executables first on PATH. Fleet's code
  runs there with any locale-encoded read or write as an error. Each area is a
  named check: `uv run fleet check --list` maps them. `tests/architecture/` is
  the one area that never runs fleet — it parses every module under `fleet/`
  and `scripts/lib/` and holds the constraints `CONTRIBUTING.md`'s `scripts/**`
  review rules state, which are true of lines no run reaches.
- `registry/owners.txt` — the GitHub owners the map covers, one per line.
  `registry/owners.example.txt` is the tracked copy it starts from.
- `registry/repos.generated.yaml` — generated index of every repo under those
  owners. **Never hand-edit it.** Refresh with `uv run fleet sync-registry`.
- `registry/context/<repo>.md` — the human-owned truth about a project: what it
  is, how it relates to others, current goals. Read the relevant one before
  reasoning about a project, and keep it short and current.
- `orchestration/session-profiles.yaml` — named default settings a worker
  session STARTS under (`--env`, and `--command` for a setting that is a flag),
  as opposed to where its work goes. `uv run fleet session-flags <profile>`
  renders one into `session create` flags. One file, one layer — edit it
  directly. The file's own header owns the rules that keep a profile safe.
- `orchestration/publish.example.conf` and `agent.example.conf` — the two
  settings that keep fleet agnostic about YOUR tools. The first holds the
  default publish method and the free-text command that produces it, plus the
  attestation marker your pipeline emits; `scripts/lib/queue.py` models five
  ARTIFACT SHAPES (`attested`, `pr`, `push`, `note`, `none`) and no tool
  names, so a publisher fleet has never heard of still works. `note` is a
  review or comment on the change request or issue a task records as its
  `target` (`fleet queue add --target`), and `none` is a deliverable no forge
  holds; both words, and `target`, are part of `task.yaml`'s contract. The
  second holds which agent your workers run, which provider `refuel` gates on,
  and how that agent says it hit a limit. **Both tracked copies name nothing**
  — `uv run fleet check automerge` fails one that does — so a fresh clone
  inherits no operator's pipeline, vendor or agent. Copy either to a gitignored
  `*.conf` beside it to set anything. The marker is read in TWO SHAPES — the
  JSON inside the comment, or the marker alone with the JSON in a fenced block
  after it — because a publisher that writes the second is a different SHAPE
  and not a different
  string, which no value of the setting would have reached. The retired word
  `no-mistakes` is still accepted wherever a method is read and means
  `attested`, so a record written before the rename still loads.
- `orchestration/agent-policy.example.conf` — the per-repository agent policy:
  which agents may serve which repositories. A rule maps a host-qualified
  prefix (`github.com/owner/repo`, `gitlab.example.com/group/project`) to an
  ordered comma-separated list of agents; the first is the default, the rest
  are allowed only with `--agent`. Tracked and naming none, so a fresh clone
  enforces nothing; copy it to a gitignored `agent-policy.conf` beside it to
  set anything. `add` and `dispatch` read it on every call, so an edit takes
  effect on the next one. A task whose checkout is on another machine, or
  whose repository cannot be read from `origin`, is refused when any policy is
  in force.
- `orchestration/session-glyphs.example.conf` — the mark fleet's sessions wear
  in the thurbox session list: `📡` on the lead, `🚀` on every worker, under ONE
  `GLYPHS=on|off` setting whose `off` is the one-cell `⌖` and no worker prefix.
  Tracked defaults; copy it to a gitignored `session-glyphs.conf` beside it to
  change anything, since editing a tracked file would leave
  `fleet sync-checkout` a dirty tree. Two readers and no third:
  `fleet install-extension` renders the lead's mark into the manifest, and
  `scripts/lib/queue.py` puts the worker's on at dispatch. **Changing it is a
  RENAME of the lead, and installing is not applying one** — see
  `extension.toml.in`'s RENAMING header.
- `orchestration/fleet.example.conf` — THIS FLEET'S NAME, which is what lets
  one machine run more than one. A fleet is a CHECKOUT — queue, registry, run
  logs, reconciler runtime and first-run answers all live in it — so two clones
  were always two fleets in every respect but two NAMES, and thurbox resolves
  both of those by name: the extension (`fleet`) and the lead
  (`<glyph> Mission Control`). `NAME=acme` renders them `fleet-acme` and
  `<glyph> Mission Control · acme` instead. Tracked and naming none, so an
  unnamed fleet renders exactly what fleet always rendered and a machine with
  one fleet never meets this; copy it to a gitignored `fleet.conf` beside it.
  ONE READER: `fleet install-extension`, at render time — everything else reads
  the rendered manifest or the live session list, so there is no second copy of
  the answer. **Naming a fleet that is already running is a RENAME** with
  everything `extension.toml.in`'s RENAMING header says one costs; name the
  SECOND fleet. A second fleet is a second clone:
  `sh install.sh --dir ~/fleet-acme --name acme` (`install.ps1` on Windows), and
  `uv run fleet queue`'s control-plane guard is what keeps two of them from
  writing one queue.
- `orchestration/queue/<topic>/` — the task queue. A prompt becomes a TOPIC
  holding its verbatim `PROMPT.md`; the topic decomposes into task directories,
  each with its own `task.yaml`, `BRIEF.md`, `progress.jsonl` and `result.md`.
  `uv run fleet queue` owns it end to end, and `scripts/lib/queue.py`'s
  docstring is the model and the full usage.
  Gitignored except the `README.md` that documents the layout, the `POLICY.md`
  every brief points its worker at instead of restating it, and
  `OPERATOR.example.md` — the form of the operator's own `OPERATOR.md`, which
  is theirs, stays ignored, and is pointed at by every brief scaffolded while
  it exists and is not empty. **The queue belongs to the control-plane
  checkout — the clone the Mission Control session opens — and not to whatever
  directory your shell is in**, so a second clone of this repo cannot
  silently fork it: `topic add` and `add` refuse there, everything else
  warns, and `fleet queue root` names the directory in use.
- `scripts/lib/forge.py` — the FORGE seam. Everything fleet knows about a
  change request — a pull request on GitHub, a merge request on GitLab — it
  asks this module for; `scripts/lib/queue.py` runs no forge CLI itself and
  builds no forge URL. TWO implementations ship — GitHub through `gh`, GitLab
  through `glab` — and each is a CONFIGURATION and not an assumption, so a
  self-hosted instance is the ordinary case and not a special one. **Which
  hosts the GitLab adapter owns is READ OFF THE MACHINE**, from `glab auth
  status`; `configured_hosts`' own docstring owns that rule and its two
  overrides, and the module's docstring owns the interface and how to add a
  third. Two things follow: a repository is identified by HOST plus path
  (`github.com/Thurbeen/fleet`), because a bare `owner/repo` names two
  different repositories once two forges exist; and **the seam is driven, not
  asserted** — `tests/queue/test_forge_seam.py` and `test_gitlab.py` run
  `collect`, the landing check and `shepherd` through a second forge with `gh`
  on PATH as a tripwire. That is the bar every other seam here is judged
  against.
- `orchestration/reconcile/` — the reconciler's runtime state: the `lock` its
  supervisor holds for its whole life, the heartbeat proving its loop is
  ticking, a pidfile for people, its log, the advisory `nudge` flag, the `down`
  flag, and `notified.json` — which ready tasks the lead has
  already been woken about, so a transition is told once. That last one is
  runtime state and not a record for the same reason as all the others: "the
  lead has been told" is true of one machine's loop and one conversation, and
  writing it onto a task would make the loop a second writer over the queue.
  Written by `uv run fleet reconcile` (`scripts/lib/reconcile.py`) and created
  on first start. The loop's code is tracked; nothing it writes is.
- `interface/fleet_queue.lua` — the TUI queue pane, and the fleet's only live
  view of the queue, drawn in a thurbox column over the same records
  `fleet queue list` reads. `uv run fleet install-extension` installs it
  with `thurbox-cli plugin install`; the file's own header owns the view.
  **Placing it is a guarded block in the user's `layout.lua`, and
  `uv run fleet place-pane` writes that block — only ever after the operator
  was ASKED and said yes** — because a pane no arrangement places loads, lists,
  and draws nothing. It refuses a layout it cannot recognise, backs the file up,
  re-reads its own edit with `lua`, and verifies with `thurbox-cli plugin
  check`; the fleet-pane skill's §4 owns the ask. **The first Mission Control
  session asks it, once per checkout, ever**: FLEET.md has the lead run
  `uv run fleet pane-ask`, which keeps the answer in the gitignored
  `orchestration/first-run/` and never asks where the pane is already placed —
  a command and not a hook, because a hook would be one agent's.
  `tests/pane/` renders it offline — no thurbox, no queue, no session;
  `fleet check pane` runs it.
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
- `install.sh` and `install.ps1` — the one-liners (`curl … | sh`,
  `irm … | iex`): install uv when it is missing, clone or fast-forward the
  checkout, and hand over to `uv run fleet install` (`scripts/lib/install.py`).
  That prints every missing dependency with this machine's command, asks once
  (`--yes` skips), installs through the package manager, links
  `.claude/skills`, merges the reconciler's Stop nudge into Claude Code's user
  settings, installs the extension and ends with preflight; a second run
  changes nothing. It places no pane, and refuses rather than overwrites an
  existing checkout; `install.sh`'s header owns where the clone goes and why
  that choice is sticky. `tests/install/` drives both bootstraps, and
  `tests/extension/` drives `fleet pane-ask` and `fleet voice-ask` —
  onboarding's ask for the two names, before the extension renders them.
- `uv run fleet preflight` — every dependency fleet needs, in one pass, in
  three tiers (required / recommended / gate), each row carrying what breaks
  without it and the command that installs it with this machine's package
  manager (winget on Windows). **The table is data**: `scripts/lib/preflight.py`
  holds it as records, so another module acts on exactly what preflight
  reports. It probes and prints; installing is the operator's, which is what
  `--commands` is for. `uv run fleet discover-owners` is its counterpart for
  the one input the map needs: it reads every `gh` account, the git config and
  the remotes of the clones already on the disk, and prints owner candidates
  with the evidence for each. Both write nothing. EVERY `gh` ACCOUNT, not just
  the active one, there and in `fleet sync-registry` — a machine with several
  logins reaches a different set of repositories per login.
  `scripts/lib/gh_accounts.py` is the seam every reader goes through and its
  docstring owns the mechanism; the one thing to know here is that it reads
  each login's token BY NAME and never switches the account the operator's `gh`
  is pointing at. **Neither CLI's own status command answers the question
  preflight has**, so both authentication rows go through a seam instead: `gh
  auth` is decided per ACCOUNT, and `glab auth` per HOST through
  `scripts/lib/glab_hosts.py` — a bare `glab auth status` is all-or-nothing
  across every instance glab has configured, so it called a self-hosted-only
  setup broken, which the forge seam says is the ordinary one.
- `uv run fleet add-owner` — the incremental half, for what the operator gains
  AFTER a first run: an owner, a repo, or a whole account. It names the owners
  the current `gh` accounts reach that `registry/owners.txt` does not list,
  grouped by the account that reaches them; with `--all` or a named list it
  APPENDS them — header and order kept, a duplicate refused — then syncs and
  reports what moved rather than the whole map. It logs nobody in, and a GitLab
  host is reported as evidence and never as an owner. The fleet-onboarding
  skill's **Re-running** section owns the ask that goes with it.
- `.agents/skills/<name>/SKILL.md` — agent skills, in one agent-agnostic tree.
  `.claude/skills` points at it — made by `uv run fleet install`, a symlink on
  POSIX and a junction on Windows, untracked and gitignored — so Claude Code
  and opencode (which
  auto-discovers `.claude/skills`) both load the same copy. Never add a second
  copy under `.claude/`, and do not mirror into `.opencode/skills` — that
  registers the same skill twice. Seven skills live there: `fleet-queue` (the
  queue: intake, ordering, dispatch, and the two halves of completion),
  `thurbox-session` (driving one worker session), `fleet-onboarding` (a fresh
  clone to a working control plane: dependencies, owners, registry, extension,
  the pane on screen, the loop up),
  `fleet-pane` (getting the TUI queue pane onto a screen, and diagnosing one
  that is installed and drawing nothing), `update-fleet` (a working control
  plane that is BEHIND origin, and the consequences of the sync that
  `fleet sync-checkout` only ever reports), `diagnose-machine` (what is eating
  this machine's CPU, RAM, swap and disk; what is safe to free, and the project
  whose code produced the debris), and `review-prs` (a maintainer's recurring
  review over a repository's open change requests, through to the merge).
  **The last two drive a session rather than running in the lead**: both read
  far more than their verdict is worth — process tables, `du` output, whole
  diffs — and a lead that ran them itself would carry all of it.

## Orchestration model

This repo drives [thurbox](https://github.com/Thurbeen/thurbox) **directly**. Do
not invoke an external `orchestrate` skill or any other outside orchestration
workflow — the control plane is self-contained.

The loop, driven by `uv run fleet queue`, whose module docstring
(`scripts/lib/queue.py`) is its full usage and whose rules
`.agents/skills/fleet-queue/` owns. What follows is the index, not
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
   record it with `fleet queue block`. **A blocker names a task or a
   CONDITION**, and only a person clears the second kind — nothing releases a
   task on a guess. A queue that runs one task at a time is slower than no
   queue at all.
4. Each worker targets a real repo and its own git worktree — the control plane
   holds the plan and the log, never the workers' branches. `dispatch` gets each
   new session past its agent's trust dialog before it sends the brief, calling
   `scripts/lib/session_trust.py` in-process (`uv run fleet session-trust` runs
   the same module by hand), because sending one into that dialog is how every
   fleet-spawned worker used to break. A task may name a `--host` and run on
   that machine instead, probed first and carried by ssh, so that completion
   stays one model.
5. **Completion is two things you read, never something that interrupts you.**
   `fleet queue watch` folds thurbox's event stream into each task's record and
   closes nothing; `fleet queue collect` reads the `result.md` the worker wrote,
   and only that closes a task. A turn ending is not a task finishing. The run
   log refreshes its own facts as this happens, which leaves you the half no
   record can hold: the goal in your words, the decisions, what went wrong.
   Write those in while you still know them.
6. **Release is a third thing, and it is not manual.** `shipped` means the
   artifact exists and the session is kept, because it is the cheap way to fix
   what review finds. Only the FORGE saying it merged moves a task to `landed`,
   and `fleet queue reap` — which `collect` runs itself — deletes the session
   then. It never touches one that is working, blocked, or was given up in:
   that session is the evidence. Blockers clear on `landed`, and a topic whose
   every task is terminal archives itself.
7. **The change request outlives the task, so `fleet queue shepherd` is a fourth
   thing, run as reflexively as `collect`.** It asks the FORGE for every open
   change request on the repos the queue names, not the tasks' recorded
   artifacts, and merges only where the operator's own
   `orchestration/auto-merge.conf` says it may — the tracked example names
   NONE, so a fresh clone of this public repo merges nowhere. Squash is the
   only method it merges by. `--dry-run` first.
8. **A worker that hits its agent's token limit does not fail — it sits, and
   nothing above ever notices.** `fleet queue refuel` is a fifth thing: the
   account's shared quota window first, and a restart only when a stale
   `working` is paired with the agent's own limit signal.
9. Review the change requests; the operator merges every one `shepherd` did
   not, and everything after that is `reap`'s.

**Nothing above happens because somebody remembered to run it.**
`uv run fleet reconcile` is a supervised loop — `ensure` / `start` / `stop` /
`status`, a log and a durable `down` flag, under the rule that `ensure` honours
the flag and `start` clears it. **Up is a lock plus a heartbeat**: the
supervisor holds an exclusive lock on `orchestration/reconcile/lock` for its
whole life, the OS drops it however that process dies, so no stale pid is ever
trusted or signalled, and a holder that never beats is never called healthy.
It consumes `fleet queue watch` continuously and calls `collect`, `shepherd`
and `refuel` on separate intervals; `scripts/lib/reconcile.py`'s docstring
argues every number and is the full usage. Four things about it are
load-bearing:

- **It writes no record.** Every effect on the queue goes through
  `fleet queue`, which stays the only writer over the records; its own runtime
  directory above holds the rest. It calls exactly `watch`, `collect`,
  `shepherd`, `refuel` and the read-only `plan`, and `tests/reconcile/` asserts
  that the set is those five and argues in place why a READ may join it while
  `dispatch` never may.
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
  Code `Stop` hook can call `uv run --project <checkout> fleet reconcile nudge`
  to bring the periodic pass forward; a worker that died on a token limit fires
  no hook at all, which is why the timer is what the design rests on.
  `uv run fleet install` merges the hook into Claude Code's user settings
  (`uv run fleet paths claude-settings`), never into thurbox's hooks file,
  which thurbox rewrites on every start; `fleet reconcile hook` prints it.
  `nudge` runs no queue command, so a worker
  firing it can never collect or reap itself.

`.agents/skills/fleet-queue/` is the driving surface for 1–3 and 5–8, and
`.agents/skills/thurbox-session/` for one session: spawning, prompting, cleanup.
Use both. In particular, read the latter's **session state** section before you
judge whether a worker is still working: `idle` means the agent said it is at
rest, and `running`, `uncovered` and `unreported` each mean something else. Its
§1c and §1d cover the two choices every spawn makes — what a name collision
means, and what settings the agent starts with.

## Keeping the map honest

- After adding, renaming, or archiving a repo — or after editing
  `registry/owners.txt` — run `uv run fleet sync-registry` locally. Never edit
  the generated YAML directly. There is nothing to push: both files are
  gitignored.
- When you learn something durable about a project (its purpose shifted, a new
  dependency between repos, a parked goal), update its
  `registry/context/<repo>.md`. That file is where judgement lives.

## Gates

CI only runs on pull requests, and routine control-plane changes go straight to
`main`. So gate locally before you push:

```text
uv run fleet check          # every check
uv run fleet check --fix    # same, applying the fixes a check can apply
uv run fleet check --list   # every check, and what it runs
```

That one command is the whole gate, and `scripts/lib/check.py`'s docstring
owns it. CI runs it on a Linux and a native Windows runner, the prek hooks run
it, and `.publish.yaml` declares it as the gate the `publish` skill runs, so a
green local run and a green pull request mean the same thing.
`CONTRIBUTING.md` owns that declaration and the review rules `.publish.yaml`
points at.

**The gate reads no operator state** — not the queue's records, the registry
map, a gitignored `*.conf`, your HOME or your git config — so one commit gets
one verdict in a worker's worktree, on CI and in this checkout alike.
`uv run fleet status --records` is where your live records are validated;
`fleet check isolation` is what keeps the gate from reading them again, and
every test runs inside `tests/harness.py`'s `isolated_env`.

Changes that open a pull request land by **squash merge** — the only merge
method the remote allows — so the pull request title becomes the commit on
`main`. `CONTRIBUTING.md` owns that process.

`extension.toml` is generated by `uv run fleet install-extension` and
gitignored. Edit `extension.toml.in` instead, and re-run the installer. Two
consequences to know before you debug the extension:

- **Re-installing does not move the Mission Control session.** thurbox reuses
  an extension's session by name and never repoints it, so after the clone
  moves, a re-install rewrites the manifest and changes nothing that runs. The
  installer detects the mismatch and exits non-zero naming the remedy
  (`thurbox-cli extension deactivate <this fleet's id>`, which deletes the
  session, then install again — `fleet`, or `fleet-<name>` where this fleet
  named itself, since the wrong id deletes another fleet's lead).
  `extension status` will not catch it — it checks that the session exists, not
  where it points.
- **`thurbox-cli extension update <id>` re-reads the *rendered* file**, not
  `extension.toml.in`, because the install stamped this clone as the extension's
  `source`. So it refreshes to whatever was last rendered, and fails outright if
  `extension.toml` was cleaned away. `uv run fleet install-extension` is this
  extension's real update command.
- **The lead SESSION is Mission Control; the EXTENSION is still `fleet`**,
  which is why every command above still takes `fleet` — or `fleet-<name>`
  once this fleet named itself (the `fleet.example.conf` bullet above). Every
  `thurbox-cli extension ...` command takes the id the RENDERED manifest
  carries, so read it there rather than assuming the bare word. The extension
  registers **no agent of its own**: the lead binds to a stock thurbox agent —
  `AGENT` in `orchestration/agent.conf`, rendered into the manifest, else
  thurbox's own `claude` — so it inherits the hook settings that let it report
  state and whatever model that agent defaults to.
  `extension.toml.in`'s no-`[[agents]]` note owns why. A
  glyph is part of the session name because thurbox has no per-session icon
  field, and WHICH glyph is a setting (see the glyph bullet above), so the
  mailbox address must be **pasted** out of `thurbox-cli session list`, not
  typed — no keyboard has either glyph, and nothing here spells the full name
  except the rendered manifest. `extension.toml.in`'s RENAMING header owns the
  split and the sequence for renaming either, including which one costs the lead
  its conversation.

## Pulling changes in

`uv run fleet sync-checkout` fast-forwards this checkout from `origin`, and the
`SessionStart` hook runs it (`uv run --frozen --quiet fleet sync-checkout`, one
command every shell parses alike, which exits 0 itself). It only ever
fast-forwards and refuses rather than forces on a dirty tree, a feature branch,
or a divergence.

**After a sync that touched `FLEET.md`, `AGENTS.md` or `.agents/skills/`, the
running Mission Control session is holding stale instructions** — it froze
them at launch and nothing reloads them from disk. This is equally true of a
plain `git pull`. The sync says so when it happens; act on it rather
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
