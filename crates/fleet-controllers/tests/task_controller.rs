//! TaskController as a pure function: a Task and what the probes saw go in;
//! a plan comes out. Each case is one transition or one Unknown of the
//! Task's lifecycle.

use fleet_api::condition::ConditionStatus;
use fleet_api::plan::{Action, EffectOutcome, Observations, Plan, Probe, Receipt};
use fleet_api::task::{Phase, SessionRef, TaskStatus};
use fleet_api::{Object, Observation};
use fleet_controllers::{MAX_ATTEMPTS, RETRY_DELAY_MS, TaskController, TaskInput};
use serde_json::json;

const NOW: u64 = 1_000_000;

fn task(dispatch: bool, status: Option<TaskStatus>) -> Object {
    let mut o: Object = serde_json::from_value(json!({
        "apiVersion": "fleet/v1alpha1",
        "kind": "Task",
        "metadata": {"name": "t", "uid": "u7", "generation": 1, "resourceVersion": 7, "finalizers": ["fleet.dev/release"]},
        "spec": {"harness": "claude-code", "brief": "do it", "workspace": {"dir": "w"}, "dispatch": dispatch}
    }))
    .unwrap();
    if let Some(s) = status {
        o.status = serde_json::to_value(s).unwrap();
    }
    o
}

fn session() -> SessionRef {
    SessionRef {
        id: "s1".into(),
        host: "local".into(),
        key: "t.u7".into(),
    }
}

fn run_at(obj: &Object, obs: Observations, now: u64) -> (Plan, TaskStatus) {
    let input = TaskInput {
        task: obj,
        observations: obs,
        now,
    };
    let plan = TaskController.reconcile(&input).unwrap();
    let st = match &plan.status {
        Some(v) => serde_json::from_value(v.clone()).unwrap(),
        None => serde_json::from_value(obj.status.clone()).unwrap_or_default(),
    };
    (plan, st)
}

fn run(obj: &Object, obs: Observations) -> (Plan, TaskStatus) {
    run_at(obj, obs, NOW)
}

fn probes(obj: &Object) -> Vec<Probe> {
    let input = TaskInput {
        task: obj,
        observations: Observations::default(),
        now: NOW,
    };
    TaskController.probes(&input).unwrap()
}

fn cond<'a>(st: &'a TaskStatus, t: &str) -> &'a fleet_api::condition::Condition {
    st.conditions
        .iter()
        .find(|c| c.type_ == t)
        .unwrap_or_else(|| panic!("no {t} in {st:#?}"))
}

fn receipt(action: &str, outcome: Observation<EffectOutcome>) -> Receipt {
    Receipt {
        action: action.into(),
        at: NOW - 10,
        outcome,
    }
}

#[test]
fn an_undispatched_task_asks_nothing_and_does_nothing() {
    let obj = task(false, None);
    assert!(probes(&obj).is_empty());
    let (plan, st) = run(&obj, Observations::default());
    assert!(plan.effects.is_empty());
    assert_eq!(st.phase, Phase::Pending);
    assert_eq!(cond(&st, "Spawned").reason, "AwaitingDispatch");
}

#[test]
fn a_dispatched_task_records_the_spawn_intent_with_its_effect() {
    let obj = task(true, None);
    assert_eq!(
        probes(&obj),
        vec![Probe::Host {
            host: "local".into()
        }]
    );
    let obs = Observations {
        host: Some(Observation::Known(true)),
        ..Default::default()
    };
    let (plan, st) = run(&obj, obs);
    assert_eq!(st.phase, Phase::Spawning);
    assert_eq!(plan.effects.len(), 1);
    let e = &plan.effects[0];
    assert_eq!(e.key, "task/t/u7/spawn/1");
    assert!(
        st.intents.contains_key(&e.key),
        "the intent is in the status written before the effect"
    );
    match &e.action {
        Action::Spawn(s) => assert_eq!(
            (s.key.as_str(), s.host.as_str(), s.harness.as_str()),
            ("t.u7", "local", "claude-code")
        ),
        other => panic!("{other:?}"),
    }
    assert_eq!(st.observed_generation, 1);
}

#[test]
fn an_unknown_host_never_spawns() {
    let obs = Observations {
        host: Some(Observation::unknown("timeout")),
        ..Default::default()
    };
    let (plan, st) = run(&task(true, None), obs);
    assert!(plan.effects.is_empty());
    assert_eq!(
        cond(&st, "HostReachable").status,
        fleet_api::condition::ConditionStatus::Unknown
    );
}

