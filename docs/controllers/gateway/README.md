# Gateway controller

Design, not implemented. The installed binary has no `[gateway]` key, port,
crate or resource kind from this page. A `Conversation` manifest is
refused today as an unknown kind.

The Gateway ties chat conversations to Tasks. It carries messages into Fleet
and out of Fleet. It is not a domain of its own.

The basic flow is:

1. A person mentions Fleet's bot in a thread.
2. Fleet starts a Task for that thread.
3. From then on, every message in the thread reaches the Task's inbox.
4. What the Task declares reaches the thread.

The Gateway never runs a model. A Task does everything agentic. The Gateway
does three things:

- It admits people.
- It keeps the tie between a thread and its Task.
- It moves messages.

The Gateway is the [Forge controller](../forge/README.md) applied to chat:

- A Task declares what it wants said.
- A mirror object follows the thread.
- Fleet relays new messages into the Task's inbox under a cursor.
- Fleet recognises its own posts by a marker.
- One adapter per platform sits behind one port.

Slack is the first adapter. Telegram, Discord and Microsoft Teams are mapped
on paper, to check the model.

| Read | Answers |
| --- | --- |
| [Conversation](../../resources/conversation.md) | What does the mirror of one thread hold? Who deletes it? |
| [Listener](../../resources/listener.md) | Is the listener of an account running, connected and current? |
| [Task gateway status](../../resources/task.md#talking-in-a-conversation) | What does a Task declare to talk? What do `status.gateway` and `status.answered` hold? |
| [State machine](state-machine.md) | How does a thread move between Untied, Tied and Orphaned? What does Fleet relay or post, and when? |
| [Interface](interface.md) | What are the port, the receive verb, the probes, the effects, the keys and the write rules? |
| [Adapters](adapters.md) | How do Slack, Telegram, Discord and Teams map onto the port? What did each platform change in the model? |
| [Native config](../../configuration/fleet-toml.md#the-gateway-section) | Where do accounts, people, routes and admission come from? |
| [Forge controller](../forge/README.md) | The controller this one is modelled on. |
| [Overall architecture](../../ARCHITECTURE.md#the-gateway-controller) | Where would this fit in Fleet? |

## One run: a mention starts a Task

Terms:

- A **listener** is a resident process outside the manager. It holds the
  connection to the platform.
- The **spool** is the list of received events on a Conversation that the
  controller has not numbered yet.

1. A person writes in a channel that a route covers. The person is on the
   allowlist of the route. The message mentions Fleet's bot. The thread can
   be new, or an old thread that already has messages.
2. The platform delivers the event to a listener. Then:
   1. The listener puts the raw payload in an envelope. The envelope says
      how the listener authenticated the payload.
   2. The listener hands the envelope to `fleet gateway receive`, a bounded
      manager verb.
   3. The verb asks the adapter to read the payload into a platform-neutral
      event.
   4. The verb keeps two types of event. It keeps an event for a thread that
      Fleet already mirrors. It also keeps an event that could start a Task
      or earn a refusal. The verb drops every other event.
   5. The verb appends a kept event to the spool of the thread's
      [Conversation](../../resources/conversation.md). If the Conversation
      does not exist, the verb creates it under a name derived from the
      address.
   6. The listener acknowledges the event to the platform only after the
      verb exits zero.
3. On the next sweep, ConversationController numbers the spooled event into
   the mirror. Then:
   1. For a thread it has never seen, the controller first asks the adapter
      for the whole history of the thread. So a mention partway through a
      thread brings everything before it.
   2. The mention passes the admission rules of the route.
   3. The plan creates one Task from the route. The Task gets the TaskConfig,
      brief and workspace of the route. The Task is labelled with the name
      and `uid` of the Conversation.
   4. The plan records the name and `uid` of the Task as the tie of the
      thread.
4. GatewayController is a controller over that Task. It finds the tie and
   joins at the beginning of the thread:
   - Its first relay is one inbox entry that holds the whole retained thread.
   - Each message in the entry is labelled with its author.
   - Each message is also labelled with whether its author is on the
     allowlist.
   - TaskController delivers this entry after the brief.
5. GatewayController relays every later message in the thread from a person
   on the allowlist:
   - Everything that arrived since the last relay goes out as one inbox
     entry.
   - The cursor advances in the same write.
   - Every admitted message is a worker turn. Nothing decides whether an
     admitted message deserves a turn.
   - A message from a person who is not on the allowlist starts no turn. It
     waits above the cursor and goes out with the next message from a person
     on the allowlist.
   - Such a message is labelled as not from a person on the allowlist. It is
     content, never instructions.
6. The worker answers by declaring:
   - `fleet declare TASK --json '{"kind":"Comment","id":"c1","text":"…"}'`
     says something.
   - A `Question` asks something.
   - A `ChangeRequest` declares a change that the worker opens.
   - `fleet result` reports.

   For each declaration:
   1. GatewayController renders it into one post.
   2. GatewayController writes the intent of the post with its key.
   3. The manager posts it through the Gateway port.
   4. The receipt lands on the Task.

   The worker holds no platform credential. The worker names no destination.
7. The thread lives as long as people write in it:
   - A Concluded Task still receives what Fleet relays, as it does today.
   - If someone deletes the Task, the Conversation becomes Orphaned.
   - Then the next message in the thread from a person on the allowlist
     starts a new Task. Fleet joins that Task with the history of the thread.
8. An Orphaned or Untied Conversation that sees no message for its retention
   period is deleted by its own controller. Nothing on the platform changes.

## One run: a Question opens a thread

1. A Task that nobody started from chat declares
   `{"kind":"Question","id":"q1","text":"…"}`. For example, a Notion factory
   plugin created the Task.
2. GatewayController sees a Question on a Task that has no tie and no
   parent. Then:
   1. It picks the questions route. The route comes from the
      `gateway.fleet.dev/route` label of the Task, or else from the
      `[gateway]` default.
   2. It writes an `Open` intent under
      `task/<name>/<uid>/gateway/question/q1`.
3. The manager opens the thread:
   1. The manager opens a new thread in the question channel of that route.
      The first post of the thread is the Question. The post carries the key
      as its marker.
   2. The manager writes the receipt.
   3. On its next pass, GatewayController plans a second keyed step,
      `Receive`.
   4. The manager performs `Receive` against the store, as the receive verb
      would. `Receive` ensures the Conversation for the new thread exists and
      spools the first post.
   5. Either the listener's keyed copy of the first post or `Receive` can
      create the Conversation. In both cases, ConversationController ties
      the thread to the Task that the marker key names.
4. Fleet relays replies in that thread to the inbox of the Task:
   - The first reply from a person on the allowlist that was written after
     the post of the Question records the Question as answered on the Task,
     in `status.answered.q1`.
   - This record is in the same write that queues the reply.
   - A Notion factory plugin can show the same Question as "Needs input".
     The plugin reads `status.answered` and stops showing the Question.
   - If the answer arrives in the plugin first, the inbox append of the
     plugin records the answer. Then the thread stops waiting.
   - Fleet does not repost, nudge or remind.
5. A `Comment`, news about a change, or a result never opens a thread. Fleet
   posts them only on a Task that is already tied.

## The Forge pattern, applied to chat

| Forge | Gateway | Why the same |
| --- | --- | --- |
| A forge host, one adapter per kind (`gh`, `glab`, `az`, `curl`) | A chat account, one adapter per platform | Nothing above the port names a platform. The adapter translates once. |
| `ChangeRequest`, the mirror of one change, named from its canonical ref | `Conversation`, the mirror of one thread, named from its canonical address | One reader per thread, however many consumers. An unreachable platform is a condition of the mirror, never a state of the thread. |
| `revision` and per-note `seq` | `revision` and per-message `seq` | A consumer diffs with one integer. |
| ForgeController over the Task, owning `status.forge` | GatewayController over the Task, owning `status.gateway` | A second writer on the Task. It has its own subtree and writes nothing else. |
| Relay into the inbox under a cursor, in one CAS write (`record_relay`) | The same mutation, for `status.gateway` | The cursor and the entries land together or not at all. |
| The worker mark `<!-- fleet:worker <uid> -->` | A platform marker that carries the intent key of the post | Fleet tells its own words from the words of a person. Fleet recovers an Unknown post by looking for the key. |
| Mark-ready, the one forge write: keyed, intent before and receipt after | `Open` and `Post`: keyed, intent before and receipt after | Fleet never repeats a key. Fleet answers an Unknown outcome by observation. |
| `fleet declare --change-request` ensures the mirror | `fleet gateway receive` ensures the mirror | A mirror exists as soon as anything names it. It is created exclusively, by name. |

Where the two controllers differ, the difference comes from chat:

- **A chat pushes.** Fleet polls a forge. A chat platform delivers events
  over a WebSocket or a webhook. It offers history only as a backstop, or not
  at all. So Fleet adds an inbound verb and a listener. See
  [inbound transport](#inbound-transport).
- **A thread creates its Task.** A Task that already exists opens a change.
  A conversation starts before any Task exists. So the controller of the
  mirror creates the Task, through the same create verb that the plan of a
  plugin uses.
- **Fleet speaks.** The Forge controller writes one thing on a forge. The
  Gateway posts on every declaration:
  - Each post is a keyed effect.
  - Posts to one thread go out in declaration order.
  - Only one post per thread is unresolved at a time.

## Every write is local

Each writer stays in its own area:

- ConversationController writes only Conversations. It creates Tasks through
  the create verb of the manager.
- The plans of GatewayController write only `status.gateway` and the inbox of
  the Task itself. The one exception is the spool append below.
- GatewayController reads Conversations, ChangeRequests and child Tasks by
  name or label.
- Two writes reach a Conversation from outside its controller:
  - the receive verb,
  - the manager when it performs a `Receive` action, which records the post
    that Fleet just made.

  Both writes use the one typed spool append. Neither write touches any
  other part of the status.
- The beats and refusals of a Listener belong to the listener. Only the
  receive verb writes them. ListenerController owns the rest of the
  Listener.
- `status.answered` has no single owner, like `status.inbox`. Only the typed
  inbox append that carries the answer writes it. The first writer wins.

Posting is the only effect that either controller performs.

## Where it lives

The Gateway is a built-in controller, not a Fleet plugin. A plugin would
need these things:

- **Every Task.** The Gateway reads the declarations of any Task. It posts
  for any Task that asks a Question. A plugin would need `tasks = "any"`,
  the widest right that Fleet has. A built-in does the same work as a named
  field manager with one subtree.
- **A Task subtree.** The relay moves the cursor in the same write as the
  inbox append. That needs a status subtree on the Task. The
  [Plugin controller](../plugin/README.md#scope) puts a plugin-owned subtree
  on a built-in kind out of scope. So a plugin would have to keep its cursor
  on its own object. It would also have to append without the atomic
  guarantee that `record_relay` gives.
- **Waking on a Task.** A plugin object is due only on `notBefore`, a
  `generation` change or deletion. A new declaration on a Task does not wake
  it. So every Comment would wait for the poll of the plugin.
- **A resident connection.** A plugin process serves one bounded call. The
  platforms require a resident listener in both designs. So a plugin saves
  nothing there.
- **Kinds that another component writes.** The receive verb must ensure a
  Conversation. Only the plans of a plugin create the kinds of that plugin.

The Notion factory is the opposite case:

- Its records are its own.
- It needs only its own Tasks.
- Its cadence is a poll.

So it stays a plugin. It meets the Gateway only on a Task: in
`status.declared`, in the inbox and in `status.answered`.

## Inbound transport

Fleet's rule is bounded calls, and no service of its own to supervise. Every
platform except one pushes:

| Platform | What it offers | What it forces |
| --- | --- | --- |
| Slack | Socket Mode, a WebSocket; or the Events API, HTTPS to a public URL | A resident connection or a public endpoint. History is readable, with rate limits. |
| Telegram | `getUpdates`, a long poll with an offset; or a webhook | Nothing resident. A bounded call with a cursor works. No history. |
| Discord | The Gateway, a WebSocket. No other way to receive channel messages | A resident connection. History is readable. |
| Microsoft Teams | HTTPS to the messaging endpoint of the bot | A public endpoint. History through Microsoft Graph, with a separate permission. |

The options:

| Option | For | Against |
| --- | --- | --- |
| (a) Poll inside bounded calls | Fits Fleet exactly. The Telegram offset is a natural cursor. | Fleet cannot poll Discord or Teams for new mentions. Slack's history methods have rate limits per channel. To find a mention in an untied thread, Fleet must read every channel the route covers. |
| (b) `fleet up` holds the connection | One resident process already exists. | The manager gains a network service and a WebSocket stack. A listener stall shares fate with reconciliation. `fleet reconcile` alone can never receive. |
| (c) A separate listener hands events to a manager verb | The manager stays bounded. The verb is one store write, like `fleet send`. The listener holds nothing durable. | One more resident process for the operator to run. |

Recommendation: **(c)**, for every platform.

The listener is transport only. It does three things:

1. It holds the connection or the endpoint.
2. It authenticates the platform: the Socket Mode token, or the signature or
   JWT of a webhook.
3. It pipes each raw payload, in its envelope, into
   `fleet gateway receive --account NAME`.

Then:

- The verb runs the pure `event` reading of the adapter. So platform parsing
  stays in `fleet-gateway`, with its truth tables.
- The verb refuses a duplicate delivery id. So at-least-once redelivery by a
  platform is harmless.
- The listener acknowledges only after the verb exits zero.

A listener crash loses only unacknowledged events. Either the platform
redelivers them or, for tied threads, the history backstop recovers them.

The Telegram listener is a loop of bounded `getUpdates` calls. It confirms
an offset only after the verb succeeds. That loop needs no connection, so a
later step may run it inside `fleet up`, as option (a).

A listener is not part of the Fleet runtime:

- `fleet-gateway` stays an adapter of bounded calls through `fleet-exec`. It
  has no WebSocket client and no resident code.
- The first listener is for Slack Socket Mode. It is a separate small
  program, maintained outside the workspace, as plugin SDKs are. It is
  written against Slack's own SDK.
- Any program that holds the connection and runs the verb per event is a
  listener.
- The service manager of the operator runs the listener.

Fleet supervises nothing, but it observes the listener:

- A listener reports its state through `fleet gateway receive --beat`.
- Each beat carries the digest of the account configuration that the
  listener started from.
- The [Listener](../../resources/listener.md) of the account shows three
  facts:
  - accepted: the config is valid and routed,
  - resolved: the platform confirms the bot identity of Fleet,
  - inbound-ready: a fresh beat, connected, from the current configuration.
- A dead listener becomes a False condition within one sweep. So does a
  listener left on an old configuration. Neither is ever a silence that looks
  healthy.

Each delivery reaches the verb in an envelope:

- The envelope states how the listener authenticated the delivery: an
  authenticated connection or a verified signature.
- The `inbound` setting of the account fixes which method is allowed. The
  verb refuses any other.
- The spool is keyed by the delivery's own id. This is the platform's id
  where the platform has one.
- So a redelivery is a duplicate, and an edit of a message is not a
  duplicate.

## Scope

In scope:

- accounts, people and routes in native config,
- admission: the allowlist, mention rules, which authors count per channel,
  and one polite refusal,
- the Conversation mirror, with numbered messages and retention,
- one Task per thread, created from a route,
- relaying a thread into the inbox of its Task under a cursor,
- posting what a Task declares, keyed, in order,
- opening a thread for a Question from an untied Task,
- recording answers,
- funnelling the words of a child Task to its parent,
- the Slack adapter,
- the receive verb that a listener calls, and the Listener that reports the
  health of the listener.

Out of scope, deliberately:

- merging, as for the Forge controller,
- suspending and resuming idle sessions,
- Notion, which belongs to a plugin,
- any judgement about the worth of a message: an intake model, a planner or a
  nudging heartbeat,
- implementing the Telegram, Discord or Teams adapters. These are mapped in
  [adapters](adapters.md) only to check the model,
- reactions, files, edits by Fleet of its own posts, and interactive
  components,
- the listener programs themselves.

## Open questions and recommendations

| Question | Recommendation |
| --- | --- |
| Where does the thread history go when a mention lands partway through a thread? | In the first inbox entry, as the join message of the Forge controller, not in the brief. The brief stays the brief of the route. It is identical for every conversation Task and frozen at creation. The history can be long, and it is data. |
| What is the brief of a conversation Task? | The `brief` file of the route. The manager reads it when it loads the config. The manager copies it into the spec of each Task when it creates the Task. TaskConfig does not define briefs or workspaces. So a route names a TaskConfig for execution defaults, and the route itself carries the brief and the workspace. |
| What workspace does a conversation Task get? | A route setting, today a directory. Every Task of the route would share it. Two Tasks that write through one directory break the Fleet rule of one workspace per Task. So Fleet serialises the route: its creates carry an `exclusive` precondition, and the manager starts no Task while another Task has that directory. A waiting thread raises `WorkspaceBusy` and starts when the workspace is free. Concurrent conversations on one route wait for provisioned workspaces: a worktree per Task from the repository of the route, on a branch derived from the name of the Conversation. That is a separate change. |
| How is a child tied to its parent? | By two labels on the child: `gateway.fleet.dev/parent: <parent Task name>` and `gateway.fleet.dev/parent-uid: <its uid>`. Only the create of the child sets them. Only a writer that may already append to the inbox of the parent can set them: a person, or a plugin whose Task scope covers the parent. Naming a parent writes to its inbox. So a `tasks = "own"` plugin cannot name a Task it does not own. The GatewayController of the parent lists its children by the name label. It relays only children whose `parent-uid` is its own. It relays their Questions, Comments, Drafts and results into its own inbox. The GatewayController of the child never opens or ties a thread. |
| A child whose parent is readably gone? | Fleet treats the child as untied. Its next Question opens a thread through the questions route, because nobody else will answer it. Fleet does not replay its earlier declarations. |
| How is an answer recognised in a thread? | By the first message from a person on the allowlist whose platform time is after the time of the Question post. That time comes from the receipt of the post. This message answers every Question of that Task that is posted in that thread and still open. These messages do not count: a message written before the Question but relayed after it, and an old message edited later, because an edit moves its `seq` and not its time. A platform can have neither sub-second time nor message order. Only there, the time Fleet first saw a message decides a same-second message. The Gateway does not judge whether the message really answers. A worker that needs more declares a new Question with a new id. |
| What does a person get when writing in a thread whose Task is gone? | A new Task, numbered after the last one, and joined with the retained history of the thread. This happens if the Conversation still exists and the message passes admission. After the retention of the Conversation, the thread is a new thread, and the mention rule of the route applies again. |
| How long is a Conversation kept? | Always, while its tied Task exists. When it is Untied or Orphaned, `gateway.retainMs` after the later of its last message and its orphaning. The default is thirty days. Then its controller deletes it by `uid` and `resourceVersion`, as ChangeRequest retention does. |
| How long can a conversation Task's declarations grow? | Today `status.declared` has no cap, and a long thread declares a Comment per reply. Recommendation: a store cap on declarations per Task. Past the cap, the store refuses with `NeedsAttention/DeclaredFull`. Then a person deletes the Task, and the thread starts a new one with its history. The Gateway itself only keeps cursors. |
| Why does a Comment carry an `id`? | `fleet declare` treats an identical object as a no-op. So two identical Comments ("Done.") would be one declaration. An `id` unique per Task makes each Comment its own declaration, with its own post key. |
| Who deletes a conversation Task? | In the first step, nothing deletes it automatically. A person or a conductor deletes it, as for any Task. On a serialised route, the next thread waits for that deletion, under `WorkspaceBusy`. A later change can add a route policy that deletes a Concluded Task after it is idle for a set time. |
| How is the questions route overridden per Task? | By the label `gateway.fleet.dev/route`, which a plugin or a person sets on a Task it creates. A TaskConfig field would also work. But TaskConfig holds execution defaults, and plugins already mark Tasks with labels. |
| What vocabulary of declarations does the Gateway read? | `Question`, `Comment` and `Draft`, plus the existing `ChangeRequest` and the result. The Gateway ignores unknown kinds, as the Forge controller does. See the [interface](interface.md#declarations-the-gateway-reads). |
| What if a platform cannot recognise Fleet's posts? | Fleet never repeats an Unknown post outcome there. It raises `NeedsAttention/PostUnconfirmed`. Later posts to that thread wait for a person. See [adapters](adapters.md). |

## Implementation steps

Each step is its own change. Each step:

- has its own E2E suite against the built CLI, a fake gateway and a stub
  `curl`,
- keeps `cargo xtask check` green,
- adds its crate edges, field managers and negative fixtures to
  `crates/architecture.toml`.

| Step | What | Where | Evidence |
| --- | --- | --- | --- |
| 1 | The Gateway port; `fleet-gateway` with the fake adapter; the Conversation and Listener kinds; ConversationController with numbering and retention; ListenerController; `fleet gateway receive` with its envelope and `--beat`; `fleet gateway account` and `status`; `[gateway]` config | `fleet-api/src/gateway.rs`, `crates/fleet-gateway`, `fleet-controllers/src/conversation.rs`, `fleet-manager`, `fleet-cli` | `conversation_controller`, `e2e_gateway`, `store_contract`, `architecture` |
| 2 | Admission; routes; Task creation from a Conversation with the `exclusive` workspace precondition; the refusal post | `fleet-controllers/src/conversation.rs`, `fleet-api/src/gateway.rs` | `conversation_controller`, `e2e_gateway` |
| 3 | GatewayController: the tie; the join; the relay under a cursor; `status.answered` and the `answers` inbox field; the parent labels and their scope check | `fleet-controllers/src/gateway.rs`, `fleet-store` (`record_relay` for `status.gateway`, `append_inbox`) | `gateway_controller`, `e2e_gateway_controller`, `store_contract` |
| 4 | Posting declarations, results and change news; opening a thread for a Question, and its `Receive`; children | `fleet-controllers/src/gateway.rs`, `fleet-manager` (the effects and receipts) | `gateway_controller`, `e2e_gateway_post` |
| 5 | The Slack adapter through `curl` | `crates/fleet-gateway/src/slack.rs` | Truth tables in the unit tests of the adapter; recorded Socket Mode payloads read through `event`; `e2e_gateway` against the stub `curl` |
