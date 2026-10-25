# ChangeRequest controller interface

The controller is a pure decision function, like
[TaskController](../task-controller/interface.md). The manager owns I/O. The
[state machine](state-machine.md) defines how the values below change the
ChangeRequest; the [resource](../resources/change-request.md) defines the
external JSON fields.

## Controller API

Source: [ChangeRequestController](../../crates/fleet-controllers/src/change_request.rs)
and [plan types](../../crates/fleet-api/src/plan.rs).

```rust
pub struct ChangeRequestInput<'a> {
    pub object: &'a Object,
    pub observations: ChangeRequestObservations,
    pub now: Millis,
    pub defaults: ChangeRequestDefaults,
}

pub fn probes(&self, input: &ChangeRequestInput<'_>) -> Result<Vec<Probe>, StoreError>;
pub fn reconcile(&self, input: &ChangeRequestInput<'_>) -> Result<Plan, StoreError>;
```

`object` is the stored ChangeRequest. `now` comes from the manager's `Clock`.
`defaults` carries `forge.closedGraceMs` and `forge.settledTtlMs` from the
native config, as values, for a spec that names neither. Missing status
decodes to the default Watching state with `revision: 0`; malformed typed
spec or status returns `StoreError::Invalid`. Neither method reads the
clock, disk, environment, configuration, forge or runtime itself.

### Requested probes

| State | Probes |
| --- | --- |
| Watching or Closed, due | `Change { ref }`. |
| Settled | None. |
| Deleting | None. |

`ChangeRequestObservations` holds two optional answers. `change`, a
[`ChangeObservation`](../../crates/fleet-api/src/forge.rs): `Known(Some)`
carries the adapter's complete, forge-neutral reading of the change;
`Known(None)` says the forge answered that the change does not exist;
`Unknown { kind, why, retryAfter }` carries the reason, as the
`Uncertainty` the `Reachable` condition is named from, and, when the forge
said so, how long to wait. `None` means the probe was not asked. `parsed`,
an `Observation<ChangeRef>`, is the manager's parse of `spec.url`, supplied
only while `status.ref` is absent: the probe names the ref, so the first
poll needs it before `probes` is asked, and the controller records it as
`status.ref` in the same write as that poll's result.

### Returned plan

The plan carries `status`, and once a Settled object's retention has passed,
`delete: true` with no status, which asks the manager to delete this object
by `uid` and `resourceVersion` under the lease. `effects` is always empty
and `remove_finalizer` is always `None`: this controller writes its own
object and touches nothing else.

## Forge port

Source: [`fleet_api::forge::Forge`](../../crates/fleet-api/src/forge.rs). Every answer is Known or Unknown;
a refused write is `Known(EffectOutcome::Failed { why })`. A host is always
a forge hostname, never a runtime host, and every call runs on the machine
the manager runs on, with that machine's CLI logins.

| Method | Input | Answer |
| --- | --- | --- |
| `operation_bound` | None | Worst-case duration of one call, in milliseconds. The lease is sized to outlast it. |
| `parse` | A URL | The `ChangeRef` for it, normalised, using the adapter `[forge.hosts]` names for the host. Unknown for a host not configured. |
| `whoami` | A host | Fleet's login on that host, or Unknown. |
| `change` | A `ChangeRef` | `Some(ChangeSnapshot)`, complete; `None` for proven absence; Unknown on timeout, an incomplete read, a throttled answer or an unreadable answer, with `retryAfter` when the forge gave one. |
| `mark_ready` | A `ChangeRef`, the expected `head.id` and state, and an intent key | `MarkedReady { already }` once the change is out of draft, after a fresh read proved it still had the expected head and state; `Moved { head, state }` when either differed, nothing mutated; Failed when the forge answered no; Unknown when the answer could not be read, since the mutation may have landed. The [Forge controller](../forge-controller/interface.md)'s one effect, and Fleet's one forge write. |

The port has no merge method.

**One bounded observation, not one process.** `change` may make several
calls, paginating notes, threads and reviews, within `operation_bound`. It
answers Known only when it read everything the forge holds for the change;
if the bound runs out first it answers Unknown with `Incomplete`. The
controller never numbers a partial read.

