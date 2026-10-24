# Native Fleet configuration (fleet.toml)

The native versioned TOML file selects a resource store, runtime and optional
VEGA workspace. It is separate from the JSON
[Task](../resources/task.md), [TaskConfig](../resources/task-config.md) and
[EnvProvider](../resources/env-provider.md) resources applied into that store.
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

## Planned `[forge]` section

Design, not implemented. The [ChangeRequest](../resources/change-request.md)
and [ForgeTask](../resources/forge-task.md) designs add one section. An
unknown section is refused today, so this example does not load.

```toml
[forge]
callTimeoutMs = 15000
closedGraceMs = 604800000     # one week; a ChangeRequest spec may override
noCheckGraceMs = 300000       # the checksPassed predicate's wait for a check
relayBots = false

[forge.hosts]
"github.com"         = { kind = "github", cli = "gh" }
"gitlab.com"         = { kind = "gitlab", cli = "glab" }
"gitlab.example.com" = { kind = "gitlab", cli = "glab" }

[forge.readyPolicies.checks]
checksPassed = true

[forge.readyPolicies.checks-and-bots]
checksPassed = true
botReviewsAddressed = true

[forge.readyPolicies.strict]
checksPassed = true
botReviewsAddressed = true
noUnresolvedThreads = true
```

| Field | Required/default | Meaning |
| --- | --- | --- |
| `forge.callTimeoutMs` | `15000` | Bound for each forge CLI call; the manager lease must outlast the port's reported worst case. |
| `forge.closedGraceMs` | `604800000` | How long a closed change stays watched before it settles. |
| `forge.noCheckGraceMs` | `300000` | How long `checksPassed` waits for a check to appear before treating a checkless head as passed. |
| `forge.relayBots` | `false` | Relay bot-authored notes to the worker. |
| `forge.hosts.<host>` | One per forge host | `kind` is `github`, `gitlab` or `fake`; `cli` is a PATH name or absolute path. A URL on a host not listed is refused at apply. |
| `forge.readyPolicies.<name>` | The three above are shipped | A set of predicates the [ForgeTask controller](../forge-task-controller/interface.md#ready-policies) defines; an unknown predicate is refused at load. |

Credentials stay with each CLI's own login. Fleet passes none to the forge and
stores none.
