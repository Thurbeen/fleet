//! Acceptance: a forge host's poll interval, its call budget shared by every
//! ChangeRequest on it, and its back-off when the forge asks to slow down,
//! through the real `fleet` binary, the file store and a forge. Each run is
//! its own process, so what the budget remembers between runs is what the
//! store holds.

mod support;

use std::fs;

use fleet_api::forge::{
    ChangeSnapshot, ChangeState, Checks, HeadSeen, Mergeable, ReviewDecision, Threads,
};
use fleet_forge::ForgeFault;
use serde_json::{Value, json};
use support::{
    Env, FORGE_HOST, GITLAB_HOST, assert_condition, change_request, derived_name, now_ms,
};

const PROJECT: &str = "acme/widgets";

fn url(id: u32) -> String {
    format!("https://forge.test/acme/widgets/pull/{id}")
}

fn open_change() -> ChangeSnapshot {
    ChangeSnapshot {
        state: ChangeState::Open,
        head: HeadSeen {
            id: "aaa111".into(),
            checks: Checks::Pending,
            failing: Vec::new(),
        },
        review_decision: ReviewDecision::None,
        mergeable: Mergeable {
            value: None,
            reason: None,
        },
        threads: Threads { unresolved: 0 },
        reviews: Vec::new(),
        notes: Vec::new(),
    }
}

/// Replace `from` with `to` in the config file, as an operator would edit it.
fn edit_config(env: &Env, from: &str, to: &str) {
    let cfg = fs::read_to_string(&env.config).expect("config");
    assert!(cfg.contains(from), "{from:?} not in {cfg}");
    fs::write(&env.config, cfg.replacen(from, to, 1)).expect("config");
}

const FAKE_HOST_ENTRY: &str = "{ kind = \"fake\", dir = \"forge\" }";

/// `n` open changes on the fake forge, each mirrored by a ChangeRequest.
fn watched(env: &Env, n: u32) -> Vec<String> {
    let forge = env.fake_forge();
    let mut names = Vec::new();
    let mut manifests = Vec::new();
    for id in 1..=n {
        forge
            .set_change(PROJECT, &id.to_string(), open_change())
            .unwrap();
        names.push(derived_name("fake", FORGE_HOST, PROJECT, &id.to_string()));
        manifests.push(change_request(&url(id), json!({})));
    }
    env.apply(Value::Array(manifests)).ok();
    names
}

/// Let the minute the budget counts pass, as time would: every call a
/// ChangeRequest recorded moves a minute into the past.
fn age_ledger(env: &Env, name: &str) {
    let path = env.object_path("ChangeRequest", name);
    let mut obj: Value = serde_json::from_slice(&fs::read(&path).expect("record")).expect("json");
    if let Some(calls) = obj["status"]
        .get_mut("forgeCalls")
        .and_then(Value::as_array_mut)
    {
        for c in calls {
            c["at"] = json!(c["at"].as_u64().unwrap().saturating_sub(60_000));
        }
    }
    fs::write(&path, obj.to_string()).expect("record");
}

fn observed(env: &Env, name: &str) -> bool {
    env.change_request_status(name)["observedAt"].is_u64()
}

