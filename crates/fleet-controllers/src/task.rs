//! TaskController: spawns one agent session for one brief, briefs it once,
//! records the worker's answer, and releases the session when the Task is
//! deleted.
//!
//! Pure: it reads the Task and what the probes saw, and returns a Plan. The
//! manager performs the plan.

use fleet_api::condition::{ConditionStatus, condition, set_condition, settle_transitions};
use fleet_api::meta::{Millis, Object};
use fleet_api::plan::{
    Action, Effect, EffectOutcome, Intent, Observations, Plan, Probe, Receipt, SpawnSpec,
    effect_key,
};
use fleet_api::store::FieldManager;
use fleet_api::task::{self, BriefRecord, Phase, SessionRef, TaskSpec, TaskStatus};
use fleet_api::validate::typed;
use fleet_api::{Observation, StoreError};

/// What one reconcile of one Task reads. Every outside fact arrives here as
/// a value; nothing is read from the world inside the controller.
#[derive(Debug, Clone)]
pub struct TaskInput<'a> {
    pub task: &'a Object,
    pub observations: Observations,
    pub now: Millis,
}

/// How many times a spawn or a brief send is tried before the Task stalls,
/// and how many unreadable delivery checks or failed releases raise
/// NeedsAttention (those two keep being retried: neither can do harm).
pub const MAX_ATTEMPTS: u32 = 3;

/// The wait after a failed or unknown attempt before the next one, persisted
/// as `status.notBefore` so a new manager run honours it too.
pub const RETRY_DELAY_MS: Millis = 30_000;

#[derive(Debug, Clone, Copy, Default)]
pub struct TaskController;

/// Condition types TaskController writes.
pub mod conditions {
    pub const HOST_REACHABLE: &str = "HostReachable";
    pub const SPAWNED: &str = "Spawned";
    pub const BRIEFED: &str = "Briefed";
    pub const STALLED: &str = "Stalled";
    pub const WORKER_GONE: &str = "WorkerGone";
    pub const CONCLUDED: &str = "Concluded";
    pub const RELEASED: &str = "Released";
    /// Something only a person can decide: a stall, a vanished worker, a
    /// release that kept failing.
    pub const NEEDS_ATTENTION: &str = "NeedsAttention";
}
use conditions::*;

const SPAWN: &str = "spawn";
const BRIEF: &str = "brief";
const RELEASE: &str = "release";

impl TaskController {
    /// The kind this controller reconciles and the field manager it writes as.
    pub const KIND: &'static str = task::KIND;
    pub const MANAGER: FieldManager = FieldManager::TaskController;

    /// What the next `reconcile` needs observed. Only these are asked.
    pub fn probes(&self, input: &TaskInput<'_>) -> Result<Vec<Probe>, StoreError> {
        let (spec, st) = decode(input.task)?;
        let host = st
            .session
            .as_ref()
            .map_or_else(|| spec.host.clone(), |s| s.host.clone());
        let find = Probe::Find {
            host: host.clone(),
            key: session_key(input.task),
        };
        if input.task.is_deleting() {
            let mut probes = vec![find];
            if let Some(s) = &st.session {
                probes.push(Probe::AtRest { session: s.clone() });
            }
            return Ok(probes);
        }
        Ok(match st.phase {
            Phase::Pending if spec.dispatch => vec![Probe::Host { host }],
            Phase::Spawning => vec![find],
            Phase::Spawned => {
                let mut probes = vec![find];
                if let Some(s) = &st.session {
                    probes.push(Probe::Ready { session: s.clone() });
                    let bk = brief_key(input.task);
                    if st.intents.contains_key(&bk) && !known_receipt(&st, &bk) {
                        probes.push(Probe::BriefDelivered {
                            session: s.clone(),
                            key: bk,
                        });
                    }
                }
                probes
            }
            Phase::Briefed if st.result.is_none() => vec![find],
            _ => Vec::new(),
        })
    }

