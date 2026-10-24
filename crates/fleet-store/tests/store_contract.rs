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

fn change_request(name: &str) -> Object {
    serde_json::from_value(json!({
        "apiVersion": "fleet/v1alpha1",
        "kind": "ChangeRequest",
        "metadata": {"name": name},
        "spec": {"url": "https://forge.test/acme/widgets/pull/7"}
    }))
    .unwrap()
}

/// A controller retires its own object by the uid and resourceVersion it
/// decided from, under the lease: a recreated object under the same name, a
/// rewritten one, a stale lease and another field manager all remove nothing.
#[test]
fn a_controllers_delete_is_fenced_by_uid_version_and_lease() {
    let dir = Dir::new("delete-fenced");
    let store = FileStore::open(&dir.0).unwrap();
    let cr = FieldManager::ChangeRequestController;
    let created = store
        .create(change_request("cr-1"), FieldManager::Apply)
        .unwrap();
    let (uid, rv) = (
        created.metadata.uid.clone(),
        created.metadata.resource_version,
    );
    let lease = store.acquire_lease("m", 60_000).unwrap();

    assert!(matches!(
        store.delete_fenced(
            "ChangeRequest",
            "cr-1",
            &uid,
            rv,
            FieldManager::Apply,
            &lease
        ),
        Err(StoreError::Forbidden(_))
    ));
    assert!(matches!(
        store.delete_fenced("ChangeRequest", "cr-1", &uid, rv + 1, cr, &lease),
        Err(StoreError::Conflict { .. })
    ));
    // Recreated under the same name: a new uid the old plan did not name.
    store
        .delete("ChangeRequest", "cr-1", FieldManager::Apply)
        .unwrap();
    let again = store
        .create(change_request("cr-1"), FieldManager::Apply)
        .unwrap();
    assert_ne!(again.metadata.uid, uid);
    assert!(matches!(
        store.delete_fenced(
            "ChangeRequest",
            "cr-1",
            &uid,
            again.metadata.resource_version,
            cr,
            &lease
        ),
        Err(StoreError::Conflict { .. })
    ));
    assert!(
        known(store.get("ChangeRequest", "cr-1")).is_some(),
        "still there"
    );
    // A stale lease is refused even with the right identity.
    store.release_lease(&lease).unwrap();
    let (uid2, rv2) = (again.metadata.uid.clone(), again.metadata.resource_version);
    assert!(matches!(
        store.delete_fenced("ChangeRequest", "cr-1", &uid2, rv2, cr, &lease),
        Err(StoreError::LeaseLost(_))
    ));
    let lease = store.acquire_lease("m", 60_000).unwrap();
    assert_eq!(
        store
            .delete_fenced("ChangeRequest", "cr-1", &uid2, rv2, cr, &lease)
            .unwrap(),
        DeleteOutcome::Removed,
        "no finalizer: the record goes at once"
    );
    assert!(known(store.get("ChangeRequest", "cr-1")).is_none());
}

