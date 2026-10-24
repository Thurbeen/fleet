//! The verbs people and workers use: spec writes (`apply`, `delete`) and the
//! worker's answer (`result`). The CLI calls these and nothing below them, so
//! every rule lives here once.

use fleet_api::change_request::{self, ChangeRequestSpec};
use fleet_api::forge::{ChangeRef, Forge};
use fleet_api::inbox::{CLI_SOURCE_PREFIX, InboxEntry, send_key};
use fleet_api::result::{TaskResult, validate_outputs};
use fleet_api::task::{self, TaskSpec, TaskStatus};
use fleet_api::validate::typed;
use fleet_api::{Clock, DeleteOutcome, FieldManager, Object, Observation, Store, StoreError};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum ApplyOutcome {
    Created,
    Configured,
    Unchanged,
}

/// Bounded retries for a compare-and-swap that keeps losing to another
/// writer; past it the conflict is reported, never forced.
const CAS_TRIES: u32 = 8;

/// Start a bootstrap Task once, retaining its immutable inputs and identity
/// on reconnect. A changed template never rewrites an already running Task.
pub fn ensure_task(store: &dyn Store, obj: Object) -> Result<Object, StoreError> {
    store.validate(&obj)?;
    for _ in 0..CAS_TRIES {
        match store.get(&obj.kind, &obj.metadata.name) {
            Observation::Known(Some(current)) => return Ok(current),
            Observation::Known(None) => match store.create(obj.clone(), FieldManager::Apply) {
                Ok(created) => return Ok(created),
                Err(StoreError::AlreadyExists(_)) => continue,
                Err(e) => return Err(e),
            },
            Observation::Unknown { why } => return Err(StoreError::Unknown(why)),
        }
    }
    Err(StoreError::Busy(
        "bootstrap Task creation kept conflicting".into(),
    ))
}

/// Create the object, or replace its spec and labels when it exists.
/// Status in an applied object is refused: status belongs to controllers.
pub fn apply(store: &dyn Store, obj: Object) -> Result<(ApplyOutcome, Object), StoreError> {
    store.validate(&obj)?;
    if !obj.status.is_null() {
        return Err(StoreError::Invalid(format!(
            "{}: status is written by controllers; remove it from the applied object",
            obj.key()
        )));
    }
    let name = obj.metadata.name.clone();
    for _ in 0..CAS_TRIES {
        match store.get(&obj.kind, &name) {
            Observation::Known(None) => match store.create(obj.clone(), FieldManager::Apply) {
                Ok(o) => return Ok((ApplyOutcome::Created, o)),
                // Another writer created it between our read and our create.
                Err(StoreError::AlreadyExists(_)) => continue,
                Err(e) => return Err(e),
            },
            Observation::Known(Some(current)) => {
                if current.metadata.owner_references != obj.metadata.owner_references {
                    return Err(StoreError::Invalid(format!(
                        "{}: ownerReferences cannot change",
                        obj.key()
                    )));
                }
                let rv = current.metadata.resource_version;
                let labels = Some(obj.metadata.labels.clone());
                match store.update_spec(
                    &obj.kind,
                    &name,
                    obj.spec.clone(),
                    labels,
                    rv,
                    FieldManager::Apply,
                ) {
                    Ok(o) if o.metadata.resource_version == rv => {
                        return Ok((ApplyOutcome::Unchanged, o));
                    }
                    Ok(o) => return Ok((ApplyOutcome::Configured, o)),
                    Err(StoreError::Conflict { .. }) => continue,
                    Err(e) => return Err(e),
                }
            }
            Observation::Unknown { why } => return Err(StoreError::Unknown(why)),
        }
    }
    Err(StoreError::Busy(format!(
        "{}/{name}: kept conflicting with other writers",
        obj.kind
    )))
}

pub fn delete(store: &dyn Store, kind: &str, name: &str) -> Result<DeleteOutcome, StoreError> {
    store.delete(kind, name, FieldManager::Apply)
}

/// `cr-` and the first twelve hex digits of the SHA-256 of the canonical
/// ref: one change names one object, two projects that flatten alike cannot
/// collide, and long project paths fit.
pub fn derived_name(reference: &ChangeRef) -> String {
    let digest = Sha256::digest(reference.canonical().as_bytes());
    let hex: String = digest.iter().take(6).map(|b| format!("{b:02x}")).collect();
    format!("cr-{hex}")
}

