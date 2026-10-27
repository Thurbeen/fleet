//! ConversationController: the mirror of one chat thread. From the stored
//! mirror, the deliveries the receive verb spooled, one read of the thread's
//! history and the Task the thread is tied to, it numbers what is new,
//! keeps the tie honest, and retires the object after its retention. It
//! performs no effect in this step: the platform is read, never written,
//! from here, and the Tasks a thread starts are a later step's.
//!
//! Pure: it reads the Conversation and what the probes saw, and returns a
//! Plan whose `status` is the whole Conversation status with the spool
//! drained of what was numbered, and whose `delete` retires the object.
//! The manager performs the plan through `record_received` and the fenced
//! delete.

use std::collections::{BTreeMap, BTreeSet};

use fleet_api::condition::{ConditionStatus, condition, set_condition, settle_transitions};
use fleet_api::conversation::{
    self, ConversationSpec, ConversationStatus, MESSAGE_CAP, Phase, SEEN_EVENTS_CAP, StoredMessage,
    TieState, Via,
};
use fleet_api::gateway::{
    Capabilities, ConversationRef, Delivery, Event, GatewayDefaults, HISTORY_FIRST_READ, History,
    HistoryObservation, HistoryQuery, Message, Starter, Verified,
};
use fleet_api::meta::{Millis, Object};
use fleet_api::plan::{Plan, Probe};
use fleet_api::store::FieldManager;
use fleet_api::task;
use fleet_api::validate::typed;
use fleet_api::{Observation, StoreError};

/// What one reconcile of one Conversation reads. Every outside fact arrives
/// here as a value; nothing is read from the world inside the controller.
#[derive(Debug, Clone)]
pub struct ConversationInput<'a> {
    pub object: &'a Object,
    pub observations: ConversationObservations,
    pub now: Millis,
    /// The `[gateway]` timings, and the route the spec names while the
    /// config has it.
    pub defaults: GatewayDefaults,
}

