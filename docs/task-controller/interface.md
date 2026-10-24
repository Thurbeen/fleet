# Task controller interface

The controller is a pure decision function. The manager owns I/O, persistence
and process execution. The [state machine](state-machine.md) defines how the
values below change the Task; the [resource](resource.md) defines the external
JSON fields.

## Controller API

Source: [TaskController](../../crates/fleet-controllers/src/task.rs) and
[plan types](../../crates/fleet-api/src/plan.rs).

```rust
pub struct TaskInput<'a> {
    pub task: &'a Object,
    pub observations: Observations,
    pub now: Millis,
}

pub fn probes(&self, input: &TaskInput<'_>) -> Result<Vec<Probe>, StoreError>;
pub fn reconcile(&self, input: &TaskInput<'_>) -> Result<Plan, StoreError>;
```

The two methods belong to `TaskController`. `task` is the stored object,
including metadata, spec and status. `now` is Unix time in milliseconds,
supplied by the manager's `Clock`. Missing status decodes to the default
Pending state; malformed typed spec or status returns `StoreError::Invalid`.
Neither method reads the clock, disk, environment or runtime itself.

The manager starts with empty observations, asks `probes`, performs those
probes through `Runtime`, then calls `reconcile` with their answers. It skips
Tasks with a future `status.notBefore` before asking for any probe.

### Requested probes

| Task state | Probes |
| --- | --- |
| Pending, `dispatch: false` | None. |
| Pending, `dispatch: true` | `Host { host }`. |
| Spawning | `Find { host, key }`. |
| Spawned | `Find`; `Ready` for the recorded session; `BriefDelivered` when a brief intent exists without a known receipt. |
| Briefed, no worker result | `Find`. |
| Concluded or Stalled | None. |
| Deleting, regardless of phase | `Find`; `AtRest` when a session is recorded. |

`Observations` holds `host`, `find`, `ready`, `brief_delivered` and `at_rest`.
Each is optional: `None` means no answer was supplied, not a negative answer.
Each supplied answer is an [Observation](../../crates/fleet-api/src/observation.rs):

| Value | Meaning | Safe inference |
| --- | --- | --- |
| `Known(true)` / `Known(false)` | A probe answered yes / no. | Use that specific answer for that specific probe. |
| `Known(Some(SessionRef))` | A readable lookup found a session under the key. | Adopt it while spawning; afterward compare it with recorded identity. |
| `Known(None)` from `find` | A readable lookup proved the key absent. | Handle absence; do not confuse it with an unreadable lookup. |
| `Unknown { why }` | A timeout, unreadable answer or ambiguous outcome. | Retain uncertainty and report why; never infer absence or success. |

### Returned plan

```rust
pub struct Plan {
    pub status: Option<serde_json::Value>,
    pub effects: Vec<Effect>,
    pub remove_finalizer: Option<String>,
}
```

| Field | Contract |
| --- | --- |
| `status` | Desired typed Task status serialized as JSON, including every new effect intent; `None` means unchanged. The worker-owned `result` is preserved. |
| `effects` | Ordered effects, each with a deterministic key and `Action`. The controller does not execute them. |
| `remove_finalizer` | Request to remove `fleet.dev/release` after the status and effects; present only for a deleting Task whose release is established. |

An action is `Spawn(SpawnSpec)`, `Brief { session, text }` or
`Release { session }`. SpawnSpec carries `host`, `key`, `harness`, `dir` and
optional `model`. `SessionRef` carries `id`, `host` and `key`; a later session
reusing a key does not inherit readiness or at-rest proof for the recorded id.

## Manager execution protocol

Source: [Manager](../../crates/fleet-manager/src/lib.rs).

1. Acquire the manager lease and list Tasks. A list that is Unknown is not
   treated as an empty list.
2. For each due Task, renew the lease; request and perform its probes,
   renewing before each probe. Call the pure controller.
3. CAS-write planned status against the resourceVersion read. This persists
   intents before effects. A conflict drops this plan; no effect runs from it.
4. Renew before each effect, perform it, then persist its receipt. On a
   receipt conflict, reread current status and retry that receipt write so a
   concurrent worker result is preserved.
