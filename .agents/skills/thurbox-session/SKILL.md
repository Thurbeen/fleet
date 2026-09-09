---
name: thurbox-session
description: Spawn and drive a thurbox worker session with thurbox-cli. Use whenever the control plane is asked to do new work in a real repo. Covers single-repo and multi-repo (--add-repo / --add-dir) sessions, idempotent re-spawns (--on-existing), agent settings (--env / --command via session profiles), prompting, completion detection, and cleanup.
user-invocable: true
allowed-tools: Read, Edit, Write, Bash, Glob, Grep
---

## thurbox-session

FLEET.md's **What you delegate** section owns whether a task runs inline or
here. This skill is the reference for driving the session once you've decided
to spawn one — naming, prompting, completion detection, cleanup. The control
plane holds the plan and the run log; workers hold the branches.

## Interface: use the CLI

`thurbox-cli` is the reliable surface. The thurbox **MCP** tools
(`create_session`, `send_prompt`, `capture_session_output`, …) are frequently
**not registered**. Check once with ToolSearch; if absent, don't retry — use the
CLI. Every subcommand takes `--json` (and `--pretty`) for machine-readable
output, which is what you should parse.

## 1. Spawn

**Sync the base branch first.** A worktree inherits whatever the local base
branch points at. A stale local `main` produces a worker that does correct work
and opens a CONFLICTING PR. Fixing that afterwards costs a force-push.

```bash
scripts/sync-checkout.sh   # fast-forwards main when clean; reports otherwise
```

This repo's `SessionStart` hook (`.claude/settings.json`) runs that, so the
control plane is current the moment a session opens. **Other repos have no such
hook** — for those, `git -C <repo> fetch origin && git -C <repo> merge --ff-only
origin/main` before `session create`, or check that
`git rev-list --count main..origin/main` is 0.

`session create` is **synchronous** — the tmux window is live when it returns.

```bash
thurbox-cli session create --name 'Run exec automations off the TUI thread' \
  --repo-path /abs/path/to/repo \
  --worktree-branch fix/automation-exec-nonblocking --base-branch main \
  --on-existing fail \
  --json
```

| Flag | Meaning |
|---|---|
| `--name` | 1–64 **bytes**, no slashes, no leading `.`; spaces are fine — write an imperative sentence, not a slug |
| `--repo-path` | absolute path to the **primary** repo |
| `--worktree-branch` | create a git worktree on this branch |
| `--base-branch` | base for the worktree (default `main`) |
| `--agent` | `claude`, `codex`, … (default from `agents.toml`) |
| `--parent` | lead session UUID, for lead/worker trees |
| `--host` | remote host from `hosts.toml`; worktree + tmux live there |
| `--on-existing` | what a name collision means — never leave it defaulted, see §1c |
| `--env` / `--command` / `--arg` / `--reports-as` | how the agent starts; render them from a profile, see §1d |

The first seven rows place the work; the eighth says what a name already in use
means (§1c) and the ninth shapes the agent that does it (§1d). Leave neither
defaulted: the defaults silently make a second session under the same name, and
give the new session whatever ambient environment the thurbox server has.

Capture the returned UUID — every later command keys off it. `create --json`
also returns **`created`**, which is `false` when `--on-existing adopt`
handed back a session that was already there; §1c is what to do with that.

### Naming a session

A session name is an **imperative summary of the work, in sentence case**: what
the worker is being asked to do, not an identifier for it.

```
Run exec automations off the TUI thread     good
Document the customization surface          good
thurbox-automation-nonblocking              bad — a kebab slug, reads as an id
Fix stuff                                   bad — says nothing
```

Spaces are allowed: `session create`, `session get --json`, and
`message send --to` all round-trip a spaced name intact. The rest of the rules
follow from how the name is used:

- **Imperative mood, sentence case.** Capitalize the first word only. Leave
  identifiers in the casing they already have — `gh`, `TUI`, `extension.toml`.
- **No repo prefix.** The repo is already on the session (`session get --json`,
  field `cwd`) and in the run log. Repeating it spends the 64-byte cap twice.
- **Quote it.** The name is a mailbox address — `message send --to 'Run exec
  automations off the TUI thread'`. Unquoted, the shell splits it on spaces and
  the send addresses something that isn't there.
