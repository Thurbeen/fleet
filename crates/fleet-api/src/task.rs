//! Task: one agent session doing one brief.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::condition::Condition;
use crate::follow::{ForgeSpec, ForgeStatus};
use crate::inbox::{Deliveries, DeliveryAttempt, InboxEntry};
use crate::meta::Millis;
use crate::plan::{Intent, Receipt};
use crate::result::{OutputDecl, TaskResult};

pub const KIND: &str = "Task";

/// The finalizer every Task carries from create: TaskController releases the
/// session before the record goes.
pub const RELEASE_FINALIZER: &str = "fleet.dev/release";

/// The runtime host a Task runs on when it names none: this machine.
pub const LOCAL_HOST: &str = "local";

/// What a Task asks for. Fixed once created, like a Pod's spec: only
/// `dispatch` and `forge` may change, and a different brief is a different
/// Task.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TaskSpec {
    /// The agent harness, e.g. `claude-code`. Passed to the runtime as is.
    #[serde(default)]
    pub harness: String,
    /// The runtime host the session runs on.
    #[serde(default = "local_host")]
    pub host: String,
    /// The model the harness runs, when it is not the harness's default.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub config_ref: Option<crate::config::ConfigRef>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub env: BTreeMap<String, String>,
    /// The brief, sent once.
    pub brief: String,
    pub workspace: Workspace,
    /// Set by whoever starts the work; nothing spawns while it is false.
    #[serde(default)]
    pub dispatch: bool,
    /// The structured outputs a worker's result must carry, by name.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub outputs: BTreeMap<String, OutputDecl>,
    /// Overrides of the `[forge]` relay and draft defaults for the changes
    /// this Task's worker declares; mutable like `dispatch`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub forge: Option<ForgeSpec>,
}

