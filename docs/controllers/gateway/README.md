# Gateway controller

Design, not implemented. No `[gateway]` key, port, crate or resource kind
described here exists in the installed binary; a `Conversation` manifest is
refused today as an unknown kind.

The Gateway ties chat conversations to Tasks. It is a way for messages to
get into Fleet and out of it, not a domain of its own: a person mentions
Fleet's bot in a thread, a Task is started for that thread, and from then on
every message in the thread reaches the Task's inbox and what the Task
declares reaches the thread. The Gateway never runs a model. Everything
agentic is a Task; the Gateway admits people, keeps the tie between a thread
and its Task, and moves messages.

It is the [Forge controller](../forge/README.md) applied to chat. A Task
declares what it wants said, a mirror object follows the thread, new
messages are relayed into the Task's inbox under a cursor, Fleet's own posts
are recognised by a marker, and one adapter per platform sits behind one
port. Slack is the first adapter; Telegram, Discord and Microsoft Teams are
mapped on paper to check the model.

| Read | Answers |
| --- | --- |
| [Conversation](../../resources/conversation.md) | What does the mirror of one thread hold, and who deletes it? |
| [Task gateway status](../../resources/task.md#talking-in-a-conversation) | What does a Task declare to talk, and what do `status.gateway` and `status.answered` hold? |
| [State machine](state-machine.md) | How does a thread move between Untied, Tied and Orphaned, and what is relayed or posted when? |
| [Interface](interface.md) | What are the port, the receive verb, the probes, the effects, the keys and the write rules? |
| [Adapters](adapters.md) | How do Slack, Telegram, Discord and Teams map onto the port, and what did each change in the model? |
| [Native config](../../configuration/fleet-toml.md#the-gateway-section) | Where do accounts, people, routes and admission come from? |
| [Forge controller](../forge/README.md) | The controller this one is modelled on. |
| [Overall architecture](../../ARCHITECTURE.md#the-gateway-controller) | Where would this fit in Fleet? |

## One run: a mention starts a Task

1. A person on a route's allowlist writes in a channel the route covers,
   mentioning Fleet's bot, in a new thread or partway through an old one.
2. The platform delivers the event to a listener, a resident process outside
   the manager that holds the platform's connection. The listener hands the
   raw payload to `fleet gateway receive`, a bounded manager verb. The verb
   asks the adapter to read the payload into a platform-neutral event. An
   event for a thread Fleet already mirrors, or one that could start a Task
   or earn a refusal, is appended to the spool of that thread's
   [Conversation](../../resources/conversation.md), which the verb creates
   under a name derived from the address when it does not exist; any other
   event is dropped. The listener acknowledges the platform only once the
   verb exits zero.
3. On the next sweep ConversationController numbers the spooled event into
   the mirror. For a thread it has never seen it first asks the adapter for
   the thread's whole history, so a mention partway through a thread brings
   everything before it. The mention passes the route's admission rules, so
   the plan creates one Task from the route, with the route's TaskConfig,
   brief and workspace, labelled with the Conversation's name and `uid`, and
   records the Task's name and `uid` as the thread's tie.
4. GatewayController, a controller over that Task, finds the tie, and joins
   at the beginning: its first relay is one inbox entry holding the whole
   retained thread, each message labelled with its author and whether that
   author is on the allowlist. TaskController delivers it after the brief.
5. Every later message by an allowlisted person in the thread is relayed;
   whatever arrived since the last relay goes out as one inbox entry, the
   cursor advancing in the same write. Every message is
   a worker turn; nothing decides whether an admitted message deserves one. Messages
   from people off the allowlist start no turn: they wait above the cursor
   and ride along with the next allowlisted message, labelled as not coming
   from someone on the allowlist, content and never instructions.
6. The worker answers by declaring: `fleet declare TASK --json
   '{"kind":"Comment","id":"c1","text":"…"}'` to say something, a
   `Question` to ask,
   a `ChangeRequest` when it opens a change, and `fleet result` when it
   reports. GatewayController renders each into one post, writes the post's
   intent with its key, and the manager posts it through the Gateway port;
   the receipt lands on the Task. The worker holds no platform credential
   and names no destination.
7. The thread lives as long as people write in it. A Concluded Task still
   receives what is relayed, as it does today. If the Task is deleted, the
   Conversation is Orphaned; the next allowlisted message in the thread
   starts a new Task, joined with the thread's history.
8. An Orphaned or Untied Conversation that sees no message for its
   retention is deleted by its own controller. Nothing on the platform
   changes.

## One run: a Question opens a thread

1. A Task nobody started from chat, one a Notion factory plugin created for
   example, declares `{"kind":"Question","id":"q1","text":"…"}`.
2. GatewayController sees a Question on a Task that has no tie and no
   parent. It picks the questions route, from the Task's
   `gateway.fleet.dev/route` label or the `[gateway]` default, and writes an
   `Open` intent under `task/<name>/<uid>/gateway/question/q1`.
3. The manager opens a new thread in that route's question channel with the
   Question as its first post, carrying the key as its marker. With the
   receipt it ensures the Conversation for the new thread and spools that
   first post, as the receive verb would; whichever of the two creates the
   Conversation, ConversationController ties the thread to the Task the
   marker key names.
4. Replies in that thread are relayed to the Task's inbox. The first
   allowlisted reply written after the Question's own post records it as answered
   on the Task, `status.answered.q1`, in the same write that queues the
   reply. A Notion factory plugin showing the same Question as "Needs input"
   reads that fact and stops showing it; if the answer arrives there first,
   the plugin's inbox append records it instead and the thread stops
   waiting. Nothing is reposted, nudged or reminded.
5. A `Comment`, a change's news or a result never opens a thread. They are
   posted only on a Task already tied.

## The Forge pattern, applied to chat

| Forge | Gateway | Why the same |
| --- | --- | --- |
| A forge host, one adapter per kind (`gh`, `glab`, `az`, `curl`) | A chat account, one adapter per platform | Nothing above the port names a platform; the adapter translates once. |
| `ChangeRequest`, the mirror of one change, named from its canonical ref | `Conversation`, the mirror of one thread, named from its canonical address | One reader per thread however many consumers; an unreachable platform is the mirror's condition, never a state of the thread. |
| `revision` and per-note `seq` | `revision` and per-message `seq` | A consumer diffs with one integer. |
| ForgeController over the Task, owning `status.forge` | GatewayController over the Task, owning `status.gateway` | A second writer on the Task with its own subtree, writing nothing else. |
| Relay into the inbox under a cursor, in one CAS write (`record_relay`) | The same mutation, for `status.gateway` | The cursor and the entries land together or not at all. |
| The worker mark `<!-- fleet:worker <uid> -->` | A platform marker carrying the post's intent key | Fleet tells its own words from a person's, and recovers an Unknown post by looking for the key. |
| Mark-ready, the one forge write, keyed, intent before and receipt after | `Open` and `Post`, keyed, intent before and receipt after | Fleet never repeats a key; an Unknown outcome is answered by observation. |
| `fleet declare --change-request` ensures the mirror | `fleet gateway receive` ensures the mirror | A mirror exists as soon as anything names it, created exclusively by name. |

Where the two differ, the difference is chat's:

- **A chat pushes.** A forge is polled; a chat platform delivers events,
  over a WebSocket or a webhook, and offers history only as a backstop or
  not at all. Fleet adds an inbound verb and a listener; see
  [inbound transport](#inbound-transport).
- **A thread creates its Task.** A change is opened by a Task that already
  exists. A conversation starts before any Task, so the mirror's controller
  creates one, through the same create verb a plugin's plan uses.
- **Fleet speaks.** The Forge controller writes one thing on a forge;
  the Gateway posts on every declaration. Each post is a keyed effect, and
  posts to one thread go out in declaration order, one unresolved at a
  time.

## Every write is local

ConversationController writes only Conversations, and creates Tasks through
the manager's create verb. GatewayController's plans write only
`status.gateway` and the Task's own inbox, apart from the one spool append
below, and read Conversations,
ChangeRequests and child Tasks by name or label. Two writes reach a
Conversation from outside its controller, both through the one typed spool
append and neither touching its status otherwise: the receive verb, and the
manager after an `Open` receipt, which records the post it just made.
`status.answered`, like `status.inbox`, has no single owner: it is written
only by the typed inbox append that carries the answer, first writer wins.
Posting is the only effect either controller performs.

## Where it lives

A built-in controller, not a Fleet plugin. A plugin would need:

- **Every Task.** The Gateway reads any Task's declarations and posts for any
  Task that asks a Question. A plugin would need `tasks = "any"`, the widest
  right Fleet has, to do what a built-in does as a named field manager with
  one subtree.
- **A Task subtree.** Relaying under a cursor in the same write as the
  inbox append needs a status subtree on the Task. The
  [Plugin controller](../plugin/README.md#scope) leaves a plugin-owned
  subtree on a built-in kind out of scope; a plugin would have to keep its
  cursor on its own object and append without the atomic guarantee
  `record_relay` gives.
- **Waking on a Task.** A plugin object is due only on `notBefore`, a
  `generation` change or deletion; a new declaration on a Task does not wake
  it. Every Comment would wait for the plugin's poll.
- **A resident connection.** A plugin process serves one bounded call. The
  listener the platforms require is resident either way, so a plugin saves
  nothing there.
- **Kinds another component writes.** The receive verb must ensure a
  Conversation; a plugin's kinds are created only through its own plans.

The Notion factory is the opposite case: its records are its own, it needs
only its own Tasks, and its cadence is a poll. It stays a plugin and meets
the Gateway only on a Task: in `status.declared`, in the inbox and in
`status.answered`.

## Inbound transport

Fleet's rule is bounded calls and no service of its own to supervise. Every
platform but one pushes:

| Platform | What it offers | What it forces |
| --- | --- | --- |
| Slack | Socket Mode, a WebSocket; or the Events API, HTTPS to a public URL | A resident connection or a public endpoint. History is readable, rate-limited. |
| Telegram | `getUpdates`, a long poll with an offset; or a webhook | Nothing resident: a bounded call with a cursor works. No history. |
| Discord | The Gateway, a WebSocket; no other way to receive channel messages | A resident connection. History is readable. |
| Microsoft Teams | HTTPS to the bot's messaging endpoint | A public endpoint. History through Microsoft Graph, separately permitted. |

The options:

| Option | For | Against |
| --- | --- | --- |
| (a) Poll inside bounded calls | Fits Fleet exactly; Telegram's offset is a natural cursor | Discord and Teams cannot be polled for new mentions; Slack's history methods are rate-limited per channel, and finding a mention in an untied thread means reading every channel the route covers |
| (b) `fleet up` holds the connection | One resident process already exists | The manager gains a network service and a WebSocket stack; a listener stall shares fate with reconciliation; `fleet reconcile` alone can never receive |
| (c) A separate listener hands events to a manager verb | The manager stays bounded; the verb is one store write, like `fleet send`; the listener holds nothing durable | One more resident process for the operator to run |

Recommendation: **(c)**, for every platform. The listener is transport
only: it holds the connection or the endpoint, authenticates the platform
(the Socket Mode token, a webhook's signature or JWT), and pipes each raw
payload into `fleet gateway receive --account NAME`. The verb runs the
adapter's pure `event` reading, so platform parsing stays in
`fleet-gateway` with its truth tables; it refuses a duplicate event id, so
a platform's at-least-once redelivery is harmless; and the listener
acknowledges only after the verb exits zero. A listener crash loses only
unacknowledged events, which the platform redelivers or, for tied threads,
the history backstop recovers. Telegram's listener is a loop of bounded
`getUpdates` calls that confirms an offset only after the verb succeeds; a
later step may run that loop inside `fleet up` as option (a), since it
needs no connection.

A listener is not part of Fleet's runtime, and `fleet-gateway` stays an
adapter of bounded calls through `fleet-exec`, with no WebSocket client and
no resident code. The first listener, for Slack Socket Mode, is a separate
small program maintained outside the workspace, as plugin SDKs are, written
against Slack's own SDK. Any program that holds the connection and runs the
verb per event is a listener. The operator's service manager runs it; Fleet
supervises nothing.

## Scope

In scope: accounts, people and routes in native config; admission (the
allowlist, mention rules, which authors count per channel, one polite
refusal); the Conversation mirror with numbered messages and retention;
one Task per thread, created from a route; relaying a thread into its
Task's inbox under a cursor; posting what a Task declares, keyed, in order;
opening a thread for a Question from an untied Task; recording answers;
funnelling a child Task's words to its parent; the Slack adapter; the
receive verb a listener calls.

Out of scope, deliberately: merging, as for the Forge controller; suspending
and resuming idle sessions; Notion, which is a plugin's; any judgement about
a message's worth, an intake model, a planner or a heartbeat; implementing
the Telegram, Discord or Teams adapters, which are mapped in
[adapters](adapters.md) only to check the model; reactions, files, edits by
Fleet of its own posts and interactive components; the listener programs themselves.

## Open questions and recommendations

| Question | Recommendation |
| --- | --- |
| Where does the thread history go when a mention lands partway through a thread? | The first inbox entry, as the Forge controller's join message, not the brief. The brief stays the route's, identical for every conversation Task and frozen at creation; the history can be long and is data. |
| What is the brief of a conversation Task? | The route's `brief` file, read by the manager when it loads the config and copied into each Task's spec when the Task is created. TaskConfig does not define briefs or workspaces, so a route names a TaskConfig for execution defaults and carries the brief and workspace itself. |
| What workspace does a conversation Task get? | A route setting. Prefer a provisioned worktree per Task, from the repository the route names and on a branch derived from the Conversation's name, once provisioned workspaces (open as a draft) land. Until then a prepared directory, which concurrent conversation Tasks on one route would share; the route documentation says so. |
| How is a child tied to its parent? | A label on the child, `gateway.fleet.dev/parent: <parent Task name>`, set by whoever creates it. The parent's GatewayController lists its children by that label and relays their Questions, Comments, Drafts and results into the parent's own inbox; the child's GatewayController never opens or ties a thread. A label names a Task by name, not `uid`, so a child created after its parent was replaced by a same-named Task follows the new one; the parent's controller records each child's `uid` on first sight. |
| A child whose parent is readably gone? | It is treated as untied: its next Question opens a thread through the questions route, because nobody else will answer it. Its earlier declarations are not replayed. |
| How is an answer recognised in a thread? | The first allowlisted message whose platform time is after the Question post's own time, taken from the post's receipt, answers every Question of that Task posted in that thread and still open. A message written before the Question but relayed after it does not count, and neither does an old message edited later, since an edit moves its `seq` and not its time. The Gateway makes no judgement whether it really answers; a worker that needs more declares a new Question with a new id. |
| What does a person get when writing in a thread whose Task is gone? | A new Task, numbered after the last, joined with the retained thread history, if the Conversation still exists and the message passes admission. After the Conversation's retention it is a new thread, and the route's mention rule applies again. |
| How long is a Conversation kept? | While its tied Task exists, always. Untied or Orphaned, `gateway.retainMs` after the later of its last message and its orphaning, thirty days by default, then its controller deletes it by `uid` and `resourceVersion`, as ChangeRequest retention does. |
| How long can a conversation Task's declarations grow? | `status.declared` has no cap today and a long thread declares a Comment per reply. Recommend a store cap on declarations per Task, refused past it with `NeedsAttention/DeclaredFull`, so a person deletes the Task and the thread starts a new one with its history; the Gateway itself only keeps cursors. |
| Why does a Comment carry an `id`? | `fleet declare` treats an identical object as a no-op, so two identical Comments ("Done.") would be one. A per-Task unique `id` makes each its own declaration and its own post key. |
| Who deletes a conversation Task? | Nobody automatically in the first step: a person or a conductor, as for any Task. A route-level policy that deletes a Concluded Task idle for a set time is a later change. |
| How is the questions route overridden per Task? | The label `gateway.fleet.dev/route`, which a plugin or a person sets on the Task it creates. A TaskConfig field would also work, but TaskConfig is execution defaults and labels are already how plugins mark Tasks. |
| What vocabulary of declarations does the Gateway read? | `Question`, `Comment` and `Draft`, plus the existing `ChangeRequest` and the result. Unknown kinds are ignored, as the Forge controller ignores them. See the [interface](interface.md#declarations-the-gateway-reads). |
| What if a platform cannot recognise Fleet's posts? | An Unknown post outcome there is never repeated: it raises `NeedsAttention/PostUnconfirmed` and later posts to that thread wait for a person. See [adapters](adapters.md). |

## Implementation steps

Each step its own change, with its own E2E suite against the built CLI, a
fake gateway and a stub `curl`, keeping `cargo xtask check` green and adding
its crate edges, field managers and negative fixtures to
`crates/architecture.toml`:

| Step | What | Where | Evidence |
| --- | --- | --- | --- |
| 1 | The Gateway port, `fleet-gateway` with the fake adapter, the Conversation kind and ConversationController with numbering and retention, `fleet gateway receive`, `[gateway]` config | `fleet-api/src/gateway.rs`, `crates/fleet-gateway`, `fleet-controllers/src/conversation.rs`, `fleet-manager`, `fleet-cli` | `conversation_controller`, `e2e_gateway`, `store_contract`, `architecture` |
| 2 | Admission, routes and Task creation from a Conversation, the refusal post | `fleet-controllers/src/conversation.rs`, `fleet-api/src/gateway.rs` | `conversation_controller`, `e2e_gateway` |
| 3 | GatewayController: the tie, the join, the relay under a cursor, `status.answered` and the `answers` inbox field | `fleet-controllers/src/gateway.rs`, `fleet-store` (`record_relay` for `status.gateway`, `append_inbox`) | `gateway_controller`, `e2e_gateway_controller`, `store_contract` |
| 4 | Posting declarations, results and change news; opening a thread for a Question; children | `fleet-controllers/src/gateway.rs`, `fleet-manager` (the effects and receipts) | `gateway_controller`, `e2e_gateway_post` |
| 5 | The Slack adapter through `curl` | `crates/fleet-gateway/src/slack.rs` | Truth tables in the adapter's unit tests, recorded Socket Mode payloads read through `event`; `e2e_gateway` against the stub `curl` |