fn spawning(receipt_outcome: Option<Observation<EffectOutcome>>) -> Object {
    let mut st = TaskStatus {
        phase: Phase::Spawning,
        spawn_attempts: 1,
        ..Default::default()
    };
    if let Some(o) = receipt_outcome {
        st.receipts
            .insert("task/t/u7/spawn/1".into(), receipt("spawn", o));
    }
    task(true, Some(st))
}

#[test]
fn an_intent_without_a_receipt_is_decided_by_the_session_key() {
    let obj = spawning(None);
    assert_eq!(
        probes(&obj),
        vec![Probe::Find {
            host: "local".into(),
            key: "t.u7".into()
        }]
    );

    let found = Observations {
        find: Some(Observation::Known(Some(session()))),
        ..Default::default()
    };
    let (plan, st) = run(&obj, found);
    assert!(plan.effects.is_empty(), "adopted, not respawned");
    assert_eq!(st.phase, Phase::Spawned);
    assert_eq!(cond(&st, "Spawned").reason, "Adopted");

    let unknown = Observations {
        find: Some(Observation::unknown("list unreadable")),
        ..Default::default()
    };
    let (plan, st) = run(&obj, unknown);
    assert!(plan.effects.is_empty(), "Unknown is not absence");
    assert_eq!(st.phase, Phase::Spawning);

    let absent = Observations {
        find: Some(Observation::Known(None)),
        ..Default::default()
    };
    let (plan, st) = run(&obj, absent);
    assert!(plan.effects.is_empty());
    assert_eq!(st.phase, Phase::Pending, "tried again on the next pass");
}

#[test]
fn spawn_attempts_are_bounded() {
    let mut st = TaskStatus {
        phase: Phase::Spawning,
        spawn_attempts: MAX_ATTEMPTS,
        ..Default::default()
    };
    st.receipts.insert(
        format!("task/t/u7/spawn/{MAX_ATTEMPTS}"),
        receipt(
            "spawn",
            Observation::Known(EffectOutcome::Failed { why: "no".into() }),
        ),
    );
    let (plan, st) = run(&task(true, Some(st)), Observations::default());
    assert!(plan.effects.is_empty());
    assert_eq!(st.phase, Phase::Stalled);
    assert_eq!(cond(&st, "NeedsAttention").reason, "Stalled");
}

fn spawned(extra: impl FnOnce(&mut TaskStatus)) -> Object {
    let mut st = TaskStatus {
        phase: Phase::Spawned,
        spawn_attempts: 1,
        session: Some(session()),
        ..Default::default()
    };
    extra(&mut st);
    task(true, Some(st))
}

#[test]
fn the_brief_waits_for_a_ready_session() {
    let obj = spawned(|_| {});
    let live = Some(Observation::Known(Some(session())));
    let (plan, _) = run(
        &obj,
        Observations {
            find: live.clone(),
            ready: Some(Observation::Known(false)),
            ..Default::default()
        },
    );
    assert!(
        plan.effects.is_empty(),
        "never type into an unproven session (P7)"
    );
    let (plan, _) = run(
        &obj,
        Observations {
            find: live.clone(),
            ready: Some(Observation::unknown("capture failed")),
            ..Default::default()
        },
    );
    assert!(plan.effects.is_empty());
    let (plan, st) = run(
        &obj,
        Observations {
            find: live,
            ready: Some(Observation::Known(true)),
            ..Default::default()
        },
    );
    assert_eq!(plan.effects.len(), 1);
    assert_eq!(plan.effects[0].key, "task/t/u7/brief");
    assert_eq!(st.brief_sends, 1);
}

