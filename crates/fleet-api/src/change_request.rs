//! ChangeRequest: a mirror of one change on a forge, numbered so a consumer
//! diffs it with one integer.

use serde::{Deserialize, Serialize};

use crate::condition::Condition;
use crate::forge::{
    ChangeRef, ChangeState, Checks, FailingCheck, Mergeable, Note, NoteKind, Review,
    ReviewDecision, Threads,
};
use crate::meta::Millis;

pub const KIND: &str = "ChangeRequest";

/// Poll interval while Watching, when neither the spec, the host nor the
/// `[forge]` section names one.
pub const DEFAULT_POLL_MS: Millis = 60_000;
/// The window a host's call budget counts over: `maxCallsPerMinute`.
pub const CALL_WINDOW_MS: Millis = 60_000;
/// How long a closed change stays watched before it settles: one week.
pub const DEFAULT_CLOSED_GRACE_MS: Millis = 604_800_000;
/// How long a settled ChangeRequest is retained: thirty days.
pub const DEFAULT_SETTLED_TTL_MS: Millis = 2_592_000_000;
/// A closed change is polled this many times more slowly.
pub const CLOSED_POLL_FACTOR: Millis = 10;
/// The newest notes kept in the snapshot.
pub const NOTE_CAP: usize = 100;

/// What a ChangeRequest asks for. Fixed once created: a different change is
/// a different ChangeRequest.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ChangeRequestSpec {
    /// The change's URL, the only forge-specific input.
    pub url: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub poll_ms: Option<Millis>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub closed_grace_ms: Option<Millis>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub settled_ttl_ms: Option<Millis>,
}

/// The configured values a spec may override, handed to the controller as
/// values: it reads no configuration itself. `poll_ms` is already resolved
/// for the object's host: the host's `pollMs`, else `[forge] pollMs`, else
/// [`DEFAULT_POLL_MS`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ChangeRequestDefaults {
    pub poll_ms: Millis,
    pub closed_grace_ms: Millis,
    pub settled_ttl_ms: Millis,
}

impl Default for ChangeRequestDefaults {
    fn default() -> Self {
        ChangeRequestDefaults {
            poll_ms: DEFAULT_POLL_MS,
            closed_grace_ms: DEFAULT_CLOSED_GRACE_MS,
            settled_ttl_ms: DEFAULT_SETTLED_TTL_MS,
        }
    }
}

/// One `[forge.hosts]` entry's pacing, as values for the manager.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct HostPacing {
    /// The host's poll interval, between a spec's and the `[forge]` default.
    pub poll_ms: Option<Millis>,
    /// The calls every ChangeRequest on the host may make together in any
    /// [`CALL_WINDOW_MS`].
    pub max_calls_per_minute: Option<u64>,
}

impl ChangeRequestSpec {
    pub fn poll_ms(&self, defaults: &ChangeRequestDefaults) -> Millis {
        self.poll_ms.unwrap_or(defaults.poll_ms).max(1)
    }

    pub fn closed_grace_ms(&self, defaults: &ChangeRequestDefaults) -> Millis {
        self.closed_grace_ms.unwrap_or(defaults.closed_grace_ms)
    }

    pub fn settled_ttl_ms(&self, defaults: &ChangeRequestDefaults) -> Millis {
        self.settled_ttl_ms.unwrap_or(defaults.settled_ttl_ms)
    }
}

/// How closely the change is watched.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub enum Phase {
    #[default]
    Watching,
    Closed,
    Settled,
}

/// A value and the revision at which it last changed.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Sequenced<T> {
    pub value: T,
    pub seq: u64,
}

/// The current head, numbered.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Head {
    pub id: String,
    pub checks: Checks,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub failing: Vec<FailingCheck>,
    /// The revision at which `id`, `checks` or `failing` last changed.
    pub seq: u64,
    /// When this `id` was first seen, in Unix milliseconds.
    pub first_seen_at: Millis,
}

/// A note as stored: what the adapter read, plus the sequence number the
/// controller gave it when it first saw it (moved by an edit or an unresolve).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct StoredNote {
    pub id: String,
    pub seq: u64,
    pub kind: NoteKind,
    pub author: String,
    #[serde(default)]
    pub bot: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub thread: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resolved: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub line: Option<u64>,
    pub at: Millis,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub edited_at: Option<Millis>,
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub body: String,
}

