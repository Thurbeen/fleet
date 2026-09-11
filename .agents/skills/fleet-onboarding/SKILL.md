---
name: fleet-onboarding
description: Take a fresh clone of this control plane to a working fleet — check and install the dependencies, discover the GitHub owners from the machine itself, sync the registry, install the thurbox extension, place the TUI queue pane on the operator's screen, and bring the reconciler up. Use when someone has just cloned the repo, asks how to set the control plane up, asks to install fleet's dependencies or the queue pane, asks to start or restart the fleet reconciler, or invokes /fleet-onboarding.
user-invocable: true
allowed-tools: Read, Edit, Write, Bash, Glob, Grep, AskUserQuestion
---

## fleet-onboarding

Takes a fresh clone of fleet to a control plane that actually runs: dependencies
installed, owners known, registry synced, thurbox extension installed, the queue
pane **on screen**, and the reconciler up.

**Do the work, don't narrate it — but keep the operator oriented while you do.**
Every step is a script in `scripts/`, and running them is yours. What the
operator needs from you is a sense of where they are, and a real say at the
four points where the answer is genuinely theirs.

### The shape of a run

Seven steps, in this order, each announced in one line before you do it:

```text
Step 1/7  Dependencies      preflight.sh, then install what is missing   [ask]
Step 2/7  This checkout     is this the clone to keep?
Step 3/7  Owners            discover-owners.sh, then confirm             [ask]
Step 4/7  Registry          sync-registry.sh
Step 5/7  Extension         install-extension.sh
Step 6/7  Queue pane        place it on screen — right by default        [ask]
Step 7/7  Reconciler        reconcile.sh ensure                          [ask]
```

**Four questions, and no more than four.** Everything else is discoverable or
has one correct answer. Ask each one at the step it belongs to and not before —
a wall of questions up front is asked before the operator has seen anything, and
answered blind.

**Say what each step landed, in one line, with the evidence.** "Registry:
41 repos across 3 owners" is the report; the command's own output is not.

**A fresh clone is mostly empty.** No `registry/owners.txt`, no generated map,
no context files, no run logs, no queue: everything a running fleet writes is
gitignored, because this repo is public and that content is the operator's. What
is tracked is the machinery plus the `_TEMPLATE.md` forms. Say that when it
comes up; a user who finds half the layout missing should hear that it is
correct.

Each script's own header is its full usage, and each remains the supported
manual path.

## Step 1/7 — Dependencies

```bash
./scripts/preflight.sh
```

One pass over everything fleet needs, in three tiers, each row carrying what
breaks without it and the command that installs it. **Read the table; do not
re-probe it tool by tool.** It exits non-zero when a REQUIRED dependency is
missing or a `thurbox-cli` is below the manifest's floor.

| Tier | What it means |
|---|---|
| required | fleet cannot run — `git`, `gh` (authenticated), `jq`, `python3` + PyYAML, `thurbox-cli` |
| recommended | a named capability degrades — `quota-axi` for fuel and `refuel`, `glab` for GitLab |
| gate | only `./scripts/check.sh` needs it — `lua`, `shellcheck`, `rumdl`, `prek`, plus the git commit-signing configuration, which is not a tool |

`gh` is required even on a fleet whose work is entirely on GitLab: it is what
builds the repo map from `registry/owners.txt`, which is a list of GITHUB
owners. `quota-axi` is the one most often missed, and it is not decorative —
without it the pane's fuel rows and `./scripts/fleet-status.sh` have nothing to
read, and `queue.sh refuel` cannot tell a spent account window from a live one
before it restarts a worker.

The last gate row is not a tool at all: **git commit signing turned on with no
key outside this checkout**. Nothing here needs it fixed to run a fleet, but
every commit in a repo an `includeIf gitdir:` key does not cover then fails —
a throwaway sandbox, or a worktree somewhere that block does not name.
`scripts/queue-selftest.sh` forces signing off for the repos it builds, so the
gate itself no longer reports it as a dozen unrelated queue failures. Report it
as what it is — a machine-config problem with a one-line fix and no bearing on
the rest of the setup.

