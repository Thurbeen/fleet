# Reusable execution configuration

`TaskConfig` and `EnvProvider` are passive `fleet/v1alpha1` resources. They
have mutable specs, identity and generation, but no controller, status or
finalizer. Task remains the lifecycle owner. Existing Tasks with explicit
execution fields need no reference and retain their behavior.

```json
[
  {
    "apiVersion": "fleet/v1alpha1",
    "kind": "EnvProvider",
    "metadata": {"name": "project-env"},
    "spec": {
      "data": {"LANG": "C.UTF-8"},
      "sources": [{"type": "file", "path": "/work/config/agent-env.json"}]
    }
  },
  {
    "apiVersion": "fleet/v1alpha1",
    "kind": "TaskConfig",
    "metadata": {"name": "reviewer"},
    "spec": {
      "harness": "claude-code",
      "host": "local",
      "envFrom": [{"name": "project-env"}],
      "env": {"REVIEW_MODE": "careful"}
    }
  },
  {
    "apiVersion": "fleet/v1alpha1",
    "kind": "Task",
    "metadata": {"name": "review"},
    "spec": {
      "configRef": {"name": "reviewer"},
      "env": {"REVIEW_MODE": "focused"},
      "workspace": {"dir": "/work/project"},
      "brief": "Review the current change.",
      "dispatch": true
    }
  }
]
```

Create `/work/config/agent-env.json` as a JSON object of string values before
dispatch, for example `{"PROJECT_MODE":"review"}`. On Windows use native
absolute paths such as `C:\work\config\agent-env.json` (escape backslashes
in JSON). Source paths are **manager-local**, including for remote Tasks;
they are never resolved against the session's workspace or remote host.

## Schema and precedence

TaskConfig accepts optional `harness`, `host`, `model`, `env` (string map)
and `envFrom` (ordered provider references, each `{"name":"provider"}`).
Task accepts `configRef` and `env` in addition to its existing fields.
An explicitly present Task harness, host or model overrides the corresponding
config default. An explicit `model: null` disables an inherited model.
Omitted host ultimately means `local`; a resolved harness is required.
Task brief, workspace and outputs always belong to the Task. There are no
task templates. All Task fields except `dispatch` stay immutable, including
whether an execution field was explicitly present.

Environment values merge from lowest to highest priority:

```text
provider.data -> provider.sources (list order)
              -> TaskConfig.envFrom (list order)
              -> TaskConfig.env -> Task.env -> runtime.modelEnv selection
```

Each later source wins. Keys use `[A-Za-z_][A-Za-z0-9_]*`; case collisions
within one map are refused, and merging treats case variants as the same key
on every platform, retaining the winning spelling. Values may contain spaces,
Unicode, `=` and newlines, but never NUL. Each source and the merged environment
are limited to 64 KiB. Literal data is plaintext configuration, not a secret
vault: `get envprovider` and `get taskconfig` expose their declared specs.
Use external sources for values that must stay out of resource manifests.

## External sources

EnvProvider accepts `data` and an ordered `sources` list. A source is either
`{"type":"file","path":"/absolute/file.json"}` or:

```json
{
  "type": "command",
  "program": "/absolute/path/to/credential-helper",
  "args": ["environment", "--json"],
  "cwd": "/absolute/working/directory",
  "timeoutMs": 5000
}
```

Programs run directly with explicit argv, never through an implicit shell,
using the manager's inherited environment. Program, cwd and file paths must
be absolute on the manager's platform. Commands must exit zero and emit a
UTF-8 JSON string map on stdout. Stderr is never included in source errors.
Timeout is explicit, from 1 through 15000 ms; the manager lease must exceed
20000 ms for command sources. The existing executor bounds captured streams
and kills the process tree on timeout. Output exceeding 64 KiB, malformed
JSON, invalid maps, nonregular or missing files, missing/deleting references,
failed commands and timeouts block launch with a reconcile error. Correcting
the input allows a subsequent reconcile to retry. No partial/defaulted worker
is spawned. Sources are trusted local programs: only reference helpers you
intend the manager to execute. Helpers should only read data: a failed or
interrupted resolution may run them again before a snapshot is durable.

## Freeze, restart and retention

```text
Task dispatch enabled
  -> resolve config and all providers through Store/Runtime ports
  -> persist private launch snapshot under Task UID and manager lease
  -> persist status.execution identity
  -> ordinary Task probes, intent, spawn and brief
```

Undispatched Tasks consume current defaults when enabled. The first successful
resolution is frozen before any runtime probe/effect. Source kind, name, UID
and generation are recorded in `status.execution`, together with harness,
host and model. Resolved environment values never enter Task status, effect
intents, error output or process arguments. Thurbox receives them through
`session create --env-stdin`; a Thurbox version supporting that flag is required.
The model's configured variable and value are frozen along with other values.

Private plaintext snapshots live at `launches/<Task UID>.json` inside the
store; they are outside `objects/` and unavailable through resource commands.
On Unix the directory is mode 0700 and files, including temporary writes,
are mode 0600. On Windows they inherit the store directory's ACL: keep the
store in a user-private directory and restrict its ACL before use, including
when choosing a custom data path. Fleet does not encrypt this data or provide
a credential vault. Backups of the store include these values and require
the same access restrictions.

Retries and manager restarts reuse the snapshot, including a crash between
snapshot and status writes. Config/provider edits, deletion, name reuse,
changed files or changed command output cannot change an existing execution.
A missing/corrupt snapshot for an already frozen Task blocks rather than
re-resolving credentials. Restore the private snapshot from a protected
backup or deliberately delete/recreate the Task after its session is safely
released. Snapshots remain through conclusion and are removed on final Task
deletion. There is no background refresh or provider controller.

## Commands

```sh
fleet apply -f resources.json
fleet get taskconfig reviewer
fleet list envproviders --json
fleet status taskconfig reviewer --json
fleet delete envprovider project-env
fleet reconcile
```

Passive status has no lifecycle phase. Deleting a passive resource removes
it immediately; pending references then block launch, while already frozen
Tasks keep their execution. `fleet version --json` advertises all served
kinds. API and store versions remain `fleet/v1alpha1` and format 1; older
binaries reject the new kinds/fields. Use the updated binary for a store
containing them; no live-store migration is performed.
