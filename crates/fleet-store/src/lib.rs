//! Fleet's file-backed store: one JSON file per object in a data directory
//! (Q9), written through [`durable`]'s primitives under one store lock.
//!
//! Layout of a data directory:
//!
//! ```text
//! store.json                  format marker: {"format": "fleet-store", "version": 1}
//! lock                        the store lock (an OS file lock)
//! seq                         the last resourceVersion handed out
//! lease.json                  the manager lease, if one is held
//! objects/<Kind>/<name>.json  one object, spec and status together (Q6)
//! tmp/                        temp files; anything here at open is a crashed write
//! ```
//!
//! Reads take no lock: every record is replaced atomically, so a read sees a
//! whole old or whole new file. Every write takes the store lock, re-reads,
//! checks, then writes; that is what makes create exclusive and update a
//! compare-and-swap across processes.

pub mod durable;

use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use fleet_api::kinds;
use fleet_api::validate::{
    validate_kind, validate_name, validate_object, validate_spec_update, validate_status,
};
use fleet_api::{
    Clock, DeleteOutcome, FieldManager, LeaseToken, Millis, Object, Observation, Store, StoreError,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::durable::LockGuard;

const FORMAT: &str = "fleet-store";
const FORMAT_VERSION: u32 = 1;

/// Wall-clock milliseconds: the store stamps records and expires leases.
#[derive(Debug, Clone, Copy, Default)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> Millis {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.as_millis() as Millis)
    }
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Marker {
    format: String,
    version: u32,
}

pub struct FileStore {
    root: PathBuf,
    clock: Arc<dyn Clock + Send + Sync>,
}

impl std::fmt::Debug for FileStore {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("FileStore")
            .field("root", &self.root)
            .finish_non_exhaustive()
    }
}

impl FileStore {
    /// Open the store at `root`, creating it when the directory is absent or
    /// empty. A non-empty directory that is not a fleet store is refused, so
    /// a mistyped path never turns someone's files into a store.
    pub fn open(root: &Path) -> Result<FileStore, StoreError> {
        FileStore::open_with_clock(root, Arc::new(SystemClock))
    }

    pub fn open_with_clock(
        root: &Path,
        clock: Arc<dyn Clock + Send + Sync>,
    ) -> Result<FileStore, StoreError> {
        let store = FileStore {
            root: root.to_path_buf(),
            clock,
        };
        let marker = root.join("store.json");
        match fs::read(&marker) {
            Ok(bytes) => store.check_marker(&bytes)?,
            Err(e) if e.kind() == io::ErrorKind::NotFound => store.init()?,
            Err(e) => return Err(io_unknown(&marker, e)),
        }
        store.clean_temp()?;
        Ok(store)
    }