#[test]
fn change_request_status_belongs_to_its_controller_alone() {
    let dir = Dir::new("cr-status-owner");
    let store = FileStore::open(&dir.0).unwrap();
    let created = store
        .create(change_request("cr-2"), FieldManager::Apply)
        .unwrap();
    let rv = created.metadata.resource_version;
    let lease = store.acquire_lease("m", 60_000).unwrap();
    let status = json!({"phase": "Watching", "revision": 1, "everReady": true, "droppedBefore": 0});
    for by in [
        FieldManager::Apply,
        FieldManager::Worker,
        FieldManager::TaskController,
    ] {
        let err = store
            .update_status(
                "ChangeRequest",
                "cr-2",
                status.clone(),
                rv,
                by,
                Some(&lease),
            )
            .unwrap_err();
        assert!(
            matches!(err, StoreError::Forbidden(_) | StoreError::LeaseLost(_)),
            "{by:?}: {err}"
        );
    }
    assert!(
        matches!(
            store.update_status(
                "ChangeRequest",
                "cr-2",
                json!({"phase": "Elsewhere"}),
                rv,
                FieldManager::ChangeRequestController,
                Some(&lease)
            ),
            Err(StoreError::Invalid(_))
        ),
        "a status its readers would refuse is not stored"
    );
    store
        .update_status(
            "ChangeRequest",
            "cr-2",
            status,
            rv,
            FieldManager::ChangeRequestController,
            Some(&lease),
        )
        .unwrap();
    // The spec is fixed once created.
    let mut other = change_request("cr-2").spec;
    other["url"] = json!("https://forge.test/acme/widgets/pull/8");
    let current = known(store.get("ChangeRequest", "cr-2")).unwrap();
    assert!(matches!(
        store.update_spec(
            "ChangeRequest",
            "cr-2",
            other,
            None,
            current.metadata.resource_version,
            FieldManager::Apply
        ),
        Err(StoreError::Invalid(_))
    ));
}
// --- The inbox: two typed mutations on a field nobody owns. ---------------

use fleet_api::inbox::{DeliveryReceipt, InboxAbout, InboxEntry, MAX_DELIVERIES, MAX_INBOX};

fn entry(key: &str) -> InboxEntry {
    InboxEntry {
        key: key.into(),
        from: "cli/tester".into(),
        text: format!("message {key}"),
        about: None,
        queued_at: 1,
    }
}

fn receipt(key: &str) -> DeliveryReceipt {
    DeliveryReceipt {
        key: key.into(),
        from: String::new(),
        at: 2,
        recovered: false,
    }
}

fn inbox_status(obj: &Object) -> TaskStatus {
    serde_json::from_value(obj.status.clone()).unwrap_or_default()
}

#[test]
fn an_inbox_key_is_refused_while_queued_or_receipted() {
    let dir = Dir::new("inbox-dup");
    let store = FileStore::open(&dir.0).unwrap();
    let t = store.create(task("t"), FieldManager::Apply).unwrap();
    let lease = store.acquire_lease("m", 10_000).unwrap();
    let t = store
        .append_inbox(
            "t",
            entry("k1"),
            t.metadata.resource_version,
            FieldManager::Cli,
            None,
        )
        .unwrap();
    let err = store
        .append_inbox(
            "t",
            entry("k1"),
            t.metadata.resource_version,
            FieldManager::Cli,
            None,
        )
        .unwrap_err();
    assert!(matches!(err, StoreError::AlreadyExists(_)), "{err}");
    assert_eq!(inbox_status(&t).inbox.len(), 1, "nothing was written");

    // Delivered: the receipt in, the entry out, one write.
    let rv = t.metadata.resource_version;
    let t = store
        .record_delivery(
            "t",
            t.status.clone(),
            receipt("k1"),
            rv,
            FieldManager::TaskController,
            &lease,
        )
        .unwrap();
    assert_eq!(t.metadata.resource_version, rv + 1, "one write");
    let st = inbox_status(&t);
    assert!(st.inbox.is_empty());
    assert_eq!(st.deliveries.receipts.len(), 1);
    assert_eq!(st.deliveries.receipts[0].key, "k1");
    assert_eq!(st.deliveries.receipts[0].from, "cli/tester");
    let err = store
        .append_inbox(
            "t",
            entry("k1"),
            t.metadata.resource_version,
            FieldManager::Cli,
            None,
        )
        .unwrap_err();
    assert!(
        matches!(err, StoreError::AlreadyExists(_)),
        "a receipted key is refused too: {err}"
    );
    // Recording a delivery nobody queued is refused, not invented.
    assert!(matches!(
        store.record_delivery(
            "t",
            t.status.clone(),
            receipt("k9"),
            t.metadata.resource_version,
            FieldManager::TaskController,
            &lease,
        ),
        Err(StoreError::NotFound(_))
    ));
}