/// The answers to the probes; `None` means "not asked".
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ConversationObservations {
    /// The adapter's parse of `spec.address`, supplied before the first
    /// pass while `status.ref` is still absent.
    pub parsed: Option<Observation<ConversationRef>>,
    /// The answer to `Probe::History`.
    pub history: Option<HistoryObservation>,
    /// The answer to `Probe::Object` for the tied Task: `Known(None)` is
    /// readable absence.
    pub task: Option<Observation<Option<Object>>>,
    /// The answer to `Probe::Capabilities` for the account.
    pub capabilities: Option<Observation<Capabilities>>,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct ConversationController;

/// Condition types ConversationController writes.
pub mod conditions {
    /// The platform's history answered.
    pub const REACHABLE: &str = "Reachable";
    /// A live Task is tied.
    pub const TIED: &str = "Tied";
    /// `RouteUnknown`, `MentionsOnly`.
    pub const NEEDS_ATTENTION: &str = "NeedsAttention";
}
use conditions::*;

/// The route the spec names left the config.
const ROUTE_UNKNOWN: &str = "RouteUnknown";
/// The platform delivers only mentions, so only mentions are relayed.
const MENTIONS_ONLY: &str = "MentionsOnly";

impl ConversationController {
    /// The kind this controller reconciles and the field manager it writes as.
    pub const KIND: &'static str = conversation::KIND;
    pub const MANAGER: FieldManager = FieldManager::ConversationController;

    /// Whether the manager should run this controller on the object now:
    /// the thread has unread events, or `notBefore` passed. A deleting
    /// Conversation is never due.
    pub fn due(object: &Object, now: Millis) -> bool {
        if object.is_deleting() {
            return false;
        }
        let Ok((_, st)) = decode(object) else {
            // A status this controller cannot read is reported by the
            // reconcile, not skipped silently.
            return true;
        };
        !st.received.is_empty() || st.not_before.is_none_or(|t| t <= now)
    }

    /// What the next `reconcile` needs observed and does not have yet: the
    /// tied Task, then, when a history read is due, the account's
    /// capabilities and the read itself. The manager answers what is asked
    /// and asks again until nothing is.
    pub fn probes(&self, input: &ConversationInput<'_>) -> Result<Vec<Probe>, StoreError> {
        if input.object.is_deleting() {
            return Ok(Vec::new());
        }
        let (spec, st) = decode(input.object)?;
        let obs = &input.observations;
        let mut probes = Vec::new();
        if let Some(tie) = &st.task
            && tie.state == TieState::Created
            && obs.task.is_none()
        {
            probes.push(Probe::Object {
                kind: task::KIND.to_string(),
                name: tie.name.clone(),
            });
        }
        let reference = st
            .reference
            .clone()
            .or_else(|| obs.parsed.as_ref().and_then(|p| p.known().cloned()));
        let Some(reference) = reference else {
            return Ok(probes);
        };
        if let Some(query) = history_due(&spec, &st, input) {
            match &obs.capabilities {
                None => probes.push(Probe::Capabilities {
                    account: spec.account.clone(),
                }),
                Some(Observation::Known(caps)) if caps.history && obs.history.is_none() => {
                    probes.push(Probe::History {
                        account: spec.account.clone(),
                        conversation: reference,
                        query,
                    });
                }
                // No history on this platform, or capabilities Unknown:
                // nothing to read this pass.
                Some(_) => {}
            }
        }
        Ok(probes)
    }

    pub fn reconcile(&self, input: &ConversationInput<'_>) -> Result<Plan, StoreError> {
        if input.object.is_deleting() {
            // No finalizer and no owner: the store removes the record itself.
            return Ok(Plan::default());
        }
        let (spec, before) = decode(input.object)?;
        let mut pass = Pass {
            input,
            spec: &spec,
            st: before.clone(),
            attention: Attention::carried(&before),
            retry_after: None,
            delete: false,
        };
        pass.advance();
        settle_transitions(&mut pass.st.conditions, &before.conditions);
        pass.schedule();
        let status = if pass.st == before {
            None
        } else {
            Some(
                serde_json::to_value(&pass.st)
                    .map_err(|e| StoreError::Invalid(format!("Conversation status: {e}")))?,
            )
        };
        Ok(Plan {
            status,
            delete: pass.delete,
            ..Plan::default()
        })
    }
}

/// Which history read this pass wants, if any: the first read of the
/// thread, or the backstop while the thread is Tied and active.
fn history_due(
    spec: &ConversationSpec,
    st: &ConversationStatus,
    input: &ConversationInput<'_>,
) -> Option<HistoryQuery> {
    let now = input.now;
    if st.observed_at.is_none() {
        return Some(HistoryQuery::After {
            id: None,
            limit: HISTORY_FIRST_READ,
        });
    }
    if st.phase != Phase::Tied {
        return None;
    }
    let active = st
        .last_message_at
        .is_some_and(|at| now.saturating_sub(at) <= input.defaults.active_ms);
    let poll = spec.poll_ms(input.defaults.poll_ms);
    let elapsed = st
        .observed_at
        .is_some_and(|at| now.saturating_sub(at) >= poll);
    if !(active && elapsed) {
        return None;
    }
    let newest = st
        .messages
        .iter()
        .max_by(|a, b| (a.at, &a.id).cmp(&(b.at, &b.id)))
        .map(|m| m.id.clone());
    Some(HistoryQuery::After {
        id: newest,
        limit: HISTORY_FIRST_READ,
    })
}

fn decode(obj: &Object) -> Result<(ConversationSpec, ConversationStatus), StoreError> {
    let spec: ConversationSpec = typed(conversation::KIND, &obj.spec)?;
    let st: ConversationStatus = if obj.status.is_null() {
        ConversationStatus::default()
    } else {
        serde_json::from_value(obj.status.clone())
            .map_err(|e| StoreError::Invalid(format!("Conversation status: {e}")))?
    };
    Ok((spec, st))
}

/// The open NeedsAttention reasons, each with its message, carried from
/// pass to pass in the condition's message.
struct Attention(BTreeMap<String, String>);

impl Attention {
    fn carried(st: &ConversationStatus) -> Attention {
        let mut open = BTreeMap::new();
        if let Some(c) = condition(&st.conditions, NEEDS_ATTENTION)
            && c.status == ConditionStatus::True
        {
            for line in c.message.lines() {
                if let Some((reason, text)) = line.split_once(' ') {
                    open.insert(reason.to_string(), text.to_string());
                }
            }
        }
        Attention(open)
    }

    fn raise(&mut self, reason: &str, text: impl Into<String>) {
        self.0.insert(reason.to_string(), text.into());
    }

    fn clear(&mut self, reason: &str) {
        self.0.remove(reason);
    }

    fn message(&self) -> String {
        self.0
            .iter()
            .map(|(reason, text)| format!("{reason} {text}"))
            .collect::<Vec<_>>()
            .join("\n")
    }

    fn reason(&self) -> String {
        let reasons: BTreeSet<&str> = self.0.keys().map(String::as_str).collect();
        match reasons.len() {
            1 => reasons.into_iter().next().unwrap_or_default().to_string(),
            _ => "Multiple".to_string(),
        }
    }
}

/// One reconcile in progress.
struct Pass<'a, 'b> {
    input: &'a ConversationInput<'b>,
    spec: &'a ConversationSpec,
    st: ConversationStatus,
    attention: Attention,
    /// What the platform asked Fleet to wait after an Unknown read.
    retry_after: Option<Millis>,
    delete: bool,
}

/// A message first seen this pass, before it is numbered.
struct Fresh {
    message: Message,
    via: Via,
    edited_at: Option<Millis>,
}

impl Pass<'_, '_> {
    fn now(&self) -> Millis {
        self.input.now
    }

    fn cond(&mut self, t: &str, s: ConditionStatus, reason: &str, message: impl Into<String>) {
        let (generation, now) = (self.input.object.metadata.generation, self.now());
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

    fn advance(&mut self) {
        if self.st.reference.is_none() {
            match self.input.observations.parsed.clone() {
                Some(Observation::Known(reference)) => self.st.reference = Some(reference),
                Some(Observation::Unknown { why }) => {
                    self.cond(REACHABLE, ConditionStatus::Unknown, "AddressNotParsed", why);
                }
                None => {}
            }
        }
        self.tie();
        self.route();
        self.capabilities();
        let history = self.history();
        let spooled = std::mem::take(&mut self.st.received);
        self.number(history, spooled);
        self.phase();
        self.conditions();
    }

    /// The tie, as the Task's observation leaves it: a readably absent
    /// Task, or another Task under its name, orphans the thread. Unknown
    /// changes nothing.
    fn tie(&mut self) {
        let Some(tie) = self.st.task.clone() else {
            return;
        };
        if tie.state != TieState::Created {
            return;
        }
        let gone = match &self.input.observations.task {
            Some(Observation::Known(None)) => true,
            Some(Observation::Known(Some(obj))) => obj.metadata.uid != tie.uid,
            Some(Observation::Unknown { .. }) | None => false,
        };
        if gone && let Some(tie) = self.st.task.as_mut() {
            tie.state = TieState::Gone;
        }
    }

    fn route(&mut self) {
        match &self.input.defaults.route {
            Some(_) => self.attention.clear(ROUTE_UNKNOWN),
            None => self.attention.raise(
                ROUTE_UNKNOWN,
                format!(
                    "route {:?} is no longer under [gateway.routes]; nothing starts from this thread",
                    self.spec.route
                ),
            ),
        }
    }

    fn capabilities(&mut self) {
        if let Some(Observation::Known(caps)) = &self.input.observations.capabilities {
            if caps.unmentioned {
                self.attention.clear(MENTIONS_ONLY);
            } else {
                self.attention.raise(
                    MENTIONS_ONLY,
                    "the platform delivers only mentions to Fleet, so only mentions reach the mirror",
                );
            }
        }
    }

    /// The history read, when one was made: Known numbers below and marks
    /// the thread Reachable; Unknown leaves the mirror untouched and waits.
    fn history(&mut self) -> Option<History> {
        match self.input.observations.history.clone()? {
            HistoryObservation::Known(h) => {
                self.cond(REACHABLE, ConditionStatus::True, "Observed", "");
                self.st.observed_at = Some(self.now());
                Some(h)
            }
            HistoryObservation::Unsupported => {
                self.cond(
                    REACHABLE,
                    ConditionStatus::False,
                    "Unsupported",
                    "bots cannot read history on this platform",
                );
                // Nothing to read, now or later: the mirror is the events.
                self.st.observed_at = Some(self.now());
                None
            }
            HistoryObservation::Unknown {
                kind,
                why,
                retry_after,
            } => {
                self.cond(REACHABLE, ConditionStatus::Unknown, kind.reason(), why);
                self.retry_after = retry_after;
                None
            }
        }
    }

    /// Number what is new: the history's messages and the spooled
    /// deliveries, in one revision, oldest first by the platform; an edit
    /// moves a message's number, a removal takes it out. The drained
    /// delivery ids are remembered so a redelivery is still a no-op.
    fn number(&mut self, history: Option<History>, spooled: Vec<Delivery>) {
        let next = self.st.revision + 1;
        let mut messages = std::mem::take(&mut self.st.messages);
        let mut fresh: Vec<Fresh> = Vec::new();
        let mut changed = false;
        if let Some(h) = history {
            for m in h.messages {
                match messages.iter_mut().find(|s| s.id == m.id) {
                    Some(stored) => {
                        changed |= refresh(stored, &m, next);
                    }
                    None => fresh.push(Fresh {
                        message: m,
                        via: Via {
                            verified: Verified::History,
                            received_at: self.now(),
                        },
                        edited_at: None,
                    }),
                }
            }
            if let Some(starter) = h.starter {
                changed |= self.starter(starter);
            }
        }
        for delivery in spooled {
            if self.st.seen_events.contains(&delivery.id) {
                continue;
            }
            match delivery.event {
                Event::Message { message, .. } => {
                    match messages.iter_mut().find(|s| s.id == message.id) {
                        Some(stored) => {
                            // Seen from history already: one message.
                            changed |= refresh(stored, &message, next);
                        }
                        None if fresh.iter().any(|f| f.message.id == message.id) => {}
                        None => {
                            if message.starts_thread && self.st.starter.is_none() {
                                self.st.starter = Some(Starter::message(&message));
                                changed = true;
                            }
                            fresh.push(Fresh {
                                message,
                                via: Via {
                                    verified: delivery.verified,
                                    received_at: delivery.received_at,
                                },
                                edited_at: None,
                            });
                        }
                    }
                }
                Event::Edited {
                    id,
                    text,
                    edited_at,
                    ..
                } => {
                    if let Some(stored) = messages.iter_mut().find(|s| s.id == id) {
                        if stored.edited_at.is_none_or(|at| at < edited_at) {
                            stored.text = text;
                            stored.edited_at = Some(edited_at);
                            stored.seq = next;
                            changed = true;
                        }
                    } else if let Some(f) = fresh.iter_mut().find(|f| f.message.id == id)
                        && f.edited_at.is_none_or(|at| at < edited_at)
                    {
                        f.message.text = text;
                        f.edited_at = Some(edited_at);
                    }
                }
                Event::Removed { id, .. } => {
                    let before = messages.len() + fresh.len();
                    messages.retain(|s| s.id != id);
                    fresh.retain(|f| f.message.id != id);
                    if messages.len() + fresh.len() != before {
                        changed = true;
                    }
                    if self
                        .st
                        .starter
                        .as_ref()
                        .is_some_and(|s| s.id.as_deref() == Some(&id))
                    {
                        self.st.starter = Some(Starter::removed());
                        changed = true;
                    }
                }
            }
            self.st.seen_events.push(delivery.id);
        }
        if self.st.seen_events.len() > SEEN_EVENTS_CAP {
            let excess = self.st.seen_events.len() - SEEN_EVENTS_CAP;
            self.st.seen_events.drain(..excess);
        }
        if !fresh.is_empty() {
            changed = true;
            fresh.sort_by(|a, b| (a.message.at, &a.message.id).cmp(&(b.message.at, &b.message.id)));
            for (position, f) in fresh.into_iter().enumerate() {
                let m = f.message;
                if let Some(key) = &m.key
                    && let Some(starter) = self.st.starter.as_mut()
                    && starter.id.as_deref() == Some(&m.id)
                    && starter.key.is_none()
                {
                    starter.key = Some(key.clone());
                }
                messages.push(StoredMessage {
                    id: m.id,
                    seq: next,
                    first_seq: next,
                    position: position as u64,
                    at: m.at,
                    edited_at: f.edited_at,
                    author: m.author,
                    mentions_fleet: m.mentions_fleet,
                    text: m.text,
                    key: m.key,
                    via: f.via,
                });
            }
        }
        messages.sort_by(|a, b| (a.at, &a.id).cmp(&(b.at, &b.id)));
        while messages.len() > MESSAGE_CAP {
            let dropped = messages.remove(0);
            self.st.dropped_before = self.st.dropped_before.max(dropped.seq);
            changed = true;
        }
        let newest = messages.iter().map(|m| m.at).max();
        if let Some(at) = newest {
            self.st.last_message_at = Some(self.st.last_message_at.map_or(at, |l| l.max(at)));
        }
        self.st.messages = messages;
        if changed {
            self.st.revision = next;
        }
    }

    /// The thread's first message as a history read reports it.
    fn starter(&mut self, starter: Starter) -> bool {
        let merged = match &self.st.starter {
            Some(current) if current.removed => return false,
            Some(current) if !starter.removed => Starter {
                key: starter.key.clone().or_else(|| current.key.clone()),
                ..starter
            },
            _ => starter,
        };
        if self.st.starter.as_ref() == Some(&merged) {
            return false;
        }
        self.st.starter = Some(merged);
        true
    }

    fn phase(&mut self) {
        self.st.phase = match self.st.task.as_ref().map(|t| t.state) {
            Some(TieState::Created) => Phase::Tied,
            Some(TieState::Gone) => Phase::Orphaned,
            Some(TieState::Pending | TieState::Busy) => self.st.phase,
            None => Phase::Untied,
        };
    }

    fn conditions(&mut self) {
        match (self.st.phase, self.st.task.clone()) {
            (Phase::Tied, Some(tie)) => {
                self.cond(TIED, ConditionStatus::True, "TaskKnown", tie.name);
            }
            (Phase::Orphaned, Some(tie)) => self.cond(
                TIED,
                ConditionStatus::False,
                "TaskGone",
                format!(
                    "Task/{} is readably gone; the next admitted message starts a new Task",
                    tie.name
                ),
            ),
            _ => self.cond(TIED, ConditionStatus::False, "Untied", ""),
        }
        if !self.attention.0.is_empty() {
            let (reason, message) = (self.attention.reason(), self.attention.message());
            self.cond(NEEDS_ATTENTION, ConditionStatus::True, &reason, message);
        } else if condition(&self.st.conditions, NEEDS_ATTENTION)
            .is_some_and(|c| c.status == ConditionStatus::True)
        {
            self.cond(NEEDS_ATTENTION, ConditionStatus::False, "Resolved", "");
        }
    }

    /// When to look again: the backstop interval while Tied, the end of
    /// the retention while Untied or Orphaned, and past it the delete; an
    /// Unknown read's `retryAfter`, or one interval, before either.
    fn schedule(&mut self) {
        let now = self.now();
        let poll = self.spec.poll_ms(self.input.defaults.poll_ms);
        let retry = self.retry_after.filter(|_| {
            condition(&self.st.conditions, REACHABLE)
                .is_some_and(|c| c.status == ConditionStatus::Unknown)
        });
        let unknown_read = condition(&self.st.conditions, REACHABLE)
            .is_some_and(|c| c.status == ConditionStatus::Unknown);
        let next_read = now.saturating_add(retry.unwrap_or(poll));
        let waiting = self
            .st
            .task
            .as_ref()
            .is_some_and(|t| t.state == TieState::Busy);
        if self.st.phase == Phase::Tied || waiting {
            self.st.not_before = Some(next_read);
            return;
        }
        let orphaned_at = (self.st.phase == Phase::Orphaned)
            .then(|| condition(&self.st.conditions, TIED).map(|c| c.last_transition_time))
            .flatten();
        let since = [
            self.st.last_message_at,
            orphaned_at,
            self.input.object.metadata.creation_timestamp,
        ]
        .into_iter()
        .flatten()
        .max()
        .unwrap_or(now);
        let until = since.saturating_add(self.input.defaults.retain_ms);
        if until <= now {
            self.delete = true;
            self.st.not_before = Some(until);
        } else if unknown_read {
            self.st.not_before = Some(until.min(next_read));
        } else {
            self.st.not_before = Some(until);
        }
    }
}

/// A message the mirror holds, read again: its text or marker as the
/// platform has it now. A change moves its number.
fn refresh(stored: &mut StoredMessage, seen: &Message, next: u64) -> bool {
    let mut changed = false;
    if stored.text != seen.text {
        stored.text = seen.text.clone();
        stored.seq = next;
        changed = true;
    }
    if stored.key.is_none() && seen.key.is_some() {
        stored.key = seen.key.clone();
        stored.seq = next;
        changed = true;
    }
    if seen.mentions_fleet && !stored.mentions_fleet {
        stored.mentions_fleet = true;
        changed = true;
    }
    changed
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn attention_lines_round_trip_through_the_condition_message() {
        let mut a = Attention(BTreeMap::new());
        a.raise(ROUTE_UNKNOWN, "route eng left the config");
        let mut st = ConversationStatus::default();
        set_condition(
            &mut st.conditions,
            NEEDS_ATTENTION,
            ConditionStatus::True,
            &a.reason(),
            a.message(),
            1,
            1,
        );
        assert_eq!(Attention::carried(&st).message(), a.message());
        assert_eq!(a.reason(), ROUTE_UNKNOWN);
        a.raise(MENTIONS_ONLY, "only mentions");
        assert_eq!(a.reason(), "Multiple");
        a.clear(ROUTE_UNKNOWN);
        assert_eq!(a.reason(), MENTIONS_ONLY);
    }
}
