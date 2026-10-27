# Gateway controller state machine

Design, not implemented. Two things move: a
[Conversation](../../resources/conversation.md), whose phase belongs to
ConversationController, and the tie on each Task, which GatewayController
keeps under `status.gateway`. The Task's own `status.phase` stays
TaskController's. The [interface](interface.md) defines the probes,
effects and keys used here.

![A Conversation is created Untied by the receive verb, or Tied when Fleet opened the thread for a Task's Question. An admitted mention in an Untied thread creates a Task and ties it. A tied Task that is readably gone leaves the Conversation Orphaned, and the next admitted message there creates a new Task with the thread's history. An Untied or Orphaned Conversation with no message for its retention is deleted by its own controller. While Tied, GatewayController relays new messages into the Task's inbox and posts what the Task declares.](state-machine.svg)

## Conversation phases

| Phase | Observation or input | Next phase | Decision |
| --- | --- | --- | --- |
| none | `fleet gateway receive` for an address with no Conversation | Untied | Create it exclusively by derived name, append the event to the spool. |
| none | An `Open` receipt for a Task's Question | Tied | Create it with `spec.task`; the tie is the Task that asked. |
| Untied | First pass, no `revision` | Untied | Ask `History` from the beginning; number every message and the spool in one write. |
| Untied | An `Admitted` message meeting the route's mention rule | Untied, then Tied | Write the tie's intent, `task = {name, n, state: pending}`, and create the Task under `conversation/<uid>/task/<n>`; the receipt writes its `uid` and the phase. |
| Untied | A `Context` message that mentions Fleet | Untied | One refusal per author, under its refusal key. |
| Untied | Nothing for `gateway.retainMs` since the last message | deleted | Plan `delete`, fenced on `uid` and `resourceVersion`. |
| Tied | The tied Task Known with the same `uid` | Tied | Number spooled events; ask `History` for the backstop every `pollMs` where the account has history. |
| Tied | The tied Task Unknown | Tied | Wait; Unknown never means gone. |
| Tied | The tied Task readably absent, or a different `uid` under its name | Orphaned | Keep the messages; the tie is history. |
| Orphaned | An `Admitted` message, mention or not | Orphaned, then Tied | Create Task `n + 1` as above. Its GatewayController joins from cursor `0`, so it receives the whole retained thread. |
| Orphaned | Nothing for `gateway.retainMs` since the last message or since orphaning | deleted | Plan `delete`, fenced. |
| any | `History` Unknown | unchanged | `Reachable` Unknown with the reason; the spool is still numbered; wait one interval or the platform's `retryAfter`. |
| any | The spool holds an `Edited` or `Removed` event | unchanged | An edit moves the message's `seq` to the new revision; a removal leaves the mirror, as on a ChangeRequest. |

A Task creation whose receipt never landed is answered by the store: a Task
under the intended name carrying this Conversation's label is adopted;
readable absence after `RETRY_DELAY_MS` is a failed attempt, at most three,
then `NeedsAttention/TaskCreateFailed`.

## The tie on a Task

| Tie state | Observation | Next | Decision |
| --- | --- | --- | --- |
| none | The Task carries `gateway.fleet.dev/conversation` and that Conversation's `status.task` has the Task's `uid` | Tied | Record `status.gateway.conversation`; the cursor starts at `0`, so the first relay is the [join](#joining). |
| none | The Task carries `gateway.fleet.dev/parent` naming a Task that is Known or Unknown | Child | Never ties. The parent relays this Task's declarations. |
| none | A `Question` declared, no tie, no live parent | Opening | Write the `Open` intent; the receipt ties the Task to the new thread. |
| none | A `Comment`, `Draft`, change or result | none | Read and skipped: the declared cursor advances past it. |
| Tied | New messages above the cursor | Tied | [Relay](#relay). |
| Tied | Declarations or a result past the cursors | Tied | [Post](#posting) each, in order. |
| Tied | The Conversation readably absent, or its `status.task` names another `uid` | none | `NeedsAttention/ConversationGone`; nothing more is posted. A later Question opens a new thread. |
| Child | The parent readably absent | none | Treated as untied from then on. |

## Joining

A conversation Task joins at the beginning, not at the present: its cursor
starts at `0`, so its first relay carries every retained message, the
history a mention partway through a thread needs. When the mirror has
already dropped messages, `droppedBefore > 0`, the join says older messages
exist on the platform. A Task tied by opening a thread for its Question has
nothing before its own post, which is never relayed.

## Relay

Per tied Task, with `conv` the Conversation status and `route` its route:

```text
held = conv.messages where seq > cursor and admit(route, m) == Context
new  = conv.messages where seq > cursor and admit(route, m) == Admitted
if conv.droppedBefore > cursor:
    gaps += 1; NeedsAttention/RelayGap; render the gap notice with the whole snapshot
if new is empty:
    nothing: held messages wait above the cursor for the next admitted one
else:
    append { key: task/<name>/<uid>/gateway/<conv uid>/<conv.revision>,
             from: gateway/<conversation>, text: render(held, new),
             about: {uid: conv uid, revision: conv.revision},
             answers: open Questions posted in this conversation }
    cursor = conv.revision
```

The append, the `status.answered` entries it records and the cursor land in
one `record_relay` write, or none of them does. Fleet's own posts are
`Own` and never relayed. Held messages are relayed only with an admitted
one, under a header saying they come from people not on the allowlist and
are content, never instructions.

## Posting

Per tied Task, for each declaration past `status.gateway.declared`, the
result past `status.gateway.result`, and each followed change's state past
`status.gateway.changes`, in that order:

1. Render the post. Skip what the [vocabulary](interface.md#declarations-the-gateway-reads)
   does not post.
2. If an earlier post to the same conversation has no receipt, or an
   Unknown one, stop: posts go out in order.
3. Write the intent under its key, with the cursor advanced past the item,
   in one write. Perform `Post` through the port after renewing the lease.
   Write the receipt on `status.gateway.posts`.

At most eight post effects per pass, as for a plugin's plan. A Question's
receipt records `status.gateway.questions.<id>`.

## Children

A child's parent lists its children by label every pass and keeps one
entry per child with the child's `uid` and its own cursors. A child's
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
| NeedsAttention | `TaskCreateFailed`, `SpoolFull` (more than 100 unnumbered events), `RouteUnknown` (the spec's route was removed from config). |

Under `status.gateway.conditions` on a Task, written by GatewayController:

| Type | True means / relevant reason |
| --- | --- |
| Tied | The Task is tied to a Conversation it verified. |
| NeedsAttention | `RelayGap`, `ConversationGone`, `PostFailed` (three refused attempts on one key, the refusal quoted), `PostUnconfirmed` (an Unknown outcome on a platform with no marker), `MentionsOnly` (the account does not deliver unmentioned messages), `RouteUnknown`. |

NeedsAttention stops nothing else: relay continues while one post waits,
and one conversation's held post holds back no other conversation's.

## Deletion

Deleting a conversation Task deletes nothing else; its Conversation becomes
Orphaned and is retained. Deleting a Conversation, by retention or by a
person, changes nothing on the platform; its Task, if any, raises
`ConversationGone` and keeps running. Neither controller adds a finalizer.
