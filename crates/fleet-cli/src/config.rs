//! The one configuration file: versioned, typed, read through this loader
//! only. The one environment variable is the file's own location.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::Deserialize;

/// The config schema version this build reads (P9).
pub const CONFIG_VERSION: u32 = 1;

/// The one fleet setting read from the environment: where the config file
/// is. (The default data directory follows the platform's own variables.)
pub const CONFIG_ENV: &str = "FLEET_CONFIG";

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Config {
    pub version: u32,
    #[serde(default)]
    pub data: Data,
    #[serde(default)]
    pub runtime: Option<RuntimeConfig>,
    #[serde(default)]
    pub starter: Option<StarterConfig>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StarterConfig {
    pub workspace: PathBuf,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Data {
    pub dir: Option<PathBuf>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeConfig {
    /// `thurbox`, or `fake` for tests and trials.
    pub kind: String,
    /// The fake host's directory.
    pub dir: Option<PathBuf>,
    /// thurbox: the `thurbox-cli` executable, a path or a name on PATH.
    pub cli: Option<String>,
    /// thurbox: the bound on every call.
    pub call_timeout_ms: Option<u64>,
    /// thurbox: the prefix of every session key; give a second fleet on the
    /// same hosts its own.
    pub key_prefix: Option<String>,
    /// thurbox: the variable each harness reads `Task.spec.model` from.
    #[serde(default)]
    pub model_env: BTreeMap<String, String>,
}

impl Config {
    pub fn load(path: &Path) -> Result<Config, String> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| format!("cannot read config {}: {e}", path.display()))?;
        let mut config: Config =
            toml::from_str(&text).map_err(|e| format!("config {}: {e}", path.display()))?;
        if config.version != CONFIG_VERSION {
            return Err(format!(
                "config {}: version {} is not served; this build reads version {CONFIG_VERSION}",
                path.display(),
                config.version
            ));
        }
        // Relative paths in the file are relative to the file, not to the
        // directory fleet happens to run in.
        let base = path.parent().unwrap_or(Path::new("."));
        if let Some(d) = &config.data.dir {
            config.data.dir = Some(base.join(d));
        }
        if let Some(rt) = config.runtime.as_mut()
            && let Some(d) = &rt.dir
        {
            rt.dir = Some(base.join(d));
        }
        if let Some(starter) = config.starter.as_mut() {
            starter.workspace = base.join(&starter.workspace);
        }
        Ok(config)
    }

    pub fn empty() -> Config {
        Config {
            version: CONFIG_VERSION,
            data: Data::default(),
            runtime: None,
            starter: None,
        }
    }
}

/// Explicit CLI path, then FLEET_CONFIG, then one native per-user location.
/// No search through the current directory or its parents.
pub fn discover(explicit: Option<PathBuf>) -> Option<PathBuf> {
    explicit
        .or_else(|| std::env::var_os(CONFIG_ENV).map(PathBuf::from))
        .or_else(default_config_path)
}

pub fn default_config_path() -> Option<PathBuf> {
    if cfg!(windows) {
        return std::env::var_os("APPDATA")
            .filter(|d| !d.is_empty())
            .map(|d| PathBuf::from(d).join("fleet/config.toml"));
    }
    if let Some(d) = std::env::var_os("XDG_CONFIG_HOME").filter(|d| !d.is_empty()) {
        return Some(PathBuf::from(d).join("fleet/config.toml"));
    }
    std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config/fleet/config.toml"))
}

/// A dedicated data directory per platform: `$XDG_DATA_HOME/fleet`, `~/.local/share/fleet`, or
/// `%LOCALAPPDATA%\fleet`.
pub fn default_data_dir() -> Option<PathBuf> {
    if cfg!(windows) {
        return std::env::var_os("LOCALAPPDATA").map(|d| PathBuf::from(d).join("fleet"));
    }
    if let Some(d) = std::env::var_os("XDG_DATA_HOME").filter(|d| !d.is_empty()) {
        return Some(PathBuf::from(d).join("fleet"));
    }
    std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local").join("share").join("fleet"))
}
