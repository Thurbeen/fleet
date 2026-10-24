# Fleet v2: the contracts of the Task core

Fleet v2 runs agent sessions through Task resources and their controller. A
Task is one agent session doing one brief. This file says what each part guarantees
and which tests hold it to that. The [overall architecture](../ARCHITECTURE.md)
explains the component boundaries. The
[Task controller guide](../docs/task-controller/README.md) defines the
resource fields, state machine and controller interface.

Fleet v2 is a clean break from the Python v1 control plane. It reads none of
v1's queue records, extension or operator configuration, and the root
`FLEET_MAJOR` file (`2`) keeps a v1 checkout from updating onto it.

## What is served

| Kind | Role | Status written by |
| --- | --- | --- |
| `Task` | one agent session doing one brief | TaskController; the worker writes `status.result` |

Unrecognized kinds are refused on write as `unknown kind "X"`. A Task spec
accepts the fields below (`deny_unknown_fields`), and no object may carry
`ownerReferences`.

## Crates and the allowed graph

```text
fleet-api          pure           types and ports
fleet-controllers  pure           TaskController         -> api
fleet-exec         adapter        the one process spawner
fleet-store        adapter        file store             -> api
fleet-runtime      adapter        thurbox-cli, fake      -> api, exec
fleet-manager      orchestration  lease, plans, verbs    -> api, controllers
fleet-cli          edge           the `fleet` binary     -> api, manager, store, runtime
fleet-arch         tooling        the architecture test
fleet-dev          tooling        `cargo xtask`: gate and release
```

`crates/architecture.toml` declares the graph and these rules, and the
`architecture` test of `fleet-arch` enforces them on every gate run:

- Both the declared and the actual crate graph are acyclic, and every actual
  edge (normal, build and dev dependencies, renames included) is declared.
- A pure crate reaches no filesystem, process, network, environment, thread
  or clock, and uses only the external crates its rule lists. Paths are
  resolved through aliases, globs and re-exports first, across crates.
- No crate reaches another crate it may not depend on through a third
  crate's re-export.
- Only the crates listed may name a status-writing field manager: only
  fleet-controllers names `FieldManager::TaskController`.
- The CLI calls no store write, no controller method and no runtime effect:
  it parses, calls a fleet-manager verb, and prints.
- Only fleet-exec reaches `std::process::Command`, `std::os::unix::process`,
  `std::os::windows::process` or `process_wrap`. Every program fleet runs is
  bounded and has its whole process tree killed past its bound or on an early
  return: a process group on Unix, a job object on Windows.

Each rule has a negative fixture that breaks it, and the test requires the
checker to report exactly that rule. The pure crates also carry
`compile_fail` doc probes for every forbidden edge.

## Task

```text
spec.harness        the agent harness, passed to the runtime as is
spec.host           the runtime host (default "local")
spec.model          optional; reaches the harness through its model variable
spec.brief          the brief, sent once
spec.workspace.dir  the session's working directory
spec.dispatch       nothing spawns while false
spec.outputs        optional: the structured outputs a result must carry
```

The spec is immutable once created, except `dispatch`; a different brief is a
different Task. `harness` and `host` may not start with `-`, since both reach
the runtime as arguments.

## Task lifecycle

TaskController is a pure function from the Task and what the probes saw to a
plan: the status to write, the effects to run, and whether to drop the
finalizer. The manager performs it in this order:

1. **Intent first.** The status carrying each effect's intent is written
   (compare-and-swap, under the lease) before the effect runs.
2. **Effect.** Spawn, brief or release, each under a deterministic key
   (`task/<name>/<uid>/<action>[/<attempt>]`).
3. **Receipt after.** The outcome is written as the key's receipt. An intent
   without a receipt is a question the next pass answers by observing the
   key, never by repeating the effect blindly.

Guarantees, each with its test:

- **Spawn once.** The session key is `<name>.<uid>`. After an unknown spawn
  outcome the controller looks the key up: found is adopted, absent is retried,
  unreadable waits.
- **Brief once.** The brief is sent only to a ready session, under one key
  the session stores. An unconfirmed send is asked about: delivered is a
  recovered receipt, not delivered is one more send under the same key, and
  "cannot tell" is retried after a wait. Three unreadable probes raise
  `NeedsAttention(BriefUnconfirmed)`; probes continue so recovery can settle it.
- **Retries.** Spawns and brief sends are tried at most `MAX_ATTEMPTS` (3)
  times before `Stalled` and `NeedsAttention`. Failed attempts wait
  `RETRY_DELAY_MS` (30 seconds), persisted as `status.notBefore` across manager
  runs. Unreadable delivery probes and failed releases raise `NeedsAttention`
  after three attempts but continue after each wait: recovery can confirm
  delivery or remove the finalizer.
- **Result.** `fleet result` writes `status.result` as field manager
  `worker`, only once the brief has a receipt, only for the generation it
  was briefed for, and only when its outputs match `spec.outputs`. The same
  result again is a no-op; a different one is refused. A recorded result
  concludes the Task.
- **WorkerGone.** A session a readable list no longer shows sets `WorkerGone`
  and `NeedsAttention`. An unreadable list is never read as gone. Nothing
  abandons the Task by itself.