**ASK — installing is the operator's call.** A package manager is the one part
of this setup that touches the machine outside the checkout, so nothing is
installed unasked. Show the missing rows and ask:

- **Install everything missing** (recommended) — required, recommended and gate
- **Required and recommended** — skip the tools only the gate needs
- **Required only** — the smallest thing that runs
- **Skip** — nothing is installed

Then run the lines, which are the script's own — one flag per answer, so which
lines to run is never your judgement call:

```bash
./scripts/preflight.sh --commands                                  # everything missing
./scripts/preflight.sh --commands --tier required --tier recommended
./scripts/preflight.sh --commands --tier required
```

Run them one at a time and show what each said; several need `sudo`, and an
operator watching a sudo prompt should know which command asked for it. An
install that fails is reported and does not stop the others — one missing gate
tool is not a reason to abandon a setup.

Then **re-run `./scripts/preflight.sh` and read it back.** That is the
verification, not the package manager's exit code.

If a REQUIRED tool is still missing after that, stop here and say which. A
half-onboarded clone — owners written, no registry — is worse than one that
never started. The single exception is `thurbox-cli`: steps 1 to 4 are still
worth doing without it, so say plainly that steps 5 and 6 are deferred and that
`./scripts/install-extension.sh` is the one command that picks them both up.

## Step 2/7 — This checkout

There is one remote and nothing to wire:

```bash
git remote -v      # origin -> their own copy of fleet
```

What matters here is **which directory this is**. Step 5 bakes this checkout's
absolute path into the thurbox extension, and a Mission Control session
registered against a scratch copy self-heals forever against a directory that is
about to vanish. So if the working directory is a thurbox worktree, a temp
directory or an obvious throwaway, say so now and stop — moving later costs a
session deletion (see step 5), and it is free to avoid here.

`./scripts/sync-checkout.sh` is how changes arrive afterwards. It runs from the
`SessionStart` hook and only ever fast-forwards, so there is nothing to
configure; it is worth knowing it exists because it is also what reports that a
pull left the running lead session holding stale instructions.

## Step 3/7 — Owners

`registry/owners.txt` is the one input that genuinely needs the operator, and
nearly all of it is already on the machine. Ask the machine first:

```bash
./scripts/discover-owners.sh
```

Three sources, each candidate printed with the evidence behind it:

- **gh account and orgs** — `gh api user`, `gh api user/orgs`
- **git config** — `github.user`, and a `@users.noreply.github.com` commit email
- **local clones** — the `origin` of every checkout under `~/code`, `~/src`,
  this clone's own parent and the rest, counted per owner. Origin and no other
  remote, so a fork's `upstream` never becomes an owner. It matches an ssh
  host ALIAS (`git@github-perso:owner/repo`) as well as `github.com`, so a
  machine with two GitHub accounts is not invisible to it.

A GitLab remote it finds is printed in its own section and is **not** a
candidate: the map is built with `gh`, and a GitLab repo is targeted per task
through the forge seam instead. Say that if the operator asks why their GitLab
group is not on the list.

**ASK — one question, not one per owner.** Show the candidates with their
evidence and ask which the map should cover:

- **All of them** — every candidate found
- **Just my account** — the narrowest useful map
- **A subset I name** — they pick from the list
- **Scan somewhere else first** — their clones live outside the default roots,
  so run `./scripts/discover-owners.sh ~/that/dir` and ask again with the
  fuller list

An account with no orgs and no other evidence has nothing to ask about — write
the username and move on. Discovery exiting 1 means the machine said nothing at
all: no `gh` session, no `github.user`, no GitHub remote under the roots it
scanned. Then, and only then, ask them to type their username, and offer the
directory scan as the alternative.

Then write the file. It is **gitignored** and will not exist in a fresh clone,
so start from the tracked example rather than from memory:

```bash
[ -f registry/owners.txt ] || cp registry/owners.example.txt registry/owners.txt
```