#[test]
fn the_inbox_bound_and_the_delivered_revision_hold_on_append() {
    let dir = Dir::new("inbox-bound");
    let store = FileStore::open(&dir.0).unwrap();
    let mut t = store.create(task("t"), FieldManager::Apply).unwrap();
    for i in 0..=MAX_INBOX {
        t = store
            .append_inbox(
                "t",
                entry(&format!("k{i}")),
                t.metadata.resource_version,
                FieldManager::Cli,
                None,
            )
            .unwrap_or_else(|e| panic!("append {i}: {e}"));
    }
    assert_eq!(inbox_status(&t).inbox.len(), MAX_INBOX + 1);
    let err = store
        .append_inbox(
            "t",
            entry("one-more"),
            t.metadata.resource_version,
            FieldManager::Cli,
            None,
        )
        .unwrap_err();
    assert!(
        matches!(err, StoreError::Forbidden(ref m) if m.contains("undelivered")),
        "{err}"
    );
    assert_eq!(inbox_status(&t).inbox.len(), MAX_INBOX + 1);

    // An entry's revision survives its receipt's rotation: the store keeps
    // the highest delivered revision per source. (A relay's source is
    // `forge/<change>`; until that caller exists, the rule is exercised on
    // a `cli/` source carrying `about`.)
    let dir2 = Dir::new("inbox-revision");
    let store2 = FileStore::open(&dir2.0).unwrap();
    let mut r = store2.create(task("r"), FieldManager::Apply).unwrap();
    let relay = |key: &str, revision: u64| InboxEntry {
        from: "cli/relay".into(),
        about: Some(InboxAbout {
            uid: "c1".into(),
            revision,
            head: Some("abc".into()),
        }),
        ..entry(key)
    };
    let lease2 = store2.acquire_lease("m", 10_000).unwrap();
    r = store2
        .append_inbox(
            "r",
            relay("relay/7", 7),
            r.metadata.resource_version,
            FieldManager::Cli,
            None,
        )
        .unwrap();
    r = store2
        .record_delivery(
            "r",
            r.status.clone(),
            receipt("relay/7"),
            r.metadata.resource_version,
            FieldManager::TaskController,
            &lease2,
        )
        .unwrap();
    for i in 0..MAX_DELIVERIES {
        let k = format!("s{i}");
        r = store2
            .append_inbox(
                "r",
                entry(&k),
                r.metadata.resource_version,
                FieldManager::Cli,
                None,
            )
            .unwrap();
        r = store2
            .record_delivery(
                "r",
                r.status.clone(),
                receipt(&k),
                r.metadata.resource_version,
                FieldManager::TaskController,
                &lease2,
            )
            .unwrap();
    }
    let st = inbox_status(&r);
    assert_eq!(
        st.deliveries.receipts.len(),
        MAX_DELIVERIES,
        "newest 100 kept"
    );
    assert!(
        !st.deliveries.delivered("relay/7"),
        "the oldest rotated out"
    );
    assert_eq!(st.deliveries.revisions["cli/relay"], 7);
    let err = store2
        .append_inbox(
            "r",
            relay("relay/7b", 7),
            r.metadata.resource_version,
            FieldManager::Cli,
            None,
        )
        .unwrap_err();
    assert!(
        matches!(err, StoreError::Forbidden(ref m) if m.contains("revision 7")),
        "{err}"
    );
    store2
        .append_inbox(
            "r",
            relay("relay/8", 8),
            r.metadata.resource_version,
            FieldManager::Cli,
            None,
        )
        .unwrap();
}

