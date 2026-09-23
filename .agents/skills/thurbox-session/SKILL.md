---
name: thurbox-session
description: Spawn and drive a thurbox worker session with thurbox-cli. Use whenever the control plane is asked to do new work in a real repo. Covers single-repo and multi-repo (--add-repo / --add-dir) sessions, idempotent re-spawns (--on-existing), agent settings (--env / --command via session profiles), prompting, completion detection, and cleanup.
user-invocable: true
allowed-tools: Read, Edit, Write, Bash, Glob, Grep
---

## thurbox-session

FLEET.md's **What you delegate** owns whether a task runs inline or here. This
skill is the reference for driving one session once you have decided to spawn
it. The control plane holds the plan and the run log; workers hold the
branches. For more than one unit of work, drive it through `uv run fleet
queue` and the `fleet-queue` skill, which owns the records, the ordering and
both halves of completion; the commands here are what it runs underneath, and
what you run for a session you spawn by hand.

## Run loop

1. Sync the base branch (§1). `uv run fleet queue topic add` opens the run log.
2. `session create` with an `--on-existing` mode (§1c) and the run's profile
   flags (§1d); `uv run fleet session-trust <uuid>` (§1b).
3. `session send` one line (§3) — unless `created` came back `false`.
4. Read the result file the worker writes (§4); supervise with the state
   table (§4a).
5. Review the pull request. `session delete --force` when it closes out (§5).

## Interface: use the CLI

`thurbox-cli` is the reliable surface. The thurbox **MCP** tools
(`create_session`, `send_prompt`, …) are frequently not registered: check
once with ToolSearch, and if absent use the CLI. Every subcommand takes
`--json` (and `--pretty`); parse that.

## 1. Spawn

**Sync the base branch first.** A worktree inherits whatever the local base
branch points at, and a stale `main` produces a worker that does correct work
and opens a CONFLICTING pull request. This repo's `SessionStart` hook runs
`uv run fleet sync-checkout`; other repos have no such hook, so for those:
`git -C <repo> fetch origin && git -C <repo> merge --ff-only origin/main`.

`session create` is synchronous — the window is live when it returns.

```bash
thurbox-cli session create --name 'Run exec automations off the TUI thread' \
  --repo-path /abs/path/to/repo \
  --worktree-branch fix/automation-exec-nonblocking --base-branch main \
  --on-existing fail \
  --json
```

| Flag | Meaning |
|---|---|
| `--name` | 1–64 **bytes**, no slashes, no leading `.`; an imperative sentence, not a slug |
| `--repo-path` | absolute path to the **primary** repo |
| `--worktree-branch` | create a git worktree on this branch |
| `--base-branch` | base for the worktree (default `main`) |
| `--agent` | `claude`, `codex`, … (default from `agents.toml`) |
| `--parent` | lead session UUID, so `session list --parent` enumerates workers — **same host only**: leave it off with `--host` |
| `--host` | remote host from `hosts.toml`; worktree and window live there (§1a) |
| `--on-existing` | what a name collision means — never leave it defaulted (§1c) |
| `--env` / `--command` / `--arg` / `--reports-as` | how the agent starts; render them from a profile (§1d) |

Capture the returned UUID; every later command keys off it. `create --json`
also returns `created`, which is `false` when `--on-existing adopt` handed
back a session that was already there (§1c).

### Naming a session

An **imperative summary of the work, in sentence case**: `Run exec
automations off the TUI thread`, not `thurbox-automation-nonblocking` (reads
as an id) and not `Fix stuff`. Spaces are fine and round-trip through every
by-name command.

- **No repo prefix.** The repo is on the session (`cwd`) and in the run log.
- **Quote it.** The name is a mailbox address, and unquoted the shell splits
  it.
- **Keep it short.** The TUI's window list truncates, and two names that only
  differ past the cut are the same name to the operator.
- **The cap is 64 BYTES, whatever the error says.** `session create` refuses
  with *"Name too long (max 64 characters)"* but counts bytes: on thurbox
  2.19.5 a 61-character name wearing a 5-byte `🚀 ` was accepted at 64 bytes
  and refused at 65. A spawn that fails leaves that task `queued`.
- **Fleet's own sessions wear a mark**, under the one setting in
  `orchestration/session-glyphs.example.conf`. `fleet queue dispatch` puts it
  on a worker's name in-process; a session you spawn by hand gets it from
  `uv run fleet session-name <kind> '<title>'`, which is what
  `diagnose-machine` and `review-prs` call. Never type a glyph into a
  `--name`: `GLYPHS=off` could never take it back off.

