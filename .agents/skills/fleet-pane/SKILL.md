---
name: fleet-pane
description: Put the fleet queue pane on the operator's thurbox screen and diagnose it when it is installed and drawing nothing, or drawing the wrong thing. Covers the install (a side effect of uv run fleet install-extension), what verifies it, the layout.lua block that places it and the command that writes that block on the operator's word, the F-key that hides it, and removal. Use when asked to install, place, hide, remove or debug the TUI queue pane, when the pane is there and empty, or when it draws too much to read.
user-invocable: true
allowed-tools: Read, Bash, Glob, Grep
---

## fleet-pane

`interface/fleet_queue.lua`, drawn in a thurbox column. Its own header owns
what the view is and why it is built the way it is; this skill owns getting
it onto a screen and finding out why it is not on one.

> **Installing the pane and seeing the pane are two different things.**
> `thurbox-cli plugin install` succeeding and `plugin list` showing the pane
> are both true of a pane the operator cannot see. A pane names a *slot*; the
> ARRANGEMENT decides where that slot goes, and the arrangement is
> `layout.lua`, a file every pane on their screen shares. A pane no
> arrangement places loads cleanly, declares its keys, and draws nothing.
>
> **`uv run fleet place-pane` writes that block, and only ever because the
> operator said to.** Ask, then run it — §4.

That is the failure with no symptom. Reach for §4 first when a pane is
"installed but not there".

## 1. What it is

A **readout, not a place you go**: `focusable = false`, no key on it
dispatches, collects or merges anything, the wheel scrolls it, and its one
action is the F-key in §5. It is the fleet's only live view of the queue, over
the same records `uv run fleet queue list` reads; `fleet queue show` is still
where you read a task in full.

**It calls nothing itself.** Fuel comes from `uv run fleet status --fuel` on a
five-minute TTL, never from `quota-axi` directly. Every word of the `⇡`
artifact row under a task comes off `task.yaml`'s `publish` block, so the pane
runs no `gh` and says nothing `fleet queue show` would not.

**Which fleet it draws is the SESSION LIST's answer.** With one lead it draws
it and asks nothing. With several it draws whichever lead is selected,
remembers that choice by its checkout, and keeps drawing it while you work in
worker sessions; until one is selected it lists them and draws none. The
frame's title carries the fleet's name whenever it has one.

Four things that turn into questions:

- **An absent row means "nothing to report", never a fault.** A provider that
  could not be read is not drawn; a task whose publish has not started has no
  `⇡` row. Only nothing reading at all draws `unavailable` with the reason.
- **A remembered reading never looks measured**: a probe that has not
  answered is a spinner, a stale one is hatched and flagged.
- **`green` is not the ok colour.** It means every gate the forge knows holds
  and *nobody vetted it* — a different claim from `ready`, and why fleet will
  not merge it. The note beside a state is its next move (`— yours to merge`).
- **If the column looks sheared by one cell, turn `FUEL_GLYPH` off** at the
  top of `interface/fleet_queue.lua` before looking anywhere else. U+26FD is
  two cells wide, and a font that draws it narrow shears every row below it.

It runs inside the thurbox interface, which knows nothing about fleet, so it
finds the control plane by **probing the lead session by NAME** and running
its queue probe in that session's checkout. Hence most of §7: the lead must
exist under the name the pane expects, and the pane needs thurbox's `run`
capability. The name is `CONTROL_PLANE` at the top of the pane, **without the
glyph** — which glyph the lead wears is a setting
(`orchestration/session-glyphs.example.conf`) that `fleet install-extension`
renders into the manifest, and `uv run fleet check pane` holds the two files
to one name. Both probes are one plain command line, because thurbox runs
them through `sh -c` on POSIX and `cmd /C` on Windows:

```text
uv run --frozen --quiet python scripts/lib/pane_probe.py
uv run --frozen --quiet fleet status --fuel
```

So `uv` has to be on the PATH thurbox runs them with.
`scripts/lib/pane_probe.py`'s docstring owns the record format.

## 2. Installing it

```sh
uv run fleet install-extension
```

That is the whole command: it installs the extension and, in a second pass,
hands the pane to `thurbox-cli plugin install`. Two things to get right:

- **Run it from the control-plane checkout the operator intends to keep** —
  never a worktree, a scratch clone or a temp directory. The manifest bakes
  the absolute path in, and the pane probes the session that path spawns.
- **If a lead session already exists, re-running does not move it.** The
  installer detects that and exits non-zero naming a remedy that deletes the
  session's conversation. Surface it; do not run it.

The pane install is non-fatal to the extension install, so a failed `plugin
install` prints a warning and the command still exits 0. Read the output.

## 3. Verifying

```sh
thurbox-cli plugin check --text
```

The one command that tells "placed" from "loads and draws nothing": it loads
the interface as thurbox does and exits non-zero on a pane nothing places,
naming the file and the block. `fleetqueue` in its `✓ loads — …` list, exit 0,
is the verification. Anything else, §4.

`thurbox-cli plugin list --text` answers a different question — whether the
FILE is installed (`plugins/91_fleet_queue.lua  pane  installed  hidden`).
**Do not read that row as working**: `hidden` is what a placed pane says while
toggled off. `--json` adds `installed_from`, which names the checkout the pane
came from, so a stale path there and a moved clone are the same bug.

## 4. Placing it — the operator's call, and then the command's job

The block goes **inside the `columns` list** of `layout.lua`:

```lua
if panels.shown("fleetqueue") and filled(ctx, "fleetqueue") then
  columns[#columns + 1] = { slot = "fleetqueue", pct = 30, min = 34 }
end
```

**Ask first — always.** A mistake in `layout.lua` takes the whole interface
down, and it is the operator's file. The choice is real: right of the
terminal (recommended — a narrow readout beside the agent), left of it, or
the block printed for them to add by hand. On yes:

```sh
uv run fleet place-pane --dry-run    # the file, the anchor and the exact block
uv run fleet place-pane              # right of the terminal
uv run fleet place-pane --left       # between the session list and the terminal
uv run fleet place-pane --check      # is it placed? changes nothing
```

**The first-run ask goes through `uv run fleet pane-ask`**, which FLEET.md
has the lead run at the start of every session: it says `ask` only while the
pane is unplaced and nobody has answered, and the answer goes back as its
argument — `yes`, `yes --left` or `no` — kept in the gitignored
`orchestration/first-run/pane` so the question is asked once per checkout,
ever. Placing by hand with `fleet place-pane` is fine too; the next `pane-ask`
finds it placed.

`scripts/lib/place_pane.py`'s docstring owns what makes it safe: it refuses a
layout it does not recognise, is idempotent, backs the file up to
`layout.lua.bak-<timestamp>`, re-reads its edit with `lua` and restores the
backup if the result no longer parses, keeps the line endings, reads the slot
from the pane file, and finishes with `plugin check`.

If they would rather do it themselves, print the block, name the file, and
stop. Find the file with `thurbox-cli plugin dir --text` (its first line is
the directory) rather than assuming a path — Windows keeps it under
`%APPDATA%`, a dev build elsewhere again. **Give the guard, not just the
slot**: `plugin check` suggests a bare `{ slot = "fleetqueue" }`, which is
enough to DRAW and not enough for the F-key — an unguarded column is carved
every frame, so the key flips a state nothing reads and the pane never
closes. `uv run fleet install-extension` prints this same block when `plugin
check` came back unplaced.

## 5. The F-key

`F3` hides and shows the column — the pane's only action, a **global** chord
because an unfocusable pane can be reached no other way, and the way back
since it resolves from the key registry rather than from what is on screen.
The `Fleet · F3` button on the pane's top border and the `Fleet` pill in the
action band are the clickable forms, and both follow a rebind in thurbox's
settings. A queue longer than the column scrolls under the fuel block and the
counters; the wheel scrolls it, `↑ N above` / `↓ N below` page it, and the
palette (`Ctrl+P`) has `fleetqueue.page_up` / `page_down`. There is no scroll
chord on purpose: a global key is taken from every terminal.

What the key must not be is one the KERNEL owns (help, theme, settings): a
plugin-scoped binding loses to a kernel one silently, registering fine and
never receiving the key. `uv run fleet check pane` refuses those.

