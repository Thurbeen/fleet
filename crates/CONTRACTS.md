# Fleet v2: the contracts of the Task core

Fleet v2 runs agent sessions through Task resources and their controller. A
Task is one agent session doing one brief. This file says what each part guarantees
and which tests hold it to that. The [overall architecture](../docs/ARCHITECTURE.md)
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
| `TaskConfig` | reusable execution defaults | nobody (passive data) |
| `EnvProvider` | stored data and explicit file/command environment sources | nobody (passive data) |
| `ChangeRequest` | one mirrored change on a forge | ChangeRequestController |

Unrecognized kinds are refused on write as `unknown kind "X"`. A Task spec
accepts the fields below (`deny_unknown_fields`), and no object may carry
`ownerReferences`.

The [Forge controller](../docs/forge-controller/README.md), a second
controller over the Task, is designed and not served. That design adds one
thing to the store contract below: a Task field, `status.inbox`, with no
single owner, reached only through typed append and remove mutations that
refuse a duplicate key.

## Crates and the allowed graph

```text
fleet-api          pure           types and ports
fleet-controllers  pure           TaskController         -> api
fleet-exec         adapter        the one process spawner
fleet-store        adapter        file store             -> api
fleet-runtime      adapter        thurbox-cli, fake      -> api, exec
fleet-forge        adapter        gh, glab, fake         -> api, exec
fleet-manager      orchestration  lease, plans, verbs    -> api, controllers
fleet-cli          edge           the `fleet` binary     -> api, manager, store, runtime, forge
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
  fleet-controllers names `FieldManager::TaskController` and
  `FieldManager::ChangeRequestController`.
- The CLI calls no store write, no controller method, no runtime effect and
  no forge read: it parses, calls a fleet-manager verb, and prints.
- Only fleet-exec reaches `std::process::Command`, `std::os::unix::process`,
  `std::os::windows::process` or `process_wrap`. Every program fleet runs is
  bounded and has its whole process tree killed past its bound or on an early
  return: a process group on Unix, a job object on Windows.
- fleet-forge depends on nothing above the API and the spawner and is
  depended on by the CLI alone: no forge-specific name appears above the
  Forge port.

Each rule has a negative fixture that breaks it, and the test requires the
checker to report exactly that rule. The pure crates also carry
`compile_fail` doc probes for every forbidden edge.

## Task

```text
spec.configRef      optional TaskConfig reference
spec.env            explicit environment overrides
spec.harness        the agent harness, explicit or inherited
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

See [execution configuration](../docs/resources/task-config.md) for
reference validation, deterministic precedence and private launch freezing.
New configuration kinds have no lifecycle, finalizers or status writers.

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

## ChangeRequest

```text
spec.url             the change's URL, the only forge-specific input
spec.pollMs          poll interval while Watching (default 60000)
spec.closedGraceMs   how long a closed change stays watched (default forge.closedGraceMs)
spec.settledTtlMs    how long a settled mirror is retained (default forge.settledTtlMs)
```

The spec is fixed once created. `fleet apply` parses the URL through the
adapter of its host (a host not under `[forge.hosts]` is refused), derives
the name `cr-<12 hex of sha256(<kind>/<host>/<project>/<id>)>` when the
manifest gives none, and reuses an existing object only when it mirrors the
same change: its `status.ref` once written, else its own URL parsed. See the
[resource](../docs/resources/change-request.md),
[state machine](../docs/change-request-controller/state-machine.md) and
[interface](../docs/change-request-controller/interface.md).

ChangeRequestController is a pure function from the ChangeRequest and one
observation of its change to a plan: the status to write and, after
retention, whether to delete its own object. It has no effects and no
finalizer. Guarantees, each with its test in `change_request_controller` and
`e2e_forge`:

- **One complete observation, or Unknown.** The adapter answers Known only
  when it read every page the forge holds; a timeout, a throttled answer, an
  incomplete or unparseable read or a missing login is Unknown with its
  reason, leaves the snapshot untouched, and waits one interval or the
  forge's `retryAfter`. Unknown never means closed, merged, empty or gone.
