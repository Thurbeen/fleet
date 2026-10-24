# ChangeRequest controller

Design, not implemented. These pages describe a planned controller; nothing
here is served by the installed binary.

A ChangeRequest mirrors one change on a forge. Its controller decides, from
the stored snapshot and one forge observation, whether anything changed and
when to look again. It performs no effect: the forge is read, never written,
from here.

| Read | Answers |
| --- | --- |
| [ChangeRequest](../resources/change-request.md) | What do I apply, and what does the snapshot contain? |
| [State machine](state-machine.md) | How does a change move between Watching, Closed and Settled? |
| [Interface](interface.md) | What are the probes, the Forge port and the write rules? |
| [ForgeTask controller](../forge-task-controller/README.md) | Who consumes the snapshot, and how? |
| [Native config](../configuration/fleet-toml.md#planned-forge-section) | How are forge hosts and adapters configured? |
| [Overall architecture](../ARCHITECTURE.md) | Where does this controller fit in Fleet? |

## One run

1. A ChangeRequest is applied, by a person or by a ForgeTask. The adapter for
   its host parses the URL into `status.ref`.
2. On each due pass the controller asks one probe, `Change { ref }`. The
   manager answers it through the Forge port.
3. A Known answer is compared with the stored snapshot. If anything differs,
   `revision` is incremented, new notes and a changed head get that `seq`,
   and the snapshot is written. If nothing differs, nothing is written.
4. An Unknown answer sets `Reachable=Unknown`, leaves the snapshot untouched
   and waits one poll interval. Unknown never means closed, merged or empty.
5. A merged change settles. A closed change is watched more slowly for a
   grace period, then settles; reopened within it, it is watched again.
6. Deletion removes the record. There is no finalizer.

Polling cadence is `status.notBefore`, which the manager already honours for
Tasks. One ChangeRequest costs one bounded forge call per interval and nothing
else while idle.

## Why a resource of its own

- One poller per change, however many consumers care. A ForgeTask, a
  reviewer and a conductor read the same snapshot instead of each calling the
  forge.
- The forge's vocabulary is translated once, in the adapter. No consumer sees
  a GitHub or GitLab field.
- An unreachable forge is this controller's condition. No consumer can
  mistake it for a state of the change.

## Implementation and evidence

Planned. The first implementation step is this controller together with the
`fleet-forge` crate: the Forge port, the `github` and `gitlab` adapters, the
file-backed fake, sequence numbers and the closed grace, each with an E2E
suite driving the built CLI against stub `gh` and `glab` executables. The
[ForgeTask guide](../forge-task-controller/README.md#implementation-and-evidence)
lists the steps that follow.
