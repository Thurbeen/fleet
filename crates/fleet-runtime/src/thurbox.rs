//! The runtime contract served by `thurbox-cli` (Q26, n9): sessions on this
//! machine, or on any host thurbox's own `hosts.toml` reaches over SSH or in
//! WSL. Thurbox owns how a host is reached; fleet passes `--host <name>` and
//! nothing else about it.
//!
//! Every call is one bounded `thurbox-cli … --json` through fleet-exec, so a
//! hung call costs `call_timeout` and leaves no process behind, and a call
//! that times out is Unknown, never a guess.
//!
//! Identity. A session's thurbox key is `<keyPrefix>.<uid>`: the Task's uid
//! is unique in its store and short enough for thurbox's 64-character keys,
//! and the prefix keeps two fleets on one host from adopting each other's
//! sessions. Its name, for people, is the Task's name. `--on-existing adopt`
//! makes a repeated create return the session that holds the key.
//!
//! The brief is sent once under its intent key, recorded in the session's
//! own metadata (`session meta`): `fleet.brief.pending` before the send,
//! `fleet.brief` after it. Read back, the pair says delivered, not delivered,
//! or "sent, unconfirmed", which is Unknown and never a second send.

use std::collections::BTreeMap;
use std::ffi::OsString;
use std::time::Duration;

use fleet_api::Observation;
use fleet_api::plan::{EffectOutcome, SpawnSpec};
use fleet_api::runtime::Runtime;
use fleet_api::task::{
    LOCAL_HOST as LOCAL, PrimaryWorktree, RuntimeWorkspace, RuntimeWorktree, SessionRef, Workspace,
    uid_of_session_key,
};
use fleet_exec::{Outcome, Spec, run};
use serde_json::Value;

/// The longest brief sent as one argument: Windows caps a whole command line
/// near 32 767 characters.
pub const MAX_BRIEF: usize = 24_000;

/// The most thurbox-cli calls one operation makes: a brief (meta get, meta
/// set, send, meta set).
pub const MAX_CALLS_PER_OPERATION: u32 = 4;

const BRIEF_META: &str = "fleet.brief";

#[derive(Debug, Clone)]
pub struct ThurboxConfig {
    /// The `thurbox-cli` executable: a path, or a name found on PATH.
    pub cli: OsString,
    pub call_timeout: Duration,
    /// Prefix of every thurbox key fleet gives a session.
    pub key_prefix: String,
    /// The variable each harness reads its model from, for `Task.spec.model`.
    pub model_env: BTreeMap<String, String>,
}

impl ThurboxConfig {
    pub fn new(cli: impl Into<OsString>) -> ThurboxConfig {
        ThurboxConfig {
            cli: cli.into(),
            call_timeout: Duration::from_secs(30),
            key_prefix: "fleet".into(),
            model_env: BTreeMap::from([
                ("claude".into(), "ANTHROPIC_MODEL".into()),
                ("claude-code".into(), "ANTHROPIC_MODEL".into()),
            ]),
        }
    }
}

#[derive(Debug, Clone)]
pub struct ThurboxRuntime {
    cfg: ThurboxConfig,
}

/// One call's answer: the JSON it printed, or why there is none.
enum Answer {
    Ok(String),
    /// thurbox-cli answered no; the first line of what it said.
    No(String),
    Unknown(String),
}

fn first_line(bytes: &[u8]) -> String {
    let text = String::from_utf8_lossy(bytes);
    let line = text
        .lines()
        .find(|l| !l.trim().is_empty())
        .unwrap_or("")
        .trim();
    line.chars().take(200).collect()
}

impl ThurboxRuntime {
    pub fn new(cfg: ThurboxConfig) -> ThurboxRuntime {
        ThurboxRuntime { cfg }
    }

    pub fn thurbox_key(&self, key: &str) -> String {
        format!("{}.{}", self.cfg.key_prefix, uid_of_session_key(key))
    }

