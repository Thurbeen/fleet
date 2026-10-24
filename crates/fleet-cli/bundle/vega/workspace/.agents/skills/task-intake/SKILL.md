---
name: task-intake
description: Create or dispatch bounded work as a Task in the Task-only Fleet API. Load when a user requests new work or asks Mission Control to start a worker.
license: MIT
---

# Create a Task

Read workspace and target-repository instructions first. Run `fleet apply --help`
and `fleet version --json`; this skill targets `fleet/v1alpha1`, kind `Task`.
Use the same configuration as Mission Control, never a guessed default store.

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

Only `dispatch` is mutable in an existing Task spec. A new request needs a
new Task. Do not supply status, uid, generation or ownerReferences on create.
Setting dispatch false does not stop an already spawned session. Do not delete
another worker's Task as a retry strategy or treat an ambiguous runtime probe as
permission to spawn a replacement.
