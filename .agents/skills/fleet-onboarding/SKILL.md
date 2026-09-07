---
name: fleet-onboarding
description: Take a fresh clone of this control-plane template to a working fleet — discover the GitHub owners, write registry/owners.txt, sync the registry, install the thurbox extension, and verify each step. Use when someone has just used the template, asks how to set the control plane up, or invokes /fleet-onboarding.
user-invocable: true
allowed-tools: Read, Edit, Write, Bash, Glob, Grep, AskUserQuestion
---

## fleet-onboarding

Takes a clone made from **Use this template** to a control plane that actually
runs: owners known, registry synced, thurbox extension installed.

**Do the work, don't narrate it.** The three setup steps are mechanical —
`registry/owners.txt`, `scripts/sync-registry.sh`, `scripts/install-extension.sh`
— and the user should not be reading a numbered list and typing along. Infer
what is discoverable, ask once about the one thing that genuinely needs them,
run the scripts, and **verify each step landed** rather than assuming it did.

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

`jq` is needed by `scripts/sync-registry.sh`; `thurbox-cli` only by step 3. If
thurbox is the only thing missing you may still do steps 1 and 2 — say plainly
that step 3 is deferred and what to run once thurbox is installed.

## 1. Owners — infer, then confirm once

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

Write the confirmed owners into `registry/owners.txt`:

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

## 2. Registry

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

## 3. Thurbox extension

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
that is about to vanish. If the clone later moves, re-run the installer.

## 4. Hand over

Two tracked files changed: `registry/owners.txt` and
`registry/repos.generated.yaml`. `extension.toml` is gitignored and stays
local.

Gate before committing — this repo's whole convention is that a green local run
is the real gate, and the `yaml` check is the one that asserts the generated
map's shape:

```bash
./scripts/check.sh
```

Then offer the commit; do not push on the user's behalf without asking.

```bash
git add registry/owners.txt registry/repos.generated.yaml
git commit -m "chore(registry): onboard this control plane"
```

Then tell them the one thing that is genuinely theirs to do next: open the
`fleet` session in thurbox and give it a goal. Everything else — playbooks, run
logs, worker sessions — follows from that, and `AGENTS.md` is where the session
picks the loop up.

Optionally point at `registry/context/_TEMPLATE.md`: the generated map says
which repos exist, and a context file is where the judgement about one of them
goes. That is the first thing worth writing, not a setup step they are missing.

## Re-running

Assume someone runs this twice. Every step above **converges**:

| Step | Second run |
|---|---|
| Preflight | pure probes, writes nothing |
| Owners | adds only missing entries; never duplicates or reorders |
| Registry | the script rewrites the file wholesale from live GitHub |
| Extension | a reinstall keeps existing `agents.toml` entries, so a customized model survives |

So the right move on an already-configured clone is not to refuse. Detect it —
`registry/owners.txt` has active entries, the generated map exists, the
extension status is healthy — say which parts are already in place, and offer
to refresh the map rather than redoing the whole thing.

The one thing a re-run does **not** fix is a **rename**. If the session has
been renamed away from `fleet`, the extension is registered under the new name
and `extension status fleet` is the wrong question to ask. The README's
customizing section owns that; don't reimplement it here.