    fn call(&self, args: &[&str]) -> Answer {
        self.call_spec(Spec::new(&self.cfg.cli, self.cfg.call_timeout).args(args))
    }

    fn call_spec(&self, spec: Spec) -> Answer {
        let what = spec
            .args
            .iter()
            .take(2)
            .map(|a| a.to_string_lossy().into_owned())
            .collect::<Vec<_>>()
            .join(" ");
        match run(&spec) {
            Outcome::Exited {
                code: Some(0),
                stdout,
                ..
            } => Answer::Ok(String::from_utf8_lossy(&stdout).into_owned()),
            Outcome::Exited {
                code: Some(_),
                stderr,
                ..
            } => Answer::No(first_line(&stderr)),
            other => Answer::Unknown(format!("thurbox-cli {what}: {}", other.failure())),
        }
    }

    fn json(&self, args: &[&str]) -> Result<Option<Value>, String> {
        match self.call(args) {
            Answer::Ok(out) => serde_json::from_str(out.trim())
                .map(Some)
                .map_err(|e| format!("thurbox-cli {}: unreadable answer: {e}", args.join(" "))),
            Answer::No(_) => Ok(None),
            Answer::Unknown(why) => Err(why),
        }
    }

    /// The live rows carrying a thurbox key, on `host` only. Unknown when the
    /// list cannot be read, or when thurbox itself says the host's sessions
    /// cannot be reached: a mirrored row of an unreachable host is not proof
    /// of anything.
    fn rows(&self, host: &str, filter: (&str, &str)) -> Observation<Vec<Value>> {
        let rows = match self.call(&["session", "list", "--json", filter.0, filter.1]) {
            Answer::Ok(out) => match serde_json::from_str::<Value>(out.trim()) {
                Ok(Value::Array(rows)) => rows,
                Ok(_) => return Observation::unknown("thurbox-cli session list: not a list"),
                Err(e) => return Observation::unknown(format!("thurbox-cli session list: {e}")),
            },
            Answer::No(why) => {
                return Observation::unknown(format!("thurbox-cli session list: {why}"));
            }
            Answer::Unknown(why) => return Observation::unknown(why),
        };
        let mine: Vec<Value> = rows.into_iter().filter(|r| row_host(r) == host).collect();
        if mine.iter().any(|r| r["state"] == "unreachable") {
            return Observation::unknown(format!("host {host}: thurbox cannot reach its sessions"));
        }
        Observation::Known(mine)
    }

    /// The session's own row, by id. Unknown when it cannot be read.
    fn get(&self, session: &SessionRef) -> Observation<Value> {
        match self.call(&["session", "get", &session.id, "--json"]) {
            Answer::Ok(out) => match serde_json::from_str::<Value>(out.trim()) {
                Ok(v) if v["state"] == "unreachable" => {
                    Observation::unknown(format!("host {}: unreachable", session.host))
                }
                Ok(v) => Observation::Known(v),
                Err(e) => Observation::unknown(format!("thurbox-cli session get: {e}")),
            },
            Answer::No(why) => Observation::unknown(format!("session {}: {why}", session.id)),
            Answer::Unknown(why) => Observation::unknown(why),
        }
    }

