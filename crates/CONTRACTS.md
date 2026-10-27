# Fleet v2: the contracts of the Task core

Fleet v2 runs agent sessions through Task resources and their controller. A
Task is one agent session doing one brief. This file says what each part
guarantees
and which tests hold it to that. The [overall
architecture](../docs/ARCHITECTURE.md)
explains the component boundaries. The
[Task controller guide](../docs/controllers/task/README.md) defines the
resource fields, state machine and controller interface.

Fleet v2 is a clean break from the Python v1 control plane. It reads none of
v1's queue records, extension or operator configuration, and the root
`FLEET_MAJOR` file (`2`) keeps a v1 checkout from updating onto it.

## What is served

| Kind | Role | Status written by |
| --- | --- | --- |
| `Task` | one agent session doing one brief | TaskController; the worker writes `status.result` and `status.declared`; ForgeController writes `status.forge` |
| `TaskConfig` | reusable execution defaults | nobody (passive data) |
| `EnvProvider` | stored data and explicit file/command environment sources | nobody (passive data) |
| `ChangeRequest` | one mirrored change on a forge | ChangeRequestController |
| Plugin-defined | a registered external controller’s resource | `plugin/<name>`, including reserved host bookkeeping |

Kinds not built in or registered by a plugin are refused on write as `unknown
kind "X"`. A Task spec
accepts the fields below (`deny_unknown_fields`), and no object may carry
`ownerReferences`.

