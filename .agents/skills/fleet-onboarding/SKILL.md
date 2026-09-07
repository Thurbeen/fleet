---
name: fleet-onboarding
description: Take a fresh clone of this control-plane template to a working fleet — check the clone is wired to update, discover the GitHub owners, write registry/owners.txt, sync the registry, install the thurbox extension, and verify each step. Use when someone has just cloned the template, asks how to set the control plane up, or invokes /fleet-onboarding.
user-invocable: true
allowed-tools: Read, Edit, Write, Bash, Glob, Grep, AskUserQuestion
---

## fleet-onboarding

Takes a fresh clone of the fleet template to a control plane that actually runs:
wired to update, owners known, registry synced, thurbox extension installed.

**Do the work, don't narrate it.** The steps are mechanical —
`registry/owners.txt`, `scripts/sync-registry.sh`, `scripts/install-extension.sh`
— and the user should not be reading a numbered list and typing along. Infer
what is discoverable, ask once about the one thing that genuinely needs them,
run the scripts, and **verify each step landed** rather than assuming it did.

**A fresh clone is mostly empty on purpose.** No `registry/owners.txt`, no
generated map, no context files, no run logs, no `orchestration/playbooks/local/`
content. Everything a running fleet writes is gitignored, so the template ships
the *shape* — the `_TEMPLATE.md` files and the directories — and nothing else.
Say that when it comes up; a user who reads the layout and finds half of it
missing should hear that it is correct, not wonder what failed.

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

`jq` is needed by `scripts/sync-registry.sh`; `thurbox-cli` only by step 4. If
thurbox is the only thing missing you may still do steps 1 to 3 — say plainly
that step 4 is deferred and what to run once thurbox is installed.

## 1. Remotes — the update path, before anything else

A control plane that cannot update itself is the failure this skill exists to
prevent, and it is invisible later: everything works until the day someone tries
to pull and there is nothing to pull from.

```bash
git remote -v
```

Two remotes are wanted, and they are not interchangeable:

| Remote | Points at | Used by |
|---|---|---|
| `origin` | **their** repo | `git push`, `scripts/sync-checkout.sh` |
| `template` | the fleet template | `scripts/update-from-template.sh` |

Fix whatever is missing:

- **`template` missing, `origin` still the template** — they cloned and have not
  repointed yet. Rename it, then create their own repo as the new `origin`:

  ```bash
  git remote rename origin template
  gh repo create <name> --private --source=. --remote=origin --push
  ```

  Ask before creating a repo on their account. Offer the name of the checkout
  directory as the default.

- **`template` missing, `origin` already theirs** — just add it:

  ```bash
  git remote add template https://github.com/Thurbeen/fleet.git
  ```

- **Neither, and no shared history** — this is a repo made with "Use this
  template" or bootstrapped on its own. Add the remote as above; the update path
  then needs a one-time `./scripts/update-from-template.sh --adopt`, which
  `/fleet-update` owns. Do not run it here without saying what it does.

Verify by asking for the answer rather than assuming:

```bash
./scripts/update-from-template.sh     # previews; writes nothing
```

`already current` or a preview both mean the path works. `skipped: no 'template'
remote` or `skipped: no shared history` mean it does not — say which, and fix it
before moving on.

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

Two more instance-owned locations exist so nobody has to conjure them. Seed the
profile overrides from the tracked example if it is not there yet, and say where
both live — a user who cannot find where their own work goes will edit the
tracked file, diverge, and lose the fast-forward on their first customisation:

```bash
[ -f orchestration/session-profiles.local.yaml ] ||
  cp orchestration/session-profiles.local.example.yaml \
     orchestration/session-profiles.local.yaml
ls orchestration/playbooks/local/          # your playbooks go here
```

| You want to… | Edit | Not |
|---|---|---|
| write a playbook | `playbooks/local/<name>.md` | `playbooks/<name>.md` |
| tune a worker's settings | `session-profiles.local.yaml` | `session-profiles.yaml` |
| set a value you would not commit | `session-profiles.local.yaml` | anything tracked |

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
that is about to vanish. If the clone later moves, re-run the installer.

## 5. Hand over

**Nothing this skill wrote is tracked.** `registry/owners.txt`,
`registry/repos.generated.yaml` and `extension.toml` are all gitignored, so
`git status` is clean and there is nothing to commit or push. That is the
design, not a step you forgot: an instance's tracked tree stays identical to the
template's, which is what keeps `./scripts/update-from-template.sh` a clean
fast-forward. `.gitignore`'s header has the reasoning.

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
| Remotes | `git remote add` on an existing remote fails harmlessly; check before adding |
| Owners | adds only missing entries; never duplicates or reorders |
| Registry | the script rewrites the file wholesale from live GitHub |
| Extension | a reinstall keeps existing `agents.toml` entries, so a customized model survives |

So the right move on an already-configured clone is not to refuse. Detect it —
both remotes are present, `registry/owners.txt` has active entries, the
generated map exists, the extension status is healthy — say which parts are
already in place, and offer to refresh the map rather than redoing the whole
thing.

The one thing a re-run does **not** fix is a **rename**. If the session has
been renamed away from `fleet`, the extension is registered under the new name
and `extension status fleet` is the wrong question to ask. The README's
customizing section owns that; don't reimplement it here.
