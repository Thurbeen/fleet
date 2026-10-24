# ForgeTask resource

Design, not implemented. This page and the
[controller guide](../forge-task-controller/README.md) describe a planned
kind. The installed binary refuses it as an unknown kind until the design
lands.

A ForgeTask is a [Task](task.md) whose work may end in a change on a forge. It
creates the Task, creates a [ChangeRequest](change-request.md) for every
change the worker declares, relays what is new on each change into the Task's
inbox, and takes a draft change out of draft when a configured policy says it
is ready. It never merges.

## Example

```json
{
  "apiVersion": "fleet/v1alpha1",
  "kind": "ForgeTask",
  "metadata": {"name": "fix-314"},
  "spec": {
    "task": {
      "harness": "claude-code",
      "host": "local",
      "brief": "Fix the flaky retry test and open a change.",
      "workspace": {"dir": "/work/fix-314"}
    },
    "forge": {
      "pollMs": 60000,
      "relay": {"notes": true, "checks": true},
      "draft": {"policy": "checks-and-bots"}
    },
    "dispatch": true
  }
}
```

## Spec

Written by people and scripts through `fleet apply`. Fixed once created,
except `dispatch`.

| Field | Required | Meaning |
| --- | --- | --- |
| `task` | yes | A complete [Task spec](task.md#spec), validated by the same rules, minus `dispatch`. |
| `forge.pollMs` | no | Passed to every ChangeRequest this ForgeTask creates. Default `60000`. |
| `forge.relay.notes` | no | Relay new notes to the worker. Default `true`. |
| `forge.relay.checks` | no | Relay a passed or failed checks state per head. Default `true`. |
| `forge.draft.policy` | no | The name of a ready policy under `[forge.readyPolicies]`. Absent: the change is never taken out of draft. |
| `dispatch` | no | Nothing is created while `false` (the default). |

There is no change URL in the spec: the change does not exist when the
ForgeTask is applied. The worker declares it later. Any other field is
refused.

## Status

Written by ForgeTaskController under the manager lease.

| Field | Meaning |
| --- | --- |
| `phase` | `Pending`, `Running`, `Linked` or `Concluded`, defined by the [state machine](../forge-task-controller/state-machine.md). |
| `task` | The created Task's `name` and `uid`. |
| `changes` | One entry per declared change: `name` and `url` of its ChangeRequest, `cursor` (the ChangeRequest `revision` relayed through) and `flipped` (`{head}` once taken out of draft, else `null`). |
| `intents`, `receipts` | Each effect's intent, written before it runs, and its outcome, written after. |
| `flipAttempts` | Per change, bounded at 3. |
| `notBefore` | Earliest next reconcile after a failed attempt. |
| `conditions` | One entry per [condition type](../forge-task-controller/state-machine.md#conditions). |

## The brief the worker receives

The Task is created with `spec.task.brief` plus two rules appended:

- Declare every change you open, the moment you open it:
  `fleet declare <task> --change <url>`.
- End every comment, reply and review you post on a change with
  `<!-- fleet:worker -->` on its own line. The forge renders nothing. It is
  how Fleet tells your words from a person's on the same account; without it
  your reply is sent back to you as review feedback.

The worker replies on the forge itself, with the forge's own CLI. Fleet has no
outbound comment path.

## Worker verbs

`fleet declare TASK --change URL` appends to the worker-owned
`Task.status.declared` list, as `fleet result` writes `status.result`. It is
accepted after the brief has a receipt and before deletion. Repeating a URL is
a no-op. It concludes nothing: the worker still reports with `fleet result`.

`fleet send TASK TEXT` appends an entry to `Task.status.inbox` with
`from: "cli/<user>"`. A person or a conductor uses it to reach a running
worker; the ForgeTask relay uses the same field. See
[planned Task additions](task.md#planned-additions).

## Metadata and ownership

`apiVersion` is `fleet/v1alpha1`; `kind` is `ForgeTask`. Names follow the
Task rule. The store assigns `uid`, `generation`, `resourceVersion`, creation
time and the `fleet.dev/children` finalizer. The created Task and every
created ChangeRequest carry an owner reference to the ForgeTask; deleting the
ForgeTask deletes them and the finalizer holds its record until both are gone
or proven absent. Deletion never closes, flips or comments on the change.

## Commands

```sh
fleet apply -f forge-task.json                # create, or change dispatch
fleet status forgetask fix-314 --json         # phase, changes, cursors
fleet declare fix-314 --change https://github.com/OWNER/REPO/pull/314
fleet send fix-314 "Please also update the changelog."
fleet delete forgetask fix-314                # delete Task and ChangeRequests
```