- **The branch is not the name.** `--worktree-branch` stays kebab-case and may
  contain slashes (`fix/automation-exec-nonblocking`); `--name` may not.
- **Keep it short.** The TUI's window list truncates. Two names that only differ
  past the cut are the same name as far as the operator can see.
- **The cap is 64 BYTES, whatever the error says.** `session create` refuses
  with *"Name too long (max 64 characters)"*, but it counts bytes: measured
  against thurbox 2.19.5, a 61-character name wearing a 5-byte `🚀 ` is
  accepted at 64 bytes and refused at 65. So a name that fits in characters can
  still fail at spawn the moment it carries anything non-ASCII, and a spawn that
  fails takes its whole dispatch with it. `scripts/lib/queue.py`'s
  `session_name()` cuts by byte, on a codepoint boundary, for exactly that
  reason.
- **fleet's own workers wear a mark.** `queue.sh dispatch` puts `🚀 ` in front
  of the name it builds from the task title, under the one setting in
  `orchestration/session-glyphs.example.conf` that also decides the lead's. The
  convention above is unchanged — the name is still an imperative sentence, now
  with a glyph before it — and a session you spawn by hand wears nothing unless
  you type one.

## 1a. Remote hosts (`--host`)

Hosts come from `~/.config/thurbox/hosts.toml`; a host `foo` registers the
backend `ssh:foo`.

With `--host`, **everything runs on the remote**: the agent process, the tmux
window, and the git worktrees. Only the TUI is local. Three consequences:

- **`--repo-path` is a path on the remote host**, not locally. A local absolute
  path that happens to exist on your machine will simply not be found there.
- **The `BRIEF.md` trick needs the file on the remote.** `Write` puts it on your
  machine. Copy it over (`scp` / `ssh 'cat >'`) into the remote worktree, or the
  worker reads nothing.
- **The remote needs its own GitHub credentials** to clone, fetch, and push.
  Yours are not inherited. Forwarding your SSH agent fixes it, but forwards
  every key the agent holds — decide that before reaching for it.

Before spawning remotely, check all three, in this order:

```bash
ssh <host> true                                   # reachable?
ssh <host> 'ssh -T git@github.com'                # can it reach GitHub?
ssh <host> 'ls -d <repo-path>'                    # does the repo exist there?
```

Until all three pass, **spawn locally**. A remote worker will start and then
fail at its first `git` call, which looks like an agent bug and is not one.

A Windows/PowerShell host is not a POSIX shell: probes like `command -v` and
`2>/dev/null` misfire there; use `Get-Command`. `hosts.toml` spells such a host
by giving it a non-`tmux` `multiplexer` (`psmux`), and that also turns off its
remote hook status — a Windows worker never reports `working`/`done` on its
own, so its `state` sits at `unreported` and you read the pane instead.

**The pane IS reachable on a remote host.** `session get`, `session capture`,
`session key` and `session send` each delegate the whole verb to the
thurbox-cli on that machine, so `scripts/session-trust.sh` answers a remote
trust dialog exactly as it answers a local one. The config-seeding fallback
does not travel: `scripts/trust-thurbox-dir.sh` writes THIS machine's
`~/.claude.json`, and a remote agent reads the remote one. Run it on the host
if you need it. The one host where delegation is unavailable is one whose
`hosts.toml` entry sets `share_sessions = false`; there, nothing can see the
pane and nothing can answer the dialog.

**`./scripts/queue.sh add --host <name>` is the driven version of all of it**,
and is what the control plane should use rather than a hand-rolled spawn: it
refuses an unknown, non-POSIX or unshared host at `add` time, runs the three
probes above before it spawns, copies the brief into the remote worktree, and
fetches the worker's `result.md` back over ssh. `fleet-queue` §1 and §4 own it.

## 1b. Get past the trust dialog — as part of the spawn, not after it

An agent started in a directory it has not seen asks whether it may work
there, and thurbox mints a **fresh worktree path per session**. So a worker
sits on that dialog — the session exists, the pane is live, the agent has not
started — and `session send` then types the brief INTO the dialog. This broke
every worker fleet spawned.

