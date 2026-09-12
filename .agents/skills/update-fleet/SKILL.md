---
name: update-fleet
description: Bring an already-working control plane up to date — fast-forward the checkout, then re-apply only the consequences the sync reports and cannot fix itself: the extension manifest, the TUI queue pane, the registry, the reconciler, and the stale lead session. Use when asked to update fleet, pull the control plane, apply what a sync brought in, or when invoked as /update-fleet.
user-invocable: true
allowed-tools: Read, Bash, Glob, Grep
---

## update-fleet

**`fleet-onboarding` is "this clone is not yet a working fleet"; this skill is
"this working fleet is behind origin"** — it fast-forwards the checkout and then
re-applies only what that sync actually touched.

Scope is the **control plane only**. thurbox, Claude Code and the agent tooling
have their own update paths. Say so if asked to do more.

> **This skill changes no tracked state on its own initiative.** No commits, no
> pushes, no reverts, no `git checkout -- .`, no rebase, no reset. It runs
> `./scripts/sync-checkout.sh`, which only ever fast-forwards, and everything
> after that is re-installing or re-generating something the sync left stale.
> When the sync refuses, you report the refusal and the operator's action — you
> do not reconcile the tree for them.

**Nothing changed is the common case, and it is one line.** Step 1 says so, and
steps 2–7 are each conditional on a path that actually moved. A run with nothing
behind origin ends at "already current; nothing to re-apply."

## 1. Sync — the one command that decides the rest

```bash
before="$(git rev-parse HEAD)"
msg="$(./scripts/sync-checkout.sh | jq -r '.systemMessage // empty')"
after="$(git rev-parse HEAD)"
printf '%s\n' "${msg:-already current}"
```

`scripts/sync-checkout.sh`'s header owns the rules — read it rather than
guessing at them. It fast-forwards only when that is unambiguously safe, it
never rebases or resets, and it **always exits 0**, so the exit code tells you
nothing. The message does, and **no message at all means nothing to say**.

| What `msg` says | What happened | What you do |
|---|---|---|
| *(empty)* | already current | stop — say it in one line |
| `fast-forwarded '<branch>' N commit(s) to <sha>` | the work case | go to §2 |
| `the tree is dirty. Not fast-forwarding.` | refusal — dirty tree | §1a |
| `on '<branch>'; origin/<default> is N commit(s) ahead` | refusal — not on the default branch | §1b |
| `has diverged from origin/<default>` | refusal — diverged | §1c |
| `is N commit(s) ahead ... and not pushed` | nothing to pull; local work is unpushed | report it; there is nothing to re-apply |
| `could not reach origin (offline?)` | no fetch happened | report it and stop; a re-apply on an unsynced tree proves nothing |
| `would not fast-forward. Left alone.` | behind, but the merge refused | usually an untracked file colliding with an incoming one; report what `git merge --ff-only origin/<default>` says and stop |

### 1a. A dirty tree is a normal state here, not a fault

The operator sometimes copies a worker's files into this checkout to try them
live. So **report what is dirty and stop. Do not suggest reverting**, do not
offer `git checkout -- .`, and do not stash — the stash stack is shared with
every worktree on this machine.

```bash
git status --porcelain --untracked-files=no
```

Show that list and say the update is paused until the operator commits it,
moves it aside, or tells you it is disposable. Their call, not yours.

### 1b. Not on the default branch

You are in a worktree or on a feature branch. The control plane updates on its
default branch, so say which branch this is and how far the base has moved, and
leave it alone. If the operator wanted their feature branch caught up, that is
`git rebase origin/<default>` and it is theirs to run.

### 1c. Diverged

Local commits and remote commits both exist. The script never reconciles this
and neither do you: report the two counts and say it needs a rebase or a merge
by hand. Naming the wrong one of those costs a force-push.

## 2. What moved

The sync's own message classifies two path sets — `INSTRUCTION_PATHS` and
`WIRING_PATHS`, defined at the top of `scripts/sync-checkout.sh` — and reports
them as `restart-lead:` and `reinstall-extension:` lines. **Read those; do not
re-derive them.** For the paths it has no opinion about, diff the range it just
moved:

```bash
git diff --name-only "$before" "$after"
```

`$before` equal to `$after` means nothing moved and there is nothing below to
do. Otherwise map the list:

| A path in the range | Step | Why |
|---|---|---|
| `extension.toml.in`, `FLEET.md`, `orchestration/voice.example.conf` — or a `reinstall-extension:` line | §3 | the installed extension no longer matches what it was rendered from |
| `interface/fleet_queue.lua` | §4 | the installed plugin is a stale copy of that file |
| `registry/owners.txt` | §5 | the generated map covers the wrong owners |
| `orchestration/auto-merge.example.conf`, or `scripts/lib/queue.py`'s allowlist | §5b | `shepherd` may now merge in a different set of repos, or in none |
| `orchestration/publish.example.conf`, `agent.example.conf`, or POLICY.md's frontmatter | §5c | tasks may publish a different way, or `refuel` may gate on a different account |
| `scripts/reconcile.sh` | §6 | the running reconciler loop is executing old code |
| `FLEET.md`, `AGENTS.md`, `CLAUDE.md`, `.agents/skills`, `.claude/skills`, `.claude/settings.json` — or a `restart-lead:` line | §8 | the lead is holding instructions it froze at launch |

