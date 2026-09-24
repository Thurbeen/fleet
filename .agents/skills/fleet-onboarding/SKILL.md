---
name: fleet-onboarding
description: Take a fresh clone of this control plane to a working fleet — check and install the dependencies, discover the GitHub owners and sync the registry when the fleet works on a forge (a local-only fleet skips both), install the thurbox extension, place the TUI queue pane on the operator's screen, and bring the reconciler up. Use when someone has just cloned the repo, asks how to set the control plane up, asks to install fleet's dependencies or the queue pane, asks to start or restart the fleet reconciler, or invokes /fleet-onboarding.
user-invocable: true
allowed-tools: Read, Edit, Write, Bash, Glob, Grep, AskUserQuestion
---

## fleet-onboarding

A fresh clone of fleet to a control plane that runs: dependencies installed,
owners known and registry synced if the fleet works on a forge, extension
installed, the queue pane **on screen**, the reconciler up. Every step is a
`uv run fleet` command and running them is yours; what the operator needs is
a sense of where they are and a real say at the five points where the answer
is theirs.

```text
Step 1/7  Dependencies      fleet install: the plan, one answer          [ask]
Step 2/7  This checkout     is this the clone to keep?
Step 3/7  Owners            fleet discover-owners, then confirm   [ask, forge only]
Step 4/7  Registry          fleet sync-registry                         [forge only]
Step 5/7  Extension         fleet voice-ask, then fleet install-extension [ask]
Step 6/7  Queue pane        place it on screen — right by default         [ask]
Step 7/7  Reconciler        fleet reconcile ensure                        [ask]
```

