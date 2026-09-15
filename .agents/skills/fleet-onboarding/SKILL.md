---
name: fleet-onboarding
description: Take a fresh clone of this control plane to a working fleet — check and install the dependencies, discover the GitHub owners and sync the registry when the fleet works on a forge (a local-only fleet skips both), install the thurbox extension, place the TUI queue pane on the operator's screen, and bring the reconciler up. Use when someone has just cloned the repo, asks how to set the control plane up, asks to install fleet's dependencies or the queue pane, asks to start or restart the fleet reconciler, or invokes /fleet-onboarding.
user-invocable: true
allowed-tools: Read, Edit, Write, Bash, Glob, Grep, AskUserQuestion
---

## fleet-onboarding

Takes a fresh clone of fleet to a control plane that actually runs: dependencies
installed, owners known and registry synced if the fleet works on a forge,
thurbox extension installed, the queue pane **on screen**, and the reconciler up.

**Do the work, don't narrate it — but keep the operator oriented while you do.**
Every step is a `uv run fleet` command, and running them is yours. What the
operator needs from you is a sense of where they are, and a real say at the
five points where the answer is genuinely theirs.

### The shape of a run

Seven steps, in this order, each announced in one line before you do it:

```text
Step 1/7  Dependencies      fleet install: the plan, one answer          [ask]
Step 2/7  This checkout     is this the clone to keep?
Step 3/7  Owners            fleet discover-owners, then confirm   [ask, forge only]
Step 4/7  Registry          fleet sync-registry                         [forge only]
Step 5/7  Extension         fleet voice-ask, then fleet install-extension [ask]
Step 6/7  Queue pane        place it on screen — right by default         [ask]
Step 7/7  Reconciler        fleet reconcile ensure                        [ask]
```

### A local-only fleet

**No forge CLI and no forge login is a supported setup, not a degraded one.**
Step 1's ask decides it: an operator who installs no forge there skips steps 3
and 4 and says so in one line each — there are no owners and no map, and
nothing downstream needs either. What changes:

- `uv run fleet preflight` exits 0 with every forge row missing; its FORGE tier
  says what each would add.
- Tasks target local checkouts: `fleet queue add --repo <path>`. Publish with
  `push` — the worker pushes to `origin`, which may be a bare repository or any
  remote no forge owns, and reports the commit's full sha as its artifact, which
  git alone proves — or with `none`, which closes on the worker's word.
  `pr`, `attested` and `note` need a forge: without one `collect` closes them as
  `unchecked` and names `uv run fleet preflight --tier forge`.
- `fleet queue shepherd` says "no forge configured" once and exits 0, and the
  reconciler skips it and logs that once per start.
- `sync-registry`, `discover-owners` and a bare `add-owner` say the map is
  optional and exit 0.

A forge can be added at any time: `uv run fleet install --forge github` (or
`gitlab`), then steps 3 and 4.

**Five asks, and no more than five.** Everything else is discoverable or
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

Each command's module docstring under `scripts/lib/` is its full usage, and
each remains the supported manual path.

## Step 1/7 — Dependencies

```bash
uv run fleet preflight
```

One pass over everything fleet needs, in four tiers, each row carrying what
breaks without it — or, for a forge, what it adds — and the command that
installs it. **Read the table; do not re-probe it tool by tool.** It exits
non-zero when a REQUIRED dependency is missing or a `thurbox-cli` is below the
manifest's floor, and never for anything else.

| Tier | What it means |
|---|---|
| required | fleet cannot run — `git`, `uv`, `thurbox-cli`, and the multiplexer thurbox runs sessions in: `tmux` 3.2 or newer, or `psmux` on native Windows |
| recommended | a named capability degrades — `quota-axi` for fuel and `refuel` |
| forge | optional — `gh` and `gh auth` add GitHub, `glab` and `glab auth` add GitLab: the repo map, publish checks on change requests, shepherd merges. A local-only fleet needs none |
| gate | only `uv run fleet check` needs it — `lua`, `prek`, plus the git commit-signing configuration, which is not a tool |

