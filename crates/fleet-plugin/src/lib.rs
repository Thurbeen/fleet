//! The process adapter. Controller calls receive only the OS startup environment.
use fleet_api::plugin::{PluginConfig, Plugins};
use fleet_exec::{Spec, run, worst_case};
use serde_json::Value;
use std::collections::BTreeMap;
use std::time::Duration;

#[derive(Debug)]
pub struct StdioPlugins {
    configs: BTreeMap<String, PluginConfig>,
    failures: std::cell::RefCell<BTreeMap<String, u32>>,
}
impl StdioPlugins {
    pub fn new(configs: BTreeMap<String, PluginConfig>) -> Self {
        Self {
            configs,
            failures: std::cell::RefCell::new(BTreeMap::new()),
        }
    }
}
impl Plugins for StdioPlugins {
    fn available(&self, plugin: &str) -> bool {
        self.failures.borrow().get(plugin).copied().unwrap_or(0) < 3
    }
    fn operation_bound(&self) -> u64 {
        self.configs
            .values()
            .map(|c| worst_case(Duration::from_millis(c.call_timeout_ms)).as_millis() as u64)
            .max()
            .unwrap_or(0)
    }
    fn call(
        &self,
        plugin: &str,
        call: &str,
        request: Value,
        env: &BTreeMap<String, String>,
    ) -> Result<Value, String> {
        let result = self.invoke(plugin, call, request, env);
        let mut failures = self.failures.borrow_mut();
        let count = failures.entry(plugin.into()).or_default();
        if result.is_ok() {
            *count = 0;
        } else {
            *count = count.saturating_add(1);
        }
        result
    }
}
impl StdioPlugins {
    fn invoke(
        &self,
        plugin: &str,
        call: &str,
        request: Value,
        env: &BTreeMap<String, String>,
    ) -> Result<Value, String> {
        let config = self.configs.get(plugin).ok_or("unregistered plugin")?;
        if !["manifest", "probes", "reconcile", "observe", "perform"].contains(&call) {
            return Err("unknown plugin call".into());
        }
        let mut spec = Spec::new(
            &config.program,
            Duration::from_millis(config.call_timeout_ms),
        )
        .args(&config.args)
        .arg(call);
        spec.clear_env = true;
        spec.output_limit = Some(8 * 1024 * 1024);
        spec.cwd = Some((&config.cwd).into());
        for name in ["PATH", "SYSTEMROOT"] {
            if (name == "PATH" || cfg!(windows))
                && let Ok(value) = std::env::var(name)
            {
                spec.env.push((name.into(), value));
            }
        }
        if matches!(call, "observe" | "perform") {
            let mut observer = BTreeMap::new();
            for name in &config.pass_env {
                if let Ok(value) = std::env::var(name) {
                    observer.insert(name.clone(), value);
                }
            }
            fleet_api::config::merge_env(&mut observer, env);
            spec.env.extend(observer);
        }
        let mut bytes = serde_json::to_vec(&request).map_err(|_| "plugin request encoding")?;
        bytes.push(b'\n');
        spec.stdin = Some(bytes);
        let outcome = run(&spec);
        let output = outcome
            .success_text()
            .ok_or_else(|| format!("plugin {call}: {}", outcome.failure()))?;
        if output.len() > 8 * 1024 * 1024 {
            return Err("plugin stdout exceeds 8 MiB".into());
        }
        let answer: Value = serde_json::from_str(&output).map_err(|_| "malformed plugin answer")?;
        if answer["id"] != request["id"] || answer["ok"] != true {
            return Err("plugin refused request or returned an unmatched id".into());
        }
        Ok(answer)
    }
}