- **Keep the comment header.** It documents the file's own format for whoever
  edits it later by hand.
- Replace the two `# your-github-username` / `# your-org` placeholder lines with
  the confirmed owners, one per line, username first.
- On a **re-run** there are no placeholders left. Add only owners not already
  present, and leave the existing order alone — the sync emits owners in this
  file's order, so reshuffling it churns the generated map for nothing.

Verify before moving on; the sync refuses a file with no active entries, and it
is better to catch that here:

```bash
grep -vE '^[[:space:]]*(#|$)' registry/owners.txt
```

Nothing else needs seeding. Playbooks and session profiles are tracked files
the operator edits directly — `orchestration/playbooks/<name>.md` from
`_TEMPLATE.md`, and `orchestration/session-profiles.yaml` in place. The one
thing worth saying about the latter: it is committed to a **public** repo, so
nothing environment-specific goes in it, and a credential should reach a worker
by inheriting the thurbox server's environment rather than by living in a file.

## Step 4/7 — Registry

```bash
./scripts/sync-registry.sh
```

It enumerates every repo the operator's own `gh` session can reach, keeps the
ones under those owners, and writes `registry/repos.generated.yaml` —
**generated**, so never hand-edit it and never hand-write it if the script
fails.

Verify the map is not empty, and read the totals back as the evidence:

```bash
tail -3 registry/repos.generated.yaml   # totals: repos / owners
```

**The trap:** a mistyped owner does not fail the sync. The script prints
`warning: no accessible repos for owner '<x>'` on stderr and carries on, so a
typo yields a quietly thinner map. Surface that warning — it almost always means
a typo or an org the token cannot see, and it is fixable in seconds now.

## Step 5/7 — Thurbox extension

```bash
./scripts/install-extension.sh
```

