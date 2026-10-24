# Agent rules

Fleet v2 runs Task resources through a Rust controller built from the
workspace in `crates/`.
Read [the architecture](docs/ARCHITECTURE.md), [CONTRIBUTING.md](CONTRIBUTING.md)
and [the contracts](crates/CONTRACTS.md) for changes in their areas. The
[Task controller guide](docs/task-controller/README.md) owns its resource,
state machine and interface documentation. Keep the architecture simple and
explicit.

- For an issue, first write an end-to-end regression and observe it fail for
  the intended reason. New functionality starts with executable acceptance.
- Never give estimates of time, effort, or completion.
- Prefer a clear diagram to a long explanation when one fits.
- Check versions in registries, release metadata and lockfiles; never guess.
- Publishing uses the installed `publish` skill, normal signed commit hooks,
  the full repository gate, and independent review of the exact candidate SHA.
  A local-only contribution must not push, merge, tag, release, or deploy.
- Work in an isolated worktree. Never change a running control-plane checkout,
  its records, credentials, services, hooks or sessions as a side effect.
- Split work to fit the available context. Preserve other workers' source and
  use your own build output and temporary directory outside shared `/tmp`.
- Never put private paths, host names, addresses, account identities or live
  records in public code, fixtures, commit messages or forge comments.

## Boundaries

`fleet-api` and `fleet-controllers` are pure. Adapters (the store, the
runtime) perform I/O through ports. `fleet-exec` is the only production
process spawner: bounded calls kill whole process trees on timeout or unwind.
The manager runs the Task controller and persists its plans under the lease.
The CLI parses, wires and prints.

`crates/architecture.toml` declares every crate's allowed dependencies,
status writers and effect boundaries. Add a new crate there in the same
change; keep the positive and negative architecture fixtures meaningful.
Never weaken a gate to make a change pass.

TaskController owns Task status, except `status.result`, which the worker
writes through `fleet result`. Read [worker policy](orchestration/queue/POLICY.md)
before reporting. Unknown observations never mean absence. Persist a
deterministic effect intent before running an external effect; recover a
missing receipt by observing its key rather than blindly repeating it.

## Repository tooling

`cargo xtask check` is the canonical gate, shared by CI, hooks and publish.
`cargo xtask check --list` lists its areas. Narrow checks are appropriate while
developing; run the full gate on the complete candidate before publication.
`cargo xtask release` is repository tooling, separate from the installed
`fleet` binary. Read its help before invoking a release subcommand.

Read `~/.agent-rules/OPINIONS.md` before picking tools, adding dependencies or
changing CI. Read `~/.agent-rules/VOICE.md` before publishing prose on behalf
of the operator. Do not invent these files if they are absent.

## Deployment safety

The new binary uses a fresh, versioned resource store. It does not read legacy
queue records or migrate live data. Installers only install a binary; they do
not restart managers or configure a thurbox extension. The operator chooses a
no-child cutover boundary and preserves the old deployment for recovery.
Keep `.github/release-readiness.toml` held until acceptance, and keep the
root `FLEET_MAJOR` at `2`: Fleet v1 checkouts read it to refuse moving onto
v2. Report platform evidence and missing evidence precisely.