    /// Before its first turn Codex may not have emitted any lifecycle hook.
    /// Process liveness is insufficient: prove the initial empty composer at
    /// the visible cursor, never by searching scrollback for an old prompt.
    fn initial_codex_ready(&self, session: &SessionRef, row: &Value) -> Observation<bool> {
        if row["id"] != session.id
            || row["key"] != self.thurbox_key(&session.key)
            || row_host(row) != session.host
            || row["agent"] != "codex"
            || row["state"] != "running"
            || row["state_source"] != "process"
            || row["stopped"] != false
            || row["hook_reported"] != false
            || !row["hook_state"].is_null()
            || row["hook_state_contradicted"] != false
            || row["hook_corroboration"] != "agent"
            || !codex_process(&row["foreground_process"])
        {
            return Observation::unknown("session has no initial Codex readiness evidence");
        }
        let capture =
            match self.json(&["session", "capture", &session.id, "--lines", "0", "--json"]) {
                Ok(Some(v)) => v,
                Ok(None) => return Observation::unknown("initial composer capture refused"),
                Err(why) => return Observation::unknown(why),
            };
        let Some(output) = capture["output"].as_str() else {
            return Observation::unknown("initial composer capture has no output");
        };
        let cursor_line = capture["cursor_row"]
            .as_u64()
            .and_then(|n| usize::try_from(n).ok())
            .and_then(|n| output.lines().nth(n));
        if capture["session_id"] == session.id
            && capture["ansi"] == false
            && capture["lines"] == 0
            && codex_process(&capture["foreground_process"])
            && capture["cursor_col"] == 2
            && cursor_line.map(str::trim_end) == Some("› Ask Codex to do anything")
            && output
                .lines()
                .any(|l| l.trim_start().starts_with(">_ OpenAI Codex ("))
            && output.lines().any(|l| {
                l.trim() == "To get started, describe a task or try one of these commands:"
            })
        {
            Observation::Known(true)
        } else {
            Observation::unknown("capture does not prove an empty initial Codex composer")
        }
    }

    fn meta(&self, session: &SessionRef, key: &str) -> Result<String, String> {
        match self.call(&["session", "meta", "get", &session.id, key]) {
            Answer::Ok(out) => Ok(out.trim().to_string()),
            Answer::No(why) => Err(format!("session meta {key}: {why}")),
            Answer::Unknown(why) => Err(why),
        }
    }

    /// Every key retains its own receipt; a later prompt cannot erase an
    /// earlier ambiguous send or the original brief's acknowledgement.
    fn delivery_journal(&self, session: &SessionRef) -> Result<BTreeMap<String, String>, String> {
        let value = self.meta(session, BRIEF_META)?;
        if value.is_empty() {
            return Ok(BTreeMap::new());
        }
        serde_json::from_str(&value).map_err(|e| format!("unreadable delivery journal: {e}"))
    }

    fn set_meta(&self, session: &SessionRef, key: &str, value: &str) -> Answer {
        // The value goes on stdin, as `meta set` reads it when none is given.
        self.call_spec(
            Spec::new(&self.cfg.cli, self.cfg.call_timeout)
                .args(["session", "meta", "set", &session.id, key])
                .stdin(value.as_bytes().to_vec()),
        )
    }

    fn state_word(&self, session: &SessionRef) -> Observation<String> {
        self.get(session).map(|v| {
            if v["stopped"] == true {
                "stopped".to_string()
            } else {
                v["state"].as_str().unwrap_or("").to_string()
            }
        })
    }

    fn find_checked(
        &self,
        host: &str,
        key: &str,
        workspace: Option<&Workspace>,
    ) -> Observation<Option<SessionRef>> {
        let tk = self.thurbox_key(key);
        match self.rows(host, ("--key", &tk)) {
            Observation::Known(rows) => match rows.as_slice() {
                [] => Observation::Known(None),
                [row] => match (
                    row["id"].as_str(),
                    workspace.map_or(Ok(None), |workspace| resolved_workspace(row, workspace)),
                ) {
                    (Some(id), Ok(resolved)) => Observation::Known(Some(SessionRef {
                        id: id.to_string(),
                        host: host.to_string(),
                        key: key.to_string(),
                        workspace: resolved,
                    })),
                    (None, _) => Observation::unknown("a session row without an id"),
                    (_, Err(why)) => Observation::unknown(why),
                },
                _ => Observation::unknown(format!("key {tk} is held by more than one session")),
            },
            Observation::Unknown { why } => Observation::Unknown { why },
        }
    }
}

fn codex_process(value: &Value) -> bool {
    value
        .as_str()
        .is_some_and(|p| matches!(p.rsplit(['/', '\\']).next(), Some("codex" | "codex.exe")))
}

