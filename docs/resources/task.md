# Task resource

A Task is one agent session doing one brief. TaskController spawns the
session on the host the Task names,
sends the brief once, records the worker's answer, and deletes the session
when the Task is deleted. The guarantees behind each step are in
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
the worker writes through `fleet result`.

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

## Planned additions

Design, not implemented. The [Forge controller](../forge-controller/README.md)
design adds the pieces below to the Task. None is served by the installed
binary.

| Field or verb | Owner | Meaning |
| --- | --- | --- |
| `spec.forge` | `apply`; mutable like `dispatch` | Optional overrides of the `[forge]` defaults: `relay.notes`, `relay.checks`, `draft.policy` (a name under `[forge.readyPolicies]`, refused if undefined). |
| `status.declared` | `worker`, through `fleet declare TASK --change URL` | The changes the worker opened, each `{url, changeRequest}`. The verb also ensures the named ChangeRequest exists. Accepted after the brief has a receipt and before deletion; a repeated URL is a no-op; it concludes nothing. |
| `status.forge` | ForgeController | One entry per declared change, keyed by ChangeRequest name: `cursor`, `flipped`, `flipAttempts`, flip intents and receipts; plus this controller's conditions and its own `notBefore`. Empty for a Task that declared nothing. |
| `status.inbox` | `Inbox`, appended by `fleet send TASK TEXT` or the Forge controller's relay | Messages for the running session: `{key, from, text, queuedAt}`. Duplicate keys are refused. |
| `status.deliveries` | TaskController | One receipt per delivered inbox key. The entry leaves `inbox` once receipted. |

Any Task may declare a change, with or without a `spec.forge` block; the
standing worker policy tells every worker to open its change as a draft, to
declare it, and to end every post on it with `<!-- fleet:worker -->` on its
own line, which is how Fleet tells the worker's words from a person's on the
same account. Nothing forge-related has to be created in advance, and a
second Task may declare a change another Task opened.

TaskController delivers each inbox entry exactly as it delivers the brief:
only to a ready session, under the entry's key as the runtime intent key,
journaled by the runtime, at most 3 sends, then a receipt. A Concluded Task
with a nonempty inbox is due again; a readable lookup that no longer shows
its session is `WorkerGone` and `NeedsAttention`, never a respawn. No new
finalizer and no owner reference: a ChangeRequest outlives the Tasks that
follow it, and deleting a Task deletes nothing else. The
[Forge controller interface](../forge-controller/interface.md#store-port-and-write-ownership)
lists the resulting writers.

## Commands

```sh
fleet apply -f task.json             # create, or change dispatch
fleet status task design-305 --json  # phase and conditions
fleet result design-305 --generation 1 --outcome shipped \
  --output prUrl='"https://example.test/pr/1"'
fleet delete task design-305         # release the session, then remove
fleet reconcile                      # run the controller under the lease
```
