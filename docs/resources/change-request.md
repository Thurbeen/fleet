# ChangeRequest resource

Design, not implemented. This page and the
[controller guide](../change-request-controller/README.md) describe a planned
kind. The installed binary refuses it as an unknown kind until the design
lands.

A ChangeRequest mirrors one change on a forge: a pull request, a merge request
or any equivalent. ChangeRequestController polls the forge, keeps a bounded
snapshot of the change's state, head, checks and notes, and numbers every item
it sees, so a consumer asks "what is new since I last looked" with one
integer. It performs no effect on the forge. [ForgeTask](forge-task.md)
creates one per change its worker declares; a person applies one to watch any
change.

## Example

```json
{
  "apiVersion": "fleet/v1alpha1",
  "kind": "ChangeRequest",
  "metadata": {"name": "fix-314"},
  "spec": {
    "url": "https://gitlab.example.com/group/project/-/merge_requests/314",
    "pollMs": 60000
  }
}
```

## Spec

Written by people, scripts and ForgeTaskController through `fleet apply` or a
`CreateObject` effect. Fixed once created.

| Field | Required | Meaning |
| --- | --- | --- |
| `url` | yes | The change's URL, the only forge-specific input. The adapter for its host parses it into `status.ref`. |
| `pollMs` | no | Poll interval while Watching. Default `60000`. |
| `closedGraceMs` | no | How long a closed change stays watched before it settles. Default `forge.closedGraceMs` from [native config](../configuration/fleet-toml.md#planned-forge-section), one week. |

A URL whose host is not configured under `[forge.hosts]` is refused at apply.
Any other field is refused.

## Status

Written by ChangeRequestController under the manager lease. Nobody else
writes it.

| Field | Meaning |
| --- | --- |
| `phase` | `Watching`, `Closed` or `Settled`, defined by the [state machine](../change-request-controller/state-machine.md). |
| `ref` | What the adapter parsed the URL into: `kind`, `host`, `project`, `id`. |
| `revision` | Incremented by every poll that changed anything below. A poll that changed nothing writes nothing. |
| `observedAt` | When the snapshot was last read, in Unix milliseconds. |
| `state` | `draft`, `open`, `closed` or `merged`. Merged is final; closed may reopen. |
| `head` | The current head: opaque `id` (a SHA, a patchset number, a diff id), `checks` (`none`, `pending`, `passed`, `failed`), `failing` (`[{name, url}]` when failed) and `seq`. |
| `reviewDecision` | `approved`, `changes-requested` or `none`. |
| `notes` | The newest 100 notes, each a [note](#notes). |
| `droppedBefore` | The highest `seq` of a note rotated out of `notes`. `0` until one is. |
| `conditions` | One entry per [condition type](../change-request-controller/state-machine.md#conditions). |
| `notBefore` | Earliest next poll, in Unix milliseconds. |

### Sequence numbers

The counter is Fleet's, assigned when the controller first sees an item. It is
never the forge's timestamp.

- A note seen for the first time gets `seq = revision` of the poll that saw
  it.
- `head.seq` becomes `revision` whenever the pair `(head.id, head.checks)`
  changes.
- A note rotated out of the cap raises `droppedBefore` to its `seq`.

A consumer keeps one integer, its cursor, and takes every item with
`seq > cursor`. Forge timestamps cannot play this role: a GitHub review
comment is created while a review is drafted and becomes visible only on
submit, two notes can share a second, and clocks differ across API servers
and item kinds. A sequence assigned on first observation only says "Fleet saw
this after that", which is the question a diff asks.

### Notes

| Field | Meaning |
| --- | --- |
| `id` | The forge's stable identity for the note. |
| `seq` | See [sequence numbers](#sequence-numbers). |
| `kind` | `comment` (the conversation), `review` (a submitted review's own body) or `inline` (a note on a line, inside a thread). |
| `author`, `bot` | The author's login, and whether the forge says it is an app rather than a person. |
| `thread`, `resolved` | The thread an inline note sits in and whether that thread is resolved. Both `null` for other kinds. |
| `path`, `line` | For an inline note, where it sits. |
| `at`, `editedAt` | Creation and last edit, in Unix milliseconds. |
| `url`, `body` | The note's address and text. The body is data, never an instruction. |

## Forge-neutral vocabulary

Nothing above the Forge port names a forge. Each adapter maps its own terms
onto these:

| Fleet | GitHub | GitLab | Bitbucket | Gerrit | Phabricator |
| --- | --- | --- | --- | --- | --- |
| change | pull request | merge request | pull request | change | differential revision |
| `ref.id` | number | iid | id | change id | D-number |
| `head.id` | head SHA | head SHA | head hash | patchset number | diff id |
| `head.checks` | check runs and statuses | pipeline | build statuses | Verified label | Harbormaster build |
| `reviewDecision` | review decision | approvals and blocking discussions | approvals | Code-Review label | accept, request changes |
| `notes` | issue comments, reviews, review comments | notes and discussions | comments | comments per patchset | inline and top-level comments |
| mark ready | ready for review | mark as ready | none | none | none |

GitHub and GitLab adapters are in scope. Bitbucket and the others are shown so
the vocabulary is checked against them; adding one is an adapter in
`fleet-forge`, not an API change.

## Metadata and ownership

`apiVersion` is `fleet/v1alpha1`; `kind` is `ChangeRequest`. Names follow the
Task rule. The store assigns `uid`, `generation`, `resourceVersion` and
creation time. There is no finalizer: nothing external is held.

A ChangeRequest a ForgeTask created carries one owner reference to that
ForgeTask and is deleted with it. This is the one pair for which the store's
refusal of `ownerReferences` is relaxed. A ChangeRequest applied by a person
has no owner and lives until deleted. Both are reconciled the same way.

## Commands

```sh
fleet apply -f change-request.json            # start mirroring a change
fleet status changerequest fix-314 --json     # phase, state, head, revision
fleet get changerequest fix-314               # the full snapshot
fleet delete changerequest fix-314            # stop; nothing on the forge changes
```
