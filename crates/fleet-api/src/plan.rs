//! What a controller hands the manager: the status it wants, the effects it
//! needs, and when to look again. A controller never performs any of it.

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::forge::ChangeRef;
use crate::meta::Millis;
use crate::observation::Observation;
use crate::task::SessionRef;

/// One reconcile's answer.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Plan {
    /// The controller-owned status to write, compare-and-swap, before any
    /// effect runs. It carries the intents of `effects` (P5). `None` = unchanged.
    pub status: Option<Value>,
    /// Effects to run, in order, once `status` is written.
    pub effects: Vec<Effect>,
    /// Remove this finalizer once `status` is written and every effect ran.
    pub remove_finalizer: Option<String>,
    /// Delete this object, by its `uid` and `resourceVersion` under the
    /// lease: how a controller retires its own object once its retention
    /// passed. Only ChangeRequestController asks.
    pub delete: bool,
}

/// An effect outside the store, under a deterministic key: a key that has a
/// receipt is never run again.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Effect {
    pub key: String,
    pub action: Action,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", tag = "type")]
pub enum Action {
    Spawn(SpawnSpec),
    Brief {
        session: SessionRef,
        text: String,
    },
    /// Delete the session: its checkout and conversation go with it.
    Release {
        session: SessionRef,
    },
}

impl Action {
    pub fn name(&self) -> &'static str {
        match self {
            Action::Spawn(_) => "spawn",
            Action::Brief { .. } => "brief",
            Action::Release { .. } => "release",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SpawnSpec {
    pub host: String,
    pub key: String,
    pub harness: String,
    pub dir: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "std::collections::BTreeMap::is_empty")]
    pub env: std::collections::BTreeMap<String, String>,
    /// The manager already froze runtime model-variable selection with env.
    #[serde(default)]
    pub environment_frozen: bool,
}

/// Written before the effect runs.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Intent {
    pub action: String,
    pub at: Millis,
    pub generation: u64,
}

/// Written after the effect ran, with what the adapter said.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Receipt {
    pub action: String,
    pub at: Millis,
    pub outcome: Observation<EffectOutcome>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", tag = "type")]
pub enum EffectOutcome {
    /// A session exists for the key; `adopted` when it existed already.
    Spawned {
        session: SessionRef,
        adopted: bool,
    },
    /// The brief is on the session; `already` when the session refused a
    /// second send of the same key.
    Delivered {
        already: bool,
    },
    Released,
    /// The adapter answered, and the answer is no.
    Failed {
        why: String,
    },
}

/// What the controller needs to know before it can decide. The manager asks
/// the adapters, then calls the controller again with the answers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Probe {
    Host {
        host: String,
    },
    Find {
        host: String,
        key: String,
    },
    Ready {
        session: SessionRef,
    },
    BriefDelivered {
        session: SessionRef,
        key: String,
    },
    AtRest {
        session: SessionRef,
    },
    /// One complete observation of a change, through the Forge port.
    Change {
        change: ChangeRef,
    },
}

/// The answers to the probes; `None` means "not asked".
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Observations {
    pub host: Option<Observation<bool>>,
    /// `Known(None)` means a readable list showed no such session.
    pub find: Option<Observation<Option<SessionRef>>>,
    pub ready: Option<Observation<bool>>,
    pub brief_delivered: Option<Observation<bool>>,
    pub at_rest: Option<Observation<bool>>,
}

/// `task/<name>/<uid>/<action>[/<attempt>]`: deterministic, so a repeat is
/// recognised, and scoped by uid, so a Task recreated under the same name
/// starts every key fresh. A Task's spec cannot change once created (only
/// `dispatch` may), so one uid is one brief and needs no generation in the key.
pub fn effect_key(kind: &str, name: &str, uid: &str, action: &str, attempt: Option<u32>) -> String {
    let kind = kind.to_ascii_lowercase();
    match attempt {
        Some(n) => format!("{kind}/{name}/{uid}/{action}/{n}"),
        None => format!("{kind}/{name}/{uid}/{action}"),
    }
}
