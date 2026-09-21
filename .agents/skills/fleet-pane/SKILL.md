---
name: fleet-pane
description: Put the fleet queue pane on the operator's thurbox screen and diagnose it when it is installed and drawing nothing, or drawing the wrong thing. Covers the install (a side effect of uv run fleet install-extension), what verifies it, the layout.lua block that places it and the command that writes that block on the operator's word, the F-key that hides it, and removal. Use when asked to install, place, hide, remove or debug the TUI queue pane, when the pane is there and empty, or when it draws too much to read.
user-invocable: true
allowed-tools: Read, Bash, Glob, Grep
---

## fleet-pane

`interface/fleet_queue.lua` drawn in a thurbox column. Its own header owns what
the view is and why it is built the way it is; this skill owns getting it onto a
screen and finding out why it is not on one.

> **Installing the pane and seeing the pane are two different things.**
> `thurbox-cli plugin install` succeeding and `thurbox-cli plugin list` showing
> the pane are both true of a pane the operator cannot see. A thurbox pane
> names a *slot*; the ARRANGEMENT decides where that slot goes, and the
> arrangement is `layout.lua` — a file every pane on their screen shares. A
> pane no arrangement places loads cleanly, declares its keys, appears in
> `plugin list`, and draws nothing.
>
> **`uv run fleet place-pane` writes that block, and only ever because the
> operator said to.** It is never a step that happens on the way to something
> else: ask, then run it. It refuses a layout it cannot recognise, backs the
> file up, re-reads its own edit with `lua`, and verifies with `plugin check`.
> §4 is the whole of it.

That is the failure with no symptom, and every message the operator has says it
should be working. Reach for §4 before anything else when a pane is "installed
but not there".

## 1. What it is, and what it is not

A **readout, not a place you go**. `focusable = false`, so the focus ring walks
past it, `ctrl+h`/`ctrl+l` never land on it, and no key on it dispatches,
collects or merges anything — `uv run fleet queue` stays the only thing that
writes to the queue. The wheel scrolls it. Its one action is the F-key in §5.

It is the fleet's only live view of the queue, over the same records
`uv run fleet queue list` reads. `fleet queue show` is still where you read a
task in full; the pane is for noticing a task changed state without asking.

**`interface/fleet_queue.lua`'s header owns what it draws and why** — the fuel
rows above the counters, the `⇡` artifact row under a task (its declared
`publish.method` — `attested`, `pr`, `push`, `note`, `served` or `none` — what
that turned out to be, and what fleet last saw), and what a narrow column drops first (`PUBLISH_WORD`,
`PUBLISH_LADDER`).

**It calls nothing itself.** Fuel comes from `uv run fleet status --fuel` on a
five-minute TTL, never from `quota-axi` directly; FLEET.md's `## Fuel` section
owns the reserve. Every word of the `⇡` row comes off `task.yaml`'s `publish`
block, written by `collect`, `shepherd` and `reap` — so the pane runs no `gh`
and says nothing `fleet queue show` would not print in the same word.

**Which fleet it draws is the SESSION LIST's answer, not a setting.** A machine
may run several fleets — one clone each, each with a Mission Control of its own
— and they are different queues. With one lead the pane draws it and asks
nothing. With several it draws whichever lead is selected in the session list,
remembers that choice by its checkout, and keeps drawing it while you work in
worker sessions (a worker names no fleet, and its worktree points at no queue).
Until one is selected it lists them and draws none: the fleet you meant is not
a thing to guess. The frame's title carries the fleet's name whenever it has
one.

Four consequences are worth knowing here because they turn into questions:

- **An absent row means "nothing to report", never a fault.** A provider that
  could not be read is not drawn; a task whose publish has not started has no
  `⇡` row. Only nothing reading at all draws a head row saying `unavailable`,
  with the reason under it. `uv run fleet status` names every provider and why
  its fetch failed.
- **A remembered reading never looks measured.** A probe that has not answered
  is a spinner and a stale one is hatched and flagged.
- **`green` is not the ok colour.** It means every gate the forge knows about
  holds and *nobody vetted it* — a different claim from `ready`, and why fleet
  will not merge it for you. The note beside a state is its next move, not its
  colour: `— yours to merge` on green, `— review` on `open`.
- **If the column looks sheared by one cell, turn `FUEL_GLYPH` off before
  looking anywhere else.** It is at the top of `interface/fleet_queue.lua`; set
  it to nil and the block draws what it drew before the glyph existed. U+26FD is
  East_Asian_Width WIDE — two cells — and the pane measures it correctly, but a
  font that draws it narrow shears every row below it.