    pub fn reconcile(&self, input: &TaskInput<'_>) -> Result<Plan, StoreError> {
        let (spec, before) = decode(input.task)?;
        let mut r = Pass {
            input,
            spec: &spec,
            st: before.clone(),
            generation: input.task.metadata.generation,
            effects: Vec::new(),
            remove_finalizer: None,
        };
        r.st.observed_generation = r.generation;
        if input.task.is_deleting() {
            r.release();
        } else {
            r.advance();
        }
        settle_transitions(&mut r.st.conditions, &before.conditions);
        let status = if r.st == before {
            None
        } else {
            Some(
                serde_json::to_value(&r.st)
                    .map_err(|e| StoreError::Invalid(format!("Task status: {e}")))?,
            )
        };
        Ok(Plan {
            status,
            effects: r.effects,
            remove_finalizer: r.remove_finalizer,
            delete: false,
        })
    }
}

/// One reconcile in progress.
struct Pass<'a, 'b> {
    input: &'a TaskInput<'b>,
    spec: &'a TaskSpec,
    st: TaskStatus,
    generation: u64,
    effects: Vec<Effect>,
    remove_finalizer: Option<String>,
}

impl Pass<'_, '_> {
    fn now(&self) -> Millis {
        self.input.now
    }

    fn cond(&mut self, t: &str, s: ConditionStatus, reason: &str, message: impl Into<String>) {
        let (generation, now) = (self.generation, self.now());
        set_condition(
            &mut self.st.conditions,
            t,
            s,
            reason,
            message,
            generation,
            now,
        );
    }

    /// Wait [`RETRY_DELAY_MS`] from `from` before the next attempt.
    fn wait_from(&mut self, from: Millis) -> bool {
        let until = from.saturating_add(RETRY_DELAY_MS);
        if until > self.now() {
            self.st.not_before = Some(until);
            true
        } else {
            self.st.not_before = None;
            false
        }
    }

    fn intend(&mut self, key: String, action: Action) {
        let intent = Intent {
            action: action.name().to_string(),
            at: self.now(),
            generation: self.generation,
        };
        self.st.intents.insert(key.clone(), intent);
        self.effects.push(Effect { key, action });
    }

    fn needs_attention(&mut self, reason: &str, message: impl Into<String>) {
        self.cond(NEEDS_ATTENTION, ConditionStatus::True, reason, message);
    }

    /// Clear NeedsAttention when the cause `reason` named has gone.
    fn settle_attention(&mut self, reason: &str) {
        if condition(&self.st.conditions, NEEDS_ATTENTION)
            .is_some_and(|c| c.status == ConditionStatus::True && c.reason == reason)
        {
            self.cond(NEEDS_ATTENTION, ConditionStatus::False, "Resolved", "");
        }
    }

    fn advance(&mut self) {
        if matches!(self.st.phase, Phase::Concluded | Phase::Stalled) {
            return;
        }
        // A recorded result ends the run: a result is only accepted once the
        // brief has a receipt, so this is Briefed in practice.
        if let Some(result) = &self.st.result {
            let outcome = format!("{:?}", result.outcome);
            self.st.phase = Phase::Concluded;
            self.cond(
                CONCLUDED,
                ConditionStatus::True,
                "ResultRecorded",
                format!("outcome {outcome}"),
            );
            return;
        }
        match self.st.phase {
            Phase::Pending => self.pending(),
            Phase::Spawning => self.spawning(),
            Phase::Spawned => self.spawned(),
            Phase::Briefed => self.briefed(),
            Phase::Stalled | Phase::Concluded | Phase::Releasing => {}
        }
    }

