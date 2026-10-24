//! Resolve passive launch configuration before the first runtime effect.
use fleet_api::config::{
    ConfigSource, ENV_PROVIDER, EnvProviderSpec, Execution, TASK_CONFIG, TaskConfigSpec, merge_env,
};
use fleet_api::runtime::Runtime;
use fleet_api::task::{LOCAL_HOST, TaskSpec};
use fleet_api::validate::{typed, validate_env, validate_execution};
use fleet_api::{Object, Observation, Store, StoreError};

fn source(
    store: &dyn Store,
    kind: &str,
    name: &str,
    identities: &mut Vec<ConfigSource>,
) -> Result<Object, StoreError> {
    let obj = match store.get(kind, name) {
        Observation::Known(Some(o)) if !o.is_deleting() => o,
        Observation::Known(_) => {
            return Err(StoreError::Invalid(format!(
                "{kind}/{name} missing or deleting"
            )));
        }
        Observation::Unknown { .. } => {
            return Err(StoreError::Unknown(format!("{kind}/{name} unavailable")));
        }
    };
    identities.push(ConfigSource {
        kind: kind.into(),
        name: name.into(),
        uid: obj.metadata.uid.clone(),
        generation: obj.metadata.generation,
    });
    Ok(obj)
}

pub(crate) fn resolve(
    store: &dyn Store,
    runtime: &dyn Runtime,
    task: &Object,
    lease: &mut fleet_api::LeaseToken,
    ttl: u64,
) -> Result<Execution, StoreError> {
    let spec: TaskSpec = typed("Task", &task.spec)?;
    let mut sources = Vec::new();
    let defaults: TaskConfigSpec = if let Some(r) = &spec.config_ref {
        typed(
            TASK_CONFIG,
            &source(store, TASK_CONFIG, &r.name, &mut sources)?.spec,
        )?
    } else {
        TaskConfigSpec::default()
    };
    // Validate every reference before executing any external provider command.
    let mut providers = Vec::new();
    for r in &defaults.env_from {
        let obj = source(store, ENV_PROVIDER, &r.name, &mut sources)?;
        let provider: EnvProviderSpec = typed(ENV_PROVIDER, &obj.spec)?;
        providers.push((r.name.clone(), provider));
    }
    let harness = if task.spec.get("harness").is_some() {
        spec.harness
    } else {
        defaults.harness.unwrap_or_default()
    };
    let host = if task.spec.get("host").is_some() {
        spec.host
    } else {
        defaults.host.unwrap_or_else(|| LOCAL_HOST.into())
    };
    let model = if task.spec.get("model").is_some() {
        spec.model
    } else {
        defaults.model
    };
    validate_execution(Some(&harness), Some(&host), model.as_deref())?;
    let mut env = std::collections::BTreeMap::new();
    for (name, provider) in providers {
        let mut data = provider.data;
        for (index, s) in provider.sources.iter().enumerate() {
            if matches!(s, fleet_api::config::EnvSource::Command { .. }) && ttl <= 20_000 {
                return Err(StoreError::Invalid(
                    "environment command requires a manager lease above 20000 ms".into(),
                ));
            }
            *lease = store.renew_lease(lease, ttl)?;
            match runtime.env_source(s) {
                Observation::Known(map) => merge_env(&mut data, &map),
                Observation::Unknown { why } => {
                    return Err(StoreError::Unknown(format!(
                        "EnvProvider/{} source {index}: {why}",
                        name
                    )));
                }
            }
            validate_env(&data)?;
        }
        merge_env(&mut env, &data);
    }
    merge_env(&mut env, &defaults.env);
    merge_env(&mut env, &spec.env);
    validate_env(&env)?;
    match runtime.prepare_execution(&Execution {
        harness,
        host,
        model,
        env,
        sources,
    }) {
        Observation::Known(e) => Ok(e),
        Observation::Unknown { why } => Err(StoreError::Unknown(why)),
    }
}
