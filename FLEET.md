# Fleet operator context

Operate Tasks with the installed `fleet` binary. Read AGENTS.md for working
rules, README.md for safe installation and cutover, ARCHITECTURE.md for the
overall structure, docs/task-controller/README.md for the resource, state
machine and interfaces, and crates/CONTRACTS.md for integration guarantees.

Inspect Tasks with `fleet list task --json`, `fleet get task NAME` and
`fleet status task NAME --json`. Apply desired spec with
`fleet apply -f FILE`. Run the manager with `fleet reconcile`. Never infer
absence from Unknown, or report a worker's claim as verified.

Keep private manifests and config outside this public checkout. Do not
deploy, reset data, restart services or alter an operational session merely
because an implementation branch is ready. A Task whose condition
`NeedsAttention` is True waits for a person: read its reason and message,
then decide; Fleet never abandons a Task by itself.
