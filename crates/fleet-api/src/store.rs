//! The store port (P2, P3, Q15): get, exclusive create, compare-and-swap
//! update and list, plus the one manager lease.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

use crate::meta::{Millis, Object};
use crate::observation::Observation;

/// Who is writing. The store checks it against the kind's ownership table
/// (`kinds::status_owner`) on every status write and finalizer change.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum FieldManager {
    /// People and scripts writing spec through `fleet apply`.
    Apply,
    /// The worker answering through `fleet result`; owns `Task.status.result`.
    Worker,
    /// The one controller for Task; owns the rest of Task status.
    TaskController,
    /// The one controller for ChangeRequest; owns all of its status.
    ChangeRequestController,
    /// A person at the command line, through `fleet send`: appends to
    /// `Task.status.inbox` and owns nothing.
    Cli,
}

impl FieldManager {
    pub fn name(self) -> &'static str {
        match self {
            FieldManager::Apply => "apply",
            FieldManager::Worker => "worker",
            FieldManager::TaskController => "task-controller",
            FieldManager::ChangeRequestController => "change-request-controller",
            FieldManager::Cli => "cli",
        }
    }

    /// Controllers write only while they hold the manager lease (fencing).
    pub fn is_controller(self) -> bool {
        matches!(
            self,
            FieldManager::TaskController | FieldManager::ChangeRequestController
        )
    }
}

/// Proof of the manager lease. `token` increases every time the lease changes
/// hands, so a writer that lost it is refused even if it does not know yet.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct LeaseToken {
    pub holder: String,
    pub token: u64,
    pub expires_at: Millis,
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum StoreError {
    #[error("{0} not found")]
    NotFound(String),
    #[error("{0} already exists")]
    AlreadyExists(String),
    #[error("conflict on {key}: expected resourceVersion {expected}, found {actual}")]
    Conflict {
        key: String,
        expected: u64,
        actual: u64,
    },
    #[error("forbidden: {0}")]
    Forbidden(String),
    #[error("invalid: {0}")]
    Invalid(String),
    #[error("unsupported: {0}")]
    Unsupported(String),
    #[error("lease lost: {0}")]
    LeaseLost(String),
    #[error("lease held by {holder} until {expires_at}")]
    LeaseHeld { holder: String, expires_at: Millis },
    #[error("store busy: {0}")]
    Busy(String),
    /// The store could not tell: an unreadable or corrupt record. Never read
    /// as absence.
    #[error("could not tell: {0}")]
    Unknown(String),
}

/// What the store does to a delete request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum DeleteOutcome {
    /// Finalizers remain: the object is marked and its controller releases it.
    Marked,
    Removed,
}

pub trait Store {
    /// Private immutable launch data, keyed by Task UID, never a resource/status.
    fn launch(&self, _task: &Object) -> Result<Option<crate::config::Execution>, StoreError> {
        Err(StoreError::Unsupported(
            "private launches unsupported".into(),
        ))
    }
    fn freeze_launch(
        &self,
        _task: &Object,
        _execution: &crate::config::Execution,
        _lease: &LeaseToken,
    ) -> Result<crate::config::Execution, StoreError> {
        Err(StoreError::Unsupported(
            "private launches unsupported".into(),
        ))
    }

    /// `Known(None)` only when the store can prove absence.
    fn get(&self, kind: &str, name: &str) -> Observation<Option<Object>>;
    /// Every object of a kind, or Unknown as a whole: never a partial list.
    fn list(&self, kind: &str) -> Observation<Vec<Object>>;
    /// Exclusive: a second create of the same name is `AlreadyExists`.
    fn create(&self, obj: Object, by: FieldManager) -> Result<Object, StoreError>;
    /// Replace spec (and labels); bumps generation when spec changed.
    fn update_spec(
        &self,
        kind: &str,
        name: &str,
        spec: Value,
        labels: Option<std::collections::BTreeMap<String, String>>,
        expected: u64,
        by: FieldManager,
    ) -> Result<Object, StoreError>;
    /// Compare-and-swap on resourceVersion; `by` may change only the status
    /// fields it owns, and a controller must present the current lease.
    fn update_status(
        &self,
        kind: &str,
        name: &str,
        status: Value,
        expected: u64,
        by: FieldManager,
        lease: Option<&LeaseToken>,
    ) -> Result<Object, StoreError>;
    fn delete(&self, kind: &str, name: &str, by: FieldManager)
    -> Result<DeleteOutcome, StoreError>;
    /// Delete an object its own controller decided to remove: fenced by the
    /// `uid` and `resourceVersion` the decision was made from and by the
    /// lease, so a stale plan cannot remove a recreated object.
    fn delete_fenced(
        &self,
        kind: &str,
        name: &str,
        uid: &str,
        expected: u64,
        by: FieldManager,
        lease: &LeaseToken,
    ) -> Result<DeleteOutcome, StoreError>;
    /// Append one entry to a Task's `status.inbox`, compare-and-swap on
    /// resourceVersion, as a caller `kinds::may_append_inbox` admits. Refused
    /// when the key is already in `inbox` or `deliveries`, when more than
    /// `inbox::MAX_INBOX` entries are undelivered, or on a deleting Task.
    /// No other status field changes.
    fn append_inbox(
        &self,
        name: &str,
        entry: crate::inbox::InboxEntry,
        expected: u64,
        by: FieldManager,
        lease: Option<&LeaseToken>,
    ) -> Result<Object, StoreError>;
    /// TaskController's status write that also records one delivery: the
    /// receipt goes into `status.deliveries` and its entry leaves
    /// `status.inbox`, in the same compare-and-swap write as `status`, which
    /// is checked for ownership exactly as in `update_status` (so it cannot
    /// change `inbox` itself). Refused when no entry holds the receipt's key.
    fn record_delivery(
        &self,
        name: &str,
        status: Value,
        receipt: crate::inbox::DeliveryReceipt,
        expected: u64,
        by: FieldManager,
        lease: &LeaseToken,
    ) -> Result<Object, StoreError>;
    /// Only the kind's controller, holding the lease, removes a finalizer.
    fn remove_finalizer(
        &self,
        kind: &str,
        name: &str,
        finalizer: &str,
        expected: u64,
        by: FieldManager,
        lease: &LeaseToken,
    ) -> Result<DeleteOutcome, StoreError>;
    /// Validate an object as the store would before writing it.
    fn validate(&self, obj: &Object) -> Result<(), StoreError> {
        crate::validate::validate_object(obj)
    }
    fn acquire_lease(&self, holder: &str, ttl: Millis) -> Result<LeaseToken, StoreError>;
    fn renew_lease(&self, lease: &LeaseToken, ttl: Millis) -> Result<LeaseToken, StoreError>;
    fn release_lease(&self, lease: &LeaseToken) -> Result<(), StoreError>;
}

/// The clock port: time is an input to pure code, read here by an adapter.
pub trait Clock {
    fn now(&self) -> Millis;
}