## 1a. Remote hosts (`--host`)

Hosts come from thurbox's `hosts.toml`, in the directory `uv run fleet paths
thurbox-config` prints. With `--host`, **everything runs on the remote** — the
agent, the window, the worktrees — and only the TUI is local. So
`--repo-path` is a path on that host, a `BRIEF.md` you `Write` lands on YOUR
machine and has to be copied over, and the host needs its own credentials for
that repository's forge (forwarding your SSH agent fixes it and forwards every
key the agent holds).

**`uv run fleet queue add --host <name>` is the driven version of all of it**:
it refuses an unknown, unspeakable or unshared host at `add` time, probes
reachability, the repo and the forge credential before it spawns, copies the
brief over, and fetches `result.md` back — `fleet-queue` §1 and §4. Spawning
remotely by hand, check the same three things first; until they pass, spawn
locally. A remote worker that fails at its first `git` call looks exactly like
an agent bug.

A Windows/PowerShell host (`multiplexer = "psmux"` in `hosts.toml`) is not a
POSIX shell: `command -v` and `2>/dev/null` misfire, so use `Get-Command`, and
send a script as `powershell -NoProfile -EncodedCommand <UTF-16LE base64>`
rather than `-Command "..."`, whose quoting the host's shell rewrites. It also
turns off remote hook status — a Windows worker sits at `unreported` and you
read the pane — and psmux captures a pane with its spaces gone
(`Yes,Itrustthisfolder`), so match with whitespace stripped.

**The pane IS reachable on a remote host.** `session get`, `capture`, `key`
and `send` each delegate to the thurbox-cli on that machine, so `uv run fleet
session-trust` answers a remote dialog as it answers a local one. The
config-seeding fallback (`fleet trust-thurbox-dir`) writes THIS machine's
`~/.claude.json` and does not travel. A host with `share_sessions = false` is
the one where nothing can see the pane.

## 1b. Get past the trust dialog — as part of the spawn, not after it

An agent started in a directory it has not seen asks whether it may work
there, and thurbox mints a fresh worktree path per session. So a new worker
sits on that dialog, and `session send` types the brief INTO it. This broke
every worker fleet spawned.

```bash
uv run fleet session-trust <uuid>          # confirm → answer → confirm
uv run fleet session-trust <uuid> --json   # for a driver
```

`fleet queue dispatch` runs it for you. Run it yourself only for a session you
spawned by hand, before anything has been typed into the pane. It confirms the
dialog is there, answers with that agent's keys, and confirms it is gone; if
it cannot confirm either, it sends nothing and exits 3.

`uv run fleet session-trust --help` carries the per-agent table. The trap in
it: **`claude`'s default selection is `No, exit`**, so a bare Enter exits the
agent — the answer is Down, then Enter — and under a directory whose
`CLAUDE.md` imports a file outside it a second dialog follows (`Allow external
CLAUDE.md file imports?`), answered with its default `No`. `cursor` and `muse`
are not keystrokes at all: their trust is a launch flag, carried by the
`cursor-trusted` and `muse-trusted` profiles in
`orchestration/session-profiles.yaml` (read `muse-trusted`'s comment first —
`--yolo` drops the sandbox too). An agent not in the table is refused, not
guessed at; `TRUST_SIGNATURE` and `TRUST_KEYS` in `orchestration/agent.conf`
teach it one.

Answering inside a worktree records trust against the **repository's main
worktree path** (Claude Code, observed 2026-09-07), so the first worker in a
repo meets the dialog and later ones do not — but a ready set dispatched at
once against one repo draws it on every one of them.

**The fallback.** `uv run fleet trust-thurbox-dir <path>` (or
`--all-worktrees`) writes Claude Code's trust into `~/.claude.json` directly,
for a dialog that cannot be answered or to pre-seed an unattended run. Not the
default: it writes to a file the operator owns, needs a format per agent, and
every live Claude Code process rewrites that file, so a concurrent write can
clobber a seed. Trust is a real guard either way — only point either tool at
worktrees of repos you trust.

## 1c. Re-running a spawn (`--on-existing`)

`session create` defaults to `--on-existing allow`: a second create under a
name already in use makes a **second session** with that name, and every
by-name command then refuses rather than guesses — `session get`, `message
send --to`, `adopt` and `replace` too. Nothing recovers from that except
deleting one by id. So decide what a collision means, every time:

| Mode | Choose it when | What you get |
|---|---|---|
| `adopt` | re-running a playbook — reconciling desired state | the session already there, `created: false` |
| `fail` | a one-off spawn, where a collision is news | exit 1, nothing created, the session in the way named |
| `replace` | you have decided to start this work over | the old session **and its worktree** torn down, then a fresh one |
| `allow` | never, here | a twin, and by-name addressing broken for both |

`replace` deletes uncommitted work; reach for `session restart` first.

**With `adopt`, read `created` before you send.** `created: false` means the
work is already running: sending a brief interrupts a worker mid-turn and
prepends a stale instruction to whatever it was doing. Go and read its state
(§4a) instead.

## 1d. How the agent starts (`--env`, `--command`)

Model, effort, feature flags and the command line live in
`orchestration/session-profiles.yaml`, one named profile per set, and
`uv run fleet session-flags <profile>` renders one into flags — NUL-separated,
because a `--arg` is often a whole command line; split on NUL and pass each
piece. `fleet queue dispatch` renders the task's profile in-process. A
profile of the operator's own — a model, a thinking budget — goes in the
gitignored `session-profiles.local.yaml` beside it, where it replaces the
tracked profile of that name whole; editing the tracked file dirties the tree
`fleet sync-checkout` has to fast-forward. `--check` validates both files.

Two rules the gate enforces:

- **`THURBOX_*` is not yours to set.** thurbox's identity variables win over
  `--env`; `THURBOX_SESSION=x` does not fail, it is silently ignored, which is
  the worst kind of setting. The renderer refuses the key.
- **`--command` never ships silent.** Either `reports_as` names the hook
  family or `uncovered: true` accepts that there is none; dropping both, or
  writing both, is refused.

And one convention the gate cannot check: the file is public, so a credential
reaches a worker by inheriting the thurbox server's environment and never
lands in a file.

### The `--command` trap

`--command` launches any executable and is how a profile expresses a setting
that is a *flag*. It is mutually exclusive with `--agent`, and `--resume` is
refused for it. The trap is that thurbox reads hook coverage against the
**command's file stem**:

```text
--command /bin/sh --arg -c --arg 'exec claude'
  agent: "sh"  reports_as: null      hook_coverage: "none"  state: "uncovered"