There is no Python row. `uv` is the one runtime dependency that carries the
rest: it brings the Python and the PyYAML `uv.lock` pins, and the gate's own
`ruff`, `rumdl` and `pytest`. The table itself is data —
`scripts/lib/preflight.py`'s `dependencies()` — so what a row says and what an
installer acts on can never drift apart.

The two authentication rows are the ones worth reading rather than skimming,
because neither CLI's own status command answers the question fleet has.
`gh auth` is decided **per account**, so one expired token among three reads
`3 of 4 accounts` and the broken login is named on stderr — not `missing`.
`glab auth` is decided **per host** and names the instance that answered:
`GITLAB_HOST` decides when it is set, and otherwise one working credential is
enough. An operator authenticated to their company's GitLab and not to
gitlab.com has a working setup, and this row says so.

No forge is required. `gh` is what builds the repo map from
`registry/owners.txt`, which is a list of GITHUB owners, so a GitLab-only fleet
that wants a map still needs `gh` — and a fleet that wants no map needs neither.
`quota-axi` is the one most often missed, and it is not decorative —
without it the pane's fuel rows and `uv run fleet status` have nothing to
read, and `fleet queue refuel` cannot tell a spent account window from a live
one before it restarts a worker.

The last gate row is not a tool at all: **git commit signing turned on with no
key outside this checkout**. Nothing here needs it fixed to run a fleet, but
every commit in a repo an `includeIf gitdir:` key does not cover then fails —
a throwaway sandbox, or a worktree somewhere that block does not name.
Every test runs under `tests/harness.py`'s `isolated_env`, whose git
configuration turns signing off, so the gate itself never reports it as a dozen
unrelated queue failures. Report it as what it is — a machine-config problem
with a one-line fix and no bearing on the rest of the setup.

**ASK — installing is the operator's call.** A package manager is the one part
of this setup that touches the machine outside the checkout, so nothing is
installed unasked. `uv run fleet install` prints the whole plan — every missing
dependency with THIS machine's command (`winget` on native Windows; `apt-get`,
`dnf`, `pacman` or `brew` elsewhere, or a tool's own installer where that is the
recommended route), the `.claude/skills` link and the reconciler's nudge hook —
and asks once. It plans no forge unless one is named. Show that plan and ask
both halves in one go:

- **Install the plan** (recommended) — required and recommended
- **The plan and the gate's tools** — for an operator who will run
  `uv run fleet check`
- **Skip** — nothing is installed

and **which forge, if any** — **GitHub**, **GitLab**, both, or **none: a
local-only fleet**. None is a complete answer: steps 3 and 4 are then skipped
(see *A local-only fleet* above).

Then run it with their answer, so what gets installed is never your judgement
call:

```bash
uv run fleet install --yes                  # the plan, no forge
uv run fleet install --yes --forge github   # and gh, and its login to run
uv run fleet install --yes --dev            # the plan and the gate tier
```

`--forge` repeats (`--forge github --forge gitlab`). A forge's login is a
`you run` row: it is interactive and the operator's.

