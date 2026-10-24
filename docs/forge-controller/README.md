# Forge controller

Design, not implemented. These pages describe a planned controller; nothing
here is served by the installed binary.

ForgeController is a second controller over the [Task](../resources/task.md).
It does not create Tasks and it is not a kind of its own. It reacts to one
fact a worker records on any Task, the change it opened, and from then on
keeps the worker informed about that change and takes it out of draft when a
policy says it is ready. It owns one status subtree, `status.forge`, the way
the worker owns `status.result`; TaskController's fields are untouched.

| Read | Answers |
| --- | --- |
| [Task](../resources/task.md#planned-additions) | What does a worker declare, and what does `spec.forge` and `status.forge` hold? |
| [ChangeRequest](../resources/change-request.md) | What does the snapshot this controller reads contain? |
| [State machine](state-machine.md) | How does each declared change move from Declared to Settled, and what is relayed when? |
| [Interface](interface.md) | What are the probes, effects, keys, ready policies and write rules? |
| [Task controller](../task-controller/README.md) | How is the Task itself run, and how is its inbox delivered? |
| [ChangeRequest controller](../change-request-controller/README.md) | Who mirrors the forge? |
| [Native config](../configuration/fleet-toml.md#planned-forge-section) | Where do defaults, hosts and ready policies come from? |
| [Overall architecture](../ARCHITECTURE.md) | Where does this controller fit in Fleet? |

## One run

1. A person applies a Task, with or without a `spec.forge` block. Nothing
   forge-related exists yet and nothing needs to.
2. The worker decides it needs a change, pushes, opens it with the forge's
   CLI and runs `fleet declare <task> --change <url>`. The standing worker
   policy tells every worker to do this; a Task applied with no thought of a
   change declares one the same way.
3. On the next sweep ForgeController sees a declared URL with no entry in
   `status.forge.changes`. It plans `CreateObject(ChangeRequest)` owned by the
   Task and adds its `fleet.dev/changes` finalizer to the Task. The manager
   persists the intent, creates the object and persists the receipt.
   ChangeRequestController starts mirroring the change.
4. On every later sweep ForgeController reads the Task and each of its
   ChangeRequests. Items with `seq` above the change's cursor, filtered,
   become one `Enqueue` into the Task's inbox. TaskController delivers it to
   the ready session under the entry's key, journaled, as it delivered the
   brief.
5. The worker replies on the forge with the forge's CLI, ending each post
   with the worker mark, so its own words are never relayed back to it.
6. When the ready policy holds on a draft change, ForgeController plans
   `MarkReady` once. The next ChangeRequest revision proves it.
7. The worker reports with `fleet result`. Notes that arrive afterwards are
   still relayed: a Concluded Task with a nonempty inbox is due again.
8. Deleting the Task deletes its ChangeRequests; the `fleet.dev/changes`
   finalizer holds the record until they are proven absent, beside the
   `fleet.dev/release` finalizer that holds it for the session.

The diff is one integer per change. The ChangeRequest numbers every item it
first sees with its `revision`; `status.forge.changes[n].cursor` is what this
controller relayed through. A second consumer keeps its own cursor against
the same snapshot.

## Two controllers, one object

TaskController owns `status.phase`, `conditions`, `session`, the spawn,
brief and release records, and `status.deliveries`. The worker owns
`status.result` and `status.declared`. ForgeController owns `status.forge`.
`Inbox` is written by ForgeController's relay and by `fleet send`. Each writer
names itself, the store refuses a write to a field another manager owns, and
each controller carries its own finalizer. This is the same arrangement
Kubernetes uses for a Node or a PersistentVolumeClaim, where several
controllers reconcile one object and the API server enforces field
ownership; the closest analogue to this controller is cert-manager's
ingress-shim, which watches a resource it does not own and creates an owned
one beside it.

## Scope

In scope: creating ChangeRequests for declared changes, relaying notes and
checks, taking a draft out of draft under a named policy, cascading delete.
Out of scope, deliberately: merging. `Mergeable` is reported on the
ChangeRequest for a person or a conductor; Fleet never merges.

## Implementation and evidence

Planned, in four steps, each its own change with its own E2E suite against the
built CLI and stub forge executables:

1. `fleet-forge` and the [ChangeRequest controller](../change-request-controller/README.md).
2. The Task inbox, `fleet send`, and inbox delivery in TaskController.
3. `fleet declare`, ForgeController with ChangeRequest creation, owner
   references, the `fleet.dev/changes` finalizer and the relay.
4. The draft flip and the shipped ready policies.

Each step keeps `cargo xtask check` green, adds its crate edges and status
writers to `crates/architecture.toml` with negative fixtures, and shrinks the
list of kinds `e2e_task_lifecycle` expects to be refused.