#[test]
fn only_named_callers_reach_the_inbox_and_never_through_a_status_write() {
    let dir = Dir::new("inbox-callers");
    let store = FileStore::open(&dir.0).unwrap();
    let t = store.create(task("t"), FieldManager::Apply).unwrap();
    let lease = store.acquire_lease("m", 10_000).unwrap();
    let rv = t.metadata.resource_version;
    for by in [
        FieldManager::Apply,
        FieldManager::Worker,
        FieldManager::TaskController,
    ] {
        let err = store
            .append_inbox("t", entry("k1"), rv, by, Some(&lease))
            .unwrap_err();
        assert!(matches!(err, StoreError::Forbidden(_)), "{by:?}: {err}");
    }
    let mut mislabelled = entry("k1");
    mislabelled.from = "forge/cr-1".into();
    assert!(matches!(
        store.append_inbox("t", mislabelled, rv, FieldManager::Cli, None),
        Err(StoreError::Invalid(_))
    ));
    let t = store
        .append_inbox("t", entry("k1"), rv, FieldManager::Cli, None)
        .unwrap();

    // No manager replaces the list: not the controller, not the worker.
    let mut replaced = t.status.clone();
    replaced["inbox"] = json!([]);
    for (by, lease) in [
        (FieldManager::TaskController, Some(&lease)),
        (FieldManager::Worker, None),
    ] {
        let err = store
            .update_status(
                "Task",
                "t",
                replaced.clone(),
                t.metadata.resource_version,
                by,
                lease,
            )
            .unwrap_err();
        assert!(
            matches!(err, StoreError::Forbidden(ref m) if m.contains("status.inbox")),
            "{by:?}: {err}"
        );
    }
    // Not even in the record-and-remove write itself.
    let mut edited = t.status.clone();
    edited["inbox"][0]["text"] = json!("tampered");
    let err = store
        .record_delivery(
            "t",
            edited,
            receipt("k1"),
            t.metadata.resource_version,
            FieldManager::TaskController,
            &lease,
        )
        .unwrap_err();
    assert!(matches!(err, StoreError::Forbidden(_)), "{err}");
    for by in [FieldManager::Cli, FieldManager::Worker, FieldManager::Apply] {
        let err = store
            .record_delivery(
                "t",
                t.status.clone(),
                receipt("k1"),
                t.metadata.resource_version,
                by,
                &lease,
            )
            .unwrap_err();
        assert!(matches!(err, StoreError::Forbidden(_)), "{by:?}: {err}");
    }
    // The controller's own fields still move in that write, under the lease.
    let mut moved = t.status.clone();
    moved["phase"] = json!("Briefed");
    let t = store
        .record_delivery(
            "t",
            moved,
            receipt("k1"),
            t.metadata.resource_version,
            FieldManager::TaskController,
            &lease,
        )
        .unwrap();
    assert_eq!(t.status["phase"], "Briefed");
    assert!(t.status["inbox"].as_array().is_none_or(Vec::is_empty));
    let stale = store.acquire_lease("n", 10_000);
    assert!(stale.is_err(), "the lease is held");
    let t2 = store
        .append_inbox(
            "t",
            entry("k2"),
            t.metadata.resource_version,
            FieldManager::Cli,
            None,
        )
        .unwrap();
    store.release_lease(&lease).unwrap();
    assert!(matches!(
        store.record_delivery(
            "t",
            t2.status.clone(),
            receipt("k2"),
            t2.metadata.resource_version,
            FieldManager::TaskController,
            &lease
        ),
        Err(StoreError::LeaseLost(_))
    ));
    // A deleting Task takes no more messages.
    store.delete("Task", "t", FieldManager::Apply).unwrap();
    let t = known(store.get("Task", "t")).unwrap();
    assert!(matches!(
        store.append_inbox(
            "t",
            entry("k3"),
            t.metadata.resource_version,
            FieldManager::Cli,
            None
        ),
        Err(StoreError::Forbidden(_))
    ));
}