/// Absolute on the host the path is for: a POSIX path, or a Windows drive
/// or UNC path, whatever this machine is.
fn is_absolute(dir: &str) -> bool {
    let b = dir.as_bytes();
    dir.starts_with('/')
        || dir.starts_with("\\\\")
        || (b.len() > 2
            && b[0].is_ascii_alphabetic()
            && b[1] == b':'
            && matches!(b[2], b'\\' | b'/'))
}

fn validate_workspace(workspace: &Workspace) -> Result<(), String> {
    let paths = std::iter::once(("workspace.dir".to_string(), &workspace.dir))
        .chain(
            workspace
                .additional_dirs
                .iter()
                .enumerate()
                .map(|(i, dir)| (format!("workspace.additionalDirs[{i}]"), dir)),
        )
        .chain(workspace.worktree.iter().flat_map(|worktree| {
            worktree
                .additional_repositories
                .iter()
                .enumerate()
                .map(|(i, repository)| {
                    (
                        format!("workspace.worktree.additionalRepositories[{i}].path"),
                        &repository.path,
                    )
                })
        }));
    for (field, dir) in paths {
        if !is_absolute(dir) {
            return Err(format!(
                "{field} {dir:?} must be an absolute path on the host for the thurbox runtime"
            ));
        }
    }
    Ok(())
}

fn resolved_workspace(
    row: &Value,
    workspace: &Workspace,
) -> Result<Option<RuntimeWorkspace>, String> {
    let Some(dir) = row["cwd"].as_str() else {
        return Err("session row has no workspace directory".into());
    };
    let additional_dirs = match row.get("additional_dirs") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(dirs)) => dirs
            .iter()
            .map(|dir| {
                dir.as_str()
                    .map(str::to_string)
                    .ok_or_else(|| "session row has an invalid additional directory".to_string())
            })
            .collect::<Result<Vec<_>, _>>()?,
        Some(_) => return Err("session row has invalid additional directories".into()),
    };
    if additional_dirs != workspace.additional_dirs {
        return Err("the session holding the key has conflicting workspace membership".into());
    }
    let Some(request) = &workspace.worktree else {
        return if dir == workspace.dir {
            Ok(None)
        } else {
            Err("the session holding the key has conflicting workspace membership".into())
        };
    };
    let base_branch = row["base_branch"]
        .as_str()
        .ok_or_else(|| "session row cannot verify the provisioned base branch".to_string())?;
    if base_branch != request.base_branch {
        return Err("the session holding the key has a conflicting base branch".into());
    }
    let rows = row["worktrees"]
        .as_array()
        .ok_or_else(|| "session row cannot verify provisioned worktree membership".to_string())?;
    let repositories: Vec<&str> = std::iter::once(workspace.dir.as_str())
        .chain(
            request
                .additional_repositories
                .iter()
                .map(|repository| repository.path.as_str()),
        )
        .collect();
    if rows.len() != repositories.len() {
        return Err("the session holding the key has conflicting worktree membership".into());
    }
    let mut worktrees = Vec::with_capacity(rows.len());
    for (index, (worktree, repository)) in rows.iter().zip(repositories).enumerate() {
        let actual_repository = worktree["repo_path"]
            .as_str()
            .ok_or_else(|| "session row has a worktree without a repository path".to_string())?;
        let path = worktree["worktree_path"]
            .as_str()
            .ok_or_else(|| "session row has a worktree without a resolved path".to_string())?;
        let branch = worktree["branch"]
            .as_str()
            .ok_or_else(|| "session row has a worktree without a branch".to_string())?;
        let created = worktree["created_by_thurbox"]
            .as_bool()
            .ok_or_else(|| "session row cannot verify worktree ownership".to_string())?;
        let expected_created = index != 0 || request.primary == PrimaryWorktree::Provision;
        if actual_repository != repository
            || branch != request.branch
            || created != expected_created
            || (index == 0 && path != dir)
            || !is_absolute(path)
        {
            return Err("the session holding the key has conflicting worktree membership".into());
        }
        worktrees.push(RuntimeWorktree {
            repository: actual_repository.to_string(),
            path: path.to_string(),
            branch: branch.to_string(),
            created_by_runtime: created,
        });
    }
    Ok(Some(RuntimeWorkspace {
        cwd: dir.to_string(),
        base_branch: base_branch.to_string(),
        worktrees,
        additional_dirs,
    }))
}