`FLEET.md` is in two rows: the extension's `[[files]]` payload is
`FLEET.rendered.md`, rendered FROM it, *and* it is the lead's standing context
— so it needs the reinstall in §3 and the hand-over in §8. So does a change to
`orchestration/voice.example.conf` (or your own `voice.conf`): it moves what the
rendered payload calls you.

### §5b — where fleet may merge, which a sync can silently empty

The auto-merge allowlist moved out of `scripts/lib/queue.py` into the
operator's gitignored `orchestration/auto-merge.conf`. The tracked copy beside
it names nothing, so an operator who syncs across that change and writes no
file finds `shepherd` merging nowhere. Intended, and silent unless somebody
looks:

```bash
./scripts/queue.sh shepherd --dry-run | tail -6
cp -n orchestration/auto-merge.example.conf orchestration/auto-merge.conf
$EDITOR orchestration/auto-merge.conf
```

`Fleet merges NOTHING` in that output means the file does not exist. Entries
are host-qualified; the example's header owns the format and the gates.
Read every pass, so no reinstall and no restart, and both files are gitignored.

### §5c — the publish default and the agent, which moved out of tracked files

Two settings left tracked files for the same reason the allowlist did: a tool
name or a vendor name in a file this public repo ships is one operator's setup
handed to every clone.

- **The publish default left `orchestration/queue/POLICY.md`'s frontmatter** for
  `orchestration/publish.conf`. A block still in POLICY.md is honoured and
  warns once on stderr, so nothing breaks while you move it — but that file is
  tracked, so leaving it there ships your pipeline to everyone.
- **The third method is `attested`, not `no-mistakes`.** The old word still
  reads as that shape, so existing records load; what an attestation looks like
  is now `ATTESTATION_MARKER` in the same file.
- **`refuel` no longer assumes `claude`.** It derives the provider from the
  agent in hand, or takes `FUEL_PROVIDER` from `orchestration/agent.conf`, and
  reports `undetermined` — restarting nothing — rather than gating on a window
  it guessed.

```bash
cp -n orchestration/publish.example.conf orchestration/publish.conf
cp -n orchestration/agent.example.conf orchestration/agent.conf
./scripts/queue.sh add --help | grep -A2 publish     # the three shapes
```

Read on every pass, so no reinstall and no restart.

`scripts/lib/queue.py` and `scripts/lib/notify_lead.py` are absent from this
table: the reconciler's loop sources neither — every pass shells out to
`./scripts/queue.sh` and `python3 scripts/lib/notify_lead.py` as fresh
subprocesses, so a change reaches the loop on its next call with no restart. §6
covers `scripts/reconcile.sh` itself, which the running loop does hold in
memory.

Run §3–§6 in any order, then §7, then §8 last — §8 is the one that cannot be
automated, and everything else should already be done when you raise it.

## 3. Wiring — re-install the extension

```bash
./scripts/install-extension.sh
```

