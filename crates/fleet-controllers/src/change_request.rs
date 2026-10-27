//! ChangeRequestController: mirrors one change on a forge. From the stored
//! snapshot and one complete observation it decides whether anything
//! changed, numbers what did, and says when to look again. It performs no
//! effect: the forge is read, never written, from here.
//!
//! Pure: it reads the ChangeRequest and what the probe saw, and returns a
//! Plan. The manager performs the plan.

use fleet_api::change_request::{
    self, CALL_WINDOW_MS, CLOSED_POLL_FACTOR, ChangeRequestDefaults, ChangeRequestSpec,
    ChangeRequestStatus, ForgeCalls, Head, NOTE_CAP, Phase, Sequenced, StoredNote,
};
use fleet_api::condition::{ConditionStatus, set_condition, settle_transitions};
use fleet_api::forge::{
    ChangeObservation, ChangeRef, ChangeSnapshot, ChangeState, MAX_CALLS_PER_OBSERVATION, Note,
    Uncertainty,
};
use fleet_api::meta::{Millis, Object};
use fleet_api::plan::{Plan, Probe};
use fleet_api::store::FieldManager;
use fleet_api::validate::typed;
use fleet_api::{Observation, StoreError};

/// What one reconcile of one ChangeRequest reads. Every outside fact arrives
/// here as a value; nothing is read from the world inside the controller.
#[derive(Debug, Clone)]
pub struct ChangeRequestInput<'a> {
    pub object: &'a Object,
    pub observations: ChangeRequestObservations,
    pub now: Millis,
    /// The configured values the spec may override, the poll interval
    /// already resolved for the object's host.
    pub defaults: ChangeRequestDefaults,
}