5. If requested, remove the finalizer with CAS and the current lease. The
   store removes the deleting record when no finalizers remain.
6. Repeat passes while changes or conflicts occur, up to `max_passes`, then
   release the lease when still owned. An idle report means no change in the
   last pass; it is not proof that every Task concluded.

`Manager::run(RunOptions)` returns `Result<RunReport, StoreError>`.
`RunOptions` holds `max_passes` and `lease_ttl`. The report carries counts,
`idle`, per-Task `errors` and `unknowns`. Lease loss ends the run; other
per-Task errors are reported rather than counted as successful progress.

The CLI's default lease is at least 30 seconds and twice the runtime operation
bound. An explicitly supplied lease must exceed that bound. A file-store
lease fences store writes and checks before effects; it cannot cancel an
external action already started. Recovery relies on stable runtime keys.

## Runtime port

Source: [Runtime](../../crates/fleet-api/src/runtime.rs). All session operations
return `Observation<T>`; a refused effect is
`Known(EffectOutcome::Failed { why })`, distinct from an Unknown outcome.

| Method | Input | Answer |
| --- | --- | --- |
| `operation_bound` | None | Worst-case duration of one trait operation, including timeout and cleanup grace, in milliseconds. |
| `host` | Host name | Whether the runtime can reach it. |
| `spawn` | `SpawnSpec` | `Spawned { session, adopted }`, or Failed/Unknown. |
| `find` | Host and session key | Optional session under that key, or Unknown. |
| `ready` | `SessionRef` | Whether the session can receive input now. |
| `brief` | Session, text and intent key | `Delivered { already }`, or Failed/Unknown. |
| `brief_delivered` | Session and intent key | Whether that brief was delivered, or Unknown. |
| `at_rest` | `SessionRef` | Whether it is safe to release that recorded session, or Unknown. |
| `release` | `SessionRef` | `Released` if deleted or proven absent, or Failed/Unknown. Repeating a completed release is a no-op. |

The thurbox adapter executes bounded `thurbox-cli` calls via `fleet-exec`.
Its session key is `<keyPrefix>.<uid>`. It journals each brief intent in
session metadata as pending before sending and delivered afterward. A pending
journal entry is ambiguous, so it is never a license to resend. On release,
a different id holding the key returns Unknown and is not deleted.
The fake adapter provides persisted sessions and faults without starting
processes. Both implement the same port; neither is called from pure code.

## Store port and write ownership

Source: [Store](../../crates/fleet-api/src/store.rs) and
[worker verbs](../../crates/fleet-manager/src/verbs.rs).

| Writer | May change | Required fencing |
| --- | --- | --- |
| `apply` | New Task, labels and permitted spec changes; request deletion | Store validation, exclusive creation or resourceVersion CAS for spec updates. |
| `worker` | `status.result` through `record_result` | Current resourceVersion, delivered brief generation and declared output types. |
| `task-controller` | Other Task status and release finalizer | Current resourceVersion and live manager lease. |

`Store` supplies `get`, `list`, `create`, `update_spec`, `update_status`,
`delete`, `remove_finalizer`, validation and lease acquire/renew/release.
Reads return Known/Unknown; writes return values or `StoreError`. The store
checks ownership, CAS and lease tokens at the write boundary, including when
a holder name is reused with a newer token. Lease-free worker writes do not
permit controller fields to change.

## Intent, receipt and recovery keys

| Item | Stable identity / stored fields |
| --- | --- |
| Session key | `<name>.<uid>`; the uid prevents a recreated Task adopting an earlier Task's session. |
| Spawn effect | `task/<name>/<uid>/spawn/<attempt>`. |
| Brief effect | `task/<name>/<uid>/brief`, reused for every permitted send of that one brief. |
| Release effect | `task/<name>/<uid>/release/<attempt>`. |
| Intent | Action name, timestamp and generation, stored under the effect key. |
| Receipt | Action name, timestamp and `Observation<EffectOutcome>`, stored under the effect key. |

An intent without a receipt means the action may have happened. Spawn looks
up its session key, brief reads its delivery journal, and release confirms
absence or retries after the persisted wait. The
[state machine](state-machine.md#retries-and-recovery) defines each decision.