#[test]
fn concurrent_appends_and_removes_lose_nothing() {
    let dir = Dir::new("inbox-race");
    let store = FileStore::open(&dir.0).unwrap();
    store.create(task("t"), FieldManager::Apply).unwrap();
    let lease = store.acquire_lease("m", 60_000).unwrap();
    let senders = 4;
    let per = 6;
    let appenders: Vec<_> = (0..senders)
        .map(|s| {
            let root = dir.0.clone();
            thread::spawn(move || {
                let store = FileStore::open(&root).unwrap();
                for i in 0..per {
                    loop {
                        let cur = known(store.get("Task", "t")).unwrap();
                        let e = entry(&format!("s{s}-{i}"));
                        match store.append_inbox(
                            "t",
                            e,
                            cur.metadata.resource_version,
                            FieldManager::Cli,
                            None,
                        ) {
                            Ok(_) => break,
                            Err(StoreError::Conflict { .. } | StoreError::Forbidden(_)) => {
                                thread::sleep(std::time::Duration::from_millis(2));
                            }
                            Err(e) => panic!("{e}"),
                        }
                    }
                }
            })
        })
        .collect();
    // The controller, meanwhile, delivers whatever is at the head.
    let mut delivered = 0;
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(60);
    while delivered < senders * per {
        assert!(
            std::time::Instant::now() < deadline,
            "delivered {delivered}"
        );
        let cur = known(store.get("Task", "t")).unwrap();
        let st = inbox_status(&cur);
        let Some(head) = st.inbox.first() else {
            thread::sleep(std::time::Duration::from_millis(2));
            continue;
        };
        match store.record_delivery(
            "t",
            cur.status.clone(),
            receipt(&head.key),
            cur.metadata.resource_version,
            FieldManager::TaskController,
            &lease,
        ) {
            Ok(_) => delivered += 1,
            Err(StoreError::Conflict { .. }) => {}
            Err(e) => panic!("{e}"),
        }
    }
    for h in appenders {
        h.join().unwrap();
    }
    let st = inbox_status(&known(store.get("Task", "t")).unwrap());
    assert!(st.inbox.is_empty(), "{:?}", st.inbox);
    let keys: std::collections::BTreeSet<_> = st
        .deliveries
        .receipts
        .iter()
        .map(|r| r.key.clone())
        .collect();
    assert_eq!(
        keys.len(),
        senders * per,
        "every entry delivered exactly once"
    );
    assert!(
        fs::read_dir(dir.0.join("tmp")).unwrap().next().is_none(),
        "no temp file left"
    );
}

#[test]
fn a_torn_inbox_write_leaves_the_last_complete_record() {
    let dir = Dir::new("inbox-torn");
    let store = FileStore::open(&dir.0).unwrap();
    let t = store.create(task("t"), FieldManager::Apply).unwrap();
    let t = store
        .append_inbox(
            "t",
            entry("k1"),
            t.metadata.resource_version,
            FieldManager::Cli,
            None,
        )
        .unwrap();
    drop(store);
    // An appender that died after its temp file, before the rename.
    fs::write(dir.0.join("tmp").join("999-0.tmp"), b"{\"half\": ").unwrap();
    let store = FileStore::open(&dir.0).unwrap();
    assert!(fs::read_dir(dir.0.join("tmp")).unwrap().next().is_none());
    assert_eq!(known(store.get("Task", "t")).unwrap(), t);
    assert_eq!(inbox_status(&t).inbox.len(), 1);
    // A record torn in place is Unknown, and blocks both mutations.
    let path = dir.0.join("objects").join("Task").join("t.json");
    let bytes = fs::read(&path).unwrap();
    fs::write(&path, &bytes[..bytes.len() / 2]).unwrap();
    let lease = store.acquire_lease("m", 10_000).unwrap();
    assert!(matches!(
        store.append_inbox(
            "t",
            entry("k2"),
            t.metadata.resource_version,
            FieldManager::Cli,
            None
        ),
        Err(StoreError::Unknown(_))
    ));
    assert!(matches!(
        store.record_delivery(
            "t",
            t.status.clone(),
            receipt("k1"),
            t.metadata.resource_version,
            FieldManager::TaskController,
            &lease
        ),
        Err(StoreError::Unknown(_))
    ));
    assert_eq!(
        fs::read(&path).unwrap(),
        &bytes[..bytes.len() / 2],
        "never repaired"
    );
}
