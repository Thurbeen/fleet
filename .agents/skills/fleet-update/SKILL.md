---
name: fleet-update
description: Bring this control plane current with the fleet template it was cloned from — preview the change, apply it safely, and deal with the running lead session still holding the old instructions. Use when someone asks to update fleet, pull the latest template, self-update the control plane, or invokes /fleet-update.
user-invocable: true
allowed-tools: Read, Bash, Glob, Grep, AskUserQuestion
---

## fleet-update

Updates this control plane from the **template** it was cloned from.

`scripts/update-from-template.sh` does the git. This skill does the two things a
script cannot: read the report back as outcomes rather than status lines, and
handle the fact that **you are running on the instructions this update replaces**.

## The one thing that makes this different from any other update

A running agent froze `FLEET.md`, `AGENTS.md` and every skill it had loaded at
the moment it launched. Nothing reloads them from disk. So when the update
changes them, the files on disk are new and **you are still the old version** —
and the operator will reasonably assume the update reached you.

Re-reading the files does not fix it. It appends a second copy of your own job
description with no defined precedence, and it cannot reach a skill that is
already loaded. Only a fresh conversation loads them properly.

Say this plainly whenever the updater prints `restart-lead: yes`. Never let a
successful pull read as a successful update of the agent.

## 0. Preflight

```bash
git rev-parse --show-toplevel     # you must be in the control-plane checkout
git remote -v                     # a `template` remote must exist
git status --short                # must be clean of TRACKED changes
```

The script refuses on each of these itself and says what to do, so you do not
need to pre-empt it — run it and read the answer. Do not "help" by stashing,
committing, or switching branches on the operator's behalf.

**If `scripts/update-from-template.sh` does not exist here**, this control plane
predates it. Take it from the template without touching the working tree, and
run it from there; the real copy arrives with the update it performs:

```bash
git fetch template
git show template/main:scripts/update-from-template.sh > /tmp/fleet-update.sh
bash /tmp/fleet-update.sh
```

Its exit code is the machine-readable outcome: `0` nothing to do or applied
cleanly, `1` usage or environment error, `2` refused and changed nothing.

## 1. Preview, always

```bash
./scripts/update-from-template.sh
```

It fetches, compares, prints exactly what would change, and **writes nothing**.
Show the operator the file list before applying anything. A control plane that
rewrites itself without showing its work is one nobody runs twice.

Read the first line as the outcome:

| Line | Means |
|---|---|
| `template: already current` | nothing to do — say so and stop |
| `template: would fast-forward <a>..<b> — N file(s)` | the ordinary case |
| `template: would merge <a>..<b> — N file(s)` | this instance has its own commits; the merge is already proved conflict-free |
| `template: skipped: <reason>` | nothing happened; §3 |

A `still tracked, though the template treats them as yours` block lists files
this instance committed that the split now calls instance-owned — old run logs,
old context notes. **The script never touches them, and neither should you
without asking.** They are the operator's own history. Explain the trade rather
than deciding it: while those files stay tracked the instance stays diverged, so
updates merge instead of fast-forwarding; untracking them restores the
fast-forward but stops git backing them up. The command is in the report; the
decision is theirs.

A `handover: N file(s)` block means the template has stopped tracking files it
now considers this instance's own — `registry/owners.txt` and the generated map
are the ones that moved that way. They are untracked here and left on disk
untouched. Say that plainly: nothing was deleted, and this is why the update did
not conflict on them.

## 2. Apply

```bash
./scripts/update-from-template.sh --apply
```

Then act on the two action lines it prints, in order:

**`restart-lead: yes`** — the instruction surface moved under you. Tell the
operator what changed and that the running session is stale. Offer the restart;
do not perform it silently, because it ends the conversation you are having:

```bash
thurbox-cli session restart fleet     # resumes the conversation
thurbox-cli session delete fleet      # fresh one; the extension self-heals it
```

Be honest about the difference. `restart` re-resolves launch-time wiring and
re-reads the files, but the old copy is still in the resumed conversation.
`delete` is the clean load — the extension recreates the session — at the cost
of the conversation. For a change to how you are supposed to *behave*, the
second is the honest one.

**`reinstall-extension: yes`** — `extension.toml.in` or `FLEET.md` moved, so the
installed extension no longer matches the manifest it was rendered from:

```bash
./scripts/install-extension.sh
thurbox-cli extension status fleet --json
```

Then gate, so the operator knows the checkout is coherent:

```bash
./scripts/check.sh
```

## 3. When it skips

Every skip is a real refusal that changed nothing, and the script prints the
command that resolves it. Relay the reason; do not work around it.

| Skip | What it means | What NOT to do |
|---|---|---|
| `dirty working tree` | uncommitted tracked work | never stash or commit for them |
| `on '<branch>', expected main` | wrong branch | never switch branches for them |
| `could not fetch template` | offline or unreachable | nothing; retry later |
| `no shared history with the template` | this instance was made with "Use this template", or bootstrapped on its own | §4 |
| `N file(s) would conflict` | both sides changed the same tracked file | never resolve it blind; show the list |

For a conflict, the useful next step is showing the operator what the template
actually did to the file, so they can decide:

```bash
git diff HEAD...template/main -- <path>
```

If the conflicting file is one they tuned on purpose — a playbook, a session
profile — the durable fix is to move their version to the instance-owned side
(`orchestration/playbooks/local/`, `orchestration/session-profiles.local.yaml`)
and let the tracked one match the template again. That restores the fast-forward
for every future update instead of re-fighting the same conflict.

## 4. An instance that predates the clone shape

A repo made with **Use this template**, or bootstrapped on its own, shares no
commit with the template — no common ancestor, so git has nothing to merge
against. Join them once:

```bash
./scripts/update-from-template.sh --adopt
```

It records the template as an ancestor with `-s ours`, which keeps the tree
**byte for byte** and imports no content. Say that clearly: adopt does not
deliver the improvements that already exist upstream, it makes every *future*
update ordinary. Taking today's improvements is a separate, deliberate act:

```bash
git checkout template/main -- scripts/ .agents/skills/
```

Merging the two histories for real instead is possible and is the operator's
call, not yours — it conflicts on every file both sides changed, which for a
long-lived instance is most of the tracked tree.

## 5. Report

One line per outcome, in the script's own vocabulary, then what is left for
them:

> Updated `a1b2c3d..e4f5a6b` — 12 files, fast-forward. `FLEET.md` and two skills
> changed, so this session is running the old copies; restart it to pick them
> up. The extension manifest did not move, so no reinstall.

Never report a pull as a reload. Never report a skipped update as an update.
