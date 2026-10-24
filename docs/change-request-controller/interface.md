# ChangeRequest controller interface

Design, not implemented.

The controller is a pure decision function, like
[TaskController](../task-controller/interface.md). The manager owns I/O. The
[state machine](state-machine.md) defines how the values below change the
ChangeRequest; the [resource](../resources/change-request.md) defines the
external JSON fields.

## Controller API

```rust
pub struct ChangeRequestInput<'a> {
    pub object: &'a Object,
    pub observations: ChangeRequestObservations,
    pub now: Millis,
}

pub fn probes(&self, input: &ChangeRequestInput<'_>) -> Result<Vec<Probe>, StoreError>;
pub fn reconcile(&self, input: &ChangeRequestInput<'_>) -> Result<Plan, StoreError>;
```

`object` is the stored ChangeRequest. `now` comes from the manager's `Clock`.
Missing status decodes to the default Watching state with `revision: 0`;
malformed typed spec or status returns `StoreError::Invalid`. Neither method
reads the clock, disk, environment, forge or runtime itself.

### Requested probes

| State | Probes |
| --- | --- |
| Watching or Closed, due | `Change { ref }`. |
| Settled | None. |
| Deleting | None. |

`ChangeRequestObservations` holds one optional `change`, an
`Observation<ChangeSnapshot>`: `Known` carries the adapter's forge-neutral
reading of the change; `Unknown { why }` carries the reason. `None` means the
probe was not asked.

### Returned plan

The plan carries `status` only. `effects` is always empty and
`remove_finalizer` is always `None`: this controller writes its own object
and touches nothing else.

## Forge port

Source: planned `fleet_api::forge::Forge`. Every answer is Known or Unknown;
a refused write is `Known(EffectOutcome::Failed { why })`.

| Method | Input | Answer |
| --- | --- | --- |
| `operation_bound` | None | Worst-case duration of one call, in milliseconds. The lease is sized to outlast it. |
| `parse` | A URL | The `ChangeRef` for it, using the adapter `[forge.hosts]` names for the host. Unknown for a host not configured. |
| `whoami` | A host | Fleet's login on that host, or Unknown. |
| `change` | A `ChangeRef` | The `ChangeSnapshot`: `state`, `head` (id, checks, failing), `reviewDecision`, `notes`. Unknown on timeout or an unreadable answer. |
| `mark_ready` | A `ChangeRef` and an intent key | `Done { already }`, or Failed/Unknown. Used by [ForgeTaskController](../forge-task-controller/interface.md) only. |

The port has no merge method.

Adapters live in the planned `fleet-forge` crate, which depends on `fleet-api`
and `fleet-exec`:

- **github:** every call is one bounded `gh … --json` process. One GraphQL
  query reads the change, its newest 100 reviews, comments and review
  threads, with each author's `__typename` to tell an app from a person.
- **gitlab:** every call is one bounded `glab … --output json` process over
  the merge request, its notes and discussions, and its pipeline.
- **fake:** a file-backed forge for tests and trials, with injectable faults
  (lost answers, timeouts, malformed answers). It runs no process.

Credentials stay with each CLI's own login. Fleet passes none and stores none.
A later adapter for a forge without a CLI would use HTTP from `fleet-forge`;
nothing above the port changes.

## Manager execution protocol

1. For each due ChangeRequest, renew the lease, ask `probes`, perform the
   `Change` probe through the Forge port, renewing before it.
2. Call `reconcile` with the answer and the time.
3. CAS-write the planned status against the resourceVersion read. A conflict
   drops the plan; the next pass rereads.

There are no effects and no receipts. `fleet up` sweeps ChangeRequests in the
same loop as Tasks; a ChangeRequest is due when `notBefore` is absent or
past, like a Task.

## Store port and write ownership

| Writer | May change | Required fencing |
| --- | --- | --- |
| `apply` | New ChangeRequest and labels; request deletion | Store validation, exclusive creation. |
| `forge-task-controller` | A new ChangeRequest with an owner reference, through a `CreateObject` effect | Live manager lease. |
| `change-request-controller` | All of `status` | Current resourceVersion and live manager lease. |

The spec is immutable. `ownerReferences` is accepted only when it names a
ForgeTask; the store refuses any other owner, as it refuses every owner today.