/// The answers to the probes; `None` means "not asked".
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ChangeRequestObservations {
    /// The manager's parse of `spec.url`, supplied before the first poll
    /// while `status.ref` is still absent: the probe names the ref.
    pub parsed: Option<Observation<ChangeRef>>,
    /// The answer to `Probe::Change`.
    pub change: Option<ChangeObservation>,
    /// The forge calls that answer took; `None` or 0 when none was made.
    pub calls: Option<u64>,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct ChangeRequestController;

/// Condition types ChangeRequestController writes.
pub mod conditions {
    /// The last poll read the change completely.
    pub const REACHABLE: &str = "Reachable";
    /// The forge's own answer about merging, reported and never acted on.
    pub const MERGEABLE: &str = "Mergeable";
    /// `Merged`, `ClosedGraceExpired` or `Gone`.
    pub const SETTLED: &str = "Settled";
}
use conditions::*;

impl ChangeRequestController {
    /// The kind this controller reconciles and the field manager it writes as.
    pub const KIND: &'static str = change_request::KIND;
    pub const MANAGER: FieldManager = FieldManager::ChangeRequestController;

    /// What the next `reconcile` needs observed: one complete observation of
    /// the change while it is watched and due, nothing once it settled.
    pub fn probes(&self, input: &ChangeRequestInput<'_>) -> Result<Vec<Probe>, StoreError> {
        let (_, st) = decode(input.object)?;
        if input.object.is_deleting()
            || st.phase == Phase::Settled
            || st.not_before.is_some_and(|t| t > input.now)
        {
            return Ok(Vec::new());
        }
        let reference = st.reference.or_else(|| {
            input
                .observations
                .parsed
                .as_ref()
                .and_then(|p| p.known().cloned())
        });
        Ok(reference
            .map(|change| vec![Probe::Change { change }])
            .unwrap_or_default())
    }

    pub fn reconcile(&self, input: &ChangeRequestInput<'_>) -> Result<Plan, StoreError> {
        let (spec, before) = decode(input.object)?;
        if input.object.is_deleting() {
            // No finalizer and no owner: the store removes the record itself.
            return Ok(Plan::default());
        }
        let mut pass = Pass {
            input,
            spec: &spec,
            st: before.clone(),
            generation: input.object.metadata.generation,
            delete: false,
        };
        pass.advance();
        settle_transitions(&mut pass.st.conditions, &before.conditions);
        let status = if pass.st == before {
            None
        } else {
            Some(
                serde_json::to_value(&pass.st)
                    .map_err(|e| StoreError::Invalid(format!("ChangeRequest status: {e}")))?,
            )
        };
        Ok(Plan {
            status,
            delete: pass.delete,
            ..Plan::default()
        })
    }
}

/// One reconcile in progress.
struct Pass<'a, 'b> {
    input: &'a ChangeRequestInput<'b>,
    spec: &'a ChangeRequestSpec,
    st: ChangeRequestStatus,
    generation: u64,
    delete: bool,
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

    /// The phase's poll interval: `pollMs` while Watching, ten times that
    /// while Closed.
    fn interval(&self) -> Millis {
        let poll = self.spec.poll_ms(&self.input.defaults);
        match self.st.phase {
            Phase::Closed => poll.saturating_mul(CLOSED_POLL_FACTOR),
            Phase::Watching | Phase::Settled => poll,
        }
    }

    fn wait(&mut self, ms: Millis) {
        self.st.not_before = Some(self.now().saturating_add(ms));
    }

    fn advance(&mut self) {
        if self.st.phase == Phase::Settled {
            return self.retain();
        }
        if self.st.reference.is_none() {
            match self.input.observations.parsed.clone() {
                Some(Observation::Known(reference)) => self.st.reference = Some(reference),
                Some(Observation::Unknown { why }) => {
                    self.cond(REACHABLE, ConditionStatus::Unknown, "UrlNotParsed", why);
                    let interval = self.interval();
                    self.wait(interval);
                    return;
                }
                None => return,
            }
        }
        if self.input.observations.change.is_some() {
            self.record_calls();
        }
        match self.input.observations.change.clone() {
            None => {}
            Some(ChangeObservation::Unknown {
                kind,
                why,
                retry_after,
            }) => {
                // The snapshot is untouched: Unknown never means closed,
                // merged or empty.
                self.cond(REACHABLE, ConditionStatus::Unknown, kind.reason(), why);
                let wait = retry_after.unwrap_or_else(|| self.interval());
                self.wait(wait);
            }
            Some(ChangeObservation::Known(None)) => {
                self.cond(REACHABLE, ConditionStatus::True, "Observed", "");
                self.st.observed_at = Some(self.now());
                self.settle("Gone", "the forge answered that the change does not exist");
            }
            Some(ChangeObservation::Known(Some(snapshot))) => {
                self.cond(REACHABLE, ConditionStatus::True, "Observed", "");
                self.st.observed_at = Some(self.now());
                self.mergeable(&snapshot);
                self.absorb(snapshot);
                self.schedule();
            }
        }
    }

    /// Keep this object's share of its host's budget: the calls of its polls
    /// in the last window, this one's included.
    fn record_calls(&mut self) {
        let now = self.now();
        let since = now.saturating_sub(CALL_WINDOW_MS);
        self.st.forge_calls.retain(|c| c.at > since);
        if let Some(count) = self.input.observations.calls.filter(|c| *c > 0) {
            self.st.forge_calls.push(ForgeCalls { at: now, count });
        }
    }

    /// Settled: nothing is asked; the object goes once its retention passed.
    fn retain(&mut self) {
        let now = self.now();
        let settled_at = *self.st.settled_at.get_or_insert(now);
        let until = settled_at.saturating_add(self.spec.settled_ttl_ms(&self.input.defaults));
        if until <= now {
            self.delete = true;
        } else {
            self.st.not_before = Some(until);
        }
    }

    fn settle(&mut self, reason: &str, message: &str) {
        let now = self.now();
        self.st.phase = Phase::Settled;
        self.st.settled_at = Some(now);
        self.cond(SETTLED, ConditionStatus::True, reason, message);
        // Not looked at again until its retention has passed.
        let ttl = self.spec.settled_ttl_ms(&self.input.defaults);
        self.st.not_before = Some(now.saturating_add(ttl));
    }

    /// The forge's own answer, reported as a condition and never acted on.
    fn mergeable(&mut self, snapshot: &ChangeSnapshot) {
        let reason = snapshot.mergeable.reason.clone().unwrap_or_default();
        match snapshot.mergeable.value {
            Some(true) => self.cond(MERGEABLE, ConditionStatus::True, "Mergeable", reason),
            Some(false) => {
                let word = if reason.is_empty() {
                    "NotMergeable".to_string()
                } else {
                    camel(&reason)
                };
                self.cond(MERGEABLE, ConditionStatus::False, &word, reason);
            }
            None => self.cond(
                MERGEABLE,
                ConditionStatus::Unknown,
                "Computing",
                "the forge has not answered yet",
            ),
        }
    }

    /// Compare the observation with the stored snapshot. If anything
    /// differs, `revision` moves and every new or changed item takes it;
    /// otherwise the snapshot is left exactly as stored.
    fn absorb(&mut self, snapshot: ChangeSnapshot) {
        let st = &self.st;
        let next = st.revision + 1;
        let state_changed = st.state.as_ref().is_none_or(|s| s.value != snapshot.state);
        let head_id_changed = st.head.as_ref().is_none_or(|h| h.id != snapshot.head.id);
        let head_changed = head_id_changed
            || st.head.as_ref().is_none_or(|h| {
                h.checks != snapshot.head.checks || h.failing != snapshot.head.failing
            });
        let decision_changed = st
            .review_decision
            .as_ref()
            .is_none_or(|d| d.value != snapshot.review_decision);
        let mergeable_changed = st.mergeable.as_ref() != Some(&snapshot.mergeable);
        let threads_changed = st.threads.as_ref() != Some(&snapshot.threads);
        let reviews_changed = st.reviews != snapshot.reviews;
        let (notes, dropped_before, notes_changed) =
            merge_notes(&st.notes, snapshot.notes, st.dropped_before, next);
        if !(state_changed
            || head_changed
            || decision_changed
            || mergeable_changed
            || threads_changed
            || reviews_changed
            || notes_changed)
        {
            return;
        }
        let now = self.now();
        let st = &mut self.st;
        st.revision = next;
        if state_changed {
            st.state = Some(Sequenced {
                value: snapshot.state,
                seq: next,
            });
        }
        if snapshot.state != ChangeState::Draft {
            st.ever_ready = true;
        }
        if head_changed {
            let first_seen_at = match (&st.head, head_id_changed) {
                (Some(h), false) => h.first_seen_at,
                _ => now,
            };
            st.head = Some(Head {
                id: snapshot.head.id,
                checks: snapshot.head.checks,
                failing: snapshot.head.failing,
                seq: next,
                first_seen_at,
            });
        }
        if decision_changed {
            st.review_decision = Some(Sequenced {
                value: snapshot.review_decision,
                seq: next,
            });
        }
        st.mergeable = Some(snapshot.mergeable);
        st.threads = Some(snapshot.threads);
        st.reviews = snapshot.reviews;
        st.notes = notes;
        st.dropped_before = dropped_before;
    }

    /// Move between Watching, Closed and Settled on what the state says, and
    /// set the next poll.
    fn schedule(&mut self) {
        let Some(state) = self.st.state.as_ref().map(|s| s.value) else {
            return;
        };
        let now = self.now();
        match state {
            ChangeState::Merged => self.settle("Merged", "the change was merged"),
            ChangeState::Closed => {
                if self.st.phase == Phase::Watching {
                    self.st.phase = Phase::Closed;
                    self.st.closed_at = Some(now);
                }
                let closed_at = *self.st.closed_at.get_or_insert(now);
                let grace = self.spec.closed_grace_ms(&self.input.defaults);
                if now >= closed_at.saturating_add(grace) {
                    self.settle(
                        "ClosedGraceExpired",
                        "the change stayed closed for the whole grace period",
                    );
                } else {
                    let interval = self.interval();
                    self.wait(interval);
                }
            }
            ChangeState::Draft | ChangeState::Open => {
                if self.st.phase == Phase::Closed {
                    self.st.phase = Phase::Watching;
                    self.st.closed_at = None;
                }
                let interval = self.interval();
                self.wait(interval);
            }
        }
    }
}

/// Merge what the forge holds with what is stored, under the cap.
///
/// The newest [`NOTE_CAP`] notes by creation time are the window. A note in
/// the window keeps its number unless its body was edited or its thread went
/// from resolved to unresolved; one seen for the first time takes `next`. A
/// stored note pushed below the window raises `droppedBefore` to its number;
/// one that left the forge altogether just leaves. A note below the window
/// that was never stored was rotated out before and is not numbered again.
///
/// Returns the notes to store, the new `droppedBefore`, and whether anything
/// differs from what was stored.
fn merge_notes(
    stored: &[StoredNote],
    mut seen: Vec<Note>,
    dropped_before: u64,
    next: u64,
) -> (Vec<StoredNote>, u64, bool) {
    seen.sort_by(|a, b| (a.at, &a.id).cmp(&(b.at, &b.id)));
    let cut = seen.len().saturating_sub(NOTE_CAP);
    let (below, window) = seen.split_at(cut);
    let mut out = Vec::with_capacity(window.len());
    let mut changed = false;
    for note in window {
        match stored.iter().find(|s| s.id == note.id) {
            Some(s) => {
                let moved = s.body != note.body
                    || s.edited_at != note.edited_at
                    || (s.resolved == Some(true) && note.resolved == Some(false));
                if s.as_seen() != *note {
                    changed = true;
                }
                let seq = if moved { next } else { s.seq };
                out.push(StoredNote::seen(note.clone(), seq));
            }
            None => {
                changed = true;
                out.push(StoredNote::seen(note.clone(), next));
            }
        }
    }
    let mut dropped = dropped_before;
    for s in stored {
        if window.iter().any(|n| n.id == s.id) {
            continue;
        }
        changed = true;
        if below.iter().any(|n| n.id == s.id) {
            dropped = dropped.max(s.seq);
        }
    }
    if !changed {
        return (stored.to_vec(), dropped_before, false);
    }
    (out, dropped, true)
}

/// A forge's reason word (`ci_must_pass`, `changes-requested`, `blocked`)
/// as a CamelCase condition reason.
fn camel(reason: &str) -> String {
    let mut out = String::new();
    let mut upper = true;
    for c in reason.chars() {
        if c.is_ascii_alphanumeric() {
            if upper {
                out.extend(c.to_uppercase());
                upper = false;
            } else {
                out.push(c);
            }
        } else {
            upper = true;
        }
    }
    if out.is_empty() {
        "NotMergeable".to_string()
    } else {
        out
    }
}

/// One forge host's pacing over a sweep: the calls every ChangeRequest on it
/// made in the last [`CALL_WINDOW_MS`], as their statuses record them, and a
/// back-off the forge asked for. The manager seeds it from the stored
/// objects, asks it before each poll and tells it what each poll spent; the
/// store is its memory, so a new manager run still counts what the last one
/// spent.
#[derive(Debug, Clone, Default)]
pub struct HostGate {
    budget: Option<u64>,
    spent: Vec<ForgeCalls>,
    paused: Option<Pause>,
}

#[derive(Debug, Clone)]
struct Pause {
    until: Millis,
    kind: Uncertainty,
    why: String,
}

impl HostGate {
    /// A gate for a host whose ChangeRequests may make `budget` calls
    /// together in any window; `None` for no budget.
    pub fn new(budget: Option<u64>) -> HostGate {
        HostGate {
            budget,
            ..HostGate::default()
        }
    }

    /// Count one stored ChangeRequest of the host: the calls its status
    /// records, and the wait of a throttled answer it is serving, which the
    /// whole host serves with it. An undecodable status counts nothing.
    pub fn seed(&mut self, obj: &Object) {
        let Ok((_, st)) = decode(obj) else {
            return;
        };
        self.spent.extend(st.forge_calls);
        let throttled = st.conditions.iter().any(|c| {
            c.type_ == REACHABLE
                && c.status == ConditionStatus::Unknown
                && c.reason == Uncertainty::Throttled.reason()
        });
        if let (true, Some(until)) = (throttled, st.not_before) {
            self.pause(
                until,
                Uncertainty::Throttled,
                "the forge asked to slow down",
            );
        }
    }

    /// Back the whole host off until `until`, keeping the longest wait.
    pub fn pause(&mut self, until: Millis, kind: Uncertainty, why: &str) {
        if self.paused.as_ref().is_none_or(|p| p.until < until) {
            self.paused = Some(Pause {
                until,
                kind,
                why: why.to_string(),
            });
        }
    }

    /// Record the calls a poll started at `now` made.
    pub fn charge(&mut self, now: Millis, count: u64) {
        if count > 0 {
            self.spent.push(ForgeCalls { at: now, count });
        }
    }

    /// `None` when a poll may start now: the host is not backing off and
    /// its budget still holds one observation's worst case. Otherwise the
    /// Unknown answer to hand the controller instead of asking, with the
    /// wait until the host may be asked again.
    pub fn admit(&self, now: Millis) -> Option<ChangeObservation> {
        if let Some(p) = self.paused.as_ref().filter(|p| p.until > now) {
            return Some(ChangeObservation::Unknown {
                kind: p.kind,
                why: format!(
                    "deferred with the host: {}; the last observation stands and is stale",
                    p.why
                ),
                retry_after: Some(p.until - now),
            });
        }
        let budget = self.budget?;
        let since = now.saturating_sub(CALL_WINDOW_MS);
        let mut live: Vec<&ForgeCalls> = self.spent.iter().filter(|c| c.at > since).collect();
        live.sort_by_key(|c| c.at);
        let mut total: u64 = live.iter().map(|c| c.count).sum();
        if total + MAX_CALLS_PER_OBSERVATION <= budget {
            return None;
        }
        // The first moment enough of the window has aged out.
        let mut free_at = now + CALL_WINDOW_MS;
        for c in live {
            total -= c.count;
            if total + MAX_CALLS_PER_OBSERVATION <= budget {
                free_at = c.at + CALL_WINDOW_MS;
                break;
            }
        }
        Some(ChangeObservation::Unknown {
            kind: Uncertainty::OverBudget,
            why: format!(
                "the host's budget of {budget} forge calls a minute is spent; the last observation stands and is stale"
            ),
            retry_after: Some(free_at.saturating_sub(now).max(1)),
        })
    }
}

fn decode(obj: &Object) -> Result<(ChangeRequestSpec, ChangeRequestStatus), StoreError> {
    let spec: ChangeRequestSpec = typed(change_request::KIND, &obj.spec)?;
    let st: ChangeRequestStatus = if obj.status.is_null() {
        ChangeRequestStatus::default()
    } else {
        serde_json::from_value(obj.status.clone())
            .map_err(|e| StoreError::Invalid(format!("ChangeRequest status: {e}")))?
    };
    Ok((spec, st))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn forge_reasons_become_camel_case_condition_reasons() {
        assert_eq!(camel("ci_must_pass"), "CiMustPass");
        assert_eq!(camel("changes-requested"), "ChangesRequested");
        assert_eq!(camel("BLOCKED"), "BLOCKED");
        assert_eq!(camel("conflicts"), "Conflicts");
        assert_eq!(camel("--"), "NotMergeable");
    }
}