    fn pending(&mut self) {
        if self.st.not_before.is_some_and(|t| t > self.now()) {
            return;
        }
        if !self.spec.dispatch {
            self.cond(
                SPAWNED,
                ConditionStatus::False,
                "AwaitingDispatch",
                "spec.dispatch is false",
            );
            return;
        }
        match self.input.observations.host.clone() {
            Some(Observation::Known(true)) => {
                self.cond(HOST_REACHABLE, ConditionStatus::True, "Reachable", "");
            }
            Some(Observation::Known(false)) => {
                self.cond(HOST_REACHABLE, ConditionStatus::False, "Unreachable", "");
                return;
            }
            Some(Observation::Unknown { why }) => {
                self.cond(HOST_REACHABLE, ConditionStatus::Unknown, "ProbeFailed", why);
                return;
            }
            None => {
                self.cond(HOST_REACHABLE, ConditionStatus::Unknown, "NotProbed", "");
                return;
            }
        }
        let attempt = self.st.spawn_attempts + 1;
        let key = self.key(SPAWN, Some(attempt));
        self.st.spawn_attempts = attempt;
        self.st.not_before = None;
        self.st.phase = Phase::Spawning;
        self.cond(
            SPAWNED,
            ConditionStatus::Unknown,
            "Spawning",
            format!("attempt {attempt}"),
        );
        let spec = SpawnSpec {
            host: self.spec.host.clone(),
            key: session_key(self.input.task),
            harness: self.spec.harness.clone(),
            dir: self.spec.workspace.dir.clone(),
            model: self.spec.model.clone(),
            env: self.spec.env.clone(),
            environment_frozen: false,
        };
        self.intend(key, Action::Spawn(spec));
    }

    fn spawning(&mut self) {
        let key = self.key(SPAWN, Some(self.st.spawn_attempts));
        match self.st.receipts.get(&key).map(|r| r.outcome.clone()) {
            Some(Observation::Known(EffectOutcome::Spawned { session, .. })) => {
                self.adopt(session, "Spawned")
            }
            Some(Observation::Known(EffectOutcome::Failed { why })) => self.spawn_retry(&why),
            Some(Observation::Known(other)) => {
                self.spawn_retry(&format!("unexpected outcome {other:?}"))
            }
            // Unknown outcome, or an intent with no receipt (a crash between the
            // effect and its receipt): the key decides, never a blind respawn.
            Some(Observation::Unknown { .. }) | None => {
                match self.input.observations.find.clone() {
                    Some(Observation::Known(Some(session))) => {
                        let receipt = Receipt {
                            action: SPAWN.into(),
                            at: self.now(),
                            outcome: Observation::Known(EffectOutcome::Spawned {
                                session: session.clone(),
                                adopted: true,
                            }),
                        };
                        self.st.receipts.insert(key, receipt);
                        self.adopt(session, "Adopted");
                    }
                    Some(Observation::Known(None)) => self.spawn_retry("no session holds the key"),
                    Some(Observation::Unknown { why }) => {
                        self.cond(
                            SPAWNED,
                            ConditionStatus::Unknown,
                            "SessionListUnreadable",
                            why,
                        );
                    }
                    None => {}
                }
            }
        }
    }

    fn adopt(&mut self, session: SessionRef, reason: &str) {
        self.st.session = Some(session);
        self.st.phase = Phase::Spawned;
        self.cond(SPAWNED, ConditionStatus::True, reason, "");
    }

    /// A spawn proven not to have happened is tried again after
    /// [`RETRY_DELAY_MS`], at most [`MAX_ATTEMPTS`] times in all.
    fn spawn_retry(&mut self, why: &str) {
        let attempts = self.st.spawn_attempts;
        if attempts >= MAX_ATTEMPTS {
            self.st.phase = Phase::Stalled;
            self.cond(
                SPAWNED,
                ConditionStatus::False,
                "SpawnFailed",
                why.to_string(),
            );
            self.cond(
                STALLED,
                ConditionStatus::True,
                "SpawnAttemptsExhausted",
                format!("{attempts} attempts: {why}"),
            );
            self.needs_attention("Stalled", "spawn attempts exhausted");
            return;
        }
        self.st.phase = Phase::Pending;
        self.wait_from(self.now());
        self.cond(
            SPAWNED,
            ConditionStatus::False,
            "SpawnRetry",
            why.to_string(),
        );
    }

