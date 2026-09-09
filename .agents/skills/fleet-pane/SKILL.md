---
name: fleet-pane
description: Put the fleet queue pane on the operator's thurbox screen and diagnose it when it is installed and drawing nothing. Covers the install (a side effect of scripts/install-extension.sh), what verifies it, the layout.lua block that places it and that nothing here writes, the F-key that hides it, and removal. Use when asked to install, place, hide, remove or debug the TUI queue pane, or when the pane is there and empty.
user-invocable: true
allowed-tools: Read, Bash, Glob, Grep
---

## fleet-pane

`interface/fleet_queue.lua` drawn in a thurbox column. Its own header owns what
the view is and why it is built the way it is; this skill owns getting it onto a
screen and finding out why it is not on one.

> **Installing the pane and seeing the pane are two different things, and only
> one of them is fleet's.** `thurbox-cli plugin install` succeeding and
> `thurbox-cli plugin list` showing the pane are both true of a pane the
> operator cannot see. A thurbox pane names a *slot*; the ARRANGEMENT decides
> where that slot goes, and the arrangement is `layout.lua` — a file every pane
> on their screen shares, so **nothing in this repo writes it and neither do
> you**. A pane no arrangement places loads cleanly, declares its keys, appears
> in `plugin list`, and draws nothing.

That is the failure with no symptom, and every message the operator has says it
should be working. Reach for §4 before anything else when a pane is "installed
but not there".

## 1. What it is, and what it is not

A **readout, not a place you go**. `focusable = false`, so the focus ring walks
past it, `ctrl+h`/`ctrl+l` never land on it, and there is no key on it that
dispatches, collects or merges anything — `scripts/queue.sh` stays the only
thing that writes to the queue. The wheel scrolls it. Its one action is the
F-key in §5.

It is the same view `./scripts/webui.sh` serves, in a column instead of a
browser tab, reading the same records. The web page is still the better place to
read a `BRIEF.md`; the pane is for not alt-tabbing to notice a task changed
state.

**The top rows are the fuel, not a task.** The account's remaining provider
windows are the constraint every row under them competes for, so they sit above
the counters: a head row carrying the reserve and how old the reading is, then
**one row per subscription** — the provider's name, a bar, and its percentage.
The bar is a second encoding of the number and never a replacement, it is
coloured by the same reserve the head row names, and it marks where that floor
falls across it. The pane does not read `quota-axi` — it asks
`./scripts/fleet-status.sh --fuel`, the same reading the status screen prints,
on a five-minute TTL of its own because that reading costs a network call.
FLEET.md's `## Fuel` section owns the reserve, which arrives on the record so
the pane never spells the number itself.

**Three readings are not bars**, and each looks different on purpose: a probe
that has not answered is a spinner, a provider that could not be read says
`unavailable` with its reason — an empty bar there would read as a spent window
rather than a missing one — and a stale reading is hatched and flagged, because
a number that is remembered rather than observed must not look identical to one
that was just measured.

**What a narrow column drops**, and this one is routinely thirty cells wide: the
reserve on the head row first, then the bar (under five cells it is a
decoration), then the reason on an unavailable row, which truncates. The number
never goes. The detail row under a reading — the binding window and when it
comes back — is drawn only when exactly one provider carries a number; several
readings at two rows each would push the queue itself off the column, and
`./scripts/fleet-status.sh` is where every window is printed in full.

It runs inside the thurbox interface, which knows nothing about fleet, so it
finds the control plane by **probing the lead session by NAME** and running
`./scripts/queue.sh root` in it. Two consequences that explain most of §7: the
lead session must exist under the name the pane expects, and the pane needs
thurbox's `run` capability to ask it anything. The name lives in the
`CONTROL_PLANE` constant at the top of `interface/fleet_queue.lua` and in
`extension.toml.in`, which owns renaming it — read it there rather than
remembering it.

## 2. Installing it

```bash
./scripts/install-extension.sh
```

That is the whole command. The pane is **not a separate step**: that script
installs the thurbox extension and, in a second pass, hands
`interface/fleet_queue.lua` to `thurbox-cli plugin install` with the destination
name and `--text`. Its header owns the details and the reason a pane travels
this way rather than as an `[[external_files]]` payload in the manifest.

Two things to get right before running it:

- **Run it from the control-plane checkout the operator intends to keep** —
  never a worktree, a scratch clone or a temp directory. The manifest bakes this
  clone's absolute path in at install time, and the pane probes the session that
  path spawns.
- **If a lead session already exists, re-running does not move it.** The
  installer detects that and exits non-zero naming the remedy, which deletes
  that session's conversation history. That is the operator's call to make, not
  yours — surface it, do not run it.

The pane install is deliberately not fatal to the extension install: a control
plane with no pane still works, so a `plugin install` that failed prints a
warning and the script still exits 0. Read the output; do not infer the pane
from the exit code.

## 3. Verifying

