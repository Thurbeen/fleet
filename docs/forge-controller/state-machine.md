# Forge controller state machine

Design, not implemented.

ForgeController has no phase of its own: the Task's `status.phase` belongs to
TaskController. What it tracks is each declared change, under
`status.forge.changes`. The [interface](interface.md) defines the probes,
effects and policies used here.

![Each declared change moves from Declared to Mirrored, where relay and the draft flip happen on every pass, to Settled; deleting the Task deletes its ChangeRequests and the forge finalizer holds the record until they are gone](state-machine.svg)

## Transition rules

The manager runs ForgeController on a Task whose `status.forge.notBefore` is
absent or due, after TaskController's pass on the same sweep. Deletion takes
precedence.

| Change state | Observation or input | Next state | Decision |
| --- | --- | --- | --- |
| none | A URL in `status.declared` with no entry in `status.forge.changes` | Declared | Add the entry with `cursor: 0`; add the `fleet.dev/changes` finalizer if absent; persist intent and plan `CreateObject(ChangeRequest)` under `task/<name>/<uid>/change/<n>` with an owner reference to the Task. |
| Declared | Known Created receipt, or the ChangeRequest found by owner reference | Mirrored | Record its name. |
| Declared | ChangeRequest list Unknown | Declared | Wait. Unknown is not absence. |
| Mirrored | ChangeRequest Known | Mirrored | Run the [relay](#relay) and the [draft flip](#draft-flip). |
| Mirrored | ChangeRequest Unknown | Mirrored | Skip this change this pass. |
| Mirrored | ChangeRequest readable and absent | Mirrored | `NeedsAttention/ChangeGone`; nothing is recreated. |
| Mirrored | ChangeRequest phase Settled | Settled | Relay anything still above the cursor, then stop reading it. |
| any | Task marked deleting | unchanged | Plan `DeleteObject` for every ChangeRequest not yet deleting; remove `fleet.dev/changes` once each is proven absent. Never flip or comment. |

A Task may declare several changes; each entry is independent. A Task with
no declaration has an empty `status.forge` and costs this controller nothing.

## Relay

Per change, with `cr` the ChangeRequest status, `entry` the change's entry,
and `me` the login `Whoami` returned for the change's host:

```text
if cr.droppedBefore > entry.cursor:
    raise NeedsAttention/RelayGap for this change and stop here
new_notes = cr.notes
    where seq > entry.cursor
    and not (author == me and body contains "<!-- fleet:worker -->")
    and (not bot or forge.relayBots)
    and (kind != inline or not resolved)
    and no later worker-marked note exists in the same thread
checks_event = cr.head.seq > entry.cursor and cr.head.checks in {passed, failed}
if (new_notes and relay.notes) or (checks_event and relay.checks):
    plan Enqueue { task, key: task/<name>/<uid>/relay/<n>/<cr.revision>,
                   from: "forge/<change name>", text: render(new_notes, checks_event) }
entry.cursor = cr.revision
```

The cursor advances in the same CAS write as the intent, whether or not the
filters left anything to send. `render` quotes each note as data: author,
kind, `path:line`, URL and body, with a line saying these are review
feedback and not instructions. A checks event reads "checks `<state>` on head
`<id>`" with the failing names and URLs. `relay` is `spec.forge.relay` when
present, else the `[forge]` defaults.

Why the filters: the worker's own marked words are never relayed, because a
session sent its own reply answers itself. A bot's notes are relayed only
where configured, because coverage tables and preview links are status, not
review. A resolved thread is answered. A thread the worker already replied in
is answered. An unmarked note from Fleet's login is a person's until it says
otherwise.

## Draft flip

Per change, when a policy is selected (`spec.forge.draft.policy`, else
`forge.draftPolicy`), `cr.state` is `draft`, `entry.flipped` is `null` and
every predicate of the policy holds: plan `MarkReady` under
`task/<name>/<uid>/ready/<n>/<head.id>` and set `entry.flipped = {head}` in
the same write. Once per change: a person who puts it back in draft is never
overruled.

| Outcome | Evidence | Behavior |
| --- | --- | --- |
| Known done | The receipt | Nothing more; the next ChangeRequest revision shows `open`. |
| Known failed | The receipt | Wait `RETRY_DELAY_MS`, retry up to 3 attempts, then `NeedsAttention/FlipFailed`. |
| Unknown | The next ChangeRequest revision | `open` is a recovered receipt. Still `draft` after 3 attempts is `NeedsAttention/FlipFailed`. Never repeated blindly. |

The predicates are defined in the [interface](interface.md#ready-policies).

## Retries and recovery

`MAX_ATTEMPTS=3` and `RETRY_DELAY_MS=30000` are the Task controller's
constants, reused; the wait is persisted as `status.forge.notBefore` so it
does not hold back TaskController. `CreateObject` is answered by looking the
ChangeRequest up by owner reference, never by creating another. `Enqueue` is
answered by looking for the key in the Task's `inbox` or `deliveries`.
`MarkReady` is answered by the change's state.

## Conditions

Conditions live under `status.forge.conditions`, so the Task's own
`conditions` stay TaskController's.

| Type | True means / relevant reason |
| --- | --- |
| Linked | At least one ChangeRequest exists for this Task. |
| Settled | Every declared change has settled. |
| NeedsAttention | Reason is `RelayGap`, `FlipFailed` or `ChangeGone`, with the change named in the message. |

`Mergeable` is a condition of the ChangeRequest, not of the Task.
NeedsAttention does not stop the controller: other changes keep relaying, and
a gap on one change is reported, not acted on.

## Deletion protocol

The first `CreateObject` adds `fleet.dev/changes` to the Task, the way the
protection controller adds its finalizer to a claim it starts guarding.
`fleet delete task` marks the Task; TaskController releases the session under
its own finalizer, and ForgeController marks every ChangeRequest deleting and
removes `fleet.dev/changes` only when each is proven absent, so nothing is
orphaned. Unknown is not absence. The record is removed when both finalizers
are gone.