    fn check_marker(&self, bytes: &[u8]) -> Result<(), StoreError> {
        let m: Marker = serde_json::from_slice(bytes).map_err(|e| {
            StoreError::Unknown(format!(
                "{}: unreadable store marker: {e}",
                self.root.display()
            ))
        })?;
        if m.format != FORMAT || m.version != FORMAT_VERSION {
            return Err(StoreError::Unsupported(format!(
                "{} holds {} version {}; this build reads {FORMAT} version {FORMAT_VERSION}",
                self.root.display(),
                m.format,
                m.version
            )));
        }
        Ok(())
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Create the store under its lock, so two processes opening the same
    /// fresh directory at once agree on one marker.
    fn init(&self) -> Result<(), StoreError> {
        match fs::read_dir(&self.root) {
            Ok(entries) => {
                // Only a store's own entries may be present: a second process
                // can be initialising the same empty directory right now.
                let own = ["store.json", "lock", "seq", "lease.json", "objects", "tmp"];
                let foreign = entries
                    .filter_map(Result::ok)
                    .any(|e| !own.iter().any(|o| e.file_name() == *o));
                if foreign {
                    return Err(StoreError::Invalid(format!(
                        "{} is not empty and is not a fleet store; refusing to use it",
                        self.root.display()
                    )));
                }
            }
            Err(e) if e.kind() == io::ErrorKind::NotFound => {
                fs::create_dir_all(&self.root).map_err(|e| io_unknown(&self.root, e))?;
            }
            Err(e) => return Err(io_unknown(&self.root, e)),
        }
        let _guard = self.lock()?;
        let path = self.root.join("store.json");
        if path.exists() {
            let bytes = fs::read(&path).map_err(|e| io_unknown(&path, e))?;
            return self.check_marker(&bytes);
        }
        for dir in [self.tmp_dir(), self.root.join("objects")] {
            fs::create_dir_all(&dir).map_err(|e| io_unknown(&dir, e))?;
        }
        let marker = serde_json::to_vec_pretty(&Marker {
            format: FORMAT.into(),
            version: FORMAT_VERSION,
        })
        .map_err(|e| StoreError::Invalid(e.to_string()))?;
        durable::create_exclusive(&self.tmp_dir(), &path, &marker)
            .map_err(|e| io_unknown(&path, e))?;
        Ok(())
    }

    /// A temp file present while nobody holds the lock belongs to a writer
    /// that died before linking it; it was never visible, so it goes.
    fn clean_temp(&self) -> Result<(), StoreError> {
        let _guard = self.lock()?;
        let dir = self.tmp_dir();
        let entries = fs::read_dir(&dir).map_err(|e| io_unknown(&dir, e))?;
        for entry in entries {
            let entry = entry.map_err(|e| io_unknown(&dir, e))?;
            durable::remove(&entry.path()).map_err(|e| io_unknown(&entry.path(), e))?;
        }
        Ok(())
    }

    fn tmp_dir(&self) -> PathBuf {
        self.root.join("tmp")
    }

    fn kind_dir(&self, kind: &str) -> PathBuf {
        self.root.join("objects").join(kind)
    }

    fn object_path(&self, kind: &str, name: &str) -> PathBuf {
        self.kind_dir(kind).join(format!("{name}.json"))
    }

    fn lock(&self) -> Result<LockGuard, StoreError> {
        let path = self.root.join("lock");
        LockGuard::acquire(&path).map_err(|e| io_unknown(&path, e))
    }

    fn now(&self) -> Millis {
        self.clock.now()
    }

    fn next_rv(&self) -> Result<u64, StoreError> {
        let path = self.root.join("seq");
        let current = match fs::read_to_string(&path) {
            Ok(s) => s
                .trim()
                .parse::<u64>()
                .map_err(|e| StoreError::Unknown(format!("{}: corrupt: {e}", path.display())))?,
            Err(e) if e.kind() == io::ErrorKind::NotFound => 0,
            Err(e) => return Err(io_unknown(&path, e)),
        };
        let next = current
            .checked_add(1)
            .ok_or_else(|| StoreError::Invalid("resourceVersion exhausted".into()))?;
        durable::replace(&self.tmp_dir(), &path, next.to_string().as_bytes())
            .map_err(|e| io_unknown(&path, e))?;
        Ok(next)
    }

    fn read(&self, kind: &str, name: &str) -> Observation<Option<Object>> {
        if let Err(e) = validate_kind(kind).and_then(|()| validate_name(name)) {
            return Observation::unknown(e.to_string());
        }
        read_object(&self.object_path(kind, name), kind, name)
    }

    /// Read under the lock for a write: Unknown is an error, absence NotFound.
    fn read_existing(&self, kind: &str, name: &str) -> Result<Object, StoreError> {
        match self.read(kind, name) {
            Observation::Known(Some(o)) => Ok(o),
            Observation::Known(None) => Err(StoreError::NotFound(format!("{kind}/{name}"))),
            Observation::Unknown { why } => Err(StoreError::Unknown(why)),
        }
    }

    fn write(&self, obj: &Object) -> Result<(), StoreError> {
        let path = self.object_path(&obj.kind, &obj.metadata.name);
        let bytes = encode(obj)?;
        durable::replace(&self.tmp_dir(), &path, &bytes).map_err(|e| io_unknown(&path, e))
    }

    fn check_lease(&self, lease: &LeaseToken) -> Result<(), StoreError> {
        match self.read_lease()? {
            Some(l)
                if l.holder == lease.holder
                    && l.token == lease.token
                    && self.now() < l.expires_at =>
            {
                Ok(())
            }
            Some(l) => Err(StoreError::LeaseLost(format!(
                "{} token {} no longer holds the lease (now {} token {})",
                lease.holder, lease.token, l.holder, l.token
            ))),
            None => Err(StoreError::LeaseLost(format!(
                "{} token {}: no lease is held",
                lease.holder, lease.token
            ))),
        }
    }

    fn read_lease(&self) -> Result<Option<LeaseToken>, StoreError> {
        let path = self.root.join("lease.json");
        match fs::read(&path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map(Some).map_err(|e| {
                StoreError::Unknown(format!("{}: corrupt lease: {e}", path.display()))
            }),
            Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(io_unknown(&path, e)),
        }
    }

    fn write_lease(&self, lease: &LeaseToken) -> Result<(), StoreError> {
        let path = self.root.join("lease.json");
        let bytes =
            serde_json::to_vec_pretty(lease).map_err(|e| StoreError::Invalid(e.to_string()))?;
        durable::replace(&self.tmp_dir(), &path, &bytes).map_err(|e| io_unknown(&path, e))
    }

    /// Mark `obj` deleted; it stays while a finalizer holds it.
    fn mark_deleted(&self, mut obj: Object) -> Result<DeleteOutcome, StoreError> {
        if obj.metadata.deletion_timestamp.is_none() {
            obj.metadata.deletion_timestamp = Some(self.now());
            obj.metadata.resource_version = self.next_rv()?;
            self.write(&obj)?;
        }
        self.remove_if_done(&obj)
    }

    /// An object marked for deletion goes once it has no finalizer.
    fn remove_if_done(&self, obj: &Object) -> Result<DeleteOutcome, StoreError> {
        if !obj.is_deleting() || !obj.metadata.finalizers.is_empty() {
            return Ok(DeleteOutcome::Marked);
        }
        let path = self.object_path(&obj.kind, &obj.metadata.name);
        durable::remove(&path).map_err(|e| io_unknown(&path, e))?;
        Ok(DeleteOutcome::Removed)
    }
}

fn read_object(path: &Path, kind: &str, name: &str) -> Observation<Option<Object>> {
    let bytes = match fs::read(path) {
        Ok(b) => b,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Observation::Known(None),
        Err(e) => return Observation::unknown(format!("inaccessible {}: {e}", path.display())),
    };
    match serde_json::from_slice::<Object>(&bytes) {
        Ok(o) if o.kind == kind && o.metadata.name == name => {
            if o.metadata.uid.is_empty()
                || o.metadata.resource_version == 0
                || o.metadata.generation == 0
                || o.metadata.creation_timestamp.is_none()
                || (!o.is_deleting()
                    && !o
                        .metadata
                        .finalizers
                        .iter()
                        .any(|f| f == fleet_api::task::RELEASE_FINALIZER))
                || validate_object(&o).is_err()
                || validate_status(kind, &o.status).is_err()
            {
                Observation::unknown(format!(
                    "corrupt {}: invalid persisted resource metadata or status",
                    path.display()
                ))
            } else {
                Observation::Known(Some(o))
            }
        }
        Ok(o) => Observation::unknown(format!("corrupt {}: holds {}", path.display(), o.key())),
        Err(e) => Observation::unknown(format!("corrupt {}: {e}", path.display())),
    }
}

fn encode(obj: &Object) -> Result<Vec<u8>, StoreError> {
    let mut bytes =
        serde_json::to_vec_pretty(obj).map_err(|e| StoreError::Invalid(e.to_string()))?;
    bytes.push(b'\n');
    Ok(bytes)
}

fn io_unknown(path: &Path, e: io::Error) -> StoreError {
    StoreError::Unknown(format!("{}: {e}", path.display()))
}

fn expect_rv(obj: &Object, expected: u64) -> Result<(), StoreError> {
    if obj.metadata.resource_version != expected {
        return Err(StoreError::Conflict {
            key: obj.key(),
            expected,
            actual: obj.metadata.resource_version,
        });
    }
    Ok(())
}

impl Store for FileStore {
    fn get(&self, kind: &str, name: &str) -> Observation<Option<Object>> {
        self.read(kind, name)
    }

