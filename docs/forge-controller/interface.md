# Forge controller interface

Design, not implemented.

The controller is a pure decision function over a Task and the ChangeRequests
it owns. The manager owns I/O, persistence and process execution. The
[state machine](state-machine.md) defines how the values below change
`status.forge`; the [Task resource](../resources/task.md#planned-additions)
defines the external JSON fields.

## Controller API

```rust
pub struct ForgeInput<'a> {
    pub task: &'a Object,
    pub observations: ForgeObservations,
    pub now: Millis,
}

pub fn probes(&self, input: &ForgeInput<'_>) -> Result<Vec<Probe>, StoreError>;
pub fn reconcile(&self, input: &ForgeInput<'_>) -> Result<Plan, StoreError>;
```

`task` is the stored Task, including the worker's `status.declared` and this
controller's `status.forge`. Missing `status.forge` decodes to no changes.
Neither method reads the clock, disk, environment, forge or runtime itself.

### Requested probes

| Task state | Probes |
| --- | --- |
| No `status.declared` and no `status.forge.changes` | None. |
| A declared URL without an entry | `Owned { kind: ChangeRequest, owner: uid }`, to find one already created. |
| One or more entries not Settled | `Object { kind: ChangeRequest, name }` per entry; `Whoami { host }` once per distinct host. |
| Every entry Settled, not deleting | None. |
| Deleting, any entries | `Object` for every entry still recorded. |

`Probe::Object` and `Probe::Owned` are generic: the manager answers them from
the store as `Known(Some)`, `Known(None)` for proven absence, or Unknown when
a record is unreadable. They need no lease renewal and run before any runtime
or forge probe. `Probe::Whoami` is answered by `Forge::whoami` and cached by
the manager for the sweep.

`ForgeObservations` holds `owned`, `changes` (keyed by entry index) and
`whoami` (keyed by host). Each is optional; `None` means not asked.

### Returned plan

The same `Plan` type the Task controller returns, restricted to this
controller's fields: `status` carries the desired `status.forge` including
every new intent and advanced cursor and nothing else; the store refuses a
plan that names another manager's field. `effects` are the actions below.
`add_finalizer` and `remove_finalizer` name `fleet.dev/changes` only.

| Action | Performed by the manager through | Receipt |
| --- | --- | --- |
| `CreateObject(Object)` | The Store, under the lease, with an owner reference to the Task | The created object's uid; a repeat finds it by owner reference and returns `already`. |
| `Enqueue { task, key, from, text }` | The Store: append to `Task.status.inbox` as field manager `Inbox`, CAS on the Task | `Done { already }` when the key is already in `inbox` or `deliveries`. |
| `MarkReady { change }` | The Forge port | `Done { already }`, Failed, or Unknown. |
| `DeleteObject { kind, name }` | The Store: mark the object deleting | `Done { already }`. |

## Effect and recovery keys

| Item | Key |
| --- | --- |
| ChangeRequest creation | `task/<name>/<uid>/change/<n>` |
| Relay | `task/<name>/<uid>/relay/<n>/<revision>` |
| Draft flip | `task/<name>/<uid>/ready/<n>/<head.id>` |

`<n>` is the change's index in `status.forge.changes`; `<revision>` is the
ChangeRequest revision the relay covers. The same snapshot can never produce
two different relays, and a relay key is also the runtime intent key the
Task controller journals the delivery under. An intent without a receipt is a
question the next pass answers by observing the key, never by repeating the
effect. Intents and receipts for these keys live under `status.forge`, apart
from TaskController's.

## Ready policies

A policy is a set of predicates, declared in native config under
`[forge.readyPolicies.<name>]` and selected by `spec.forge.draft.policy` or
the `forge.draftPolicy` default. The name is only a key; Fleet attaches no
meaning to it. Each predicate is a pure function of the ChangeRequest
snapshot and the Task:

| Predicate | Holds when |
| --- | --- |
| `workerDone` | The Task has a `shipped` result. On in every policy and not configurable. |
| `checksPassed` | `head.checks` is `passed`, or it is `none` and `forge.noCheckGraceMs` has passed since the worker's last push reason: its result, its last relay or its last inbox delivery. A repository whose CI starts only on the flip would otherwise wait forever. |
| `botReviewsAddressed` | Every bot-authored inline note is resolved or has a later worker-marked note in its thread, and no bot review on the current head is `changes-requested`. |
| `noUnresolvedThreads` | No inline thread, bot or person, is unresolved. |

`fleet init` writes three policies into the generated config, `checks`,
`checks-and-bots` and `strict`; the
[native config](../configuration/fleet-toml.md#planned-forge-section) shows
them. They are ordinary entries: rename, edit or delete them. A policy naming
an unknown predicate is refused at config load; a Task selecting a policy the
config does not define is refused at apply. Adding a predicate is a controller
change and a config key, not an API change.

## Manager execution protocol

The [Task controller's protocol](../task-controller/interface.md#manager-execution-protocol)
applies, with these additions:

1. On each sweep a due Task is reconciled by TaskController first, then by
   ForgeController when `status.forge.notBefore` is absent or due. Each
   controller's plan is CAS-written separately; a conflict between them
   rereads and reruns the later one.
2. `Object` and `Owned` probes are answered from the store before the lease
   is renewed for runtime or forge probes.
3. Store effects (`CreateObject`, `Enqueue`, `DeleteObject`) are performed
   under the lease with CAS on the target object. A conflict rereads the
   target and retries the one write; it never reruns the plan.
4. `MarkReady` is performed through the Forge port after the lease is
   renewed, like a runtime effect.

## Store port and write ownership

| Writer | May change | Required fencing |
| --- | --- | --- |
| `apply` | New Task, labels, `dispatch`, `spec.forge`; request deletion | Store validation, exclusive creation or resourceVersion CAS. |
| `worker` | `status.result`; `status.declared` through `fleet declare` | Current resourceVersion and a delivered brief. |
| `forge-controller` | All of `status.forge`; the `fleet.dev/changes` finalizer; new ChangeRequests with an owner reference; `Task.status.inbox` entries through `Enqueue` | Current resourceVersion and live manager lease. |
| `cli` | `Task.status.inbox` entries through `fleet send`, as field manager `Inbox` | Current resourceVersion. No lease: the CLI calls a manager verb. |
| `task-controller` | Everything else in Task status; `status.deliveries`; removes delivered `inbox` entries; the `fleet.dev/release` finalizer | Current resourceVersion and live manager lease. |

`Inbox` is a field manager of its own so that two writers, the relay and a
person, can append to one list without either owning the Task's other status
fields. The architecture test lists which crates may name each manager, as it
does for the managers that exist.
