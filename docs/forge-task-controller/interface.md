# ForgeTask controller interface

Design, not implemented.

The controller is a pure decision function over three stored objects: the
ForgeTask, its Task and its ChangeRequests. The manager owns I/O, persistence
and process execution. The [state machine](state-machine.md) defines how the
values below change the ForgeTask; the [resource](../resources/forge-task.md)
defines the external JSON fields.

## Controller API

```rust
pub struct ForgeTaskInput<'a> {
    pub object: &'a Object,
    pub observations: ForgeTaskObservations,
    pub now: Millis,
}

pub fn probes(&self, input: &ForgeTaskInput<'_>) -> Result<Vec<Probe>, StoreError>;
pub fn reconcile(&self, input: &ForgeTaskInput<'_>) -> Result<Plan, StoreError>;
```

### Requested probes

| State | Probes |
| --- | --- |
| Pending, `dispatch: false` | None. |
| Pending, dispatched | None. The spawn is the Task controller's. |
| Running | `Object { kind: Task, name }`. |
| Linked | `Object { kind: Task, name }`; `Object { kind: ChangeRequest, name }` per entry in `status.changes`; `Whoami { host }` once per distinct host. |
| Concluded | None. |
| Deleting, any phase | `Object` for every child still recorded. |

`Probe::Object` is new and generic: the manager answers it from the store as
`Known(Some(object))`, `Known(None)` for proven absence, or Unknown when the
record is unreadable. It needs no lease renewal and runs before any runtime
or forge probe. `Probe::Whoami` is answered by `Forge::whoami` and cached by
the manager for the sweep.

`ForgeTaskObservations` holds `task`, `changes` (keyed by entry index) and
`whoami` (keyed by host). Each is optional; `None` means not asked.

### Returned plan

The same `Plan` type the Task controller returns. `status` is the desired
ForgeTask status including every new intent and the advanced cursors;
`effects` are the actions below; `remove_finalizer` names
`fleet.dev/children` only for a deleting ForgeTask whose children are gone.

| Action | Performed by the manager through | Receipt |
| --- | --- | --- |
| `CreateObject(Object)` | The Store, under the lease, with an owner reference to the ForgeTask | The created object's uid; a repeat finds the child by owner reference and returns `already`. |
| `Enqueue { task, key, from, text }` | The Store: append to `Task.status.inbox` as field manager `Inbox`, CAS on the Task | `Done { already }` when the key is already in `inbox` or `deliveries`. |
| `MarkReady { change }` | The Forge port | `Done { already }`, Failed, or Unknown. |
| `DeleteObject { kind, name }` | The Store: mark the child deleting | `Done { already }`. |

## Effect and recovery keys

| Item | Key |
| --- | --- |
| Task creation | `forgetask/<name>/<uid>/task` |
| ChangeRequest creation | `forgetask/<name>/<uid>/change/<n>` |
| Relay | `forgetask/<name>/<uid>/relay/<n>/<revision>` |
| Draft flip | `forgetask/<name>/<uid>/ready/<n>/<head.id>` |

`<n>` is the change's index in `status.changes`; `<revision>` is the
ChangeRequest revision the relay covers. The same snapshot can never produce
two different relays, and a relay key is also the runtime intent key the
Task controller journals the delivery under. An intent without a receipt is a
question the next pass answers by observing the key, never by repeating the
effect.

## Ready policies

A policy is a set of predicates, declared in native config and named by
`spec.forge.draft.policy`. Each predicate is a pure function of the
ChangeRequest snapshot and the Task:

| Predicate | Holds when |
| --- | --- |
| `workerDone` | The Task has a `shipped` result. On in every policy and not configurable. |
| `checksPassed` | `head.checks` is `passed`, or it is `none` and `forge.noCheckGraceMs` has passed since the worker's last push reason: its result, its last relay or its last inbox delivery. A repository whose CI starts only on the flip would otherwise wait forever. |
| `botReviewsAddressed` | Every bot-authored inline note is resolved or has a later worker-marked note in its thread, and no bot review on the current head is `changes-requested`. |
| `noUnresolvedThreads` | No inline thread, bot or person, is unresolved. |

Fleet ships three policies: `checks`, `checks-and-bots` and `strict`; the
[native config](../configuration/fleet-toml.md#planned-forge-section) shows
them. A policy naming an unknown predicate is refused at config load. Adding
a predicate is a controller change and a config key, not an API change.

## Manager execution protocol

The [Task controller's protocol](../task-controller/interface.md#manager-execution-protocol)
applies, with these additions:

1. `Object` probes are answered from the store before the lease is renewed
   for runtime or forge probes.
2. Store effects (`CreateObject`, `Enqueue`, `DeleteObject`) are performed
   under the lease with CAS on the target object. A conflict rereads the
   target and retries the one write; it never reruns the plan.
3. `MarkReady` is performed through the Forge port after the lease is
   renewed, like a runtime effect.
4. The manager dispatches by kind: one sweep reconciles every due Task,
   ChangeRequest and ForgeTask under the one lease.

## Store port and write ownership

| Writer | May change | Required fencing |
| --- | --- | --- |
| `apply` | New ForgeTask, labels, `dispatch`; request deletion | Store validation, exclusive creation or resourceVersion CAS. |
| `forge-task-controller` | All ForgeTask `status`; the `fleet.dev/children` finalizer; new children with an owner reference; `Task.status.inbox` entries through `Enqueue` | Current resourceVersion and live manager lease. |
| `worker` | `Task.status.declared` through `fleet declare` | Current resourceVersion and a delivered brief. |
| `cli` | `Task.status.inbox` entries through `fleet send`, as field manager `Inbox` | Current resourceVersion. No lease: the CLI calls a manager verb. |
| `task-controller` | `Task.status.deliveries`; removes delivered `inbox` entries | Current resourceVersion and live manager lease. |

`Inbox` is a new field manager so that two writers, the relay and a person,
can append to one list without either owning the Task's other status fields.
The architecture test lists which crates may name it, as it does for the
managers that exist.