impl StoredNote {
    pub fn seen(note: Note, seq: u64) -> StoredNote {
        StoredNote {
            id: note.id,
            seq,
            kind: note.kind,
            author: note.author,
            bot: note.bot,
            thread: note.thread,
            resolved: note.resolved,
            path: note.path,
            line: note.line,
            at: note.at,
            edited_at: note.edited_at,
            url: note.url,
            body: note.body,
        }
    }

    /// The note as the adapter would read it now, without its number.
    pub fn as_seen(&self) -> Note {
        Note {
            id: self.id.clone(),
            kind: self.kind,
            author: self.author.clone(),
            bot: self.bot,
            thread: self.thread.clone(),
            resolved: self.resolved,
            path: self.path.clone(),
            line: self.line,
            at: self.at,
            edited_at: self.edited_at,
            url: self.url.clone(),
            body: self.body.clone(),
        }
    }
}

/// ChangeRequest status, written by ChangeRequestController only. Two kinds
/// of field live here: the bookkeeping, which every poll writes without
/// moving `revision`, and the snapshot, which changes only when the forge
/// said something new and then moves `revision`.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ChangeRequestStatus {
    // Bookkeeping.
    #[serde(default)]
    pub phase: Phase,
    /// What the adapter parsed the URL into.
    #[serde(rename = "ref", default, skip_serializing_if = "Option::is_none")]
    pub reference: Option<ChangeRef>,
    /// When the forge last answered Known.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub observed_at: Option<Millis>,
    /// Earliest next poll; the manager skips the object before then.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub not_before: Option<Millis>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub closed_at: Option<Millis>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub settled_at: Option<Millis>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub conditions: Vec<Condition>,
    /// The forge calls this object's polls made in the last
    /// [`CALL_WINDOW_MS`]: its share of the host's budget, kept with the
    /// object so the next manager run still counts it.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub forge_calls: Vec<ForgeCalls>,

    // The snapshot.
    /// Incremented by every poll that changed anything below.
    #[serde(default)]
    pub revision: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub state: Option<Sequenced<ChangeState>>,
    /// True once the change was seen in any state other than draft; never
    /// returns to false.
    #[serde(default)]
    pub ever_ready: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub head: Option<Head>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub review_decision: Option<Sequenced<ReviewDecision>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mergeable: Option<Mergeable>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub threads: Option<Threads>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub reviews: Vec<Review>,
    /// The newest [`NOTE_CAP`] notes.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub notes: Vec<StoredNote>,
    /// The highest `seq` of a note rotated out of `notes`; 0 until one is.
    #[serde(default)]
    pub dropped_before: u64,
}

/// The calls one poll made, and when.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ForgeCalls {
    pub at: Millis,
    pub count: u64,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_spec_takes_its_defaults_from_the_resource_and_the_config() {
        let spec: ChangeRequestSpec =
            serde_json::from_value(serde_json::json!({"url": "https://forge.example/p/1"}))
                .unwrap();
        let defaults = ChangeRequestDefaults::default();
        assert_eq!(spec.poll_ms(&defaults), DEFAULT_POLL_MS);
        assert_eq!(spec.closed_grace_ms(&defaults), DEFAULT_CLOSED_GRACE_MS);
        assert_eq!(spec.settled_ttl_ms(&defaults), DEFAULT_SETTLED_TTL_MS);
        let overridden = ChangeRequestDefaults {
            poll_ms: 4,
            closed_grace_ms: 5,
            settled_ttl_ms: 6,
        };
        assert_eq!(spec.poll_ms(&overridden), 4);
        assert_eq!(spec.closed_grace_ms(&overridden), 5);
        assert_eq!(spec.settled_ttl_ms(&overridden), 6);
    }

    #[test]
    fn missing_status_is_watching_at_revision_zero() {
        let st = ChangeRequestStatus::default();
        assert_eq!(st.phase, Phase::Watching);
        assert_eq!(st.revision, 0);
        assert!(!st.ever_ready);
        let v = serde_json::to_value(&st).unwrap();
        assert_eq!(
            v,
            serde_json::json!({"phase": "Watching", "revision": 0, "everReady": false, "droppedBefore": 0})
        );
    }

    #[test]
    fn a_spec_refuses_fields_it_does_not_know() {
        let err = serde_json::from_value::<ChangeRequestSpec>(
            serde_json::json!({"url": "https://forge.example/p/1", "relayNotes": true}),
        )
        .unwrap_err();
        assert!(err.to_string().contains("unknown field"), "{err}");
    }
}
