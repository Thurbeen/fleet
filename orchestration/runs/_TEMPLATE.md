# Run: `<YYYY-MM-DD>` — `<slug>`

> One run, one topic. `./scripts/queue.sh topic add` opens this file, and
> `dispatch`, `collect`, `shepherd` and `run` refresh the fenced block below
> from the queue's own records as the run goes on.
>
> **Everything outside that fence is yours.** Nothing rewrites it, nothing
> generates it, and it is the reason the file exists — the block says what
> happened, and these sections say what you decided and what it cost.

<!-- fleet:facts -->
<!-- fleet:facts:end -->

## Goal

What this run is meant to achieve, in your words.

## Playbook

`../playbooks/<name>.md`, or "ad hoc".

## Decisions worth keeping

The calls you made and why: what you serialized and on what condition, what you
dispatched together despite an overlap, what you decided not to do at all.

## What went wrong

Where the tooling or the plan failed, and what the next lead should do instead.
A defect written down here is the one that gets fixed.

## Outcome

What shipped, what is pending, what to follow up on. Update the relevant
`registry/context/<repo>.md` if this run changed a project's goals or relations.
