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
"dev.azure.com"      = { kind = "azure-devops", cli = "az" }
"bitbucket.org"      = { kind = "bitbucket-cloud", cli = "curl" }

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
| `forge.relayNotes`, `forge.relayChecks` | `true` | Whether the [Forge controller](../controllers/forge/README.md) relays new notes, and passed or failed checks, to a Task's worker. A Task's `spec.forge.relay` overrides each. |
| `forge.relayBots` | `false` | Whether bot-authored notes are relayed to the worker. A selected policy that waits on bots relays their blockers regardless. |
| `forge.noCheckGraceMs` | `300000` | How long `checksPassed` waits, from a head's first observation, for a check to appear before treating that checkless head as passed. |
| `forge.draftPolicy` | Absent | The ready policy applied to a Task that sets none; it must name a policy below. Absent: a draft is never taken out of draft unless the Task names a policy. |
| `forge.readyPolicies.<name>` | Three written by `fleet init` | A set of the predicates the [Forge controller](../controllers/forge/interface.md#ready-policies) defines (`checksPassed`, `botReviewsAddressed`, `noUnresolvedThreads`, `forgeMergeable`; `workerDone` and `workerIdle` are always on), under a name that is only a key. Any other key is refused at load. |
| `forge.hosts.<host>` | One per forge host | `kind` is `azure-devops`, `bitbucket-cloud`, `github`, `gitlab` or `fake`; `cli` is a PATH name or absolute path (`az`, `curl`, `gh` or `glab` by default); a `fake` host takes `dir`, its directory, relative to this file. Configure `dev.azure.com` once for hosted Azure DevOps Services; its `{organization}.visualstudio.com` aliases route to that adapter. `bitbucket-cloud` is accepted only for `bitbucket.org`; Server/Data Center is a different API. A URL on any other unlisted host is refused at apply. |

Credentials stay with each external client's own configuration. Fleet passes
none to the forge and stores none. Azure DevOps login is resolved separately
for each organization; Fleet never reads or writes a PAT, token, subscription
or CLI default.
Bitbucket Cloud has no official CLI boundary here: Fleet runs bounded HTTPS
GETs through `curl --netrc-optional` and relies on curl's externally managed
config, netrc or credential helper. Fleet never constructs an Authorization
header, username, password or token. The client must be able to read
`/2.0/user`, pull requests, comments, pull-request commit statuses and
mergeability checks. Draft observation is supported, but mark-ready fails
closed because Bitbucket's `PUT draft=false` has no atomic head-and-state
precondition.
`fleet init` writes the three `readyPolicies` tables above into
the generated config and no `draftPolicy`: a Task is marked ready only once
it, or the operator's config, names a policy. The shipped
[example config](../../examples/fleet.toml) adds a fake host,
`forge.example`, with `draftPolicy = "checks"` and the same three policies,
so a declared draft can be taken out of draft offline.

## `[plugins]` section

The [Plugin controller](../controllers/plugin/README.md) registers each
plugin here, operator-owned.

```toml
[plugins.github-issues]
program = "/usr/local/bin/node"
args = ["/opt/fleet-plugins/github-issues/dist/plugin.js"]
cwd = "/opt/fleet-plugins/github-issues"
callTimeoutMs = 15000
tasks = "own"                 # or "any"
passEnv = ["GH_TOKEN"]
envFrom = [{ name = "github-issues-env" }]
```

| Field | Required/default | Meaning |
| --- | --- | --- |
| `plugins.<name>` | One per plugin | The key is the plugin's name; the manifest's `name` must equal it. |
| `program` | yes | Absolute path of the program on the manager, as for an EnvProvider command source: no PATH search, no shell, no rebasing beside TOML. |
| `args` | `[]` | Arguments before the call name the host appends. |
| `cwd` | yes | Absolute working directory on the manager. |
| `callTimeoutMs` | `15000` | Bound for each plugin process; the tree is killed past it, and the manager lease is raised to outlast the port's reported worst case. |
| `tasks` | `"own"` | Which Tasks the plugin may patch, delete, send to or declare on: `own`, those carrying its `fleet.dev/plugin` label, or `any`. Creating a Task needs no permission beyond this entry. |
| `passEnv` | `[]` | Variables copied from the manager's environment into the plugin's observer calls only. |
| `envFrom` | `[]` | EnvProviders whose merged map reaches the observer calls only, resolved per sweep through the provider machinery TaskConfig uses; a provider's command source runs under the same bounds and lease rule. |

The controller calls, `manifest`, `probes` and `reconcile`, receive none of
these variables. Values never reach the store, a status or argv. Programs run
on the manager with the manager's user; register only programs you intend
Fleet to execute, as for EnvProvider command sources.

## The `[gateway]` section

The installed binary reads and validates this section, and opens its
accounts before a manager run, but the
[Gateway controller](../controllers/gateway/README.md) that acts on it is a
design, not implemented. It reads its chat accounts, people and routes from
here, as the Forge controller reads `[forge.hosts]`.

```toml
[gateway]
callTimeoutMs = 15000
pollMs = 300000               # history backstop for tied threads
activeMs = 86400000           # ... while the thread had a message this recently
retainMs = 2592000000         # thirty days: an Untied or Orphaned Conversation's retention
beatMs = 60000                # a listener beats at least this often; stale after three
questions = "eng"             # the route whose channel takes Questions from untied Tasks

[gateway.accounts.slack-main]
platform = "slack"
team = "T0123ABCD"            # the team, or the enterprise id in an Enterprise org
bot = "U0BOT0001"             # Fleet's bot user id, checked against the platform's answer
inbound = "socket-mode"       # how the listener receives and authenticates deliveries
cli = "curl"
curlConfig = "/etc/fleet/slack-curl.conf"   # operator-owned; holds the bot token header

[gateway.people.alex]
slack-main = "U0123ABCD"      # one platform id per account

[gateway.people.sam]
slack-main = "U0456EFGH"

[gateway.routes.eng]
account = "slack-main"
channels = ["C0123ABCD"]
mention = "required"          # a new thread needs a mention of Fleet's bot
allow = ["alex", "sam"]       # names under [gateway.people]
taskConfig = "conversation"   # a TaskConfig resource: harness, host, model, env
brief = "/etc/fleet/briefs/conversation.md"
workspace = { dir = "/work/conversations" }   # shared: one conversation Task at a time
refusal = "Sorry, I only take requests from people on this channel's list."

[gateway.routes.alerts]
account = "slack-main"
channels = ["C0789IJKL"]
mention = "top-level"         # every top-level post starts a Task
allow = ["alex", "sam"]
allowBots = ["B0123ABCD"]     # the alerting app whose posts are requests
taskConfig = "conversation"
brief = "/etc/fleet/briefs/triage.md"
workspace = { dir = "/work/triage" }   # shared: each alert waits for the last Task's deletion
```

| Field | Required/default | Meaning |
| --- | --- | --- |
| `gateway.callTimeoutMs` | `15000` | Bound for each adapter call; the manager lease is raised to outlast the port's reported worst case. |
| `gateway.pollMs` | `300000` | How often a Tied Conversation reads its history as a backstop for events a listener missed, where the account has history. A Conversation's `spec.pollMs` overrides it for that thread. It is also the interval at which a create waiting under `WorkspaceBusy` is planned again, the cap on the recovery-read backoff, and how often a Listener asks `whoami` and `capabilities`. |
| `gateway.activeMs` | `86400000` | How long after a Tied thread's last message its history backstop keeps polling. |
| `gateway.retainMs` | `2592000000` | How long an Untied or Orphaned Conversation is kept after the later of its last message and its orphaning. |
| `gateway.beatMs` | `60000` | The longest a listener goes between beats. An account's `InboundReady` turns False when three are missed. |
| `gateway.questions` | Absent | The route whose first channel takes a new thread for a Question declared by a Task with no tie and no parent. A Task's `gateway.fleet.dev/route` label overrides it. Absent and unlabelled: such a Question is not posted. |
| `gateway.accounts.<name>` | One per chat account | `platform` is `slack`, or `fake` with a `dir`, in this build, and later `telegram`, `discord` or `teams`; a platform the build lacks is refused when the manager runs; `team` the platform's workspace identity; `bot`, Fleet's bot id there, which the adapter's pure event reading uses to tell Fleet's own posts and mentions apart; `inbound`, required, the one way its listener receives deliveries, which fixes how each is authenticated (Slack: `socket-mode`, an authenticated connection, or `events-api`, a signed HTTPS delivery; `fake`: `connection`), and an envelope claiming another is refused; `cli` and the platform's credential reference, here `curlConfig`, an absolute path Fleet passes to `curl -K` and never reads. |
| `gateway.people.<name>` | Optional | A person, with one platform id per account name. Ids only: never a display name, username or email. |
| `gateway.routes.<name>` | One per set of channels | `account`; `channels`, platform channel ids; `mention`, `required` or `top-level`; `allow`, people names; `allowBots`, bot or app ids whose messages count as a person's; `taskConfig`, the TaskConfig a new conversation Task references; `brief`, an absolute path the manager reads when it loads the config and copies into each Task it creates; `workspace`, the Task's workspace as a Task spec gives it, a directory every Task of the route shares, so the route starts a Task only while no other Task uses that directory, and no two routes' directories may equal or contain each other; `refusal`, the one polite refusal. A channel belongs to at most one route. |

Credentials stay with the external client: Fleet passes the curl config's
path and never constructs an Authorization header. The listener holds the
platform's connection token itself. TaskConfig defines no brief or
workspace, so a route carries both.
