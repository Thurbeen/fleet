//! Which kinds this build serves, and who may write what on each.

use crate::store::FieldManager;
use crate::{change_request, conversation, listener, task};

/// Every kind this build serves, in the order `fleet version` lists them.
/// A plugin manifest may claim none of these names.
pub const BUILT_IN: &[&str] = &[
    task::KIND,
    crate::config::TASK_CONFIG,
    crate::config::ENV_PROVIDER,
    change_request::KIND,
    conversation::KIND,
    listener::KIND,
];

/// Whether this build stores and reconciles a kind.
pub fn is_served(kind: &str) -> bool {
    BUILT_IN.contains(&kind)
}

/// The controller that owns a kind's status, if the kind has one.
pub fn controller_of(kind: &str) -> Option<FieldManager> {
    match kind {
        task::KIND => Some(FieldManager::TaskController),
        change_request::KIND => Some(FieldManager::ChangeRequestController),
        conversation::KIND => Some(FieldManager::ConversationController),
        listener::KIND => Some(FieldManager::ListenerController),
        _ => None,
    }
}

/// Who owns one top-level status field of a kind. `None`: nobody may write
/// it through `update_status`. `Task.status.inbox`, a Conversation's
/// `received` and a Listener's `beats`, `refused` and `beatRevision` have
/// no owner: they change only through the typed mutations below.
pub fn status_owner(kind: &str, field: &str) -> Option<FieldManager> {
    match kind {
        task::KIND if task::UNOWNED_FIELDS.contains(&field) => None,
        task::KIND if task::WORKER_FIELDS.contains(&field) => Some(FieldManager::Worker),
        task::KIND if task::FORGE_FIELDS.contains(&field) => Some(FieldManager::ForgeController),
        task::KIND => Some(FieldManager::TaskController),
        change_request::KIND => Some(FieldManager::ChangeRequestController),
        conversation::KIND if conversation::UNOWNED_FIELDS.contains(&field) => None,
        conversation::KIND => Some(FieldManager::ConversationController),
        listener::KIND if listener::UNOWNED_FIELDS.contains(&field) => None,
        listener::KIND => Some(FieldManager::ListenerController),
        _ => None,
    }
}

/// Who may create an object of a kind (`Store::create`): `apply` for any
/// kind, and an account's listener, through the receive verb, for the
/// Conversation of a thread it delivers for and for its own Listener.
pub fn may_create(kind: &str, by: &FieldManager) -> bool {
    match by {
        FieldManager::Apply => true,
        FieldManager::Listener(_) => matches!(kind, conversation::KIND | listener::KIND),
        _ => false,
    }
}

/// Who may spool a delivery on a Conversation (`Store::receive_event`) and
/// record beats and refusals on a Listener (`Store::record_beat`,
/// `Store::record_refusal`): the listener of the account the object's spec
/// names, and nobody else.
pub fn is_listener_of(by: &FieldManager, account: &str) -> bool {
    matches!(by, FieldManager::Listener(a) if a == account)
}

/// Who may write a Conversation's status and drain its spool in one write
/// (`Store::record_received`): its controller only.
pub fn may_record_received(by: &FieldManager) -> bool {
    *by == FieldManager::ConversationController
}

/// Who may append to `Task.status.inbox` (`Store::append_inbox`), each
/// naming itself: `fleet send` as `cli`, and the Forge controller, whose
/// relay lands through `Store::record_relay`.
pub fn may_append_inbox(by: FieldManager) -> bool {
    matches!(by, FieldManager::Cli | FieldManager::ForgeController)
}

/// Who may replace `Task.status.forge` and append the relay's entries in
/// one write (`Store::record_relay`): the Forge controller only.
pub fn may_record_relay(by: FieldManager) -> bool {
    by == FieldManager::ForgeController
}

/// The source prefix an inbox appender must name itself with: `cli/<user>`
/// for `fleet send`, `forge/<change>` for the Forge controller. `None` for
/// a writer that never appends.
pub fn inbox_source_prefix(by: FieldManager) -> Option<&'static str> {
    match by {
        FieldManager::Cli => Some(crate::inbox::CLI_SOURCE_PREFIX),
        FieldManager::ForgeController => Some(crate::inbox::FORGE_SOURCE_PREFIX),
        _ => None,
    }
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
