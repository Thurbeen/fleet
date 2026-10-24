---
name: fleet-queue
description: Use when inspecting Fleet Tasks or changing their desired spec.
---

# Inspect and update Fleet Tasks

Use `fleet list task --json`, `fleet get task NAME` and
`fleet status task NAME --json` to read work. Task fields are documented in
docs/task-controller/resource.md; inspect supported kinds with
`fleet version --json`. Update
desired spec with `fleet apply -f FILE`; only `spec.dispatch` may change.
Deletion uses `fleet delete task NAME`; the finalizer holds the record until
its session is released.

A Task is one session. Its worker reports through `fleet result TASK` with
the brief's generation and declared output schema. A result is a claim, not
proof of an artifact landing. The controller owns status; never edit status
directly.
Unknown means the adapter could not tell and blocks the corresponding effect.

Run `fleet reconcile` to reconcile under the single manager lease. Do not
invent an independent polling program or a second status owner. Use the
requester's explicit config and data path, keeping private manifests out of
public commits. Follow orchestration/queue/POLICY.md and report the final
outcome with `fleet result`.
