# Contributing

This is a **control plane**, not a code base: markdown playbooks, a handful of
shell scripts, a generated registry, and agent skills. Everything below
follows from that.

## The gate

One script is the whole gate:

```bash
./scripts/check.sh          # shellcheck, markdown, YAML, session profiles, skills
./scripts/check.sh --fix    # the same, applying the fixes a check can apply
./scripts/check.sh shell    # just one check
```

CI runs that script, the pre-commit hooks run it, and `.no-mistakes.yaml` points
its `lint` command at it. One definition means a green local run and a green
pull request mean the same thing — which matters more here than in most repos,
because **CI only fires on pull requests** while routine control-plane changes
go straight to `main`. The local run is the one doing the work.

It needs `shellcheck`, `rumdl` and `python3` with PyYAML. A missing tool fails
the check rather than skipping it: a gate that passes silently when its linter
is absent is worse than no gate.

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
`disable_project_settings` — are read only from the trusted default branch,
never from a pushed branch. A contributor therefore cannot weaken the gate that
reviews their own change; an edit to one of those fields takes effect once it
has merged to `main`.

## Merging: squash only

**Squash is the only merge method this repository allows.** Merge commits and
rebase merging are both disabled on the remote, and the branch is deleted on
merge.

The consequence worth internalising:

```text
docs: name worker sessions with an imperative sentence
  ↓ squash merge
docs: name worker sessions with an imperative sentence (#12)
```

The commit that reaches `main` is **not** any commit from your branch. GitHub
builds it from the **pull request title** plus its own `(#N)` suffix, and takes
its body from the pull request body. So:

- Title the pull request the way you would write the commit. This repository's
  history is conventional and scopeless — `feat:`, `docs:`, `chore:`, `fix:`.
- Keep a pull request to one purpose, and title it after its most significant
  change.
- Anything that has to survive into the history goes in the pull request
  **body**, not in a branch commit's footer. The squash discards those.

Branch commits themselves are cheap: they are a working record, not the
artifact. `main` is a straight line of one commit per pull request.

> This deviates from the standing "allow rebase merging, disable merge commits"
> preference in favour of squash, deliberately: it is what
> [thurbox](https://github.com/Thurbeen/thurbox) does, and it satisfies the same
> underlying rule that `main` carries no merge commits.

## Layout conventions

### Skills live in `.agents/`

`.agents/skills/<name>/SKILL.md` holds the real files. `.claude/skills` is a
**symlink** to that directory, committed as a symlink (git mode `120000`).

One tree, every CLI: Claude Code reads `.claude/skills`, and opencode
auto-discovers `.claude/skills` too — so the symlink already serves it. Do not
mirror the tree into `.opencode/skills`, which would register the same skill
twice, and do not add a second copy under `.claude/`.

`scripts/check.sh skills` guards both failure modes: it asserts the link is a
symlink pointing at `../.agents/skills` (a clone made with `core.symlinks=false`
materialises it as a text file holding its target instead), that it resolves,
and that every skill directory has a `SKILL.md`.

### What is tracked, and what is not

This is the convention every other one now hangs off, so read `.gitignore`'s
header before adding a path. **What the template ships is tracked; what a
running fleet writes is not.** An instance's tracked tree therefore stays
identical to the template's, which is the only reason
`./scripts/update-from-template.sh` can be a clean fast-forward instead of a
merge that conflicts on somebody's tuned playbook.

Adding a file to the template means deciding which side it is on, and saying so:

| If it is… | Then | Example |
|---|---|---|
| shipped by the template, same for everyone | tracked | `scripts/`, `playbooks/ship-feature.md` |
| generated from a live source | ignored | `repos.generated.yaml` |
| a record of what one instance did | ignored | `runs/<date>-<slug>.md` |
| authored by one operator, for themselves | ignored, in `local/` or `*.local.yaml` | `playbooks/local/`, `session-profiles.local.yaml` |

Prefer a whole ignored **directory** over a per-file negation. A negation list
has to be extended every time the template ships another file of that kind, and
the day someone forgets, that file lands ignored in every instance and nobody
sees it. `orchestration/playbooks/local/` is one rule that never needs touching;
the alternative would have been a `!` line per shipped playbook.

Never make the template stop tracking a file without knowing what that does to
an instance that has one. A plain merge sees "you changed it, they deleted it"
and conflicts. `update-from-template.sh` handles it — a path deleted upstream,
tracked locally, and claimed by the incoming `.gitignore` is untracked with
`git rm --cached` first, which leaves the file on disk, and the handover is
reported — but that is the reason the change is safe to make, not a licence to
skip thinking about it.

### The generated registry

`registry/repos.generated.yaml` is written by `./scripts/sync-registry.sh` from
your live `gh` session. Never hand-edit it, and never commit it — it is
gitignored, along with `registry/owners.txt`. Human judgement about a project
goes in `registry/context/<repo>.md`, which the sync never touches and which is
also gitignored: this repo distributes the template's shape, it does not back up
an instance's content.

### The session profiles

`orchestration/session-profiles.yaml` holds the settings a worker session
starts under, and `./scripts/session-flags.sh` renders one profile into
`thurbox-cli session create` flags. It is the **template's** file, committed so
it is reviewed — which is the point, and also the convention: **template
defaults only**. An instance tunes profiles in `orchestration/session-profiles.local.yaml`
instead — copied from the tracked `.local.example.yaml`, gitignored, and layered
over the defaults — so tuning costs nobody their fast-forward. A profile named
there replaces the shipped one of that name wholesale, and the renderer says on
stderr which profiles it is shadowing, so precedence is never silent.

Both layers are held to the two enforced rules below, and the tracked example is
validated too so it cannot rot. **No-secrets is not one of them.** The gate does
not read YAML for secrets and does not claim to — the profiles file used to say
all three rules were enforced, which was a guarantee the code never gave, and
that is the defect class worth avoiding everywhere in this repo. It is a
convention with an obvious home instead: the tracked file holds template
defaults because it is public, the gitignored local file is where a value you
would not commit goes, and nothing has to force the point because an instance
has no tracked changes of its own to push. A real credential is better off never
in a file at all — a worker inherits the environment of the thurbox server that
spawns it, so a credential belongs wherever that process gets its own.

`scripts/check.sh profiles` enforces, across both layers, the two rules a
reviewer should not have to catch by eye — and only those two. A `THURBOX_*`
key is refused, because thurbox's own identity variables always win over
`--env` and such a setting would look applied while doing nothing. And
`command` without `reports_as` is refused, because thurbox reads hook coverage
against the command rather than the agent in the pane, and an undeclared
session reports nothing and renders as `uncovered` while it works.

### The extension manifest

`extension.toml` is rendered from `extension.toml.in` by
`./scripts/install-extension.sh` and is gitignored — it carries your clone's
absolute path. Edit the `.in` file and re-run the installer.

`min_thurbox_version` there is a claim about the whole range the manifest
supports, and the file records why the floor sits where it does. Raising it is
only correct alongside the reason.

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
repo, `FLEET.md` the standing context of the long-lived `fleet` session, this
file the contribution process and the configuration of external tooling, and
`.agents/skills/thurbox-session/SKILL.md` the working reference for driving
`thurbox-cli`. Reduce a duplicate to a pointer rather than keeping two copies in
step.