#[test]
fn a_brief_without_a_known_receipt_asks_the_session_and_never_resends_blind() {
    let obj = spawned(|st| {
        st.brief_sends = 1;
        st.intents.insert(
            "task/t/u7/brief".into(),
            fleet_api::plan::Intent {
                action: "brief".into(),
                at: NOW - 5,
                generation: 1,
            },
        );
    });
    assert!(probes(&obj).contains(&Probe::BriefDelivered {
        session: session(),
        key: "task/t/u7/brief".into()
    }));
    let live = Some(Observation::Known(Some(session())));

    let (plan, st) = run(
        &obj,
        Observations {
            find: live.clone(),
            brief_delivered: Some(Observation::Known(true)),
            ..Default::default()
        },
    );
    assert!(plan.effects.is_empty());
    assert_eq!(st.phase, Phase::Briefed);
    assert!(st.brief.as_ref().unwrap().recovered);

    let mut last_probe = obj.clone();
    last_probe.status["briefProbeFailures"] = serde_json::json!(MAX_ATTEMPTS - 1);
    let (plan, st) = run(
        &last_probe,
        Observations {
            find: live.clone(),
            brief_delivered: Some(Observation::unknown("?")),
            ..Default::default()
        },
    );
    assert!(
        plan.effects.is_empty(),
        "never resent on an unknown receipt"
    );
    assert_eq!(st.phase, Phase::Spawned, "a read never stalls the Task");
    assert_eq!(cond(&st, "NeedsAttention").reason, "BriefUnconfirmed");
    assert!(st.not_before.is_some(), "the next read waits");

    let (plan, st) = run(
        &obj,
        Observations {
            find: live,
            ready: Some(Observation::Known(true)),
            brief_delivered: Some(Observation::Known(false)),
            ..Default::default()
        },
    );
    assert_eq!(
        plan.effects.len(),
        1,
        "Known not delivered: send under the same key"
    );
    assert_eq!(plan.effects[0].key, "task/t/u7/brief");
    assert_eq!(st.brief_sends, 2);
}

#[test]
fn a_result_concludes_and_a_vanished_session_is_worker_gone() {
    let briefed = |extra: fn(&mut TaskStatus)| {
        spawned(|st| {
            st.phase = Phase::Briefed;
            extra(st);
        })
    };
    let (_, st) = run(
        &briefed(|st| {
            st.result = Some(
                serde_json::from_value(json!({"outcome": "shipped", "generation": 1})).unwrap(),
            );
        }),
        Observations::default(),
    );
    assert_eq!(st.phase, Phase::Concluded);

    let gone = Observations {
        find: Some(Observation::Known(None)),
        ..Default::default()
    };
    let (_, st) = run(&briefed(|_| {}), gone);
    assert_eq!(cond(&st, "WorkerGone").reason, "SessionGone");
    assert_eq!(cond(&st, "NeedsAttention").reason, "WorkerGone");
    assert_eq!(
        st.phase,
        Phase::Briefed,
        "Q13: never abandoned by the controller"
    );

    let unreadable = Observations {
        find: Some(Observation::unknown("ssh down")),
        ..Default::default()
    };
    let (_, st) = run(&briefed(|_| {}), unreadable);
    assert!(
        st.conditions.iter().all(|c| c.type_ != "WorkerGone"),
        "an unreadable list is never read as gone"
    );
}

fn deleting(st: TaskStatus) -> Object {
    let mut o = task(true, Some(st));
    o.metadata.deletion_timestamp = Some(NOW - 1);
    o
}

#[test]
fn release_runs_only_for_a_session_proven_at_rest_or_gone() {
    let obj = deleting(TaskStatus {
        phase: Phase::Concluded,
        session: Some(session()),
        ..Default::default()
    });
    let live = Some(Observation::Known(Some(session())));
    let (plan, st) = run(
        &obj,
        Observations {
            find: live.clone(),
            at_rest: Some(Observation::Known(false)),
            ..Default::default()
        },
    );
    assert!(plan.effects.is_empty());
    assert_eq!(st.phase, Phase::Releasing);
    let (plan, _) = run(
        &obj,
        Observations {
            find: live.clone(),
            at_rest: Some(Observation::unknown("?")),
            ..Default::default()
        },
    );
    assert!(plan.effects.is_empty());
    let (plan, _) = run(
        &obj,
        Observations {
            find: live,
            at_rest: Some(Observation::Known(true)),
            ..Default::default()
        },
    );
    assert!(matches!(plan.effects[0].action, Action::Release { .. }));
    let (plan, _) = run(
        &obj,
        Observations {
            find: Some(Observation::Known(None)),
            ..Default::default()
        },
    );
    assert_eq!(
        plan.effects.len(),
        1,
        "a gone session is released without proof of rest"
    );
}

