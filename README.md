<!-- rumdl-disable MD041 -->
<!-- The banner is the first line by design; MD041 wants a level-1 heading. -->

![Fleet: fighter craft over a volcanic canyon, powered by thurbox][banner]

[banner]: media/fleet-banner.jpg

# fleet

A **control plane** for your work across GitHub: one repo that holds the *map* of
your projects and the *orchestration* of AI agent sessions run against them,
using [thurbox](https://github.com/Thurbeen/thurbox). The defining constraint is
that the control plane holds the plan and the log, and never holds the workers'
branches — real work happens in thurbox worker sessions, each in its own git
worktree in a real repo. What accumulates here is playbooks (reusable recipes)
and run logs (what actually happened).

Click **Use this template** to make your own. It holds context and intent, not
code.

## Quickstart

From a fresh clone:

1. **Use this template**, then clone your new repo.
2. Edit `registry/owners.txt` — your GitHub username, plus any orgs you belong
   to, one per line. The sync refuses to run while the file has no active
   entries, rather than emit an empty map.
3. `./scripts/sync-registry.sh` — writes `registry/repos.generated.yaml` from
   your live `gh` session. Commit the result.
4. `./scripts/install-extension.sh` — renders `extension.toml` and installs the
   thurbox extension.
5. Open the `fleet` session in thurbox and give it a goal.

Requires `gh` (authenticated), `jq`, and `thurbox-cli` **2.19.0 or newer** —
`extension.toml.in` records why the floor sits there.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the gate (`./scripts/check.sh`),
the squash-only merge policy, and the layout conventions.

## Customizing

Three things are yours to change, in descending order of how likely you are to
want to:

1. **`registry/owners.txt`** — required, and covered in the Quickstart above. It
   is the only edit a fresh clone actually needs.
2. **The agent and model** — optional.
3. **The name `fleet`** — optional, and leaving it alone is a fine answer.

### The agent and model

`extension.toml.in`'s `[[agents]]` block pins the model:

```toml
[[agents]]
name = "fleet"
command = "claude"
args = ["--model", "claude-opus-4-8"]
```

Edit that block and re-run `./scripts/install-extension.sh`. `command` can point
at a different CLI entirely — `codex`, say — as long as the `resume_args`,
`fork_args`, and `new_session_args` beneath it still carry thurbox's `{id}`
token. That token is what makes the session resume by its own id instead of by
"the last session in this directory".

Editing `agents.toml` after install works too, and takes effect immediately: a
reinstall keeps existing entries, so your edit is not clobbered. But the
manifest is where the entry is *generated* from, and that makes it the durable
place. An `agents.toml` edit lives on one machine and does not survive
`extension uninstall`; a manifest edit is committed, and every install of your
control plane gets it.

### The name `fleet`

`fleet` names the **session**, not you and not your repo. Your control plane can
be called `mission-control` while the session it runs is still `fleet` — nothing
reads the repo name. Keeping `fleet` is a perfectly good default. Renaming is
optional, not a setup step you have overlooked.

If you do rename it, the name is not in one place. It is in six, and they move
together:

```text
extension.toml.in
  name = "fleet"                (1) the extension id — the argument to every
                                    `thurbox-cli extension ...` command, so it
                                    also appears in the three hints
                                    scripts/install-extension.sh prints on exit
                                    and in its own header comment
  [[agents]] name = "fleet"     (2) the agent id, registered in agents.toml
  [[sessions]] name  = "fleet"  (3) the session's name
  [[sessions]] agent = "fleet"      ... bound to the agent in (2)

derived, not edited
  ~/.config/thurbox/extensions/fleet/
                                (4) the extension home. The manifest has no
                                    `home` key, so thurbox derives it from (1)

prose that has to agree
  FLEET.md                      (5) the [[files]] payload, whose text opens
                                    "You are the **fleet** session"
  --to fleet / --for fleet      (6) the mailbox address workers send to and the
                                    lead drains. In FLEET.md, in this README,
                                    and in extension.toml.in's header comment
```

Only (4) is not a line you edit; it follows from (1). The rest are.

The failure mode is a **partial rename**. Change (1) and the extension installs
as `mission-control`; leave (6) and every worker brief still mails its result
`--to fleet`. Whether that send bounces or lands in an inbox nobody drains, the
lead never sees it — the worker did the work, the PR exists, and nothing
surfaces it. So:

1. Change all six.
2. If the extension is already installed under the old name, uninstall it first:
   `thurbox-cli extension uninstall fleet`. Skip this and you get two registered
   extensions and two self-healing sessions, each faithfully recreating itself.
3. `./scripts/install-extension.sh`
4. `thurbox-cli extension status <newname>` to confirm.

`FLEET.md`'s **filename** is a separate question. The manifest names it four
times — one `[[files]] path` and three `[[symlinks]] target`s — so renaming the
file means editing those four lines too. You do not have to: renaming the
session does not require renaming the file, and `FLEET.md` is a fine name for
the standing context of a session called anything.

**Worker** session names are not among the six and not coupled to them. They are
free-form strings that nothing resolves, and by the convention in
`orchestration/playbooks/` and `orchestration/runs/_TEMPLATE.md` each is an
imperative sentence describing the work — `Document the customization surface` —
carrying neither a `fleet` prefix nor a repo prefix. A rename never reaches
them. The same goes for this README's own prose: it is documentation, so it
follows a rename rather than driving one.

## Layout

```text
registry/
  owners.txt               The GitHub owners the map covers, one per line.
  repos.generated.yaml     Auto-synced index of every repo (owners → repos).
                           GENERATED — do not hand-edit.
  context/
    _TEMPLATE.md           Copy this to add a project.
    <repo>.md              Curated notes: purpose, relations, active goals.

orchestration/
  playbooks/
    _TEMPLATE.md           Copy this to add a reusable orchestration recipe.
    <name>.md              A repeatable way to run thurbox for a class of work.
  runs/
    _TEMPLATE.md           Copy this per orchestration run.
    <date>-<slug>.md       Log of one run: goal, sessions, outcomes.

scripts/
  check.sh                 The whole gate: shell, markdown, YAML, skills.
  install-extension.sh     Renders extension.toml, then installs it.
  sync-registry.sh         Regenerates repos.generated.yaml from the GitHub API.
  sync-checkout.sh         Fast-forwards main when that is unambiguously safe.
  trust-thurbox-dir.sh     Seeds Claude Code workspace trust for a worktree.
  lib/check_yaml.py        The YAML + registry-shape assertions check.sh runs.

.agents/skills/
  <name>/SKILL.md          Agent skills. ONE tree, agent-agnostic.
.claude/skills             A committed SYMLINK to .agents/skills.

media/
  fleet-banner.jpg         The banner at the top of this README.

.github/workflows/
  ci.yml                   PR checks feeding a single "All Checks" gate.
                           Every job runs scripts/check.sh.

AGENTS.md                  How an agent should operate inside this repo.
CLAUDE.md                  A two-line pointer that imports AGENTS.md.
FLEET.md                   Standing context for the `fleet` SESSION — what it
                           is for, as opposed to how to work in the checkout.
CONTRIBUTING.md            The gate, the squash-only policy, the conventions.
```

Skills live in `.agents/skills/` and `.claude/skills` is a symlink to it, so one
copy serves every CLI: Claude Code reads `.claude/skills`, and opencode
auto-discovers the same path. Mirroring the tree into `.opencode/skills` would
register the same skill twice — don't. `./scripts/check.sh skills` fails if the
link is not a symlink, which is what a clone with `core.symlinks=false` leaves
behind.

## The map

`registry/repos.generated.yaml` is a machine view — regenerated from GitHub, so
it never drifts. Everything a human (or an agent) actually needs to *understand*
a project lives in `registry/context/<repo>.md`, which the sync never touches.
That is where judgement goes: what a project is for, how it relates to the
others, what is parked and why.

The sync runs **locally**. It enumerates every repo you can reach using your own
`gh` session and keeps the ones owned by an owner in `registry/owners.txt`, so
there is no cloud PAT and no CI secret to manage. Refresh the map whenever you
like, then push:

```bash
./scripts/sync-registry.sh
git commit -am "chore(registry): sync" && git push
```

## Orchestration

The control plane drives [thurbox](https://github.com/Thurbeen/thurbox)
**directly** — it does not depend on any external orchestration skill.

### The run loop

1. **Clarify the goal.** Vague goals produce vague workers.
2. **Pick or write a playbook** in `orchestration/playbooks/`.
3. **Open a run log** from `orchestration/runs/_TEMPLATE.md`, named
   `<YYYY-MM-DD>-<slug>.md`.
4. **One worker session per unit of work.** Each targets a real repo and its own
   git worktree. Workers share no context with the lead and none with each
   other, so every prompt states the goal, the constraints, and what "done"
   looks like, from scratch.
5. **Record outcomes as they happen** — session name, repo, intent, PR. The run
   log is the source of truth for what happened, not your memory of it.
6. **Review the PRs**, then delete each session as it closes out.

Fast-forward a target repo's base branch *before* spawning a worker against it.
A stale local `main` is inherited by the new worktree: the worker does correct
work and its PR arrives conflicting.

### The mailbox convention

A worker finishes by mailing its result to the lead:

```bash
thurbox-cli message send --to '<lead>' --kind result --body '<PR url or NOT_APPLICABLE>'
```

and the lead drains the inbox exactly-once:

```bash
thurbox-cli message inbox --for '<lead>' --claim --json
```

Quote the address. A session name is an imperative sentence, spaces and all, so
an unquoted one is split by the shell and mails somewhere else.

This beats polling `gh pr list` on three counts. It is **exact** — the worker
names its own artifact instead of you inferring it from a PR list that may
contain someone else's. It is **immediate** — `message send` wakes the
recipient, so the lead never polls at all. And it can report **"not
applicable"**, which a PR poll can never distinguish from "still working": the
absence of a PR is not a signal. Because the payload travels through thurbox's
durable database rather than a tmux pane, it also survives scrollback, TUI
chrome, and line-wrapping — all of which make pane-scraping fragile.

See [`orchestration/playbooks/_TEMPLATE.md`](orchestration/playbooks/_TEMPLATE.md)
for the anatomy of a playbook, [`AGENTS.md`](AGENTS.md) for how an agent should
operate inside this repo, and
[`.agents/skills/thurbox-session/SKILL.md`](.agents/skills/thurbox-session/SKILL.md)
for the detailed driving surface: spawning, prompting, completion detection,
cleanup.

## Thurbox extension

The repo ships [`extension.toml.in`](extension.toml.in), so thurbox can keep the
control plane running as a first-class session:

```bash
./scripts/install-extension.sh
```

That registers exactly two things:

- **A `fleet` agent** in `agents.toml` — `claude` pinned to Opus, resuming and
  forking by thurbox's session id like the stock `claude` agent.
- **A long-lived `fleet` session**, whose standing context is
  [`FLEET.md`](FLEET.md) (symlinked to `CLAUDE.md` / `AGENTS.md` / `GEMINI.md`
  in the extension home). thurbox **self-heals** it: delete the session and it
  comes back. Because it is a real session, workers can mail their results to it
  with `thurbox-cli message send --to fleet`.

Payload files land in `~/.config/thurbox/extensions/fleet/`; the registry,
playbooks, and run logs stay in your checkout, where they are versioned.

### Why the manifest is a `.in` file

`[[sessions]] repo_path` must be an **absolute path** to your clone.

That used to be true for two reasons. The historical one is gone: older thurbox
did not expand `~` there — `ExtensionDef::resolved_for_home()` substituted the
`{home}` token but never called `expand_tilde`, so a leading tilde was taken
literally and the session landed in a directory named `~`.
[thurbox#782](https://github.com/Thurbeen/thurbox/pull/782) fixed that in
0.174.2, and `min_thurbox_version` is now 2.19.0, so no supported thurbox still
has the bug.

The permanent reason stands on its own: no token spells "my clone". `{home}` is
substituted,
but it resolves to the *extension home*, not your checkout — while the session
must open the checkout, because it needs `registry/` and `orchestration/` in
hand.

A template cannot hardcode a path that exists on one machine. So the manifest
ships as `extension.toml.in` with a `__REPO_PATH__` placeholder, and
`scripts/install-extension.sh` renders it to a gitignored `extension.toml`
carrying your clone's real path. Re-run the installer after moving the clone.

There are deliberately **no automations**. The one scheduled candidate — the
registry sync — commits and pushes to `main`, so it stays a manual
`./scripts/sync-registry.sh` run by a human who reads the diff.

```bash
thurbox-cli extension status fleet     # per-resource health
thurbox-cli extension deactivate fleet # the real off-switch
thurbox-cli extension uninstall fleet  # reverse the install
```

## License

MIT — see [LICENSE](LICENSE).