**`scripts/session-trust.sh <uuid>` is the answer, and `./scripts/queue.sh
dispatch` runs it for you** between `session create` and the first `session
send`. Run it yourself only for a session you spawned by hand, and only in that
same window — before anything has been typed into the pane.

```bash
scripts/session-trust.sh <uuid>          # confirm → answer → confirm
scripts/session-trust.sh <uuid> --json   # for a driver
```

It **confirms the dialog is on the pane before sending anything**, answers with
the keys that agent needs, then confirms the dialog is gone. If it cannot
confirm either, it sends nothing and exits 3 — a session waiting on a dialog is
visible and fixable; a session that has been typed into randomly is neither.

The per-agent differences, one of which is a trap:

| agent | gate |
|---|---|
| `claude` | a dialog whose default selection is **`No, exit`**. A bare Enter DISMISSES it and the agent exits. Down, then Enter. |
| `codex` | a dialog; Enter accepts. Persists per repo root. |
| `pi`, `pi-signed` | a dialog; Enter accepts. Persists per path. |
| `grok`, `kimi` | no dialog inside a git repo, which a worktree always is. |
| `cursor`, `muse` | **not a keystroke** — a launch flag (`--trust`, `--yolo`). Use the `cursor-trusted` / `muse-trusted` profiles in `orchestration/session-profiles.yaml` (§1d). |

**Which path the trust is recorded against** (observed 2026-09-07, Claude Code):
answering inside a worktree records it against the **repository's main worktree
path**, not the worktree's own. So the first worker in a repo meets the dialog
and later ones do not — but a whole ready set dispatched at once against one
repo draws the dialog on every one of them simultaneously, because none has
been answered yet when they start.

**The config-seeding fallback.** `scripts/trust-thurbox-dir.sh` writes Claude
Code's trust into `~/.claude.json` directly:

```bash
scripts/trust-thurbox-dir.sh /abs/path/to/worktree   # one path
scripts/trust-thurbox-dir.sh --all-worktrees         # every existing one
```

Use it when a dialog cannot be answered, or to pre-seed before an unattended
run. It is **not** the default: it writes to a file the operator owns, for a
tool fleet did not install, it needs a different format per agent, and
`~/.claude.json` is rewritten by every live Claude Code process, so a concurrent
write can clobber a seed. Prefer answering.

Trust is a real guard either way — accepting it vouches for the code in that
directory, so only ever point either tool at worktrees of repos you trust.

## 1c. Re-running a spawn (`--on-existing`)

`session create` defaults to `--on-existing allow`: a second create under a name
already in use makes a **second session** carrying that name. In this control
plane that is a one-way door.

A worker's name is an imperative sentence describing the work, and it is also
its **mailbox address**. Once two sessions share one, every by-name command
refuses rather than guesses — `session get`, `message send --to`, and
`--on-existing adopt` and `replace` too, because there is no single session
for them to act on:

```text
'Ship the registry cache' matches 2 active sessions on local-tmux, so there is
no single one to adopt. Address them by id, or pick another name
```

Nothing recovers from that except deleting one by id. So decide what a
collision means, every time:

| Mode | Choose it when | What you get |
|---|---|---|
| `adopt` | re-running a playbook — reconciling desired state | the session that is already there, `created: false`. Creation becomes idempotent |
| `fail` | a one-off spawn, where a collision is news | exit 1, nothing created, the session in the way named |
| `replace` | you have decided to start this work over | the old session **and its worktree** torn down, then a fresh one |
| `allow` | never, here | a twin, and by-name addressing broken for both |

`replace` deletes uncommitted work in the old worktree. It is the answer for a
worker that is wedged and whose branch you do not want, and wrong for anything
else — reach for `session restart` first.

**The rule that goes with `adopt`: read `created` before you send.**

```bash
out=$(thurbox-cli session create --name "$name" ... --on-existing adopt --json)
id=$(jq -r .id <<<"$out")
if [ "$(jq -r .created <<<"$out")" = true ]; then
	# brand new — write the brief and send it (§3)
fi
```

`session send` types its text into the pane and presses Enter. Sending a brief
to a session that was adopted mid-turn does not restart it: it interrupts a
worker that is already doing the job and prepends a stale instruction to
whatever it was in the middle of. `created: false` means *the work is already
running* — go and read its state (§4a) instead.