The [Forge controller](../docs/controllers/forge/README.md) is a second
controller over the Task: it follows the changes a worker declared and
relays their feedback into the Task's inbox, a status field with no single
owner, `status.inbox`, reached only through the typed mutations under
[Store](#store), which refuse a duplicate key. `fleet send` and the Forge
controller append to it and TaskController delivers it. The Forge
controller's one effect, mark-ready, is the only write Fleet makes on a
forge: it takes a declared draft out of draft under a configured ready
policy, and nothing else.

The [Plugin controller](../docs/controllers/plugin/README.md) hosts external
controllers. The store serves their schema-validated
[kinds](../docs/resources/plugin-kinds.md); the manager calls their pure
`probes` and `reconcile` and bounded `observe` and `perform` through the
`Plugins` port. A pure gate admits plans before leased status writes and
atomic verbs. `e2e_plugin_controller` and `plugin_gate` exercise the host.
SDKs and issue-specific plugins remain separate projects.

The [Gateway controller](../docs/controllers/gateway/README.md) is a design
and serves nothing yet: `Conversation` is refused as an unknown kind,
`[gateway]` as an unknown config field, and no `fleet gateway` verb exists.
As designed it adds a `Gateway` port in `fleet-api`, adapters in a
`fleet-gateway` crate depending on `fleet-api` and `fleet-exec` only,
`ConversationController` and `GatewayController` field managers, a
`listener/<account>` appender to the Conversation spool (and the manager
appending Fleet's opening post there after an `Opened` answer, direct or
recovered from channel history), a `gateway/`
inbox source, and `status.answered`, written only by an inbox append that
carries `answers`. None of these guarantees hold until implemented with
their tests.

## Crates and the allowed graph

```text
fleet-api          pure           types and ports
fleet-controllers  pure           controllers and gate   -> api
fleet-exec         adapter        the one process spawner
fleet-store        adapter        file store             -> api
fleet-runtime      adapter        thurbox-cli, fake      -> api, exec
fleet-forge        adapter        az, curl, gh, glab, fake -> api, exec
fleet-plugin       adapter        bounded plugin stdio   -> api, exec
fleet-manager      orchestration  lease, plans, verbs    -> api, controllers
fleet-cli          edge           the `fleet` binary     -> api, manager, store,
runtime, forge, plugin
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
  fleet-controllers names `FieldManager::TaskController`,
  `FieldManager::ChangeRequestController` and `FieldManager::ForgeController`,
  only fleet-api and fleet-controllers name `FieldManager::Plugin`;
  only fleet-manager's `result` and `declare` verbs name `FieldManager::Worker`,
  and only fleet-manager's `send` verb (and the store that checks it) names
  `FieldManager::Cli`, the caller of the inbox append.
- The CLI calls no store write, no controller method, no runtime effect and
  no forge read: it parses, calls a fleet-manager verb, and prints.
- Only fleet-exec reaches `std::process::Command`, `std::os::unix::process`,
  `std::os::windows::process` or `process_wrap`. Every program fleet runs is
  bounded and has its whole process tree killed past its bound or on an early
  return: a process group on Unix, a job object on Windows.
- fleet-forge depends on nothing above the API and the spawner and is
  depended on by the CLI alone: no forge-specific name appears above the
  Forge port.
- The Forge port itself, which carries the one forge write, is held only by
  fleet-api, fleet-forge, fleet-manager and fleet-cli: no other adapter and
  no controller can reach `mark_ready`.

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
spec.forge          optional: relay and draft overrides for declared changes
```

The spec is immutable once created, except `dispatch` and `forge`; a
different brief is a different Task. `harness` and `host` may not start
with `-`, since both reach the runtime as arguments.

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
- **Inbox.** `fleet send TASK TEXT` appends `{key, from, text, queuedAt}` to
  `status.inbox` as `cli/<user>`, under `task/<name>/<uid>/send/<n>`, `n`
  the resourceVersion appended against. The oldest entry not given up on is
  delivered as the brief is: to a session observed ready on that pass, under
  its key as the runtime intent key, journaled, one entry per pass, at most
  three sends; a lost answer is read back from the journal, never resent
  blindly. A Known receipt moves the entry's receipt into
  `status.deliveries` and the entry out of `status.inbox` in one write, and
  drops its intent, receipt and attempt records. Three refused sends raise
  `NeedsAttention/DeliveryFailed` naming the entry, which stays while later
  entries go out; three unreadable journal reads raise
  `DeliveryUnconfirmed` and reading continues; more than 20 undelivered raise
  `InboxFull`. Delivery runs in Spawned (after the brief), Briefed and
  Concluded, which stays terminal and is due only while its inbox is
  nonempty; a readable list without the session there is `WorkerGone`, never
  a respawn. Stalled delivers nothing; deletion keeps precedence and refuses
  new sends.
- **No churn.** A condition keeps its `lastTransitionTime` unless its status
  changes against what was stored, so a pass that sees nothing new writes
  nothing and the manager goes idle.

Conditions: `HostReachable`, `Spawned`, `Briefed`, `Stalled`, `WorkerGone`,
`Concluded`, `Released`, `NeedsAttention`.

## Following declared changes

ForgeController is a pure function from the Task, the ChangeRequests its
worker declared, Fleet's login on their hosts and, when asked, whether the
session is at rest, to a plan: the desired `status.forge`, the inbox
entries to land with it, and the `MarkReady` effects whose intents it
carries. It has one effect and no finalizer, and never writes a
ChangeRequest. Guarantees, each with its test in `forge_controller`,
`e2e_forge_controller` and `e2e_mark_ready`:

- **Declare.** `fleet declare TASK --change-request URL` parses the URL
  through the Forge port, ensures one ChangeRequest under the derived name
  (an existing one is reused after its `status.ref`, else its own URL, is
  verified) and appends `{kind, url, name}` to `status.declared` as
  `worker`, with CAS; a repeated URL is a no-op. `--json OBJECT` appends any
  object with a string `kind`. Both need a brief receipt and refuse a
  deleting Task, and an ineligible Task creates no orphan; a crash between
  the two steps leaves a ChangeRequest nobody follows, which the next
  `declare` of that URL finds.
- **Join at the present.** A declaration whose object is Known with a `ref`
  gets an entry with the object's `uid` and `cursor = revision`, and one
  message holding the whole snapshot, under
  `task/<name>/<uid>/relay/<change uid>/<revision>` from `forge/<name>`;
  when the mirror has already dropped notes, the message says older notes
  exist on the forge. An object without a `ref` yet, or Unknown, waits;
  readable absence raises `NeedsAttention/ChangeGone`.
- **Relay by cursor.** Every note with `seq` above the cursor that passes the
  filters (never this Task's own marked words; bots only per `relayBots`;
  never a resolved thread or one this Task already answered in) and every
  transition of state, review decision or passed/failed checks above it
  become one entry, with `about` naming the mirror's `uid`, `revision` and
  head; the cursor advances in the same write, whether or not anything was
  sent. Another worker's marked note is relayed and labelled with its Task.
  `spec.forge.relay.notes` and `.checks` stop each kind on its own.
- **Gaps.** `droppedBefore` above the cursor relays a gap notice with the
  whole retained snapshot, counts `gaps` and raises `NeedsAttention/RelayGap`
  until the next relay that carries something.
- **Attribution first.** An Unknown login on the change's host skips the
  entry this pass: nothing is relayed that could not be filtered.
- **Rebind.** A different `uid` under the same name is a new incarnation:
  the entry takes it, resets its cursor and mark-ready record, and joins
  again; the delivered-revision guard is per mirror `uid`, so the new join
  is not refused as a repeat.
- **Settled.** An entry whose ChangeRequest settled is relayed one last time
  and read no more; a Concluded Task is still relayed to; a deleted Task
  leaves every ChangeRequest alone.
- **One write.** The cursor and the entries land through `record_relay`, or
  not at all: a duplicate key or a stale version refuses the whole write.
  A mark-ready intent rides in the same write.
- **Mark ready once.** A following entry whose change is a draft that was
  never seen ready (`everReady` false), whose mirror is fresh (`Reachable`
  True, `observedAt` within `2 × pollMs`) and whose selected policy holds,
  `workerDone` (a `shipped` result) and `workerIdle` (`AtRest` Known true)
  always included, gets one intent under
  `task/<name>/<uid>/ready/<change uid>/<head>` and one `MarkReady` effect
  through the Forge port, after the lease is renewed; the receipt lands on
  the entry. The session is asked only once every predicate the snapshot
  answers holds. A change opened ready, or put back into draft by a person,
  is never marked, by this Task or any other following it; Fleet never puts
  a change back into draft.
- **Policies.** A policy is a named set of `checksPassed` (passed, or
  checkless for `noCheckGraceMs` since the head was first seen),
  `botReviewsAddressed`, `noUnresolvedThreads` and `forgeMergeable`;
  `spec.forge.draft.policy` selects one, else `forge.draftPolicy`, and
  `null` selects none. Any other key is refused at config load, a Task
  naming an undefined policy is refused at apply, and a policy removed from
  the config afterwards raises `NeedsAttention/PolicyUnknown`. A policy that
  waits on bots relays their blockers whatever `relayBots` says. `fleet
  init` writes `checks`, `checks-and-bots` and `strict`.
- **Moved is not an attempt.** The adapter re-reads head and state
  immediately before the mutation and answers `Moved` when either differs,
  mutating nothing; the record returns to `none` without counting, and that
  head is not tried again while the mirror still shows it. The window
  between the fresh read and the mutation is the forge's.
- **Retries.** A Known refusal waits `RETRY_DELAY_MS` on the entry's own
  `notBefore`, holding back no other entry; at most `MAX_ATTEMPTS` (3)
  intents are written against one change, then `NeedsAttention/MarkReadyFailed`
  and no more.
- **Unknown is a question.** An Unknown outcome, or an intent whose receipt
  never landed, is answered by the change's next observation made after the
  intent: ready is a recovered receipt (`done`), still a draft is a failed
  attempt. The effect is never repeated to find out.

Conditions, under `status.forge.conditions`: `Following`, `Settled`,
`NeedsAttention` (`RelayGap`, `ChangeGone`, `MarkReadyFailed`,
`PolicyUnknown`).

## ChangeRequest

```text
spec.url             the change's URL, the only forge-specific input
spec.pollMs          poll interval while Watching (default 60000)
spec.closedGraceMs   how long a closed change stays watched (default
forge.closedGraceMs)
spec.settledTtlMs    how long a settled mirror is retained (default
forge.settledTtlMs)
```

The spec is fixed once created. `fleet apply` parses the URL through the
adapter of its host (a host not under `[forge.hosts]` is refused), derives
the name `cr-<12 hex of sha256(<kind>/<host>/<project>/<id>)>` when the
manifest gives none, and reuses an existing object only when it mirrors the
same change: its `status.ref` once written, else its own URL parsed. See the
[resource](../docs/resources/change-request.md),
[state machine](../docs/controllers/change-request/state-machine.md) and
[interface](../docs/controllers/change-request/interface.md).

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
- Each status field has one owner, checked on every write, except
  `Task.status.inbox`, which has none and which no status write may change.
  A controller writes only under the current manager lease: an expired or
  superseded token is refused, and the token only grows.
- `status.inbox` is reached through three typed mutations, each named by
  its caller and compare-and-swap under the lock: `append_inbox` (`cli`,
  through the manager's `send` verb, no lease; or `forge-controller`, under
  the lease) refuses a key already in `inbox` or `deliveries`, an
  `about.revision` at or below the mirror's (`about.uid`) highest delivered
  one, an append while more than 20 entries are undelivered, a deleting
  Task, and a source not under the caller's prefix (`cli/`, `forge/`);
  `record_relay` (ForgeController, under the lease) replaces `status.forge`
  and appends each listed entry under the same checks, in one write that a
  single refused entry fails whole; `record_delivery` (TaskController,
  under the lease) writes the controller's status and, in the same write,
  the receipt into the newest-100 `status.deliveries` and the entry out of
  `inbox`, refusing a key no entry holds. None changes another field;
  `store_contract` races appenders against the remover and tears the
  record.
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
and `mark_ready`. `change` answers `Known(Some)` with a complete
`ChangeSnapshot`, `Known(None)` for proven absence, or Unknown with an
`Uncertainty` and an optional `retryAfter`. `mark_ready` takes the expected
head and state and an intent key, reads both afresh, answers `Moved` when
either differs (nothing mutated), `MarkedReady { already }` once the change
is out of draft, Failed when the forge said no, and Unknown only when the
mutation's answer could not be read. Adapters live in `fleet-forge`, one
per `[forge.hosts]` entry, chosen by the host a URL or ref names:

- **github:** bounded `gh api graphql --hostname <host> --include` calls, one
  query paginated over reviews, comments and review threads; at most six
  calls per observation, Unknown with `Incomplete` past that. Mark-ready is
  one fresh read of `isDraft`, `state` and `headRefOid`, then
  `markPullRequestReadyForReview` with the intent key as `clientMutationId`.
- **gitlab:** bounded `glab api --hostname <host> --include` calls: the
  merge request, its discussions by page, its approvals; a 404 is absence
  only when the project itself answers. Mark-ready is one fresh read, then
  the title updated with its draft marker removed.
- **azure-devops:** bounded `az devops invoke` calls for hosted Azure DevOps
  Services. Organization/project/repository identities are checked against
  the URL and every continuation token stays inside the adapter's call bound.
- **bitbucket-cloud:** bounded authenticated `curl` reads against
  `https://api.bitbucket.org/2.0/` for `bitbucket.org` only. The adapter
  brackets paginated comments, commit statuses and mergeability checks with
  two pull-request reads, requires every status to link to that exact head,
  and rejects repository or head movement. Participant
  votes are not exposed as head-bound reviews. Inline thread counts require
  explicit resolution evidence. Mark-ready fails closed because Bitbucket's
  `PUT draft=false` has no atomic head-and-state precondition.
- **fake:** a file-backed forge for tests and trials, with injectable faults
  (lost, timeout, throttle with `retryAfter`, incomplete, malformed, absent;
  for mark-ready `moved`, `failed`, `lost` and `lost-applied`). It runs no
  process and records every mark-ready that mutated a change.

Credentials stay with each external client's own configuration; Fleet passes
none and stores none. Bitbucket Cloud uses curl configuration or netrc rather
than assuming an official provider CLI.

## The command line

`fleet
init|up|apply|get|list|status|send|declare|result|delete|reconcile|version`.
Exit codes:
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
"bitbucket.org" = { kind = "bitbucket-cloud", cli = "curl" }

[forge.readyPolicies.checks]   # selected by spec.forge.draft.policy or
forge.draftPolicy
checksPassed = true
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
or the same stub copied as `az`, `curl`, `gh` and `glab`, across process
restarts and injected faults; `e2e_inbox` covers `fleet send` and delivery on
both the fake host and the stub; `e2e_forge_controller` covers `fleet declare`,
the join and the relay against the fake forge and the fake host.
`e2e_mark_ready` covers the mark-ready effect against the fake forge and
the fake host, and through the stub `az`, `curl`, `gh` and `glab`.
`task_controller`, `change_request_controller` and `forge_controller` test
each controller as a pure function, one transition or Unknown per case.
`store_contract` tests the store against races, torn writes, stale leases
and the fenced delete. The `architecture` suite enforces the graph above.
