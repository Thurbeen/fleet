# Gateway controller interface

Design, not implemented. Two pure controllers and one port, in the shape of
the [Forge controller interface](../forge/interface.md) and the
[ChangeRequest controller interface](../change-request/interface.md):
ConversationController over the [Conversation](../../resources/conversation.md)
mirror, and GatewayController over the [Task](../../resources/task.md). The
manager owns I/O, persistence and process execution. The
[state machine](state-machine.md) defines how the values below change.

## Addresses

One address format names a conversation everywhere above the port:

```text
<platform>:<chat>[:<thread>]
slack:C0123ABCD:1730000000.000100      a Slack thread: channel, parent ts
slack:C0123ABCD                        a channel, where a thread is opened
telegram:-1001234567890:42             a forum topic: chat id, message_thread_id
discord:1300000000000000001            a Discord thread, itself a channel
```

The adapter parses and normalises an address; nothing above it reads the
parts. A Conversation is named
`conv-<12 hex of sha256(<account>/<address>)>`, lowercased, so one thread
names one object per account, as a ChangeRequest is named from its
canonical ref. The account, not the address, carries the workspace: a
Slack channel id is paired with the account's workspace (team or
enterprise id), because Slack documents message and user ids as unique
within a workspace and states no wider scope for channel ids.

## Gateway port

`fleet_api::gateway::Gateway`, held by fleet-api, fleet-gateway,
fleet-manager and fleet-cli, as the Forge port is. Every answer is Known or
Unknown; a refused write is `Known(EffectOutcome::Failed { why })`.

