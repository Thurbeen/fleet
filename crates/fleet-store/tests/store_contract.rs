//! The store contract against the file store: exclusive create,
//! compare-and-swap without lost updates, torn and crashed writes, field
//! ownership, the manager lease and finalizers.

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};
use std::thread;

use fleet_api::task::TaskStatus;
use fleet_api::{
    Clock, DeleteOutcome, FieldManager, Object, Observation, OwnerReference, Store, StoreError,
};
use fleet_store::FileStore;
use serde_json::{Value, json};

static SEQ: AtomicU32 = AtomicU32::new(0);

struct Dir(PathBuf);

impl Dir {
    fn new(name: &str) -> Dir {
        let n = SEQ.fetch_add(1, Ordering::Relaxed);
        let p = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
            .join("store")
            .join(format!("{name}-{}-{n}", std::process::id()));
        let _ = fs::remove_dir_all(&p);
        Dir(p)
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        if !thread::panicking() {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
}

#[derive(Debug, Default)]
struct TestClock(AtomicU64);

impl Clock for TestClock {
    fn now(&self) -> u64 {
        self.0.load(Ordering::SeqCst)
    }
}

impl TestClock {
    fn advance(&self, ms: u64) {
        self.0.fetch_add(ms, Ordering::SeqCst);
    }
}

fn task(name: &str) -> Object {
    serde_json::from_value(json!({
        "apiVersion": "fleet/v1alpha1",
        "kind": "Task",
        "metadata": {"name": name},
        "spec": {"harness": "h", "brief": "b", "workspace": {"dir": "d"}}
    }))
    .unwrap()
}

fn known(o: Observation<Option<Object>>) -> Option<Object> {
    match o {
        Observation::Known(v) => v,
        Observation::Unknown { why } => panic!("unexpected Unknown: {why}"),
    }
}

fn status(phase: &str) -> Value {
    json!({"phase": phase, "observedGeneration": 1})
}

#[test]
fn create_is_exclusive_under_a_race() {
    let dir = Dir::new("create-race");
    let store = FileStore::open(&dir.0).unwrap();
    drop(store);
    let wins = Arc::new(AtomicU32::new(0));
    let handles: Vec<_> = (0..8)
        .map(|_| {
            let (root, wins) = (dir.0.clone(), wins.clone());
            thread::spawn(move || {
                let store = FileStore::open(&root).unwrap();
                match store.create(task("same"), FieldManager::Apply) {
                    Ok(_) => {
                        wins.fetch_add(1, Ordering::SeqCst);
                    }
                    Err(StoreError::AlreadyExists(_)) => {}
                    Err(e) => panic!("{e}"),
                }
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }
    assert_eq!(wins.load(Ordering::SeqCst), 1);
    assert!(
        fs::read_dir(dir.0.join("tmp")).unwrap().next().is_none(),
        "no temp file left"
    );
}

#[test]
fn compare_and_swap_loses_no_update() {
    let dir = Dir::new("cas");
    let store = FileStore::open(&dir.0).unwrap();
    store.create(task("counter"), FieldManager::Apply).unwrap();
    let threads = 6;
    let per = 15;
    let handles: Vec<_> = (0..threads)
        .map(|_| {
            let root = dir.0.clone();
            thread::spawn(move || {
                let store = FileStore::open(&root).unwrap();
                let mut conflicts = 0;
                for _ in 0..per {
                    loop {
                        let cur = known(store.get("Task", "counter")).unwrap();
                        let n: u64 = cur
                            .metadata
                            .labels
                            .get("n")
                            .map_or(0, |v| v.parse().unwrap());
                        let labels = BTreeMap::from([("n".to_string(), (n + 1).to_string())]);
                        let rv = cur.metadata.resource_version;
                        match store.update_spec(
                            "Task",
                            "counter",
                            cur.spec.clone(),
                            Some(labels),
                            rv,
                            FieldManager::Apply,
                        ) {
                            Ok(_) => break,
                            Err(StoreError::Conflict { .. }) => conflicts += 1,
                            Err(e) => panic!("{e}"),
                        }
                    }
                }
                conflicts
            })
        })
        .collect();
    let conflicts: u32 = handles.into_iter().map(|h| h.join().unwrap()).sum();
    let end = known(store.get("Task", "counter")).unwrap();
    assert_eq!(
        end.metadata.labels["n"],
        (threads * per).to_string(),
        "every increment landed"
    );
    assert_eq!(end.metadata.generation, 1, "labels do not bump generation");
    // Not asserted to be non-zero: the race is real but not guaranteed.
    let _ = conflicts;
}

#[test]
fn a_stale_write_is_a_conflict() {
    let dir = Dir::new("stale");
    let store = FileStore::open(&dir.0).unwrap();
    let a = store.create(task("t"), FieldManager::Apply).unwrap();
    let mut spec = a.spec.clone();
    spec["dispatch"] = json!(true);
    let b = store
        .update_spec(
            "Task",
            "t",
            spec.clone(),
            None,
            a.metadata.resource_version,
            FieldManager::Apply,
        )
        .unwrap();
    assert_eq!(b.metadata.generation, 2);
    let err = store
        .update_spec(
            "Task",
            "t",
            spec,
            None,
            a.metadata.resource_version,
            FieldManager::Apply,
        )
        .unwrap_err();
    assert!(
        matches!(err, StoreError::Conflict { expected, actual, .. } if expected == a.metadata.resource_version && actual == b.metadata.resource_version)
    );
}

#[test]
fn a_crashed_write_leaves_the_old_record_and_no_temp() {
    let dir = Dir::new("torn");
    let store = FileStore::open(&dir.0).unwrap();
    let a = store.create(task("t"), FieldManager::Apply).unwrap();
    drop(store);
    // A writer that died after writing its temp file and before the rename.
    fs::write(dir.0.join("tmp").join("999-0.tmp"), b"{\"half\": ").unwrap();
    let store = FileStore::open(&dir.0).unwrap();
    assert!(
        fs::read_dir(dir.0.join("tmp")).unwrap().next().is_none(),
        "open removes a dead writer's temp file"
    );
    assert_eq!(
        known(store.get("Task", "t")).unwrap(),
        a,
        "the record is the last complete one"
    );
}

#[test]
fn a_torn_record_is_unknown_and_blocks_writes() {
    let dir = Dir::new("corrupt");
    let store = FileStore::open(&dir.0).unwrap();
    store.create(task("t"), FieldManager::Apply).unwrap();
    let path = dir.0.join("objects").join("Task").join("t.json");
    let bytes = fs::read(&path).unwrap();
    fs::write(&path, &bytes[..bytes.len() / 2]).unwrap();
    assert!(store.get("Task", "t").is_unknown());
    assert!(store.list("Task").is_unknown(), "never a partial list");
    assert!(
        matches!(
            store.create(task("t"), FieldManager::Apply),
            Err(StoreError::Unknown(_))
        ),
        "a create cannot tell whether the name is free"
    );
    assert!(matches!(
        store.delete("Task", "t", FieldManager::Apply),
        Err(StoreError::Unknown(_))
    ));
    assert_eq!(
        known(store.get("Task", "absent")),
        None,
        "absence is still Known"
    );
}

#[test]
fn a_record_holding_another_name_is_corrupt() {
    let dir = Dir::new("misfiled");
    let store = FileStore::open(&dir.0).unwrap();
    store.create(task("a"), FieldManager::Apply).unwrap();
    let objects = dir.0.join("objects").join("Task");
    fs::copy(objects.join("a.json"), objects.join("b.json")).unwrap();
    assert!(store.get("Task", "b").is_unknown());
}

#[test]
fn each_field_manager_writes_only_its_own_status_fields() {
    let dir = Dir::new("owners");
    let clock = Arc::new(TestClock::default());
    let store = FileStore::open_with_clock(&dir.0, clock.clone()).unwrap();
    let t = store.create(task("t"), FieldManager::Apply).unwrap();
    let lease = store.acquire_lease("m", 10_000).unwrap();

    let err = store
        .update_status(
            "Task",
            "t",
            status("Briefed"),
            t.metadata.resource_version,
            FieldManager::Worker,
            None,
        )
        .unwrap_err();
    assert!(
        matches!(err, StoreError::Forbidden(ref m) if m.contains("worker may not write status.")),
        "{err}"
    );
    let err = store
        .update_status(
            "Task",
            "t",
            status("Briefed"),
            t.metadata.resource_version,
            FieldManager::Apply,
            None,
        )
        .unwrap_err();
    assert!(matches!(err, StoreError::Forbidden(_)), "{err}");

    let t = store
        .update_status(
            "Task",
            "t",
            status("Briefed"),
            t.metadata.resource_version,
            FieldManager::TaskController,
            Some(&lease),
        )
        .unwrap();
    let mut with_result = t.status.clone();
    with_result["result"] = json!({"outcome": "shipped", "generation": 1});
    let err = store
        .update_status(
            "Task",
            "t",
            with_result.clone(),
            t.metadata.resource_version,
            FieldManager::TaskController,
            Some(&lease),
        )
        .unwrap_err();
    assert!(
        matches!(err, StoreError::Forbidden(ref m) if m.contains("status.result")),
        "{err}"
    );
    let t = store
        .update_status(
            "Task",
            "t",
            with_result,
            t.metadata.resource_version,
            FieldManager::Worker,
            None,
        )
        .unwrap();
    let st: TaskStatus = serde_json::from_value(t.status).unwrap();
    assert!(st.result.is_some());

    let err = store.update_status(
        "Task",
        "t",
        json!({"phase": "NotAPhase"}),
        t.metadata.resource_version,
        FieldManager::TaskController,
        Some(&lease),
    );
    assert!(
        matches!(err, Err(StoreError::Invalid(_))),
        "status must parse as the kind's status"
    );
    let cfg: Object =
        serde_json::from_value(json!({"apiVersion": "fleet/v1alpha1", "kind": "TaskConfig",
        "metadata": {"name": "worker"}, "spec": {"harness": "h", "hostRef": "local"}}))
        .unwrap();
    assert!(
        matches!(
            store.create(cfg, FieldManager::Apply),
            Err(StoreError::Invalid(_))
        ),
        "unsupported kinds are refused"
    );
    let err = store.update_status(
        "TaskConfig",
        "worker",
        json!({}),
        1,
        FieldManager::TaskController,
        Some(&lease),
    );
    assert!(
        matches!(err, Err(StoreError::Forbidden(_))),
        "no other kind has a status"
    );
}

#[test]
fn a_controller_writes_only_under_the_current_lease() {
    let dir = Dir::new("lease");
    let clock = Arc::new(TestClock::default());
    let store = FileStore::open_with_clock(&dir.0, clock.clone()).unwrap();
    let t = store.create(task("t"), FieldManager::Apply).unwrap();
    let rv = t.metadata.resource_version;

    let err = store
        .update_status(
            "Task",
            "t",
            status("Spawning"),
            rv,
            FieldManager::TaskController,
            None,
        )
        .unwrap_err();
    assert!(matches!(err, StoreError::LeaseLost(_)));

    let a = store.acquire_lease("a", 1_000).unwrap();
    assert!(
        matches!(store.acquire_lease("b", 1_000), Err(StoreError::LeaseHeld { ref holder, .. }) if holder == "a")
    );
    assert!(
        matches!(
            store.acquire_lease("a", 1_000),
            Err(StoreError::LeaseHeld { .. })
        ),
        "not even under the same holder name"
    );
    let a = store.renew_lease(&a, 1_000).unwrap();

    clock.advance(1_001);
    let err = store
        .update_status(
            "Task",
            "t",
            status("Spawning"),
            rv,
            FieldManager::TaskController,
            Some(&a),
        )
        .unwrap_err();
    assert!(
        matches!(err, StoreError::LeaseLost(_)),
        "an expired lease fences its holder"
    );
    let b = store.acquire_lease("b", 1_000).unwrap();
    assert!(
        b.token > a.token,
        "the fencing token grows when the lease changes hands"
    );
    assert!(
        matches!(store.renew_lease(&a, 1_000), Err(StoreError::LeaseLost(_))),
        "a lost lease cannot be renewed back"
    );
    store
        .update_status(
            "Task",
            "t",
            status("Spawning"),
            rv,
            FieldManager::TaskController,
            Some(&b),
        )
        .unwrap();

    store.release_lease(&b).unwrap();
    let c = store.acquire_lease("c", 1_000).unwrap();
    assert!(c.token > b.token, "release keeps the token monotonic");
    assert!(matches!(
        store.update_status(
            "Task",
            "t",
            status("Spawned"),
            rv + 1,
            FieldManager::TaskController,
            Some(&b)
        ),
        Err(StoreError::LeaseLost(_))
    ));
}

#[test]
fn finalizers_hold_deletion_and_only_the_controller_removes_them() {
    let dir = Dir::new("finalizers");
    let store = FileStore::open(&dir.0).unwrap();
    let t = store.create(task("t"), FieldManager::Apply).unwrap();
    assert_eq!(t.metadata.finalizers, vec!["fleet.dev/release".to_string()]);
    assert!(matches!(
        store.delete("Task", "t", FieldManager::TaskController),
        Err(StoreError::Forbidden(_))
    ));
    assert_eq!(
        store.delete("Task", "t", FieldManager::Apply).unwrap(),
        DeleteOutcome::Marked
    );
    let t = known(store.get("Task", "t")).expect("still there");
    assert!(t.is_deleting());
    let mut spec = t.spec.clone();
    spec["dispatch"] = json!(true);
    assert!(matches!(
        store.update_spec(
            "Task",
            "t",
            spec,
            None,
            t.metadata.resource_version,
            FieldManager::Apply
        ),
        Err(StoreError::Forbidden(_))
    ));

    let lease = store.acquire_lease("m", 10_000).unwrap();
    let rv = t.metadata.resource_version;
    assert!(matches!(
        store.remove_finalizer(
            "Task",
            "t",
            "fleet.dev/release",
            rv,
            FieldManager::Worker,
            &lease
        ),
        Err(StoreError::Forbidden(_))
    ));
    assert!(matches!(
        store.remove_finalizer(
            "Task",
            "t",
            "fleet.dev/release",
            rv - 1,
            FieldManager::TaskController,
            &lease
        ),
        Err(StoreError::Conflict { .. })
    ));
    assert_eq!(
        store
            .remove_finalizer(
                "Task",
                "t",
                "fleet.dev/release",
                rv,
                FieldManager::TaskController,
                &lease
            )
            .unwrap(),
        DeleteOutcome::Removed
    );
    assert_eq!(known(store.get("Task", "t")), None);
}

#[test]
fn nothing_owns_a_task() {
    let dir = Dir::new("no-owners");
    let store = FileStore::open(&dir.0).unwrap();
    let parent = store.create(task("parent"), FieldManager::Apply).unwrap();
    let mut child = task("child");
    child.metadata.owner_references = vec![OwnerReference {
        kind: "Task".into(),
        name: "parent".into(),
        uid: parent.metadata.uid.clone(),
        controller: true,
    }];
    assert!(
        matches!(
            store.create(child, FieldManager::Apply),
            Err(StoreError::Invalid(_))
        ),
        "an owner reference is refused"
    );
    assert_eq!(known(store.get("Task", "child")), None);
}
#[test]
fn metadata_the_store_assigns_cannot_be_supplied() {
    let dir = Dir::new("assigned");
    let store = FileStore::open(&dir.0).unwrap();
    let mut t = task("t");
    t.metadata.uid = "u1".into();
    assert!(matches!(
        store.create(t, FieldManager::Apply),
        Err(StoreError::Invalid(_))
    ));
    let mut t = task("t");
    t.status = json!({"phase": "Concluded"});
    assert!(matches!(
        store.create(t, FieldManager::Apply),
        Err(StoreError::Invalid(_))
    ));
    let mut t = task("../escape");
    t.metadata.name = "../escape".into();
    assert!(matches!(
        store.create(t, FieldManager::Apply),
        Err(StoreError::Invalid(_))
    ));
    assert!(
        store.get("Task", "../escape").is_unknown(),
        "an invalid name is refused, not looked up"
    );
}

#[test]
fn a_foreign_or_newer_directory_is_refused() {
    let dir = Dir::new("foreign");
    fs::create_dir_all(&dir.0).unwrap();
    fs::write(dir.0.join("notes.txt"), b"x").unwrap();
    assert!(matches!(
        FileStore::open(&dir.0),
        Err(StoreError::Invalid(_))
    ));
    let dir = Dir::new("newer");
    fs::create_dir_all(&dir.0).unwrap();
    fs::write(
        dir.0.join("store.json"),
        br#"{"format": "fleet-store", "version": 2}"#,
    )
    .unwrap();
    assert!(matches!(
        FileStore::open(&dir.0),
        Err(StoreError::Unsupported(_))
    ));
}

#[cfg(unix)]
#[test]
fn not_found_and_inaccessible_are_different_answers() {
    use std::os::unix::fs::PermissionsExt;
    let dir = Dir::new("perm");
    let store = FileStore::open(&dir.0).unwrap();
    store.create(task("t"), FieldManager::Apply).unwrap();
    let kind_dir = dir.0.join("objects").join("Task");
    fs::set_permissions(&kind_dir, fs::Permissions::from_mode(0o000)).unwrap();
    let readable = fs::read_dir(&kind_dir).is_ok();
    let (get, list) = (store.get("Task", "t"), store.list("Task"));
    fs::set_permissions(&kind_dir, fs::Permissions::from_mode(0o755)).unwrap();
    if readable {
        return; // running as root: permissions do not apply
    }
    assert!(get.is_unknown(), "{get:?}");
    assert!(list.is_unknown(), "{list:?}");
}

#[test]
fn a_finalizer_never_comes_off_a_live_object() {
    let dir = Dir::new("live-finalizer");
    let store = FileStore::open(&dir.0).unwrap();
    let t = store.create(task("t"), FieldManager::Apply).unwrap();
    let lease = store.acquire_lease("m", 10_000).unwrap();
    let err = store
        .remove_finalizer(
            "Task",
            "t",
            "fleet.dev/release",
            t.metadata.resource_version,
            FieldManager::TaskController,
            &lease,
        )
        .unwrap_err();
    assert!(matches!(err, StoreError::Forbidden(_)), "{err}");
    // So a later delete still waits for the release.
    assert_eq!(
        store.delete("Task", "t", FieldManager::Apply).unwrap(),
        DeleteOutcome::Marked
    );
}

#[test]
fn a_stale_token_is_refused_even_under_the_same_holder_name() {
    let dir = Dir::new("same-holder");
    let clock = Arc::new(TestClock::default());
    let store = FileStore::open_with_clock(&dir.0, clock.clone()).unwrap();
    let t = store.create(task("t"), FieldManager::Apply).unwrap();
    let old = store.acquire_lease("m", 1_000).unwrap();
    clock.advance(1_001);
    let new = store.acquire_lease("m", 1_000).unwrap();
    assert_eq!(old.holder, new.holder);
    assert!(new.token > old.token);
    let rv = t.metadata.resource_version;
    let err = store
        .update_status(
            "Task",
            "t",
            status("Spawning"),
            rv,
            FieldManager::TaskController,
            Some(&old),
        )
        .unwrap_err();
    assert!(
        matches!(err, StoreError::LeaseLost(_)),
        "the token, not the name, fences: {err}"
    );
    assert!(matches!(
        store.renew_lease(&old, 1_000),
        Err(StoreError::LeaseLost(_))
    ));
    store
        .update_status(
            "Task",
            "t",
            status("Spawning"),
            rv,
            FieldManager::TaskController,
            Some(&new),
        )
        .unwrap();
}

#[test]
fn concurrent_creates_of_distinct_objects_get_distinct_uids_and_versions() {
    let dir = Dir::new("distinct");
    drop(FileStore::open(&dir.0).unwrap());
    let handles: Vec<_> = (0..8)
        .map(|i| {
            let root = dir.0.clone();
            thread::spawn(move || {
                let store = FileStore::open(&root).unwrap();
                (0..5)
                    .map(|j| {
                        store
                            .create(task(&format!("t{i}-{j}")), FieldManager::Apply)
                            .unwrap()
                    })
                    .collect::<Vec<_>>()
            })
        })
        .collect();
    let all: Vec<Object> = handles
        .into_iter()
        .flat_map(|h| h.join().unwrap())
        .collect();
    let uids: std::collections::BTreeSet<_> = all.iter().map(|o| o.metadata.uid.clone()).collect();
    let rvs: std::collections::BTreeSet<_> =
        all.iter().map(|o| o.metadata.resource_version).collect();
    assert_eq!(
        (uids.len(), rvs.len()),
        (40, 40),
        "every uid and resourceVersion is unique"
    );
    match FileStore::open(&dir.0).unwrap().list("Task") {
        Observation::Known(objs) => assert_eq!(objs.len(), 40),
        Observation::Unknown { why } => panic!("{why}"),
    }
}

#[test]
fn a_writer_waits_out_a_slow_lock_holder_instead_of_failing_busy() {
    // Under machine load a holder's fsyncs can keep the store lock longer
    // than any fixed polling bound; a waiter must still get through rather
    // than fail Busy, because every holder is making progress and the OS
    // frees the lock if one dies.
    let dir = Dir::new("slow-holder");
    let store = FileStore::open(&dir.0).unwrap();
    let lock = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(dir.0.join("lock"))
        .unwrap();
    lock.lock().unwrap();
    let root = dir.0.clone();
    let waiter = thread::spawn(move || {
        FileStore::open(&root).and_then(|s| s.create(task("late"), FieldManager::Apply))
    });
    thread::sleep(std::time::Duration::from_secs(11));
    lock.unlock().unwrap();
    let created = waiter.join().unwrap();
    assert!(
        created.is_ok(),
        "the waiter must get the lock once it is free: {created:?}"
    );
    assert!(known(store.get("Task", "late")).is_some());
}
