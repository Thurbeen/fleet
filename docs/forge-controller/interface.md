# Forge controller interface

Design, not implemented.

The controller is a pure decision function over a Task and the ChangeRequests
it follows. The manager owns I/O, persistence and process execution. The
[state machine](state-machine.md) defines how the values below change
`status.forge`; the [Task resource](../resources/task.md#planned-additions)
lists the fields among the Task's planned additions.

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

## Fields this controller reads and owns

`status.declared` is the worker's, one item per `fleet declare`. Each item
is a JSON object with a required string `kind` and nothing else validated,
so other tools can declare their own facts through the same verb. This
controller reads items whose `kind` is `ChangeRequest` and ignores the rest.
The item `fleet declare --change-request` writes:

| Field | Meaning |
| --- | --- |
| `kind` | `ChangeRequest`. |
| `url` | The URL as declared. |
| `name` | The name of the ChangeRequest the verb ensured for it. |

`status.forge` is this controller's:

| Field | Meaning |
| --- | --- |
| `changes` | A map from ChangeRequest name to one entry, below. One per declared change; a change declared twice has one entry. |
| `conditions` | `Following`, `Settled` and `NeedsAttention`, as the [state machine](state-machine.md#conditions) defines them. |

Each entry of `changes`:

| Field | Meaning |
| --- | --- |
| `uid` | The ChangeRequest's `uid` when the entry was made or last rebound. A different `uid` under the same name is a new incarnation. |
| `state` | `following` or `settled`. |
| `cursor` | The ChangeRequest `revision` relayed through. Set to the revision of the join message; advances in the same write as each later relay. |
| `gaps` | How many times a relay found `droppedBefore` above the cursor. |
| `flip` | `{state, head, attempts, intents, receipts}`; `state` is `none`, `pending`, `done` or `failed`. |
| `notBefore` | Earliest next pass for this entry after a failed flip, in Unix milliseconds. Separate from the Task's own `notBefore` and from other entries'. |

A Task that opened a change in each of two repositories:

```json
"status": {
  "declared": [
    {"kind": "ChangeRequest", "url": "https://github.com/OWNER/app/pull/88",
     "name": "cr-1b7d02e4a9c3"},
    {"kind": "ChangeRequest", "url": "https://github.com/OWNER/infra/pull/12",
     "name": "cr-9e40c6f1d2ab"}
  ],
  "forge": {
    "changes": {
      "cr-1b7d02e4a9c3":
        {"uid": "…", "state": "following", "cursor": 7, "gaps": 0,
         "flip": {"state": "done", "head": "9f3c…", "attempts": 1}},
      "cr-9e40c6f1d2ab":
        {"uid": "…", "state": "following", "cursor": 2, "gaps": 0,
         "flip": {"state": "none", "head": null, "attempts": 0}}
    },
    "conditions": [{"type": "Following", "status": "True", "reason": "TwoOpen"}]
  }
}
```

Entries are independent: each is probed, relayed and flipped on its own, each
relay is its own inbox entry naming the change, and a problem on one is
reported in the condition message and stops nothing else.

Each entry of `status.inbox`, which this controller and `fleet send` append
to and TaskController removes from:

| Field | Meaning |
| --- | --- |
| `key` | Deterministic: `task/<name>/<uid>/relay/<change uid>/<revision>` for a relay or a join, `task/<name>/<uid>/send/<n>` for `fleet send`. Refused if already present in `inbox` or `deliveries`. |
| `from` | `forge/<change>` or `cli/<user>`. |
| `text` | The message, delivered as typed. |
| `about` | For a relay: the mirror's `uid`, `revision` and `head.id` the message was computed from. Absent for `fleet send`. |
| `queuedAt` | Unix milliseconds. |

### Requested probes

| Task state | Probes |
| --- | --- |
| No declaration and no entries | None. |
| A declaration without an entry, or an entry not Settled | `Object { kind: ChangeRequest, name }` per such change; `Whoami { host }` once per distinct host. |
| An entry whose flip is being evaluated | `AtRest { session }` for the Task's recorded session, for the `workerIdle` predicate. |
| Every entry Settled | None. |
| Deleting | None. |

`Probe::Object` is generic: the manager answers it from the store as
`Known(Some)`, `Known(None)` for proven absence, or Unknown when the record
is unreadable. It needs no lease renewal and runs before any runtime or forge
probe. `Probe::Whoami` is answered by `Forge::whoami` and cached by the
manager for the sweep. `AtRest` is the Task controller's existing probe,
reused.

`ForgeObservations` holds `changes` (keyed by entry), `whoami` (keyed by
host) and `at_rest`. Each is optional; `None` means not asked.

### Returned plan

The same `Plan` type the Task controller returns, restricted to this
controller's fields: `status` carries the desired `status.forge`, including
every advanced cursor and every new flip intent, and a list of inbox entries
to append, and nothing else; the store refuses a plan that names another
manager's field. `effects` holds `MarkReady` actions only.
`remove_finalizer` is always `None`.

| Action | Performed by the manager through | Receipt |
| --- | --- | --- |
| `MarkReady { change, expect: {head, state} }` | The Forge port, which re-reads head and state first | `Done { already }`, Failed with `Moved` when either changed, Failed otherwise, or Unknown. |

A relay is not an effect. It is part of the status write: the inbox entries
and the cursors land together or not at all.

## Effect and recovery keys

| Item | Key |
| --- | --- |
| Join or relay (inbox entry) | `task/<name>/<uid>/relay/<change uid>/<revision>` |
| Draft flip | `task/<name>/<uid>/ready/<change uid>/<head.id>` |

`<change uid>` is the ChangeRequest's `uid`, so a mirror recreated after
retention never collides with keys from its predecessor; `<revision>` is the
ChangeRequest revision the message covers. The same snapshot can never
produce two different relays, the append refuses a duplicate key, and a
relay key is also the runtime intent key the Task controller journals the
delivery under. A flip intent without a receipt is a question the next pass
answers by observing the change's state, never by repeating the effect.
Flip intents and receipts live on the entry, apart from TaskController's.

## Ready policies

A policy is a set of predicates, declared in native config under
`[forge.readyPolicies.<name>]` and selected by `spec.forge.draft.policy` or
the `forge.draftPolicy` default. The name is only a key; Fleet attaches no
meaning to it. Each predicate is a pure function of the ChangeRequest
snapshot, the Task and the probes above:

| Predicate | Holds when |
| --- | --- |
| `workerDone` | The Task has a `shipped` result. On in every policy and not configurable. |
| `workerIdle` | `AtRest` for the Task's session is Known true: the agent has finished its turn and is not mid-way through answering feedback. On in every policy and not configurable. |
| `checksPassed` | `head.checks` is `passed`; or it is `none` and `forge.noCheckGraceMs` has passed since `head.firstSeenAt`, because a repository whose CI starts only on the flip would otherwise wait forever. `pending` and Unknown never pass. |
| `botReviewsAddressed` | Every bot-authored inline note in the snapshot is resolved or has a later note from this Task's worker in its thread, and no bot review in `reviews` on the current head is `changes-requested`. |
| `noUnresolvedThreads` | `threads.unresolved` is `0`, over the whole change. |
| `forgeMergeable` | `mergeable.value` is `true`: the forge itself says the change can be merged. Optional; off in the shipped policies because a draft is often reported unmergeable for being a draft. |

`fleet init` writes three policies into the generated config, `checks`,
`checks-and-bots` and `strict`; the
[native config](../configuration/fleet-toml.md#planned-forge-section) shows
them. They are ordinary entries: rename, edit or delete them. A policy naming
an unknown predicate is refused at config load; a Task selecting a policy the
config does not define is refused at apply, and a policy removed from the
config after that raises `NeedsAttention/PolicyUnknown` on the Tasks that
name it. Adding a predicate is a controller change and a config key, not an
API change.

The standing worker policy tells every worker to open its change as a draft.
Fleet never puts a change back into draft, and never flips a change that has
ever been seen ready.

### `spec.forge`

```json
"forge": {
  "relay": {"notes": true, "checks": false},
  "draft": {"policy": "checks-and-bots"}
}
```

Each field present overrides the matching `[forge]` default; a field absent
inherits it. `"draft": {"policy": null}` disables the flip for this Task
even when the config names a default. The block is mutable like `dispatch`
and takes effect on the next pass; turning relay back on relays only what
comes after, because the cursor advanced meanwhile. A spec change increments
the Task's generation as today, and a worker result still names the
generation of the brief it received, so a running worker is never asked for
a generation it has not seen.

## The declare verb

`fleet declare TASK --change-request URL` is a manager verb, like
`fleet result`:

1. Parse the URL through the Forge port; a host not configured is refused.
2. Ensure one ChangeRequest exists for it, named from the canonical ref, by
   exclusive creation; an existing one is reused after its `status.ref` is
   verified against the parsed URL.
3. Append `{"kind": "ChangeRequest", "url": …, "name": …}` to
   `Task.status.declared` as field manager `worker`, with CAS on the Task.
   A repeated URL is a no-op.

`fleet declare TASK --json '{"kind": "…", …}'` appends any object with a
string `kind`; Fleet validates nothing else and this controller ignores
kinds it does not know. Both forms are accepted after the brief has a receipt
and before deletion, and conclude nothing. The two steps of the first form
are not atomic; a crash between them leaves a ChangeRequest nobody follows,
which is polled until it settles and then removed by retention, and the next
`declare` of that URL finds it.

## Manager execution protocol

The [Task controller's protocol](../task-controller/interface.md#manager-execution-protocol)
applies, with these additions:

1. Each controller has its own due gate: TaskController's `status.notBefore`,
   and for ForgeController any entry's `notBefore` or a declaration without
   an entry. A Task due for either is swept. TaskController runs first; the
   manager rereads the Task before running ForgeController, so the second
   plan does not conflict with the first on every sweep.
2. `Object` probes are answered from the store before the lease is renewed
   for runtime or forge probes.
3. The plan's status write uses the store's typed mutation for this
   controller: replace `status.forge` and append the listed inbox entries,
   in one CAS write. TaskController's counterpart records a delivery and
   removes the delivered entry in one write. `fleet send` appends one entry.
4. `MarkReady` is performed through the Forge port after the lease is
   renewed, like a runtime effect, then its receipt is written.

## Store port and write ownership

| Writer | May change | Required fencing |
| --- | --- | --- |
| `apply` | New Task, labels, `dispatch`, `spec.forge`; request deletion | Store validation, exclusive creation or resourceVersion CAS. |
| `worker` | `status.result`; `status.declared` through `fleet declare` | Current resourceVersion and a delivered brief. |
| `forge-controller` | All of `status.forge`; appends to `status.inbox` | Current resourceVersion and live manager lease. |
| `cli` | Appends to `status.inbox` through `fleet send` | Current resourceVersion. No lease: the CLI calls a manager verb. |
| `task-controller` | Everything else in Task status; `status.deliveries`; removes delivered `inbox` entries | Current resourceVersion and live manager lease. |

`status.inbox` has no single owner. It is reached only through three typed
mutations, append, append and remove, each named by its caller, with one
rule the store enforces, no duplicate keys across `inbox` and `deliveries`.
This is an addition to the store's one-owner-per-field contract, stated in
[the contracts](../../crates/CONTRACTS.md#what-is-served), not an exception
to it: no mutation touches another manager's field. The architecture test
lists which crates may name each manager, as it does for the managers that
exist.