It runs inside the thurbox interface, which knows nothing about fleet, so it
finds the control plane by **probing the lead session by NAME** and running its
queue probe in that session's checkout. Two consequences that explain most of
§7: the lead must exist under the name the pane expects, and the pane needs
thurbox's `run` capability to ask it anything. The name lives in the
`CONTROL_PLANE` constant at the top of `interface/fleet_queue.lua` and in
`extension.toml.in`, which owns renaming it.

**Both probes are one command line**, because thurbox runs a pane's probe
through `sh -c` on POSIX and `cmd /C` on Windows, and a plain command is the
one thing both read the same way:

```text
uv run --frozen --quiet python scripts/lib/pane_probe.py
uv run --frozen --quiet fleet status --fuel
```

So `uv` has to be on the PATH thurbox runs them with.
`scripts/lib/pane_probe.py`'s docstring owns the record format the pane reads;
it asks `queue.py` for the queue root, which is the answer
`uv run fleet queue root` gives in that checkout.

That constant holds the name **without the glyph**, matching the lead behind any
single mark: which glyph the lead wears is a setting
(`orchestration/session-glyphs.example.conf`) that
`uv run fleet install-extension` renders into the manifest, and a pane spelling
one of its values would say "no session" the day the operator flipped it.
`uv run fleet check pane` holds the two files to the same name.

## 2. Installing it

```sh
uv run fleet install-extension
```

That is the whole command. The pane is **not a separate step**: it installs the
thurbox extension and, in a second pass, hands `interface/fleet_queue.lua` to
`thurbox-cli plugin install` with the destination name and `--text`.
`scripts/lib/install_extension.py`'s docstring owns the details.

Two things to get right before running it:

- **Run it from the control-plane checkout the operator intends to keep** —
  never a worktree, a scratch clone or a temp directory. The manifest bakes this
  clone's absolute path in at install time, and the pane probes the session that
  path spawns.
- **If a lead session already exists, re-running does not move it.** The
  installer detects that and exits non-zero naming the remedy, which deletes
  that session's conversation history. That is the operator's call to make, not
  yours — surface it, do not run it.

The pane install is not fatal to the extension install — a control plane with
no pane still works — so a failed `plugin install` prints a warning and the
command still exits 0. Read the output; do not infer the pane from the exit
code.

## 3. Verifying

```sh
thurbox-cli plugin check --text
```

This is the one command that can tell "placed" from "loads and draws nothing".
It loads the interface exactly as thurbox does and exits non-zero on a pane
nothing places, naming the file and the block to add:

```text
  ✓ loads — sessions, agent, confirm, search, new_session, restore, fleetqueue
```

`fleetqueue` in that list, exit 0, is the verification. Anything else, go to §4.

`thurbox-cli plugin list --text` answers a different question — whether the FILE
is installed and where it came from:

```text
plugins/91_fleet_queue.lua  pane  installed  hidden
```

**Do not read that row as working.** `hidden` is what a placed pane says while
its column is toggled off; the bundled session list says `hidden` too. The row
proves the file is registered in `plugins.toml`, and proves nothing about
whether anything draws it. `--json` adds `installed_from`, which is the useful
part: it names the checkout the pane was installed from, so a stale path here
and a moved clone are the same bug.

## 4. Placing it — the operator's call, and then the command's job

The block goes **inside the `columns` list** of `layout.lua`, beside the other
side columns:

```lua
if panels.shown("fleetqueue") and filled(ctx, "fleetqueue") then
  columns[#columns + 1] = { slot = "fleetqueue", pct = 30, min = 34 }
end
```

**Ask first — always.** A mistake in `layout.lua` takes the whole interface
down, not one column, and it is the operator's file: the bundled panes, their
arrangement and every other plugin they have all live in it. So the choice is
theirs, and it is a real one — right of the terminal (the recommended place: a
narrow readout beside the agent, session list still on the left), left of it,
or the block printed for them to add by hand.

On yes:

```sh
uv run fleet place-pane --dry-run    # the file, the anchor and the exact block
uv run fleet place-pane              # right of the terminal
uv run fleet place-pane --left       # between the session list and the terminal
uv run fleet place-pane --check      # is it placed? changes nothing
```

