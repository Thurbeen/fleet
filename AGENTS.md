# AGENTS.md — operating guide for this control plane

This is a **control-plane** repo. When you work here you are helping orchestrate
and map projects, not shipping application code.

This file is the one copy. `CLAUDE.md` beside it is a two-line pointer that
imports it, the same way `.claude/skills` is a symlink to `.agents/skills` and
the extension surfaces one `FLEET.md` under three names. Edit this file, not the
pointer.

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
- `orchestration/queue/<topic>/` — the task queue. A prompt becomes a TOPIC
  holding its verbatim `PROMPT.md`; the topic decomposes into task directories,
  each with its own `task.yaml`, `BRIEF.md`, `progress.jsonl` and `result.md`.
  `./scripts/queue.sh` owns it end to end and its header is the full usage.
  Gitignored except the `README.md` that documents the layout, the `POLICY.md`
  every brief points its worker at instead of restating it, and
  `OPERATOR.example.md` — the form of the operator's own `OPERATOR.md`, which
  is theirs, stays ignored, and is pointed at by every brief scaffolded while
  it exists and is not empty. **The queue belongs to the control-plane
  checkout — the clone the `mission control` session opens — and not to whatever
  directory your shell is in**, so a second clone of this repo cannot
  silently fork it: `topic add` and `add` refuse there, everything else
  warns, and `queue.sh root` names the directory in use.
- `orchestration/webui/` — the monitor's runtime state: the port it chose at
  bind time, its supervisor's pid, its log, and the `down` flag. Written by
  `./scripts/webui.sh` and created on first start. The server's code
  (`scripts/webui.sh`, `scripts/lib/webui.py`) is tracked; nothing it writes is.
- `interface/fleet_queue.lua` — the TUI queue pane: the same view the monitor
  serves, drawn in a thurbox column. `scripts/install-extension.sh` installs it
  with `thurbox-cli plugin install`; the file's own header owns the view, and
  `extension.toml.in`'s header argues why it is not an `[[external_files]]`
  payload. **Placing it is a guarded block in the user's `layout.lua` and
  nothing here writes it** — a pane no arrangement places loads, lists, and
  draws nothing.
- `orchestration/playbooks/<name>.md` — reusable recipes for running thurbox.
  All tracked; write new ones here, from `_TEMPLATE.md`.
- `orchestration/runs/<date>-<slug>.md` — a log per orchestration run.
- `.agents/skills/<name>/SKILL.md` — agent skills, in one agent-agnostic tree.
  `.claude/skills` is a **symlink** to it, so Claude Code and opencode (which
  auto-discovers `.claude/skills`) both load the same copy. Never add a second
  copy under `.claude/`, and do not mirror into `.opencode/skills` — that
  registers the same skill twice. Three skills live there: `fleet-queue` (the
  queue: intake, ordering, dispatch, and the two halves of completion),
  `thurbox-session` (driving one worker session), and `fleet-onboarding` (a
  fresh clone to a working control plane, including bringing the monitor up).

## Orchestration model