    fn list(&self, kind: &str) -> Observation<Vec<Object>> {
        if let Err(e) = validate_kind(kind) {
            return Observation::unknown(e.to_string());
        }
        let dir = self.kind_dir(kind);
        let entries = match fs::read_dir(&dir) {
            Ok(e) => e,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Observation::Known(Vec::new()),
            Err(e) => return Observation::unknown(format!("inaccessible {}: {e}", dir.display())),
        };
        let mut objs = Vec::new();
        for entry in entries {
            let path = match entry {
                Ok(e) => e.path(),
                Err(e) => {
                    return Observation::unknown(format!("inaccessible {}: {e}", dir.display()));
                }
            };
            let Some(name) = path
                .file_name()
                .and_then(|n| n.to_str())
                .and_then(|n| n.strip_suffix(".json"))
            else {
                return Observation::unknown(format!(
                    "unexpected file {} in the store",
                    path.display()
                ));
            };
            match read_object(&path, kind, name) {
                Observation::Known(Some(o)) => objs.push(o),
                // Removed between read_dir and read: it is gone, which is a fact.
                Observation::Known(None) => {}
                Observation::Unknown { why } => return Observation::Unknown { why },
            }
        }
        objs.sort_by_key(|o| o.metadata.name.clone());
        Observation::Known(objs)
    }

