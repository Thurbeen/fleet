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

CI runs that script, the pre-commit hooks run it, and `.no-mistakes.yaml` points
its `lint` command at it, so a green local run and a green pull request mean the
same thing. That matters more here than in most repos: **CI only fires on pull
requests** while routine control-plane changes go straight to `main`, so the
local run is the one doing the work.

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

Changes here are gated with
[no-mistakes](https://github.com/kunchenguid/no-mistakes) before they reach the
push target. `.no-mistakes.yaml` is committed and carries the reasoning for
every key it sets.

Its **gate-control** fields — `commands.*`, `agent`, `document.instructions`,
`review.path_instructions`, `ci.rerun_transient`, `no_ci`,
`disable_project_settings` — are read only from the trusted default branch. A
contributor therefore cannot weaken the gate that reviews their own change; an
edit to one of those fields takes effect once it has merged to `main`.

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
your live `gh` session. Never hand-edit it, and never commit it — it is
gitignored, along with `registry/owners.txt`. Human judgement about a project
goes in `registry/context/<repo>.md`, which the sync never touches and which is
also gitignored: this repo is public and holds the machinery, not the
operator's content.

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
reference in a workflow is a bug — and `rumdl`, pinned by version in
`RUMDL_VERSION` in `ci.yml`, tracked by a custom regex manager since Renovate
has no built-in manager for a version in an `env:` block.

## Prose

Hand-wrap markdown at 80 columns — `scripts/check.sh markdown` enforces it.

Each class of fact has exactly one owner, and `.no-mistakes.yaml`'s
`document.instructions` is the map. In short: `README.md` is the human-facing
guide, `AGENTS.md` the agent-facing operating guide for working *inside* this
repo, `FLEET.md` the standing context of the long-lived Mission Control
session, this file the contribution process and the configuration of external
tooling, and `.agents/skills/thurbox-session/SKILL.md` the working reference
for driving `thurbox-cli`. A script's or config file's own header owns how that
thing works.
Reduce a duplicate to a pointer rather than keeping two copies in step.