fn row_host(row: &Value) -> &str {
    row["host"]
        .as_str()
        .filter(|h| !h.is_empty())
        .unwrap_or(LOCAL)
}

impl Runtime for ThurboxRuntime {
    fn prepare_execution(
        &self,
        execution: &fleet_api::config::Execution,
    ) -> Observation<fleet_api::config::Execution> {
        let mut result = execution.clone();
        if let Some(model) = &execution.model {
            let Some(var) = self.cfg.model_env.get(&execution.harness) else {
                return Observation::unknown(
                    "no model variable configured for harness; set runtime.modelEnv",
                );
            };
            let map = BTreeMap::from([(var.clone(), model.clone())]);
            if fleet_api::validate::validate_env(&map).is_err() {
                return Observation::unknown("invalid model environment mapping");
            }
            fleet_api::config::merge_env(&mut result.env, &map);
        }
        if fleet_api::validate::validate_env(&result.env).is_err() {
            return Observation::unknown("launch environment exceeds bounds");
        }
        Observation::Known(result)
    }

    fn env_source(
        &self,
        source: &fleet_api::config::EnvSource,
    ) -> Observation<std::collections::BTreeMap<String, String>> {
        crate::resolve_env_source(source)
    }

    fn operation_bound(&self) -> fleet_api::Millis {
        let one = fleet_exec::worst_case(self.cfg.call_timeout) * MAX_CALLS_PER_OPERATION;
        u64::try_from(one.as_millis()).unwrap_or(u64::MAX)
    }

    fn host(&self, host: &str) -> Observation<bool> {
        let doctor = match self.json(&["doctor", "--json"]) {
            Ok(Some(v)) => v,
            Ok(None) => return Observation::unknown("thurbox-cli doctor refused"),
            Err(why) => return Observation::unknown(why),
        };
        let check = |key: &str| {
            doctor["checks"]
                .as_array()
                .and_then(|cs| cs.iter().find(|c| c["key"] == key))
                .map(|c| c["level"].as_str().unwrap_or("").to_string())
        };
        let level = if host == LOCAL {
            check("multiplexer")
        } else {
            check(&format!("host:{host}"))
        };
        match level.as_deref() {
            Some("ok") | Some("warn") => {}
            // Not configured, or thurbox says its launcher is missing.
            _ => return Observation::Known(false),
        }
        if host == LOCAL {
            return Observation::Known(true);
        }
        let prefix = format!("{}.", self.cfg.key_prefix);
        self.rows(host, ("--key-prefix", &prefix)).map(|_| true)
    }