    fn create(&self, obj: Object, by: FieldManager) -> Result<Object, StoreError> {
        if by != FieldManager::Apply {
            return Err(StoreError::Forbidden(format!(
                "{} does not create objects",
                by.name()
            )));
        }
        let _guard = self.lock()?;
        self.insert(obj)
    }

    fn update_spec(
        &self,
        kind: &str,
        name: &str,
        spec: Value,
        labels: Option<BTreeMap<String, String>>,
        expected: u64,
        by: FieldManager,
    ) -> Result<Object, StoreError> {
        if by != FieldManager::Apply {
            return Err(StoreError::Forbidden(format!(
                "{} does not write spec",
                by.name()
            )));
        }
        let _guard = self.lock()?;
        let mut obj = self.read_existing(kind, name)?;
        expect_rv(&obj, expected)?;
        if obj.is_deleting() {
            return Err(StoreError::Forbidden(format!(
                "{} is being deleted",
                obj.key()
            )));
        }
        validate_spec_update(kind, &obj.spec, &spec)?;
        let mut changed = false;
        if obj.spec != spec {
            obj.spec = spec;
            obj.metadata.generation = obj
                .metadata
                .generation
                .checked_add(1)
                .ok_or_else(|| StoreError::Invalid("generation exhausted".into()))?;
            changed = true;
        }
        if let Some(labels) = labels
            && labels != obj.metadata.labels
        {
            obj.metadata.labels = labels;
            changed = true;
        }
        if changed {
            obj.metadata.resource_version = self.next_rv()?;
            self.write(&obj)?;
        }
        Ok(obj)
    }