… --reports-as claude
  agent: "sh"  reports_as: "claude"  hook_coverage: "full"  state: "unreported"
```

The first reports nothing, forever, and reads as idle while it works (§4a).
`--reports-as` changes nothing about what runs; it tells thurbox which agent's
hooks the pane speaks. For an agent thurbox ships no hooks for,
`--reports-as` is refused and `uncovered: true` is the declaration instead.
`thurbox-cli session reports-as <session> <agent>` makes the declaration after
the fact, `--clear` undoes it.

The declaration is about what FLEET can wire, not what the agent can do, and
`dispatch` resolves it against the live session document: a session that
reports gets told it reports, and only one silent at hand-off gets the warning
that `refuel` will not touch it and `reap` is by hand. Judge a live session
by `state_source` and `hook_reported`, never by the profile that spawned it.

## 2. Multi-repo mode

- `--add-repo PATH[@BASE]` — its **own isolated worktree** on the shared
  `--worktree-branch`, off `BASE` (default: the primary's base). The
  per-repo-PR shape.
- `--add-dir PATH` — attached **as-is**: no worktree, no branch. Reference
  material.

```bash
thurbox-cli session create --name 'Add a license header to every source file' \
  --repo-path /repos/a --agent claude \
  --worktree-branch chore/license-header --base-branch main \
  --add-repo /repos/b@main --add-repo /repos/c@master \
  --add-dir /repos/reference \
  --json
