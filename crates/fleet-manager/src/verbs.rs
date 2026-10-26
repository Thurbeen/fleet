//! The verbs people and workers use: spec writes (`apply`, `delete`), a
//! person's message (`send`) and the worker's answers (`result`, `declare`).
//! The CLI calls these and nothing below them, so every rule lives here
//! once.

use std::collections::BTreeSet;

use fleet_api::change_request::{self, ChangeRequestSpec};
use fleet_api::follow::{DECLARED_CHANGE_REQUEST, ForgeSpec, validate_declaration};
use fleet_api::forge::{ChangeRef, Forge};
use fleet_api::inbox::{CLI_SOURCE_PREFIX, InboxEntry, send_key};
use fleet_api::meta::API_VERSION;
use fleet_api::result::{TaskResult, validate_outputs};
use fleet_api::task::{self, TaskSpec, TaskStatus};
use fleet_api::validate::typed;
use fleet_api::{Clock, DeleteOutcome, FieldManager, Object, Observation, Store, StoreError};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
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
/// Status in an applied object is refused: status belongs to controllers. A
/// Task whose `spec.forge.draft.policy` names a policy not in
/// `ready_policies`, the names under `[forge.readyPolicies]`, is refused:
/// a policy the config does not define would never mark anything ready.
pub fn apply(
    store: &dyn Store,
    obj: Object,
    ready_policies: &BTreeSet<String>,
) -> Result<(ApplyOutcome, Object), StoreError> {
    store.validate(&obj)?;
    if obj.kind == task::KIND {
        let spec: TaskSpec = typed(task::KIND, &obj.spec)?;
        if let Some(name) = spec.forge.as_ref().and_then(ForgeSpec::named_policy)
            && !ready_policies.contains(name)
        {
            return Err(StoreError::Invalid(format!(
                "{}: spec.forge.draft.policy {name:?} is not defined under [forge.readyPolicies]",
                obj.key()
            )));
        }
    }
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
                verify_mirrors(forge, &current, &reference)?;
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

/// An existing ChangeRequest is reused only when it mirrors `reference`:
/// its recorded `status.ref` once the controller wrote one, else its own
/// URL parsed again.
fn verify_mirrors(
    forge: &dyn Forge,
    current: &Object,
    reference: &ChangeRef,
) -> Result<(), StoreError> {
    let recorded: Option<ChangeRef> = match current.status.get("ref") {
        Some(v) if !v.is_null() => Some(
            serde_json::from_value(v.clone())
                .map_err(|e| StoreError::Unknown(format!("{}: status.ref: {e}", current.key())))?,
        ),
        _ => None,
    };
    let mirrors = match recorded {
        Some(r) => r,
        None => {
            let existing: ChangeRequestSpec = typed(change_request::KIND, &current.spec)?;
            parse_url(forge, &existing.url, &current.metadata.name)?
        }
    };
    if mirrors != *reference {
        return Err(StoreError::Forbidden(format!(
            "{} mirrors {}, not {}: a change names one object, so give this one another name",
            current.key(),
            mirrors.canonical(),
            reference.canonical()
        )));
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum DeclareOutcome {
    Declared,
    /// The same fact was already declared: a retry of `fleet declare`.
    Unchanged,
}

/// What `fleet declare --change-request` did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Declared {
    pub outcome: DeclareOutcome,
    /// The declaration as appended (or found).
    pub item: Value,
    /// The ChangeRequest's name, and whether this verb created it.
    pub change_request: String,
    pub created: bool,
}

/// `fleet declare TASK --change-request URL`: parse the URL through the
/// Forge port (a host not configured is refused), ensure one ChangeRequest
/// exists for it under its derived name (an existing one is reused after
/// its `status.ref` is verified), then append `{kind, url, name}` to the
/// Task's `status.declared` as field manager `worker`. A repeated URL is a
/// no-op. The two steps are not atomic: a crash between them leaves a
/// ChangeRequest nobody follows, which the next `declare` of that URL finds.
pub fn declare_change_request(
    store: &dyn Store,
    forge: &dyn Forge,
    task_name: &str,
    url: &str,
) -> Result<Declared, StoreError> {
    // Eligibility first, so an ineligible Task creates no orphan.
    eligible_for_declare(store, task_name)?;
    let url = url.trim();
    let (name, created) = ensure_change_request(store, forge, url)?;
    let item = json!({"kind": DECLARED_CHANGE_REQUEST, "url": url, "name": name});
    let outcome = append_declaration(store, task_name, item.clone(), |existing| {
        existing.get("kind").and_then(Value::as_str) == Some(DECLARED_CHANGE_REQUEST)
            && existing.get("name").and_then(Value::as_str) == Some(name.as_str())
    })?;
    Ok(Declared {
        outcome,
        item,
        change_request: name,
        created,
    })
}

/// Ensure a verified mirror without applying over its operator-owned spec or labels.
pub(crate) fn ensure_change_request(
    store: &dyn Store,
    forge: &dyn Forge,
    url: &str,
) -> Result<(String, bool), StoreError> {
    let url = url.trim();
    let reference = match forge.parse(url) {
        Observation::Known(r) => r,
        Observation::Unknown { why } => {
            return Err(StoreError::Invalid(format!("change URL {url:?}: {why}")));
        }
    };
    let name = derived_name(&reference);
    for _ in 0..CAS_TRIES {
        match store.get(change_request::KIND, &name) {
            Observation::Known(None) => {
                let manifest = Object {
                    api_version: API_VERSION.to_string(),
                    kind: change_request::KIND.to_string(),
                    metadata: fleet_api::ObjectMeta {
                        name: name.clone(),
                        ..fleet_api::ObjectMeta::default()
                    },
                    spec: json!({"url": url}),
                    status: Value::Null,
                };
                store.validate(&manifest)?;
                match store.create(manifest, FieldManager::Apply) {
                    Ok(_) => return Ok((name, true)),
                    Err(StoreError::AlreadyExists(_)) => continue,
                    Err(e) => return Err(e),
                }
            }
            Observation::Known(Some(current)) => {
                verify_mirrors(forge, &current, &reference)?;
                return Ok((name, false));
            }
            Observation::Unknown { why } => return Err(StoreError::Unknown(why)),
        }
    }
    Err(StoreError::Busy(
        "ChangeRequest creation kept conflicting".into(),
    ))
}

/// `fleet declare TASK --json OBJECT`: append any JSON object with a string
/// `kind` to `status.declared`; Fleet validates nothing else. The same
/// object again is a no-op.
pub fn declare(
    store: &dyn Store,
    task_name: &str,
    item: Value,
) -> Result<DeclareOutcome, StoreError> {
    validate_declaration(&item).map_err(StoreError::Invalid)?;
    append_declaration(store, task_name, item.clone(), |existing| *existing == item)
}

/// Both forms are accepted after the brief has a receipt and before
/// deletion, and conclude nothing.
fn eligible_for_declare(store: &dyn Store, name: &str) -> Result<(Object, TaskStatus), StoreError> {
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
    let st: TaskStatus = if obj.status.is_null() {
        TaskStatus::default()
    } else {
        typed(task::KIND, &obj.status)?
    };
    if st.brief.is_none() {
        return Err(StoreError::Forbidden(format!(
            "{}: no brief has a receipt yet; a declaration comes from a briefed worker",
            obj.key()
        )));
    }
    Ok((obj, st))
}

fn append_declaration(
    store: &dyn Store,
    name: &str,
    item: Value,
    same: impl Fn(&Value) -> bool,
) -> Result<DeclareOutcome, StoreError> {
    for _ in 0..CAS_TRIES {
        let (obj, mut st) = eligible_for_declare(store, name)?;
        if st.declared.iter().any(&same) {
            return Ok(DeclareOutcome::Unchanged);
        }
        st.declared.push(item.clone());
        let status = serde_json::to_value(&st).map_err(|e| StoreError::Invalid(e.to_string()))?;
        match store.update_status(
            task::KIND,
            name,
            status,
            obj.metadata.resource_version,
            FieldManager::Worker,
            None,
        ) {
            Ok(_) => return Ok(DeclareOutcome::Declared),
            Err(StoreError::Conflict { .. }) => continue,
            Err(e) => return Err(e),
        }
    }
    Err(StoreError::Busy(format!(
        "{}/{name}: kept conflicting with other writers",
        task::KIND
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
