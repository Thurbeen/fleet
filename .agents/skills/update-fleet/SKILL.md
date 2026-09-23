---
name: update-fleet
description: Bring an already-working control plane up to date — fast-forward the checkout, then re-apply only the consequences the sync reports and cannot fix itself: the extension manifest, the TUI queue pane, the registry, the reconciler, and the stale lead session. Use when asked to update fleet, pull the control plane, apply what a sync brought in, or when invoked as /update-fleet.
user-invocable: true
allowed-tools: Read, Bash, Glob, Grep
---

## update-fleet

**`fleet-onboarding` is "this clone is not yet a working fleet"; this skill is
"this working fleet is behind origin"**: fast-forward, then re-apply only what
the sync touched. Scope is the control plane only — thurbox and the agent
tooling have their own update paths.

> **This skill changes no tracked state on its own initiative.** No commits,
> pushes, reverts, `git checkout -- .`, rebase or reset. It runs
> `uv run fleet sync-checkout`, which only ever fast-forwards, and everything
> after that re-installs or re-generates something the sync left stale. When
> the sync refuses, report the refusal and the operator's action.

**Nothing changed is the common case, and it is one line.** Steps 2–7 are
each conditional on a path that moved; a run with nothing behind origin ends
at "already current; nothing to re-apply."

## 1. Sync — the one command that decides the rest

```bash
git rev-parse HEAD            # before
uv run fleet sync-checkout    # one JSON object, or nothing at all
git rev-parse HEAD            # after
```

Keep both SHAs; §2 diffs between them. `uv run fleet sync-checkout --help`
owns the rules. It **always exits 0**, so the exit code tells you nothing;
the object's `systemMessage` does, and **no output at all means nothing to
say**.

| What the message says | What happened | What you do |
|---|---|---|
| *(empty)* | already current | stop — say it in one line |
| `fast-forwarded '<branch>' N commit(s) to <sha>` | the work case | §2 |
| `the tree is dirty. Not fast-forwarding.` | refusal | §1a |
| `on '<branch>'; origin/<default> is N commit(s) ahead` | refusal — a worktree or feature branch | say which branch and how far the base moved; a `git rebase origin/<default>` is theirs to run |
| `has diverged from origin/<default>` | refusal | report the two counts; a rebase or merge by hand is theirs, and naming the wrong one costs a force-push |
| `is N commit(s) ahead ... and not pushed` | nothing to pull | report it; nothing to re-apply |
| `could not reach origin (offline?)` | no fetch happened | report and stop; a re-apply on an unsynced tree proves nothing |
| `would not fast-forward. Left alone.` | behind, but the merge refused | usually an untracked file colliding with an incoming one; report what `git merge --ff-only origin/<default>` says and stop |

### 1a. A dirty tree is a normal state here, not a fault

The operator sometimes copies a worker's files into this checkout to try them
live. So show `git status --porcelain --untracked-files=no`, say the update is
paused until they commit it, move it aside, or call it disposable, and stop.
**Do not suggest reverting**, do not offer `git checkout -- .`, and do not
stash — the stash stack is shared with every worktree on this machine.

## 2. What moved

The sync's message classifies three path sets — `INSTRUCTION_PATHS`,
`WIRING_PATHS` and `RECONCILER_PATHS` at the top of
`scripts/lib/sync_checkout.py` — into `restart-lead:`, `reinstall-extension:`
and `restart-reconciler:` lines. **Read those; do not re-derive them.** For
the paths it has no opinion about, diff the range:

```bash
git diff --name-only <before> <after>
```

| A path in the range | Step |
|---|---|
| a `reinstall-extension:` line — `extension.toml.in`, `FLEET.md`, the voice or glyph example | §3 |
| `interface/fleet_queue.lua` | §4 |
| `registry/owners.txt` | §5 |
| any `orchestration/*.example.conf`, or `orchestration/queue/POLICY.md` | §5b |
| a `restart-reconciler:` line — `scripts/lib/reconcile.py`, `scripts/lib/fleet_platform.py` | §6 |
| a `restart-lead:` line — `FLEET.md`, `AGENTS.md`, `CLAUDE.md`, `.agents/skills`, `.claude/settings.json` | §8 |

`FLEET.md` is in two rows: it is rendered into the extension's payload *and*
it is the lead's standing context, so it needs §3 and §8.
`scripts/lib/queue.py` and `notify_lead.py` are in none: the loop runs them
as fresh child processes every pass, so a change reaches it with no restart.

### §5b — settings the sync can silently change

Every operator setting is a gitignored copy of a tracked example
(`orchestration/publish.conf`, `agent.conf`, `auto-merge.conf`,
`agent-policy.conf`, `voice.conf`, `session-glyphs.conf`, `fleet.conf`), read
on every pass with no reinstall and no restart. A sync that moves an example
can add a key the operator's copy lacks, or move a default out of a tracked
file — the auto-merge allowlist once left `scripts/lib/queue.py` for
`auto-merge.conf`, and an operator who synced across that and wrote no file
found `shepherd` merging nowhere, silently. So when an example moved: diff it
against the operator's copy, offer the new keys, and read
`uv run fleet queue shepherd --dry-run` — `Fleet merges NOTHING` means the
file does not exist. Copy an example only where the operator has no file yet
(`cp -n`); never overwrite one.

