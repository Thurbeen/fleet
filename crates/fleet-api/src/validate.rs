//! Validation on write: every object the store accepts passes these.

use serde::de::DeserializeOwned;
use serde_json::Value;

use crate::config::{ENV_PROVIDER, EnvProviderSpec, EnvSource, TASK_CONFIG, TaskConfigSpec};
use crate::kinds::is_served;
use crate::meta::{API_VERSION, Object};
use crate::store::StoreError;
use crate::task::{self, TaskSpec};
use std::collections::{BTreeMap, BTreeSet};

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
    match kind {
        TASK_CONFIG => {
            let s: TaskConfigSpec = typed(kind, spec)?;
            validate_execution(s.harness.as_deref(), s.host.as_deref(), s.model.as_deref())?;
            validate_env(&s.env)?;
            for r in &s.env_from {
                validate_name(&r.name)?;
            }
            return Ok(());
        }
        ENV_PROVIDER => {
            let s: EnvProviderSpec = typed(kind, spec)?;
            validate_env(&s.data)?;
            if s.sources.len() > 32 {
                return Err(StoreError::Invalid("too many environment sources".into()));
            }
            for source in s.sources {
                match source {
                    EnvSource::File { path } => validate_absolute(&path)?,
                    EnvSource::Command {
                        program,
                        args,
                        cwd,
                        timeout_ms,
                    } => {
                        validate_absolute(&program)?;
                        validate_absolute(&cwd)?;
                        if !(1..=15_000).contains(&timeout_ms)
                            || args.iter().any(|a| a.contains('\0'))
                        {
                            return Err(StoreError::Invalid(
                                "invalid environment command timeout or arguments".into(),
                            ));
                        }
                    }
                }
            }
            return Ok(());
        }
        _ => {}
    }
    let s: TaskSpec = typed(kind, spec)?;
    if let Some(r) = &s.config_ref {
        validate_name(&r.name)?;
    }
    validate_env(&s.env)?;
    validate_execution(
        if s.config_ref.is_some() && !spec.as_object().is_some_and(|m| m.contains_key("harness")) {
            None
        } else {
            Some(&s.harness)
        },
        Some(&s.host),
        s.model.as_deref(),
    )?;
    for (field, value) in [
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
    if kind != task::KIND {
        return Ok(());
    }
    let mut a: TaskSpec = typed(kind, old)?;
    let b: TaskSpec = typed(kind, new)?;
    // Explicit presence affects inheritance, so preserve it in immutability checks.
    for field in ["harness", "host", "model"] {
        if old.get(field).is_some() != new.get(field).is_some() {
            return Err(StoreError::Invalid(
                "Task execution field presence is immutable".into(),
            ));
        }
    }
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
    if kind != task::KIND {
        return Err(StoreError::Invalid(
            "passive configuration has no status".into(),
        ));
    }
    serde_json::from_value::<task::TaskStatus>(status.clone())
        .map(|_| ())
        .map_err(|e| StoreError::Invalid(format!("Task status: {e}")))
}

pub fn validate_execution(
    harness: Option<&str>,
    host: Option<&str>,
    model: Option<&str>,
) -> Result<(), StoreError> {
    for (field, value) in [("harness", harness), ("host", host), ("model", model)]
        .into_iter()
        .filter_map(|(f, v)| v.map(|v| (f, v)))
    {
        if value.trim().is_empty() || value.starts_with('-') || value.contains('\0') {
            return Err(StoreError::Invalid(format!(
                "invalid {field}: empty, flag-shaped or NUL"
            )));
        }
    }
    Ok(())
}

pub fn validate_env(env: &BTreeMap<String, String>) -> Result<(), StoreError> {
    let mut names = BTreeSet::new();
    let mut bytes = 0;
    for (k, v) in env {
        let mut chars = k.chars();
        if !chars
            .next()
            .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
            || !chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
            || v.contains('\0')
            || !names.insert(k.to_ascii_uppercase())
        {
            return Err(StoreError::Invalid(
                "invalid environment key, NUL value or case collision".into(),
            ));
        }
        bytes += k.len() + v.len();
    }
    if bytes > crate::config::SOURCE_CAP {
        return Err(StoreError::Invalid("environment exceeds 64 KiB".into()));
    }
    Ok(())
}

fn validate_absolute(path: &str) -> Result<(), StoreError> {
    // Platform-native interpretation belongs to the adapter; allow both portable forms.
    let bytes = path.as_bytes();
    if path.contains('\0')
        || !(path.starts_with('/')
            || (bytes.len() > 2
                && bytes[0].is_ascii_alphabetic()
                && bytes[1] == b':'
                && matches!(bytes[2], b'/' | b'\\')))
    {
        return Err(StoreError::Invalid(
            "environment source paths must be absolute".into(),
        ));
    }
    Ok(())
}
