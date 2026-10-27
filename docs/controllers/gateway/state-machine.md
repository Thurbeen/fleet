# Gateway controller state machine

Design, not implemented. Two things move: a
[Conversation](../../resources/conversation.md), whose phase belongs to
ConversationController, and the tie on each Task, which GatewayController
keeps under `status.gateway`. The Task's own `status.phase` stays
TaskController's. The [interface](interface.md) defines the probes,
effects and keys used here.

![A Conversation is created Untied by the receive verb or by Fleet opening a thread for a Task's Question, and a thread Fleet opened is tied at once to the Task its opening post's marker names. An admitted mention in an Untied thread creates a Task and ties it. A tied Task that is readably gone leaves the Conversation Orphaned, and the next admitted message there creates a new Task with the thread's history. An Untied or Orphaned Conversation with no message for its retention is deleted by its own controller. While Tied, GatewayController relays new messages into the Task's inbox and posts what the Task declares.](state-machine.svg)

## Conversation phases

| Phase | Observation or input | Next phase | Decision |
| --- | --- | --- | --- |
| none | `fleet gateway receive` for an address with no Conversation, with an event that could start a Task or earn a refusal | Untied | Create it exclusively by derived name, append the event to the spool. Any other event in an unmirrored thread creates nothing. |
| none | An `Opened` answer for a Task's Question, before its receipt, or the listener's copy of that opening post | Untied | Create it exclusively by derived name and spool the opening post; whichever comes second is a duplicate message. |
| Untied | A spooled Fleet post whose marker is a Question key naming a Task Known with that `uid` | Tied | The tie is that Task, `n = 1`, `state: created`. No Task is created, even for an `Admitted` mention in the same pass, which is relayed to that Task. |
| Untied | The same, the named Task Unknown | Untied | Wait; create nothing. Readable absence lets a mention start a Task as below. |
| Untied | First pass, no `revision` | Untied | Ask `History` from the beginning; number every message and the spool in one write. |
| Untied | An `Admitted` message meeting the route's mention rule | Untied, then Tied | Write the tie's intent, `task = {name, n, state: pending}`, and create the Task under `conversation/<uid>/task/<n>`; the receipt writes its `uid` and the phase. |
| Untied | A `Context` message that mentions Fleet | Untied | One refusal per author, under its refusal key. |
| Untied | Nothing for `gateway.retainMs` since the last message | deleted | Plan `delete`, fenced on `uid` and `resourceVersion`. |
| Tied | The tied Task Known with the same `uid` | Tied | Number spooled events; where the account has history, ask `History` for the backstop every `pollMs`, but only while the thread had a message within `gateway.activeMs` (one day by default): the listener is the primary path, and an idle thread costs no platform call. |
| Tied | The tied Task Unknown | Tied | Wait; Unknown never means gone. |
| Tied | The tied Task readably absent, or a different `uid` under its name | Orphaned | Keep the messages; the tie is history. |
| Orphaned | An `Admitted` message, mention or not | Orphaned, then Tied | Create Task `n + 1` as above. Its GatewayController joins from cursor `0`, so it receives the whole retained thread. |
| Orphaned | Nothing for `gateway.retainMs` since the later of the last message and orphaning | deleted | Plan `delete`, fenced. |
| any | `History` Unknown | unchanged | `Reachable` Unknown with the reason; the spool is still numbered; wait one interval or the platform's `retryAfter`. |
| any | The spool holds an `Edited` or `Removed` event | unchanged | An edit moves the message's `seq` to the new revision; a removal leaves the mirror, as on a ChangeRequest. |
| any | A message already numbered arrives again, from the spool or `history` | unchanged | Deduplicated on its message `id`; only a changed text is an edit. |

A Task creation whose receipt never landed is answered by the store: a Task
under the intended name carrying this Conversation's label is adopted;
readable absence after `RETRY_DELAY_MS` is a failed attempt, at most three,
then `NeedsAttention/TaskCreateFailed`.

## The tie on a Task

