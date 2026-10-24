//! Fleet's manager: the only code that performs a controller's plan.
//!
//! It holds the one manager lease, walks the Tasks, asks TaskController what
//! to probe, probes through the runtime port, asks it to decide, then writes
//! in the order the contract requires: the status carrying each effect's
//! intent first (compare-and-swap, fenced by the lease), then the effect,
//! then its receipt. It also hosts the spec and worker verbs, so the CLI only
//! parses and prints.

mod execution;
pub mod verbs;

use fleet_api::plan::{Action, Effect, EffectOutcome, Observations, Plan, Probe, Receipt};
use fleet_api::runtime::Runtime;
use fleet_api::task;
use fleet_api::{Clock, LeaseToken, Millis, Object, Observation, Store, StoreError};
use fleet_controllers::{TaskController, TaskInput};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub use verbs::{ApplyOutcome, ResultOutcome, apply, delete, record_result};

/// How a bounded reconcile run ends.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RunOptions {
    /// Stop after this many passes even if work remains.
    pub max_passes: u32,
    /// The lease's time to live; renewed before every object and effect.
    pub lease_ttl: Millis,
}

/// What a run did, for `fleet reconcile`'s output.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunReport {
    pub holder: String,
    pub lease_token: u64,
    pub passes: u32,
    /// True when the last pass changed nothing: everything is waiting.
    pub idle: bool,
    pub status_writes: u32,
    pub effects: u32,
    pub conflicts: u32,
    /// Objects that could not be reconciled, with why. Never silently skipped.
    pub errors: Vec<String>,
    /// Observations that were Unknown during the run, with why.
    pub unknowns: Vec<String>,
}

pub struct Manager<'a> {
    pub store: &'a dyn Store,
    pub runtime: &'a dyn Runtime,
    pub clock: &'a dyn Clock,
    pub holder: String,
}

impl std::fmt::Debug for Manager<'_> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Manager")
            .field("holder", &self.holder)
            .finish_non_exhaustive()
    }
}

/// The outcome of one object's reconcile.
#[derive(Debug, Default)]
struct Step {
    wrote: u32,
    effects: u32,
    conflict: bool,
}

