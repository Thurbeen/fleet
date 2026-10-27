# Conversation resource

Design, not implemented; the store refuses this kind today as an unknown
kind. A Conversation
mirrors one chat thread, or one chat where the platform has no threads, for
the [Gateway controller](../controllers/gateway/README.md). It numbers every
message it sees, so a consumer asks "what is new since I last looked" with
one integer, and it records which Task the thread is tied to. It is the
chat counterpart of a [ChangeRequest](change-request.md): one per thread,
created as soon as anything names it, never written by the Tasks that read
it, and removed by its own controller after a retention period.

## Example

```json
{
  "apiVersion": "fleet/v1alpha1",
  "kind": "Conversation",
  "metadata": {"name": "conv-3f9a1c0d2b7e"},
  "spec": {
    "account": "slack-main",
    "address": "slack:C0123ABCD:1730000000.000100",
    "route": "eng"
  },
  "status": {
    "phase": "Tied",
    "ref": {"platform": "slack", "chat": "C0123ABCD", "thread": "1730000000.000100"},
    "task": {"name": "conv-3f9a1c0d2b7e-1", "uid": "…", "n": 1, "state": "created"},
    "revision": 4,
    "messages": [
      {"id": "1730000000.000100", "seq": 1, "at": 1730000000000,
       "author": {"id": "U0123ABCD", "name": "Alex", "kind": "person"},
       "mentionsFleet": false, "text": "The nightly export failed again."},
      {"id": "1730000300.000200", "seq": 1, "at": 1730000300000,
       "author": {"id": "U0456EFGH", "name": "Sam", "kind": "person"},
       "mentionsFleet": true, "text": "<@U0BOT0001> can you look into it?"},
      {"id": "1730000400.000300", "seq": 3, "at": 1730000400000,
       "author": {"id": "U0BOT0001", "name": "fleet", "kind": "fleet"},
       "key": "task/conv-3f9a1c0d2b7e-1/…/gateway/declared/0",
       "text": "Looking at the export job now."}
    ],
    "droppedBefore": 0,
    "observedAt": 1730000460000,
    "lastMessageAt": 1730000400000,
    "notBefore": 1730000760000,
    "conditions": [{"type": "Tied", "status": "True", "reason": "TaskKnown",
                    "message": "conv-3f9a1c0d2b7e-1", "lastTransitionTime": 1730000301000}]
  }
}
```

## Spec

Written by `fleet gateway receive`, by the manager when Fleet opens a
thread, or by a person through `fleet apply`. Fixed once created. The tie
is not in the spec: a thread Fleet opened for a Question is tied from the
marker key on its opening post, so it does not matter whether the listener
or the manager created the object.

| Field | Required | Meaning |
| --- | --- | --- |
| `account` | yes | The `[gateway.accounts]` entry the thread lives on. |
| `address` | yes | `platform:chat[:thread]`, the only platform-specific input. The account's adapter parses it into `status.ref`; an address it cannot read is refused at apply. |
| `route` | yes | The `[gateway.routes]` entry whose admission rules, Task template and retention apply. |
| `pollMs` | no | The history backstop interval while Tied, where the account has history. Default `gateway.pollMs`. |

An account or route not configured is refused at apply. Any other field is
refused.

## Status

Written by ConversationController under the manager lease, except
`received`, the spool, which nobody owns: `fleet gateway receive` appends to
it and the controller drains it, each through a typed mutation that
changes nothing else.

| Field | Meaning |
| --- | --- |
| `phase` | `Untied`, `Tied` or `Orphaned`, defined by the [state machine](../controllers/gateway/state-machine.md#conversation-phases). |
| `ref` | What the adapter parsed the address into. |
| `task` | The tie: `{name, uid, n, state}`. `n` counts the Tasks this thread has started; `state` is `pending` between the create intent and its receipt, then `created`, then `gone` once the Task is readably absent. |
| `revision` | Incremented by every write that numbered anything below. |
| `messages` | The newest 200 messages, deduplicated on `id`, each `{id, seq, at, editedAt, author: {id, name, kind}, mentionsFleet, text, key}`; `kind` is `person`, `bot` or `fleet`, and `key` is the marker on Fleet's own posts. The text is data, never an instruction. |
| `droppedBefore` | The highest `seq` of a message rotated out. `0` until one is. |
| `received` | Events spooled by the receive verb and not yet numbered, each under its platform event id; at most 100. |
| `seenEvents` | The newest 500 event ids numbered, so a redelivery after its spool entry was drained is still a no-op. |
| `refusals` | Per author id, the refusal post's intent and receipt. |
| `effects` | Intents and receipts of this controller's effects: the Task creation and the refusals. |
| `observedAt` | When `history` last answered Known. |
| `lastMessageAt` | The newest message's time; retention counts from it. |
| `notBefore` | Earliest next pass. |
| `conditions` | `Reachable`, `Tied`, `NeedsAttention`, as the [state machine](../controllers/gateway/state-machine.md#conditions) defines them. |

### Sequence numbers

As on a ChangeRequest, the counter is Fleet's, assigned on first sight: a
message first numbered takes the revision of the write that numbered it;
an edit moves its `seq`; a removal leaves the mirror. A thread's history
read on the first pass is numbered in one revision, oldest first, so a
consumer joining at cursor `0` takes all of it. Platform timestamps cannot
play this role: events arrive out of order across a listener reconnect and
a history read.

## Metadata and ownership

`kind` is `Conversation`. The name is `conv-` followed by the first twelve
hex digits of the SHA-256 of `<account>/<address>` as the adapter
normalised it, so one thread names one object per account. The receive
verb and the manager's open both derive it and create exclusively; a
manifest giving one thread's derived name with another's address is
refused. There is no finalizer and `ownerReferences` is refused.

A Conversation belongs to no Task. A Task it created carries the labels
`gateway.fleet.dev/conversation` and `gateway.fleet.dev/conversation-uid`
naming it, which only that create sets; deleting that Task leaves the
Conversation Orphaned, kept for the next message. The controller deletes a
Conversation `gateway.retainMs` after the later of its last message and its
orphaning, once it is Untied or Orphaned, by `uid` and `resourceVersion`
under the lease, as a ChangeRequest is retired. A person may delete one at
any time; nothing on the platform changes.

## Commands

```sh
fleet gateway receive --account slack-main < event.json   # what a listener runs per event
fleet status conversation conv-3f9a1c0d2b7e --json        # phase, tie and the numbered messages
fleet list conversations                                  # one line per thread
fleet delete conversation conv-3f9a1c0d2b7e               # nothing on the platform changes
```