#[test]
fn a_host_budget_bounds_the_calls_of_every_change_request_on_it() {
    let env = Env::forge("budget");
    // Nine calls a minute: a poll starts only while one observation's worst
    // case, six calls, still fits.
    edit_config(
        &env,
        FAKE_HOST_ENTRY,
        "{ kind = \"fake\", dir = \"forge\", maxCallsPerMinute = 9 }",
    );
    let forge = env.fake_forge();
    let names = watched(&env, 13);

    let report = env.reconcile().ok().json();
    assert_eq!(report["errors"], json!([]), "{report:#}");
    let first = forge.calls("change").unwrap();
    assert!(first <= 9, "{first} calls in one minute: {report:#}");
    assert_eq!(first, 4, "0, 1, 2 and 3 calls spent each left room for six");

    let skipped: Vec<&String> = names.iter().filter(|n| !observed(&env, n)).collect();
    assert_eq!(skipped.len(), 9);
    for name in &skipped {
        let st = env.change_request_status(name);
        assert_condition(&st, "Reachable", "Unknown", "OverBudget");
        assert!(
            st["notBefore"].as_u64().unwrap() > now_ms(),
            "{name} waits for the budget: {st:#}"
        );
    }

    // Still inside the same minute, in a new process: everything is due
    // again, and the store still knows what was spent.
    for name in &names {
        env.expire_wait_of("ChangeRequest", name);
    }
    let report = env.reconcile().ok().json();
    assert_eq!(forge.calls("change").unwrap(), first, "{report:#}");
    // A polled change kept its snapshot and says it is stale.
    let polled = names.iter().find(|n| observed(&env, n)).unwrap();
    let st = env.change_request_status(polled);
    assert_eq!(st["revision"], 1, "{st:#}");
    assert_condition(&st, "Reachable", "Unknown", "OverBudget");
    let message = st["conditions"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["type"] == "Reachable")
        .unwrap()["message"]
        .to_string();
    assert!(message.contains("stale"), "{message}");

    // Minute by minute, the oldest observed go first: the never-observed
    // nine before any observed one, so none starves.
    let mut rounds = 0;
    while names.iter().any(|n| !observed(&env, n)) {
        rounds += 1;
        assert!(rounds <= 3, "a change starved");
        let before = forge.calls("change").unwrap();
        let unobserved = names.iter().filter(|n| !observed(&env, n)).count();
        for name in &names {
            age_ledger(&env, name);
            env.expire_wait_of("ChangeRequest", name);
        }
        env.reconcile().ok();
        let spent = forge.calls("change").unwrap() - before;
        assert!(spent <= 9, "round {rounds}: {spent}");
        let now_unobserved = names.iter().filter(|n| !observed(&env, n)).count();
        assert_eq!(
            unobserved - now_unobserved,
            usize::try_from(spent).unwrap().min(unobserved),
            "round {rounds}: every call went to a never-observed change"
        );
    }
}

#[test]
fn a_host_poll_interval_applies_to_an_existing_change_request() {
    let env = Env::forge("host-poll");
    let names = watched(&env, 1);
    let cr = &names[0];
    let t = now_ms();
    env.reconcile().ok();
    let nb = env.change_request_status(cr)["notBefore"].as_u64().unwrap();
    assert!(nb >= t + 60_000 && nb < t + 120_000, "the 60 s default");

    // The [forge] default, then the host's own, each on the next poll of
    // the same object.
    edit_config(
        &env,
        "callTimeoutMs = 3000",
        "callTimeoutMs = 3000\npollMs = 120000",
    );
    env.expire_wait_of("ChangeRequest", cr);
    let t = now_ms();
    env.reconcile().ok();
    let nb = env.change_request_status(cr)["notBefore"].as_u64().unwrap();
    assert!(nb >= t + 120_000 && nb < t + 180_000, "[forge] pollMs");

    edit_config(
        &env,
        FAKE_HOST_ENTRY,
        "{ kind = \"fake\", dir = \"forge\", pollMs = 300000 }",
    );
    env.expire_wait_of("ChangeRequest", cr);
    let t = now_ms();
    env.reconcile().ok();
    let st = env.change_request_status(cr);
    let nb = st["notBefore"].as_u64().unwrap();
    assert!(nb >= t + 300_000 && nb < t + 360_000, "host pollMs: {st:#}");
    assert_eq!(
        env.change_request(cr)["metadata"]["generation"],
        1,
        "not recreated"
    );

    // The spec still wins over both.
    env.fake_forge()
        .set_change(PROJECT, "2", open_change())
        .unwrap();
    env.apply(change_request(&url(2), json!({"pollMs": 7000})))
        .ok();
    let t = now_ms();
    env.reconcile().ok();
    let nb =
        env.change_request_status(&derived_name("fake", FORGE_HOST, PROJECT, "2"))["notBefore"]
            .as_u64()
            .unwrap();
    assert!(nb >= t + 7000 && nb < t + 60_000, "spec pollMs");
}

