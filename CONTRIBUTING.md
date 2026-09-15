# Contributing

This is a **control plane**: markdown playbooks, a handful of shell scripts, a
generated registry, and agent skills. Everything below follows from that.

## The gate

One script is the whole gate:

```bash
./scripts/check.sh          # every check
./scripts/check.sh --fix    # the same, applying the fixes a check can apply
./scripts/check.sh shell    # just one check
```

CI runs that script, the pre-commit hooks run it, and `.publish.yaml` declares
it as this repository's whole gate, so a green local run and a green pull
request mean the same thing. That matters more here than in most repos: **CI
only fires on pull requests** while routine control-plane changes go straight to
`main`, so the local run is the one doing the work.

A missing tool fails the check rather than skipping it. The script's header
holds the full list of checks, the tools they need, and the full usage —
it is the one place that list lives.

### Pre-commit hooks

Optional, and run with [`prek`](https://github.com/j178/prek) rather than the
Python `pre-commit`:

```bash
prek install
```

`.pre-commit-config.yaml` holds one hook per check, each shelling out to
`scripts/check.sh` — so the hooks cannot drift from CI.

### The gate before the push

Changes here are published with the
[`publish`](https://github.com/LeTuR/publish) skill: it rebases, reviews the
whole branch against this repository's own rules, runs the gate, commits the
fixes, pushes, opens the pull request, watches CI, and writes an attestation
naming the commit it ran on.

`.publish.yaml` is committed and is how this repository declares all of that —
the gate command above, the base branch, and the documents the review reads.
Its own comments carry the reasoning for every key it sets.

**The declaration is ordinary branch content.** The tool this replaced read its
gate-control fields only from the trusted default branch, so a contributor
could not weaken the rules that reviewed their own change. Nothing reproduces
that here: a change to `.publish.yaml`, to the rules below, or to
`scripts/check.sh` takes effect on the branch that makes it, and the only thing
standing between a weakened rule and a merge is that weakening it shows up in
the diff. Review it as such.

## Review rules, by path

House rules a reviewer reading only the diff would not know. Each heading is
the path glob its rules apply to: **read the ones the change touches**, and
skip the rest. They were scoped by glob mechanically until the gate that did
that was removed; the skill's `review.rules` is a flat list of files, so the
scoping is these headings and this sentence.

Nothing here is excluded from review any more, either.
`registry/repos.generated.yaml` and `media/**` used to be skipped as files
carrying no reviewable intent — the sync script is the reviewable artifact, its
output is not, and the banner artwork is a raster with no reviewable diff. The
rules below still say so; what is gone is the mechanism that acted on it, so
expect both to be read.

### `scripts/**`

Shell run by humans and by CI, linted by shellcheck under `scripts/check.sh`.
Two constraints this repo has already paid for:

A script here must work inside a LINKED GIT WORKTREE, where `.git` is a file
rather than a directory. Every thurbox worker runs in one, and a tool that walks
a directory tree looking for a git repo can silently find nothing there — which
reads as a pass. Prefer `git ls-files` and explicit paths over letting a linter
discover its own inputs.

`scripts/sync-checkout.sh` runs from a `SessionStart` hook and must always exit
0: a sync problem must never block a session from starting. It only ever
fast-forwards, and never rebases or resets.

Comments explain why, never what. A stale comment is worse than none.

### `.agents/skills/**`

An agent skill: the working reference a coding agent loads when it is about to
drive thurbox. Judge it as instructions rather than as prose — every command
must be one that the installed `thurbox-cli` actually accepts, with the flags
spelled as that binary spells them, because an agent will run what this file
says without checking.

A claim about what a CLI does not expose is the expensive kind of mistake here:
it sends an agent down a fallback path forever. Check it against `thurbox-cli
<cmd> --help`, which is version-matched to the installed binary, before
asserting an absence.

`.claude/skills` is a symlink to this tree. Do not add a parallel copy under
`.claude/`, and do not mirror into `.opencode/skills` — opencode auto-discovers
`.claude/skills`, so the symlink already serves it and a mirror would register
the same skill twice.

### `orchestration/**`

Playbooks are recipes an operator follows; run logs are history. A playbook
states its inputs, how the goal decomposes into sessions, and the acceptance
signal for each. It does not restate a target repo's own conventions — it
points at them.

Do not request test coverage for either.

`orchestration/session-profiles.yaml` is neither: it is configuration that
renders into `thurbox-cli session create` flags, so judge it the way you would
judge a command line. It is committed and this repo is public, so a credential
in it is a finding regardless of how it is spelled. Two further rules are
machine-enforced by `./scripts/check.sh profiles` — a `THURBOX_*` key (thurbox's
identity vars always win over `--env`, so it would look applied and do nothing)
and a `command` without a `reports_as` (thurbox reads hook coverage against the
command's file stem, so an undeclared session reports nothing and renders as
`uncovered` while it works). If a diff weakens either assertion in
`scripts/lib/session_profiles.py`, that is the finding, not the profile that
would then pass.

### `registry/**`

`registry/repos.generated.yaml` is generated by `scripts/sync-registry.sh` and
must never be hand-edited; a diff that edits it directly is a finding regardless
of whether the content is correct. `registry/context/<repo>.md` is the opposite:
human-owned judgement the sync never touches.

### `extension.toml.in`

The thurbox extension manifest, rendered to a gitignored `extension.toml` by
`scripts/install-extension.sh`. `__REPO_PATH__` is load-bearing — no thurbox
token spells "my clone" (`{home}` is the extension home), so it cannot be
simplified back to a `~` path.

`min_thurbox_version` is a claim about the whole range this manifest supports,
not a note about the version someone happens to run. Raising it is only correct
alongside the reason, and lowering it means every behavior the repo's prose
relies on must hold at the new floor too.

### `.github/workflows/**`

CI fires only on pull requests. Every job feeds the single required "All Checks"
gate, so adding or removing a job never needs a branch ruleset change — but a
new job must be listed in that gate's `needs:`, or it can fail while the gate
reports green.

Actions are pinned by commit SHA with the version in a trailing comment, and
Renovate keeps them current. A tag reference is a finding.

The jobs run `scripts/check.sh` rather than inlining their checks, so that the
local gate and the pull-request gate cannot drift apart. The Windows job is the
exception, because it has no bash: it runs `uv run fleet check
--platform-ported`, which calls the same modules for the checks already ported
to Python, and every job gets a `timeout-minutes` (`scripts/check.sh workflow`
holds that and the `needs:` rule above).

## Merging: squash only

**Squash is the only merge method this repository allows.** Merge commits and
rebase merging are both disabled on the remote, and the branch is deleted on
merge.

The consequence:

```text
docs: name worker sessions with an imperative sentence
  ↓ squash merge
docs: name worker sessions with an imperative sentence (#12)
```

The commit that reaches `main` is not any commit from your branch: GitHub builds
it from the **pull request title** plus its own `(#N)` suffix, and takes its
body from the pull request body. So:

- Title the pull request the way you would write the commit. This repository's
  history is conventional and scopeless — `feat:`, `docs:`, `chore:`, `fix:`.
- Keep a pull request to one purpose, and title it after its most significant
  change.
- Anything that has to survive into the history goes in the pull request
  **body**, not in a branch commit's footer. The squash discards those.

Branch commits are a working record. `main` is a straight line of one commit per
pull request.

## Layout conventions

### Skills live in `.agents/`

`.agents/skills/<name>/SKILL.md` holds the real files. `.claude/skills` is a
**symlink** to that directory, committed as a symlink (git mode `120000`).

One tree, every CLI: Claude Code reads `.claude/skills` and opencode
auto-discovers the same path, so the symlink already serves both. Do not mirror
the tree into `.opencode/skills`, which would register the same skill twice, and
do not add a second copy under `.claude/`.

`scripts/check.sh skills` guards both failure modes: it asserts the link is a
symlink pointing at `../.agents/skills` (a clone made with `core.symlinks=false`
materialises it as a text file holding its target instead), that it resolves,
and that every skill directory has a `SKILL.md`.

### What is tracked, and what is not

Read `.gitignore`'s header before adding a path; it owns this split and the
reason for every entry. **The machinery is tracked; what a running fleet
writes is not.** This repo is public, and the queue, the map and the run logs
are the operator's own working state, which has no business in it.

Adding a file means deciding which side it is on, and saying so:

| If it is… | Then | Example |
|---|---|---|
| machinery — scripts, skills, playbooks, prose | tracked | `scripts/`, `playbooks/ship-feature.md` |
| generated from a live source, or rendered from a tracked one | ignored | `repos.generated.yaml`, `FLEET.rendered.md` |
| true on one machine | ignored | `extension.toml`, `orchestration/reconcile/` |
| the operator's own working state | ignored | `runs/<date>-<slug>.md`, `queue/<topic>/`, `voice.conf` |

Prefer a whole ignored **directory** with a `!` negation for the one tracked
form it contains, the way `registry/context/`, `orchestration/runs/` and
`orchestration/queue/` each do. A per-file negation list has to be extended
every time another file of that kind is added, and the day someone forgets,
that file lands ignored and nobody sees it.

### The generated registry

`registry/repos.generated.yaml` is written by `./scripts/sync-registry.sh` from
every `gh` login on your machine, not just the active one. Never hand-edit it,
and never commit it — it is gitignored, along with `registry/owners.txt`. Human
judgement about a project goes in `registry/context/<repo>.md`, which the sync
never touches and which is also gitignored: this repo is public and holds the
machinery, not the operator's content.

### The session profiles

`orchestration/session-profiles.yaml` holds the settings a worker session
starts under, and `./scripts/session-flags.sh` renders one profile into
`thurbox-cli session create` flags. **One file, one layer**, committed so a
change to it is reviewed in a diff — which is the point.

Every profile is held to the two enforced rules below. **No-secrets is not one
of them** — the gate does not read YAML for secrets and does not claim to. It
is a convention: this repo is public, so nothing environment-specific belongs
in the file, and a real credential is better off never in a file at all — a
worker inherits the environment of the thurbox server that spawns it, so a
credential belongs wherever that process gets its own.

`scripts/check.sh profiles` enforces the two rules a reviewer should not have to
catch by eye — and only those two. A `THURBOX_*`
key is refused, because thurbox's own identity variables always win over
`--env` and such a setting would look applied while doing nothing. And
`command` without `reports_as` is refused, because thurbox reads hook coverage
against the command rather than the agent in the pane, and an undeclared
session reports nothing and renders as `uncovered` while it works.

### The extension manifest

`extension.toml` is rendered from `extension.toml.in` by
`./scripts/install-extension.sh` and is gitignored — it carries your clone's
absolute path. Edit the `.in` file and re-run the installer.

The same run renders `FLEET.rendered.md`, the payload the manifest ships, from
the tracked `FLEET.md` — substituting the two names in
`orchestration/voice.example.conf` (or the gitignored `voice.conf` beside it).
Edit `FLEET.md`, never the rendered copy. Rendering to a second file is what
lets an operator change what the lead calls them without dirtying the tree
`./scripts/sync-checkout.sh` has to fast-forward.

`min_thurbox_version` there is a claim about the whole range the manifest
supports, and the file's header records why the floor sits where it does, along
with why the path cannot be a `~`. Raise it only alongside the reason.

## Dependencies

Two dependency surfaces, both pinned and both kept current by
[Renovate](https://renovatebot.com) (`renovate.json`): the GitHub Actions used
by CI, pinned by commit SHA with the version in a trailing comment — a tag
reference in a workflow is a bug — and the Python dependencies in
`pyproject.toml` and the committed `uv.lock`: PyYAML, the `uv_build` build
backend, and the gate tools `rumdl` and `ruff` in the `dev` group, which CI
runs through `uv run` on both runners so they share one pin. Renovate's default
PEP 621 manager picks all of them up with no custom rule.

## Prose

Hand-wrap markdown at 80 columns — `scripts/check.sh markdown` enforces it.

### Documentation ownership

This repo carries four overlapping prose files by design, and the failure mode
is one fact restated in all four and updated in one. **Every fact has exactly
one owner; reduce a duplicate to a pointer rather than synchronizing it.** This
section is the map, and `.publish.yaml` names this file so a review reads it.

`README.md` is the human-facing guide: what the control plane is, the
quickstart, the customization surface, and the layout. It owns the setup story.

`AGENTS.md` is the agent-facing operating guide for working INSIDE this repo —
what the trees are, the run loop, and the local gates. It is an index into the
other owners, not a second copy of them. `CLAUDE.md` is a two-line pointer that
imports it and owns nothing; never write content there.

`FLEET.md` is the standing context of the long-lived Mission Control session,
laid down at the extension home by `extension.toml.in`. It owns what that
session is FOR. It deliberately defers to `AGENTS.md` for how to work in the
repo.

`CONTRIBUTING.md` — this file — owns the contribution process: the
branch-and-pull-request flow, the squash-only merge policy, the local gate, the
review rules above, and the configuration of external tooling such as
`.publish.yaml`, `.pre-commit-config.yaml`, `.rumdl.toml` and `renovate.json`. A
root-level config file's rationale belongs here, not in `README.md`.

`.agents/skills/thurbox-session/SKILL.md` is the working reference for driving
`thurbox-cli`: spawning, prompting, completion detection, cleanup. Detail an
agent needs only while launching a worker belongs there rather than in
`AGENTS.md`. It is a reference, not an owner — the rationale still lives in the
documents above. `.claude/skills` is a symlink to `.agents/skills`, so the skill
has exactly one copy; never write a second one under `.claude/`.

`scripts/queue.sh`'s header owns the task queue: the layout, the ordering rule,
and why completion is a stream plus a file rather than a message.
`orchestration/queue/README.md` owns the on-disk record shape, and
`.agents/skills/fleet-queue/SKILL.md` is the working reference for driving it —
a reference, not an owner. `scripts/lib/session_trust.py` owns the
trust-dialog mechanics and the per-agent table, and
`scripts/trust-thurbox-dir.sh`'s owns the config-seeding fallback. `README.md`
owns the human-facing version of all of it. Point at one of those rather than
restating the doctrine in a fifth place.

`orchestration/playbooks/<name>.md` owns a repeatable recipe for a class of
work; `orchestration/runs/<date>-<slug>.md` owns what happened in one run and is
append-only history, never edited to match a later decision.
`registry/context/<repo>.md` owns the human truth about one project.
`orchestration/session-profiles.yaml` owns the settings a worker session starts
under, and its own header comment owns the schema and the three rules;
`README.md` owns it as a customization surface and this file the rationale for
the gate check over it. Point at one of those rather than restating a rule in a
fourth place.

This repository has no `CHANGELOG.md` and does not need one. Do not add a new
documentation file to close a perceived gap when an owner above already covers
the subject.