On Linux several routes need `sudo`, and an operator watching a sudo prompt
should know which command asked for it; the output names each as it runs. An
install that fails is reported and does not stop the others. When every
required row is present it also installs the extension with the default names,
which step 5 re-renders with the operator's. It ends with `uv run fleet
preflight`, and **that table is the verification**, not a package manager's
exit code. Re-running is safe: a complete machine is asked nothing.

If a REQUIRED tool is still missing after that, stop here and say which. A
half-onboarded clone — owners written, no registry — is worse than one that
never started. The single exception is `thurbox-cli`: steps 1 to 4 are still
worth doing without it, so say plainly that steps 5 and 6 are deferred and that
`uv run fleet install-extension` is the one command that picks them both up.

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

`uv run fleet sync-checkout` is how changes arrive afterwards. It runs from the
`SessionStart` hook and only ever fast-forwards, so there is nothing to
configure; it is worth knowing it exists because it is also what reports that a
pull left the running lead session holding stale instructions.

## Step 3/7 — Owners

**Only for a fleet that works on a forge.** A local-only operator has no owners
file and needs none: say "Owners: skipped — local-only" and go to step 5.

`registry/owners.txt` is the one input the map needs from the operator, and
nearly all of it is already on the machine. Ask the machine first:

```bash
uv run fleet discover-owners
```

Three sources, each candidate printed with the evidence behind it:

- **gh account and orgs** — `gh api user`, `gh api user/orgs`, asked once per
  `gh` login rather than only the active one. An operator with a personal
  account and an employer's reaches two disjoint sets of orgs, and an owner
  never offered is one that never reaches the map. Each login's token is read
  by name, so nothing switches which account `gh` is pointing at; a login
  whose credential has expired is named on stderr and skipped
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
  so run `uv run fleet discover-owners ~/that/dir` and ask again with the
  fuller list

An account with no orgs and no other evidence has nothing to ask about — write
the username and move on. `No candidate owners found` (still exit 0: the map is
optional) means the machine said nothing at all: no `gh` session, no
`github.user`, no GitHub remote under the roots it scanned. Then, and only
then, ask them to type their username, and offer the directory scan as the
alternative.

Then write the file. It is **gitignored** and will not exist in a fresh clone,
so start from the tracked example rather than from memory:

```bash
[ -f registry/owners.txt ] || cp registry/owners.example.txt registry/owners.txt
```

- **Keep the comment header.** It documents the file's own format for whoever
  edits it later by hand.
- Replace the two `# your-github-username` / `# your-org` placeholder lines with
  the confirmed owners, one per line, username first.
- On a **re-run** there are no placeholders left, and appending by hand is no
  longer the way to do it: `uv run fleet add-owner <owner>...` appends, leaves
  the existing order alone — the sync emits owners in this file's order, so
  reshuffling it churns the generated map for nothing — and refuses a
  duplicate. **What the operator gains afterwards**, below, is the fuller path.

Verify before moving on; the sync writes nothing for a file with no active
entries, and it is better to catch that here: read `registry/owners.txt` back,
and see at least one line that is neither blank nor a `#` comment.

Nothing else needs seeding. Playbooks are tracked files the operator edits
directly, `orchestration/playbooks/<name>.md` from `_TEMPLATE.md`. Session
profiles of their own go in the gitignored
`orchestration/session-profiles.local.yaml`, never in the tracked
`session-profiles.yaml`, whose edit would stop `fleet sync-checkout`. Gitignored
is not secret: a credential should reach a worker by inheriting the thurbox
server's environment rather than by living in either file.

## Step 4/7 — Registry

**Only when step 3 wrote owners.** With no owners file, no owner in it, or no
`gh`, the command says the map is optional, writes nothing and exits 0 — so a
local-only run says "Registry: skipped — no map" and moves on.

```bash
uv run fleet sync-registry
```

It enumerates every repo the operator's `gh` sessions can reach — **every
login, not just the active one**, each asked with its own token and none of them
switched — keeps the ones under those owners, and writes
`registry/repos.generated.yaml` — **generated**, so never hand-edit it and never
hand-write it if the command fails. A repo two logins both reach is one repo,
and a login whose credential no longer works costs its own repos and not the
map: it is named on stderr and skipped.

Verify the map is not empty, and read the totals back as the evidence: the last
three lines of `registry/repos.generated.yaml` count the repos and the owners.

**The trap:** a mistyped owner does not fail the sync. The command prints
`warning: no accessible repos for owner '<x>'` on stderr and carries on, so a
typo yields a quietly thinner map. Surface that warning — it means a typo or an
org no login can see, and it is fixable in seconds now.

It used to mean a third thing, which is why the multi-login read above matters:
with only the active account asked, every owner the *other* logins reach
produced this same warning, indistinguishable from a typo. It no longer does.

## Step 5/7 — Thurbox extension

