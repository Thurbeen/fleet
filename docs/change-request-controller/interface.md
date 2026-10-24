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
`Observation<Option<ChangeSnapshot>>`: `Known(Some)` carries the adapter's
complete, forge-neutral reading of the change; `Known(None)` says the forge
answered that the change does not exist; `Unknown { why, retryAfter }`
carries the reason and, when the forge said so, when to ask again. `None`
means the probe was not asked.

### Returned plan

The plan carries `status`, and once a Settled object's retention has passed,
`delete: true`, which asks the manager to delete this object by `uid` and
`resourceVersion` under the lease. `effects` is always empty and
`remove_finalizer` is always `None`: this controller writes its own object
and touches nothing else.

## Forge port

Source: planned `fleet_api::forge::Forge`. Every answer is Known or Unknown;
a refused write is `Known(EffectOutcome::Failed { why })`. A host is always
a forge hostname, never a runtime host, and every call runs on the machine
the manager runs on, with that machine's CLI logins.

| Method | Input | Answer |
| --- | --- | --- |
| `operation_bound` | None | Worst-case duration of one call, in milliseconds. The lease is sized to outlast it. |
| `parse` | A URL | The `ChangeRef` for it, normalised, using the adapter `[forge.hosts]` names for the host. Unknown for a host not configured. |
| `whoami` | A host | Fleet's login on that host, or Unknown. |
| `change` | A `ChangeRef` | `Some(ChangeSnapshot)`, complete; `None` for proven absence; Unknown on timeout, an incomplete read, a throttled answer or an unreadable answer, with `retryAfter` when the forge gave one. |
| `mark_ready` | A `ChangeRef`, the expected `head.id` and state, and an intent key | `Done { already }`, or Failed when the change is no longer the expected head or state, or Failed/Unknown otherwise. Used by the [Forge controller](../forge-controller/interface.md) only. |

The port has no merge method.

**One bounded observation, not one process.** `change` may make several
calls, paginating notes, threads and reviews, within `operation_bound`. It
answers Known only when it read everything the forge holds for the change;
if the bound runs out first it answers Unknown with `Incomplete`. The
controller never numbers a partial read.

Adapters live in the planned `fleet-forge` crate, which depends on `fleet-api`
and `fleet-exec`:

- **github:** bounded `gh api graphql` processes read the pull request, its
  reviews, comments and review threads with pagination, each author's
  `__typename` to tell an app from a person, `mergeable` and
  `mergeStateStatus`, and the check runs and statuses of the head. `mark_ready`
  is the ready-for-review mutation, after a fresh read of head and draft
  state.
- **gitlab:** bounded `glab api` processes read the merge request, its notes
  and discussions with pagination, its approvals, `detailed_merge_status`
  and the head pipeline. `mark_ready` removes the draft marker after the
  same fresh read.
- **fake:** a file-backed forge for tests and trials, with injectable faults
  (lost answers, timeouts, throttling with `retryAfter`, incomplete reads,
  malformed answers). It runs no process.

Credentials stay with each CLI's own login. Fleet passes none and stores none.
A later adapter for a forge without a CLI would use HTTP from `fleet-forge`;
nothing above the port changes.

### Adapter truth tables

Each adapter maps the forge's answer onto the vocabulary by these rules; an
answer the rule cannot place is Unknown for the whole observation, never a
guess.

| Field | GitHub | GitLab |
| --- | --- | --- |
| `state` | `isDraft` then `draft`; else `state` OPEN, CLOSED, MERGED. | `draft` then `draft`; else `state` opened, closed, merged. |
| `head.checks` | Over check runs and commit statuses of the head: any failed or errored is `failed`; else any queued, in progress or pending is `pending`; else any success is `passed`; none at all is `none`. Skipped and neutral count as absent. | Over the head pipeline: failed or canceled is `failed`; running, pending, created or manual is `pending`; success is `passed`; no pipeline is `none`. Skipped is absent. |
| `reviewDecision` | `reviewDecision` APPROVED, CHANGES_REQUESTED, else `none`. | Approved when the approval rules are satisfied; `changes-requested` is not a GitLab concept and is never produced; else `none`. |
| `mergeable` | `mergeable` MERGEABLE is `true`; CONFLICTING is `false` with `conflicts`; UNKNOWN is `null`. `mergeStateStatus` BLOCKED, BEHIND, UNSTABLE, DRAFT give `false` with that word. | `detailed_merge_status` mergeable is `true`; `checking` and `unchecked` are `null`; every other value is `false` with that value. |
| `threads.unresolved` | Review threads with `isResolved` false, over all pages. | Resolvable discussions with `resolved` false, over all pages. |
| `reviews` | Submitted reviews on the head commit, state APPROVED, CHANGES_REQUESTED, COMMENTED; dismissed reviews are omitted. | Approvals as `approved` reviews; there are no others. |
| `bot` | `__typename` is `Bot`. | The user is a bot or a project access token user. |

## Manager execution protocol

1. For each due ChangeRequest, renew the lease, ask `probes`, perform the
   `Change` probe through the Forge port, renewing before it.
2. Call `reconcile` with the answer and the time.
3. CAS-write the planned status against the resourceVersion read. A conflict
   drops the plan; the next pass rereads.
4. When the plan says `delete`, delete the object by `uid` and
   `resourceVersion` under the lease.

There are no effects and no receipts. `fleet up` sweeps ChangeRequests in the
same loop as Tasks; a ChangeRequest is due when `notBefore` is absent or
past, like a Task. ChangeRequests of one host are swept in a stable order so
that a `retryAfter` from the forge defers all of them together, and a sweep
spends no more than its bounded run on them, so forge polling cannot starve
Task delivery, deletion or lease renewal.

## Store port and write ownership

| Writer | May change | Required fencing |
| --- | --- | --- |
| `apply` | New ChangeRequest and labels; request deletion | Store validation, exclusive creation. |
| `declare` | A new ChangeRequest named from the canonical ref, on a Task's behalf | Exclusive creation; an existing object is reused unchanged after its `status.ref` is verified against the URL. |
| `change-request-controller` | All of `status`; deletion of its own object after retention | Current resourceVersion and live manager lease. |

The spec is immutable. `ownerReferences` is refused, as on every kind: a
ChangeRequest belongs to nobody.