impl Manager<'_> {
    /// Keep the single manager lease for the entire foreground service.
    /// Every sweep visits all due Tasks, independently of agent activity.
    /// `wait` supplies the clock wait and shutdown policy at the CLI edge.
    pub fn serve(
        &self,
        opts: RunOptions,
        mut wait: impl FnMut(&RunReport) -> bool,
    ) -> Result<(), StoreError> {
        let mut lease = self.store.acquire_lease(&self.holder, opts.lease_ttl)?;
        let outcome = (|| {
            loop {
                lease = self.store.renew_lease(&lease, opts.lease_ttl)?;
                let mut report = RunReport {
                    holder: self.holder.clone(),
                    lease_token: lease.token,
                    ..RunReport::default()
                };
                self.passes(&mut lease, opts, &mut report)?;
                // The last bounded operation may have consumed most of
                // its lease. Give the subsequent poll wait a fresh lease.
                lease = self.store.renew_lease(&lease, opts.lease_ttl)?;
                if !wait(&report) {
                    return Ok(());
                }
            }
        })();
        if !matches!(outcome, Err(StoreError::LeaseLost(_))) {
            self.store.release_lease(&lease)?;
        }
        outcome
    }

    /// Take the lease, run passes until idle or `max_passes`, give the lease
    /// back. A lease that is held elsewhere or lost mid-run ends the run with
    /// that error: no write happens without it.
    pub fn run(&self, opts: RunOptions) -> Result<RunReport, StoreError> {
        let mut lease = self.store.acquire_lease(&self.holder, opts.lease_ttl)?;
        let mut report = RunReport {
            holder: self.holder.clone(),
            lease_token: lease.token,
            ..RunReport::default()
        };
        let outcome = self.passes(&mut lease, opts, &mut report);
        // A lost lease is not ours to release; anything else is.
        if !matches!(outcome, Err(StoreError::LeaseLost(_))) {
            self.store.release_lease(&lease)?;
        }
        outcome.map(|()| report)
    }

    fn passes(
        &self,
        lease: &mut LeaseToken,
        opts: RunOptions,
        report: &mut RunReport,
    ) -> Result<(), StoreError> {
        let mut passes = 0;
        while passes < opts.max_passes {
            passes += 1;
            report.passes += 1;
            let tasks = match self.store.list(task::KIND) {
                Observation::Known(t) => t,
                Observation::Unknown { why } => {
                    report.unknowns.push(format!("list Task: {why}"));
                    report.idle = false;
                    return Ok(());
                }
            };
            let mut busy = false;
            for obj in tasks.iter().filter(|t| self.due(t)) {
                *lease = self.store.renew_lease(lease, opts.lease_ttl)?;
                let step = self.reconcile_task(obj, lease, opts.lease_ttl, report);
                busy |= self.tally(step, &obj.key(), report)?;
            }
            if !busy {
                report.idle = true;
                return Ok(());
            }
        }
        report.idle = false;
        Ok(())
    }

    /// A Task waiting out `notBefore` after a failed or unknown attempt is
    /// not looked at again until then, in this run or the next.
    fn due(&self, obj: &Object) -> bool {
        obj.status
            .get("notBefore")
            .and_then(Value::as_u64)
            .is_none_or(|t| t <= self.clock.now())
    }

    fn tally(
        &self,
        step: Result<Step, StoreError>,
        key: &str,
        report: &mut RunReport,
    ) -> Result<bool, StoreError> {
        match step {
            Ok(step) => {
                report.status_writes += step.wrote;
                report.effects += step.effects;
                report.conflicts += u32::from(step.conflict);
                Ok(step.wrote > 0 || step.effects > 0 || step.conflict)
            }
            Err(e @ StoreError::LeaseLost(_)) => Err(e),
            Err(e) => {
                report.errors.push(format!("{key}: {e}"));
                Ok(false)
            }
        }
    }

    fn reconcile_task(
        &self,
        obj: &Object,
        lease: &mut LeaseToken,
        ttl: Millis,
        report: &mut RunReport,
    ) -> Result<Step, StoreError> {
        let spec: task::TaskSpec = fleet_api::validate::typed(task::KIND, &obj.spec)?;
        let st: task::TaskStatus = if obj.status.is_null() {
            task::TaskStatus::default()
        } else {
            fleet_api::validate::typed(task::KIND, &obj.status)?
        };
        let needs_launch = spec.config_ref.is_some() || !spec.env.is_empty();
        let mut effective = None;
        if needs_launch && !obj.is_deleting() && spec.dispatch {
            let snapshot = match self.store.launch(obj)? {
                Some(e) => e,
                None if st.execution.is_some()
                    || !st.intents.is_empty()
                    || st.session.is_some() =>
                {
                    return Err(StoreError::Unknown(
                        "private launch missing; refusing to re-resolve an existing execution"
                            .into(),
                    ));
                }
                None => {
                    *lease = self.store.renew_lease(lease, ttl)?;
                    let e = execution::resolve(self.store, self.runtime, obj, lease, ttl)?;
                    self.store.freeze_launch(obj, &e, lease)?
                }
            };
            if st.execution.is_none() {
                let mut status = st;
                status.execution = Some(snapshot.identity());
                let plan = Plan {
                    status: Some(serde_json::to_value(status).map_err(|_| {
                        StoreError::Invalid("execution status encoding failed".into())
                    })?),
                    ..Plan::default()
                };
                return self.perform_plan(obj, plan, lease, ttl, report);
            }
            if st.execution.as_ref() != Some(&snapshot.identity()) {
                return Err(StoreError::Unknown(
                    "private launch identity mismatch".into(),
                ));
            }
            effective = Some(snapshot);
        }
        let mut input = TaskInput {
            task: obj,
            observations: Observations::default(),
            now: self.clock.now(),
        };
        let ctl = TaskController;
        for probe in ctl.probes(&input)? {
            // Every probe is bounded but not instant: renew first, so a
            // Task's probes together never outlive the lease.
            *lease = self.store.renew_lease(lease, ttl)?;
            self.observe(&probe, obj, &mut input.observations, report);
        }
        let mut plan = ctl.reconcile(&input)?;
        if let Some(execution) = effective {
            for effect in &mut plan.effects {
                if let Action::Spawn(spawn) = &mut effect.action {
                    spawn.harness = execution.harness.clone();
                    spawn.host = execution.host.clone();
                    spawn.model = execution.model.clone();
                    spawn.environment_frozen = true;
                    spawn.env = execution.env.clone();
                }
            }
        }
        self.perform_plan(obj, plan, lease, ttl, report)
    }

    /// Write the status, run the effects with their receipts, then remove the
    /// finalizer: intent before effect, receipt after.
    fn perform_plan(
        &self,
        obj: &Object,
        plan: Plan,
        lease: &mut LeaseToken,
        ttl: Millis,
        report: &mut RunReport,
    ) -> Result<Step, StoreError> {
        let by = TaskController::MANAGER;
        let mut step = Step::default();
        let mut current = obj.clone();
        if let Some(status) = plan.status {
            let rv = current.metadata.resource_version;
            match self.store.update_status(
                task::KIND,
                &obj.metadata.name,
                status,
                rv,
                by,
                Some(&*lease),
            ) {
                Ok(o) => {
                    current = o;
                    step.wrote += 1;
                }
                // Someone wrote first (the worker, a person): nothing ran yet,
                // so drop this plan and decide again from what is there now.
                Err(StoreError::Conflict { .. }) => {
                    step.conflict = true;
                    return Ok(step);
                }
                Err(e) => return Err(e),
            }
        }
        for effect in &plan.effects {
            // Fence the effect itself, not only the writes around it: a
            // manager that lost the lease since its intent write stops here.
            // (The runtime cannot be fenced from fleet's side; an effect
            // already started when the lease changed hands is adopted or
            // refused by its key.)
            *lease = self.store.renew_lease(lease, ttl)?;
            let outcome = self.perform(effect);
            if let Observation::Unknown { why } = &outcome {
                report
                    .unknowns
                    .push(format!("{} {}: {why}", obj.key(), effect.action.name()));
            }
            step.effects += 1;
            current = self.write_receipt(&current, effect, outcome, lease)?;
            step.wrote += 1;
        }
        if let Some(finalizer) = plan.remove_finalizer {
            let rv = current.metadata.resource_version;
            self.store.remove_finalizer(
                task::KIND,
                &obj.metadata.name,
                &finalizer,
                rv,
                by,
                lease,
            )?;
            step.wrote += 1;
        }
        Ok(step)
    }

    fn observe(
        &self,
        probe: &Probe,
        task: &Object,
        obs: &mut Observations,
        report: &mut RunReport,
    ) {
        let rt = self.runtime;
        let key = task.key();
        let mut note = |what: &str, why: Option<&str>| {
            if let Some(why) = why {
                report.unknowns.push(format!("{key} {what}: {why}"));
            }
        };
        match probe {
            Probe::Host { host } => {
                let o = rt.host(host);
                note(&format!("host {host}"), o.why());
                obs.host = Some(o);
            }
            Probe::Find { host, key: k } => {
                let o = rt.find(host, k);
                note(&format!("find on {host}"), o.why());
                obs.find = Some(o);
            }
            Probe::Ready { session } => {
                let o = rt.ready(session);
                note("ready", o.why());
                obs.ready = Some(o);
            }
            Probe::BriefDelivered { session, key: k } => {
                let o = rt.brief_delivered(session, k);
                note("briefDelivered", o.why());
                obs.brief_delivered = Some(o);
            }
            Probe::AtRest { session } => {
                let o = rt.at_rest(session);
                note("atRest", o.why());
                obs.at_rest = Some(o);
            }
        }
    }

    fn perform(&self, effect: &Effect) -> Observation<EffectOutcome> {
        match &effect.action {
            Action::Spawn(spec) => self.runtime.spawn(spec),
            Action::Brief { session, text } => self.runtime.brief(session, text, &effect.key),
            Action::Release { session } => self.runtime.release(session),
        }
    }

    /// The effect happened (or may have): its receipt must land even if the
    /// object moved meanwhile, so this re-reads and retries on a conflict.
    fn write_receipt(
        &self,
        current: &Object,
        effect: &Effect,
        outcome: Observation<EffectOutcome>,
        lease: &LeaseToken,
    ) -> Result<Object, StoreError> {
        let receipt = Receipt {
            action: effect.action.name().to_string(),
            at: self.clock.now(),
            outcome,
        };
        let receipt =
            serde_json::to_value(&receipt).map_err(|e| StoreError::Invalid(e.to_string()))?;
        let mut obj = current.clone();
        for _ in 0..16 {
            let mut status = if obj.status.is_object() {
                obj.status.clone()
            } else {
                Value::Object(serde_json::Map::new())
            };
            let receipts = status.as_object_mut().map(|m| {
                m.entry("receipts")
                    .or_insert_with(|| Value::Object(serde_json::Map::new()))
            });
            match receipts.and_then(Value::as_object_mut) {
                Some(r) => {
                    r.insert(effect.key.clone(), receipt.clone());
                }
                None => {
                    return Err(StoreError::Invalid(format!(
                        "{}: status.receipts is not a map",
                        obj.key()
                    )));
                }
            }
            let rv = obj.metadata.resource_version;
            match self.store.update_status(
                task::KIND,
                &obj.metadata.name,
                status,
                rv,
                TaskController::MANAGER,
                Some(lease),
            ) {
                Ok(o) => return Ok(o),
                Err(StoreError::Conflict { .. }) => {
                    obj = match self.store.get(task::KIND, &obj.metadata.name) {
                        Observation::Known(Some(o)) => o,
                        Observation::Known(None) => return Err(StoreError::NotFound(obj.key())),
                        Observation::Unknown { why } => return Err(StoreError::Unknown(why)),
                    };
                }
                Err(e) => return Err(e),
            }
        }
        Err(StoreError::Busy(format!(
            "{}: receipt for {} kept conflicting",
            obj.key(),
            effect.key
        )))
    }
}