Run §3–§6 in any order, then §7, then §8 last — §8 cannot be automated, and
everything else should be done when you raise it.

## 3. Wiring — re-install the extension

```bash
uv run fleet install-extension
```

**This is the extension's real update command.** `thurbox-cli extension
update <id>` re-reads the *rendered* `extension.toml`, not `extension.toml.in`,
so it refreshes to whatever was last rendered.

**A non-zero exit here is a finding, not a failure to retry.** The installer
compares the LIVE session's directory with this clone and exits non-zero when
they differ — the moved-clone case, which `extension status` calls healthy.
Its message names the remedy, and that remedy **deletes the lead's
conversation history**, which is why it refuses to run it for you. Surface the
message verbatim and stop. On a machine running several fleets the message
has a second reading, and it is in the message: the session it names may be
another fleet's lead, and then the remedy is naming this fleet in
`orchestration/fleet.conf`, not a deactivate. Updating one fleet is per
checkout; run this skill in each clone.

The command also re-installs the pane as a non-fatal second pass, printing
warnings and still exiting 0 — read its output (§4).

## 4. Pane — the installed plugin is stale

`.agents/skills/fleet-pane/` owns the pane end to end; §3 already re-ran the
install, so what is left is its verification (`thurbox-cli plugin check`) and,
if that comes back unplaced, its placement section — which asks first.

## 5. Registry — only when the owners changed

```bash
uv run fleet sync-registry
```

**Only when `registry/owners.txt` moved in the range, or the operator asks.**
It is a full GitHub crawl over every owner, and running it every time turns a
no-op update into a minutes-long one. A local-only fleet has no owners file
or no `gh`: the command then says the map is optional and exits 0, so skip
this section. Nothing to commit afterwards; both files are gitignored.

## 6. Reconciler — restart it on new loop code, unless it was asked down

```bash
uv run fleet reconcile status   # ticking? since when? asked down?
uv run fleet reconcile ensure   # never `start`: that clears an operator's `stop`
```

`ensure` adopts a loop already running, so on its own it will not pick up new
code in `scripts/lib/reconcile.py`. When §2 put you here and `status` says it
is running, `uv run fleet reconcile restart` replaces it — but that clears
the down flag, so use it only on a loop that is actually up, never to bring a
stopped one back. If `status` reports the loop asked down, say so and change
nothing. A `legacy` line in `status` is a bash reconciler from before the uv
port, still running with its script deleted and failing every pass;
`ensure` stops it and says which pid went.

## 7. Gate

```bash
uv run fleet check
```

Last, and **report its result honestly**: it is the same command CI runs, so
a red run here is a real problem in what just arrived. Do not run `--fix`
(it edits tracked files, and this skill changes none); print what failed and
hand it over. The gate reads no operator record; `uv run fleet status
--records` is where the live queue and map are validated after a sync —
report a problem there as one in the records, not in what arrived.

## 8. Hand over the stale lead — the step that cannot be automated

If an instruction path moved, the running Mission Control session **froze
`FLEET.md`, `AGENTS.md` and every skill at launch, and nothing reloads them
from disk.** Say that plainly: a lead that reports the update as applied while
running the old instructions is the failure this section exists to prevent.

**You are probably that session.** `/update-fleet` runs in the lead, and the
refresh kills the lead's window. So **print the command and stop.**

### The sequence that keeps the conversation

```bash
thurbox-cli session restart '<the lead>'
```

**`<the lead>` is a name to paste from `thurbox-cli session list`, never one
to type**: it wears a glyph no keyboard has, and which glyph is a setting, so
no file spells the whole name. `session restart` re-spawns in place with
`--resume` — a new process that reads the instruction files off disk, the
conversation carried across, and **the name kept**, which matters because the
pane and `ensure_extension` both find the lead by name. Its help labels the
argument `<UUID>`, but the resolver takes a name. Not the fork sequence in
`extension.toml.in`'s RENAMING header: that moves a name, and here the name
must not move.

Two costs to state first: **a turn in flight dies with the window** (check
the lead is at rest with `thurbox-cli session get '<the lead>'`, reading the
word as `thurbox-session` §4a says), and **the old instructions are still in
the resumed history**.

### When the new instructions have to win

For a change that must not argue with an older copy in the same context,
`thurbox-cli session delete '<the lead>'`: the extension self-heals the
session under the same name at the same checkout, reading everything from
disk with **no conversation at all**. Say the cost in those words and let the
operator choose. **Never do either unasked.**

`thurbox-cli extension deactivate <this fleet's id>` plus re-install is a
different operation, for a manifest that changed which session it declares
(§3's moved-clone case), and the wrong id deletes another fleet's lead. It is
not how you refresh instructions.

## 9. Report

One short report, in the order the work happened: what the sync did, in its
own words; each of §3–§6 you ran and what it said, and each you **skipped and
why** ("registry untouched, not crawled" is information); the gate's verdict;
the hand-over, if §8 applies, with the command to copy. If nothing was behind
origin, that whole report is one line.