- **Release.** The `fleet.dev/release` finalizer holds a deleted Task until
  its session is deleted, only once the session is proven at rest or gone.
- **No churn.** A condition keeps its `lastTransitionTime` unless its status
  changes against what was stored, so a pass that sees nothing new writes
  nothing and the manager goes idle.

Conditions: `HostReachable`, `Spawned`, `Briefed`, `Stalled`, `WorkerGone`,
`Concluded`, `Released`, `NeedsAttention`.

## Store

A data directory holds `store.json` (format and version), `lock`, `seq`,
`lease.json`, `objects/<Kind>/<name>.json` and `tmp/`. The default is the
platform data directory (`$XDG_DATA_HOME/fleet`, `~/.local/share/fleet`,
`%LOCALAPPDATA%\fleet`); a non-empty directory that is not a store is
refused.

- Writes go to a synced temp file, then a hard link (exclusive create) or a
  rename (replace), then the directory is synced.
- Every write holds the store lock, an OS file lock that a writer waits for,
  and checks `resourceVersion`.
- A missing record is `Known(None)`; an unreadable, corrupt or misfiled one is
  Unknown and blocks writes to that name. A list with one bad record is
  Unknown as a whole.
- Each status field has one owner, checked on every write. A controller
  writes only under the current manager lease: an expired or superseded
  token is refused, and the token only grows.
- Deleting marks a Task while its finalizer remains; only its controller,
  holding the lease, removes the finalizer, and only from a deleting Task.

## Runtime contract

`fleet_api::runtime::Runtime`: `host`, `spawn`, `find`, `ready`, `brief`,
`brief_delivered`, `at_rest` and `release`, each answering Known or Unknown.
Each adapter reports its longest single operation (`operation_bound`), and
`fleet reconcile` refuses a lease that could not outlast it.

- **thurbox:** every call is one bounded `thurbox-cli … --json`. A session's
  thurbox key is `<keyPrefix>.<uid>`, and `--on-existing adopt` makes a
  repeated create return the session holding the key. The brief's delivery
  is journaled in the session's own metadata. `spec.model` reaches the
  harness as the variable `runtime.modelEnv` names for it. Before Codex's first
  lifecycle report, readiness additionally accepts its initial empty composer:
  a matching owned session, corroborated live Codex process, no reported or
  contradicted hook, and a visible-pane capture placing the cursor at the
  initial prompt under the welcome screen. Missing or different evidence
  remains Unknown. This input-only proof never authorizes release; `at_rest`
  still requires idle, done or stopped. `e2e_runtime` covers initial delivery,
  reconnect, busy/dialog/cursor guards and unchanged deletion safety.
- **fake:** a file-backed host for tests and trials, with injectable faults
  (lost answers, refusals, delays, hangs). It runs no process.

## The command line

`fleet init|up|apply|get|list|status|result|delete|reconcile|version`. Exit codes:
0 ok, 1 refused, 2 usage, 3 not found, 4 Unknown, 5 conflict, 6 lease.

The config file is versioned TOML; relative paths are relative to it. This
one runs the fake runtime:

```toml
version = 1

[data]
dir = "data"

[runtime]
kind = "fake"
dir = "fake-host"
```

A real host uses the thurbox runtime:

```text
[runtime]
kind = "thurbox"
cli = "thurbox-cli"       # a path or a name on PATH
callTimeoutMs = 15000     # the bound on every call; shorter than the lease
keyPrefix = "fleet"       # give a second fleet on the same hosts its own
```

A Task manifest:

```json
[
  {
    "apiVersion": "fleet/v1alpha1",
    "kind": "Task",
    "metadata": {"name": "design-305", "labels": {"topic": "fleet-305"}},
    "spec": {
      "harness": "claude-code",
      "host": "local",
      "brief": "Write the controller design.",
      "workspace": {"dir": "work/design-305"},
      "dispatch": true,
      "outputs": {"prUrl": {"type": "string", "required": true}}
    }
  }
]
```

`fleet reconcile` takes the manager lease, runs passes until nothing changes
(or `--max-passes`), and gives the lease back. Its default lease is 30 s,
raised to twice the runtime's longest operation.

`fleet init` installs the pinned embedded VEGA starter offline and creates
native per-user config; `--update` advances unedited managed workspace files
and writes conflict candidates for customizations. Config is never replaced.
The [README](../README.md#configuration-and-customization) specifies precedence,
paths, provenance and recovery. Bootstrap does not read v1 data.

`fleet up` ensures one ordinary `Task/vega` through the manager, then retains
the manager lease across periodic sweeps for all due Tasks. The poll interval
must fit inside the lease as well as runtime operations. Ctrl-C/SIGTERM stops
after bounded work and releases the lease; Tasks and sessions survive. Crash
recovery waits for lease expiry. Uncertain runtime observations remain Unknown.
`e2e_bootstrap` covers installed first-run, repeat/concurrent initialization,
customized updates, resident progress, exclusive startup and crash/reconnect.

## Tests

The `e2e_*` suites drive the real `fleet` binary against an isolated store,
the fake host or a stub `thurbox-cli`, across process restarts and injected
faults. `task_controller` tests the controller as a pure function, one
transition or Unknown per case. `store_contract` tests the store against
races, torn writes and stale leases. The `architecture` suite enforces the
graph above.
