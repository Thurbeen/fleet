# Run: `<YYYY-MM-DD>` — `<slug>`

> One run, one topic. `uv run fleet queue topic add` opens this file once, and
> `dispatch`, `collect`, `shepherd` and `run` keep the facts file linked below
> current from the queue's own records as the run goes on.
>
> **This file is yours.** Fleet created it and never writes it again. The facts
> say what happened; the sections below, all optional, say what you decided
> and what it cost.

<!-- fleet:facts-link -->

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