**The first-run ask goes through `uv run fleet pane-ask`**, which FLEET.md has
the lead run at the start of every session: it says `ask` only while the pane is
unplaced and nobody has answered, and the operator's answer goes back as its
argument — `yes`, `yes --left` or `no`. `yes` runs `fleet place-pane`, and the
answer is kept in the gitignored `orchestration/first-run/pane` so the question
is asked once per checkout, ever. A layout that already places the pane is never
asked about. Answering here by hand with `fleet place-pane` is fine too — the
next `fleet pane-ask` finds it placed and records that.

What makes it safe enough to run, argued in full in
`scripts/lib/place_pane.py`'s docstring: it refuses a layout it does not
recognise and names the part it could not find, it is idempotent, it backs the
file up to `layout.lua.bak-<timestamp>`, it re-reads its own edit with `lua`
(when `lua` is on PATH) and restores the backup if the result no longer parses,
and it finishes with `thurbox-cli plugin check`. It rewrites the file in place
and keeps its line endings, so a CRLF layout stays CRLF. The slot it writes is
read from `interface/fleet_queue.lua`, so a rename cannot half-land.

If they would rather do it themselves, print the block, name the file, and stop
there on purpose. Find the file rather than assuming a path — on Windows the
interface directory is under `%APPDATA%`, and a dev build's is elsewhere again.
The first line this prints is the directory, and `layout.lua` is in it:

```sh
thurbox-cli plugin dir --text
```

**Give the guard, not just the slot.** `plugin check` suggests a bare
`{ slot = "fleetqueue" }`, and that is enough to make the pane DRAW — which is
all `check` knows about. It is not enough to make the F-key work: an unguarded
column is carved on every frame, so the key flips a panel state nothing reads
and the pane opens and never closes. `panels` and `filled` both already exist in
the stock `layout.lua`, guarding the session list exactly this way.

`uv run fleet install-extension` prints this same block, at the moment it is
needed, when `plugin check` came back unplaced. Prefer letting it — the block it
prints is the one the gate holds to the pane's actual slot name.

## 5. The F-key

`F3` hides and shows the column, and that is the pane's only action. It is a
**global** chord because an unfocusable pane can never be reached by any other
kind — and it is the way BACK, since the binding resolves from the key registry
rather than from what is on screen, so a hidden pane still answers it. The hint
rides on the pane's own top border as a `Fleet · F3` button, filled like the
agent pane's tabs, because a pane the focus ring skips never reaches the
footer's context hints; it sits in the frame's overlay rather than its title
because only an overlay run is a click target. A click on it hides the column,
and the `Fleet` pill in the action band is the clickable way back, since a
closed column has no border to click.

Nothing depends on the key being F3: the button resolves its own chord from the
registry, and so does the pill, so rebinding the action in thurbox's settings
moves the binding and both hints together.

A queue longer than the column scrolls under the fuel block and the counters,
which stay put. The wheel scrolls it; `↑ N above` and `↓ N below` are clickable
and page it; the palette (`Ctrl+P`) has `fleetqueue.page_up` and
`fleetqueue.page_down`; and the bottom border says which rows are on screen
(`12-30 of 58`). There is deliberately no scroll chord: the pane never holds
focus, so only a global key could reach it, and a global key is taken from
every terminal. If the wheel does nothing over the pane, the pane's root has
lost its `id` — an unfocusable pane is a wheel target only through one. What the key must not be is one the KERNEL already owns — a
plugin-scoped binding loses to a kernel one silently, registering fine and never
receiving the key. `uv run fleet check pane` refuses those; see §8.

## 6. Removing it

```sh
thurbox-cli plugin remove plugins/91_fleet_queue.lua
```

The argument is the **destination path**, not the basename.
`plugin remove 91_fleet_queue.lua` answers "not listed in plugins.toml" and
removes nothing. That one command takes back the file, its `plugins.toml` entry
and the lock together; `plugin list` names the path to pass while it is still
installed, and `scripts/lib/install_extension.py`'s docstring owns this.

Removing the pane leaves the `layout.lua` block behind, and `fleet place-pane`
has no verb that takes it back out — a block it did not necessarily write is
not one it should delete. It is guarded by `filled(ctx, "fleetqueue")`, so an
orphaned block carves nothing and is harmless; deleting it is the operator's
line, on the same terms as §4.

Taking back the whole extension is a different verb —
`thurbox-cli extension deactivate` / `uninstall`, which the installer's closing
message lists — and it does not remove the pane.

## 7. Installed but nothing draws

First, the question that splits the problem in two: **is there a column with
words in it?**

The pane never draws a blank column. Every condition it can distinguish it
spells out in the column itself, so:

- **No column at all, or a column that is not there after `F3`** — a placement
  problem. Go to §4.
