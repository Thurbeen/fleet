---
name: task-intake
description: Route a follow-up to the Task that owns it, or create and dispatch bounded work as a new Task in the Task-only Fleet API. Load when a user requests work, asks about existing work, or asks Mission Control to start a worker.
license: MIT
---

# Create a Task

Read workspace and target-repository instructions first. Run `fleet apply --help`
and `fleet version --json`; this skill targets `fleet/v1alpha1`, kind `Task`.
Use the same configuration as Mission Control, never a guessed default store.

## Route a follow-up

Check whether an existing Task owns the request before you prepare a new Task.
Run `fleet list task --json`, then `fleet status task NAME --json` for each
candidate.

Answer the user yourself, with no message and no new Task, when status, a
recorded result or the workspace files already answer the question.

Send the request to the existing Task with `fleet send TASK TEXT` when both
conditions are true:

- The request concerns what that Task owns: its document, change request,
  branch or question.
- Its session is live: `phase` is `Spawned`, `Briefed` or `Concluded`,
  `session` is not null, `deleting` is false, and no `WorkerGone` condition has
  status `True`. A Concluded Task with a live session still receives messages.

Create a new Task when one of these conditions is true:

- The request asks for a new bounded result.
- The owner's session is gone: `WorkerGone` is `True`, `session` is null, the
  phase is `Stalled`, or the Task is deleting.
- The work needs a different repository, harness or workspace.
- The user asks for a separate worker.

A failed status read leaves the session state unknown. Ask or retry; do not
create a duplicate worker.

A message cannot change the brief. The worker can receive a queued message
after it has acted on earlier ones. Write each message to stand alone:

- State it as an addition to the brief, or as a correction that takes
  precedence over the brief.
- Name the document, change request or branch it concerns.
- Repeat the constraints the brief carries: the publishing rules,
  `fleet declare`, and the `<!-- fleet:worker <task uid> -->` marker.

Use task-status to confirm delivery. A delivered entry leaves `inbox`.
`DeliveryFailed` or `InboxFull` under `NeedsAttention` means no delivery.

## Prepare

Confirm the request's repository, intended result, necessary permissions and
checks. Prepare a separate existing absolute workspace directory on the target
host. A Task does not create a checkout or isolate files for you. Read the
repository's instructions to choose the harness; verify it is installed and
already authenticated. Choose a unique lowercase DNS-like Task name.

Write a JSON file using the following shape, replacing every example value.
Use a JSON serializer for values containing quotes, slashes or newlines.

```json
{
  "apiVersion": "fleet/v1alpha1",
  "kind": "Task",
  "metadata": {"name": "inspect-project"},
  "spec": {
    "harness": "codex",
    "host": "local",
    "brief": "Inspect the repository instructions and report the requested finding. Run its required checks. Do not publish without authorization. Record the result through fleet result using the generation of the delivered brief.",
    "workspace": {"dir": "/absolute/path/to/prepared-workspace"},
    "dispatch": false
  }
}
```

## Apply and dispatch

1. Check `fleet get task NAME` before applying. If it already exists, inspect
   it; never reuse a name for a different request. A failed read is not evidence
   that the Task is absent unless it explicitly reports not found.
2. Run `fleet apply -f task.json --json` to create the suspended Task.
3. When its prerequisites are satisfied and work is authorized, set only
   `spec.dispatch` to `true` in that same file and apply it again.
4. The engine's running `fleet up` reconciles it. For a deliberately bounded
   manual run, use `fleet reconcile --max-passes 10 --json`.
5. Use task-status to read the recorded result and verify the evidence.

Only `dispatch` is mutable in an existing Task spec. A new bounded result needs
a new Task. Do not supply status, uid, generation or ownerReferences on create.
Setting dispatch false does not stop an already spawned session. Do not delete
another worker's Task as a retry strategy or treat an ambiguous runtime probe as
permission to spawn a replacement.
