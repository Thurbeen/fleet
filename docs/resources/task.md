# Task resource

A Task is one agent session doing one brief. TaskController spawns the
session on the host the Task names, sends the brief once, delivers what is
queued in the Task's inbox, records the worker's answer, and deletes the
session when the Task is deleted. The guarantees behind each step are in
[the contracts](../../crates/CONTRACTS.md). Read the
[controller overview](../task-controller/README.md),
[interface](../task-controller/interface.md) and
[state machine](../task-controller/state-machine.md) for execution semantics.

## Example

```json
{
  "apiVersion": "fleet/v1alpha1",
  "kind": "Task",
  "metadata": {"name": "design-305"},
  "spec": {
    "harness": "claude-code",
    "host": "local",
    "brief": "Write the controller design.",
    "workspace": {"dir": "/work/design-305"},
    "dispatch": true,
    "outputs": {"prUrl": {"type": "string", "required": true}}
  }
}
```

## Spec

Written by people and scripts through `fleet apply`. Fixed once created,
except `dispatch`.

| Field | Required | Meaning |
| --- | --- | --- |
| `harness` | unless inherited | The agent harness, such as `claude-code`, passed to the runtime as is. |
| `host` | no | The runtime host the session runs on. Default `local`. |
| `model` | no | The model the harness runs; it reaches the harness through the variable `runtime.modelEnv` names for it. |
| `configRef` | no | `{"name":"config"}` references passive execution defaults. |
| `env` | no | Explicit string environment map; overrides inherited values. |
| `brief` | yes | The brief, sent once to the ready session. |
| `workspace.dir` | yes | The session's working directory. The thurbox runtime needs an absolute path on the host. |
| `dispatch` | no | Nothing spawns while `false` (the default). |
| `outputs` | no | The structured outputs a result must carry, by name: each `{"type": T, "required": bool}`, where T is `string`, `integer`, `number`, `boolean`, `object` or `array`. |

`harness` and `host` may not start with `-`. Any other field is refused.

See [TaskConfig](task-config.md) and [EnvProvider](env-provider.md) for provider sources,
precedence, validation and private launch snapshots.

## Status

