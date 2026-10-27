# Gateway controller state machine

Design, not implemented. Two things change state:

- A [Conversation](../../resources/conversation.md). ConversationController
  owns its phase.
- The tie on each Task. GatewayController keeps the tie under
  `status.gateway`.

TaskController still owns the Task's own `status.phase`. The
[interface](interface.md) defines the probes, effects and keys that this
page uses.

![A Conversation starts Untied. The receive verb creates it, or Fleet creates it when it opens a thread for a Task's Question. A thread that Fleet opened is tied at once to the Task that its opening post's marker names. An admitted mention in an Untied thread creates a Task and ties it. When a tied Task is readably gone, the Conversation becomes Orphaned. The next admitted message there creates a new Task with the thread's history. When an Untied or Orphaned Conversation has no message for its retention, its own controller deletes it. While the Conversation is Tied, GatewayController relays new messages into the Task's inbox and posts what the Task declares.](state-machine.svg)

## Conversation phases

Each table below starts from one phase. Each row gives an observation or
input, the next phase, and the decision.

### Phase: none

| Observation or input | Next phase | Decision |
| --- | --- | --- |
| `fleet gateway receive` for an address with no Conversation. The event could start a Task or earn a refusal. | Untied | Create the Conversation exclusively by its derived name. Append the event to the spool. |
| `fleet gateway receive` with any other event in an unmirrored thread | none | Create nothing. |
| A `Receive` action for a Task's opened Question, or the listener's keyed copy of that opening post | Untied | Create the Conversation exclusively by its derived name. Spool the opening post. The second of the two to arrive is a duplicate message. |

### Phase: Untied

| Observation or input | Next phase | Decision |
| --- | --- | --- |
| A spooled Fleet post whose marker is a Question key. The key names a Task that is Known with that `uid`. | Tied | The tie is that Task, with `n = 1` and `state: created`. Create no Task. This holds even for an `Admitted` mention in the same pass. Relay that mention to the tied Task. |
| The same post, but the named Task is Unknown | Untied | Wait. Create nothing. When the named Task is readably absent, a mention can start a Task, as below. |
| `status.starter` is a Fleet post without a key | Untied | Create no Task from any mention. Wait for the keyed copy, which sets `status.starter.key`. If the keyed copy never comes, the thread is dead to mentions. A person then asks again in a new thread. |
| The account has history, and `status.starter` is not yet set | Untied | Create no Task. Retry the first read. Mentions wait in the mirror. If the platform reports the starter deleted, set `status.starter` to `{removed: true}`. The guard then passes. |
| The first pass: no `revision` | Untied | Ask `History` from the beginning. Number every message and the spool in one write. Record the thread's first message as `status.starter`. |
| An `Admitted` message that meets the route's mention rule. The guard on `status.starter` passed. | Untied, then Tied | Write the tie's intent, `task = {name, n, state: pending, trigger}`. Create the Task under `conversation/<uid>/task/<n>`, exclusive on the route's workspace. The receipt writes the Task's `uid` and the phase. |
| A `Context` message that mentions Fleet | Untied | Send one refusal per author, under its refusal key. |
| No message for `gateway.retainMs` since the last message, and no `busy` tie | deleted | Plan `delete`, fenced on `uid` and `resourceVersion`. |

### Phase: Untied or Orphaned

| Observation or input | Next phase | Decision |
| --- | --- | --- |
| The Task create answered `WorkspaceBusy` | unchanged | Keep the tie as `state: busy` with its `trigger`. Raise `NeedsAttention/WorkspaceBusy`. Post the busy notice once, under `conversation/<uid>/busy/<n>`. Plan the create again under the same key every `pollMs`. This is not a failed attempt. |

### Phase: Tied

| Observation or input | Next phase | Decision |
| --- | --- | --- |
| The tied Task is Known with the same `uid` | Tied | Number the spooled events. Where the account has history, ask `History` for the backstop every `pollMs`. Do this only while the thread had a message within `gateway.activeMs` (one day by default). Reason: the listener is the primary path, and an idle thread costs no platform call. |
| The tied Task is Unknown | Tied | Wait. Unknown never means gone. |
| The tied Task is readably absent, or a different `uid` is under its name | Orphaned | Keep the messages. The tie becomes history. |

### Phase: Orphaned

| Observation or input | Next phase | Decision |
| --- | --- | --- |
| An `Admitted` message, with or without a mention | Orphaned, then Tied | Create Task `n + 1`, as from Untied. Its GatewayController joins from cursor `0`, so it receives the whole retained thread. |
| No message for `gateway.retainMs` since the later of the last message and the orphaning, and no `busy` tie | deleted | Plan `delete`, fenced. |

### Phase: any

