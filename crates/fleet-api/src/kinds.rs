//! Which kinds this build serves, and who may write what on each.

use crate::change_request;
use crate::store::FieldManager;
use crate::task;

/// Whether this build stores and reconciles a kind.
pub fn is_served(kind: &str) -> bool {
    matches!(
        kind,
        task::KIND
            | crate::config::TASK_CONFIG
            | crate::config::ENV_PROVIDER
            | change_request::KIND
    )
}

/// The controller that owns a kind's status, if the kind has one.
pub fn controller_of(kind: &str) -> Option<FieldManager> {
    match kind {
        task::KIND => Some(FieldManager::TaskController),
        change_request::KIND => Some(FieldManager::ChangeRequestController),
        _ => None,
    }
}

/// Who owns one top-level status field of a kind. `None`: nobody may write
/// it through `update_status`. `Task.status.inbox` has no owner: it changes
/// only through the typed mutations below.
pub fn status_owner(kind: &str, field: &str) -> Option<FieldManager> {
    match kind {
        task::KIND if task::UNOWNED_FIELDS.contains(&field) => None,
        task::KIND if task::WORKER_FIELDS.contains(&field) => Some(FieldManager::Worker),
        task::KIND => Some(FieldManager::TaskController),
        change_request::KIND => Some(FieldManager::ChangeRequestController),
        _ => None,
    }
}

/// Who may append to `Task.status.inbox` (`Store::append_inbox`), each
/// naming itself: `fleet send` as `cli`.
pub fn may_append_inbox(by: FieldManager) -> bool {
    by == FieldManager::Cli
}

/// Who may record a delivery and remove its entry
/// (`Store::record_delivery`): the Task's controller only.
pub fn may_record_delivery(by: FieldManager) -> bool {
    controller_of(task::KIND) == Some(by)
}

/// Finalizers the store puts on an object when it is created.
pub fn finalizers_on_create(kind: &str) -> Vec<String> {
    match kind {
        task::KIND => vec![task::RELEASE_FINALIZER.to_string()],
        _ => Vec::new(),
    }
}