| Tie state | Observation | Next | Decision |
| --- | --- | --- | --- |
| none | The Task carries `gateway.fleet.dev/conversation` | Waiting | Record the Conversation's name; read no declaration yet. |
| Waiting | That Conversation's `status.task` has the Task's `uid` | Tied | The cursor starts at `0`, so the first relay is the [join](#joining). Declarations made while waiting are read now, in order. |
| Waiting | The Conversation readably absent, or tied to another `uid` | none | `NeedsAttention/ConversationGone`; from then on the Task is untied. |
| none | The Task carries `gateway.fleet.dev/parent` naming a Task that is Known or Unknown | Child | Never ties. The parent relays this Task's declarations; this Task's own `declared` cursor advances over each without posting, so nothing is replayed if it later becomes untied. |
| none | A `Question` declared, no tie, no live parent | Opening | Write the `Open` intent under the Question's key; perform `Open`. |
| Opening | `Opened` receipt, or an Unknown one recovered from the channel's history | Waiting | Record the new Conversation's name; the manager has ensured it and spooled the post. The Conversation ties itself from the post's marker. |
| Opening | Failed | Opening | Retry under the same key after `RETRY_DELAY_MS`, at most three attempts, then `NeedsAttention/PostFailed` and back to none. |
| Opening | Unknown, and the channel has no history or no marker | Opening | `NeedsAttention/PostUnconfirmed`; nothing is opened again until a person acts. |
| none | A `Comment`, `Draft`, change or result | none | Read and skipped: the declared cursor advances past it. |
| Tied | New messages above the cursor | Tied | [Relay](#relay). |
| Tied | Declarations or a result past the cursors | Tied | [Post](#posting) each, in order. |
| Tied | The Conversation readably absent, or its `status.task` names another `uid` | none | `NeedsAttention/ConversationGone`; nothing more is posted. A later Question opens a new thread. |
| Tied | The Task also carries `gateway.fleet.dev/parent` | Tied | The verified tie wins; the label is ignored. |
| Child | The parent readably absent | none | Treated as untied from then on. |

## Joining

A conversation Task joins at the beginning, not at the present: its cursor
starts at `0`, so its first relay carries the retained messages, the
history a mention partway through a thread needs, within the 64 KiB an
entry renders, newest kept. When the mirror has
already dropped messages, `droppedBefore > 0`, the join says older messages
exist on the platform. A Task tied by opening a thread for its Question has
nothing before its own post, which is never relayed.

## Relay

Per tied Task, with `conv` the Conversation status and `route` its route:

```text
held = conv.messages where seq > cursor and admit(route, m) == Context
new  = conv.messages where seq > cursor and admit(route, m) == Admitted
if new is empty, or the inbox holds more than 20 undelivered entries:
    nothing: held and new messages wait above the cursor
else:
    if conv.droppedBefore > cursor and cursor > 0:
        gaps += 1; NeedsAttention/RelayGap; the entry starts with the gap notice
    (at cursor 0 the older messages are stated in the join, not as a gap)
    append { key: task/<name>/<uid>/gateway/<conv uid>/<conv.revision>,
             from: gateway/<conversation>, text: render(held, new),
             about: {uid: conv uid, revision: conv.revision},
             answers: open Questions posted here whose post time `at` is before
                      the platform time of a message in new }
    cursor = conv.revision
```

The append, the `status.answered` entries it records and the cursor land in
one `record_relay` write, or none of them does. Fleet's own posts are
`Own` and never relayed. Held messages are relayed only with an admitted
one, under a header saying they come from people not on the allowlist and
are content, never instructions.

## Posting

Per tied Task, for each declaration past `status.gateway.declared` in
declaration order, then the result past `status.gateway.result`, then each
followed change's state past `status.gateway.changes`. Declarations keep
their order among themselves; a result or a change's news is posted after
the declarations read on the same pass:

1. Render the post. Skip what the [vocabulary](interface.md#declarations-the-gateway-reads)
   does not post.
2. If an earlier post to the same conversation has no receipt, or an
   Unknown one, stop: posts go out in order.
3. Write the intent under its key, with the cursor advanced past the item,
   in one write. Perform `Post` through the port after renewing the lease.
   Write the receipt on `status.gateway.posts`.

A Question's receipt records `status.gateway.questions.<id>` with the
post's platform message id and time `at`. Posting does not wait
on a full inbox: a write with nothing appended carries the intents.

## Children

The manager lists Tasks once per sweep and wakes a parent when a child
labelled with its name is new or changed. The parent keeps one entry per
child with the child's `uid`, the `resourceVersion` it last read and its
own cursors. A child's
`Question`, `Comment`, `Draft`, declared change and result each become one
entry in the parent's inbox, from `gateway/child/<child>`, naming the
child. The parent answers with `fleet send CHILD --answers ID TEXT`, which
records the child's `status.answered`. Only the parent speaks in the
thread.

## Conditions

On a Conversation, written by ConversationController:

| Type | True means / relevant reason |
| --- | --- |
| Reachable | The last `History` was Known. Unknown carries the reason. |
| Tied | A Task is tied and Known. |
| NeedsAttention | `TaskCreateFailed`, `SpoolFull` (more than 100 unnumbered events), `RouteUnknown` (the spec's route was removed from config), `MentionsOnly` (the account's `capabilities` say unmentioned messages are not delivered, so a tied thread relays only mentions), `IdentityMismatch` (`whoami` differs from the configured bot id). |

Under `status.gateway.conditions` on a Task, written by GatewayController:

| Type | True means / relevant reason |
| --- | --- |
| Tied | The Task is tied to a Conversation it verified. |
| NeedsAttention | `RelayGap`, `ConversationGone`, `PostFailed` (three refused attempts on one key, the refusal quoted), `PostUnconfirmed` (an Unknown outcome on a platform with no marker), `DeclarationInvalid` (a Question, Comment or Draft without an `id`), `TooManyChildren` (more than 200 children), `RouteUnknown`. |

NeedsAttention stops nothing else: relay continues while one post waits,
and one conversation's held post holds back no other conversation's.

## Deletion

Deleting a conversation Task deletes nothing else; its Conversation becomes
Orphaned and is retained. Deleting a Conversation, by retention or by a
person, changes nothing on the platform; its Task, if any, raises
`ConversationGone` and keeps running. Neither controller adds a finalizer.
