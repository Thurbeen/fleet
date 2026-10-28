//! What a controller hands the manager: the status it wants, the effects it
//! needs, and when to look again. A controller never performs any of it.

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::forge::{ChangeRef, ChangeState, Expected};
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
    /// passed. ChangeRequestController, ConversationController and
    /// ListenerController ask.
    pub delete: bool,
    /// An inbox entry proven delivered: its receipt goes into
    /// `status.deliveries` and the entry leaves `status.inbox` in the same
    /// write as `status`, through the store's record-and-remove mutation.
    pub delivery: Option<crate::inbox::DeliveryReceipt>,
    /// Inbox entries to append in the same write as `status`, through the
    /// store's relay mutation. Only ForgeController asks, and its `status`
    /// is then `status.forge` alone.
    pub appends: Vec<crate::inbox::InboxEntry>,
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
    /// Take a draft change out of draft, through the Forge port: the only
    /// forge write Fleet performs. `expect` is the head and state the
    /// decision was made against; the adapter re-reads both immediately
    /// before the mutation and answers `Moved` instead when either differs.
    MarkReady {
        change: ChangeRef,
        expect: Expected,
    },
}

impl Action {
    pub fn name(&self) -> &'static str {
        match self {
            Action::Spawn(_) => "spawn",
            Action::Brief { .. } => "brief",
            Action::Release { .. } => "release",
            Action::MarkReady { .. } => "markReady",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SpawnSpec {
    pub host: String,
    pub key: String,
    pub harness: String,
    /// What the Task named; the runtime decides how to make it.
    pub workspace: crate::task::Workspace,
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
    /// The change is out of draft; `already` when the forge said it was no
    /// longer a draft by the time the mutation ran, inside the window
    /// between the adapter's fresh read and the mutation.
    MarkedReady {
        already: bool,
    },
    /// The change is no longer what the mark-ready effect expected: its head
    /// or state moved between the decision and the adapter's fresh read.
    /// Nothing was mutated, and this is not an attempt against the change.
    Moved {
        head: String,
        state: ChangeState,
    },
    /// A thread is open on the platform, through the Gateway port: its
    /// address, the id of the opening post and the platform's own time for
    /// it.
    Opened {
        conversation: crate::gateway::ConversationRef,
        message: String,
        at: Millis,
    },
    /// A post landed in a thread, through the Gateway port: its id and the
    /// platform's own time for it.
    Posted {
        message: String,
        at: Millis,
    },
    /// The adapter answered, and the answer is no.
    Failed {
        why: String,
    },
    /// The runtime maps no source for a repository the workspace names.
    /// Nothing was started: the runtime's configuration lacks the mapping,
    /// and adding it, not a retry, is the fix.
    Unmapped {
        url: String,
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
    /// A stored object, answered by the manager from the store: `Known(Some)`,
    /// `Known(None)` for proven absence, Unknown when the record is
    /// unreadable. Needs no lease renewal and runs before any runtime or
    /// forge probe.
    Object {
        kind: String,
        name: String,
    },
    /// Fleet's own login on a forge host, through the Forge port; cached by
    /// the manager for the sweep.
    Whoami {
        host: String,
    },
    /// One read of a thread's history through the Gateway port.
    History {
        account: String,
        conversation: crate::gateway::ConversationRef,
        query: crate::gateway::HistoryQuery,
    },
    /// What the platform offers on an account, through the Gateway port;
    /// cached by the manager for the sweep.
    Capabilities {
        account: String,
    },
    /// Fleet's bot identity on an account, through the Gateway port; cached
    /// by the manager for the sweep.
    GatewayWhoami {
        account: String,
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
/// An inbox entry's key has the same shape (`send/<n>`) and is the intent key
/// its delivery runs under.
pub fn effect_key(kind: &str, name: &str, uid: &str, action: &str, attempt: Option<u32>) -> String {
    let kind = kind.to_ascii_lowercase();
    match attempt {
        Some(n) => format!("{kind}/{name}/{uid}/{action}/{n}"),
        None => format!("{kind}/{name}/{uid}/{action}"),
    }
}