```

With two or more members thurbox launches the agent in a per-session
**symlink workspace** (`workspaces/<agent_session_id>/` under thurbox's data
directory), one symlink per repo, rebuilt on each launch and removed on
delete. The session's `cwd` still points at the primary repo. A multi-repo
**fork** lands in a fresh workspace, so `--last` / `--continue` finds no
parent; a **restart** keeps the workspace and resumes. Tell the worker it is
in a symlink workspace, that each repo is a subdirectory, and that it opens
**one PR per repo**. `worktrees[]` on `session get` enumerates the members,
each with `repo_path`, `worktree_path` and `branch`.

## 3. Prompt

```bash
thurbox-cli session send <uuid> '<single-line prompt>'
```

- **`send` takes a UUID, not a name.**
- **`send` types the text and presses Enter**, so a multi-line prompt fires
  the agent on its first line. For anything longer than a sentence, write a
  `BRIEF.md` into the worktree (`.worktrees[0].worktree_path` from `get
  --json`) and send `Read BRIEF.md and do what it says. Delete it before
  committing.` — or it lands in the pull request.
- **Do not send at all** when the spawn returned `created: false` (§1c).
- **`sent: true, submitted: true` is a claim about the keystrokes**, not the
  worker (§4c). A worker with a queue record is messaged with `uv run fleet
  queue send <ref> '<one line>'`, which writes down WHEN.

Workers share no context with the control plane and none with each other, so
each prompt states the goal, the constraints, and what "done" looks like.

## 4. Detect completion

**A worker writes a FILE. It does not send mail.** `thurbox-cli message send`
wakes its recipient — it injects into the lead's terminal and interrupts
whoever is talking to it. Completion arrives as two things the lead READS:

```text
the WHEN   thurbox-cli watch --json [--since <seq>]
           One line per transition, resumable by sequence number.

the WHAT   a result file the worker wrote when it knew what it had concluded.
```

**The stream alone is not enough.** An agent reports `done` at the end of
every turn, including the one where it gave up; a lead that treats "turn
ended" as "task done" closes tasks that failed. `uv run fleet queue` is
exactly this pair (`watch` closes nothing; `collect` reads the result file).
`orchestration/queue/POLICY.md`'s **Reporting back** is the result contract
the queue scaffolds; for a hand-spawned worker, end the brief with it
yourself. `not-applicable` is why a file beats polling `gh pr list`: the
absence of a pull request cannot be told from "still working", but a worker
saying so can.

The mailbox is still right for something genuinely urgent that a human should
see now, and wrong for routine completion.

**Fallback — sentinel + capture.** For a one-off worker with no record, have
it print a `===RESULT===` JSON sentinel and poll `thurbox-cli session capture
<uuid> --lines 400 --json` (default 200, max 10000), backing off. A missing
sentinel means "still working". Pane-scraping is the last resort: agent CLIs
are TUIs, and box chrome and wrapping make grepping a pane fragile.

## 4a. Session state: supervision, not completion

`session get`/`list --json` carry `state` — one word, always present:

| `state` | What it means |
|---|---|
| `working` | the agent's own hook says it is running a turn |
| `blocked` | the agent's own hook says it needs input or approval |
| `done` | the agent's own hook says a turn just finished |
| `idle` | **the agent said it is at rest** |
| `running` | an agent holds the pane and nothing has signalled — an observation, not a claim |
| `uncovered` | this agent is wired to report nothing, so its silence means nothing |
| `unreported` | the agent *can* report and has not yet |
| `unreachable` | a remote session whose host cannot be reached — **the TUI's word, never printed by the CLI** |
| `stopped` | parked by `session stop`; also `stopped: true` |

**The trap this table exists to prevent:** `idle` is not "no news". The last
five words are *not* the agent saying it is at rest, and treating any of them
as `idle` reports a worker mid-turn as finished. Read the word, never the
absence of one.

**`unreachable` you will not be told.** `session get` on a session whose host
has gone away answers with the state LATCHED before it went, so a worker that
last reported `idle` still reads `idle` an hour after its machine died. For a
remote session, ask the HOST (`ssh <host> true`) before you believe a resting
state; `fleet queue reap` does.

`get` and `list` answer differently, on purpose: **`session get --json` probes
the pane** (`--no-verify` skips it), which is the only way to see an agent
thurbox did not launch, so `get` answers `running` where `list` answers
`uncovered`. **`session list --json` does not probe** (a multiplexer query and
a `ps` per session), so `hook_corroboration`, `detected_agent`,
`hook_state_contradicted` and `foreground_process` are `null` — **"not
checked", not "nothing found"**. `session list --verify` buys `get`'s answer
per session.

Judge a state with the fields beside it: `hook_state_age_secs` (a `working`
from twenty minutes ago is a different fact from one from two seconds ago),
`hook_reported`, `hook_coverage` / `hook_states_reportable` (which words this
agent can produce — as of thurbox 2.19.0 that includes `grok` and `kimi`), and
`state_source`. Three names, three fields: `agent` is what the row was created
as, `reports_as` what a driver declared, `detected_agent` what is observably
running — `null` when several registered agents share an executable, which
answers `hook_corroboration: "foreign-agent"` with `state: "running"`. A remote
session answers `hook_corroboration: "unavailable"`.

**`cursor-agent` reports without a family thurbox ships**, so `hook_coverage`
stays `none`; its own user hooks file (`~/.cursor/hooks.json`) can call
`thurbox-cli session signal`, and `list.state` then follows those words —
measured on cursor-agent 2026.09.15: `sessionStart` → `idle`,
`beforeSubmitPrompt` / `preToolUse` / `postToolUse` → `working`, `stop` →
`done`, no permission-wait event. `refuel` still has no cursor limit banner,
so a spent cursor worker stays `undetermined`.

**None of this is a completion signal.** Use state to SUPERVISE — a `blocked`
worker waiting on an approval nobody will give, a `working` one whose report
has aged past anything plausible. Completion is §4's result file.

### 4b. A `working` that never ends — the session that ran out of fuel

An agent that hits its token limit **does not exit and does not report.** It
prints its own limit line and sits, so the session reads `working` hours
later. `session get` carries **no token, usage, cost or limit field**. Two
readings tell it from a slow turn:

| where | what it says |
|---|---|
| `session capture <uuid> --lines 200 --json` | the agent's banner, as rendered — `claude`'s reads `You've hit your session limit · resets 11:30pm (Europe/Paris)` |
| that agent's transcript (`claude`: `~/.claude/projects/**/<agent_session_id>.jsonl`) | the same event recorded: `"error": "rate_limit"`, `"apiErrorStatus": 429`, and the `quotaLimits` window with `rateLimitType` and `resetsAt` |