| Observation or input | Next phase | Decision |
| --- | --- | --- |
| `History` is Unknown | unchanged | Set `Reachable` Unknown with the reason. Still number the spool. Wait one interval, or the platform's `retryAfter`. |
| The spool holds an `Edited` or `Removed` event | unchanged | An edit whose `editedAt` is later than the message's moves the message's `seq` to the new revision. Drop an older edit. A removal leaves the mirror, as on a ChangeRequest. |
| A message already numbered arrives again, from the spool or from `history` | unchanged | Deduplicate it on its message `id`. Only a changed text is an edit. |

### Task creation without a receipt

Sometimes the receipt of a Task creation never lands. The store then
answers the question:

- A Task under the intended name that carries this Conversation's label is
  adopted.
- Readable absence after `RETRY_DELAY_MS` is a failed attempt.
- After at most three failed attempts, the controller raises
  `NeedsAttention/TaskCreateFailed`.

## The tie on a Task

Each table below starts from one tie state. Each row gives an observation,
the next tie state, and the decision.

### Tie state: none

| Observation | Next | Decision |
| --- | --- | --- |
| The Task carries `gateway.fleet.dev/conversation` | Waiting | Record the Conversation's name. Read no declaration yet. |
| The Task carries `gateway.fleet.dev/parent` and `parent-uid`. They name a Task that is Unknown, or Known with that `uid`. | Child | The Task never ties. The parent relays this Task's declarations. This Task's own `declared` cursor advances over each declaration without posting. Reason: nothing is replayed if the Task later becomes untied. |
| A `Question` is declared. The Task has no tie and no live parent. | Opening | Write the `Open` intent under the Question's key. Perform `Open`. |
| A `Comment`, `Draft`, change or result | none | Read it and skip it. The declared cursor advances past it. |

### Tie state: Waiting