## 6. Removing it

```sh
thurbox-cli plugin remove plugins/91_fleet_queue.lua
```

The argument is the **destination path**, not the basename —
`plugin remove 91_fleet_queue.lua` answers "not listed" and removes nothing.
That takes back the file, its `plugins.toml` entry and the lock. It leaves the
`layout.lua` block behind, and `fleet place-pane` has no verb to take it out —
a block it did not necessarily write is not one it should delete. Guarded by
`filled(ctx, "fleetqueue")`, an orphaned block carves nothing. Taking back the
whole extension is `thurbox-cli extension deactivate` / `uninstall`, and it
does not remove the pane.

## 7. Installed but nothing draws

First: **is there a column with words in it?** The pane never draws a blank
column; every condition it can distinguish it spells out in the column.

- **No column at all, or none after `F3`** — a placement problem, §4.
- **A column with a message in it** — the pane is placed and running, and the
  message is the diagnosis:

| What you see | What it means | What to do |
|---|---|---|
| no column; `plugin check` exits non-zero | installed, placed by nothing | §4 — ask, then `uv run fleet place-pane` |
| column opens and never closes | the block is missing `panels.shown` | §4 — the guard is missing from a hand-added block |
| `F3` opens Help, Theme or Settings | the chord collides with a kernel one | rebind in thurbox settings |
| `not trusted yet` | the `run` capability is declared, not granted | the operator grants it: settings (`Ctrl+,`) → `]` → `t`. You cannot do it for them |
| `no '<lead>' session` | no session by the name the pane probes | the extension is not installed, or the lead was renamed — §2, and `extension.toml.in`'s RENAMING header |
| `<n> <lead> sessions here`, then a row per fleet | several fleets on this machine, none selected — a supported setup | select the lead whose queue you want in the session list |
| the WRONG fleet's queue | bound to the fleet last selected | select this fleet's lead; the title says which is drawn |
| `the <lead> session is unreachable` | thurbox has the session but cannot reach it | a thurbox-side problem |
| `the queue probe did not run`, and a cwd | the probe printed nothing: not executable in that session, `uv` not on thurbox's PATH, or that directory is not a fleet checkout | a cwd that is not your control-plane checkout is the moved-clone case in §2; otherwise the capability, a wedged session, or run the probe line from §1 there |
| `no queue directory at <path>` | queue.py resolved a root that does not exist | run `uv run fleet queue root` in that checkout |
| `the queue probe failed: <why>` | the probe ran and could not read the queue | the reason is the diagnosis; run the probe line from §1 there |
| `the queue is empty` | nothing is queued | correct, not a fault |

A non-zero exit from the probe is NOT a fault: it spells every condition on
stdout and exits 0, so the pane treats only a probe it could not run, or one
that said nothing, as a failure. Stale rows are not a fault either: the pane
re-asks on a TTL.

**A pane that draws the WRONG thing is a different problem.** "It draws too
much", "a row appeared that should not be there" are claims about layout.
Render it instead of squinting at it:

```sh
lua scripts/lib/pane_harness.lua 44        # the pane, as text, at 44 columns
lua scripts/lib/pane_harness.lua 30        # and at the width it routinely gets
lua scripts/lib/pane_harness.lua 44 --marks   # `B` marks a row carrying bold
```

The harness stubs thurbox's `lib.*` modules and feeds the pane a fixed queue —
no thurbox, no queue, no session — and shows what the pane BUILDS, never what
a terminal paints. Edit the fixture at the bottom to reproduce a shape.

## 8. The gate

`uv run fleet check pane` keeps this skill, the installer and the pane from
drifting: one spelling of the slot name, the placement guard, the `plugin
remove` path and the F-key across all three, and no binding on a kernel chord
(`tests/pane/test_agreement.py`). `tests/pane/test_render.py` (needs `lua`)
renders the pane offline and asserts the design — one row per task, finished
work weighing less than running work, all of it fitting thirty columns — and
`tests/pane/test_place_pane.py` drives `fleet place-pane` against a recorded
stock layout. None of it is a Lua linter: the pane's own gate is
`thurbox-cli plugin check`, at install time (§3).