**This is the extension's real update command.** `thurbox-cli extension update
fleet` re-reads the *rendered* `extension.toml`, not `extension.toml.in`, so it
refreshes to whatever was last rendered and fails outright if `extension.toml`
was cleaned away. The script's own header owns the rest.

**A non-zero exit here is a finding, not a failure to retry.** The installer
verifies the LIVE session's directory against this clone, and exits non-zero
when they differ — the moved-clone case, which `extension status` calls healthy
because it checks that the session exists, not where it points. Its message
names the remedy, and that remedy **deletes the lead's conversation history**,
which is why the script refuses to run it for you. Surface the message verbatim
and stop; do not paper over the exit code, and do not run the remedy unasked.

The script also re-installs the TUI pane as a second, deliberately non-fatal
pass. It prints warnings there and still exits 0, so **read its output rather
than inferring the pane from the exit code** — §4.

## 4. Pane — the installed plugin is stale

`.agents/skills/fleet-pane/` owns the pane end to end: the install, the one
command that verifies it, the `layout.lua` block that places it and the script
that writes that block once the operator says so, the F-key, removal, and the
symptom table for a pane that is installed and drawing nothing. **Use that
skill; do not restate its procedure here.** §3 already re-ran the install, so
what is left is its verification step and, if that comes back unplaced, its
placement section.

## 5. Registry — only when the owners changed

```bash
./scripts/sync-registry.sh
```

**Only when `registry/owners.txt` moved in the range, or the operator asks for
it.** It is a full GitHub crawl over every owner; running it on every invocation
turns a no-op update into a minutes-long one. Both `registry/owners.txt` and
`registry/repos.generated.yaml` are gitignored, so there is nothing to commit
and nothing to push afterwards.

## 6. Reconciler — restart it on new loop code, unless it was asked down

```bash
./scripts/reconcile.sh ensure
```

**`ensure`, never `start`.** `scripts/reconcile.sh`'s own header owns the
`ensure`/`start`/`stop` split, and it is the whole point: `stop` writes a
durable down flag, `ensure` honours it, and `start` clears it. An update must
not undo an operator's `stop`.

`ensure` adopts a loop that is already running, so on its own it will not pick
up new code in `scripts/reconcile.sh` — the loop's body was read into the
running shell at start. When §2 put you here, ask for the restart explicitly:

```bash
./scripts/reconcile.sh status   # ticking? since when? asked down?
```

If it reports the loop asked down, say so and change nothing. If it is
running, `./scripts/reconcile.sh restart` replaces it — but that command
clears the flag, so use it only on a loop that is actually up, and never as a
way to bring a stopped one back. Report what `status` says it is watching.

## 7. Gate

```bash
./scripts/check.sh
```

Last, and **report its result honestly**. It is the whole gate — the same script
CI and the prek hooks run — so a red run here is a real problem in what just
arrived, not noise to route around. Do not run `--fix`: that edits tracked files,
and this skill changes no tracked state. Print what failed and hand it over.

A green gate is worth one line. A red one is worth the failing check's output.

## 8. Hand over the stale lead — the step that cannot be automated

If an `INSTRUCTION_PATH` moved, the running Mission Control session **froze
`FLEET.md`, `AGENTS.md` and every skill it had loaded at launch, and nothing
reloads them from disk.** New bytes in the checkout change nothing for it. Say
that plainly — a lead that reports the update as applied while still running the
old instructions is the failure this section exists to prevent.

**You are probably that session.** `/update-fleet` runs in the lead, and the
refresh kills the lead's window. So **print the command and stop**; you cannot
run it on yourself and finish this turn.

### The sequence that keeps the conversation

```bash
thurbox-cli session restart '<the lead>'
```

**`<the lead>` is a name to copy, never one to type.** The session wears a glyph
because thurbox has no icon field, and which glyph is a setting
(`orchestration/session-glyphs.example.conf`), so no file here spells the whole
name — `thurbox-cli session list` shows the one that is actually running, and
that is the string to paste.

Verified against the CLI, and it is what `scripts/sync-checkout.sh` names in its
own `restart-lead:` message:

- `session restart` is documented as *"Restart a session in-place (kills the
  window, re-spawns with `--resume`)"* — a new process, so it reads
  `FLEET.md`, `AGENTS.md`, the skills and `.claude/settings.json` off disk
  again, while `--resume` carries the conversation across.
- **In-place keeps the name.** That is what makes it the right operation here:
  `interface/fleet_queue.lua` probes the lead BY NAME and `ensure_extension`
  matches a declared session to a live one by NAME, so a lead that came back
  under any other name is a lead the pane says "no session" about forever.
- Its help labels the argument `<UUID>`, but the resolver takes a name — a bad
  argument answers *"tried it as a UUID, a name, and an id prefix"*. **Copy the
  name, do not retype it:** neither glyph is on a keyboard, and the space needs
  the quotes. `extension.toml.in`'s RENAMING header owns why the glyph is part
  of the name.

**Not the fork sequence.** `extension.toml.in`'s RENAMING header documents a
conversation-preserving fork, which moves a name to a *new* one; its second step
would ask thurbox to spawn under a name the live lead still holds. Here the name
must not move.

Two costs to state before the operator runs it:

- **A turn in flight dies with the window.** Check the lead is at rest first
  (`thurbox-cli session get '<the lead>'`), reading the state word the way
  `.agents/skills/thurbox-session/` §4a says to — `idle` is not the only word
  that is not `working`.
- **The old instructions are still in the resumed history**, which is the cost
  `restart` trades for the conversation, and what the next section is for.

### When the new instructions have to win

For an instruction change that must not be arguing with an older copy in the
same context, start the lead fresh instead:

```bash
thurbox-cli session delete '<the lead>'
```

The extension self-heals the session, so it comes back under the same name at
the same checkout, reading everything from disk with **no conversation at all**.
`scripts/sync-checkout.sh`'s message names this as the alternative; say the cost
in those words — the lead's history is gone — and let the operator choose.

**Never do either unasked.** Offer the sequence, name what it costs, and stop.

The heavier `thurbox-cli extension deactivate fleet` + re-install is a different
operation, for a manifest that changed which session it declares; §3's
moved-clone message is the case that calls for it. It is not how you refresh
instructions.

## 9. Report

One short report, in the order the work happened:

- what the sync did, in its own words;
- each of §3–§6 you ran and what it said, and each one you **skipped and why** —
  "registry untouched, not crawled" is information, not silence;
- the gate's verdict;
- the hand-over, if §8 applies, with the command to copy.

If nothing was behind origin, that whole report is one line.
