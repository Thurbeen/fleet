---
name: thurbox-session
description: Spawn and drive a thurbox worker session with thurbox-cli. Use whenever the control plane is asked to do new work in a real repo. Covers single-repo and multi-repo (--add-repo / --add-dir) sessions, prompting, completion detection, and cleanup.
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

Capture the returned UUID — every later command keys off it.

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

## 1b. Trust the worktree before the agent starts

A Claude Code session started in an untrusted directory stops on the
workspace-trust dialog. thurbox mints a **fresh worktree path per session**, so
each new worker meets it.

**Trust is not a `settings.json` key** — the settings schema has no such field.
It lives in `~/.claude.json`:

```
.projects["<absolute path>"].hasTrustDialogAccepted = true
```

keyed by *exact absolute path*. No globs, no prefix rules, and **no inheritance
from a parent directory**: a worktree under `~/.local/share/thurbox/worktrees/`
is untrusted even though the repo it belongs to is trusted.

`scripts/trust-thurbox-dir.sh` seeds a path safely — it backs `~/.claude.json`
up, validates that the result is still an object with `.projects`, and refuses
to write an empty file:

```bash
scripts/trust-thurbox-dir.sh /abs/path/to/worktree   # one path
scripts/trust-thurbox-dir.sh --all-worktrees         # every existing thurbox worktree
```

Trust is a real guard, not a nuisance: accepting it in advance vouches for the
code in that directory. Seed only worktrees of repos you already trust. Two
further caveats. `~/.claude.json` is rewritten by every live Claude Code
process, so a concurrent write can clobber the edit — seed before the session
starts, when few sessions are running. And `-p` / non-TTY invocations skip the
trust dialog entirely, so a headless probe proves nothing about the interactive
path.

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

## 4. Detect completion

**Prefer push over pane-scraping.** Agent CLIs are TUIs — box chrome, prefixes,
and line-wrapping make grepping a captured pane fragile, and it is only as
timely as your next poll.

thurbox injects `THURBOX_SESSION` (and `THURBOX_TASK`) into each session's
environment, so a worker sends its own mail with no ids:

```bash
# instruct the worker to finish with:
thurbox-cli message send --to '<lead-name-or-uuid>' --kind result --body '<PR url or NOT_APPLICABLE>'
```

Quote the address: a name is a sentence with spaces in it. The payload travels
through the durable DB, never the pane. Drain it exactly-once from the lead:

```bash
thurbox-cli message inbox --for '<lead>' --claim --json
```

**The control plane's own Claude IS a thurbox session** — it runs inside one, so
`$THURBOX_SESSION` holds its UUID. It can therefore be the lead directly; no
separate lead session is needed:

```bash
thurbox-cli message send  --to "$THURBOX_SESSION" --kind result --body 'probe'
thurbox-cli message inbox --for "$THURBOX_SESSION" --json          # peek, non-destructive
thurbox-cli message inbox --for "$THURBOX_SESSION" --claim --json  # drain exactly-once
```

`send` returns `{"enqueued":true,"woke":true,...}` and **wakes** the recipient,
so the lead does not poll. `inbox` without `--claim` peeks; `--claim` drains.

So the real loop is: spawn workers with `--parent "$THURBOX_SESSION"`, put this
line at the end of every worker brief —

```bash
thurbox-cli message send --to '<lead-uuid>' --kind result --body '<PR url or NOT_APPLICABLE>'
```

— then enumerate with `session list --parent "$THURBOX_SESSION" --json` and
drain the inbox. Prefer this over polling `gh pr list`: it is exact, immediate,
and reports `NOT_APPLICABLE` too, which a PR poll can never distinguish from
"still working".

**Fallback — sentinel + capture.** For a one-off worker where a lead session
isn't worth it, have the worker print a `===RESULT===` JSON sentinel and poll:

```bash
thurbox-cli session capture <uuid> --lines 400 --json   # default 200, max 10000
```

Back off between polls. Treat a missing sentinel as "still working", not as
failure.

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
3. Per unit of work: `session create` → `session send` → await the result mail →
   record.
4. Review PRs. `session delete --force` as each closes out.