    fn update_status(
        &self,
        kind: &str,
        name: &str,
        status: Value,
        expected: u64,
        by: FieldManager,
        lease: Option<&LeaseToken>,
    ) -> Result<Object, StoreError> {
        if kinds::controller_of(kind).is_none() {
            return Err(StoreError::Forbidden(format!("kind {kind} has no status")));
        }
        if !(status.is_object() || status.is_null()) {
            return Err(StoreError::Invalid("status must be an object".into()));
        }
        validate_status(kind, &status)?;
        let _guard = self.lock()?;
        if by.is_controller() {
            match lease {
                Some(l) => self.check_lease(l)?,
                None => {
                    return Err(StoreError::LeaseLost(format!(
                        "{} writes only under the manager lease",
                        by.name()
                    )));
                }
            }
        }
        let mut obj = self.read_existing(kind, name)?;
        expect_rv(&obj, expected)?;
        if obj.is_deleting() && !by.is_controller() {
            return Err(StoreError::Forbidden(format!(
                "{} is being deleted",
                obj.key()
            )));
        }
        let empty = serde_json::Map::new();
        let old = obj.status.as_object().unwrap_or(&empty);
        let new = status.as_object().unwrap_or(&empty);
        for field in old.keys().chain(new.keys()) {
            if old.get(field) != new.get(field) && kinds::status_owner(kind, field) != Some(by) {
                return Err(StoreError::Forbidden(format!(
                    "{} may not write status.{field} of {kind}",
                    by.name()
                )));
            }
        }
        if obj.status == status {
            return Ok(obj);
        }
        obj.status = status;
        obj.metadata.resource_version = self.next_rv()?;
        self.write(&obj)?;
        Ok(obj)
    }

    fn delete(
        &self,
        kind: &str,
        name: &str,
        by: FieldManager,
    ) -> Result<DeleteOutcome, StoreError> {
        if by != FieldManager::Apply {
            return Err(StoreError::Forbidden(format!(
                "{} does not delete objects",
                by.name()
            )));
        }
        let _guard = self.lock()?;
        let obj = self.read_existing(kind, name)?;
        self.mark_deleted(obj)
    }

    fn remove_finalizer(
        &self,
        kind: &str,
        name: &str,
        finalizer: &str,
        expected: u64,
        by: FieldManager,
        lease: &LeaseToken,
    ) -> Result<DeleteOutcome, StoreError> {
        if kinds::controller_of(kind) != Some(by) {
            return Err(StoreError::Forbidden(format!(
                "only {kind}'s controller removes its finalizers"
            )));
        }
        let _guard = self.lock()?;
        self.check_lease(lease)?;
        let mut obj = self.read_existing(kind, name)?;
        expect_rv(&obj, expected)?;
        // A finalizer comes off only when its work is done, which is only
        // after the object was marked for deletion. Removed from a live
        // object it would let a later delete skip the release entirely.
        if !obj.is_deleting() {
            return Err(StoreError::Forbidden(format!(
                "{} is not being deleted; its finalizers stay",
                obj.key()
            )));
        }
        if !obj.metadata.finalizers.iter().any(|f| f == finalizer) {
            // The finalizer write may have survived a crash before unlink.
            // Ownership, CAS, lease and deletion checks still fence cleanup.
            return self.remove_if_done(&obj);
        }
        obj.metadata.finalizers.retain(|f| f != finalizer);
        obj.metadata.resource_version = self.next_rv()?;
        self.write(&obj)?;
        self.remove_if_done(&obj)
    }

    fn acquire_lease(&self, holder: &str, ttl: Millis) -> Result<LeaseToken, StoreError> {
        let _guard = self.lock()?;
        let now = self.now();
        let current = self.read_lease()?;
        // Held until it expires, even against the same holder name: two
        // managers started with one --holder must not both hold it.
        if let Some(l) = &current
            && now < l.expires_at
        {
            return Err(StoreError::LeaseHeld {
                holder: l.holder.clone(),
                expires_at: l.expires_at,
            });
        }
        let token = current.map_or(1, |l| l.token + 1);
        let lease = LeaseToken {
            holder: holder.to_string(),
            token,
            expires_at: now.saturating_add(ttl),
        };
        self.write_lease(&lease)?;
        Ok(lease)
    }

    fn renew_lease(&self, lease: &LeaseToken, ttl: Millis) -> Result<LeaseToken, StoreError> {
        let _guard = self.lock()?;
        self.check_lease(lease)?;
        let renewed = LeaseToken {
            expires_at: self.now().saturating_add(ttl),
            ..lease.clone()
        };
        self.write_lease(&renewed)?;
        Ok(renewed)
    }