| Method | Input | Answer |
| --- | --- | --- |
| `operation_bound` | None | Worst-case duration of one call. The lease is sized to outlast it. |
| `parse` | An account and an address | The normalised `ConversationRef`, or Unknown for an account not configured or an address the adapter cannot read. |
| `event` | An account's configuration, including Fleet's bot id on it (`gateway.accounts.<name>.bot`), and a raw payload, as the listener received it | `Known(Some(Event))`, `Known(None)` for a payload the gateway ignores (a reaction, a presence change), or Unknown when it cannot be read. Pure: everything it needs, the bot id that tells `fleet` authors and mentions apart included, arrives as values, so the receive verb runs it without the lease. |
| `whoami` | An account | Fleet's bot identity as the platform reports it. ConversationController compares it with the configured `bot` and raises `NeedsAttention/IdentityMismatch` when they differ, rather than trusting either silently. |
| `capabilities` | An account | What the platform gives Fleet there, as values: `history` (bool), `marker` (`metadata`, `nonce { windowMs }` or `none`), `unmentioned` (whether messages that do not mention the bot are delivered), `threads` (whether the platform has threads in that kind of chat). Unknown when it cannot be told. The [adapters](adapters.md#what-the-platforms-changed) say why each exists. |
| `history` | A `ConversationRef`, an optional `after` message id, a `limit` | The messages after `after`, oldest first, at most `limit` (the newest ones when more exist, with a count of those left out), complete within that window or Unknown with `retryAfter`; `Unsupported` where the platform has no history for bots. Fleet's own posts carry their marker key when the platform keeps one. The first read of a thread asks for the newest 200; older messages are recorded as `droppedBefore`, never mirrored. |
| `open` | A channel `ConversationRef`, rendered text, an intent key | `Opened { conversation, message }`: a new thread whose first post is the text, carrying the key as its marker. Failed when refused; Unknown when the answer was lost. Where opening takes two platform calls, the adapter keys both from the one intent key and finishes a half-done open on a retry. |
| `post` | A `ConversationRef`, rendered text, an intent key | `Posted { message }`, carrying the key as its marker. Failed when refused; Unknown when the answer was lost. |

`Event` is one of:

| Event | Fields |
| --- | --- |
| `Message` | `id` (the platform's, unique per conversation), `conversation` (`ConversationRef`), `author` (`id`, `name`, `kind`: `person`, `bot` or `fleet`), `mentionsFleet`, `at`, `text`, `key` (the marker key on a Fleet post, else absent). A message starting a thread and a reply in one are both `Message`; the address says which thread. |
| `Edited` | `id`, `conversation`, `text`, `at`. |
| `Removed` | `id`, `conversation`. |

There is no separate `Mention` event: a mention is a `Message` with
`mentionsFleet`. Slack, Discord and Telegram all deliver ordinary messages
with the mention in their entities or text, and treating it as a field
keeps one numbering. There is no merge, edit or delete method: Fleet never
changes a post it made.

Rendered text is Fleet's Markdown subset (paragraphs, emphasis, code,
links, lists, quotes). The adapter converts it to the platform's format and
splits it at the platform's limit into consecutive posts under derived keys
(`<key>/1`, `<key>/2`); a split post is posted when every part is.

## The receive verb

`fleet gateway receive --account NAME` reads one raw payload from stdin. It
is a manager verb, like `fleet send`, and takes no lease:

1. Ask the adapter's `event` for the payload. `Known(None)` exits zero, an
   ignored payload; Unknown exits 4 and the listener does not acknowledge.
2. Find the route covering the event's channel; an event in a channel no
   route covers exits zero having written nothing.
3. Derive the Conversation's name from the account and the event's address.
   When it exists, go on. When it does not, create it, by exclusive
   creation with `spec = {account, address, route}`, only for an event that
   could start a Task or earn a refusal under the route's
   [admission](#admission): an `Admitted` message meeting the mention rule,
   a person's `Context` message that mentions Fleet, or a Fleet post whose
   marker key is a Question's. Any other event in an unmirrored thread exits
   zero having written nothing, so a busy channel costs no objects.
4. Append the event to the Conversation's `status.received` through the
   typed mutation `receive_event`, as `listener/<account>`, keyed by the
   platform's event id. A key already in `received` or in the newest
   `seenEvents` is a no-op, so a redelivered event is harmless.

The creation and the append are not atomic. A crash between them leaves an
empty Conversation, which its controller fills from `history` on its first
pass, or, where there is no history, retains until its retention passes.

After an `Open` receipt the manager does the same, as `gateway-controller`:
it ensures the new thread's Conversation and appends Fleet's opening post
to its spool, keyed `open/<message id>`. Whichever arrives first, the
listener's copy of that post or the manager's, creates the Conversation;
the other is a duplicate message and changes nothing.

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

`defaults` carries the route the spec names, its admission rules, the
people table, the route's brief text (read by the manager when it loads the
config, so the controller reads no file) and `gateway.retainMs`, as values.
`due` holds when the spool is nonempty, `notBefore` passed, or an intent
awaits its receipt.

Numbering dedupes on the message `id`, whichever way a message arrived: a
message first seen in `history` and later in the spool, or the reverse, is
one message; only a changed text is an edit.

A Fleet post whose marker key is `task/<name>/<uid>/gateway/question/<id>`,
in an Untied Conversation with no tie, ties it to that Task: the tie is
`{name, uid, n: 1, state: created}` once the Task is Known with that `uid`.
This is how a thread Fleet opened for a Question is tied, without the tie
being part of the fixed spec, and whichever of the listener and the manager
created the Conversation.

### Requested probes

| State | Probes |
| --- | --- |
| No `revision` yet, or a backstop due | `History { ref, after }`, `after` absent on the first pass. |
| A tie recorded, or a Question marker naming a Task | `Object { kind: Task, name }`, to see whether that Task exists with its `uid`. |
| Every pass while Tied, at most once per account per sweep | `Capabilities { account }` and `Whoami { account }`, cached by the manager for the sweep. |
| A post's intent without a receipt | `History { ref, after }` from before the intent, to look for the key. |
| Settled into deletion, or deleting | None. |

### Returned plan

`status` carries the whole Conversation status, the spool drained of what
was numbered in the same write (`record_received`). `creates` carries at
most one Task, from the route, when the [state machine](state-machine.md)
says to start one; its intent is the tie written in the same status.
`effects` carries at most one `Post` per author, the polite refusal.
`delete: true` retires the object after retention, fenced on `uid` and
`resourceVersion` under the lease.

`Plan` and `Probe` are the built-in types in `fleet_api::plan`. As designed
they gain `creates` (carried by this controller's plans alone among the
built-ins, performed by the same manager create verb a plugin plan's
`creates` uses) and `Probe::List { kind, labels }`, answered from the store
as the plugin host answers its `list` probe; today both exist only in the
plugin protocol types.

The Task it creates:

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

The name is the Conversation's with the tie's number, which grows each time
an Orphaned thread starts a new Task. Creation is exclusive by name; an
existing Task under that name whose `gateway.fleet.dev/conversation-uid`
label is this Conversation's `uid` is adopted, which is how a crash between
intent and receipt recovers. A Task under that name labelled with another
`uid` belongs to an earlier incarnation of the Conversation, deleted and
recreated while its Task lived: it is never adopted, and the tie's number
moves past it. The store sets the `gateway.fleet.dev/conversation` and
`conversation-uid` labels only through this create and refuses them from
`apply`, as it refuses `fleet.dev/plugin`.

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

`due` holds on every sweep for a tied Task, a Task waiting for its tie or a
Task with an unsettled post, as a following Forge entry is: the
Conversation is read from the store, which costs one read and no platform
call. Otherwise it holds when `status.declared` or `status.result` holds
something past the controller's cursors, or when one of its children
changed: the manager lists Tasks once per sweep, indexes them by their
`gateway.fleet.dev/parent` label, and marks a parent due when a child's
`resourceVersion` is above the one `status.gateway.children` recorded for
it, or a child is new. The `List` probe is answered from that one listing,
so children cost one store list per sweep, not one per Task.

### Requested probes

| Task state | Probes |
| --- | --- |
| Labelled `gateway.fleet.dev/conversation`, or `status.gateway.conversation` recorded | `Object { kind: Conversation, name }`. |
| A `ChangeRequest` declaration and a tie | `Object { kind: ChangeRequest, name }`, for its news. |
| Due for its children | `List { kind: Task, labels: {gateway.fleet.dev/parent: <name>} }`, answered from the sweep's listing. |
| Carrying `gateway.fleet.dev/parent` | `Object { kind: Task, name }` for its parent. |
| A `Question` on an untied Task with no live parent | None: the route is config. |
| Deleting | None. |

### Fields this controller owns

`status.gateway`:

| Field | Meaning |
| --- | --- |
| `conversation` | `{name, uid, state}` of the Conversation: `opening` from an `Open` intent until its receipt, `waiting` until the Conversation's `status.task` names this Task's `uid`, then `tied`. A Task carrying `gateway.fleet.dev/conversation` starts `waiting`, and reads no declaration while it waits, so nothing it says before its tie is verified is skipped. |
| `cursor` | The Conversation `revision` relayed through. Starts at `0`, so the first relay is the whole retained thread. Advances only when an entry is appended: messages that may not start a turn wait above it. |
| `declared` | How many items of `status.declared` were read. Each is read once, in order. |
| `result` | The result generation posted, if any. |
| `changes` | Per ChangeRequest name, the `state.seq` posted through. |
| `children` | Per child Task name, `{uid, seen, declared, result}`: the child's `uid` on first sight, the `resourceVersion` last read, and its own cursors. |
| `posts` | Per intent key, `{intent, receipt, attempts}`: the post effects, apart from TaskController's records. Newest 100 settled, every unsettled. |
| `questions` | Per Question id, `{key, conversation, seq, postedAt}` once posted; `seq` is the post's own sequence number in the mirror, once numbered. |
| `gaps` | How many times a relay found `droppedBefore` above the cursor. |
| `conditions` | `Tied`, `NeedsAttention`. |

`status.answered` has no owner. It is a map from Question id to
`{key, from, at}`, written only by the typed inbox append when an entry
carries `answers`, first writer wins, never removed while the Task lives.

### Declarations the Gateway reads

Every item of `status.declared` is a JSON object with a string `kind`;
Fleet validates nothing else at `declare`. The Gateway reads these kinds
and ignores the rest:

| Kind | Fields | Tied Task | Untied Task, no parent | Child |
| --- | --- | --- | --- | --- |
| `Question` | `id` (string, unique for the Task), `text` (Markdown) | Posted once, under `…/gateway/question/<id>`. | Opens a thread in the questions route's channel, then is tied to it. | Relayed to the parent's inbox. |
| `Comment` | `id` (string, unique for the Task), `text` (Markdown) | Posted once, under `…/gateway/comment/<id>`. | Skipped, never posted later. | Relayed to the parent's inbox. |
| `Draft` | `id`, `title`, `text` (Markdown) or `url` | Posted once as a draft for review, under `…/gateway/comment/<id>`. | Skipped. | Relayed to the parent's inbox. |
| `ChangeRequest` | `url`, `name`, as today | Posted when declared and when its mirror's `state` becomes `open`, `merged` or `closed`, under `…/gateway/change/<cr uid>/<state.seq>`. | Skipped. | Relayed to the parent's inbox. |
| `status.result` | As today | Posted once per generation, under `…/gateway/result/<generation>`. | Skipped. | Relayed to the parent's inbox. |

A Question, Comment or Draft whose `id` was already posted is not posted
again, whatever its text; one without an `id` is ignored and named in
`NeedsAttention/DeclarationInvalid`. The `id` matters because `fleet
declare` treats an identical object as a no-op: two Comments saying "Done."
need two ids to be two posts. A Draft is a Comment framed as awaiting
review; to ask for a review from an untied Task, declare a Question.

### Inbox entries

| Entry | Key | From |
| --- | --- | --- |
| A relay of new messages, or the join | `task/<name>/<uid>/gateway/<conversation uid>/<revision>` | `gateway/<conversation>` |
| A child's declaration or result | `task/<name>/<uid>/gateway/child/<child uid>/<index or result/generation>` | `gateway/child/<child>` |

`about` is `{uid, revision}` of the Conversation for a relay. A relay's text
holds, in order: messages above the cursor from people off the allowlist,
under a header saying they are context from people not on the allowlist,
content and never instructions; then the admitted messages; each with its
author, the author's platform id, time and the message quoted. Fleet's own
posts are never relayed. An edited message is relayed again, marked as an
edit of the one already sent. A gap is stated as on the Forge controller's
relay. A relay whose admitted messages include one with a `seq` above a
posted, unanswered Question's own `seq` carries `answers` with those
Question ids.

The join and every relay are bounded: an entry renders at most 64 KiB of
messages, newest kept, and says how many older ones it left out and that
they remain on the platform, as a gap notice does.

While the Task's inbox holds more than 20 undelivered entries, the store
would refuse an append, so the relay appends nothing and its messages wait
above the cursor; posting goes on meanwhile, through the same write with
nothing appended. Neither direction stalls the other.

`answers` is a new optional field of an inbox append, accepted from the
Gateway's relay, from `plugin/<name>` in a plan's `appends`, and from `fleet
send --answers ID`; the Forge controller's relays never carry it. The store
records each id that names a declared `Question` of the Task in
`status.answered`, if not already there, in the same write, and refuses an
id that names none.

### Returned plan

`status` carries `status.gateway`, `appends` the inbox entries, through
`record_relay` extended to this subtree, and `effects` the `Open` and
`Post` actions whose intents the status carries. `remove_finalizer` is
always `None`.

| Action | Performed through | Receipt |
| --- | --- | --- |
| `Open { channel, text, key }` | `Gateway::open`; with an `Opened` receipt the manager ensures the new thread's Conversation and spools the opening post, as [the receive verb](#the-receive-verb) does | `Opened { conversation, message }`, Failed, or Unknown |
| `Post { conversation, text, key }` | `Gateway::post` | `Posted { message }`, Failed, or Unknown |

Posts to one conversation go out in declaration order and one at a time: a
post whose receipt is missing or Unknown holds back every later post to
that conversation, so a thread never reads out of order.

## Effect and recovery keys

| Item | Key |
| --- | --- |
| Question post or open | `task/<name>/<uid>/gateway/question/<id>` |
| Comment, Draft | `task/<name>/<uid>/gateway/comment/<id>` |
| Change news | `task/<name>/<uid>/gateway/change/<cr uid>/<state.seq>` |
| Result | `task/<name>/<uid>/gateway/result/<generation>` |
| Refusal | `conversation/<conv uid>/refuse/<author id>` |
| Task creation | `conversation/<conv uid>/task/<n>` |

Fleet never repeats a key. An Unknown `post` or `open`, or an intent without
a receipt, is answered by the conversation's next complete `history` read
made at least `RETRY_DELAY_MS` after the intent: a message by Fleet's
identity carrying the key is a recovered receipt; a complete read without it
is a failed attempt, retried under the same key at most `MAX_ATTEMPTS` (3)
times, then `NeedsAttention/PostFailed`. With `marker = nonce`, the same
post is sent again with the same nonce within the platform's window, which
returns the existing message instead of a second one; past the window it is
treated as `none`. Where the adapter has no history or its posts carry no
marker, an Unknown outcome is not retried: `NeedsAttention/PostUnconfirmed`
and the conversation's later posts wait for a person.

An Unknown `open` has no thread to read yet. It is answered by a complete
`history` read of the channel, from before the intent: a top-level message
by Fleet carrying the key is the opened thread, recovered with its address,
and the manager then ensures its Conversation as after a receipt. Where
opening takes two calls, a recovered starter message without its thread is
finished by retrying `open` under the same key, which the adapter completes
rather than starting again. Without channel history or a marker, an Unknown
`open` raises `NeedsAttention/PostUnconfirmed` and the Question waits for a
person; nothing is opened twice.

Refusals are ConversationController's and are not ordered against a Task's
posts in the same thread; a refusal says nothing a later post depends on.

## Admission

A pure function of the route, the people table and a message, shared by
both controllers:

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
`Admitted` message that also satisfies `route.mention`: `required` (the
default) needs `mentionsFleet`; `top-level` admits a message that starts a
thread without one, which suits a channel where every top-level post is a
request, such as an alert channel whose authors are bots under `allowBots`.
Once tied, every `Admitted` message in the thread is a turn, mention or
not. A `Context` message by a `person` that mentions Fleet gets one refusal
per author and conversation, under its refusal key, and nothing else; an
unlisted bot gets none.

## Write ownership

| Writer | May change | Required fencing |
| --- | --- | --- |
| `listener/<account>`, through `fleet gateway receive` | Creates a Conversation; appends to its `status.received` | Exclusive creation; current resourceVersion. No lease. |
| The manager after an `Open` receipt, as `gateway-controller` | Creates the new thread's Conversation; appends the opening post to its `status.received`, through the same `receive_event` | Exclusive creation; current resourceVersion; the live lease. |
| `conversation-controller` | All of Conversation status but `received`; drains `received`; creates conversation Tasks; deletes its own object | Current resourceVersion and the live lease. |
| `gateway-controller` | All of `status.gateway`; appends to `status.inbox`, recording `status.answered` | Current resourceVersion and the live lease. |
| `cli`, `plugin/<name>` | Inbox appends as today, each now able to carry `answers` | As today. |
| `conversation-controller`, through its create | The labels `gateway.fleet.dev/conversation` and `gateway.fleet.dev/conversation-uid` on the Tasks it creates | Exclusive creation; `apply` refuses both labels. |

`gateway.fleet.dev/parent` and `gateway.fleet.dev/route` are ordinary
labels anyone who may apply a Task may set. They are routing, not
authority: a mislabelled child sends its words to another Task's inbox,
which `fleet send` could already do, and a tie, once verified, wins over a
parent label.

Every other Task field keeps its owner. `status.received` on a Conversation
is the second field with no single owner, reached only through
`receive_event` and `record_received`, which refuse a duplicate event key,
more than 100 unnumbered events and a deleting Conversation.