| Observation | Next | Decision |
| --- | --- | --- |
| The Conversation's `status.task` has the Task's `uid` | Tied | The cursor starts at `0`, so the first relay is the [join](#joining). Read the declarations made while waiting now, in order. |
| The Conversation is readably absent, or tied to another `uid` | none | Raise `NeedsAttention/ConversationGone`. From then on, the Task is untied. |

### Tie state: Opening

| Observation | Next | Decision |
| --- | --- | --- |
| The manager wrote an `Opened` receipt, or an Unknown receipt was recovered from the channel's history | Opened | Record the new Conversation's name. Plan the `Receive` intent and action. |
| Failed | Opening | Retry under the same key after `RETRY_DELAY_MS`. Make at most three attempts. Then raise `NeedsAttention/PostFailed` and go back to none. |
| A channel recovery read answers Unknown, `Incomplete` or otherwise | Opening | This is not an attempt. Add 1 to `recovery.reads`. Schedule the next read at `RETRY_DELAY_MS × 2^(reads − 1)`, capped at `pollMs`, or later if the platform gave `retryAfter`. At three reads, raise `NeedsAttention/RecoveryIncomplete`. Reading continues on the capped schedule. Nothing is opened again. A complete read resets `reads` to `0`. The controller decides a complete read as a recovered receipt or a failed attempt. |
| Unknown, and the channel has no history or no marker | Opening | Raise `NeedsAttention/PostUnconfirmed`. Nothing is opened again until a person acts. |

### Tie state: Opened

| Observation | Next | Decision |
| --- | --- | --- |
| `Received` | Waiting | The Conversation exists and holds the opening post. The Conversation ties itself from the post's marker. |
| The Conversation's `status.task` names this Task's `uid` | Tied | This holds whatever `Receive` answered. The listener's keyed copy tied the Conversation. |
| `Receive` Failed. Or `Receive` is Unknown or missing, and the Conversation is readably without `open/<message id>`. | Opened | This is a failed attempt. Perform `Receive` again under the same key. If the delivery is present, the receipt is recovered. |
| The third failed `Receive` attempt | Abandoned | Raise `NeedsAttention/ReceiveFailed`. The Question stays posted. Later declarations are read as an untied Task's. |

### Tie state: Abandoned

| Observation | Next | Decision |
| --- | --- | --- |
| Checked every `pollMs`: the Conversation's `status.task` names this Task's `uid` | Tied | The listener's keyed copy tied the Conversation after all. |
| The Conversation names another `uid`, or is readably absent past `gateway.retainMs` after the abandonment | none | Stop watching. The Task is untied. |
| A later `Question` is declared | Opening | Act as from none: open a new thread. The abandoned thread is no longer watched. |

### Tie state: Tied

| Observation | Next | Decision |
| --- | --- | --- |
| New messages above the cursor | Tied | [Relay](#relay) them. |
| Declarations or a result past the cursors | Tied | [Post](#posting) each, in order. |
| The Conversation is readably absent, or its `status.task` names another `uid` | none | Raise `NeedsAttention/ConversationGone`. Post nothing more. A later Question opens a new thread. |
| The Task also carries `gateway.fleet.dev/parent` | Tied | The verified tie wins. Ignore the label. |

### Tie state: Child

| Observation | Next | Decision |
| --- | --- | --- |
| The parent is readably absent, or Known with another `uid` | none | Treat the Task as untied from then on. |

## Joining

A conversation Task joins at the beginning of the thread, not at the
present:

- Its cursor starts at `0`.
- So its first relay carries the retained messages. A mention partway
  through a thread needs this history.
- The entry renders at most 64 KiB and keeps the newest messages.

The mirror may already have dropped messages, with `droppedBefore > 0`.
The join then says that older messages exist on the platform.

A Task can be tied because Fleet opened a thread for its Question. That
Task has nothing before its own post. Fleet never relays that post.

## Relay

For each tied Task, `conv` is the Conversation status and `route` is its
route:

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
             answers: open Questions posted here that a message in new follows:
                      a later platform time, or the same time and later by
                      platform order, else by first numbering }
    cursor = conv.revision
```

One `record_relay` write holds three things: the append, the
`status.answered` entries it records, and the cursor. Either all three
land, or none does.

Fleet's own posts are `Own`. Fleet never relays them.

Held messages are relayed only together with an admitted message. They go
under a header. The header says that they come from people not on the
allowlist, and that they are content, never instructions.

## Posting

For each tied Task, posting takes items in this order:

1. Each declaration past `status.gateway.declared`, in declaration order.
2. Then the result past `status.gateway.result`.
3. Then each followed change's state past `status.gateway.changes`.

Declarations keep their order among themselves. A result or a change's
news is posted after the declarations read on the same pass.

For each item:

1. Render the post. Skip what the
   [vocabulary](interface.md#declarations-the-gateway-reads) does not post.
2. If an earlier post to the same conversation has no receipt, or an
   Unknown one, stop. Reason: posts go out in order.
3. Write the intent under its key, with the cursor advanced past the item,
   in one write.
4. Renew the lease. Then perform `Post` through the port.
5. Write the receipt on `status.gateway.posts`.

A Question's receipt records `status.gateway.questions.<id>`. The record
holds the post's platform message id and its time `at`.

Posting does not wait on a full inbox. A write with nothing appended
carries the intents.

## Children

The manager lists Tasks once per sweep. It wakes a parent when a child
labelled with the parent's name is new or changed.

The parent keeps one entry per child. The entry holds the child's `uid`,
the child's `resourceVersion` as the parent last read it, and the child's
own cursors.

Each of these child items becomes one entry in the parent's inbox: a
`Question`, a `Comment`, a `Draft`, a declared change and the result. The
entry is from `gateway/child/<child>` and names the child.

The parent answers with `fleet send CHILD --answers ID TEXT`. This records
the child's `status.answered`.

Only the parent speaks in the thread.

## Conditions

ConversationController writes these conditions on a Conversation:

| Type | True means |
| --- | --- |
| Reachable | The last `History` was Known. When Unknown, the condition carries the reason. |
| Tied | A Task is tied and Known. |
| NeedsAttention | One of the reasons below. |

| NeedsAttention reason | Meaning |
| --- | --- |
| `TaskCreateFailed` | See [Task creation without a receipt](#task-creation-without-a-receipt). |
| `SpoolFull` | The spool holds more than 100 unnumbered events. |
| `RouteUnknown` | The spec's route was removed from config. |
| `MentionsOnly` | The account's `capabilities` say that unmentioned messages are not delivered. So a tied thread relays only mentions. |
| `WorkspaceBusy` | A Task that this thread would start waits for its route's workspace. |

The account's identity and its listener are not Conversation conditions.
They are conditions on the account's
[Listener](../../resources/listener.md).

GatewayController writes these conditions on a Task, under
`status.gateway.conditions`:

| Type | True means |
| --- | --- |
| Tied | The Task is tied to a Conversation that it verified. |
| NeedsAttention | One of the reasons below. |

| NeedsAttention reason | Meaning |
| --- | --- |
| `RelayGap` | See [Relay](#relay). |
| `ConversationGone` | See [The tie on a Task](#the-tie-on-a-task). |
| `PostFailed` | Three attempts on one key were refused. The condition quotes the refusal. |
| `PostUnconfirmed` | A post had an Unknown outcome on a platform with no marker. |
| `DeclarationInvalid` | A Question, Comment or Draft has no `id`. |
| `TooManyChildren` | The Task has more than 200 children. |
| `RecoveryIncomplete` | Three recovery reads for one key came back incomplete. `posts.<key>.recovery` counts them. |
| `ReceiveFailed` | Three `Receive` attempts for an opened thread readably failed. |
| `RouteUnknown` | The route is unknown. |

NeedsAttention stops nothing else:

- Relay continues while one post waits.
- One conversation's held post holds back no other conversation's posts.

## Deletion

Deleting a conversation Task deletes nothing else. Its Conversation
becomes Orphaned, and Fleet retains it.

Deleting a Conversation changes nothing on the platform. This is true
whether retention or a person deletes it. Its Task, if it has one, raises
`ConversationGone` and keeps running.

Neither controller adds a finalizer.