This repo drives [thurbox](https://github.com/Thurbeen/thurbox) **directly**. Do
not invoke an external `orchestrate` skill or any other outside orchestration
workflow — the control plane is self-contained.

The loop, driven by `./scripts/queue.sh`:

1. **Intake.** A prompt becomes a topic, kept verbatim, decomposed into tasks —
   one repo, one branch, one thing a single worker can finish and validate.
2. **Write each `BRIEF.md`.** Workers share no context with you and none with
   each other, so each brief states the goal, the constraints, and what "done"
   looks like, from scratch. `dispatch` refuses a brief that is still the
   scaffold's placeholder.
3. **Order, then dispatch the whole ready set at once.** File or subsystem
   overlap is a RISK SIGNAL that gets reported rather than held back. Serialize
   only for a true semantic dependency, shared mutable external state, an
   incompatible concurrent migration, or another concrete condition that makes
   independent progress unsafe — and record it with `queue.sh block`, which
   refuses one that names no kind and no reason. A queue that runs one task at
   a time is slower than no queue at all.
4. Each worker targets a real repo and its own git worktree — the control plane
   holds the plan and the log, never the workers' branches. `dispatch` gets each
   new session past its agent's trust dialog before it sends the brief
   (`./scripts/session-trust.sh`), because sending one into that dialog is how
   every fleet-spawned worker used to break. A task may name a `--host` from
   thurbox's `hosts.toml` and run on that machine instead; `--repo` is then a
   path THERE, three probes run before anything is spawned, and the brief and
   the result travel by ssh so that completion stays one model. No host means
   no change.
5. **Completion is two things you read, never something that interrupts you.**
   `queue.sh watch` folds `thurbox-cli watch`'s event stream into each task's
   record and closes nothing; `queue.sh collect` reads the `result.md` the
   worker wrote and only that closes a task. A turn ending is not a task
   finishing. Record the run in `orchestration/runs/` as it happens.
6. **Release is a third thing, and it is not manual.** `outcome: shipped` means
   a pull request is OPEN, and that session is the cheap way to fix what review
   finds — reaping at `collect` time once turned a follow-up message into a
   whole re-spawn. So a task moves to `landed` only when the FORGE says its
   artifact merged, and `queue.sh reap` — which `collect` runs itself — deletes
   the session and its worktree then. It never touches one thurbox says is
   working or blocked, nor one a worker gave up in: that session is the
   evidence. `reap --dry-run` says what it would do. Blockers clear on `landed`
   too, so a dependent task waits for the code to actually be on `main`.
7. **The pull request outlives the task, so `queue.sh shepherd` is a fourth
   thing, run as reflexively as `collect`** — which names it whenever it closed
   a task that left a PR open. It asks the FORGE for every open PR on the repos
   the queue's tasks name, not the tasks' recorded artifacts: a task records one
   artifact and #25 was a second PR from a task still pointing at the merged
   #23. A PR is linked back by artifact or head branch; an unlinked one is
   still classified and merged, it just has no session to fix it. It merges
   only in the repos `AUTO_MERGE_REPOS` names in `scripts/lib/queue.py`, and
   only for a PR whose head branch is in that repo, opened by someone who can
   push there, carrying a `no-mistakes` attestation for its **current** head —
   the five headings are text anyone can paste and were never the gate they
   looked like. `--dry-run` first; the fleet-queue skill owns the rest.
8. Review the PRs; the operator merges every one `shepherd` did not, and
   everything after that is `reap`'s.

`./scripts/webui.sh` serves a read-only web view of that same queue on
localhost — topics classified by what their tasks are doing, each with its plan,
progress and outcome. It READS the records and never writes them, so it cannot
disagree with `queue.sh list`. Its header owns the lifecycle; the one thing to
know before touching it is that `ensure` and `start` differ only in whether they
honour the `down` flag `stop` wrote, and the onboarding skill must call `ensure`.

`.agents/skills/fleet-queue/` is the driving surface for 1–3 and 5–7, and
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
./scripts/check.sh          # shellcheck, markdown, YAML, profiles, queue, monitor, status, skills, pane
./scripts/check.sh --fix    # same, applying the fixes a check can apply
```

That one script is the whole gate. CI runs it, the prek hooks run it, and
`.no-mistakes.yaml` points its `lint` command at it, so a green local run and a
green pull request mean the same thing.

Changes that open a pull request land by **squash merge** — the only merge
method the remote allows — so the pull request title becomes the commit on
`main`. `CONTRIBUTING.md` owns that process.

`extension.toml` is generated by `./scripts/install-extension.sh` and gitignored.
Edit `extension.toml.in` instead, and re-run the installer. Two consequences to
know before you debug the extension:

- **Re-installing does not move the `mission control` session.** thurbox reuses
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
- **The lead SESSION is `mission control`; the EXTENSION and its agent are
  still `fleet`**, which is why every command above still takes `fleet`. The
  session carries the operator's name for the lead, the extension carries the
  repo's — nothing reads the repo name, so the two are free to differ.
  `extension.toml.in`'s RENAMING header owns the split and the sequence for
  renaming either, including which one costs the lead its conversation.

## Pulling changes in

`./scripts/sync-checkout.sh` fast-forwards this checkout from `origin`, and the
`SessionStart` hook runs it. It only ever fast-forwards and refuses rather than
forces on a dirty tree, a feature branch, or a divergence.

**After a sync that touched `FLEET.md`, `AGENTS.md` or `.agents/skills/`, the
running `mission control` session is holding stale instructions** — it froze
them at launch and nothing reloads them from disk. This is equally true of a
plain `git pull`. The sync script says so when it happens; act on it rather
than assuming the new instructions reached the lead.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in
this project. Do not repeat what the codebase already shows; point to the
authoritative file or command instead. Prefer rewriting or pruning existing
entries over appending new ones. When updating this file, preserve this bar for
all agents and keep entries concise.
