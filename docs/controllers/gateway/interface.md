# Gateway controller interface

Design, not implemented. This page defines two pure controllers and one
port. They follow the shape of the
[Forge controller interface](../forge/interface.md) and the
[ChangeRequest controller interface](../change-request/interface.md).

- ConversationController works over the
  [Conversation](../../resources/conversation.md) mirror.
- GatewayController works over the [Task](../../resources/task.md).

The manager owns I/O, persistence and process execution. The
[state machine](state-machine.md) defines how the values below change.

## Addresses

Above the port, one address format names a conversation everywhere:

```text
<platform>:<chat>[:<thread>]
slack:C0123ABCD:1730000000.000100      a Slack thread: channel, parent ts
slack:C0123ABCD                        a channel, where a thread is opened
telegram:-1001234567890:42             a forum topic: chat id, message_thread_id
discord:1300000000000000001            a Discord thread, itself a channel
```

The adapter parses and normalises an address. Nothing above the adapter
reads the parts of an address.

A Conversation's name is `conv-<12 hex of sha256(<account>/<address>)>`,
lowercased. So one thread names one object per account. A ChangeRequest is
named from its canonical ref in the same way.

The account carries the workspace, not the address. A Slack channel id is
paired with the account's workspace (team or enterprise id). Reason: Slack
documents message and user ids as unique within a workspace. Slack states
no wider scope for channel ids.

## Gateway port

The port is `fleet_api::gateway::Gateway`. fleet-api, fleet-gateway,
fleet-manager and fleet-cli hold it, as they hold the Forge port.

Every answer is Known or Unknown. A refused write is
`Known(EffectOutcome::Failed { why })`.

| Method | Input | Answer |
| --- | --- | --- |
| `operation_bound` | None | The worst-case duration of one call. The lease is sized to outlast it. |
| `parse` | An account and an address | The normalised `ConversationRef`. Unknown for an account that is not configured, or for an address the adapter cannot read. |
| `event` | An account's configuration, and a [delivery envelope](#deliveries) as the listener wrote it | `Known(Some(Delivery))`. `Known(None)` for a payload the gateway ignores. Unknown when the payload cannot be read. |
| `whoami` | An account | Fleet's bot identity, as the platform reports it. |
| `capabilities` | An account | What the platform gives Fleet on that account, as values. Unknown when the adapter cannot tell. |
| `history` | A `ConversationRef`, an optional `after` message id or `since` time, and a `limit` | The messages after `after`, or written at or after `since`, oldest first. |
| `open` | A channel `ConversationRef`, rendered text and an intent key | `Opened { conversation, message, at }`. Failed when refused. Unknown when the answer was lost. |
| `post` | A `ConversationRef`, rendered text and an intent key | `Posted { message, at }`. Failed when refused. Unknown when the answer was lost. |

### The event method

- The account's configuration includes Fleet's bot id on that account
  (`gateway.accounts.<name>.bot`).
- An ignored payload is, for example, a reaction or a presence change.
- `event` is pure. Everything it needs arrives as values, including the bot
  id. The bot id tells `fleet` authors and mentions apart.
- So the receive verb runs `event` without the lease.

### The whoami method

ListenerController compares the answer with the configured `bot`. When the
two differ, ListenerController holds the account's `Resolved` False with
`IdentityMismatch`. It does not trust either id silently.

### The capabilities method

`capabilities` answers these values:

- `history`: a bool.
- `marker`: `metadata`, `nonce { windowMs }` or `none`.
- `unmentioned`: whether the platform delivers messages that do not mention
  the bot.
- `threads`: whether the platform has threads in that kind of chat.

