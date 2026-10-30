---
name: task-status
description: Inspect Task state and worker evidence in Task-only Fleet. Load for progress questions, completion checks, unknown state or recovery investigation.
license: MIT
---

# Report current Task state

Run `fleet version --json` and use the Mission Control configuration.

```text
fleet list task --json
fleet status task NAME --json
fleet get task NAME
```

Read phase, conditions, observedGeneration, brief receipt, session reference and
result together. A phase alone is not sufficient evidence. Match observations
and the result's generation to the current Task; stale observations stay stale.

| Observation | Report or action |
| --- | --- |
| Pending with dispatch false | Work has not been authorized to start. |
| Spawning or Spawned | Startup is in progress; inspect conditions and receipts. |
| Briefed | Brief delivery is recorded; completion is not yet recorded. |
| Stalled or NeedsAttention | Explain the recorded condition. Check the runtime before proposing recovery. |
| Unknown or disconnected runtime | State is unknown. Do not create a replacement blindly. |
| Concluded with a result | A worker recorded a claim. Check the result and artifact evidence. Its live session still receives `fleet send`. |
| WorkerGone True | The recorded session is gone. Messages are not delivered. Never respawn it; new work needs a new Task. |
| Releasing | Deletion is waiting for session cleanup; do not bypass the finalizer. |

Inspect CLI errors and exit codes; never turn an unreadable store into an empty
queue. Do not poll in a tight agent loop or compete with the engine's manager.
A bounded `fleet reconcile --max-passes 10 --json` can advance an explicitly
requested manual check; it is not a background service.

## Worker completion

Read `fleet result --help` and the Task's brief receipt. A worker records its own
answer once, with the generation that was delivered:

```text
fleet result NAME --generation 1 --outcome shipped --summary "Verified result"
```

Replace the example generation and summary with actual evidence. Outcomes are
`shipped`, `stuck`, `failed` or `not-applicable`. Supply declared structured outputs
and authorized artifact URLs when required. Do not report another worker's result
for it. Fleet stores the claim; it does not verify a PR, CI or a deployment.
After reconciliation, Concluded keeps its session until deletion. Delete only
when the user authorizes cleanup and the conversation no longer needs it.