```bash
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

## 4. Placing it — the operator's edit, not yours

The block goes **inside the `columns` list** of `layout.lua`, beside the other
side columns:

```lua
if panels.shown("fleetqueue") and filled(ctx, "fleetqueue") then
  columns[#columns + 1] = { slot = "fleetqueue", pct = 30, min = 34 }
end
```

Find the file rather than assuming `~/.config/thurbox/ui` — a dev build's
interface directory is elsewhere:

```bash
thurbox-cli plugin dir --text | head -1
```

**Print the block, say where it goes, and stop there on purpose.** A mistake in
`layout.lua` takes the whole interface down, not one column, and it is the
operator's file — the bundled panes, their arrangement and any other plugin they
have all live in it. This skill does not carry write tools for that reason. Say
plainly that you stopped, rather than offering to do it.

**Give the guard, not just the slot.** `plugin check` suggests a bare
`{ slot = "fleetqueue" }`, and that is enough to make the pane DRAW — which is
all `check` knows about. It is not enough to make the F-key work: an unguarded
column is carved on every frame, so the key flips a panel state nothing reads
and the pane opens and never closes. `panels` and `filled` both already exist in
the stock `layout.lua`, guarding the session list exactly this way.

`./scripts/install-extension.sh` prints this same block, at the moment it is
needed, when `plugin check` came back unplaced. Prefer letting it — the block it
prints is the one the gate holds to the pane's actual slot name.

## 5. The F-key

`F3` hides and shows the column, and that is the pane's only action. It is a
**global** chord because an unfocusable pane can never be reached by any other
kind — and it is the way BACK, since the binding resolves from the key registry
rather than from what is on screen, so a hidden pane still answers it. The hint
rides in the pane's own title (`F3 hides`) because a pane the focus ring skips
never reaches the footer's context hints.

Nothing depends on the key being F3: the title resolves its own chord from the
registry, so rebinding the action in thurbox's settings moves the binding and
the hint together. What the key must not be is one the KERNEL already owns — a
plugin-scoped binding loses to a kernel one silently, registering fine and never
receiving the key. `./scripts/check.sh pane` refuses those; see §8.

## 6. Removing it

```bash
thurbox-cli plugin remove plugins/91_fleet_queue.lua
```

The argument is the **destination path**, not the basename.
`plugin remove 91_fleet_queue.lua` answers "not listed in plugins.toml" and
removes nothing. That one command takes back the file, its `plugins.toml` entry
and the lock together; `plugin list` names the path to pass while it is still
installed, and `scripts/install-extension.sh`'s header owns this.

Removing the pane leaves the `layout.lua` block behind. It is guarded by
`filled(ctx, "fleetqueue")`, so an orphaned block carves nothing and is
harmless — but it is the operator's line to delete, on the same terms as §4.

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
| no column; `plugin check` exits non-zero | installed, placed by nothing | §4 — print the block |
| column opens and never closes | placement block is missing `panels.shown` | §4 — add the guard |
| `F3` opens Help, Theme or Settings | the chord collides with a kernel one | rebind in thurbox settings; `check.sh pane` refuses a kernel chord in the repo |
| `not trusted yet` | the `run` capability is declared, not granted | the operator grants it: settings (`Ctrl+,`) → `]` → `t`. You cannot do it for them |
| `no '<lead>' session` | no session by the name the pane probes | the extension has not been installed, or the lead was renamed — §2, and `extension.toml.in`'s RENAMING header |
| `the <lead> session is unreachable` | thurbox has the session but cannot reach it | a thurbox-side problem, not a pane one |
| `the queue probe did not run` | the probe could not be executed in that session | usually the capability or a wedged session; the cwd it names is where it tried |
| `not the control-plane checkout` | the lead session opens a directory with no `./scripts/queue.sh` | the manifest points at the wrong clone — moved-clone case in §2 |
| `queue.sh could not name a queue directory` | `queue.sh root` answered nothing usable | run `./scripts/queue.sh root` in that checkout and read what it says |
| `the queue is empty` | nothing is queued | correct, not a fault |

Note what is NOT a fault: a non-zero exit from the probe. It spells every
condition it can tell apart on stdout and exits 0, so the pane treats only a
probe it could not run, or one that said nothing at all, as a failure — the
header explains why reading the status instead reported an empty queue as a
broken pane.

Stale rows are also not a fault. The pane re-asks on a TTL rather than per
frame; the constant and the reasoning are at the top of `fleet_queue.lua`.

## 8. The gate

`./scripts/check.sh pane` is what keeps this skill and the installer from
drifting apart from the pane. It holds one spelling of the slot name, the
placement guard, the `plugin remove` path and the F-key across the pane, the
installer and the documents that print the block — this file among them — and it
refuses a binding on a chord the kernel owns. Read `check_pane` in
`scripts/check.sh` for what exactly it asserts; the point here is that it does,
so an edit to any of those strings must go green in `./scripts/check.sh` before
it ships.

It is not a Lua linter and does not try to be. The pane's own gate is
`thurbox-cli plugin check`, which needs a thurbox install, so it belongs at
install time — §3.