The [adapters](adapters.md#what-the-platforms-changed) say why each value
exists.

### The history method

- The `ConversationRef` is a thread, or a channel for a top-level read.
- A read with `after` returns at most `limit` messages. When more exist, it
  returns the newest ones, with a count of those it left out.
- Recovery uses a read with `since`. A `since` read is complete only when it
  left nothing out.
- A `since` read pages oldest-first from `since`, within the call bound.
- When a `since` read cannot read everything, it answers Unknown
  (`Incomplete`), not a window. So a missing key in a Known answer always
  means the post is absent.
- Otherwise, the answer is Unknown with `retryAfter`.
- The answer is `Unsupported` where the platform has no history for bots.
- Fleet's own posts carry their marker key when the platform keeps one.
- The first read of a thread asks for the newest 200 messages. Older
  messages are recorded as `droppedBefore`, and never mirrored.

A read of a thread also answers `starter`, the thread's first message:

- When the window does not reach the starter, the adapter reads the starter
  with its own call (Slack's parent, Discord's starter message).
- `starter` is `removed` when the platform reports the starter deleted.

### The open and post methods

- `open` makes a new thread. The thread's first post is the text, and the
  post carries the key as its marker.
- On some platforms, opening takes two platform calls. There the adapter
  keys both calls from the one intent key. It finishes a half-done open on a
  retry.
- A `post` carries the key as its marker.
- In `Posted`, `at` is the platform's own time for the post: Slack's `ts`,
  Telegram's `date`, Discord's `timestamp`, or the Teams activity time.

### Events

`Event` is one of:

| Event | Fields |
| --- | --- |
| `Message` | `id`, `conversation`, `author`, `mentionsFleet`, `at`, `text`, `key`, `startsThread`. |
| `Edited` | `id`, `conversation`, `text`, `editedAt`. |
| `Removed` | `id`, `conversation`. |

The `Message` fields:

- `id`: the platform's id, unique per conversation.
- `conversation`: a `ConversationRef`.
- `author`: `id`, `name` and `kind`. `kind` is `person`, `bot` or `fleet`.
- `key`: the marker key on a Fleet post. Absent on any other message.
- `startsThread`: true when the platform's payload says that the message
  starts its thread, as a Discord thread's starter or a Telegram topic's
  first message. Otherwise false.

A message that starts a thread and a reply in a thread are both `Message`.
The address says which thread.

The `Edited` event:

- It sets the message's `editedAt` and moves its `seq`.
- The message's `at`, the time the message was written, never changes.
- It is applied only when its `editedAt` is later than the mirror's. So two
  edits delivered out of order leave the newer text.

### Deliveries

A message's `id` names the message. It does not name one delivery of
something about the message. A message, its edits and its removal share one
`id`, and a platform redelivers each of them. So `event` answers a
`Delivery`, and the spool is keyed by the delivery, not by the message.

| Field | Meaning |
| --- | --- |
| `id` | The delivery id. Never the message id alone. |
| `verified` | How the listener authenticated the delivery, copied from the envelope. |
| `receivedAt` | When the listener received the delivery, from the envelope. |
| `event` | The `Message`, `Edited` or `Removed` above. |

The delivery `id`:

- Where the platform has its own identity for the event, the id is that
  identity. It is the same on every redelivery: Slack's `event_id`,
  Telegram's `update_id`, the Teams activity `id`.
- Otherwise the adapter derives the id from the event's own identity and
  version. Discord's Gateway has no event identity, so its ids are
  `create/<message id>`, `update/<message id>/<edited_timestamp>` and
  `delete/<message id>`.

The `verified` values:

- `connection`: an authenticated socket, as Socket Mode and Discord's
  Gateway are. These sign no single event.
- `signature`: a signed HTTPS delivery that the listener verified, as with
  the Events API's signing secret or a Teams JWT.
- `fleet`: a delivery that a `Receive` action spooled.
- `history`: a message that the backstop read.

The listener writes one envelope per delivery to the verb's stdin:

```json
{"verified": "connection", "receivedAt": 1730000300100, "payload": {"…": "the platform's raw event"}}
```

#### What the envelope records

- The account's `inbound` setting names the one method that its listener
  uses. The verb refuses an envelope that claims another method.
- The envelope is the listener's word. Fleet cannot check a signature that
  it never saw, and a connection-authenticated event carries no signature.
- The trust boundary is the local listener. The operator runs it as the
  manager's user. Any process able to run the verb could already write the
  store as that user.
- What the envelope adds is a record. Each numbered message keeps, as `via`,
  the `verified` and `receivedAt` of the delivery that first numbered it. So
  a Conversation shows how every message reached it after the spool is
  drained, and a listener cannot claim a method that its account was not
  configured for.
- A refused envelope is recorded on the account's
  [Listener](#listener-heartbeats), because no Conversation took it.
- Message identity stays the mirror's. Numbering, edits and removals are
  matched on the message `id`.

#### Mentions, changes and rendering

There is no separate `Mention` event. A mention is a `Message` with
`mentionsFleet`. Slack, Discord and Telegram all deliver ordinary messages
with the mention in their entities or text. Treating the mention as a field
keeps one numbering.

There is no merge, edit or delete method. Fleet never changes a post that it
made.

Rendered text is Fleet's Markdown subset: paragraphs, emphasis, code, links,
lists and quotes.

- The adapter converts rendered text to the platform's format.
- The adapter splits the text at the platform's limit into consecutive
  posts, under derived keys (`<key>/1`, `<key>/2`).
- A split post is posted when every part is posted.

## The receive verb

`fleet gateway receive --account NAME` reads one delivery envelope from
stdin. It is a manager verb, like `fleet send`. It takes no lease. The verb
does these steps:

1. Ask the adapter's `event` for the envelope.
   - `Known(None)` is an ignored payload. The verb exits zero.
   - Unknown exits 4, and the listener does not acknowledge.
2. Find the route that covers the event's channel. When no route covers the
   channel, the verb exits zero and writes nothing.
3. Derive the Conversation's name from the account and the event's address.
   - When the Conversation exists, go on.
   - When it does not exist, create it only for an event that could start a
     Task or earn a refusal under the route's [admission](#admission). The
     create is exclusive, with `spec = {account, address, route}`.
   - These events qualify: an `Admitted` message that meets the mention
     rule; a person's `Context` message that mentions Fleet; a Fleet post
     whose marker key is a Question's.
   - For any other event in an unmirrored thread, the verb exits zero and
     writes nothing. So a busy channel costs no objects.
4. Append the delivery to the Conversation's `status.received`. The verb uses
   the typed mutation `receive_event`, as `listener/<account>`, keyed by the
   [delivery id](#deliveries).
   - A key already in `received`, or in the newest `seenEvents`, is a no-op.
     So a redelivery is harmless.
   - An edit or a removal is a new delivery of the same message. The verb
     does not take it for a redelivery.

### Exit codes

The exit codes are the CLI's.

| Code | Meaning | The listener |
| --- | --- | --- |
| 0 | Written, a duplicate, or ignored. | Acknowledges. |
| 4 | Unknown: an unreadable payload or record. | Does not acknowledge. The platform redelivers under its own retry policy. |
| 5 | A conflict that the verb's own compare-and-swap retries did not resolve. | Does not acknowledge. The platform redelivers under its own retry policy. |
| 1 | Refused, for a reason below. | Does not acknowledge either. |

The verb refuses, with exit 1, in these cases:

- The spool already holds 100 unnumbered events.
- The Conversation is being deleted.
- The envelope's `verified` is not the account's `inbound` method.

Once the platform stops redelivering a refused event, the event is lost to
the spool. Where the account has history, the next backstop read recovers
it. The refusal is visible as the Conversation's `NeedsAttention/SpoolFull`.
The Conversation's controller raises that condition from the spool it reads.

### A crash between create and append

The creation and the append are not atomic. A crash between them leaves an
empty Conversation. Its controller fills it from `history` on its first
pass. Where there is no history, the controller retains it until its
retention passes.

### Threads Fleet opens

A thread that Fleet opens reaches its Conversation in the same way, through
a [`Receive` action](#returned-plan-1) in GatewayController's plan.

- The manager performs the action with the same `receive_event`, as
  `gateway-controller`.
- The action ensures the new thread's Conversation. It spools Fleet's
  opening post under the delivery id `open/<message id>`.
- Two copies of that post can arrive: the listener's keyed copy and the
  manager's. The first one creates the Conversation. The other one is a
  duplicate message and changes nothing.

### Listener heartbeats

`fleet gateway receive --account NAME --beat` reads one beat instead of an
envelope: `{"instance", "state": "connecting"|"connected"|"closing",
"digest", "sentAt", "lastDeliveryAt"}`.

The verb records the beat on the account's
[Listener](../../resources/listener.md) through the typed mutation
`record_beat`, as `listener/<account>`. When the manager has not yet created
the Listener, the verb creates it exclusively.

- `instance` names one listener process. Two processes on one account keep
  separate entries and never overwrite each other, as a replicated pair or
  the old and new halves of a restart do.
- The verb stamps the beat's `at` with its own clock. The verb keeps the
  listener's `sentAt` beside it, but `sentAt` judges nothing. So a
  listener's wrong clock cannot keep the listener looking alive.
- `digest` is the account digest that the listener started from. So Fleet
  can tell a listener on an old configuration from a current one.
- `fleet gateway account NAME --json` prints the digest, with what a
  listener needs to connect: the platform, the team, the bot id, the
  `inbound` method and the routes' channels.

A listener beats on every state change, and at least every
`gateway.beatMs`. The exit codes are the receive verb's.

#### Refused envelopes

The verb also records every envelope that it refuses or cannot read (exit 1
or 4) on the Listener, through `record_refusal`. It counts them by reason.
Reason: no Conversation took them.

Only a listener's own fault sets `refused.lastFaultAt` and turns
`InboundReady` False. A listener's own fault is one of these:

- an envelope that is not one (unreadable as an envelope),
- an envelope whose `verified` is not the account's `inbound`.

The following are one thread's or one event's trouble. The Listener counts
them, but does not judge them account-wide:

- a full spool, which is already the Conversation's `SpoolFull`,
- a deleting Conversation,
- a payload that the adapter cannot read.

The verb refuses an envelope for an account that the config does not have,
and records nothing, because that account has no Listener. That account's
absence is what `fleet gateway status` shows.

So a listener whose deliveries are all refused for its own fault is visible
while it keeps beating.

## ConversationController

```rust
pub struct ConversationInput<'a> {
    pub object: &'a Object,
    pub observations: ConversationObservations,
    pub now: Millis,
    pub defaults: GatewayDefaults,
}

pub fn due(object: &Object, now: Millis) -> bool;
pub fn probes(&self, input: &ConversationInput<'_>) -> Result<Vec<Probe>, StoreError>;
pub fn reconcile(&self, input: &ConversationInput<'_>) -> Result<Plan, StoreError>;
```

`defaults` carries these values:

- the route that the spec names, and its admission rules,
- the people table,
- the route's brief text. The manager reads it when it loads the config, so
  the controller reads no file.
- `gateway.retainMs`.

`due` holds when one of these is true:

- The spool is nonempty.
- `notBefore` passed.
- An intent awaits its receipt, and its next recovery read is due. That read
  is due `RETRY_DELAY_MS` after the intent, then at its `recovery.nextAt`.

Every plan sets `notBefore`:

- While the thread is active, `notBefore` is `pollMs`, so the history
  backstop runs.
- Afterwards it is also `pollMs`. Then an idle pass asks only the store
  `Object` probe for its Task.

So an idle Tied thread still notices that its Task is gone. The thread
becomes Orphaned, and retention retires it.

### Numbering

Numbering dedupes on the message `id`, whichever way a message arrived. A
message first seen in `history` and later in the spool is one message. So is
the reverse. Only a changed text is an edit.

When one copy carries a marker `key` and the other does not, the numbered
message keeps the key. So a listener's keyless copy of Fleet's opening post
cannot hide the marker that the tie is read from.

### Marker tie

Terms used in this section and the next ones:

- A **keyed post** is a Fleet post that carries a marker key.
- A **Question key** is a marker key of the form
  `task/<name>/<uid>/gateway/question/<id>`.
- The **starter** is the thread's first message.

A keyed post with a Question key ties a Conversation to that Task. The
Conversation must be Untied and have no tie. The tie is
`{name, uid, n: 1, state: created}`, once the Task is Known with that `uid`.

This is how a thread that Fleet opened for a Question is tied. The tie is
not part of the fixed spec. The marker tie works whichever of the listener
and the manager created the Conversation.

### Marker tie before a mention

A marker tie takes precedence. In a pass that sees both a Question key and
an `Admitted` mention, the controller:

1. ties the thread to the Task that the Question key names,
2. creates no Task,
3. relays the mention to that Task.

When the Question key's Task is Unknown in that pass, the controller creates
nothing, and the pass waits. Only when that Task is readably absent may the
mention start a Task.

### Starter guard

A mention never starts a Task in a thread whose starter is a Fleet post
without a key. Reason: Fleet started that thread for some Task. The tie
comes from the keyed copy.

The guard does not apply in a chat without threads.

How the controller finds the starter:

- `history` answers the starter explicitly, as `starter`.
- When the newest 200 messages do not reach the starter, the starter is read
  on its own. So the starter is known however long the thread is.
- The starter is never the first message of the window.

How the controller records the starter:

- The controller records it in `status.starter`, as `{id, kind, key}`, the
  first time it numbers it. `status.starter` is outside the rotating message
  list.
- A keyed copy numbered later sets the `key` there.
- The guard reads `status.starter`, never `messages`. So the guard holds
  however many replies rotate past the starter.

### When the starter is not known yet

Where the account has history, no mention starts a Task before
`status.starter` is set. A first read that answered Unknown is retried, and
the mention waits in the mirror.

A starter that the platform reports deleted is recorded as
`{removed: true}`, and the guard passes. Reasons:

- No keyed copy of a deleted post can arrive to tie the thread.
- Refusing the mention would leave the thread dead to mentions without a
  word.

Where the account has no history, the guard applies once a starter is
numbered. When Fleet opens a thread there:

1. GatewayController gets the `Opened` receipt.
2. GatewayController plans a `Receive` action.
3. The `Receive` action numbers the opening post with its key.

### The gap between open and Receive

The manager narrows the gap between `open` and `Receive`. After it writes an
`Opened` receipt, it reconciles that Task's GatewayController again. It does
this in the same pass and under the same lease. So `Receive` is planned and
performed before the pass ends.

The gap is narrowed, not closed, because the listener runs on its own. A
reply finds the thread unmirrored in these cases:

- it is delivered between the moment `open` returns and the `Receive`
  write,
- it is delivered in a longer gap, after a crash between the two.

On a platform with neither history nor marker, the gap has these effects:

1. A reply to the Question in the new thread is an event in an unmirrored
   thread. It is acknowledged and dropped.
2. A mention may start a Task there.
3. The opening post, numbered later, is never relayed.
4. The Question's Task finds the thread tied to another Task, and raises
   `ConversationGone`.

### When the keyed copy never arrives

The keyed copy can fail to arrive in these cases:

- the marker was lost,
- recovery could not use the marker.

In both cases, the opening Task reports `PostUnconfirmed`.

Then the thread is deliberately dead to mentions. Fleet does not guess
which Task the thread belongs to. The person asks again in a new thread.

### Requested probes

| State | Probes |
| --- | --- |
| No `revision` yet, or a backstop due | `History { conversation, after }`. `after` is absent on the first pass. |
| A tie recorded, or a Question marker naming a Task | `Object { kind: Task, name }`, to see whether that Task exists with its `uid`. |
| A pass that will call the platform, at most once per account per sweep | `Capabilities { account }`, cached by the manager for the sweep. |
| A refusal's intent without a settled receipt, `RETRY_DELAY_MS` after it, then at its `recovery.nextAt` | `History { conversation, since }` from before the intent, to look for the key. |
| Settled into deletion, or deleting | None. |

About `Capabilities`:

- A pass calls the platform for the first read, the backstop, a recovery
  read or a refusal post.
- An idle pass does not ask `Capabilities`. It keeps the `MentionsOnly`
  reason that it last had. A probe that was not asked is never read as
  absence.
- Fleet's identity on the account is the [Listener's](#listenercontroller)
  to check.

### Returned plan

- `status` carries the whole Conversation status. The same write drains from
  the spool what it numbered (`record_received`).
- `creates` carries at most one Task, from the route, when the
  [state machine](state-machine.md) says to start one. Its intent is the tie,
  written in the same status.
- `effects` carries at most one `Post` per author, the polite refusal. It
  also carries at most one busy notice per tie number, keyed
  `conversation/<conv uid>/busy/<n>`.
- `delete: true` retires the object after retention. The delete is fenced on
  `uid` and `resourceVersion` under the lease.

#### Changes to Plan and Probe

`Plan` and `Probe` are the built-in types in `fleet_api::plan`. As designed,
they change in these ways, each with its architecture rule:

- `Plan.creates`. Among the built-ins, only this controller's plans carry
  it. The same manager create verb that a plugin plan's `creates` uses
  performs it. Today it exists only in the plugin protocol types.
- `Plan.appends`. Today only ForgeController asks for it. GatewayController
  also asks for it.
- `Plan.delete`. Today only ChangeRequestController asks for it.
  ConversationController also asks for it.
- `Probe::List { kind, labels }`. The store answers it, as the plugin host
  answers its `list` probe.
- `Probe::History`, `Probe::Capabilities` and
  `Probe::GatewayWhoami { account }`. The Gateway port answers them. The
  existing `Whoami { host }` stays the Forge port's.
- The actions `Open` and `Post`, performed through the Gateway port.
- The internal action `Receive { account, address, route, delivery }`. The
  manager performs it against the store through `receive_event`, as the
  receive verb does, and never against a platform. It is the one way that a
  plan reaches a Conversation's spool. See
  [its ordering and recovery](#returned-plan-1).
- An optional `exclusive` precondition on a create, `{workspace}`. The
  manager holds the lease and reads the store's Tasks immediately before
  that create. It performs the create only when no Task has the same
  `spec.workspace.dir`. Otherwise it answers Failed `WorkspaceBusy`.
  ConversationController sets the precondition for a route whose workspace
  is a shared directory. See [workspaces](#workspaces).

The kind names `Conversation` and `Listener` are reserved for this design.
Once the kinds are built in, a plugin manifest that claims either kind is
refused, as one that claims `Task` is refused today.

#### The Task it creates

ConversationController creates this Task:

```json
{
  "apiVersion": "fleet/v1alpha1",
  "kind": "Task",
  "metadata": {
    "name": "conv-3f9a1c0d2b7e-1",
    "labels": {"gateway.fleet.dev/conversation": "conv-3f9a1c0d2b7e",
               "gateway.fleet.dev/conversation-uid": "<conversation uid>",
               "gateway.fleet.dev/route": "eng"}
  },
  "spec": {
    "configRef": {"name": "conversation"},
    "brief": "<the route's brief file, as read when the Task is created>",
    "workspace": {"dir": "/work/conversations"},
    "dispatch": true
  }
}
```

- The Task's name is the Conversation's name with the tie's number. The
  number grows each time an Orphaned thread starts a new Task.
- Creation is exclusive by name.
- An existing Task under that name is adopted when its
  `gateway.fleet.dev/conversation-uid` label is this Conversation's `uid`.
  This is how a crash between intent and receipt recovers.
- A Task under that name labelled with another `uid` belongs to an earlier
  incarnation of the Conversation. That Conversation was deleted and
  recreated while its Task lived. Such a Task is never adopted, and the
  tie's number moves past it.
- The store sets the `gateway.fleet.dev/conversation` and
  `conversation-uid` labels only through this create. It refuses them from
  `apply`, as it refuses `fleet.dev/plugin`.

### Workspaces

Fleet's rule is one isolated workspace per Task. But a route gives every
Task it creates the same `workspace`. Task specs today name a directory, and
nothing provisions one. So a route's workspace is a shared directory. Two
conversation Tasks that write through it at once could corrupt each other's
work.

So ConversationController serialises a route. Every create that it plans
carries the `exclusive: {workspace}` precondition.

- The manager holds the lease. Immediately before each such create, it reads
  the store's Tasks, not the sweep's listing.
- The manager refuses the create with `WorkspaceBusy` while any Task has the
  same directory, whoever created that Task.
- Directories are compared after lexical normalisation: no trailing slash,
  no `.` or `..` segment.
- The config is refused at load when one route's directory equals, contains
  or lies inside another's.
- The manager performs creates one at a time under the lease. So two threads
  of one route cannot both pass the check.

#### Limits of the workspace check

The check guards a conversation Task from every Task already there. It does
not stop a Task applied, or created by a plugin, into the same directory
later. Fleet has no rule that refuses that today. So a route's directory
should be one that no other Task names, and the route's documentation says
so. Symbolic links are the operator's to avoid.

#### A busy create

A refused create is not a failed attempt, because nothing was created.

- The tie is kept as `{n, state: busy, trigger}`. `trigger` is the id of the
  message that asked for the Task. So the start is remembered however many
  messages rotate past it.
- The thread raises `NeedsAttention/WorkspaceBusy`.
- The create is planned again under the same key every `pollMs`.
- The first pass after the workspace's Task is deleted starts the Task,
  joined with the whole thread.
- The person who asked is told once, by a post keyed
  `conversation/<conv uid>/busy/<n>`, that the request is queued behind
  another conversation. So a waiting thread is never silent.
- A busy tie holds off the Conversation's retention, because the person was
  told that the request is queued.
- A trigger message that is later removed withdraws the busy tie and starts
  nothing, because no request remains.

#### One conversation Task per route at a time

A Task keeps its directory until it is deleted, Concluded or not. Nothing
in this design deletes a conversation Task automatically. So a route runs
one conversation Task at a time, and the next one waits for a person to
delete the last one.

A route whose requests come often, such as an alert channel, is usable only
once per-Task provisioned workspaces exist. That is a change of its own.
This design waits for it rather than works around it.

## ListenerController

Every call that Fleet makes is outbound. Reaching the platform's API proves
nothing about inbound delivery. A listener may never have started. It may
have connected with an old configuration, or it may have stalled.

So each configured account has a [Listener](../../resources/listener.md).
The account's inbound health is what the listener reports, judged against
the configuration that Fleet runs.

```rust
pub struct ListenerInput<'a> {
    pub object: &'a Object,
    pub observations: ListenerObservations,
    pub now: Millis,
    pub defaults: GatewayDefaults,
}

pub fn due(object: &Object, now: Millis) -> bool;
pub fn probes(&self, input: &ListenerInput<'_>) -> Result<Vec<Probe>, StoreError>;
pub fn reconcile(&self, input: &ListenerInput<'_>) -> Result<Plan, StoreError>;
```

`defaults` carries the account's configuration and its digest. The manager
computes the digest when it loads the config. It is the same digest that
`fleet gateway account` prints.

`due` holds when one of these is true:

- `notBefore` passed.
- `status.beatRevision` is above `status.seen`. Every `record_beat` and
  `record_refusal` increments `status.beatRevision`. `status.seen` is the
  revision last judged.

The probes are `Capabilities { account }` and `GatewayWhoami { account }`,
every `gateway.pollMs`, from the sweep's per-account cache.

The plan is status alone: three conditions, and `delete: true` for a
Listener whose account left the config.

| Condition | True when | False or Unknown, by reason |
| --- | --- | --- |
| `Accepted` | The account in the loaded config is valid, and some route uses it. `status.accepted` holds its digest and when it was loaded. | `NoRoute`: no route names the account. |
| `Resolved` | `whoami` is Known and is the configured `bot`, and `capabilities` is Known. | False `IdentityMismatch`, the two ids quoted. Unknown with the port's reason. |
| `InboundReady` | Some instance in `status.beats` is `connected`, its `digest` is `status.accepted.digest`, and its verb-stamped `at` is within three `gateway.beatMs` of now. Also, no listener fault was refused within that time. | `NoListener`: never a beat. `Connecting`. `Closing`. `ListenerStale`: the freshest beat's age quoted. `ListenerConfigOld`: no fresh instance on the accepted digest. `DeliveriesRefused`: the last refusal's reason quoted. |

`notBefore` is the earlier of the next `pollMs` and the moment the freshest
beat goes stale. So when a listener stops beating, `InboundReady` turns
False within one sweep of three missed beats.

- One instance's `closing` beat does not hide another instance's fresh
  `connected` beat.
- A dead instance's last `connected` beat goes stale on its own `at`.

A listener cannot report a platform's own subscription settings. A
connected, current listener whose app is not subscribed to message events
receives nothing. Each instance's `lastDeliveryAt` is shown beside the
condition, and `fleet gateway status` prints it for each account. So a
connected listener that has delivered nothing is visible as such. Fleet does
not guess why.

## GatewayController

```rust
pub struct GatewayInput<'a> {
    pub task: &'a Object,
    pub observations: GatewayObservations,
    pub now: Millis,
    pub defaults: GatewayDefaults,
}

pub fn due(task: &Object, now: Millis) -> bool;
pub fn probes(&self, input: &GatewayInput<'_>) -> Result<Vec<Probe>, StoreError>;
pub fn reconcile(&self, input: &GatewayInput<'_>) -> Result<Plan, StoreError>;
```

`due` holds in these cases:

- For a tied Task, when its Conversation's `revision` is above
  `status.gateway.seen`. The manager lists Conversations once per sweep, and
  answers from that listing.
- For a Task whose tie is `opening`, `opened` or `waiting`.
- For an `abandoned` tie, every `pollMs`.
- For a Task with an unsettled post, once its next recovery read is due:
  `RETRY_DELAY_MS` after the intent, then at its `recovery.nextAt`.
- When `status.declared` or `status.result` holds something past the
  controller's cursors.
- When one of the Task's children changed.

A quiet tied Task costs nothing.

### Changed children

The manager lists Tasks once per sweep. It indexes them by their
`gateway.fleet.dev/parent` and `parent-uid` labels. It marks a parent due
when a child is new, or when a child's `resourceVersion` is above the one
that `status.gateway.children` recorded for it.

The `List` probe is answered from that one listing. So children cost one
store list per sweep, not one per Task.

### Requested probes

| Task state | Probes |
| --- | --- |
| Labelled `gateway.fleet.dev/conversation`, or `status.gateway.conversation` recorded | `Object { kind: Conversation, name }`. |
| A `ChangeRequest` declaration and a tie | `Object { kind: ChangeRequest, name }`, for its news. |
| Due for its children | `List { kind: Task, labels: {gateway.fleet.dev/parent: <name>, gateway.fleet.dev/parent-uid: <uid>} }`, answered from the sweep's listing. |
| Carrying `gateway.fleet.dev/parent` | `Object { kind: Task, name }` for its parent. |
| A `Question` on an untied Task with no live parent | None: the route is config. |
| A `Post` intent without a settled receipt, `RETRY_DELAY_MS` after it, then at its `recovery.nextAt` | `History { conversation, since }` from before the intent, to look for the key. |
| Opening, with an `Open` intent without a settled receipt, `RETRY_DELAY_MS` after it, then at its `recovery.nextAt` | `History { conversation: <channel>, since }` from before the intent, for a top-level post that carries the key. |
| Deleting | None. |

GatewayController asks the `Post` recovery read itself. So recovery does not
depend on the Conversation's backstop cadence.

### Fields this controller owns

`status.gateway`:

| Field | Meaning |
| --- | --- |
| `conversation` | `{name, uid, state}` of the Conversation. The states are listed below. |
| `seen` | The Conversation `revision` last read. So a tied Task is due only when its Conversation moved. |
| `cursor` | The Conversation `revision` relayed through. It starts at `0`, so the first relay is the whole retained thread. It advances only when an entry is appended. Messages that may not start a turn wait above it. |
| `declared` | How many items of `status.declared` were read. Each item is read once, in order. |
| `result` | The result generation posted, if any. |
| `changes` | Per ChangeRequest name, the `state.seq` posted through. |
| `children` | Per child Task name, `{uid, seen, declared, result}`: the child's `uid` on first sight, the `resourceVersion` last read, and the child's own cursors. |
| `posts` | Per intent key, `{intent, receipt, attempts, recovery}`, for the post, open and `Receive` effects, apart from TaskController's records. |
| `posted` | Every Question, Comment and Draft id posted, by kind, for the life of the Task. |
| `questions` | Per Question id, `{key, conversation, message, at, firstSeq, position}`, once posted. |
| `gaps` | How many times a relay, past the join, found `droppedBefore` above the cursor. |
| `conditions` | `Tied`, `NeedsAttention`. |

#### Tie states

The `state` of `conversation` is one of:

- `opening`: from an `Open` intent until its receipt.
- `opened`: from the receipt until its `Receive` action's receipt.
- `waiting`: until the Conversation's `status.task` names this Task's `uid`.
- `tied`: from then on.
- `abandoned`: after `ReceiveFailed`. It lasts until the Conversation ties
  itself to this Task, or until a later Question opens another thread.

A Task that carries `gateway.fleet.dev/conversation` starts `waiting`.

While the tie is `opening`, `opened` or `waiting`, the Task reads no
declaration past the Question that opened. So nothing that the Task says
before its tie is verified is skipped. The Task reads those declarations in
order once tied.

#### More about the fields

- `posts`: `attempts` counts performed calls. `recovery` is
  `{reads, nextAt}`, and counts the
  [recovery reads](#effect-and-recovery-keys) that came back incomplete.
  `posts` keeps the newest 100 settled records and every unsettled one.
- `posted`: so an id reused later is never posted again, after its record in
  `posts` rotated out. Bounded by the declarations that a Task may hold.
- `questions`: the last two fields are filled in when the mirror first
  numbers the post, and are kept after the post rotates out. The platform
  message id and time come from the post's receipt. So answers are ordered
  on the platform's own clock, and need no marker.

`status.answered` has no owner. It is a map from Question id to
`{key, from, at}`. Only the typed inbox append writes it, when an entry
carries `answers`. The first writer wins. An entry is never removed while
the Task lives.

#### Bounds on the maps

Every map here is bounded by what the Task declared:

- `questions`, `posted` and `status.answered`, by its declared Questions,
  Comments and Drafts.
- `changes`, by its declared changes.
- Both under the per-Task declarations cap that the
  [open questions](README.md#open-questions-and-recommendations) recommend.
- `children`, by the 200 that a `List` probe answers. The probe selects on
  both the parent's name and its `uid`, so children made for an earlier Task
  of that name are not counted.
- Past 200, the probe is Unknown with `TooMany`. No child is relayed, and
  the parent raises `NeedsAttention/TooManyChildren`.

### Declarations the Gateway reads

Every item of `status.declared` is a JSON object with a string `kind`.
Fleet validates nothing else at `declare`. The Gateway reads these kinds and
ignores the rest:

| Kind | Fields | Tied Task | Untied Task, no parent | Child |
| --- | --- | --- | --- | --- |
| `Question` | `id` (string, unique for the Task), `text` (Markdown) | Posted once, under `…/gateway/question/<id>`. | Opens a thread in the questions route's channel, then is tied to it. | Relayed to the parent's inbox. |
| `Comment` | `id` (string, unique for the Task), `text` (Markdown) | Posted once, under `…/gateway/comment/<id>`. | Skipped, and never posted later. | Relayed to the parent's inbox. |
| `Draft` | `id`, `title`, `text` (Markdown) or `url` | Posted once as a draft for review, under `…/gateway/draft/<id>`. | Skipped. | Relayed to the parent's inbox. |
| `ChangeRequest` | `url`, `name`, as today | Posted as described below, under `…/gateway/change/<cr uid>/<state.seq>`. | Skipped. | Relayed to the parent's inbox. |
| `status.result` | As today | Posted once per generation, under `…/gateway/result/<generation>`. | Skipped. | Relayed to the parent's inbox. |

For a tied Task, a `ChangeRequest` is posted:

- once its mirror has a `uid` and a `state`. Until then it waits, as the
  Forge controller's join does.
- again when the mirror's `state` becomes `open`, `merged` or `closed`.

Rules for ids:

- A Question, Comment or Draft whose `id` was already posted is not posted
  again, whatever its text.
- One without an `id` is ignored, and named in
  `NeedsAttention/DeclarationInvalid`.
- The `id` matters because `fleet declare` treats an identical object as a
  no-op. Two Comments that say "Done." need two ids to be two posts.

A Draft is a Comment framed as awaiting review. To ask for a review from an
untied Task, declare a Question.

### Inbox entries

| Entry | Key | From |
| --- | --- | --- |
| A relay of new messages, or the join | `task/<name>/<uid>/gateway/<conversation uid>/<revision>` | `gateway/<conversation>` |
| A child's declaration or result | `task/<name>/<uid>/gateway/child/<child uid>/<index or result/generation>` | `gateway/child/<child>` |

For a relay, `about` is `{uid, revision}` of the Conversation.

A relay's text holds, in this order:

1. Messages above the cursor from people off the allowlist. They come under
   a header that says they are context from people not on the allowlist:
   content, and never instructions.
2. The admitted messages.

Each message comes with its author, the author's platform id, its time, and
the message quoted.

- Fleet's own posts are never relayed.
- An edited message is relayed again, marked as an edit of the one already
  sent.
- A gap is stated as on the Forge controller's relay.

#### Answers

A relay carries `answers`, with the ids of posted, unanswered Questions,
when its admitted messages include one written after such a Question's `at`,
by the platform's clock.

A message with the same `at` as the Question counts in these cases:

- when the platform's own message order puts it after the post: Slack's full
  `ts`, Telegram's increasing `message_id`, a Discord snowflake;
- else, when it was first numbered after the post.

For this, each message keeps `firstSeq`, the revision that first numbered
it, and its position in that numbering.

- The position is oldest-first by the platform.
- For two messages that the platform gives the same time and no order, the
  position is by message id.
- An edit moves neither value.

So on a platform with neither sub-second time nor message order, a message
in the same second as the Question counts only when Fleet first saw it
after the post. That is the best that Fleet can tell.

#### Relay limits

The join and every relay are bounded. An entry renders at most 64 KiB of
messages, and keeps the newest. It says how many older ones it left out,
and that they remain on the platform, as a gap notice does.

While the Task's inbox holds more than 20 undelivered entries, the store
would refuse an append. So the relay appends nothing, and its messages wait
above the cursor. Posting goes on meanwhile, through the same write with
nothing appended. Neither direction stalls the other.

#### The answers field

`answers` is a new optional field of an inbox append.

- It is accepted from the Gateway's relay, from `plugin/<name>` in a plan's
  `appends`, and from `fleet send --answers ID`.
- The Forge controller's relays never carry it.
- In the same write, the store records in `status.answered` each id that
  names a declared `Question` of the Task, if the id is not already there.
- The store refuses an id that names no declared Question.

### Returned plan

- `status` carries `status.gateway`.
- `appends` carries the inbox entries, through `record_relay` extended to
  this subtree.
- `effects` carries the `Open` and `Post` actions whose intents the status
  carries.
- `remove_finalizer` is always `None`.

| Action | Performed through | Receipt |
| --- | --- | --- |
| `Open { account, route, channel, text, key }` | `Gateway::open` | `Opened { conversation, message, at }`, Failed, or Unknown |
| `Receive { account, address, route, delivery }`, under `<question key>/receive` | The store, never a platform: `receive_event` with exclusive creation, exactly as [the receive verb](#the-receive-verb) performs it. The delivery is the opening post, under `open/<message id>`, with `verified: fleet`. | `Received` (written or a duplicate). Failed when the store refuses it (a full spool, a deleting Conversation). Or Unknown. |
| `Post { conversation, text, key }` | `Gateway::post` | `Posted { message, at }`, Failed, or Unknown |

#### Reaching a Question's thread

A Question's thread is reached in two keyed steps. GatewayController plans
each step. Each step has its intent before and its receipt after.

1. The manager writes the `Open` answer as the `Opened` receipt, and nothing
   else, as after any effect.
2. GatewayController's next pass reads that receipt, or recovers one from
   channel history. It moves the tie to `opened`. It plans the `Receive`
   intent and action, and builds the delivery from the receipt and the
   Question's own text.
3. The manager performs `Receive` and writes `Received`. That moves the tie
   to `waiting`.

The two paths differ only in where the receipt came from.

`Receive` is the only internal action. It changes nothing outside the
store.

#### Recovering a Receive

An Unknown answer, or a crash before the receipt, is answered by
observation, as for any effect. The next pass asks the `Object` probe for
the Conversation.

- A Conversation whose `received` or `seenEvents` holds the delivery id
  `open/<message id>` is the recovered receipt.
- A Conversation readably without it is a failed attempt. The action is
  performed again under the same key.

A second attempt that races a first one landing late is a duplicate
delivery, and changes nothing.

#### A Receive that does not succeed

Whatever `Receive` answers, a Conversation whose `status.task` already names
this Task's `uid` moves the tie straight to `tied`. The listener's keyed
copy did the work.

Otherwise, after `MAX_ATTEMPTS` Failed or readably failed attempts, the
controller raises `NeedsAttention/ReceiveFailed`, and the tie becomes
`abandoned`. Then:

- The Question stays posted, recorded in `posted` and `questions`.
- The Task reads its later declarations as an untied Task does.
- A later Question opens a new thread.

So nothing waits forever on a thread that never reached its Conversation.

An `abandoned` tie keeps the Conversation's name, and its `Object` probe
every `pollMs`. The listener's keyed copy may later create the
Conversation. When that Conversation ties itself to this Task's `uid`, the
tie becomes `tied`. So a thread tied to this Task never goes unheard.

#### Post order

Posts to one conversation go out in declaration order, one at a time. A
post whose receipt is missing or Unknown holds back every later post to that
conversation. So a thread never reads out of order.

## Effect and recovery keys

| Item | Key |
| --- | --- |
| Question post or open | `task/<name>/<uid>/gateway/question/<id>` |
| Comment | `task/<name>/<uid>/gateway/comment/<id>` |
| Draft | `task/<name>/<uid>/gateway/draft/<id>` |
| One part of a split post | `<key>/<part>`. Each part is its own effect, with its own intent and receipt. The parts are posted in order, and each one is recovered on its own. The lease is renewed before each part, as before each effect. |
| Change news | `task/<name>/<uid>/gateway/change/<cr uid>/<state.seq>` |
| Result | `task/<name>/<uid>/gateway/result/<generation>` |
| Refusal | `conversation/<conv uid>/refuse/<author id>`. Every refused author id is kept for the Conversation's life. Past 200 distinct refused authors, no further refusal is sent. So the key never repeats. |
| Task creation | `conversation/<conv uid>/task/<n>`. It is planned again under the same key while its answer is `WorkspaceBusy`, because nothing was created. |
| Busy notice | `conversation/<conv uid>/busy/<n>`, once per tie number |
| A Question thread's `Receive` | `task/<name>/<uid>/gateway/question/<id>/receive` |

Fleet never repeats a key.

### Recovering a post

An Unknown `post` or `open`, or an intent without a receipt, is answered by
the conversation's next complete `history` read. That read is made at least
`RETRY_DELAY_MS` after the intent.

- A message by Fleet's identity that carries the key is a recovered
  receipt.
- A complete read without the key is a failed attempt. It is retried under
  the same key, at most `MAX_ATTEMPTS` (3) times. Then the controller raises
  `NeedsAttention/PostFailed`.

With `marker = nonce`, the same post is sent again with the same nonce,
within the platform's window. The platform then returns the existing
message instead of a second one. Past the window, the marker is treated as
`none`.

Where the adapter has no history, or its posts carry no marker, an Unknown
outcome is not retried. The controller raises
`NeedsAttention/PostUnconfirmed`, and the conversation's later posts wait for
a person.

### Recovery reads

A recovery read is not an attempt. A `history` read that answers Unknown
settles nothing and performs nothing. That includes `Incomplete`, a read
that could not page through everything since the intent within the call
bound, and any other Unknown.

Each such read:

- increments the effect's `recovery.reads`,
- sets `recovery.nextAt` to `RETRY_DELAY_MS × 2^(reads − 1)` after the read.
  That delay is capped at `pollMs`, and raised to the platform's
  `retryAfter` when the platform gave one.

When `reads` reaches `MAX_ATTEMPTS`, the controller raises
`NeedsAttention/RecoveryIncomplete` for that key. It keeps reading on the
capped schedule. Nothing is performed again.

A complete read resets `recovery` to `{reads: 0}`. The controller decides
the read as above: a recovered receipt or a failed attempt. That clears the
condition.

The same counter, on the Conversation's `effects`, bounds a refusal's
recovery.

### Recovering an open

An Unknown `open` has no thread to read yet. A complete `history` read of
the channel, from before the intent, answers it. A top-level message by
Fleet that carries the key is the opened thread, recovered with its
address.

GatewayController decides this. Its plan carries the recovered `Opened`
receipt and, in the same write, the `Receive` intent, exactly as on the
direct path. So the manager interprets no probe answer itself.

Where opening takes two calls, a recovered starter message without its
thread is finished by a retry of `open` under the same key. The adapter
completes that open rather than starting again.

Without channel history or a marker, an Unknown `open` raises
`NeedsAttention/PostUnconfirmed`, and the Question waits for a person.
Nothing is opened twice.

### Refusals and post order

Refusals are ConversationController's. They are not ordered against a
Task's posts in the same thread. Reason: a refusal says nothing that a later
post depends on.

## Admission

Admission is a pure function of the route, the people table and a message.
Both controllers share it:

```text
admit(route, message):
    if message.author.kind == fleet:                      never relayed
        Own
    if author id is under route.allow (through [gateway.people]):
        Admitted
    if author.kind == bot and author id is under route.allowBots:
        Admitted
    otherwise:
        Context                                           relayed with the next Admitted
```

ConversationController starts a Task for an untied thread only on an
`Admitted` message that also satisfies `route.mention`:

- `required`, the default, needs `mentionsFleet`.
- `top-level` admits a message that starts a thread without a mention. This
  suits a channel where every top-level post is a request, such as an alert
  channel whose authors are bots under `allowBots`.

Once the thread is tied, every `Admitted` message in it is a turn, with a
mention or not.

A `Context` message by a `person` that mentions Fleet gets one refusal per
author and conversation, under its refusal key, and nothing else. An
unlisted bot gets no refusal.

## Write ownership

| Writer | May change | Required fencing |
| --- | --- | --- |
| `listener/<account>`, through `fleet gateway receive` | Creates a Conversation. Appends to its `status.received`. | Exclusive creation; current resourceVersion. No lease. |
| The manager performing a `Receive` action of GatewayController's plan, as `gateway-controller` | Creates the new thread's Conversation. Appends the opening post to its `status.received`, through the same `receive_event`. | Exclusive creation; current resourceVersion; the live lease. |
| `conversation-controller` | All of Conversation status but `received`. Drains `received`. Creates conversation Tasks. Deletes its own object. | Current resourceVersion and the live lease. |
| `gateway-controller` | All of `status.gateway`. Appends to `status.inbox`, recording `status.answered`. | Current resourceVersion and the live lease. |
| `apply` | Creates a Conversation by manifest, refused unless its name is the one derived from its account and address. Requests deletion. | Store validation, exclusive creation. |
| `apply`, `cli`, `plugin/<name>` | Inbox appends as today (`cli`, `plugin/<name>`), each now able to carry `answers`. The parent labels on a Task they create. | As today. The parent labels only at creation, under the parent's scope below. |
| `listener/<account>`, through `fleet gateway receive` | A Listener's `status.beats`, `status.refused` and `status.beatRevision`, through `record_beat` and `record_refusal`. Creates the Listener when it does not exist. | Exclusive creation; current resourceVersion. No lease. |
| The manager, loading the config | Creates one Listener per configured account. | Exclusive creation. |
| `listener-controller` | All of Listener status but `beats`, `refused` and `beatRevision`. Deletes a Listener whose account left the config. | Current resourceVersion and the live lease. |
| `conversation-controller`, through its create | The labels `gateway.fleet.dev/conversation` and `gateway.fleet.dev/conversation-uid` on the Tasks it creates. | Exclusive creation; `apply` refuses both labels. |

### The parent label

`gateway.fleet.dev/parent` is authority, not routing. A parent relays its
children's words into its own inbox. So naming a parent is writing to that
Task's inbox.

The store accepts the label only at a Task's creation, together with
`gateway.fleet.dev/parent-uid`, the parent's `uid`. It accepts the label
only from a writer that could already append to the parent's inbox:

- a person, through `apply` or `cli`. `fleet send` already lets a person
  write to any Task.
- `plugin/<name>` under `tasks = "any"`.
- `plugin/<name>` under `tasks = "own"`, when the parent carries
  `fleet.dev/plugin: <name>`.

At the create, the store reads the named parent:

- The parent must exist with that `uid`, or the create is refused.
- A parent that the store cannot read refuses the create as Unknown, to be
  retried. The store never accepts the create unchecked.

The store refuses any other writer at the create. It also refuses a later
write that changes or removes either label. So a `tasks = "own"` plugin
cannot attach its child to another plugin's Task, or to a person's.

The parent's GatewayController relays only a child whose `parent-uid` is
its own `uid`. A child that names the parent's name with another `uid` was
made for an earlier Task of that name. The controller treats that child as
untied.

A tie, once verified, wins over a parent label.

### The route label

`gateway.fleet.dev/route` chooses the route in whose channel a Question
opens. It is an ordinary label. The operator configures a route as one that
Questions may open in, and any Task may already declare a Question.

### Fields with no single owner

Every other Task field keeps its owner. `status.received` on a Conversation
joins `status.inbox` and `status.answered` as a field with no single owner.
Only `receive_event` and `record_received` reach it. They refuse:

- a duplicate event key,
- more than 100 unnumbered events,
- a deleting Conversation.
