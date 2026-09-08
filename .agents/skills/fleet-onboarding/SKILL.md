---
name: fleet-onboarding
description: Take a fresh clone of this control plane to a working fleet — discover the GitHub owners, write registry/owners.txt, sync the registry, install the thurbox extension, bring the queue monitor up, and verify each step. Use when someone has just cloned the repo, asks how to set the control plane up, asks to start or restart the fleet monitor, or invokes /fleet-onboarding.
user-invocable: true
allowed-tools: Read, Edit, Write, Bash, Glob, Grep, AskUserQuestion
---

## fleet-onboarding

Takes a fresh clone of fleet to a control plane that actually runs: owners
known, registry synced, thurbox extension installed, and the queue monitor up.

**Do the work, don't narrate it.** The steps are mechanical —
`registry/owners.txt`, `scripts/sync-registry.sh`, `scripts/install-extension.sh`,
`scripts/webui.sh` — and the user should not be reading a numbered list and
typing along. Infer
what is discoverable, ask once about the one thing that genuinely needs them,
run the scripts, and **verify each step landed** rather than assuming it did.

**A fresh clone is mostly empty on purpose.** No `registry/owners.txt`, no
generated map, no context files, no run logs, no queue. Everything a running
fleet writes is gitignored — this repo is public and that content is the
operator's — so what is tracked is the machinery plus the `_TEMPLATE.md` forms,
and nothing else. Say that when it comes up; a user who reads the layout and
finds half of it missing should hear that it is correct, not wonder what
failed.

The scripts remain the supported manual path — see the README. This skill is
the easy road over them, not a replacement for them.

## 0. Preflight — before anything is written

Probe every prerequisite **first**. A half-onboarded clone (owners written, no
registry) is worse than one that never started, and a bad error message here
costs the user entirely.

| Need | Probe | If missing, say |
|---|---|---|
| `git` | `command -v git` | install git |
| `gh` | `command -v gh` | install the GitHub CLI: <https://cli.github.com> |
| `gh` authenticated | `gh auth status` | `gh auth login` |
| `jq` | `command -v jq` | install jq (`brew install jq`, `apt install jq`, …) |
| `thurbox-cli` | `command -v thurbox-cli` | install thurbox: <https://github.com/Thurbeen/thurbox> |
| thurbox ≥ floor | compare against `min_thurbox_version` in `extension.toml.in` | `thurbox-cli` is too old; upgrade to the floor or newer |

**Read the version floor from the manifest, never from memory.** It is one
number with one owner, and `extension.toml.in` records why it sits there:

```bash
floor=$(sed -n 's/^min_thurbox_version *= *"\(.*\)"/\1/p' extension.toml.in)
have=$(thurbox-cli --version | awk '{print $NF}')
[ "$(printf '%s\n%s\n' "$floor" "$have" | sort -V | head -1)" = "$floor" ] ||
  echo "thurbox-cli $have is below the $floor floor"
```

Report **every** missing prerequisite in one pass with its remedy, then stop.
Discovering them one restart at a time is the frustrating version of this.

`jq` is needed by both `scripts/sync-registry.sh` (step 3) and
`scripts/install-extension.sh` (step 4); `thurbox-cli` only by step 4. If
thurbox is the only thing missing you may still do steps 1 to 3 — say plainly
that step 4 is deferred and what to run once thurbox is installed.

## 1. The checkout — is this the one to keep?

There is one remote and nothing to wire:

```bash
git remote -v      # origin -> their own copy of fleet
```

The question worth asking here is not about remotes; it is **which directory
this is**. Step 4 bakes this checkout's absolute path into the thurbox
extension, and a `fleet` session registered against a scratch copy self-heals
forever against a directory that is about to vanish. So if the working
directory is a thurbox worktree, a temp directory or an obvious throwaway, say
so now and stop — moving later costs a session deletion (see step 4), and it is
free to avoid here.