## 1d. How the agent starts (`--env`, `--command`)

Everything so far is about the session. These are about the **agent** inside it:
model, effort, feature flags, and the command line itself. Do not write them on
the spawn command line — they live in `orchestration/session-profiles.yaml`, one
named profile per set of settings, and `./scripts/session-flags.sh` renders one
into flags:

```bash
mapfile -d '' -t flags < <(./scripts/session-flags.sh sweep)
thurbox-cli session create --name "$name" --repo-path "$repo" \
  --worktree-branch "$branch" --on-existing adopt "${flags[@]}" --json
```

`mapfile -d ''` because the flags come out NUL-separated: a `--arg` value is
often a whole command line. `./scripts/session-flags.sh sweep | tr '\0' '\n'`
is how you read them yourself, and `--check` validates every profile in
`session-profiles.yaml`, which is the one file there is.

Two rules the gate enforces, so a profile breaking either never reaches `main`:

- **`THURBOX_*` is not yours to set.** thurbox's identity variables always win
  over `--env`. Passing `THURBOX_SESSION=x` does not fail; the session simply
  still sees its real id, which makes it the worst kind of setting — one that
  looks applied and is not. The renderer refuses the key.
- **`--command` never ships without `--reports-as`.** This is the trap.

And one **convention**, which the gate does not check and does not pretend to:
the file is committed to a public repo, so nothing environment-specific goes in
it. A worker inherits the environment of the thurbox server that spawns it, so a
real credential belongs where that process gets its own (your shell profile,
your keyring, the agent's own login) and never lands in a file at all.

### The `--command` trap

`--command` launches any executable — a shell, a REPL, an agent with flags
thurbox has never heard of. It is how a profile expresses a setting that is a
*flag* rather than an environment variable. It is mutually exclusive with
`--agent` (thurbox refuses both), and `--resume` is refused for it too: a raw
command has no conversation to attach to.

The trap is that thurbox reads hook coverage against the **command's file
stem**, not against whatever is really in the pane:

```text
--command /bin/sh --arg -c --arg 'exec claude'
  agent: "sh"  reports_as: null      hook_coverage: "none"  state: "uncovered"
  hook_states_reportable: []

… --reports-as claude
  agent: "sh"  reports_as: "claude"  hook_coverage: "full"  state: "unreported"
  hook_states_reportable: ["working","blocked","done","idle"]
```

The first row is a session that reports nothing, forever, and therefore reads as
idle while it works — §4a has why `uncovered` is not `idle`. The second is the
same launch, declared: `--reports-as` changes nothing about what runs, it tells
thurbox which agent's hooks the pane speaks.

So the two ship together or not at all, and `session-flags.sh` refuses a profile
with `command` and no `reports_as`. `thurbox-cli session reports-as <session>
<agent>` makes the same declaration after the fact, with `--clear` to undo it.

## 2. Multi-repo mode

One session can span several repos. Two repeatable flags:

- `--add-repo PATH[@BASE]` — the repo gets its **own isolated worktree** on the
  spawn's shared `--worktree-branch`, off `BASE` (default: the primary's
  `--base-branch`). This is the per-repo-PR shape.
- `--add-dir PATH` — attached **as-is**: no worktree, no branch. For reference
  material the worker should read but not modify.

```bash
thurbox-cli session create --name 'Add a license header to every source file' \
  --repo-path /repos/a --agent claude \
  --worktree-branch chore/license-header --base-branch main \
  --add-repo /repos/b@main --add-repo /repos/c@master \
  --add-dir /repos/reference \
  --json
```

**What the worker actually sees.** With two or more members, thurbox launches
the agent in a per-session **symlink workspace**
(`~/.local/share/thurbox/workspaces/<agent_session_id>/`) holding one symlink
per repo, with the agent's cwd set there, so every repo appears as a
subdirectory. It is agent-neutral — thurbox passes no `--add-dir`-style flags to
Claude itself — symlinks only, rebuilt idempotently on each launch, and removed
on delete without touching the repos.

The consequences:

- The session's `cwd` field still points at the **primary** repo (display,
  editor, git context). The workspace is a spawn-time process-cwd detail, never
  stored.