Adapters live in the [`fleet-forge`](../../crates/fleet-forge/src/lib.rs)
crate, which depends on `fleet-api` and `fleet-exec`. The crate also holds
`ForgeHosts`, the adapters of a configuration's `[forge.hosts]`, which answers
the port by the host a URL or a ref names. Each observation spends at most
six bounded calls, each of `forge.callTimeoutMs`; the port's
`operation_bound` is six worst-case calls.

- **github:** bounded `gh api graphql --hostname <host> --include` processes
  read the pull request, its reviews, comments and review threads with
  pagination, each author's `__typename` to tell an app from a person,
  `mergeable` and `mergeStateStatus`, and the check runs and statuses of the
  head. A `Retry-After` or rate-limit reset header becomes `retryAfter`. A
  head with more than 100 checks or a thread with more than 100 comments is
  Unknown, never a partial read. `mark_ready` reads `isDraft`, `state` and
  `headRefOid` afresh in one call, then runs `markPullRequestReadyForReview`
  with the intent key as `clientMutationId`; the forge answering that the
  pull request is no longer a draft is `MarkedReady { already: true }`.
- **gitlab:** bounded `glab api --hostname <host> --include` processes read
  the merge request, its discussions with pagination, its approvals,
  `detailed_merge_status` and the head pipeline. A 404 of the merge request
  is proven absence only when the project itself answers. `mark_ready` reads
  the merge request afresh, then updates its title with the draft marker
  removed (`Draft:`, `[Draft]`, `(Draft)`, `Draft -`, or the legacy `WIP`
  forms, however cased), which is how GitLab takes a merge request out of
  draft; a draft whose title carries no such marker is a Known refusal.
- **fake:** a file-backed forge for tests and trials, with injectable faults
  (lost answers, timeouts, throttling with `retryAfter`, incomplete reads,
  malformed answers, proven absence; and for `mark_ready`, `moved`,
  `failed`, `lost` and `lost-applied`, the last two Unknown with the
  mutation not landed and landed). It runs no process, and records the
  intent key of every mark-ready that mutated a change. Its URLs take any
  shape a real forge uses (`/pull/N`, `/-/merge_requests/N`, `/changes/N`).

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

Source: [Manager](../../crates/fleet-manager/src/lib.rs).

1. For each due ChangeRequest, renew the lease. When `status.ref` is absent,
   parse `spec.url` through the Forge port and hand the answer in as
   `parsed`. Ask `probes`, perform the `Change` probe through the Forge
   port, renewing before it.
2. Call `reconcile` with the answers, the time and the configured defaults.
3. CAS-write the planned status against the resourceVersion read. A conflict
   drops the plan; the next pass rereads.
4. When the plan says `delete`, delete the object by `uid` and
   `resourceVersion` under the lease (`Store::delete_fenced`). A conflict
   means it was rewritten or recreated since: nothing is removed.

There are no effects and no receipts. `fleet up` sweeps ChangeRequests in the
same loop as Tasks, after them; a ChangeRequest is due when `notBefore` is
absent or past, like a Task. ChangeRequests of one host are swept in a stable
order, by host then name, so that a `retryAfter` from the forge defers all of
them together: the ones after it in the order are not asked and take the same
Unknown answer with the same wait. A sweep spends no more than its bounded
run on them, so forge polling cannot starve Task delivery, deletion or lease
renewal. A store without a `[forge]` section reports each ChangeRequest as
an error and polls nothing.

## Store port and write ownership

| Writer | May change | Required fencing |
| --- | --- | --- |
| `apply` | New ChangeRequest and labels; request deletion | Store validation, exclusive creation. |
| `declare` | A new ChangeRequest named from the canonical ref, on a Task's behalf | Exclusive creation; an existing object is reused unchanged after its `status.ref` is verified against the URL. |
| `change-request-controller` | All of `status`; deletion of its own object after retention | Current resourceVersion, the object's `uid` and the live manager lease. |

The spec is immutable: any change to it is refused. `ownerReferences` is
refused, as on every kind: a ChangeRequest belongs to nobody.
