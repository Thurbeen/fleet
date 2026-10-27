# Listener resource

Design, not implemented; the store refuses this kind today as an unknown
kind. A Listener records the inbound health of one chat account for the
[Gateway controller](../controllers/gateway/README.md). Fleet makes no
inbound call: a separate listener program holds the platform's connection
and hands each delivery to `fleet gateway receive`. Whether that program is
running, connected and current is what it reports here, and what
ListenerController judges against the configuration Fleet runs.

## Example

```json
{
  "apiVersion": "fleet/v1alpha1",
  "kind": "Listener",
  "metadata": {"name": "listener-slack-main"},
  "spec": {"account": "slack-main"},
  "status": {
    "accepted": {"digest": "sha256:9c1e…", "at": 1730000000000},
    "beat": {"state": "connected", "digest": "sha256:9c1e…", "at": 1730000420000,
             "lastDeliveryAt": 1730000300100},
    "observedAt": 1730000400000,
    "notBefore": 1730000600000,
    "conditions": [
      {"type": "Accepted", "status": "True", "reason": "Loaded", "lastTransitionTime": 1730000000000},
      {"type": "Resolved", "status": "True", "reason": "IdentityMatches", "lastTransitionTime": 1730000001000},
      {"type": "InboundReady", "status": "True", "reason": "Connected", "lastTransitionTime": 1730000002000}
    ]
  }
}
```

## Spec

Written by the manager when it loads the config: one Listener per
configured account, named `listener-<account>`, by exclusive creation.
Fixed once created.

| Field | Required | Meaning |
| --- | --- | --- |
| `account` | yes | The `[gateway.accounts]` entry. |

A manifest through `fleet apply` is refused: the config is the only source.

## Status

| Field | Writer | Meaning |
| --- | --- | --- |
| `accepted` | `listener-controller` | `{digest, at}`: the account's digest in the loaded config, the one `fleet gateway account NAME --json` prints for a listener to start from, and when it was loaded. |
| `beat` | `listener/<account>`, through `fleet gateway receive --beat` | The last beat: `{state, digest, at, lastDeliveryAt}`, `state` being `connecting`, `connected` or `closing`. Replaced whole by each beat, through the typed mutation `record_beat`, which changes nothing else. |
| `observedAt` | `listener-controller` | When `whoami` and `capabilities` last answered Known. |
| `notBefore` | `listener-controller` | The earlier of the next `gateway.pollMs` and the moment the last beat goes stale. |
| `conditions` | `listener-controller` | `Accepted`, `Resolved` and `InboundReady`, as the [interface](../controllers/gateway/interface.md#listenercontroller) defines them. |

`InboundReady` is True only for a beat that is `connected`, carries the
accepted digest and is younger than three `gateway.beatMs`. A listener that
stops, stalls or runs on an older configuration turns it False within one
sweep. A connected listener that has delivered nothing is still True, with
its `lastDeliveryAt` beside it: Fleet cannot see a platform's subscription
settings, and does not guess.

## Metadata and ownership

`kind` is `Listener`. There is no finalizer and `ownerReferences` is
refused. ListenerController deletes a Listener whose account left the
config, by `uid` and `resourceVersion` under the lease; the manager creates
it again if the account returns. Deleting one by hand changes nothing on the
platform and it is created again at the next config load.

## Commands

```sh
fleet gateway account slack-main --json                  # what a listener starts from, with the digest
fleet gateway receive --account slack-main --beat < b.json   # what a listener runs on each state change
fleet gateway status                                     # each account: accepted, resolved, inbound, last delivery
fleet status listener listener-slack-main --json         # the conditions and the last beat
```
