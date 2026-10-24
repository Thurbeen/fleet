# ForgeTask controller

Design, not implemented. These pages describe a planned controller; nothing
here is served by the installed binary.

A ForgeTask owns one Task and, for each change its worker declares, one
ChangeRequest. Its controller reads those objects and decides what the worker
must hear and whether a draft change is ready. It performs no I/O: the manager
creates objects and enqueues messages through the store, and takes a change
out of draft through the Forge port, under the same intent, effect, receipt
protocol a spawn uses.

| Read | Answers |
| --- | --- |
| [ForgeTask](../resources/forge-task.md) | What do I apply, and what does the worker have to do? |
| [ChangeRequest](../resources/change-request.md) | What does the snapshot this controller reads contain? |
| [State machine](state-machine.md) | How does a ForgeTask move from Pending to Concluded, and what is relayed when? |
| [Interface](interface.md) | What are the probes, effects, keys, ready policies and write rules? |
| [Task controller](../task-controller/README.md) | How is the created Task run, and how is its inbox delivered? |
| [Overall architecture](../ARCHITECTURE.md) | Where does this controller fit in Fleet? |

## One run

1. A person applies a ForgeTask with `dispatch: true`.
2. The controller plans `CreateObject(Task)` with the embedded spec and the
   two brief rules. The manager persists the intent, creates the Task under
   the lease and persists its uid as the receipt. TaskController runs the
   Task as any other.
3. The worker opens a change and runs `fleet declare`. On the next pass the
   controller sees the URL on the Task and plans `CreateObject(ChangeRequest)`
   with an owner reference. ChangeRequestController starts mirroring it.
4. On every later pass the controller reads the Task and each ChangeRequest.
   Items with `seq` above the change's cursor, filtered, become one
   `Enqueue` into the Task's inbox. TaskController delivers it to the ready
   session under the entry's key, journaled, as it delivered the brief.
5. The worker replies on the forge with the forge's CLI, ending each post
   with the worker mark, so its own words are never relayed back to it.
6. When the configured ready policy holds on a draft change, the controller
   plans `MarkReady` once. The next ChangeRequest revision proves it.
7. The worker reports with `fleet result`. Notes that arrive afterwards are
   still relayed: a Concluded Task with a nonempty inbox is due again. When
   every change has settled, the ForgeTask concludes.
8. Deletion deletes the Task and the ChangeRequests; the `fleet.dev/children`
   finalizer holds the record until both are gone or proven absent.

The diff is one integer per change. The ChangeRequest numbers every item it
first sees with its `revision`; the ForgeTask keeps `cursor` per change and
takes what is above it. A second consumer keeps its own cursor against the
same snapshot. Nothing is trimmed on the consumer's behalf, and a gap is
detectable through `droppedBefore`.

## Scope

In scope: creating the Task and ChangeRequests, relaying notes and checks,
taking a draft out of draft under a named policy, cascading delete. Out of
scope, deliberately: merging. `Mergeable` is reported on the ChangeRequest for
a person or a conductor; Fleet never merges.

## Implementation and evidence

Planned, in four steps, each its own change with its own E2E suite against the
built CLI and stub forge executables:

1. `fleet-forge` and the [ChangeRequest controller](../change-request-controller/README.md).
2. The Task inbox, `fleet send`, and inbox delivery in TaskController.
3. ForgeTask with `fleet declare`, owner references and the relay.
4. The draft flip and the shipped ready policies.

Each step keeps `cargo xtask check` green, adds its crate edges and status
writers to `crates/architecture.toml` with negative fixtures, and shrinks the
list of kinds `e2e_task_lifecycle` expects to be refused.
