---
name: thurbox-session
description: Spawn and drive a thurbox worker session with thurbox-cli. Use whenever the control plane is asked to do new work in a real repo. Covers single-repo and multi-repo (--add-repo / --add-dir) sessions, idempotent re-spawns (--on-existing), agent settings (--env / --command via session profiles), prompting, completion detection, and cleanup.
user-invocable: true
allowed-tools: Read, Edit, Write, Bash, Glob, Grep
---

## thurbox-session

New work brought to the control plane runs in a **dedicated thurbox worker
session**, not inline in this checkout. The control plane holds the plan and the
run log; workers hold the branches.

Exception: the control plane's own content — `registry/`, `orchestration/`,
`.agents/` — is edited inline and pushed straight to main.

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
| `--name` | 1–64 chars, no slashes, no leading `.`; spaces are fine — write an imperative sentence, not a slug |
| `--repo-path` | absolute path to the **primary** repo |
| `--worktree-branch` | create a git worktree on this branch |
| `--base-branch` | base for the worktree (default `main`) |
| `--agent` | `claude`, `codex`, … (default from `agents.toml`) |
| `--parent` | lead session UUID, for lead/worker trees |
| `--host` | remote host from `hosts.toml`; worktree + tmux live there |
| `--on-existing` | what a name collision means — never leave it defaulted, see §1c |
| `--env` / `--command` / `--arg` / `--reports-as` | how the agent starts; render them from a profile, see §1d |

The first seven rows place the work. The eighth says what a name already in
use means, and the ninth shapes the agent that does the work. This skill used
to cover only the first group, and the two gaps that left are worth naming: a
re-run silently made a second session under the same name, and every session
it spawned inherited whatever ambient environment the thurbox server happened
to have.

Capture the returned UUID — every later command keys off it. `create --json`
also returns **`created`**, which is `false` when `--on-existing adopt`
handed back a session that was already there; §1c is what to do with that.

### Naming a session

A session name is an **imperative summary of the work, in sentence case**. It
says what the worker is being asked to do; it is not an identifier for it.

```
Run exec automations off the TUI thread     good
Document the customization surface          good
thurbox-automation-nonblocking              bad — a kebab slug, reads as an id
Fix stuff                                   bad — says nothing
```

Spaces are allowed. `session create`, `session get --json`, and
`message send --to` all round-trip a spaced name intact; any claim that names
must be hyphenated is wrong. The rest of the rules follow from how the name is
used:

- **Imperative mood, sentence case.** Capitalize the first word only. Leave
  identifiers in the casing they already have — `gh`, `TUI`, `extension.toml`.
- **No repo prefix.** The repo is already on the session (`session get --json`,
  field `cwd`) and in the run log. Repeating it spends the 64 characters twice.
- **Quote it.** The name is a mailbox address — `message send --to 'Run exec
  automations off the TUI thread'`. Unquoted, the shell splits it on spaces and
  the send addresses something that isn't there.
- **The branch is not the name.** `--worktree-branch` stays kebab-case and may
  contain slashes (`fix/automation-exec-nonblocking`); `--name` may not.
- **Keep it short.** The TUI's window list truncates. Two names that only differ
  past the cut are the same name as far as the operator can see.

## 1a. Remote hosts (`--host`)

Hosts come from `~/.config/thurbox/hosts.toml`; a host `foo` registers the
backend `ssh:foo`.

With `--host`, **everything runs on the remote**: the agent process, the tmux
window, and the git worktrees. Only the TUI is local. Three consequences that
bite:

- **`--repo-path` is a path on the remote host**, not locally. A local absolute
  path that happens to exist on your machine will simply not be found there.
- **The `BRIEF.md` trick needs the file on the remote.** `Write` puts it on your
  machine. Copy it over (`scp` / `ssh 'cat >'`) into the remote worktree, or the
  worker reads nothing.
- **The remote needs its own GitHub credentials** to clone, fetch, and push.
  Yours are not inherited. Forwarding your SSH agent fixes it, but forwards
  every key the agent holds — decide that deliberately, don't reach for it
  reflexively.

Before spawning remotely, check all three, in this order:

```bash
ssh <host> true                                   # reachable?
ssh <host> 'ssh -T git@github.com'                # can it reach GitHub?
ssh <host> 'ls -d <repo-path>'                    # does the repo exist there?
```

Until all three pass, **spawn locally**. A remote worker will start and then
fail at its first `git` call, which looks like an agent bug and is not one.

A Windows/PowerShell host is not a POSIX shell: probes like `command -v` and
`2>/dev/null` misfire there; use `Get-Command`.

## 1b. Get past the trust dialog — as part of the spawn, not after it

An agent started in a directory it has not seen asks whether it may work
there, and thurbox mints a **fresh worktree path per session**. So a worker
sits on that dialog: the session exists, the pane is live, the agent has not
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

The per-agent differences are real, and one is a trap:

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

It still works and it is the right tool when a dialog cannot be answered, or to
pre-seed before an unattended run. It is **not** the default: it writes to a
file the operator owns, for a tool fleet did not install, and it needs a
different format per agent. Prefer answering.