/// Start mirroring a change. The URL is parsed by the forge adapter for its
/// host (a host not configured is refused), the name is derived from the
/// canonical ref when the manifest gives none, and an existing object under
/// the name is reused only if it mirrors the same change: its recorded
/// `status.ref` when the controller wrote one, else its own URL parsed.
pub fn apply_change_request(
    store: &dyn Store,
    forge: &dyn Forge,
    mut obj: Object,
) -> Result<(ApplyOutcome, Object), StoreError> {
    if obj.kind != change_request::KIND {
        return Err(StoreError::Invalid(format!(
            "{}: not a ChangeRequest",
            obj.key()
        )));
    }
    let spec: ChangeRequestSpec = typed(change_request::KIND, &obj.spec)?;
    let reference = parse_url(forge, &spec.url, &obj.metadata.name)?;
    if obj.metadata.name.is_empty() {
        obj.metadata.name = derived_name(&reference);
    }
    store.validate(&obj)?;
    if !obj.status.is_null() {
        return Err(StoreError::Invalid(format!(
            "{}: status is written by controllers; remove it from the applied object",
            obj.key()
        )));
    }
    let name = obj.metadata.name.clone();
    for _ in 0..CAS_TRIES {
        match store.get(&obj.kind, &name) {
            Observation::Known(None) => match store.create(obj.clone(), FieldManager::Apply) {
                Ok(o) => return Ok((ApplyOutcome::Created, o)),
                Err(StoreError::AlreadyExists(_)) => continue,
                Err(e) => return Err(e),
            },
            Observation::Known(Some(current)) => {
                let recorded: Option<ChangeRef> = match current.status.get("ref") {
                    Some(v) if !v.is_null() => {
                        Some(serde_json::from_value(v.clone()).map_err(|e| {
                            StoreError::Unknown(format!("{}: status.ref: {e}", obj.key()))
                        })?)
                    }
                    _ => None,
                };
                let mirrors = match recorded {
                    Some(r) => r,
                    None => {
                        let existing: ChangeRequestSpec =
                            typed(change_request::KIND, &current.spec)?;
                        parse_url(forge, &existing.url, &name)?
                    }
                };
                if mirrors != reference {
                    return Err(StoreError::Forbidden(format!(
                        "{} mirrors {}, not {}: a change names one object, so give this one another name",
                        obj.key(),
                        mirrors.canonical(),
                        reference.canonical()
                    )));
                }
                let rv = current.metadata.resource_version;
                let labels = Some(obj.metadata.labels.clone());
                match store.update_spec(
                    &obj.kind,
                    &name,
                    obj.spec.clone(),
                    labels,
                    rv,
                    FieldManager::Apply,
                ) {
                    Ok(o) if o.metadata.resource_version == rv => {
                        return Ok((ApplyOutcome::Unchanged, o));
                    }
                    Ok(o) => return Ok((ApplyOutcome::Configured, o)),
                    Err(StoreError::Conflict { .. }) => continue,
                    Err(e) => return Err(e),
                }
            }
            Observation::Unknown { why } => return Err(StoreError::Unknown(why)),
        }
    }
    Err(StoreError::Busy(format!(
        "{}/{name}: kept conflicting with other writers",
        obj.kind
    )))
}

fn parse_url(forge: &dyn Forge, url: &str, name: &str) -> Result<ChangeRef, StoreError> {
    match forge.parse(url) {
        Observation::Known(r) => Ok(r),
        Observation::Unknown { why } => Err(StoreError::Invalid(format!(
            "ChangeRequest/{name}: spec.url {url:?}: {why}"
        ))),
    }
}