It renders two gitignored files and installs them: `extension.toml` from
`extension.toml.in` (it carries this clone's absolute path), and
`FLEET.rendered.md` from `FLEET.md` (it carries the two names in
`orchestration/voice.example.conf` — what the lead calls the operator, and what
it answers to; copy that file to `voice.conf` beside it to change either). It
also hands the queue pane to `thurbox-cli plugin install`, which step 6 is
about.

Verify, rather than trusting the installer's own closing message:

```bash
thurbox-cli extension status fleet --json
```

That exits non-zero and answers `{"error": ...}` when no manifest is
registered, which is the honest signal that the install did not take.

**The trap that matters most here:** `[[sessions]] repo_path` is baked in at
install time. Run this from **the clone the operator intends to keep** — not a
thurbox worktree, not a scratch copy, not a temp directory.

Re-running the installer does not fix it. thurbox reuses an extension's session
by name and never moves it, so a second install rewrites the manifest, reports
success, and leaves the session on the old path — and `extension status` still
calls that healthy, because it checks that the session EXISTS, not where it
points. The installer catches this and exits non-zero; the remedy it names
deletes the session and its history, so hand that decision to the operator:

```bash
thurbox-cli extension deactivate fleet   # deletes the session
./scripts/install-extension.sh           # respawns it at the right path
```

## Step 6/7 — The queue pane, on screen

Step 5 installed the pane. This step is the half that **is not finished when
that script exits 0**, and skipping it is how an operator ends a setup with a
pane that loads, lists, declares its keys — and draws nothing.

A thurbox pane names a *slot*; the arrangement decides where that slot goes.
Ask the one thing that can tell an installed pane from a placed one:

```bash
thurbox-cli plugin check
```

| It says | What it means | What you do |
|---|---|---|
| `✓ loads — … fleetqueue …`, exits 0 | installed and placed | say that `F3` opens it |
| `✗ … nothing places slot "fleetqueue"` | installed, invisible | the ask below |
| no `fleetqueue` anywhere | the install did not take | re-run step 5 and read its output |

**ASK — always, and never place it silently.** `layout.lua` is the operator's
file: every pane on their screen shares it, and a mistake there takes the whole
interface rather than one column. So the edit happens on their word, and this is
the question:

- **Place it on the right** (recommended) — a column to the right of the
  terminal, `pct = 30, min = 34`, which is where the queue reads best: the
  session list on the left, the agent in the middle, the queue on the right
- **Place it on the left** — between the session list and the terminal
- **Show me the block, I will add it myself** — print it and stop
- **Skip** — the pane stays installed and invisible; `./scripts/place-pane.sh`
  places it whenever they want it

On yes, run the script that does it:

```bash
./scripts/place-pane.sh --dry-run    # the file, the anchor, the exact block
./scripts/place-pane.sh              # right of the terminal (--left for the other side)
```

It refuses rather than guesses. A layout it does not recognise — no `columns`
list it knows, or none of the helpers the block calls — is left untouched and
the block printed instead, naming the part it could not find; the file is
backed up to `layout.lua.bak-<timestamp>` before any edit; the result is
re-read with `lua` and the backup restored if it no longer parses; and it
finishes by running `thurbox-cli plugin check`, which is the verification. A
layout that already carves the slot is left exactly as it is — including one
the operator arranged differently, which is theirs and not yours to correct.

If they chose to add it themselves, print this and say plainly that you stopped
there on purpose:

```lua
if panels.shown("fleetqueue") and filled(ctx, "fleetqueue") then
  columns[#columns + 1] = { slot = "fleetqueue", pct = 30, min = 34 }
end
```

It goes inside the `columns` list of `layout.lua`, beside the other side
columns — after the `center` line for the right-hand column. Read the interface
directory back rather than assuming `~/.config/thurbox/ui`; a dev build's is
elsewhere, and this says which rule chose it:

```bash
thurbox-cli plugin dir --text | head -1
```

**Give them the guard, not just the slot.** `plugin check` suggests a bare
`{ slot = "fleetqueue" }`, and that is enough to make the pane DRAW — which is
all `check` knows about. It is not enough to make `F3` work: an unguarded slot
is carved on every frame, so the key flips a panel state nothing reads and the
pane opens and never closes. `panels` and `filled` already exist in the stock
`layout.lua`, guarding the session list the same way.

**One last thing that is theirs and not yours.** The pane finds the queue by
running `./scripts/queue.sh root` in the Mission Control session's checkout,
which needs the **`run` capability**. Declaring it does not grant it and you
cannot grant it for them — the switch is thurbox's own settings, `Ctrl+,` →
`]` → `t`. Say it once. Until they do, the pane draws an honest "not trusted
yet" rather than an empty column, so nothing is broken in the meantime.

**On a re-run**, `plugin install` reports the pane `current`, `plugin check`
says whether the block is already there, and `place-pane.sh` says "already
placed" and changes nothing. Check before you speak; a second run must never
propose a block that is already in the file. If `thurbox-cli` was missing at
step 1, defer this step exactly as step 5 is deferred: same script, same
sentence.

## Step 7/7 — The reconciler

**ASK — the loop runs on their machine, and it is theirs to start.** One
question, with what it does in the option itself:

- **Bring it up now** (recommended) — folds thurbox's event stream and runs
  `collect`, `shepherd` and `refuel` on their own intervals
- **Leave it down** — every one of those then happens only when the lead
  remembers, and `./scripts/reconcile.sh ensure` starts it later

```bash
./scripts/reconcile.sh ensure
./scripts/reconcile.sh status
```

Without it, one session ended with 19 of 20 progress timelines empty and three
merged pull requests unnoticed for forty minutes. That is what the recommended
answer is buying.

**`ensure`, never `start`, for exactly that reason.** It has the same `down`
flag with the same durability, in `orchestration/reconcile/down`, and the same
three correct answers on a re-run: started it, adopted it, or left it down
because the operator asked.

Three things to pass on, once:

- It **reconciles and does not decide**. No dispatch, no cancel, no reorder,
  and it writes no record itself — `scripts/queue.sh` stays the only writer,
  which is what keeps the queue single-writer.
- It **will occasionally type one line into Mission Control**, and only ever
  the same one: that N tasks are ready and nothing will dispatch them. That is
  the loop telling the actor who may act; an unprompted line there is this and
  not a bug. It arrives once per transition and never while the lead is
  mid-turn.
- To switch it off for good: `./scripts/reconcile.sh stop`. To bring it back:
  `./scripts/reconcile.sh start`.

Optionally, and only if they ask for it: `./scripts/reconcile.sh hook` prints a
Claude Code `Stop` hook that makes a finishing worker nudge the loop into its
next pass immediately. It goes in `~/.config/thurbox/hooks/claude.json`, which
is **thurbox's file and not fleet's** — so this prints the block and the
operator pastes it, and a thurbox update may take it away again. It is an
accelerator, never the mechanism: a worker that ran out of quota fires no hook
at all.

## Hand over

Close with a short recap: the seven steps, one line each, and what each landed —
dependencies installed, owners written, N repos across M owners, extension
healthy, pane placed on the right, reconciler up.

**Nothing this skill wrote to the repo is tracked.** `registry/owners.txt`,
`registry/repos.generated.yaml`, `extension.toml` and `FLEET.rendered.md` are
all gitignored, so `git status` is clean and there is nothing to commit or push.
That is the design, not a step you forgot: this repo is public, and an index of
every repo the operator can reach — along with one machine's absolute paths —
does not belong in it. `.gitignore`'s header has the reasoning. Say it
explicitly; an operator who set up a control plane and sees an empty
`git status` will otherwise assume it failed.

The one thing outside the repo that did change is the operator's own
`layout.lua`, if they said yes in step 6 — with a `.bak-<timestamp>` beside it.
Say that too.

Gate anyway; the `yaml` check is the one that asserts the generated map's shape:

```bash
./scripts/check.sh
```

Then tell them the one thing that is theirs to do next: open the Mission
Control session in thurbox and give it a goal. Everything else — playbooks, run
logs, worker sessions — follows from that, and `AGENTS.md` is where the session
picks the loop up.

Two things worth saying once, because neither is discoverable later:

- The map, the run logs and the context notes live in **this working copy
  only**. If they matter beyond this machine, that is theirs to back up.
- `registry/context/_TEMPLATE.md` is where the judgement about a project goes.
  The generated map says which repos exist; a context file says what one is
  *for*. That is the first thing worth writing.

## Re-running

Assume someone runs this twice. Every step above **converges**:

| Step | Second run |
|---|---|
| Dependencies | pure probes; nothing is installed without the same question |
| Checkout | a question, not a write |
| Owners | discovery re-reads the machine and marks what is already configured; adds only missing entries, never duplicates or reorders |
| Registry | the script rewrites the file wholesale from live GitHub |
| Extension | a reinstall keeps existing `agents.toml` entries, so a customized model survives |
| Queue pane | `plugin install` reports it `current`, and `place-pane.sh` says "already placed" and touches nothing |
| Reconciler | `ensure` adopts a running one, and a `down` flag it wrote stays honoured; never a twin |

So do not refuse on an already-configured clone. Detect it —
`registry/owners.txt` with active entries, the generated map there, the
extension healthy, `plugin check` green — say which parts are already in place,
and offer to refresh the map rather than redoing everything.

The one thing a re-run does **not** fix is a **rename**. thurbox names a session
when it SPAWNS it and has no verb that renames one, and `ensure_extension`
matches a declared session to a live one by NAME — so a manifest edit alone
spawns a SECOND session beside the old one and calls that healthy.

The lead is called Mission Control, wearing a glyph that is a setting rather
than a literal (`orchestration/session-glyphs.example.conf`, rendered into the
manifest at install time) — so read its exact name off `thurbox-cli session
list` rather than from any file. The EXTENSION and its agent are still `fleet`,
which is deliberate and is why `extension status fleet` stays the right question
no matter what the session is called. `extension.toml.in`'s RENAMING
header owns both sequences — the `session fork` one that carries the lead's
conversation across, and the `extension deactivate` one that discards it —
including which step must come before which. Don't reimplement it here.
