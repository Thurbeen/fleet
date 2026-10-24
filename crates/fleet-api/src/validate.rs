//! Validation on write: every object the store accepts passes these.

use serde::de::DeserializeOwned;
use serde_json::Value;

use crate::kinds::is_served;
use crate::meta::{API_VERSION, Object};
use crate::store::StoreError;
use crate::task::{self, TaskSpec};

/// DNS-subdomain-like: lowercase alphanumerics, `-` and `.`, at most 253,
/// starting and ending with an alphanumeric. Names are file names in the
/// store, so this is also what keeps a name from escaping its directory.
pub fn validate_name(name: &str) -> Result<(), StoreError> {
    let ok_char = |c: char| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-' || c == '.';
    let edge = |c: Option<char>| c.is_some_and(|c| c.is_ascii_lowercase() || c.is_ascii_digit());
    if name.is_empty()
        || name.len() > 253
        || !name.chars().all(ok_char)
        || !edge(name.chars().next())
        || !edge(name.chars().last())
        || name.contains("..")
    {
        return Err(StoreError::Invalid(format!(
            "name {name:?}: use 1-253 lowercase letters, digits, '-' or '.', starting and ending with a letter or digit"
        )));
    }
    Ok(())
}

/// Refuse a kind this build does not serve.
pub fn validate_kind(kind: &str) -> Result<(), StoreError> {
    if is_served(kind) {
        Ok(())
    } else {
        Err(StoreError::Invalid(format!("unknown kind {kind:?}")))
    }
}

pub fn validate_object(obj: &Object) -> Result<(), StoreError> {
    if obj.api_version != API_VERSION {
        return Err(StoreError::Invalid(format!(
            "apiVersion {:?} is not served; this build reads and writes {API_VERSION}",
            obj.api_version
        )));
    }
    validate_kind(&obj.kind)?;
    validate_name(&obj.metadata.name)?;
    for o in &obj.metadata.owner_references {
        validate_name(&o.name)?;
    }
    validate_spec(&obj.kind, &obj.spec)
}

pub fn validate_spec(kind: &str, spec: &Value) -> Result<(), StoreError> {
    validate_kind(kind)?;
    let s: TaskSpec = typed(kind, spec)?;
    for (field, value) in [
        ("spec.harness", &s.harness),
        ("spec.host", &s.host),
        ("spec.brief", &s.brief),
        ("spec.workspace.dir", &s.workspace.dir),
    ] {
        if value.trim().is_empty() {
            return Err(StoreError::Invalid(format!("{field} must not be empty")));
        }
    }
    // Both reach the runtime as arguments: never as a flag.
    for (field, value) in [("spec.harness", &s.harness), ("spec.host", &s.host)] {
        if value.starts_with('-') {
            return Err(StoreError::Invalid(format!(
                "{field} must not start with '-'"
            )));
        }
    }
    Ok(())
}

/// A Task's spec is fixed once created, like a Pod's: only `dispatch` may
/// change. A different brief is a different Task.
pub fn validate_spec_update(kind: &str, old: &Value, new: &Value) -> Result<(), StoreError> {
    validate_spec(kind, new)?;
    let mut a: TaskSpec = typed(kind, old)?;
    let b: TaskSpec = typed(kind, new)?;
    a.dispatch = b.dispatch;
    if a != b {
        return Err(StoreError::Invalid(
            "Task spec is immutable except spec.dispatch; create a new Task for a different brief"
                .into(),
        ));
    }
    Ok(())
}

pub fn typed<T: DeserializeOwned>(kind: &str, spec: &Value) -> Result<T, StoreError> {
    serde_json::from_value(spec.clone())
        .map_err(|e| StoreError::Invalid(format!("{kind} spec: {e}")))
}

/// Status must parse as the kind's typed status: a writer cannot store a
/// shape its readers would refuse.
pub fn validate_status(kind: &str, status: &Value) -> Result<(), StoreError> {
    if status.is_null() {
        return Ok(());
    }
    validate_kind(kind)?;
    serde_json::from_value::<task::TaskStatus>(status.clone())
        .map(|_| ())
        .map_err(|e| StoreError::Invalid(format!("Task status: {e}")))
}