It renders two gitignored files and installs them: `extension.toml` from
`extension.toml.in` (it carries this clone's absolute path), and
`FLEET.rendered.md` from `FLEET.md` (it carries two names — what the lead calls
the operator, and what it answers to). It also hands the queue pane to
`thurbox-cli plugin install`, which step 6 is about.

**ASK — the two names, before the install and never after it.** The render
bakes them into the lead's standing context, so a name chosen after this step
costs a re-install and a lead restart. Ask the command first:

```bash
uv run fleet voice-ask
```

`skip` means `orchestration/voice.conf` already holds an answer: say the two
names it printed and install. On `ask`, ask both in one go, each offering the
default it printed (`Slayer` and `VEGA` as shipped) as the recommended answer:

- **What should the lead call you?** — the default, or a name they type
- **What should the lead answer to?** — the default, or a name they type

The second is the name in the lead's prose only. The SESSION stays Mission
Control, which `extension.toml.in` sets and nothing here asks about.

Record the answer — **defaults included**, since an unrecorded answer is asked
again on the next run — then install:

```bash
uv run fleet voice-ask set '<operator>' '<lead>'
uv run fleet install-extension
```

`set` renders both names before it writes anything and refuses what the
renderer refuses — a quote, `|`, `\`, `&`, `@`, or a second line. It then
writes nothing, so ask again for the one it named. It never overwrites an
existing `voice.conf`; `--replace` does, and only on the operator's word.

If this run is inside Mission Control itself, `fleet install` has already rendered
the lead with the defaults. A different answer reaches that lead only after this
install **and** a restart of the session you are running in. Say so, and leave
the restart to the operator — `.agents/skills/update-fleet/` owns it.

Verify, rather than trusting the installer's own closing message:

```bash
thurbox-cli extension status fleet --json
```

That exits non-zero and answers `{"error": ...}` when no manifest is
registered, which is the honest signal that the install did not take.

**`fleet` is the id of an UNNAMED fleet, and a named one is `fleet-<name>`** —
a machine may run several, and asking about the wrong id answers about somebody
else's extension or about none. The installer's own closing lines print this
fleet's id in every command it hands you; `thurbox-cli extension status --json`,
with no name, lists every installed extension. Take the id from one of those
rather than typing `fleet`, in this step and in every command below.

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
thurbox-cli extension deactivate <this fleet's id>   # deletes the session
uv run fleet install-extension                      # respawns it at the right path
```

The id matters more here than anywhere else in this file: `deactivate` tears
down the sessions the named extension declares, so `deactivate fleet` run for a
fleet called `fleet-acme` deletes ANOTHER fleet's lead and its conversation,
and leaves this one exactly as it was.

**That same refusal has a SECOND cause, and the remedies are opposites.** One
machine may run several fleets — one clone each, one queue each, one Mission
Control each — and an unnamed second fleet renders the extension id and the
lead name the first one already answers to. So if the live session it names is
another fleet's lead rather than this one having moved, do not deactivate
anything: name this fleet instead, which costs nothing because nothing is
running under the name it takes.

Write `orchestration/fleet.conf` in this checkout with one line — letters,
digits, `_` and `-`, and no double underscore:

```text
NAME=acme
```

```bash
uv run fleet install-extension
```

**Write that file, never redirect a shell into it.** An agent runs on whatever
shell the machine has, and `> orchestration/fleet.conf` is three commands: cmd
reads `<name>` as a redirect, Windows PowerShell's `>` writes UTF-16LE, which
fleet reads back as anything but a setting, and only a POSIX shell does what it
looks like.

It then installs as `fleet-acme` with a lead called `<glyph> Mission Control ·
acme`, and the first fleet is untouched. Ask the operator which of the two
situations it is — the clone moved, or this is a second fleet — and never guess:
one answer deletes a conversation. `orchestration/fleet.example.conf` holds the
grammar, and naming a fleet that is ALREADY running is a rename with everything
`extension.toml.in`'s RENAMING header says one costs.

Everything after this step is per-checkout already — the queue, the registry,
the run logs, the reconciler, the pane's binding — so a second fleet runs the
same seven steps in its own clone and shares nothing with the first.

## Step 6/7 — The queue pane, on screen

Step 5 installed the pane. This step is the half that **is not finished when
that command exits 0**, and skipping it is how an operator ends a setup with a
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
- **Skip** — the pane stays installed and invisible; `uv run fleet place-pane`
  places it whenever they want it

**Ask it through `uv run fleet pane-ask`, the one record of the answer.**
Mission Control asks this same question on its first session, so an answer
given here and not recorded is a question asked twice. Run it bare first:
`skip` means it was already answered or the layout already places the pane, and
there is nothing to ask. On `ask`, ask, then record the answer:

```bash
uv run fleet place-pane --dry-run   # the file, the anchor, the exact block
uv run fleet pane-ask yes           # right of the terminal (yes --left: other side)
uv run fleet pane-ask no            # skip, or "I will add it myself" — remembered
```

If they chose to add it themselves, print this block — with its guard, since a
bare `{ slot = "fleetqueue" }` draws but leaves `F3` opening a pane that never
closes — say plainly that you stopped there on purpose, and still run
`uv run fleet pane-ask no` to record that choice, exactly as you would for
skip: without it, Mission Control's own first-run ask has no record and asks
again.

```lua
if panels.shown("fleetqueue") and filled(ctx, "fleetqueue") then
  columns[#columns + 1] = { slot = "fleetqueue", pct = 30, min = 34 }
end
```

**`.agents/skills/fleet-pane/` §4 owns the rest and this step does not restate
it**: where the block goes, what `fleet place-pane` refuses and backs up, and
the first line of `thurbox-cli plugin dir --text` for the interface directory —
never a literal path, since a dev build's differs and native Windows keeps it
under `%APPDATA%`. Its §7 is the symptom table if the pane comes back placed and
empty.

**One last thing that is theirs and not yours.** The pane finds the queue by
running `scripts/lib/pane_probe.py` through `uv` in the Mission Control
session's checkout,
which needs the **`run` capability**. Declaring it does not grant it and you
cannot grant it for them — the switch is thurbox's own settings, `Ctrl+,` →
`]` → `t`. Say it once. Until they do, the pane draws an honest "not trusted
yet" rather than an empty column, so nothing is broken in the meantime.

**On a re-run**, `plugin install` reports the pane `current`, `plugin check`
says whether the block is already there, and `fleet place-pane` says "already
placed" and changes nothing. Check before you speak; a second run must never
propose a block that is already in the file. If `thurbox-cli` was missing at
step 1, defer this step exactly as step 5 is deferred: same command, same
sentence.

## Step 7/7 — The reconciler

**ASK — the loop runs on their machine, and it is theirs to start.** One
question, with what it does in the option itself:

- **Bring it up now** (recommended) — folds thurbox's event stream and runs
  `collect`, `shepherd` and `refuel` on their own intervals
- **Leave it down** — every one of those then happens only when the lead
  remembers, and `uv run fleet reconcile ensure` starts it later

```bash
uv run fleet reconcile ensure
uv run fleet reconcile status
```

Without it, one session ended with 19 of 20 progress timelines empty and three
merged pull requests unnoticed for forty minutes. That is what the recommended
answer is buying. On a local-only fleet it is just as worth running: it skips
`shepherd` for the whole start and its log says so once.

**`ensure`, never `start`, for exactly that reason.** It has the same `down`
flag with the same durability, in `orchestration/reconcile/down`, and the same
three correct answers on a re-run: started it, adopted it, or left it down
because the operator asked. A running loop holds an exclusive lock in that same
directory for its whole life, and the OS drops the lock however the loop died,
so a crashed loop is never mistaken for a live one and never a reason for a
twin.

Three things to pass on, once:

- It **reconciles and does not decide**. No dispatch, no cancel, no reorder,
  and it writes no record itself — `fleet queue` stays the only writer,
  which is what keeps the queue single-writer.
- It **will occasionally type one line into Mission Control**, and only ever
  the same one: that N tasks are ready and nothing will dispatch them. That is
  the loop telling the actor who may act; an unprompted line there is this and
  not a bug. It arrives once per transition and never while the lead is
  mid-turn.
- To switch it off for good: `uv run fleet reconcile stop`. To bring it back:
  `uv run fleet reconcile start`.

The worker `Stop` hook that makes a finishing worker nudge the loop into its
next pass is already in place: step 1's `uv run fleet install` merged it into
Claude Code's user settings (`uv run fleet paths claude-settings`) — one
command, `uv run --project <this checkout> fleet reconcile nudge`, that exits 0
whatever happens and so never blocks a worker. It is not in thurbox's hooks
file, which thurbox rewrites on every start. It fires on Stop in every Claude
Code session on the machine, which costs a flag file. It is an accelerator,
never the mechanism: a worker that ran out of quota fires no hook at all.

## Hand over

Close with a short recap: the seven steps, one line each, and what each landed —
dependencies installed, owners written, N repos across M owners (or: local-only,
no forge and no map), the two names recorded and the extension healthy, pane
placed on the right, reconciler up.

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

**Two things are NOT set up, by design.**

*How tasks publish, and which agent they run.* `orchestration/publish.conf` and
`agent.conf` are the operator's, gitignored, and the tracked examples beside
them name no tool, no vendor and no agent — so a fresh clone publishes by the
one shape that needs no setup (`pr`: a pull request from the task's branch) and
leaves `session create` thurbox's own default agent. A local-only fleet wants
`METHOD=push` or `METHOD=none` there, since `pr` needs a forge to prove it.
Offer them, and say what each buys: a default publish command so the lead never
retypes `--publish`, an
`ATTESTATION_MARKER` if their pipeline attests, and `FUEL_PROVIDER` so `refuel`
knows whose quota window to gate on. Without that last one `refuel` derives the
provider from each task's agent and reports `undetermined` where it cannot,
which restarts nothing. Ask too whether they run a SECOND ACCOUNT of the same
agent: that is a per-agent block in the same file
(`<agent>.LIKE=`, `<agent>.ENV=`), and without it every worker on it has no
autopilot — its dialog is unanswered and its window unread.

Copy each only where the operator has none yet; neither form overwrites:

```bash
cp -n orchestration/publish.example.conf orchestration/publish.conf
cp -n orchestration/agent.example.conf orchestration/agent.conf
```

```powershell
foreach ($f in "publish", "agent") {
  $conf = "orchestration/$f.conf"
  if (-not (Test-Path $conf)) { Copy-Item "orchestration/$f.example.conf" $conf }
}
```

*Where fleet may merge.* A fresh clone
has no `orchestration/auto-merge.conf` and the tracked example names no
repository, so `fleet queue shepherd` reviews every pull request and merges
none — saying so by name. Nobody inherits another operator's merge rights by
cloning a public repo. Offer the file, never write it; the example's header owns
the format and the gates a merge still clears:

```bash
cp -n orchestration/auto-merge.example.conf orchestration/auto-merge.conf
```

```powershell
if (-not (Test-Path orchestration/auto-merge.conf)) {
  Copy-Item orchestration/auto-merge.example.conf orchestration/auto-merge.conf
}
```

Gate anyway — `uv run fleet check` reads no operator state, so it proves the
machinery this clone runs, not the map; `uv run fleet status --records` is the
one that holds the generated map to its shape:

```bash
uv run fleet check
uv run fleet status --records
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
| Registry | the command rewrites the file wholesale from live GitHub |
| Names | `fleet voice-ask` says `skip` and keeps `voice.conf`; nothing is re-asked |
| Extension | a reinstall keeps existing `agents.toml` entries, so a customized model survives |
| Queue pane | `plugin install` reports it `current`, and `fleet place-pane` says "already placed" and touches nothing |
| Reconciler | `ensure` adopts a running one, and a `down` flag it wrote stays honoured; never a twin |

So do not refuse on an already-configured clone. Detect it —
`registry/owners.txt` with active entries, the generated map there, the
extension healthy, `plugin check` green — say which parts are already in place,
and offer to refresh the map rather than redoing everything.

**An answered `orchestration/voice.conf` is kept and not re-asked.** Renaming
either name is the operator's to start, and it needs more than a new file: the
running lead keeps the names it was rendered with. The new answer goes in with
`uv run fleet voice-ask set --replace '<operator>' '<lead>'`. Then
`.agents/skills/update-fleet/` owns applying it — the re-install, then
`thurbox-cli session restart` on the lead.

### A second fleet

Not a re-run either: a clone of its own, at a directory of its own, naming
itself. One command does the whole of it, and the first fleet is neither
touched nor asked about:

```bash
sh install.sh --dir ~/fleet-acme --name acme
```

Then run this skill again **in that checkout** — its owners, its registry, its
pane binding and its reconciler are its own. The pane draws whichever fleet's
lead is selected in the session list, so both are one keystroke apart.

### What the operator gains afterwards

The thing that actually happens after a first run is not a re-run: the operator
gains an owner, a repository, or a whole `gh` or `glab` account, and the map and
the checks have to catch up. That is **one command**, and offering it is the
narrow thing this section exists for — not the seven steps again:

```bash
uv run fleet add-owner                       # what is new; writes nothing
uv run fleet add-owner --all                 # add every new owner, then sync
uv run fleet add-owner <owner> [<owner>...]  # add the ones they picked
```

The report groups owners **by the account that reaches them**, because after a
`gh auth login` that is the shape of the question: this account is now readable,
it reaches these owners, N of them are not in your map. `*` marks an owner
already in `registry/owners.txt`, `+` one that is not, and an owner already
there is never offered twice. A login's own namespace is an owner as well as its
orgs — a new account usually brings at least two.

**ASK before you add.** Same rule as step 3 and the same reason: which owners
the map covers is the operator's call, not a consequence of which tokens happen
to be on the machine.

- **Add all of them** — every owner marked `+`
- **A subset I name** — they pick from the `+` rows
- **None** — the report was the answer

Both add forms append, keep the file's comment header and its order, refuse a
duplicate, and then sync and report **what moved** — owners added, repositories
gained or lost, the totals before and after — rather than printing the map back.

Three things it does not do, each deliberate:

- **It logs nobody in.** `gh auth login` and
  `glab auth login --hostname <host>` are interactive and the operator's. Hand
  the command over and let them run it; then run the report again.
- **A GitLab host never becomes an owner.** `registry/owners.txt` is read by
  `gh`. Authenticating one changes two other things and the report says so: the
  `glab auth` row in `fleet preflight` starts naming that host, and a task can
  target a repository there through the forge seam in `scripts/lib/forge.py`.
- **It does not onboard a fresh clone.** With no `registry/owners.txt` a bare
  report says so and points back at step 3, where the candidates come from
  three sources rather than one; `--all` or a named owner is refused there.

Two preflight rows answer the same incremental question, so re-read them rather
than the exit code alone when an operator says a credential is fine and fleet
disagrees. `gh auth` is decided **per account** — one expired token among three
is `3 of 4 accounts`, not a failed setup — and `glab auth` **per host**, naming
the instance that answered, because a self-hosted GitLab is the ordinary case
and gitlab.com is often one the operator has never used.

The one thing a re-run does **not** fix is a **rename**. thurbox names a session
when it SPAWNS it and has no verb that renames one, and `ensure_extension`
matches a declared session to a live one by NAME — so a manifest edit alone
spawns a SECOND session beside the old one and calls that healthy.

The lead is called Mission Control, wearing a glyph that is a setting rather
than a literal (`orchestration/session-glyphs.example.conf`, rendered into the
manifest at install time) — so read its exact name off `thurbox-cli session
list` rather than from any file. The EXTENSION is still `fleet` — or
`fleet-<name>` where this fleet named itself — which is deliberate and is why
`extension status <the id>` stays the right question no matter what the session
is called. `extension.toml.in`'s RENAMING
header owns both sequences — the `session fork` one that carries the lead's
conversation across, and the `extension deactivate` one that discards it —
including which step must come before which. Don't reimplement it here.
