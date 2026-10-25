# Native Fleet configuration (fleet.toml)

The native versioned TOML file selects a resource store, runtime, optional
VEGA workspace and the forges ChangeRequests are read from. It is separate
from the JSON [Task](../resources/task.md),
[TaskConfig](../resources/task-config.md),
[EnvProvider](../resources/env-provider.md) and
[ChangeRequest](../resources/change-request.md) resources applied into that
store.
`fleet apply` does not load TOML, and `--config` does not apply resources.
There is no `[taskConfig]`, `[envProvider]` or task-template section in TOML.

## File selection and paths

Selection order is `--config PATH`, then `FLEET_CONFIG`, then one native path:

| Platform | Native configuration path |
| --- | --- |
| Linux | `$XDG_CONFIG_HOME/fleet/config.toml`, falling back to `~/.config/fleet/config.toml`. |
| Windows | `%APPDATA%\fleet\config.toml`. |

Fleet does not search the current directory or parents. Explicit config paths
must exist, except `fleet init --config PATH`, which creates an isolated
instance without replacing an existing config. `fleet init` creates the
native config and bundled workspace; see
[initialization/customization](../../README.md#configuration-and-customization)
for data roots, managed files, conflict handling and per-instance paths.

Relative `data.dir`, `runtime.dir` and `starter.workspace` resolve beside the
TOML file. A runtime executable should be a PATH name or absolute path;
`runtime.cli` is passed to process execution without rebasing it beside TOML.
Provider source paths are always absolute and manager-local, even for remote
Tasks; they never inherit TOML's relative-path rule.

## Real-runtime example

```toml
version = 1

[data]
dir = "store"

[runtime]
kind = "thurbox"
cli = "thurbox-cli"
callTimeoutMs = 15000
keyPrefix = "fleet"

[runtime.modelEnv]
claude-code = "ANTHROPIC_MODEL"

[starter]
workspace = "vega"
```

Set `starter.workspace` to an initialized VEGA workspace when using `fleet up`.
For ordinary `apply`/`reconcile`, the starter section is optional. A second
Fleet installation talking to the same Thurbox hosts needs a distinct
`keyPrefix`. Store, workspace and config must remain separate paths.

## Fields

| Field | Required/default | Meaning |
| --- | --- | --- |
| `version` | Required, `1` | Native config schema; unsupported versions and unknown fields are refused. |
| `data.dir` | Optional | Resource store. `--data-dir` overrides it; otherwise Fleet uses its native data directory. |
| `runtime.kind` | Required when configuring runtime | `thurbox` or `fake`; needed for reconciliation/session operations. |
| `runtime.cli` | Thurbox: PATH `thurbox-cli` (`thurbox-cli.exe` on Windows) | Bounded Thurbox CLI executable. |
| `runtime.callTimeoutMs` | Thurbox: `15000` | Bound for each Thurbox call; manager lease must outlast the runtime's reported worst case. |
| `runtime.keyPrefix` | Thurbox: `fleet` | Session identity prefix; at most 32 lowercase letters/digits/`-`, starting/ending with letter/digit. |
| `runtime.modelEnv` | Thurbox: Claude mappings below | Harness name to supported model environment-variable name. |
| `runtime.dir` | Required for `fake` | Isolated fake-host persistence, relative to this config file. |
| `starter.workspace` | Required for `up` | Initialized VEGA workspace, relative to this config file. |

`--data-dir` is for explicit store operations. `fleet up` rejects a differing
override because its workers must share the store selected by the persisted
config. Persist `data.dir` before using `up` with a different store. All
resource commands in one workflow should use the same `--config` path.

`runtime.modelEnv` extends/overrides the built-in `claude` and `claude-code`
entries, both `ANTHROPIC_MODEL`. An explicit/inherited Task model requires a
mapping for its selected harness; Fleet does not invent a Codex variable.
Choose the variable supported by the installed harness. Model selection wins
over the same environment key from providers or Task overrides. For configured
Tasks, both variable selection and value freeze with the private launch
snapshot; later TOML mapping changes do not alter that execution.

Runtime-specific fields apply only to that runtime. Keep the store and native
configuration user-private; they contain literal manifests and private launch
snapshots. Windows snapshots inherit the store's ACL, while Unix launch
storage enforces owner-only permissions. See
[privacy and retention](../resources/task-config.md#resolution-and-freezing).

## Offline fake-runtime example

```toml
version = 1

[data]
dir = "data"

[runtime]
kind = "fake"
dir = "fake-host"
```

This is the executable [example config](../../examples/fleet.toml). Fake
sessions run no agent, although explicitly configured external EnvProvider
commands still execute locally. Do not treat the fake runtime as a sandbox
for untrusted helpers.

```sh
fleet --config examples/fleet.toml apply -f examples/resources.json
fleet --config examples/fleet.toml list tasks --json
fleet --config examples/fleet.toml reconcile
```

For the new resources, save the manifests in their individual references,
then use the same explicit config for each operation:

```sh
fleet --config fleet.toml apply -f env-provider.json
fleet --config fleet.toml apply -f task-config.json
fleet --config fleet.toml apply -f task.json
fleet --config fleet.toml reconcile
```

Apply uses JSON resources. File/command provider output uses a third format:
[environment JSON maps](environment-json.md). Keep source credentials out of
resource manifests. Config schema `1`, resource API `fleet/v1alpha1` and store
format `1` are separate version contracts; `fleet version --json` reports
them. No live store/config migration or service restart occurs through this
feature. `fleet init --update` preserves the user-owned native config.

## The `[forge]` section

The forges that [ChangeRequests](../resources/change-request.md) are
mirrored from. Without this section a ChangeRequest is refused at apply, and
one already stored is reported by the manager and never polled.

```toml
[forge]
callTimeoutMs = 15000
closedGraceMs = 604800000     # one week; a ChangeRequest spec may override
settledTtlMs = 2592000000     # thirty days; a ChangeRequest spec may override
relayNotes = true             # defaults a Task's spec.forge.relay may override
relayChecks = true
relayBots = false
noCheckGraceMs = 300000       # the checksPassed predicate's wait for a check
draftPolicy = "checks"        # omit to never take a draft out of draft

[forge.hosts]
"github.com"         = { kind = "github", cli = "gh" }
"gitlab.com"         = { kind = "gitlab", cli = "glab" }
"gitlab.example.com" = { kind = "gitlab", cli = "glab" }

[forge.readyPolicies.checks]
checksPassed = true

[forge.readyPolicies.checks-and-bots]
botReviewsAddressed = true
checksPassed = true

[forge.readyPolicies.strict]
botReviewsAddressed = true
checksPassed = true
noUnresolvedThreads = true
```

| Field | Required/default | Meaning |
| --- | --- | --- |
| `forge.callTimeoutMs` | `15000` | Bound for each forge CLI call. One observation of a change may make up to six such calls, and the manager lease is raised to outlast the port's reported worst case. |
| `forge.closedGraceMs` | `604800000` | How long a closed change stays watched before it settles. |
| `forge.settledTtlMs` | `2592000000` | How long a settled ChangeRequest is retained before its controller deletes it. |
| `forge.relayNotes`, `forge.relayChecks` | `true` | Whether the [Forge controller](../forge-controller/README.md) relays new notes, and passed or failed checks, to a Task's worker. A Task's `spec.forge.relay` overrides each. |
| `forge.relayBots` | `false` | Whether bot-authored notes are relayed to the worker. A selected policy that waits on bots relays their blockers regardless. |
| `forge.noCheckGraceMs` | `300000` | How long `checksPassed` waits, from a head's first observation, for a check to appear before treating that checkless head as passed. |
| `forge.draftPolicy` | Absent | The ready policy applied to a Task that sets none; it must name a policy below. Absent: a draft is never taken out of draft unless the Task names a policy. |
| `forge.readyPolicies.<name>` | Three written by `fleet init` | A set of the predicates the [Forge controller](../forge-controller/interface.md#ready-policies) defines (`checksPassed`, `botReviewsAddressed`, `noUnresolvedThreads`, `forgeMergeable`; `workerDone` and `workerIdle` are always on), under a name that is only a key. Any other key is refused at load. |
| `forge.hosts.<host>` | One per forge host | `kind` is `github`, `gitlab` or `fake`; `cli` is a PATH name or absolute path (`gh` or `glab` by default); a `fake` host takes `dir`, its directory, relative to this file. A URL on a host not listed is refused at apply. |

Credentials stay with each CLI's own login. Fleet passes none to the forge and
stores none. `fleet init` writes the three `readyPolicies` tables above into
the generated config and no `draftPolicy`: a Task is marked ready only once
it, or the operator's config, names a policy. The shipped
[example config](../../examples/fleet.toml) adds a fake host,
`forge.example`, with `draftPolicy = "checks"` and the same three policies,
so a declared draft can be taken out of draft offline.