    fn spawn(&self, spec: &SpawnSpec) -> Observation<EffectOutcome> {
        let workspace = Workspace {
            dir: spec.dir.clone(),
            additional_dirs: spec.additional_dirs.clone(),
            worktree: spec.worktree.clone(),
        };
        // Thurbox resolves these paths on the target host. A relative path
        // would mean whatever directory fleet happened to run in.
        if let Err(why) = validate_workspace(&workspace) {
            return Observation::Known(EffectOutcome::Failed { why });
        }
        let key = self.thurbox_key(&spec.key);
        let name: String = spec
            .key
            .rsplit_once('.')
            .map_or(&*spec.key, |(n, _)| n)
            .chars()
            .take(64)
            .collect();
        let mut args: Vec<String> = [
            "session",
            "create",
            "--json",
            "--name",
            &name,
            "--repo-path",
            &spec.dir,
            "--key",
            &key,
            "--on-existing",
            "adopt",
            "--agent",
            &spec.harness,
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        for dir in &spec.additional_dirs {
            args.extend(["--add-dir".to_string(), dir.clone()]);
        }
        if let Some(worktree) = &spec.worktree {
            args.extend([
                "--worktree-branch".to_string(),
                worktree.branch.clone(),
                "--base-branch".to_string(),
                worktree.base_branch.clone(),
            ]);
            if worktree.primary == PrimaryWorktree::Existing {
                args.extend(["--existing-worktree".to_string(), spec.dir.clone()]);
            }
            for repository in &worktree.additional_repositories {
                args.extend([
                    "--add-repo".to_string(),
                    format!("{}@{}", repository.path, worktree.base_branch),
                ]);
            }
        }
        if spec.host != LOCAL {
            args.extend(["--host".to_string(), spec.host.clone()]);
        }
        let mut env = spec.env.clone();
        if let Some(model) = &spec.model
            && !spec.environment_frozen
        {
            let Some(var) = self.cfg.model_env.get(&spec.harness) else {
                return Observation::Known(EffectOutcome::Failed {
                    why: format!(
                        "no model variable is configured for harness {}; set runtime.modelEnv",
                        spec.harness
                    ),
                });
            };
            if fleet_api::validate::validate_env(&BTreeMap::from([(var.clone(), model.clone())]))
                .is_err()
            {
                return Observation::Known(EffectOutcome::Failed {
                    why: "invalid model environment variable".into(),
                });
            }
            fleet_api::config::merge_env(&mut env, &BTreeMap::from([(var.clone(), model.clone())]));
        }
        let answer = if env.is_empty() {
            let argv: Vec<&str> = args.iter().map(String::as_str).collect();
            self.call(&argv)
        } else {
            args.push("--env-stdin".into());
            let bytes = match serde_json::to_vec(&env) {
                Ok(b) => b,
                Err(_) => return Observation::unknown("environment encoding failed"),
            };
            self.call_spec(
                Spec::new(&self.cfg.cli, self.cfg.call_timeout)
                    .args(&args)
                    .stdin(bytes),
            )
        };
        match answer {
            Answer::Ok(out) => {
                let row: Value = match serde_json::from_str(out.trim()) {
                    Ok(v) => v,
                    Err(e) => {
                        return Observation::unknown(format!("thurbox-cli session create: {e}"));
                    }
                };
                let Some(id) = row["id"].as_str() else {
                    return Observation::unknown("thurbox-cli session create: no id in its answer");
                };
                let adopted = row["created"] == false;
                match self.find_workspace(&spec.host, &spec.key, &workspace) {
                    Observation::Known(Some(found)) if found.id == id => {
                        Observation::Known(EffectOutcome::Spawned {
                            session: found,
                            adopted,
                        })
                    }
                    Observation::Known(Some(_)) => Observation::unknown(
                        "session identity changed during workspace verification",
                    ),
                    Observation::Known(None) => {
                        Observation::unknown("session disappeared during workspace verification")
                    }
                    Observation::Unknown { why } => Observation::Unknown { why },
                }
            }
            Answer::No(why) => Observation::Known(EffectOutcome::Failed {
                why: format!("thurbox session creation refused: {why}"),
            }),
            Answer::Unknown(why) => Observation::unknown(why),
        }
    }

    fn find(&self, host: &str, key: &str) -> Observation<Option<SessionRef>> {
        self.find_checked(host, key, None)
    }

    fn find_workspace(
        &self,
        host: &str,
        key: &str,
        workspace: &Workspace,
    ) -> Observation<Option<SessionRef>> {
        self.find_checked(host, key, Some(workspace))
    }

    fn ready(&self, session: &SessionRef) -> Observation<bool> {
        match self.get(session) {
            Observation::Known(row) if row["stopped"] == true => Observation::Known(false),
            Observation::Known(row) => match row["state"].as_str() {
                Some("idle" | "done") => Observation::Known(true),
                Some("working" | "blocked" | "stopped") => Observation::Known(false),
                _ => self.initial_codex_ready(session, &row),
            },
            Observation::Unknown { why } => Observation::Unknown { why },
        }
    }

    fn brief(
        &self,
        session: &SessionRef,
        text: &str,
        intent_key: &str,
    ) -> Observation<EffectOutcome> {
        let mut journal = match self.delivery_journal(session) {
            Ok(journal) => journal,
            Err(why) => return Observation::unknown(why),
        };
        match journal.get(intent_key).map(String::as_str) {
            Some("delivered") => {
                return Observation::Known(EffectOutcome::Delivered { already: true });
            }
            Some(_) => {
                return Observation::unknown("prompt delivery is pending and cannot be repeated");
            }
            None => {}
        }
        if text.len() > MAX_BRIEF {
            return Observation::Known(EffectOutcome::Failed {
                why: format!(
                    "the brief is {} bytes; thurbox-cli send takes at most {MAX_BRIEF}",
                    text.len()
                ),
            });
        }
        journal.insert(intent_key.to_string(), "pending".into());
        let pending = serde_json::to_string(&journal).expect("string map");
        match self.set_meta(session, BRIEF_META, &pending) {
            Answer::Ok(_) => {}
            Answer::No(why) => return Observation::Known(EffectOutcome::Failed { why }),
            Answer::Unknown(why) => return Observation::unknown(why),
        }
        // `--`: a brief that starts with `-` is text, never a flag.
        match self.call(&["session", "send", &session.id, "--", text]) {
            Answer::Ok(_) => {}
            // `session send` can fail after it began typing, so a failure is
            // not proof that nothing arrived: never typed again on it.
            Answer::No(why) => {
                return Observation::unknown(format!(
                    "send failed and may have typed part of it: {why}"
                ));
            }
            Answer::Unknown(why) => return Observation::unknown(why),
        }
        journal.insert(intent_key.to_string(), "delivered".into());
        let delivered = serde_json::to_string(&journal).expect("string map");
        match self.set_meta(session, BRIEF_META, &delivered) {
            Answer::Ok(_) => Observation::Known(EffectOutcome::Delivered { already: false }),
            Answer::No(why) | Answer::Unknown(why) => {
                Observation::unknown(format!("sent, but the receipt was not recorded: {why}"))
            }
        }
    }

    fn brief_delivered(&self, session: &SessionRef, intent_key: &str) -> Observation<bool> {
        match self.delivery_journal(session) {
            Ok(journal) => match journal.get(intent_key).map(String::as_str) {
                Some("delivered") => Observation::Known(true),
                Some(_) => Observation::unknown(
                    "the prompt was being sent and its delivery was never confirmed",
                ),
                None => Observation::Known(false),
            },
            Err(why) => Observation::unknown(why),
        }
    }

    fn at_rest(&self, session: &SessionRef) -> Observation<bool> {
        match self.state_word(session) {
            Observation::Known(s) => match s.as_str() {
                "idle" | "done" | "stopped" => Observation::Known(true),
                "working" | "blocked" => Observation::Known(false),
                other => Observation::unknown(format!("session state {other:?} does not say")),
            },
            Observation::Unknown { why } => Observation::Unknown { why },
        }
    }

    fn release(&self, session: &SessionRef) -> Observation<EffectOutcome> {
        // The controller proved this exact session at rest. A reused key
        // cannot transfer that proof to another session.
        match self.find(&session.host, &session.key) {
            Observation::Known(None) => return Observation::Known(EffectOutcome::Released),
            Observation::Known(Some(s)) if s.id == session.id => {}
            Observation::Known(Some(_)) => {
                return Observation::unknown("the session key now belongs to a different session");
            }
            Observation::Unknown { why } => return Observation::Unknown { why },
        }
        match self.call(&["session", "delete", &session.id]) {
            Answer::Ok(_) => Observation::Known(EffectOutcome::Released),
            Answer::No(why) => Observation::Known(EffectOutcome::Failed { why }),
            Answer::Unknown(why) => Observation::unknown(why),
        }
    }
}