- Single-repo sessions are unchanged — cwd is the repo directly.
- A multi-repo **fork** of a cwd-scoped agent lands in a fresh workspace, so
  `--last` / `--continue` finds no parent. Multi-repo **restart** keeps the same
  workspace and does resume.
- `task create` takes the same `--add-repo` / `--add-dir` flags.

Say so in the prompt: tell the worker it is in a symlink workspace, that each
repo is a subdirectory, and that it should open **one PR per repo**.

## 3. Prompt

```bash
thurbox-cli session send <uuid> '<single-line prompt>'
```

Two traps:

- **`send` takes a UUID, not a name.** Capture it from `create --json`.
- **`send` types the text and presses Enter**, so a multi-line prompt fires the
  agent on its first line and dumps the rest into a half-started turn. For
  anything longer than a sentence, write the prompt to a `BRIEF.md` in the
  worker's worktree and send a one-liner pointing at it:

```bash
# after `session create`, resolve the worktree path from `get --json`:
#   .worktrees[0].worktree_path
printf '%s\n' "$PROMPT" > "$WORKTREE/BRIEF.md"
thurbox-cli session send <uuid> 'Read BRIEF.md and do what it says. Delete it before committing.'
```

Tell the worker to delete `BRIEF.md` before committing, or it lands in the PR.

Workers share no context with the control plane and none with each other, so
each prompt states the goal, the constraints, and what "done" looks like, from
scratch.

**Do not send at all** when the spawn returned `created: false` — that session
was adopted, not created, and is already working on this (§1c).

**`send` tells you it typed, and nothing more.** `sent: true, submitted: true`
is a claim about the keystrokes, not about the worker — the agent may take the
message and work for an hour while every field in `session get` stands still
(§4c). If the worker has a queue record, message it with `./scripts/queue.sh
send <ref> '<one line>'` instead: same handoff, and it writes down WHEN, which
is the only thing that makes a later observation mean anything.

## 4. Detect completion

**A worker writes a FILE. It does not send mail.** `thurbox-cli message send`
**wakes** its recipient — it injects into the lead's terminal, so a worker
reporting in interrupts whoever is talking to the lead at that moment.

So completion arrives as two things the lead READS, on its own cadence:

```text
the WHEN   thurbox-cli watch --json [--since <seq>]
           One line per transition, resumable by sequence number. The lead
           reads it when it chooses and is never interrupted. It carries the
           honest state vocabulary of §4a.

the WHAT   a result file the worker wrote when it knew what it had concluded.
```

**Both halves are needed, and the stream alone is not enough.** A transition
says a turn ended. That is not the claim that the task finished — an agent
reports `done` at the end of every turn, including the one where it gave up.
A lead that treats "turn ended" as "task done" closes tasks that failed.

`./scripts/queue.sh` implements exactly this pair and is how the control plane
should run any real work: `watch` folds transitions into each task's record and
closes nothing; `collect` reads the worker's own result file and only then does
a task close. See `.agents/skills/fleet-queue/SKILL.md`. Put the result
contract at the end of every brief:

```markdown
Write <absolute path>/result.md when you finish or conclude you cannot:

---
outcome: shipped | stuck | failed | not-applicable
artifact: <PR url, or a commit url for a plain push, or omit>
---
What you actually did, and anything the lead must know.
```

`not-applicable` is why a file beats polling `gh pr list`: the absence of a PR
cannot be distinguished from "still working", but a worker saying so can.

**The mailbox still exists**, and `thurbox-cli message send --to <lead>` is
still the right tool for something genuinely urgent that a human should see
now. It is the wrong tool for routine completion, which is most of it.

**Fallback — sentinel + capture.** For a one-off worker with no queue record
behind it, have the worker print a `===RESULT===` JSON sentinel and poll:

```bash
thurbox-cli session capture <uuid> --lines 400 --json   # default 200, max 10000
```

Back off between polls. Treat a missing sentinel as "still working", not as
failure. Pane-scraping is the last resort in any case: agent CLIs are TUIs, and
box chrome, prefixes and line-wrapping make grepping a captured pane fragile.

`worktrees[]` is how you enumerate a multi-repo session's members: one entry per
repo, each with `repo_path`, `worktree_path`, and `branch`.