`agent_session_id` from `session get --json` names the transcript, and the
record has to be the LAST conversational entry. Neither reading is hardcoded:
`scripts/lib/queue.py` keeps one entry per agent fleet has WATCHED hit a
limit (`claude` today), an agent with no entry is `undetermined`, and
`LIMIT_BANNER` / `TRANSCRIPT_DIR` in `orchestration/agent.conf` teach it one.
Where the transcript is, is the AGENT's answer: read from its own `ENV` line
there, never from the lead's environment — the lead sits on one account and
the worker may be on another, and reading the lead's was how a second
account's transcripts went unread.

**Ask the account before you restart anything.** The limit is the operator's
subscription window, shared by every session on the machine; `quota-axi`
reads it. While it is spent, a restart resumes the worker straight into the
same wall. With fuel in the account, `session restart <uuid>` re-spawns with
`--resume`; answer the trust dialog again before you send anything. **For a
session the queue dispatched, `uv run fleet queue refuel` is all of the above
in one verb** — do not hand-restart those (`fleet-queue` §5c).

### 4c. "I sent it a message — did it land?"

`session get` cannot answer this, and the trap is that it looks like it can.
On 2026-09-09 a parked worker was sent new scope, `session send` reported
success, and ten minutes later the session read `state: done | hook: done |
age(s): 3043` — a state latched BEFORE the message, while the worker had
taken it, done the work and committed. Nothing in `session get` moves when an
agent accepts a queued message, and a `done` that predates your send is not
evidence of anything.

So compare against something the worker cannot fake, from an instant you
recorded: the head of its branch (it committed) and `thurbox-cli watch --json
--since <seq>` (it transitioned). `uv run fleet queue send` records the
instant and the branch head, and `list` / `show` print the comparison
(`fleet-queue` §4a). "Nothing has moved since" is a fact, not a verdict.

## 5. Collect and clean up

For a session the queue dispatched, its facts refresh themselves in the run
log; for one you spawned by hand, note it there yourself.

```bash
thurbox-cli session restart <uuid>          # kill window, re-spawn with --resume
thurbox-cli session delete <uuid> --force   # headless cleanup
```

Plain `delete` only soft-deletes the row and leaves the TUI to reap the
window and worktrees on its next sync, which never comes when the TUI is not
running. `--force` kills the window, removes the worktrees and the symlink
workspace, and cancels pending scheduled commands. `session restore <uuid>`
undoes a soft delete.

**A session the queue dispatched is not yours to delete by hand.** `uv run
fleet queue reap` releases those once the forge says their pull requests
merged, reading the state table above first (`fleet-queue` §5b).
