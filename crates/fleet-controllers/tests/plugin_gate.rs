use fleet_api::Object;
use fleet_api::plugin::Registration;
use fleet_controllers::plugin::admit;
use serde_json::{Value, json};
use std::collections::BTreeMap;

fn registration() -> Registration {
    serde_json::from_value(json!({"manifest":{"protocol":1,"name":"issues","kinds":[{"kind":"Issue","pollMs":1000,"spec":true,"finalizer":true}]},"config":{"program":"fixture","cwd":"fixture"}})).unwrap()
}
fn object(kind: &str, name: &str, uid: &str) -> Object {
    serde_json::from_value(json!({"apiVersion":"fleet/v1alpha1","kind":kind,"metadata":{"name":name,"uid":uid},"spec":{}})).unwrap()
}
fn accepts(plan: Value) -> bool {
    admit(
        plan,
        &registration(),
        &object("Issue", "one", "uid"),
        &BTreeMap::new(),
    )
    .is_ok()
}

#[test]
fn rejects_unknown_verbs_reserved_fields_and_invalid_bounds() {
    for plan in [
        json!({"result":{}}),
        json!({"status":{"effects":{}}}),
        json!({"status":[]}),
        json!({"removeFinalizer":true}),
        json!({"effects":[{"key":"x","request":{}},{"key":"x","request":{}}]}),
        json!({"effects":(0..9).map(|n|json!({"key":n.to_string(),"request":{}})).collect::<Vec<_>>()}),
        json!({"creates":[object("ChangeRequest","forbidden","")]}),
        json!({"creates":[object("Issue","identity","already-assigned")]}),
        json!({"status":{"large":"x".repeat(256*1024)}}),
    ] {
        assert!(!accepts(plan.clone()), "{plan}");
    }
    assert!(accepts(json!({"status":{"phase":"Ready"}})));
}

#[test]
fn task_targets_need_a_known_uid_and_rights_and_only_mutable_spec_fields() {
    let mut task = object("Task", "child", "uid");
    let mut targets = BTreeMap::from([("Task/child".into(), task.clone())]);
    let patch =
        json!({"patches":[{"kind":"Task","name":"child","uid":"uid","spec":{"dispatch":true}}]});
    assert!(
        admit(
            patch.clone(),
            &registration(),
            &object("Issue", "one", "uid"),
            &targets
        )
        .is_err()
    );
    task.metadata
        .labels
        .insert("fleet.dev/plugin".into(), "issues".into());
    targets.insert("Task/child".into(), task);
    assert!(
        admit(
            patch.clone(),
            &registration(),
            &object("Issue", "one", "uid"),
            &targets
        )
        .is_ok()
    );
    let mut stale = patch.clone();
    stale["patches"][0]["uid"] = json!("old");
    assert!(
        admit(
            stale,
            &registration(),
            &object("Issue", "one", "uid"),
            &targets
        )
        .is_err()
    );
    let mut immutable = patch;
    immutable["patches"][0]["spec"] = json!({"brief":"overwrite"});
    assert!(
        admit(
            immutable,
            &registration(),
            &object("Issue", "one", "uid"),
            &targets
        )
        .is_err()
    );
}
