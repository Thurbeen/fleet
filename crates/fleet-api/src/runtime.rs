//! The runtime port: the calls a Task's lifecycle makes on an agent host.
//! Every call answers Known or Unknown, and Unknown never concludes.
//!
//! Hosts are the runtime's names (`Task.spec.host`). Keys are fleet's session
//! keys; an adapter may store them under its own prefix, and hands them back
//! unprefixed.

use crate::observation::Observation;
use crate::plan::{EffectOutcome, SpawnSpec};
use crate::task::{SessionRef, Workspace};

pub trait Runtime {
    /// Resolve runtime model-variable selection into the frozen private environment.
    fn prepare_execution(
        &self,
        execution: &crate::config::Execution,
    ) -> Observation<crate::config::Execution> {
        Observation::Known(execution.clone())
    }

    fn env_source(
        &self,
        _source: &crate::config::EnvSource,
    ) -> Observation<std::collections::BTreeMap<String, String>> {
        Observation::unknown("environment sources unsupported by this runtime")
    }

    /// The longest one call of this trait can take, every bound and grace
    /// included: the manager's lease is sized to outlast it.
    fn operation_bound(&self) -> crate::Millis {
        0
    }
    /// Whether the host answers. Unknown when the probe cannot tell.
    fn host(&self, host: &str) -> Observation<bool>;
    /// `Spawned`, or `Spawned { adopted: true }` when a live session already
    /// holds the key. Unknown on timeout: the next pass looks the key up with
    /// `find` and never retries blindly.
    fn spawn(&self, spec: &SpawnSpec) -> Observation<EffectOutcome>;
    /// The session holding `key`, `Known(None)` when a readable list shows
    /// none, Unknown when the list cannot be read or its membership conflicts
    /// with a supplied workspace. `None` skips membership comparison for a
    /// recorded session whose exact id is being checked during release.
    fn find(
        &self,
        host: &str,
        key: &str,
        workspace: Option<&Workspace>,
    ) -> Observation<Option<SessionRef>>;
    /// Ready when the agent is running and waiting for input.
    fn ready(&self, session: &SessionRef) -> Observation<bool>;
    /// Send the brief once under `intent_key`; the session stores the key, so
    /// a second send of the same key is `Delivered { already: true }`.
    fn brief(
        &self,
        session: &SessionRef,
        text: &str,
        intent_key: &str,
    ) -> Observation<EffectOutcome>;
    /// Read the intent key back: did a brief under it arrive?
    fn brief_delivered(&self, session: &SessionRef, intent_key: &str) -> Observation<bool>;
    /// The agent finished its turn and waits. Nothing is deleted unless this
    /// is Known(true).
    fn at_rest(&self, session: &SessionRef) -> Observation<bool>;
    /// Delete the session once; repeating it is a no-op.
    fn release(&self, session: &SessionRef) -> Observation<EffectOutcome>;
}