#[test]
fn a_released_task_drops_its_finalizer_and_failed_releases_are_bounded() {
    let mut st = TaskStatus {
        phase: Phase::Releasing,
        session: Some(session()),
        release_attempts: 1,
        ..Default::default()
    };
    st.receipts.insert(
        "task/t/u7/release/1".into(),
        receipt("release", Observation::Known(EffectOutcome::Released)),
    );
    let (plan, _) = run(&deleting(st), Observations::default());
    assert_eq!(plan.remove_finalizer.as_deref(), Some("fleet.dev/release"));

    let mut st = TaskStatus {
        phase: Phase::Releasing,
        session: Some(session()),
        release_attempts: 3,
        ..Default::default()
    };
    st.receipts.insert(
        "task/t/u7/release/3".into(),
        receipt(
            "release",
            Observation::Known(EffectOutcome::Failed { why: "hook".into() }),
        ),
    );
    let (plan, st) = run(&deleting(st), Observations::default());
    assert!(plan.effects.is_empty() && plan.remove_finalizer.is_none());
    assert_eq!(cond(&st, "Released").reason, "ReleaseFailed");
    assert_eq!(cond(&st, "NeedsAttention").reason, "ReleaseFailed");
}

#[test]
fn a_task_that_never_spawned_releases_nothing() {
    let (plan, st) = run(&deleting(TaskStatus::default()), Observations::default());
    assert!(plan.effects.is_empty());
    assert_eq!(plan.remove_finalizer.as_deref(), Some("fleet.dev/release"));
    assert_eq!(cond(&st, "Released").reason, "NothingToRelease");
}

#[test]
fn reconciling_a_settled_task_writes_nothing() {
    let obs = Observations {
        host: Some(Observation::Known(true)),
        ..Default::default()
    };
    let (plan, st) = run(&task(true, None), obs.clone());
    let mut obj = task(true, None);
    obj.status = serde_json::to_value(&st).unwrap();
    obj.status["phase"] = json!("Concluded");
    let (again, _) = run(&obj, obs);
    assert!(plan.status.is_some());
    assert!(again.effects.is_empty());
}

#[test]
fn an_interrupted_release_is_retried_and_still_ends() {
    let intent = |at| fleet_api::plan::Intent {
        action: "release".into(),
        at,
        generation: 1,
    };
    let gone = || Observations {
        find: Some(Observation::Known(None)),
        ..Default::default()
    };
    let mut st = TaskStatus {
        phase: Phase::Releasing,
        session: Some(session()),
        release_attempts: 1,
        ..Default::default()
    };
    st.intents
        .insert("task/t/u7/release/1".into(), intent(NOW - RETRY_DELAY_MS));
    let (plan, _) = run(&deleting(st), gone());
    assert_eq!(
        plan.effects.len(),
        1,
        "an intent with no receipt is tried again"
    );

    let mut st = TaskStatus {
        phase: Phase::Releasing,
        session: Some(session()),
        release_attempts: MAX_ATTEMPTS,
        ..Default::default()
    };
    st.intents
        .insert(format!("task/t/u7/release/{MAX_ATTEMPTS}"), intent(NOW));
    let (plan, st) = run(&deleting(st), gone());
    assert!(plan.effects.is_empty());
    assert_eq!(
        cond(&st, "Released").reason,
        "ReleaseFailed",
        "the bound holds without a receipt too"
    );
}

#[test]
fn a_task_waiting_on_its_host_reaches_a_fixed_point() {
    // A pass that sees the same world as the last writes nothing, so the
    // manager goes idle instead of rewriting conditions every pass.
    let unreachable = || Observations {
        host: Some(Observation::Known(false)),
        ..Default::default()
    };
    let (first, st) = run_at(&task(true, None), unreachable(), NOW);
    assert_eq!(cond(&st, "HostReachable").reason, "Unreachable");
    assert!(first.effects.is_empty());
    let (again, _) = run_at(&task(true, Some(st)), unreachable(), NOW + 1_000);
    assert_eq!(again.status, None, "nothing changed, so nothing is written");
}

#[test]
fn a_session_seen_again_clears_worker_gone() {
    let gone = Observations {
        find: Some(Observation::Known(None)),
        ..Default::default()
    };
    let (_, st) = run(&spawned(|st| st.phase = Phase::Briefed), gone);
    assert_eq!(cond(&st, "WorkerGone").status, ConditionStatus::True);
    let back = Observations {
        find: Some(Observation::Known(Some(session()))),
        ..Default::default()
    };
    let (_, st) = run(&task(true, Some(st)), back);
    assert_eq!(cond(&st, "WorkerGone").status, ConditionStatus::False);
    assert_eq!(cond(&st, "NeedsAttention").status, ConditionStatus::False);
}