Trust is a real guard either way: accepting it vouches for the code in that
directory. Only ever point either tool at worktrees of repos you already trust.
And `~/.claude.json` is rewritten by every live Claude Code process, so a
concurrent write can clobber a seed — one more reason the keystroke is better.

## 1c. Re-running a spawn (`--on-existing`)

`session create` defaults to `--on-existing allow`, which is what thurbox has
always done: a second create under a name already in use makes a **second
session** carrying that name. In this control plane that is a one-way door.

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

`replace` deletes uncommitted work in the old worktree. It is the honest
answer for a worker that is wedged and whose branch you do not want, and the
wrong answer for anything else — reach for `session restart` first.

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

Everything so far is about the session. These are about the **agent** inside
it: model, effort, feature flags, and the command line itself.

They are not written on the spawn command line. They live in
`orchestration/session-profiles.yaml` — one named profile per set of settings,
committed and reviewed like everything else here — and
`./scripts/session-flags.sh` renders one into flags:

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

The first row is a session that reports nothing, forever, and therefore reads
as idle while it works — see §4a for why `uncovered` is not `idle`. The second
is the same launch, declared. `--reports-as` changes nothing about what runs;
it tells thurbox which agent's hooks the pane speaks.

So the two ship together or not at all, and `session-flags.sh` refuses a
profile with `command` and no `reports_as` rather than trusting anyone to
remember. `thurbox-cli session reports-as <session> <agent>` makes the same
declaration after the fact, with `--clear` to undo it.

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
per repo, with the agent's cwd set there. Every repo appears as a subdirectory.
This is deliberately agent-neutral — thurbox passes no `--add-dir`-style flags
to Claude itself. The workspace is symlinks only, rebuilt idempotently on each
launch, removed on delete without touching the repos.

Consequences worth knowing:

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

Two traps, both learned the hard way:

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

## 4. Detect completion

**A worker writes a FILE. It does not send mail.** That is a deliberate
reversal of what this skill used to say, and the reason is concrete:
`thurbox-cli message send` **wakes** its recipient — it injects into the lead's
terminal, so a worker reporting in interrupts whoever is talking to the lead at
that moment. The property the CLI calls "immediate" is immediate in exactly the
way that hurts.

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
artifact: <PR url, or omit>
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

`session get`/`list --json` **do** carry the session's state. That is a
correction: this skill used to say the lifecycle state was persisted for the TUI
alone and not exposed by the CLI, and an agent that believed it never looked.

Read `state` — one word, always present:

| `state` | What it means |
|---|---|
| `working` | the agent's own hook says it is running a turn |
| `blocked` | the agent's own hook says it needs input or approval |
| `done` | the agent's own hook says a turn just finished |
| `idle` | **the agent said it is at rest** |
| `running` | an agent holds the pane and nothing has signalled — an observation, not a claim about what it is doing |
| `uncovered` | this agent is wired to report nothing, so its silence means nothing |
| `unreported` | the agent *can* report and has not yet |
| `unreachable` | a remote session whose host cannot be reached |
| `stopped` | parked by `session stop`; also `stopped: true` |

**The trap this table exists to prevent:** `idle` is not "no news". The last
five words above are *not* the agent saying it is at rest, and treating any of
them as `idle` — as anything that reads state here once did — reports a worker
mid-turn as finished. Read the word, never the absence of one.

`get` and `list` deliberately answer differently:

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

`detected_agent` names the registered agent observed holding the pane when it
is not the one the row was created as. Three names, three fields: `agent` is
what the row was created as, `reports_as` what a driver declared, and
`detected_agent` what is observably running. It is a live reading, never
written back. It is `null` when the observation cannot pick one profile —
several registered agents can share an executable — and that case answers
`hook_corroboration: "foreign-agent"` with `state: "running"`: an agent is
there, and which one is not knowable from a process listing. A remote session
has no pane to look at from here and answers `hook_corroboration:
"unavailable"`.

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
plausible. Completion still arrives by mail (or the sentinel below), because
only the worker knows whether it is done.

## 5. Collect and clean up

Record every session in the run log **as it happens** — name, repo(s), prompt
intent, outcome, PR/artifact. The run log is the source of truth.

```bash
thurbox-cli session restart <uuid>          # kill window, re-spawn with --resume
thurbox-cli session delete <uuid> --force   # headless cleanup
```

Plain `delete` only soft-deletes the DB row and leaves the TUI to reap the tmux
window and worktrees on its next sync. **When the TUI isn't running, pass
`--force`** — it kills the window, removes the worktrees (and the symlink
workspace), and cancels pending scheduled commands. `session restore <uuid>`
undoes a soft delete.

## Run loop

1. Clarify the goal. Pick or write a playbook in `orchestration/playbooks/`.
2. Open a run log from `orchestration/runs/_TEMPLATE.md`, named
   `<YYYY-MM-DD>-<slug>.md`.
3. Per unit of work: `session create` — with an `--on-existing` mode (§1c) and
   the run's profile flags (§1d) — → `session send`, unless `created` came back
   `false` → read the result file it writes → record.
4. Review PRs. `session delete --force` as each closes out.

For more than one unit of work, drive it through `./scripts/queue.sh` and the
`fleet-queue` skill instead of by hand: it owns the records, the ordering, and
both halves of step 3's completion.