    fn spawned(&mut self) {
        let Some(session) = self.st.session.clone() else {
            // A Spawned phase without a session is not a state this controller
            // writes; start over rather than brief nobody.
            self.st.phase = Phase::Pending;
            return;
        };
        if self.worker_gone() {
            return;
        }
        if self.st.not_before.is_some_and(|t| t > self.now()) {
            return;
        }
        let key = brief_key(self.input.task);
        match self.st.receipts.get(&key).map(|r| r.outcome.clone()) {
            Some(Observation::Known(EffectOutcome::Delivered { .. })) => {
                return self.briefed_now(key, false);
            }
            Some(Observation::Known(EffectOutcome::Failed { why })) => {
                self.cond(BRIEFED, ConditionStatus::False, "BriefRefused", why);
                if self.st.brief_sends < MAX_ATTEMPTS && self.st.not_before.is_none() {
                    let at = self.st.receipts[&key].at;
                    if self.wait_from(at) {
                        return;
                    }
                }
                return self.send_brief(session, key);
            }
            _ => {}
        }
        if self.st.intents.contains_key(&key) {
            // Sent, but no Known receipt: ask the session. Known yes is a
            // recovered receipt, Known no a send, Unknown a read tried again
            // after a wait: a read can do no harm, so it never stalls the Task.
            match self.input.observations.brief_delivered.clone() {
                Some(Observation::Known(true)) => {
                    let receipt = Receipt {
                        action: BRIEF.into(),
                        at: self.now(),
                        outcome: Observation::Known(EffectOutcome::Delivered { already: true }),
                    };
                    self.st.receipts.insert(key.clone(), receipt);
                    self.briefed_now(key, true);
                }
                Some(Observation::Known(false)) => self.send_brief(session, key),
                Some(Observation::Unknown { why }) => {
                    self.st.brief_probe_failures += 1;
                    self.cond(
                        BRIEFED,
                        ConditionStatus::Unknown,
                        "BriefUnconfirmed",
                        why.clone(),
                    );
                    self.wait_from(self.now());
                    if self.st.brief_probe_failures >= MAX_ATTEMPTS {
                        self.needs_attention(
                            "BriefUnconfirmed",
                            format!("the brief's delivery could not be read: {why}"),
                        );
                    }
                }
                None => {}
            }
            return;
        }
        self.send_brief(session, key);
    }

    /// Send under the one brief key. Each send is counted and bounded; the
    /// session refuses a second delivery of the same key, so a send after a
    /// Known "not delivered" cannot brief twice.
    fn send_brief(&mut self, session: SessionRef, key: String) {
        if self.st.brief_sends >= MAX_ATTEMPTS {
            self.st.phase = Phase::Stalled;
            let msg = format!("{} sends", self.st.brief_sends);
            self.cond(
                STALLED,
                ConditionStatus::True,
                "BriefAttemptsExhausted",
                msg,
            );
            self.needs_attention("Stalled", "brief attempts exhausted");
            return;
        }
        // Every send, including retries proven not delivered, needs fresh
        // readiness. Receipt recovery above does not send anything.
        match self.input.observations.ready.clone() {
            Some(Observation::Known(true)) => {}
            Some(Observation::Known(false)) => {
                self.cond(BRIEFED, ConditionStatus::False, "SessionNotReady", "");
                return;
            }
            Some(Observation::Unknown { why }) => {
                self.cond(BRIEFED, ConditionStatus::Unknown, "ReadyUnknown", why);
                return;
            }
            None => return,
        }
        self.st.brief_sends += 1;
        self.st.brief_probe_failures = 0;
        self.st.not_before = None;
        self.st.receipts.remove(&key);
        let msg = format!("send {}", self.st.brief_sends);
        self.cond(BRIEFED, ConditionStatus::Unknown, "Sending", msg);
        let text = self.spec.brief.clone();
        self.intend(key, Action::Brief { session, text });
    }