fn local_host() -> String {
    LOCAL_HOST.to_string()
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Workspace {
    pub dir: String,
    /// Prepared repository workspaces attached to the same session as-is.
    /// The runtime must not create branches or worktrees for these paths.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub additional_dirs: Vec<String>,
    /// An explicit request for the runtime to provision Git worktrees.
    /// Without this field every path remains caller-prepared and attached.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub worktree: Option<WorktreeRequest>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct WorktreeRequest {
    /// The one branch shared by every repository in the runtime session.
    pub branch: String,
    /// The base used for the primary and every additional repository.
    #[serde(default = "default_base_branch")]
    pub base_branch: String,
    /// Whether the primary path is provisioned or reused as-is.
    #[serde(default)]
    pub primary: PrimaryWorktree,
    /// Repository sources from which the runtime creates additional worktrees.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub additional_repositories: Vec<RepositorySource>,
}

fn default_base_branch() -> String {
    "main".to_string()
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum PrimaryWorktree {
    #[default]
    Provision,
    Existing,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RepositorySource {
    pub path: String,
}

/// What the runtime actually resolved for a provisioned workspace. This is
/// recorded separately from the immutable desired paths in the Task spec.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeWorkspace {
    pub cwd: String,
    pub base_branch: String,
    pub worktrees: Vec<RuntimeWorktree>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub additional_dirs: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeWorktree {
    pub repository: String,
    pub path: String,
    pub branch: String,
    pub created_by_runtime: bool,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub enum Phase {
    #[default]
    Pending,
    Spawning,
    Spawned,
    Briefed,
    Stalled,
    Concluded,
    Releasing,
}

/// Task status. TaskController owns every field but `result` and
/// `declared`, which the worker writes through `fleet result` and `fleet
/// declare`, `forge`, which ForgeController owns, and `inbox`, which nobody
/// owns: it is reached only through the store's typed append and
/// record-and-remove mutations (see [`crate::inbox`]).
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TaskStatus {
    #[serde(default)]
    pub phase: Phase,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub execution: Option<crate::config::ExecutionIdentity>,
    #[serde(default)]
    pub observed_generation: u64,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub conditions: Vec<Condition>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session: Option<SessionRef>,
    /// Effect intents, written before the effect runs.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub intents: BTreeMap<String, Intent>,
    /// Effect receipts, written after; an intent without one is a question.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub receipts: BTreeMap<String, Receipt>,
    #[serde(default)]
    pub spawn_attempts: u32,
    #[serde(default)]
    pub release_attempts: u32,
    /// Sends of the brief, bounded like spawn attempts.
    #[serde(default)]
    pub brief_sends: u32,
    /// Consecutive unreadable delivery probes; three raise NeedsAttention.
    #[serde(default)]
    pub brief_probe_failures: u32,
    /// After a failed or unknown attempt: the manager does not reconcile
    /// this Task again before then.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub not_before: Option<Millis>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub brief: Option<BriefRecord>,
    /// The worker's claim, written by field manager `worker` only.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub result: Option<TaskResult>,
    /// Messages queued for the session, oldest first. No owner: appended by
    /// `fleet send`, removed by TaskController, each through a typed
    /// mutation; never replaced as a list.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub inbox: Vec<InboxEntry>,
    /// The receipts of delivered inbox entries, TaskController's.
    #[serde(default, skip_serializing_if = "Deliveries::is_empty")]
    pub deliveries: Deliveries,
    /// TaskController's sends and probes per inbox entry being delivered.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub delivery_attempts: BTreeMap<String, DeliveryAttempt>,
    /// Facts the worker declared through `fleet declare`, each a JSON
    /// object with a string `kind`; field manager `worker`'s.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub declared: Vec<serde_json::Value>,
    /// The Forge controller's: one entry per followed change and its
    /// conditions. Absent for a Task that declared nothing.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub forge: Option<ForgeStatus>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct BriefRecord {
    /// The effect key the brief was sent under; the session stores it too.
    pub key: String,
    /// The spec generation the brief was sent for; `fleet result` must name it.
    pub generation: u64,
    /// True when the receipt was recovered by asking the session after a crash.
    #[serde(default)]
    pub recovered: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SessionRef {
    pub id: String,
    pub host: String,
    pub key: String,
    /// Present for runtime-provisioned workspaces after exact verification.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub workspace: Option<RuntimeWorkspace>,
}

/// The status fields the worker owns.
pub const WORKER_FIELDS: &[&str] = &["result", "declared"];

/// The status fields ForgeController owns.
pub const FORGE_FIELDS: &[&str] = &["forge"];

/// The status fields nobody owns: reached only through typed mutations.
/// Every field in none of these lists is TaskController's.
pub const UNOWNED_FIELDS: &[&str] = &["inbox"];

/// The session key for a Task: deterministic, so a second spawn finds the
/// first session instead of twinning it. The uid keeps a Task recreated under
/// the same name from adopting a session it never owned.
pub fn session_key(name: &str, uid: &str) -> String {
    format!("{name}.{uid}")
}

/// The uid part of a session key: `<name>.<uid>`, or a bare uid, which is
/// what an adapter whose keys must be short hands back from `list`.
pub fn uid_of_session_key(key: &str) -> &str {
    key.rsplit('.').next().unwrap_or(key)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_task_runs_on_this_machine_unless_it_names_a_host() {
        let spec: TaskSpec = serde_json::from_value(serde_json::json!({
            "harness": "claude-code", "brief": "b", "workspace": {"dir": "w"}
        }))
        .unwrap();
        assert_eq!(spec.host, LOCAL_HOST);
        assert!(spec.workspace.additional_dirs.is_empty());
        assert!(spec.workspace.worktree.is_none());
        assert!(!spec.dispatch);
    }

    #[test]
    fn a_session_key_carries_the_uid() {
        let key = session_key("t", "u1");
        assert_eq!(uid_of_session_key(&key), "u1");
        assert_eq!(uid_of_session_key("u1"), "u1");
    }

    #[test]
    fn worktree_requests_have_explicit_stable_runtime_defaults() {
        let spec: TaskSpec = serde_json::from_value(serde_json::json!({
            "harness": "codex", "brief": "b",
            "workspace": {
                "dir": "/source/primary",
                "worktree": {
                    "branch": "feature/task",
                    "additionalRepositories": [{"path": "/source/other"}]
                }
            }
        }))
        .unwrap();
        let worktree = spec.workspace.worktree.unwrap();
        assert_eq!(worktree.base_branch, "main");
        assert_eq!(worktree.primary, PrimaryWorktree::Provision);
        assert_eq!(worktree.additional_repositories[0].path, "/source/other");
    }
}