Written by TaskController under the manager lease, except `result`, which
the worker writes through `fleet result`, and `inbox`, which nobody owns and
which changes only through the two typed mutations described under
[Inbox](#inbox).

| Field | Meaning |
| --- | --- |
| `phase` | Execution phase, defined by the [state machine](../task-controller/state-machine.md). |
| `observedGeneration` | The spec generation the controller last acted on. |
| `conditions` | One entry per [condition type](../task-controller/state-machine.md#conditions), with `status`, `reason`, `message` and `lastTransitionTime`. |
| `execution` | Frozen execution selection and source identities; environment values are private. |
| `session` | The session's runtime id, host and key, once spawned or adopted. |
| `intents`, `receipts` | Each effect's intent, written before it runs, and its outcome, written after. |
| `spawnAttempts`, `briefSends` | Spawn and brief-send counters, each bounded at 3. |
| `briefProbeFailures`, `releaseAttempts` | Three failures raise `NeedsAttention`; reads and releases continue after each wait. |
| `notBefore` | Earliest next reconcile after a failed attempt, in Unix milliseconds. The fixed retry delay is 30 seconds and survives manager restarts. |
| `brief` | The brief's key and the generation it was sent for, once it has a receipt. |
| `result` | The worker's claim: outcome, generation, summary, artifacts, outputs. |
| `inbox` | Messages queued for the session, oldest first: `{key, from, text, about, queuedAt}`. Appended by `fleet send`, removed by TaskController once delivered, each through a typed store mutation; never replaced as a list. |
| `deliveries` | TaskController's: `receipts`, the newest 100 delivery receipts by inbox key (`{key, from, at, recovered}`), and `revisions`, per source the highest `about.revision` delivered, which keeps a duplicate out after its receipt has rotated. |
| `deliveryAttempts` | TaskController's bookkeeping per inbox entry being delivered: `sends` (bounded at 3), `probeFailures` and `failed`. Removed with the entry. |

## Metadata and ownership

`apiVersion` is `fleet/v1alpha1`; `kind` is `Task`. `metadata.name` is a
lowercase DNS-like name of at most 253 characters. The store assigns `uid`,
`generation`, `resourceVersion`, creation time and `fleet.dev/release` finalizer.
A spec change increments generation; compare-and-swap uses resourceVersion.
Deleting a Task sets `deletionTimestamp` while the finalizer holds its record.
Nonempty `ownerReferences` are refused.

`fleet apply` accepts desired spec and labels, and refuses supplied status.
Only `dispatch` can change in an existing spec. Setting it false prevents a
Pending Task from spawning; it does not stop a session already spawned.

## Worker result

`fleet result` writes `status.result`: `outcome` is `shipped`, `stuck`,
`failed` or `not-applicable`, and `generation` must match the delivered
brief's generation. Optional `summary`, `artifacts` (strings) and `outputs`
carry the answer. Required outputs must be present with their declared JSON
types; undeclared outputs and top-level null output values are refused.
Nested object and
array contents have no additional schema validation.

A result is accepted only after a brief receipt exists and before deletion.
Repeating the same result is a no-op; replacing it with another result is
refused. `shipped` is a worker's claim: Fleet does not verify a forge artifact.
The next reconcile changes phase to `Concluded`; it does not release the
session until the Task is deleted.

## Inbox

`fleet send TASK TEXT` queues a message for the Task's session. It appends
one entry to `status.inbox` as `cli/<user>` under the key
`task/<name>/<uid>/send/<n>`, where `n` is the resourceVersion the append
was made against, so a key never repeats for the life of the Task. `--json`
prints the entry as appended.

| Field | Meaning |
| --- | --- |
| `key` | Deterministic and unique across `inbox` and `deliveries`; also the runtime intent key the delivery is journaled under. |
| `from` | `cli/<user>` for `fleet send`; `forge/<change>` for a relay, by design. |
| `text` | The message, delivered as typed. |
| `about` | For a relay: the mirror's `uid`, `revision` and `head`. Absent for `fleet send`. |
| `queuedAt` | Unix milliseconds. |

`status.inbox` has no owner. The store reaches it through two typed
mutations, each named by its caller: append, which refuses a key already in
`inbox` or `deliveries`, an `about.revision` not above the source's highest
delivered one, an append while more than 20 entries are undelivered, and a
deleting Task; and record-and-remove, TaskController's status write that
also moves one entry's receipt into `deliveries` and the entry out of
`inbox`. No manager may replace the list.

TaskController delivers inbox entries as it delivers the brief, oldest
first, one per pass: only to a session observed ready on that pass, under
the entry's key as the runtime intent key, journaled by the runtime, at most
3 sends, then a receipt. A send whose answer was lost is read back from the
journal, never repeated blindly. An entry that exhausts its sends raises
`NeedsAttention/DeliveryFailed` naming it and stays queued behind nothing:
later entries are still delivered. More than 20 undelivered entries raise
`NeedsAttention/InboxFull`. A Concluded Task with a nonempty inbox is due
again, for delivery only, as the
[Task state machine](../task-controller/state-machine.md#transition-rules)
describes; a readable lookup that no longer shows its session is
`WorkerGone` and `NeedsAttention`, never a respawn. A Stalled or deleting
Task delivers nothing: a deleting Task refuses `fleet send` and releases its
session as before.

## Planned additions

Design, not implemented. The [Forge controller](../forge-controller/README.md)
design adds the pieces below to the Task. None is served by the installed
binary.

| Field or verb | Owner | Meaning |
| --- | --- | --- |
| `spec.forge` | `apply`; mutable like `dispatch` | Optional overrides of the `[forge]` defaults: `relay.notes`, `relay.checks`, `draft.policy` (a name under `[forge.readyPolicies]`, refused if undefined; `null` disables mark-ready). Field-level: absent fields inherit. |
| `status.declared` | `worker`, through `fleet declare` | Facts the worker declared, each a JSON object with a required string `kind` and nothing else validated. `--change-request URL` writes `{kind: "ChangeRequest", url, name}` and ensures the named ChangeRequest exists; `--json` writes any object. Accepted after the brief has a receipt and before deletion; a repeated URL is a no-op; it concludes nothing. |
| `status.forge` | ForgeController | One entry per declared change, keyed by ChangeRequest name: its `uid`, `cursor`, `gaps`, mark-ready record and `notBefore`; plus this controller's conditions. Empty for a Task that declared nothing. |

The Forge controller also appends to `status.inbox`, as `forge/<change>`,
through the same typed append `fleet send` uses, with `about` set. The
[Forge controller interface](../forge-controller/interface.md#fields-this-controller-reads-and-owns)
defines every field of `status.declared` and `status.forge`, with an example
of a Task following two changes.

Any Task may declare a change, with or without a `spec.forge` block; the
standing worker policy tells every worker to open its change as a draft, to
declare it, and to end every post on it with
`<!-- fleet:worker <task uid> -->` on its own line, which is how Fleet tells
the worker's words from a person's on the same account and one worker's from
another's. Those three rules are the worker's to keep: a change opened ready
is never marked ready, a change never declared is never followed, and a post
without the mark comes back to its author as feedback. Nothing forge-related
has to be created in advance, and a second Task may declare a change another
Task opened. No new finalizer and no owner reference: a ChangeRequest
outlives the Tasks that follow it, and deleting a Task deletes nothing else.
The [Forge controller interface](../forge-controller/interface.md#store-port-and-write-ownership)
lists the resulting writers.

## Commands

```sh
fleet apply -f task.json             # create, or change dispatch
fleet status task design-305 --json  # phase and conditions
fleet send design-305 "Also update the docs."  # queue a message for the session
fleet result design-305 --generation 1 --outcome shipped \
  --output prUrl='"https://example.test/pr/1"'
fleet delete task design-305         # release the session, then remove
fleet reconcile                      # run the controller under the lease
```
