//! Provisioned-workspace port.
//!
//! A session [`Runtime`] receives prepared paths only.
//! This separate port owns explicit worktree creation intent and may compose
//! with any session runtime. Some implementations, including Thurbox, create
//! the worktrees and session atomically; another implementation may create
//! paths first and delegate session creation to `runtime`.

use crate::Observation;
use crate::plan::{EffectOutcome, SpawnSpec};
use crate::runtime::Runtime;
use crate::task::{SessionRef, Workspace};

pub trait WorktreeProvisioner {
    /// The longest complete provisioner operation, including any runtime call
    /// it composes. The manager sizes its lease to the largest wired port.
    fn operation_bound(&self) -> crate::Millis {
        0
    }

    /// Create or recover the worktrees and their one session under the
    /// deterministic key in `spec`.
    fn provision(&self, runtime: &dyn Runtime, spec: &SpawnSpec) -> Observation<EffectOutcome>;

    /// Observe the session only when the provisioned sources, resolved paths,
    /// branch, base, ownership and prepared attachments match exactly.
    fn find(
        &self,
        runtime: &dyn Runtime,
        host: &str,
        key: &str,
        workspace: &Workspace,
    ) -> Observation<Option<SessionRef>>;

    /// Release a provisioned session without assuming its worktrees are safe
    /// to discard.
    fn release(&self, runtime: &dyn Runtime, session: &SessionRef) -> Observation<EffectOutcome>;
}