- **A column with a message in it** — the pane is placed and running, and the
  message is the diagnosis. It is in the table below.

| What you see | What it means | What to do |
|---|---|---|
| no column; `plugin check` exits non-zero | installed, placed by nothing | §4 — ask, then `uv run fleet place-pane` |
| column opens and never closes | placement block is missing `panels.shown` | §4 — the guard is missing from a hand-added block |
| `F3` opens Help, Theme or Settings | the chord collides with a kernel one | rebind in thurbox settings; `fleet check pane` refuses a kernel chord in the repo |
| `not trusted yet` | the `run` capability is declared, not granted | the operator grants it: settings (`Ctrl+,`) → `]` → `t`. You cannot do it for them |
| `no '<lead>' session` | no session by the name the pane probes | the extension has not been installed, or the lead was renamed — §2, and `extension.toml.in`'s RENAMING header |
| `<n> <lead> sessions here`, then a row per fleet | several fleets on THIS machine, and none of them selected yet — a supported setup, not a fault | select the lead whose queue you want in the session list; the pane binds it and stays there while you work in worker sessions. A lead mirrored from another host beside one local lead is normal: the pane binds the local one and says nothing |
| the pane draws the WRONG fleet's queue | a machine with several fleets, bound to the one last selected | select this fleet's lead in the session list. The title says which fleet is drawn (` Fleet queue · acme `), and a fleet that named none is the only fleet there is, so its title says nothing |
| `the <lead> session is unreachable` | thurbox has the session but cannot reach it | a thurbox-side problem, not a pane one |
| `the queue probe did not run`, and a cwd | the probe printed nothing: it could not be executed in that session, `uv` is not on the PATH thurbox ran it with, or that directory is not a fleet checkout | a cwd that is not your control-plane checkout is the moved-clone case in §2; otherwise the capability, a wedged session, or run the probe line from §1 in that directory and read what it says |
| `no queue directory at <path>` | queue.py resolved a queue root that does not exist | run `uv run fleet queue root` in that checkout and read what it says |
| `the queue probe failed: <why>` | the probe ran and could not read the queue | the reason is the diagnosis; run the probe line from §1 in that checkout to see it whole |
| `the queue is empty` | nothing is queued | correct, not a fault |

Note what is NOT a fault: a non-zero exit from the probe. It spells every
condition it can tell apart on stdout and exits 0, so the pane treats only a
probe it could not run, or one that said nothing at all, as a failure — the
header explains why reading the status instead reported an empty queue as a
broken pane.

Stale rows are also not a fault. The pane re-asks on a TTL rather than per
frame; the constant and the reasoning are at the top of `fleet_queue.lua`.

**And a pane that draws the WRONG thing is a different problem from a pane that
draws nothing.** "It draws too much", "I cannot tell what is running", "a row
appeared that should not be there" are claims about layout, and none of the
messages above apply to them. Render it instead of squinting at it, from the
checkout:

```sh
lua scripts/lib/pane_harness.lua 44        # the pane, as text, at 44 columns
lua scripts/lib/pane_harness.lua 30        # and at the width it routinely gets
lua scripts/lib/pane_harness.lua 44 --marks   # `B` marks a row carrying bold
```

The harness stubs thurbox's four `lib.*` modules and feeds the pane a fixed
queue, so it needs no thurbox, no queue and no session — and it shows what the
pane BUILDS, never what a terminal paints. Edit the fixture at the bottom of
the harness to reproduce a shape you are chasing.

## 8. The gate

`uv run fleet check pane` keeps this skill, the installer and the pane from
drifting apart: it holds ONE spelling of the slot name, the placement guard, the
`plugin remove` path and the F-key across all three — this file among them —
and refuses a binding on a chord the kernel owns.
`tests/pane/test_agreement.py` is what it asserts; what matters here is that an
edit to any of those strings must go green in `uv run fleet check` before it
ships.

The same check runs `tests/pane/test_render.py` (needs `lua`), which renders
the pane offline through the harness and asserts the DESIGN rather than the
wiring — one row per task, no row carrying no information, finished work
weighing less than running work, and all of it still fitting thirty columns.
`tests/pane/test_place_pane.py` covers the writer: it drives
`fleet place-pane` against a recorded stock layout — placed right by default,
left on `--left`, idempotent, backed up, line endings kept, refused on an
arrangement it cannot read, still parsing as Lua afterwards.
`uv run fleet check extension` covers the installer and the first-run ask.

None of it is a Lua linter. The pane's own gate is `thurbox-cli plugin check`,
which needs a thurbox install, so it belongs at install time — §3.