/// `fleet send`: queue a message for the Task's session as `cli/<user>`,
/// under the key `task/<name>/<uid>/send/<n>`, through the store's typed
/// append. `n` is the resourceVersion the append is made against, which the
/// store hands out once for its whole life, so a key never repeats even
/// after its receipt has rotated out of `status.deliveries`. The entry is
/// returned as appended; delivery is the controller's, on its next pass.
pub fn send(
    store: &dyn Store,
    clock: &dyn Clock,
    name: &str,
    user: &str,
    text: &str,
) -> Result<InboxEntry, StoreError> {
    let user = user.trim();
    if user.is_empty() || user.contains(['/', '\0']) {
        return Err(StoreError::Invalid(format!(
            "sender {user:?}: a user name without '/'"
        )));
    }
    if text.trim().is_empty() {
        return Err(StoreError::Invalid(
            "nothing to send: the text is empty".into(),
        ));
    }
    for _ in 0..CAS_TRIES {
        let obj = match store.get(task::KIND, name) {
            Observation::Known(Some(o)) => o,
            Observation::Known(None) => {
                return Err(StoreError::NotFound(format!("{}/{name}", task::KIND)));
            }
            Observation::Unknown { why } => return Err(StoreError::Unknown(why)),
        };
        if obj.is_deleting() {
            return Err(StoreError::Forbidden(format!(
                "{} is being deleted",
                obj.key()
            )));
        }
        let m = &obj.metadata;
        let entry = InboxEntry {
            key: send_key(&m.name, &m.uid, m.resource_version),
            from: format!("{CLI_SOURCE_PREFIX}{user}"),
            text: text.to_string(),
            about: None,
            queued_at: clock.now(),
        };
        match store.append_inbox(
            name,
            entry.clone(),
            m.resource_version,
            FieldManager::Cli,
            None,
        ) {
            Ok(_) => return Ok(entry),
            Err(StoreError::Conflict { .. }) => continue,
            Err(e) => return Err(e),
        }
    }
    Err(StoreError::Busy(format!(
        "{}/{name}: kept conflicting with other writers",
        task::KIND
    )))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum ResultOutcome {
    Recorded,
    /// The same result was already recorded: a retry of `fleet result`.
    Unchanged,
}

/// The worker's answer: written as field manager `worker` into
/// `status.result`, and only once the brief it answers has a receipt.
pub fn record_result(
    store: &dyn Store,
    name: &str,
    result: TaskResult,
) -> Result<ResultOutcome, StoreError> {
    for _ in 0..CAS_TRIES {
        let obj = match store.get(task::KIND, name) {
            Observation::Known(Some(o)) => o,
            Observation::Known(None) => {
                return Err(StoreError::NotFound(format!("{}/{name}", task::KIND)));
            }
            Observation::Unknown { why } => return Err(StoreError::Unknown(why)),
        };
        if obj.is_deleting() {
            return Err(StoreError::Forbidden(format!(
                "{} is being deleted",
                obj.key()
            )));
        }
        let spec: TaskSpec = typed(task::KIND, &obj.spec)?;
        let mut st: TaskStatus = if obj.status.is_null() {
            TaskStatus::default()
        } else {
            typed(task::KIND, &obj.status)?
        };
        let Some(brief) = &st.brief else {
            return Err(StoreError::Forbidden(format!(
                "{}: no brief has a receipt yet; a result answers a delivered brief",
                obj.key()
            )));
        };
        if result.generation != brief.generation {
            return Err(StoreError::Invalid(format!(
                "{}: stale generation {}: the brief was delivered for generation {}",
                obj.key(),
                result.generation,
                brief.generation
            )));
        }
        let problems = validate_outputs(&spec.outputs, &result.outputs);
        if !problems.is_empty() {
            return Err(StoreError::Invalid(format!(
                "{}: result does not match spec.outputs: {}",
                obj.key(),
                problems.join("; ")
            )));
        }
        match &st.result {
            Some(existing) if *existing == result => return Ok(ResultOutcome::Unchanged),
            Some(_) => {
                return Err(StoreError::Forbidden(format!(
                    "{}: a different result is already recorded",
                    obj.key()
                )));
            }
            None => {}
        }
        st.result = Some(result.clone());
        let status = serde_json::to_value(&st).map_err(|e| StoreError::Invalid(e.to_string()))?;
        match store.update_status(
            task::KIND,
            name,
            status,
            obj.metadata.resource_version,
            FieldManager::Worker,
            None,
        ) {
            Ok(_) => return Ok(ResultOutcome::Recorded),
            Err(StoreError::Conflict { .. }) => continue,
            Err(e) => return Err(e),
        }
    }
    Err(StoreError::Busy(format!(
        "{}/{name}: kept conflicting with other writers",
        task::KIND
    )))
}