#[test]
fn a_rate_limited_host_backs_off_as_a_whole_and_resumes() {
    let env = Env::forge("host-backoff");
    let forge = env.fake_forge();
    let names = watched(&env, 2);
    let (a, b) = (&names[0], &names[1]);
    env.reconcile().ok();
    assert_eq!(forge.calls("change").unwrap(), 2);

    // Only `a` is due when the forge answers 429 with Retry-After.
    forge
        .add_fault(ForgeFault::new("change", "throttle").retry_after(120_000))
        .unwrap();
    env.expire_wait_of("ChangeRequest", a);
    let t = now_ms();
    env.reconcile().ok();
    assert_eq!(forge.calls("change").unwrap(), 3);
    let st = env.change_request_status(a);
    assert_condition(&st, "Reachable", "Unknown", "Throttled");
    assert!(st["notBefore"].as_u64().unwrap() >= t + 120_000);

    // `b` comes due during the pause: it is not asked, and says why.
    env.expire_wait_of("ChangeRequest", b);
    let report = env.reconcile().ok().json();
    assert_eq!(forge.calls("change").unwrap(), 3, "{report:#}");
    let st = env.change_request_status(b);
    assert_condition(&st, "Reachable", "Unknown", "Throttled");
    assert_eq!(st["revision"], 1, "the snapshot stands");
    assert!(st["notBefore"].as_u64().unwrap() >= t + 120_000, "{st:#}");
    assert!(
        report["unknowns"]
            .to_string()
            .contains("deferred with the host"),
        "{report:#}"
    );

    // The pause is over: both are read again.
    for name in [a, b] {
        env.expire_wait_of("ChangeRequest", name);
    }
    env.reconcile().ok();
    assert_eq!(forge.calls("change").unwrap(), 5);
    for name in [a, b] {
        assert_condition(
            &env.change_request_status(name),
            "Reachable",
            "True",
            "Observed",
        );
    }

    // A 429 without Retry-After pauses the host one poll interval.
    forge
        .add_fault(ForgeFault::new("change", "throttle"))
        .unwrap();
    env.expire_wait_of("ChangeRequest", a);
    let t = now_ms();
    env.reconcile().ok();
    assert_eq!(forge.calls("change").unwrap(), 6);
    env.expire_wait_of("ChangeRequest", b);
    env.reconcile().ok();
    assert_eq!(forge.calls("change").unwrap(), 6, "b waits with the host");
    let st = env.change_request_status(b);
    assert_condition(&st, "Reachable", "Unknown", "Throttled");
    assert!(st["notBefore"].as_u64().unwrap() >= t + 60_000, "{st:#}");
}

fn gitlab_model() -> Value {
    json!({
        "state": "open", "head": "h1",
        "checks": [{"name": "ci", "state": "success"}],
        "reviewDecision": "none", "mergeable": "mergeable",
        "reviews": [], "comments": [], "threads": []
    })
}

#[test]
fn a_host_budget_counts_every_cli_call_a_poll_makes() {
    let env = Env::forge_stubs("budget-cli");
    edit_config(
        &env,
        "kind = \"gitlab\", ",
        "kind = \"gitlab\", maxCallsPerMinute = 12, ",
    );
    let project = "group/project";
    env.edit_stub(|s| {
        s["changes"] = json!({
            format!("{project}#1"): gitlab_model(),
            format!("{project}#2"): gitlab_model(),
            format!("{project}#3"): gitlab_model(),
            format!("{project}#4"): gitlab_model(),
            format!("{project}#5"): gitlab_model(),
        })
    });
    let manifests: Vec<Value> = (1..=5)
        .map(|id| {
            change_request(
                &format!("https://gitlab.test/group/project/-/merge_requests/{id}"),
                json!({}),
            )
        })
        .collect();
    env.apply(Value::Array(manifests)).ok();
    let report = env.reconcile().ok().json();
    assert_eq!(report["errors"], json!([]), "{report:#}");
    let calls = env.forge_calls(GITLAB_HOST).len();
    assert!(calls > 4, "one poll is several calls: {calls}");
    assert!(
        calls <= 12,
        "{calls} calls against a budget of 12: {report:#}"
    );
    let over: Vec<u32> = (1..=5)
        .filter(|id| {
            let st = env.change_request_status(&derived_name(
                "gitlab",
                GITLAB_HOST,
                project,
                &id.to_string(),
            ));
            st["conditions"].to_string().contains("OverBudget")
        })
        .collect();
    assert!(!over.is_empty(), "some poll waited for the budget");
}

#[test]
fn a_budget_smaller_than_one_observation_is_refused_at_load() {
    let env = Env::forge("budget-too-small");
    edit_config(
        &env,
        FAKE_HOST_ENTRY,
        "{ kind = \"fake\", dir = \"forge\", maxCallsPerMinute = 5 }",
    );
    env.reconcile().code(1).stderr_has("maxCallsPerMinute is 5");
}
