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
| `event` | An account and a raw payload, as the listener received it | `Known(Some(Event))`, `Known(None)` for a payload the gateway ignores (a reaction, a presence change), or Unknown when it cannot be read. Pure: no I/O, so the receive verb runs it without the lease. |
| `whoami` | An account | Fleet's bot identity on it: the ids its own posts carry. Unknown when unreadable. |
| `capabilities` | An account | What the platform gives Fleet there, as values: `history` (bool), `marker` (`metadata`, `nonce { windowMs }` or `none`), `unmentioned` (whether messages that do not mention the bot are delivered), `threads` (whether the platform has threads in that kind of chat). Unknown when it cannot be told. The [adapters](adapters.md#what-the-platforms-changed) say why each exists. |
| `history` | A `ConversationRef`, an optional `after` message id | Every message after `after`, oldest first, complete or Unknown with `retryAfter`; `Unsupported` where the platform has no history for bots. Fleet's own posts carry their marker key when the platform keeps one. |
| `open` | A channel `ConversationRef`, rendered text, an intent key | `Opened { conversation, message }`: a new thread whose first post is the text, carrying the key as its marker. Failed when refused; Unknown when the answer was lost. |
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
2. Derive the Conversation's name from the account and the event's address
   and ensure it exists, by exclusive creation with
   `spec = {account, address, route}`. The route is the one covering the
   event's channel; an event in a channel no route covers exits zero having
   created nothing.
3. Append the event to the Conversation's `status.received` through the
   typed mutation `receive_event`, as `listener/<account>`, keyed by the
   platform's event id. A key already in `received` or in the newest
   `seenEvents` is a no-op, so a redelivered event is harmless.

The two steps of creation and append are not atomic. A crash between them
leaves an empty Conversation, which its controller fills from `history` on
its first pass.

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
people table and `gateway.retainMs`, as values. `due` holds when the spool
is nonempty, `notBefore` passed, or an intent awaits its receipt.

### Requested probes

| State | Probes |
| --- | --- |
| No `revision` yet, or a backstop due | `History { ref, after }`, `after` absent on the first pass. |
| A tie recorded | `Object { kind: Task, name }`, to see whether the tied Task still exists with its `uid`. |
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

The Task it creates:

```json
{
  "apiVersion": "fleet/v1alpha1",
  "kind": "Task",
  "metadata": {
    "name": "conv-3f9a1c0d2b7e-1",
    "labels": {"gateway.fleet.dev/conversation": "conv-3f9a1c0d2b7e",
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
an Orphaned thread starts a new Task, so a key never repeats. Creation is
exclusive by name; an existing Task carrying this Conversation's label is
adopted, which is how a crash between intent and receipt recovers.

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

`due` holds on every sweep for a tied Task, a Task with children or a Task
with an unsettled post, as a following Forge entry is: the Conversation is
read from the store, which costs one read and no platform call. Otherwise
it holds when `status.declared` or `status.result` holds something past the
controller's cursors.

### Requested probes

| Task state | Probes |
| --- | --- |
| Labelled `gateway.fleet.dev/conversation`, or `status.gateway.conversation` recorded | `Object { kind: Conversation, name }`. |
| A `ChangeRequest` declaration and a tie | `Object { kind: ChangeRequest, name }`, for its news. |
| Any Task | `List { kind: Task, labels: {gateway.fleet.dev/parent: <name>} }`, its children; `Object { kind: Task, name }` for its own parent when it carries that label. |
| A `Question` on an untied Task with no live parent | None: the route is config. |
| Deleting | None. |

### Fields this controller owns

`status.gateway`:

| Field | Meaning |
| --- | --- |
| `conversation` | `{name, uid}` of the tied Conversation, once verified: the Conversation's `status.task` names this Task's `uid`. |
| `cursor` | The Conversation `revision` relayed through. Starts at `0`, so the first relay is the whole retained thread. Advances only when an entry is appended: messages that may not start a turn wait above it. |
| `declared` | How many items of `status.declared` were read. Each is read once, in order. |
| `result` | The result generation posted, if any. |
| `changes` | Per ChangeRequest name, the `state.seq` posted through. |
| `children` | Per child Task name, `{uid, declared, result}`, its own cursors. |
| `posts` | Per intent key, `{intent, receipt, attempts}`: the post effects, apart from TaskController's records. Newest 100 settled, every unsettled. |
| `questions` | Per Question id, `{key, conversation, postedAt}` once posted. |
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
| `Comment` | `text` (Markdown) | Posted once, under `…/gateway/declared/<index>`. | Skipped, never posted later. | Relayed to the parent's inbox. |
| `Draft` | `title`, `text` (Markdown) or `url` | Posted once as a draft for review, under `…/gateway/declared/<index>`. | Skipped. | Relayed to the parent's inbox. |
| `ChangeRequest` | `url`, `name`, as today | Posted when declared and when its mirror's `state` becomes `open`, `merged` or `closed`, under `…/gateway/change/<cr uid>/<state.seq>`. | Skipped. | Relayed to the parent's inbox. |
| `status.result` | As today | Posted once per generation, under `…/gateway/result/<generation>`. | Skipped. | Relayed to the parent's inbox. |

A Question whose `id` was already posted is not posted again, whatever its
text. A Draft is a Comment framed as awaiting review; to ask for a review
from an untied Task, declare a Question.

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
posts are never relayed. A gap is stated as on the Forge controller's
relay. A relay that follows a posted, unanswered Question carries
`answers` with those Question ids.

`answers` is a new optional field of an inbox append, accepted from every
appender: `gateway/`, `plugin/<name>` in a plan's `appends`, and
`fleet send --answers ID`. The store records each id that names a declared
`Question` of the Task in `status.answered`, if not already there, in the
same write, and refuses an id that names none.

### Returned plan

`status` carries `status.gateway`, `appends` the inbox entries, through
`record_relay` extended to this subtree, and `effects` the `Open` and
`Post` actions whose intents the status carries. `remove_finalizer` is
always `None`.

| Action | Performed through | Receipt |
| --- | --- | --- |
| `Open { channel, text, key }` | `Gateway::open`, then the manager ensures the Conversation for the new thread with `spec.task = {name, uid}` | `Opened { conversation, message }`, Failed, or Unknown |
| `Post { conversation, text, key }` | `Gateway::post` | `Posted { message }`, Failed, or Unknown |

Posts to one conversation go out in declaration order and one at a time: a
post whose receipt is missing or Unknown holds back every later post to
that conversation, so a thread never reads out of order.

## Effect and recovery keys

| Item | Key |
| --- | --- |
| Question post or open | `task/<name>/<uid>/gateway/question/<id>` |
| Comment, Draft | `task/<name>/<uid>/gateway/declared/<index>` |
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
| `conversation-controller` | All of Conversation status but `received`; drains `received`; creates conversation Tasks; deletes its own object | Current resourceVersion and the live lease. |
| `gateway-controller` | All of `status.gateway`; appends to `status.inbox`, recording `status.answered` | Current resourceVersion and the live lease. |
| `cli`, `plugin/<name>`, `forge-controller` | Inbox appends as today, each now able to carry `answers` | As today. |

Every other Task field keeps its owner. `status.received` on a Conversation
is the second field with no single owner, reached only through
`receive_event` and `record_received`, which refuse a duplicate event key,
more than 100 unnumbered events and a deleting Conversation.