    fn briefed_now(&mut self, key: String, recovered: bool) {
        self.st.brief_probe_failures = 0;
        self.st.not_before = None;
        self.settle_attention("BriefUnconfirmed");
        self.st.brief = Some(BriefRecord {
            key,
            generation: self.generation,
            recovered,
        });
        self.st.phase = Phase::Briefed;
        let reason = if recovered {
            "ReceiptRecovered"
        } else {
            "ReceiptWritten"
        };
        self.cond(BRIEFED, ConditionStatus::True, reason, "");
        self.cond(CONCLUDED, ConditionStatus::False, "NoResultYet", "");
    }

    fn briefed(&mut self) {
        self.worker_gone();
    }

    /// A session a readable list no longer shows raises WorkerGone and
    /// NeedsAttention; nothing abandons the Task. An unreadable list is never
    /// read as gone. Returns true once WorkerGone is set.
    fn worker_gone(&mut self) -> bool {
        let same_session = |s: &SessionRef| self.st.session.as_ref() == Some(s);
        let present = match &self.input.observations.find {
            Some(Observation::Known(found)) => found.as_ref().is_some_and(same_session),
            _ => return false,
        };
        if present {
            if condition(&self.st.conditions, WORKER_GONE)
                .is_some_and(|c| c.status == ConditionStatus::True)
            {
                self.cond(
                    WORKER_GONE,
                    ConditionStatus::False,
                    "SessionPresent",
                    "the session list shows the session again",
                );
            }
            self.settle_attention("WorkerGone");
            return false;
        }
        self.cond(
            WORKER_GONE,
            ConditionStatus::True,
            "SessionGone",
            "the session list no longer shows the session holding its key",
        );
        self.needs_attention("WorkerGone", "the session vanished before a result");
        true
    }

