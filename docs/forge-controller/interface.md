# Forge controller interface

Design, not implemented.

The controller is a pure decision function over a Task and the ChangeRequests
it follows. The manager owns I/O, persistence and process execution. The
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
controller's `status.forge`. Missing `status.forge` decodes to no entries.
Neither method reads the clock, disk, environment, forge or runtime itself.

### Requested probes

| Task state | Probes |
| --- | --- |
| No declaration and no entries | None. |
| One or more entries not Settled | `Object { kind: ChangeRequest, name }` per entry; `Whoami { host }` once per distinct host. |
| Every entry Settled | None. |
| Deleting | None. |

`Probe::Object` is generic: the manager answers it from the store as
`Known(Some)`, `Known(None)` for proven absence, or Unknown when the record
is unreadable. It needs no lease renewal and runs before any runtime or forge
probe. `Probe::Whoami` is answered by `Forge::whoami` and cached by the
manager for the sweep.

`ForgeObservations` holds `changes` (keyed by entry) and `whoami` (keyed by
host). Each is optional; `None` means not asked.

### Returned plan

The same `Plan` type the Task controller returns, restricted to this
controller's fields: `status` carries the desired `status.forge`, including
every advanced cursor and every new flip intent, and any entries to append
to `status.inbox`, and nothing else; the store refuses a plan that names
another manager's field. `effects` holds `MarkReady` actions only.
`remove_finalizer` is always `None`.

| Action | Performed by the manager through | Receipt |
| --- | --- | --- |
| `MarkReady { change }` | The Forge port | `Done { already }`, Failed, or Unknown. |

A relay is not an effect. It is part of the status write: the inbox entry and
the cursor land together or not at all.

## Effect and recovery keys

| Item | Key |
| --- | --- |
| Relay (inbox entry) | `task/<name>/<uid>/relay/<change>/<revision>` |
| Draft flip | `task/<name>/<uid>/ready/<change>/<head.id>` |

`<change>` is the ChangeRequest's name; `<revision>` is the ChangeRequest
revision the relay covers. The same snapshot can never produce two different
relays, `Inbox` refuses a duplicate key, and a relay key is also the runtime
intent key the Task controller journals the delivery under. A flip intent
without a receipt is a question the next pass answers by observing the
change's state, never by repeating the effect. Flip intents and receipts
live under `status.forge`, apart from TaskController's.

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

The standing worker policy tells every worker to open its change as a draft.
Fleet never puts a change back into draft.

## The declare verb

`fleet declare TASK --change URL` is a manager verb, like `fleet result`:

1. Parse the URL through the Forge port; a host not configured is refused.
2. Ensure one ChangeRequest exists for it, named from the parsed ref, by
   exclusive creation; an existing one is reused as is.
3. Append `{url, changeRequest}` to `Task.status.declared` as field manager
   `worker`, with CAS on the Task. A repeated URL is a no-op.

It is accepted after the brief has a receipt and before deletion, and it
concludes nothing. The two steps are not atomic; a crash between them leaves
a ChangeRequest nobody follows, which retention removes, and the next
`declare` finds it.

## Manager execution protocol

The [Task controller's protocol](../task-controller/interface.md#manager-execution-protocol)
applies, with these additions:

1. On each sweep a due Task is reconciled by TaskController first, then by
   ForgeController when `status.forge.notBefore` is absent or due. Each
   controller's plan is CAS-written separately; a conflict between them
   rereads and reruns the later one.
2. `Object` probes are answered from the store before the lease is renewed
   for runtime or forge probes.
3. `MarkReady` is performed through the Forge port after the lease is
   renewed, like a runtime effect, then its receipt is written.

## Store port and write ownership

| Writer | May change | Required fencing |
| --- | --- | --- |
| `apply` | New Task, labels, `dispatch`, `spec.forge`; request deletion | Store validation, exclusive creation or resourceVersion CAS. |
| `worker` | `status.result`; `status.declared` through `fleet declare` | Current resourceVersion and a delivered brief. |
| `forge-controller` | All of `status.forge`; appends to `status.inbox` | Current resourceVersion and live manager lease. |
| `cli` | Appends to `status.inbox` through `fleet send`, as field manager `Inbox` | Current resourceVersion. No lease: the CLI calls a manager verb. |
| `task-controller` | Everything else in Task status; `status.deliveries`; removes delivered `inbox` entries | Current resourceVersion and live manager lease. |

`Inbox` is an append-only list with one rule, no duplicate keys, so that
three writers can add to it without any of them owning the Task's other
status fields. The architecture test lists which crates may name each
manager, as it does for the managers that exist.