`./scripts/sync-checkout.sh` is how changes arrive afterwards. It runs from the
`SessionStart` hook and only ever fast-forwards, so there is nothing to
configure; it is worth knowing it exists because it is also what reports that a
pull left the running lead session holding stale instructions.

## 2. Owners — infer, then confirm once

`registry/owners.txt` is the one input that genuinely needs the user. It is
also mostly **discoverable**, so asking them to type what an authenticated `gh`
session already knows is exactly the friction this skill exists to remove.

```bash
gh api user --jq .login          # their username
gh api user/orgs --jq '.[].login' # the orgs they belong to
```

If the org call errors or comes back empty on an account you expect orgs for,
the token is missing the scope: `gh auth refresh -s read:org`. Do not treat
that as "no orgs" silently — say which it was.

Then **one** question, not one per owner: show the discovered list and ask
whether to cover all of it, just their username, or a subset they name. An
account with no orgs has nothing to ask about — write the username and move on.

`registry/owners.txt` is **gitignored** and will not exist in a fresh clone.
Start it from the tracked example rather than writing one from memory:

```bash
[ -f registry/owners.txt ] || cp registry/owners.example.txt registry/owners.txt
```

Then write the confirmed owners into it:

- **Keep the comment header.** It documents the file's own format for whoever
  edits it later by hand.
- Replace the two `# your-github-username` / `# your-org` placeholder lines with
  the confirmed owners, one per line, username first.
- On a **re-run** there are no placeholders left. Add only owners not already
  present, and leave the existing order alone — the sync emits owners in this
  file's order, so reshuffling it churns the generated map for nothing.

Verify before moving on — the sync refuses to run on a file with no active
entries, and it is better to catch that here:

```bash
grep -vE '^[[:space:]]*(#|$)' registry/owners.txt
```

Nothing else needs seeding. Playbooks and session profiles are tracked files
the operator edits directly — `orchestration/playbooks/<name>.md` from
`_TEMPLATE.md`, and `orchestration/session-profiles.yaml` in place. The one
thing worth saying about the latter: it is committed to a **public** repo, so
nothing environment-specific goes in it, and a credential should reach a worker
by inheriting the thurbox server's environment rather than by living in a file.

## 3. Registry

```bash
./scripts/sync-registry.sh
```

It enumerates every repo the user's own `gh` session can reach and keeps the
ones under those owners. It writes `registry/repos.generated.yaml`, which is
**generated** — never hand-edit it, and never hand-write it if the script
fails.

Verify the map is not empty, and read the totals back to the user as the
evidence that this worked:

```bash
tail -3 registry/repos.generated.yaml   # totals: repos / owners
```

**The trap:** a mistyped owner does not fail the sync. The script prints
`warning: no accessible repos for owner '<x>'` on stderr and carries on, so a
typo yields a quietly thinner map. Surface that warning — it almost always
means a typo or an org the token cannot see, and it is fixable in seconds now
and confusing a week from now.

## 4. Thurbox extension

```bash
./scripts/install-extension.sh
```

It renders `extension.toml` (gitignored — it carries this clone's absolute
path) from `extension.toml.in`, then installs it. Verify, rather than trusting
the installer's own closing message:

```bash
thurbox-cli extension status fleet --json
```

That exits non-zero and answers `{"error": ...}` when no manifest is
registered, which is the honest signal that the install did not take.

**The trap that matters most here:** `[[sessions]] repo_path` is baked in at
install time. Run this from **the clone the user intends to keep** — not a
thurbox worktree, not a scratch copy, not a temp directory. A `fleet` session
registered against a disposable path self-heals forever against a directory
that is about to vanish.

Getting it wrong is not fixed by re-running the installer. thurbox reuses an
extension's session by name and never moves it, so a second install rewrites
the manifest, reports success, and leaves the session on the old path — and
`extension status` still calls that healthy, because it checks that the session
EXISTS, not where it points. The installer catches this for you and exits
non-zero; the remedy it names deletes the session and its history, so hand that
decision to the user rather than running it for them:

```bash
thurbox-cli extension deactivate fleet   # deletes the session
./scripts/install-extension.sh           # respawns it at the right path
```

## 5. The monitor

```bash
./scripts/webui.sh ensure
```

A local, read-only web view of `orchestration/queue`: every topic classified by
what its tasks are doing, and under each the plan (`BRIEF.md`), the progress
(`progress.jsonl`) and the outcome (`result.md`). It binds `127.0.0.1` and
picks its own port, so run it and read back the URL it prints rather than
assuming one.

**Run `ensure`, never `start`.** They differ in exactly one way and it is the
one that matters here:

| | On a running monitor | After the user asked it down |
|---|---|---|
| `ensure` | adopts it, prints the URL | leaves it down |
| `start` | adopts it, prints the URL | **brings it back up** |

`stop` writes a flag to `orchestration/webui/down`, and that flag is the whole
reason "down" means anything: it survives a restart, a reboot and this skill
being run again. `ensure` reads it and does nothing. If this step used `start`,
every onboarding run would quietly resurrect a monitor the user had switched
off, which is a stop that does not stop.

So on a re-run, `ensure` says one of three things and all three are correct:
it started it, it adopted the one already running, or the user asked it down
and it stayed down. Read the output back rather than announcing a URL.

Verify, and say where it is:

```bash
./scripts/webui.sh status
```

Two things to pass on, once:

- It **displays and does not control**. There is no button that dispatches,
  cancels or reorders anything — `scripts/queue.sh` remains the only thing
  that writes to the queue.
- To switch it off for good: `./scripts/webui.sh stop`. To bring it back:
  `./scripts/webui.sh start`.

## 6. Hand over

**Nothing this skill wrote is tracked.** `registry/owners.txt`,
`registry/repos.generated.yaml` and `extension.toml` are all gitignored, so
`git status` is clean and there is nothing to commit or push. That is the
design, not a step you forgot: this repo is public, and an index of every repo
the operator can reach — along with one machine's absolute paths — does not
belong in it. `.gitignore`'s header has the reasoning.

Say it explicitly. A user who set up a control plane and sees an empty
`git status` will otherwise assume it failed.

Gate anyway — this repo's whole convention is that a green local run is the real
gate, and the `yaml` check is the one that asserts the generated map's shape:

```bash
./scripts/check.sh
```

Then tell them the one thing that is genuinely theirs to do next: open the
`fleet` session in thurbox and give it a goal. Everything else — playbooks, run
logs, worker sessions — follows from that, and `AGENTS.md` is where the session
picks the loop up.

Two things are worth saying once, because neither is discoverable later:

- The map, the run logs and the context notes live in **this working copy
  only**. If they matter beyond this machine, that is theirs to back up.
- `registry/context/_TEMPLATE.md` is where the judgement about a project goes.
  The generated map says which repos exist; a context file says what one is
  *for*. That is the first thing worth writing, not a setup step they missed.

## Re-running

Assume someone runs this twice. Every step above **converges**:

| Step | Second run |
|---|---|
| Preflight | pure probes, writes nothing |
| Checkout | a question, not a write |
| Owners | adds only missing entries; never duplicates or reorders |
| Registry | the script rewrites the file wholesale from live GitHub |
| Extension | a reinstall keeps existing `agents.toml` entries, so a customized model survives |
| Monitor | `ensure` adopts a running one and respects a `down` flag; never a twin |

So the right move on an already-configured clone is not to refuse. Detect it —
`registry/owners.txt` has active entries, the generated map exists, the
extension status is healthy — say which parts are already in place, and offer to
refresh the map rather than redoing the whole thing.

The one thing a re-run does **not** fix is a **rename**. If the session has
been renamed away from `fleet`, the extension is registered under the new name
and `extension status fleet` is the wrong question to ask. The README's
customizing section owns that; don't reimplement it here.