    /// The finalizer: the session is deleted only once it is proven at rest
    /// or gone. A failed release is tried again after a wait; past
    /// [`MAX_ATTEMPTS`] it also raises NeedsAttention, but the finalizer is
    /// never given up on, so the record cannot be stranded.
    fn release(&mut self) {
        self.st.phase = Phase::Releasing;
        // Nothing was ever spawned: there is no session to release.
        if self.st.session.is_none() && self.st.spawn_attempts == 0 {
            self.cond(RELEASED, ConditionStatus::True, "NothingToRelease", "");
            self.remove_finalizer = Some(task::RELEASE_FINALIZER.to_string());
            return;
        }
        let pending_spawn = self.st.session.is_none() && self.st.spawn_attempts > 0;
        if pending_spawn {
            match self.input.observations.find.clone() {
                Some(Observation::Known(Some(s))) => self.st.session = Some(s),
                Some(Observation::Known(None)) => {}
                Some(Observation::Unknown { why }) => {
                    self.cond(
                        RELEASED,
                        ConditionStatus::Unknown,
                        "SessionListUnreadable",
                        why,
                    );
                    return;
                }
                None => return,
            }
        }
        let Some(session) = self.st.session.clone() else {
            self.cond(RELEASED, ConditionStatus::True, "NothingToRelease", "");
            self.remove_finalizer = Some(task::RELEASE_FINALIZER.to_string());
            return;
        };
        if self.st.release_attempts > 0 {
            let last = self.key(RELEASE, Some(self.st.release_attempts));
            match self.st.receipts.get(&last).cloned() {
                Some(Receipt {
                    outcome: Observation::Known(EffectOutcome::Released),
                    ..
                }) => {
                    self.cond(RELEASED, ConditionStatus::True, "Released", "");
                    self.remove_finalizer = Some(task::RELEASE_FINALIZER.to_string());
                    return;
                }
                receipt => {
                    let why = match &receipt {
                        Some(Receipt {
                            outcome: Observation::Known(EffectOutcome::Failed { why }),
                            ..
                        }) => why.clone(),
                        Some(Receipt {
                            outcome: Observation::Unknown { why },
                            ..
                        }) => format!("outcome unknown: {why}"),
                        Some(_) => "unexpected outcome".to_string(),
                        None => "no receipt (interrupted)".to_string(),
                    };
                    if self.st.release_attempts >= MAX_ATTEMPTS {
                        self.cond(RELEASED, ConditionStatus::False, "ReleaseFailed", why);
                        self.needs_attention(
                            "ReleaseFailed",
                            format!("{} release attempts failed", self.st.release_attempts),
                        );
                    } else {
                        self.cond(RELEASED, ConditionStatus::False, "ReleaseRetry", why);
                    }
                    // Wait once per failed attempt, from that attempt: its
                    // receipt, or its intent when it was interrupted before
                    // the receipt. A wait that has run out lets it retry.
                    match self.st.not_before {
                        Some(t) if t > self.now() => return,
                        Some(_) => self.st.not_before = None,
                        None => {
                            let at = receipt
                                .map(|r| r.at)
                                .or_else(|| self.st.intents.get(&last).map(|i| i.at))
                                .unwrap_or(0);
                            if self.wait_from(at) {
                                return;
                            }
                        }
                    }
                }
            }
        }
        let gone = matches!(self.input.observations.find, Some(Observation::Known(None)));
        if !gone {
            match self.input.observations.at_rest.clone() {
                Some(Observation::Known(true)) => {}
                Some(Observation::Known(false)) => {
                    self.cond(
                        RELEASED,
                        ConditionStatus::False,
                        "SessionBusy",
                        "waiting for the session to be at rest",
                    );
                    return;
                }
                Some(Observation::Unknown { why }) => {
                    self.cond(RELEASED, ConditionStatus::Unknown, "AtRestUnknown", why);
                    return;
                }
                None => return,
            }
        }
        let attempt = self.st.release_attempts + 1;
        self.st.release_attempts = attempt;
        let key = self.key(RELEASE, Some(attempt));
        self.cond(
            RELEASED,
            ConditionStatus::Unknown,
            "Releasing",
            format!("attempt {attempt}"),
        );
        self.intend(key, Action::Release { session });
    }

    fn key(&self, action: &str, attempt: Option<u32>) -> String {
        let m = &self.input.task.metadata;
        effect_key(task::KIND, &m.name, &m.uid, action, attempt)
    }
}

fn decode(obj: &Object) -> Result<(TaskSpec, TaskStatus), StoreError> {
    let mut spec: TaskSpec = typed(task::KIND, &obj.spec)?;
    let st: TaskStatus = if obj.status.is_null() {
        TaskStatus::default()
    } else {
        serde_json::from_value(obj.status.clone())
            .map_err(|e| StoreError::Invalid(format!("Task status: {e}")))?
    };
    if let Some(execution) = &st.execution {
        spec.harness = execution.harness.clone();
        spec.host = execution.host.clone();
        spec.model = execution.model.clone();
    }
    Ok((spec, st))
}

fn session_key(obj: &Object) -> String {
    task::session_key(&obj.metadata.name, &obj.metadata.uid)
}

/// The brief has one key: every send of it reuses the key, so the session can
/// refuse a duplicate.
fn brief_key(obj: &Object) -> String {
    let m = &obj.metadata;
    effect_key(task::KIND, &m.name, &m.uid, BRIEF, None)
}

fn known_receipt(st: &TaskStatus, key: &str) -> bool {
    st.receipts
        .get(key)
        .is_some_and(|r| !r.outcome.is_unknown())
}