    fn release_lease(&self, lease: &LeaseToken) -> Result<(), StoreError> {
        let _guard = self.lock()?;
        if let Some(l) = self.read_lease()?
            && l.holder == lease.holder
            && l.token == lease.token
        {
            // Keep the token so the next holder's is larger: expire, never erase.
            let expired = LeaseToken { expires_at: 0, ..l };
            self.write_lease(&expired)?;
        }
        Ok(())
    }
}

impl FileStore {
    /// Create under the store lock the caller holds: validation, exclusive
    /// name, live owners, then the store's own metadata.
    fn insert(&self, mut obj: Object) -> Result<Object, StoreError> {
        validate_object(&obj)?;
        let m = &obj.metadata;
        if !m.uid.is_empty()
            || m.generation != 0
            || m.resource_version != 0
            || !m.finalizers.is_empty()
            || m.creation_timestamp.is_some()
            || m.deletion_timestamp.is_some()
        {
            return Err(StoreError::Invalid(
                "metadata.uid, generation, resourceVersion, finalizers and timestamps are assigned by the store".into(),
            ));
        }
        if !obj.status.is_null() {
            return Err(StoreError::Invalid(
                "status is written by controllers, not on create".into(),
            ));
        }
        let key = obj.key();
        match self.read(&obj.kind, &obj.metadata.name) {
            Observation::Known(None) => {}
            Observation::Known(Some(_)) => return Err(StoreError::AlreadyExists(key)),
            Observation::Unknown { why } => return Err(StoreError::Unknown(why)),
        }
        // Nothing owns a Task in this build, so no object may name an owner.
        if !obj.metadata.owner_references.is_empty() {
            return Err(StoreError::Invalid(
                "metadata.ownerReferences is not served: no kind owns a Task".into(),
            ));
        }
        let rv = self.next_rv()?;
        obj.metadata.uid = new_uid(rv);
        obj.metadata.generation = 1;
        obj.metadata.resource_version = rv;
        obj.metadata.finalizers = kinds::finalizers_on_create(&obj.kind);
        obj.metadata.creation_timestamp = Some(self.now());
        let dir = self.kind_dir(&obj.kind);
        fs::create_dir_all(&dir).map_err(|e| io_unknown(&dir, e))?;
        let path = self.object_path(&obj.kind, &obj.metadata.name);
        let created = durable::create_exclusive(&self.tmp_dir(), &path, &encode(&obj)?)
            .map_err(|e| io_unknown(&path, e))?;
        if !created {
            return Err(StoreError::AlreadyExists(key));
        }
        Ok(obj)
    }
}

/// Unique beyond this store: the sequence number orders, the random part keeps
/// two stores (a re-initialised data directory, a trial) from ever handing
/// out the same uid, which runtimes key sessions by.
fn new_uid(rv: u64) -> String {
    use std::hash::{BuildHasher, Hasher};
    let mut h = std::collections::hash_map::RandomState::new().build_hasher();
    h.write_u64(rv);
    h.write_u128(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or_default(),
    );
    h.write_u32(std::process::id());
    format!("u{rv}-{:016x}", h.finish())
}

#[cfg(test)]
mod initialization_tests {
    use super::*;

    #[test]
    fn initialization_rechecks_a_marker_created_by_another_writer() {
        // open() can observe no marker before another initializer gets the
        // lock. init() must recheck the winner's format while holding it.
        let root = std::env::temp_dir().join(format!("fleet-init-marker-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let marker = root.join("store.json");
        let legacy = br#"{"format":"fleet-store","version":2}"#;
        fs::write(&marker, legacy).unwrap();
        let store = FileStore {
            root: root.clone(),
            clock: Arc::new(SystemClock),
        };
        assert!(matches!(store.init(), Err(StoreError::Unsupported(_))));
        assert_eq!(fs::read(&marker).unwrap(), legacy);
        fs::remove_dir_all(root).unwrap();
    }
}