- **Numbered on first sight.** `revision` moves only when the snapshot
  differs; every note first seen, edited or unresolved, and every change of
  state, head (id, checks or failing) or review decision takes that
  revision as its `seq`. An unchanged poll writes `observedAt` and
  `notBefore` only. The newest 100 notes are kept; `droppedBefore` rises to
  the `seq` of a stored note rotated out.
- **Cadence.** Watching polls every `pollMs`; a closed change is polled ten
  times more slowly for `closedGraceMs`, reopens into Watching with a new
  `state.seq`, or settles (`ClosedGraceExpired`). Merged settles (`Merged`);
  proven absence settles (`Gone`). A settled object is not polled.
- **Retention.** `settledTtlMs` after settling, the controller plans
  `delete`; the manager performs it by `uid` and `resourceVersion` under the
  lease (`Store::delete_fenced`), so a recreated or rewritten object is not
  removed. A person may delete at any time; nothing on the forge changes.
- **Host-wide deferral.** The sweep visits ChangeRequests by host then name;
  a `retryAfter` from one defers every later one on that host without asking.
- **Nothing acted on.** `mergeable` is the forge's own answer, reported as
  the `Mergeable` condition and never acted on. The port has no merge method.

Conditions: `Reachable`, `Mergeable`, `Settled`.

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
- A controller deletes its own object (`delete_fenced`) only with the
  object's `uid` and current `resourceVersion` and the lease; a ChangeRequest
  has no finalizer, so it goes at once.

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

## Forge contract

`fleet_api::forge::Forge`: `operation_bound`, `parse`, `whoami`, `change`
and `mark_ready` (a default that answers `Failed` until the Forge controller
step implements it). `change` answers `Known(Some)` with a complete
`ChangeSnapshot`, `Known(None)` for proven absence, or Unknown with an
`Uncertainty` and an optional `retryAfter`. Adapters live in `fleet-forge`,
one per `[forge.hosts]` entry, chosen by the host a URL or ref names:

- **github:** bounded `gh api graphql --hostname <host> --include` calls, one
  query paginated over reviews, comments and review threads; at most six
  calls per observation, Unknown with `Incomplete` past that.
- **gitlab:** bounded `glab api --hostname <host> --include` calls: the
  merge request, its discussions by page, its approvals; a 404 is absence
  only when the project itself answers.
- **fake:** a file-backed forge for tests and trials, with injectable faults
  (lost, timeout, throttle with `retryAfter`, incomplete, malformed, absent).
  It runs no process.

Credentials stay with each CLI's own login; Fleet passes none and stores none.

## The command line

`fleet init|up|apply|get|list|status|result|delete|reconcile|version`. Exit codes:
0 ok, 1 refused, 2 usage, 3 not found, 4 Unknown, 5 conflict, 6 lease.
`apply`, `get`, `list`, `status` and `delete` take `changerequest` as well as
`task`, `taskconfig` and `envprovider`; `status changerequest NAME --json`
prints the ref and the whole numbered snapshot.

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

A real host uses the thurbox runtime, and a `[forge]` section names the
forges ChangeRequests are read from:

```text
[runtime]
kind = "thurbox"
cli = "thurbox-cli"       # a path or a name on PATH
callTimeoutMs = 15000     # the bound on every call; shorter than the lease
keyPrefix = "fleet"       # give a second fleet on the same hosts its own

[forge.hosts]
"github.com" = { kind = "github", cli = "gh" }
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
raised to twice the longest operation of the runtime or the forge port.

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
the fake host or a stub `thurbox-cli`, and, for `e2e_forge`, the fake forge
or the same stub copied as `gh` and `glab`, across process restarts and
injected faults. `task_controller` and `change_request_controller` test each
controller as a pure function, one transition or Unknown per case.
`store_contract` tests the store against races, torn writes, stale leases
and the fenced delete. The `architecture` suite enforces the graph above.