## 4a. Session state: supervision, not completion

`session get`/`list --json` carry the session's state. Read `state` — one word,
always present:

| `state` | What it means |
|---|---|
| `working` | the agent's own hook says it is running a turn |
| `blocked` | the agent's own hook says it needs input or approval |
| `done` | the agent's own hook says a turn just finished |
| `idle` | **the agent said it is at rest** |
| `running` | an agent holds the pane and nothing has signalled — an observation, not a claim about what it is doing |
| `uncovered` | this agent is wired to report nothing, so its silence means nothing |
| `unreported` | the agent *can* report and has not yet |
| `unreachable` | a remote session whose host cannot be reached — **the TUI's word, and not one the CLI ever prints** |
| `stopped` | parked by `session stop`; also `stopped: true` |

**The trap this table exists to prevent:** `idle` is not "no news". The last
five words above are *not* the agent saying it is at rest, and treating any of
them as `idle` reports a worker mid-turn as finished. Read the word, never the
absence of one.

**And `unreachable` has a trap of its own, which is that you will not be told
it.** It reaches the interface's session rows and stops there; `session get`
and `session list` cannot produce it. A session whose host has gone away
answers with the state that was LATCHED before it went — so a worker that last
reported `idle` still reads `idle` an hour after its machine died, and nothing
in the JSON says otherwise. For a remote session, ask the HOST (`ssh <host>
true`) before you believe a resting state. `queue.sh reap` does exactly that
before it deletes anything.

`get` and `list` answer differently, and the difference is intended:

- **`session get <uuid> --json` probes the pane** (pass `--no-verify` to skip).
  Only the probe can see an agent thurbox did not launch, which is why `get`
  answers `running` where `list` answers `uncovered`.
- **`session list --json` does not probe**, because that costs a multiplexer
  query and a `ps` *per session*. `hook_corroboration`, `detected_agent`,
  `hook_state_contradicted` and `foreground_process` / `foreground_command`
  are therefore `null` — **`null` means "not checked", not "nothing found"**.
  `session list --verify` buys `get`'s answer at `get`'s cost, per session.

Judge a state with the fields shipped beside it: `hook_state_age_secs` (a
`working` from twenty minutes ago is a different fact from one from two
seconds ago), `hook_reported` (silence is not `idle`), and `hook_coverage` /
`hook_states_reportable` (which words this agent can produce at all — as of
thurbox 2.19.0 that includes `grok` and `kimi` alongside the agents covered
before). `state_source` says whether the answer came from a hook or the
process.

Three names, three fields: `agent` is what the row was created as, `reports_as`
what a driver declared, and `detected_agent` what is observably running — a live
reading, never written back. `detected_agent` is `null` when the observation
cannot pick one profile (several registered agents can share an executable), and
that case answers `hook_corroboration: "foreign-agent"` with `state: "running"`:
an agent is there, and which one is not knowable from a process listing. A
remote session has no pane to look at from here and answers
`hook_corroboration: "unavailable"`.

A worked reading of a control-plane session created as a bare shell, which a
harness then launched Claude into:

```bash
thurbox-cli session get <uuid> --json | jq '{agent,detected_agent,state,state_source,hook_coverage,hook_corroboration}'
# {"agent":"zsh","detected_agent":"claude","state":"running",
#  "state_source":"process","hook_coverage":"none",
#  "hook_corroboration":"foreign-agent"}
```

`uncovered` from `list` and `running` from `get`, for the same session at the
same moment, and both are true.

**None of this is a completion signal.** `done` means *a turn* finished, not
that the work is finished — an agent reports `done` at the end of every turn it
takes. Use state to supervise: to spot a `blocked` worker waiting on an approval
nobody is going to give, or a `working` one whose report has aged past anything
plausible. Completion still arrives as the result file of §4, because only the
worker knows whether it is done.

### 4b. A `working` that never ends — the session that ran out of fuel

An agent that hits its token limit **does not exit and does not report.** It
prints its own limit line and sits, so the last hook state stands forever: the
session reads `working` hours later and nothing about it changes. `session get`
carries **no token, usage, cost or limit field** — do not look for one. Two
readings tell it apart from a genuinely slow turn:

| where | what it says |
|---|---|
| `session capture <uuid> --lines 200 --json` | the agent's own banner, as rendered: `You've hit your session limit · resets 11:30pm (Europe/Paris)` |
| `~/.claude/projects/**/<agent_session_id>.jsonl` | the same event recorded, and more precisely: `"error": "rate_limit"`, `"apiErrorStatus": 429`, and the `quotaLimits` window that rejected the turn — `rateLimitType` and `resetsAt` |

`agent_session_id` from `session get --json` is what names that transcript, and
the record has to be the LAST conversational entry: what follows a rejection in
a wedged session is bookkeeping, and a session that came back has an ordinary
turn after it.

**Ask the account before you restart anything.** The limit is not the session's,
it is the operator's subscription window, shared by every session on this
machine — `quota-axi` reads it. While it is spent, a `session restart` resumes
the worker straight into the same wall and burns the reset everyone is waiting
for. With fuel in the account, `session restart <uuid>` re-spawns with
`--resume`, so the conversation and the brief survive; answer the trust dialog
again (§1b) before you send anything into the new pane.

**For a session the queue dispatched, `./scripts/queue.sh refuel` is all of the
above in one verb** — the account first, the conjunction, the trusted handoff,
a cap and a record. Do not hand-restart those; see `fleet-queue` §5c.

### 4c. "I sent it a message — did it land?"

`session get` cannot answer this, and the trap is that it looks like it can.
Observed on 2026-09-09: a parked worker was sent new scope, `session send`
reported success, and ten minutes later the session read

```text
state: done | hook: done | age(s): 3043
```

Fifty-one minutes of "no change", from a state latched **before** the message
was sent. The worker was neither dead nor unreachable: it had taken the
message, done the work and committed it.

Nothing in `session get` moves when an agent accepts a queued message and
starts thinking, and a `done` that predates your send is not evidence of
anything. Reading a foreign worktree's `git log` is what is left, and it does
not scale.

So compare against something the worker cannot fake, from an instant you
recorded:

| ask | what a change after your send means |
|---|---|
| the head of its branch, in the repo the worktree came from | it committed |
| `thurbox-cli watch --json --since <seq>` | its session transitioned |

`./scripts/queue.sh send` records the instant and the branch head for you, and
`queue.sh list` / `queue.sh show` print the comparison — see `fleet-queue` §4a.
None of it is a verdict about the worker: "nothing has moved since" is a fact,
and a worker that has not answered yet reads exactly like one that never got
the message.

## 5. Collect and clean up

For a session the queue dispatched, its facts refresh themselves in the run
log — see `fleet-queue`'s **The run log**. For one you spawned by hand, note
it there yourself; the judgement — goal, decisions, outcome — is always yours
to write.

```bash
thurbox-cli session restart <uuid>          # kill window, re-spawn with --resume
thurbox-cli session delete <uuid> --force   # headless cleanup
```

Plain `delete` only soft-deletes the DB row and leaves the TUI to reap the tmux
window and worktrees on its next sync. **When the TUI isn't running, pass
`--force`** — it kills the window, removes the worktrees (and the symlink
workspace), and cancels pending scheduled commands. `session restore <uuid>`
undoes a soft delete.

**A session the queue dispatched is not yours to delete by hand.**
`./scripts/queue.sh reap` releases those itself once the forge says their pull
requests merged, and it reads this section's state table before it does — see
`fleet-queue` §5b. The commands here are for sessions you spawned yourself.

## Run loop

1. Clarify the goal. Pick or write a playbook in `orchestration/playbooks/`.
2. `./scripts/queue.sh topic add` opens this run's log — see `fleet-queue`'s
   **The run log**.
3. Per unit of work: `session create` — with an `--on-existing` mode (§1c) and
   the run's profile flags (§1d) — → `session send`, unless `created` came back
   `false` → read the result file it writes → record.
4. Review PRs. `session delete --force` as each closes out — for a session
   the queue dispatched, `queue.sh reap` does this once the PR merges.

For more than one unit of work, drive it through `./scripts/queue.sh` and the
`fleet-queue` skill instead of by hand: it owns the records, the ordering, and
both halves of step 3's completion.