Announce each step in one line before you do it. **Five asks, and no more**,
each at the step it belongs to — a wall of questions up front is answered
blind. Say what each step landed, in one line, with the evidence ("Registry:
41 repos across 3 owners"), not the command's output.

**A fresh clone is mostly empty**: no `registry/owners.txt`, no generated map,
no context files, no queue. Everything a running fleet writes is gitignored
because this repo is public. Say so when it comes up. Each command's `--help`
is its full usage.

### A local-only fleet

**No forge CLI and no forge login is a supported setup, not a degraded one.**
Step 1's ask decides it: an operator who installs no forge skips steps 3 and 4
and says so in one line each. `uv run fleet preflight` exits 0 with every
forge row missing. Tasks target local checkouts and publish with `push` — the
worker pushes to an `origin` no forge owns and reports the commit's full sha,
which git alone proves — or `none`; `pr`, `attested` and `note` need a forge,
and without one `collect` closes them as `unchecked` and names `uv run fleet
preflight --tier forge`. `shepherd` says "no forge configured" once and exits
0, the reconciler skips it while no forge CLI is on PATH and picks it up at
the next pass once one is installed, and `sync-registry`, `discover-owners`
and a bare `add-owner` say the map is optional and exit 0. A forge can be
added at any time: `uv run fleet install --forge github` (or `gitlab`), then
steps 3 and 4.

## Step 1/7 — Dependencies

```bash
uv run fleet preflight
```

One pass over everything fleet needs, in four tiers, each row with what
breaks without it — or, for a forge, what it adds — and the command that
installs it. **Read the table; do not re-probe tool by tool.** It exits
non-zero when a REQUIRED dependency is missing or `thurbox-cli` is below the
manifest's floor, and never for anything else.

| Tier | What it means |
|---|---|
| required | fleet cannot run — `git`, `uv`, `thurbox-cli`, and the multiplexer: `tmux` 3.2 or newer, or `psmux` on native Windows |
| recommended | a named capability degrades — `quota-axi` for fuel and `refuel` |
| forge | optional — `gh` and `gh auth` add GitHub, `glab` and `glab auth` add GitLab: the repo map, publish checks on change requests, shepherd merges |
| gate | only `uv run fleet check` needs it — `lua`, `prek`, and the git commit-signing configuration |

There is no Python row: `uv` brings the Python, PyYAML and the gate's own
`ruff`, `rumdl` and `pytest`. No forge is required; `gh` is what builds the
map from `registry/owners.txt`, a list of GITHUB owners, so a GitLab-only
fleet that wants a map still needs `gh`. `quota-axi` is the row most often
missed: without it the pane's fuel rows and `fleet status` read nothing, and
`refuel` cannot tell a spent window from a live one.

The two authentication rows answer what neither CLI's own status command
does. `gh auth` is decided **per account** — one expired token among three
reads `3 of 4 accounts`, the broken login named on stderr. `glab auth` is
decided **per host**, naming the instance that answered: `GITLAB_HOST`
decides when set, otherwise one working credential is enough, so an operator
authenticated to their company's GitLab and not to gitlab.com has a working
setup.

The commit-signing row is not a tool: signing turned on with no key outside
this checkout fails every commit in a repo an `includeIf gitdir:` block does
not cover — a sandbox, a worktree somewhere. The gate runs under
`tests/harness.py`'s `isolated_env`, which turns signing off, so it never
reports this as a dozen queue failures. Report it as a machine-config problem
with a one-line fix.

**ASK — installing is the operator's call.** A package manager touches the
machine outside the checkout. `uv run fleet install` prints the whole plan —
every missing dependency with THIS machine's command (`winget`, `apt-get`,
`dnf`, `pacman`, `brew`, or a tool's own installer), the `.claude/skills` link,
Codex's user-scoped `~/.agents/skills` links and the reconciler's nudge hook —
and asks once. It plans no forge unless one is named, so ask both halves in one
go:

- **Install the plan** (recommended) — required and recommended
- **The plan and the gate's tools** — for an operator who will run `fleet check`
- **Skip** — nothing is installed

and **which forge, if any** — GitHub, GitLab, both, or none: a local-only
fleet, which skips steps 3 and 4.

```bash
uv run fleet install --yes                  # the plan, no forge
uv run fleet install --yes --forge github   # and gh, and its login to run
uv run fleet install --yes --dev            # the plan and the gate tier
```

`--forge` repeats. A forge's login is a `you run` row: interactive and the
operator's. Several Linux routes need `sudo`, and the output names each
command as it runs. A failed install does not stop the others. When every
required row is present it also installs the extension with the default
names, which step 5 re-renders. It ends with `uv run fleet preflight`, and
**that table is the verification**. Re-running is safe.

If a REQUIRED tool is still missing, stop and say which: a half-onboarded
clone is worse than one that never started. The one exception is
`thurbox-cli` — steps 1 to 4 are still worth doing without it; say that 5 and
6 are deferred and that `uv run fleet install-extension` picks both up.

## Step 2/7 — This checkout

There is one remote and nothing to wire (`git remote -v`). What matters is
**which directory this is**: step 5 bakes this checkout's absolute path into
the extension, and a Mission Control registered against a scratch copy
self-heals forever against a directory about to vanish. If the working
directory is a thurbox worktree, a temp directory or an obvious throwaway,
say so and stop — moving later costs a session deletion (step 5).

`uv run fleet sync-checkout` is how changes arrive afterwards, from the
`SessionStart` hook; it only ever fast-forwards, and it is what reports that
a pull left the running lead holding stale instructions.

## Step 3/7 — Owners

**Only for a fleet that works on a forge.** A local-only operator has no
owners file and needs none: say "Owners: skipped — local-only" and go to
step 5.

`registry/owners.txt` is the one input the map needs from the operator, and
nearly all of it is already on the machine:

```bash
uv run fleet discover-owners
```

Three sources, each candidate printed with its evidence: every `gh` login's
account and orgs (asked by token name, never by switching the active
account); `github.user` and a noreply commit email in the git config; and
the `origin` of every clone under `~/code`, `~/src`, this clone's parent and
the rest — origin only, so a fork's `upstream` never becomes an owner, and an
ssh host ALIAS (`git@github-perso:owner/repo`) counts. A GitLab remote is
printed in its own section and is **not** a candidate: the map is built with
`gh`, and a GitLab repo is targeted per task through the forge seam.

**ASK — one question, not one per owner:**

- **All of them** — every candidate found
- **Just my account** — the narrowest useful map
- **A subset I name**
- **Scan somewhere else first** — `uv run fleet discover-owners ~/that/dir`,
  then ask again

An account with no orgs and no other evidence has nothing to ask about: write
the username and move on. `No candidate owners found` (still exit 0: the map
is optional) means the machine said nothing at all; only then ask them to
type their username.

Then write the file, from the tracked example:

```bash
[ -f registry/owners.txt ] || cp registry/owners.example.txt registry/owners.txt
```

Keep the comment header, replace the two placeholder lines with the confirmed
owners one per line, username first. On a **re-run** there are no
placeholders left and `uv run fleet add-owner <owner>...` is the way in
(**What the operator gains afterwards**, below). Read the file back before
moving on: the sync writes nothing for a file with no active entries.

Nothing else needs seeding. Playbooks are tracked files under
`orchestration/playbooks/`; session profiles of the operator's own go in the
gitignored `orchestration/session-profiles.local.yaml`, never the tracked
file, whose edit would stop `fleet sync-checkout`. Gitignored is not secret:
a credential reaches a worker by inheriting the thurbox server's environment.

## Step 4/7 — Registry

**Only when step 3 wrote owners.** With no owners file, no owner in it, or no
`gh`, the command says the map is optional, writes nothing and exits 0: say
"Registry: skipped — no map" and move on.

```bash
uv run fleet sync-registry
```

It enumerates every repo the operator's `gh` logins can reach — every login,
none of them switched — keeps the ones under those owners, and writes
`registry/repos.generated.yaml`. **Generated**: never hand-edit it, never
hand-write it if the command fails. Read the totals on its last three lines
back as the evidence.

**The trap:** a mistyped owner does not fail the sync. It prints `warning: no
accessible repos for owner '<x>'` on stderr and carries on, so a typo yields a
quietly thinner map. Surface that warning.

## Step 5/7 — Thurbox extension

It renders two gitignored files and installs them: `extension.toml` from
`extension.toml.in` (carrying this clone's absolute path) and
`FLEET.rendered.md` from `FLEET.md` (carrying the two names below). It also
hands the pane to `thurbox-cli plugin install`, which step 6 is about.

**ASK — the two names, before the install and never after it.** The render
bakes them into the lead's standing context, so a name chosen later costs a
re-install and a lead restart.

```bash
uv run fleet voice-ask
```

`skip` means `orchestration/voice.conf` already holds an answer: say the two
names and install. On `ask`, ask both in one go, each offering the default it
printed (`Slayer` and `VEGA` as shipped): **what should the lead call you?**
and **what should the lead answer to?** The second is the name in the lead's
prose only; the SESSION stays Mission Control. Record the answer — defaults
included, or the next run asks again — then install:

```bash
uv run fleet voice-ask set '<operator>' '<lead>'
uv run fleet install-extension
```

`set` refuses what the renderer refuses (a quote, `|`, `\`, `&`, `@`, a second
line) and then writes nothing; ask again for the one it named. It never
overwrites an existing `voice.conf`; `--replace` does, on the operator's word.
If this run is inside Mission Control itself, `fleet install` already rendered
the lead with the defaults, and a different answer reaches it only after this
install **and** a restart — `.agents/skills/update-fleet/` owns that.

Verify rather than trusting the installer's closing message:

```bash
thurbox-cli extension status <this fleet's id> --json
```

**`fleet` is the id of an UNNAMED fleet, and a named one is `fleet-<name>`.**
The installer's closing lines print this fleet's id in every command it hands
you, and `thurbox-cli extension status --json` with no name lists every
installed extension. Take the id from one of those rather than typing `fleet`,
here and in every command below — `deactivate` aimed at the wrong id deletes
ANOTHER fleet's lead and its conversation.

**The trap that matters most:** `[[sessions]] repo_path` is baked in at
install time, so run this from the clone the operator intends to keep.
Re-running the installer does not fix a moved clone: thurbox reuses an
extension's session by name and never moves it, and `extension status` still
calls it healthy because it checks that the session EXISTS. The installer
catches it and exits non-zero; the remedy deletes the session and its
history, so hand the decision to the operator:

```bash
thurbox-cli extension deactivate <this fleet's id>   # deletes the session
uv run fleet install-extension                      # respawns it at the right path
```

**That same refusal has a SECOND cause, and the remedies are opposites.** A
second, unnamed fleet on one machine renders the extension id and lead name
the first already answers to. If the live session it names is another fleet's
lead, deactivate nothing: name this fleet instead, which costs nothing
because nothing is running under the new name. Write
`orchestration/fleet.conf` with one line, `NAME=acme` (letters, digits, `_`,
`-`, no double underscore) and run `uv run fleet install-extension` again.
**Write that file with a file tool, never a shell redirect**: cmd reads
`<name>` as a redirect and Windows PowerShell's `>` writes UTF-16LE. Ask the
operator which of the two situations it is — the clone moved, or this is a
second fleet — and never guess: one answer deletes a conversation.
`orchestration/fleet.example.conf` holds the grammar.

## Step 6/7 — The queue pane, on screen

Step 5 installed the pane. This step is the half that **is not finished when
that command exits 0**: a pane names a *slot*, the arrangement decides where
that slot goes, and a pane nothing places loads, lists, declares its keys and
draws nothing.

```bash
thurbox-cli plugin check
```

| It says | What it means | What you do |
|---|---|---|
| `✓ loads — … fleetqueue …`, exits 0 | installed and placed | say that `F3` opens it |
| `✗ … nothing places slot "fleetqueue"` | installed, invisible | the ask below |
| no `fleetqueue` anywhere | the install did not take | re-run step 5 and read its output |

**ASK — always, and never place it silently.** `layout.lua` is the operator's
file; every pane on their screen shares it, and a mistake there takes the
whole interface. Ask it through `uv run fleet pane-ask`, the one record of
the answer — Mission Control asks the same question on its first session, so
an answer not recorded is asked twice. Run it bare first: `skip` means it was
already answered or the layout already places the pane. On `ask`:

- **Place it on the right** (recommended) — a column right of the terminal,
  `pct = 30, min = 34`
- **Place it on the left** — between the session list and the terminal
- **Show me the block, I will add it myself**
- **Skip** — the pane stays installed and invisible; `fleet place-pane`
  places it whenever they want it

```bash
uv run fleet place-pane --dry-run   # the file, the anchor, the exact block
uv run fleet pane-ask yes           # right of the terminal (yes --left: other side)
uv run fleet pane-ask no            # skip, or "I will add it myself" — remembered
```

If they add it themselves, print this block **with its guard** — a bare
`{ slot = "fleetqueue" }` draws but leaves `F3` opening a pane that never
closes — and still run `uv run fleet pane-ask no` to record the choice:

```lua
if panels.shown("fleetqueue") and filled(ctx, "fleetqueue") then
  columns[#columns + 1] = { slot = "fleetqueue", pct = 30, min = 34 }
end
```

`.agents/skills/fleet-pane/` §4 owns the rest: where the block goes, what
`fleet place-pane` refuses and backs up, and `thurbox-cli plugin dir --text`
for the interface directory (never a literal path). Its §7 is the symptom
table if the pane comes back placed and empty.

**One thing that is theirs and not yours.** The pane runs its probe through
`uv` in the Mission Control session's checkout, which needs thurbox's **`run`
capability**. Declaring it does not grant it: the switch is thurbox's own
settings, `Ctrl+,` → `]` → `t`. Say it once; until then the pane draws an
honest "not trusted yet".

**On a re-run**, `plugin install` reports the pane `current`, `plugin check`
says whether the block is there, and `fleet place-pane` says "already placed".
Check before you speak. If `thurbox-cli` was missing at step 1, defer this
step exactly as step 5 is deferred.

## Step 7/7 — The reconciler

**ASK — the loop runs on their machine, and it is theirs to start:**

- **Bring it up now** (recommended) — folds thurbox's event stream and runs
  `collect`, `shepherd` and `refuel` on their own intervals
- **Leave it down** — every one of those then happens only when the lead
  remembers; `uv run fleet reconcile ensure` starts it later

```bash
uv run fleet reconcile ensure
uv run fleet reconcile status
```

Without it, one session ended with 19 of 20 progress timelines empty and
three merged pull requests unnoticed for forty minutes. On a local-only fleet
it is just as worth running: it skips `shepherd` while no forge CLI is on
PATH, says so once, and resumes by itself once `fleet install --forge …` adds
one. **`ensure`, never `start`**: `stop` writes a durable `down` flag that
`ensure` honours and `start` clears, and a running loop holds a lock the OS
drops however it died, so a crashed loop is never mistaken for a live one.
`AGENTS.md`'s reconciler section owns what it may and may not do; pass on the
one thing the operator will meet — it types one line into Mission Control
when the ready set grows, and that is not a bug.

The worker `Stop` hook that nudges the loop is already in place: step 1's
`fleet install` merged it into Claude Code's user settings (`uv run fleet
paths claude-settings`), never into thurbox's hooks file, which thurbox
rewrites on every start. It is an accelerator, never the mechanism: a worker
that ran out of quota fires no hook at all.

## Hand over

Close with a short recap: the seven steps, one line each, and what each
landed — dependencies installed, owners written, N repos across M owners (or:
local-only, no forge and no map), the two names recorded and the extension
healthy, pane placed, reconciler up.

**Nothing this skill wrote to the repo is tracked.** `registry/owners.txt`,
`registry/repos.generated.yaml`, `extension.toml` and `FLEET.rendered.md` are
gitignored, so `git status` is clean and there is nothing to push. Say it: an
operator who sees an empty `git status` will otherwise assume it failed. The
one thing outside the repo that changed is `layout.lua`, if they said yes,
with a `.bak-<timestamp>` beside it.

**Two things are NOT set up, by design**, and both are gitignored copies of
tracked examples that name no tool, vendor or repository — nobody inherits
another operator's pipeline or merge rights by cloning a public repo:

- `orchestration/publish.conf` and `agent.conf`: a default publish command so
  the lead never retypes `--publish` (a local-only fleet wants `METHOD=push`
  or `METHOD=none`, since `pr` needs a forge to prove it), an
  `ATTESTATION_MARKER` if their pipeline attests, `FUEL_PROVIDER` so `refuel`
  knows whose window to gate on, and a per-agent block (`<agent>.LIKE=`,
  `<agent>.ENV=`) if they run a SECOND ACCOUNT of one agent — without it every
  worker on it has no autopilot.
- `orchestration/auto-merge.conf`: where `shepherd` may merge. Absent, it
  reviews every pull request and merges none, saying so by name.

Copy each only where the operator has none yet (`cp -n` on POSIX, `Copy-Item`
guarded by `Test-Path` on Windows); the examples' headers own the formats.

Gate anyway — `uv run fleet check` reads no operator state, so it proves the
machinery; `uv run fleet status --records` holds the generated map to its
shape. Then tell them the one thing that is theirs next: open the Mission
Control session in thurbox and give it a goal. Two things worth saying once:
the map, run logs and context notes live in **this working copy only**, and
`registry/context/_TEMPLATE.md` is where the judgement about a project goes
— the first thing worth writing.

## Re-running

Every step **converges**: dependencies are pure probes, owners are added and
never duplicated or reordered, the registry is rewritten wholesale,
`fleet voice-ask` says `skip` and keeps `voice.conf`, a reinstall keeps
existing `agents.toml` entries, `plugin install` reports `current`, and
`ensure` adopts a running loop and honours a `down` flag. So do not refuse on
an already-configured clone: detect it, say which parts are in place, and
offer to refresh the map. Renaming either voice name is
`uv run fleet voice-ask set --replace '<operator>' '<lead>'`, and then
`.agents/skills/update-fleet/` owns applying it.

### A second fleet

A clone of its own, at a directory of its own, naming itself:

```bash
sh install.sh --dir ~/fleet-acme --name acme
```

Then run this skill again **in that checkout**; it shares nothing with the
first. The pane draws whichever fleet's lead is selected in the session list.

### What the operator gains afterwards

The thing that actually happens after a first run is an owner, a repository,
or a whole `gh` or `glab` account, and the map has to catch up. That is one
command, not the seven steps again:

```bash
uv run fleet add-owner                       # what is new; writes nothing
uv run fleet add-owner --all                 # add every new owner, then sync
uv run fleet add-owner <owner> [<owner>...]  # add the ones they picked
```

The report groups owners **by the account that reaches them** (`*` already
in the map, `+` not), which is the shape of the question after a `gh auth
login`. **ASK before you add**, same rule as step 3: add all, a subset they
name, or none. Both add forms append in the file's order, refuse a duplicate,
then sync and report what moved. It logs nobody in (`gh auth login` and `glab
auth login --hostname <host>` are interactive and theirs), a GitLab host never
becomes an owner (it changes the `glab auth` row and what a task can target),
and it does not onboard a fresh clone — with no `registry/owners.txt` a bare
report points back at step 3, and `--all` or a named owner is refused.

The one thing a re-run does **not** fix is a **rename** of the lead: thurbox
names a session when it spawns it, and `ensure_extension` matches by NAME, so
a manifest edit alone spawns a second session beside the old one.
`extension.toml.in`'s RENAMING header owns both sequences; do not reimplement
it here. Read the lead's exact name off `thurbox-cli session list`.
