//! Drives the real `fleet` binary against an isolated data directory and a
//! fake host, each test in its own directory under cargo's per-target temp
//! dir. The child environment is cleared, so no test reads the operator's
//! config, queue or HOME; every child is bounded and killed past it.

#![allow(dead_code)]

use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU32, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use fleet_runtime::{FakeHost, Fault};
use serde_json::{Value, json};

static SEQ: AtomicU32 = AtomicU32::new(0);
const TIMEOUT: Duration = Duration::from_secs(60);

pub struct Env {
    pub root: PathBuf,
    pub data: PathBuf,
    pub fake: PathBuf,
    pub config: PathBuf,
    tmp: PathBuf,
    /// When set, children get this as their whole PATH.
    path: Option<PathBuf>,
    /// Extra variables every child gets, on top of the cleared environment.
    vars: Vec<(String, String)>,
}

#[derive(Debug)]
pub struct Run {
    pub code: Option<i32>,
    pub stdout: String,
    pub stderr: String,
}

impl Run {
    pub fn ok(&self) -> &Run {
        assert_eq!(
            self.code,
            Some(0),
            "expected success\nstdout:\n{}\nstderr:\n{}",
            self.stdout,
            self.stderr
        );
        self
    }

    pub fn code(&self, code: i32) -> &Run {
        assert_eq!(
            self.code,
            Some(code),
            "expected exit {code}\nstdout:\n{}\nstderr:\n{}",
            self.stdout,
            self.stderr
        );
        self
    }

    /// The process died, rather than exiting with one of fleet's own codes
    /// (0-6). A lease error (6) under load is not the crash a test arranged.
    pub fn crashed(&self) -> &Run {
        assert!(
            !matches!(self.code, Some(0..=6)),
            "expected a crash, got exit {:?}\nstdout:\n{}\nstderr:\n{}",
            self.code,
            self.stdout,
            self.stderr
        );
        self
    }

    pub fn stderr_has(&self, needle: &str) -> &Run {
        assert!(
            self.stderr.contains(needle),
            "stderr lacks {needle:?}:\n{}",
            self.stderr
        );
        self
    }

    pub fn stdout_has(&self, needle: &str) -> &Run {
        assert!(
            self.stdout.contains(needle),
            "stdout lacks {needle:?}:\n{}",
            self.stdout
        );
        self
    }

    pub fn json(&self) -> Value {
        serde_json::from_str(&self.stdout)
            .unwrap_or_else(|e| panic!("stdout is not JSON ({e}):\n{}", self.stdout))
    }
}

impl Env {
    pub fn new(name: &str) -> Env {
        let n = SEQ.fetch_add(1, Ordering::Relaxed);
        let root = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
            .join("e2e")
            .join(format!("{name}-{}-{n}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("tmp")).expect("test root");
        let config = root.join("fleet.toml");
        fs::write(
            &config,
            "version = 1\n\n[data]\ndir = \"data\"\n\n[runtime]\nkind = \"fake\"\ndir = \"fake\"\n",
        )
        .expect("config");
        Env {
            data: root.join("data"),
            fake: root.join("fake"),
            tmp: root.join("tmp"),
            config,
            root,
            path: None,
            vars: Vec::new(),
        }
    }

    /// An environment whose runtime is the thurbox adapter driving the stub
    /// thurbox-cli (`tests/stubs/stub.rs`).
    pub fn thurbox(name: &str) -> Env {
        let mut env = Env::new(name);
        let stub_dir = env.root.join("stub");
        fs::create_dir_all(&stub_dir).expect("stub dir");
        let stub = stub_exe();
        let stub = stub.to_string_lossy();
        fs::write(
            &env.config,
            format!(
                "version = 1\n\n[data]\ndir = \"data\"\n\n[runtime]\nkind = \"thurbox\"\ncli = '{stub}'\ncallTimeoutMs = 3000\n"
            ),
        )
        .expect("config");
        env.vars.push((
            "FLEET_STUB_DIR".into(),
            stub_dir.to_string_lossy().into_owned(),
        ));
        env
    }

    pub fn set_var(&mut self, k: &str, v: &str) {
        self.vars.push((k.into(), v.into()));
    }

    pub fn stub_dir(&self) -> PathBuf {
        self.root.join("stub")
    }

    pub fn stub(&self) -> Value {
        fs::read(self.stub_dir().join("state.json"))
            .map(|b| serde_json::from_slice(&b).expect("stub state"))
            .unwrap_or_else(|_| json!({}))
    }

    pub fn edit_stub(&self, f: impl FnOnce(&mut Value)) {
        let mut s = self.stub();
        f(&mut s);
        fs::write(
            self.stub_dir().join("state.json"),
            serde_json::to_vec_pretty(&s).expect("json"),
        )
        .expect("write stub state");
    }

    /// Arm a stub fault: `call` is a thurbox subcommand (`create`, `send`,
    /// `meta-get`, `delete`, ...), `relay`, or `watch-stream`.
    pub fn stub_fault(&self, call: &str, mode: &str, times: u32) {
        self.stub_fault_after(call, mode, times, 0);
    }

    /// As `stub_fault`, letting the first `after` matching calls through.
    pub fn stub_fault_after(&self, call: &str, mode: &str, times: u32, after: u32) {
        self.edit_stub(|s| {
            if !s["faults"].is_array() {
                s["faults"] = json!([]);
            }
            s["faults"]
                .as_array_mut()
                .expect("faults")
                .push(json!({"call": call, "mode": mode, "times": times, "after": after}));
        });
    }

    /// Every call the stub saw, as `{argv, envNames}`.
    pub fn stub_calls(&self) -> Vec<Value> {
        fs::read_to_string(self.stub_dir().join("calls.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).expect("call line"))
            .collect()
    }

    pub fn stub_calls_of(&self, sub: &str) -> usize {
        self.stub_calls()
            .iter()
            .filter(|c| c["argv"][0] == "session" && c["argv"][1] == sub)
            .count()
    }

    pub fn stub_sessions(&self) -> Vec<Value> {
        self.stub()["sessions"]
            .as_array()
            .cloned()
            .unwrap_or_default()
    }

    /// Pids of the grandchildren a hanging stub left behind.
    pub fn grandchildren(&self) -> Vec<u32> {
        fs::read_to_string(self.stub_dir().join("grandchildren"))
            .unwrap_or_default()
            .lines()
            .filter_map(|l| l.trim().parse().ok())
            .collect()
    }

    /// Every file under the data directory and the stub's state, as text: a
    /// secret value must appear in none of them.
    pub fn all_written_text(&self) -> String {
        fn walk(dir: &Path, out: &mut String) {
            for e in fs::read_dir(dir).into_iter().flatten().flatten() {
                let p = e.path();
                if p.is_dir() {
                    walk(&p, out);
                } else if let Ok(b) = fs::read(&p) {
                    out.push_str(&String::from_utf8_lossy(&b));
                }
            }
        }
        let mut out = String::new();
        walk(&self.data, &mut out);
        walk(&self.stub_dir(), &mut out);
        out
    }

    /// An environment with no program on PATH at all: no thurbox-cli, no
    /// fleet extension, no shell. The core must not need any of them.
    pub fn without_tools(name: &str) -> Env {
        let mut env = Env::new(name);
        let empty = env.root.join("empty-path");
        fs::create_dir_all(&empty).expect("empty PATH dir");
        env.path = Some(empty);
        env
    }

    pub fn command(&self, args: &[&str]) -> Command {
        let mut cmd = Command::new(env!("CARGO_BIN_EXE_fleet"));
        cmd.arg("--config").arg(&self.config).args(args);
        cmd.env_clear();
        for (k, v) in &self.vars {
            cmd.env(k, v);
        }
        cmd.env("HOME", &self.root)
            .env("TMPDIR", &self.tmp)
            .env("TMP", &self.tmp)
            .env("TEMP", &self.tmp);
        match &self.path {
            Some(p) => {
                cmd.env("PATH", p);
            }
            None => {
                if let Some(path) = std::env::var_os("PATH") {
                    cmd.env("PATH", path);
                }
            }
        }
        if cfg!(windows) {
            for var in ["SYSTEMROOT", "WINDIR"] {
                if let Some(v) = std::env::var_os(var) {
                    cmd.env(var, v);
                }
            }
        }
        cmd.stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        cmd
    }

    pub fn spawn(&self, args: &[&str]) -> Child {
        self.command(args).spawn().expect("spawn fleet")
    }

    pub fn fleet(&self, args: &[&str]) -> Run {
        wait(self.spawn(args))
    }

    pub fn apply(&self, doc: Value) -> Run {
        let n = SEQ.fetch_add(1, Ordering::Relaxed);
        let path = self.root.join(format!("apply-{n}.json"));
        fs::write(&path, serde_json::to_vec_pretty(&doc).expect("json")).expect("write doc");
        let path = path.to_string_lossy().into_owned();
        self.fleet(&["apply", "-f", &path])
    }

    pub fn reconcile(&self) -> Run {
        self.fleet(&["reconcile", "--json"])
    }

    pub fn host(&self) -> FakeHost {
        FakeHost::open(&self.fake).expect("fake host")
    }

    pub fn fault(&self, fault: Fault) {
        self.host().add_fault(fault).expect("fault");
    }

    pub fn get(&self, name: &str) -> Value {
        self.fleet(&["get", "task", name]).ok().json()
    }

    pub fn status(&self, name: &str) -> Value {
        self.fleet(&["status", "task", name, "--json"]).ok().json()
    }

    pub fn session_key(&self, name: &str) -> String {
        let uid = self.get(name)["metadata"]["uid"]
            .as_str()
            .expect("uid")
            .to_string();
        format!("{name}.{uid}")
    }

    pub fn calls(&self, call: &str) -> u64 {
        self.host().calls(call).expect("calls")
    }

    /// Nothing a write left behind: the store's temp directory is empty.
    pub fn assert_no_temp(&self) {
        let tmp = self.data.join("tmp");
        let left: Vec<_> = fs::read_dir(tmp)
            .map(|d| d.filter_map(Result::ok).map(|e| e.path()).collect())
            .unwrap_or_default();
        assert!(left.is_empty(), "temp files left behind: {left:?}");
    }

    /// Let a Task's wait before its next attempt run out now, as time would.
    pub fn expire_wait(&self, name: &str) {
        let path = self.object_path("Task", name);
        let mut task: Value =
            serde_json::from_slice(&fs::read(&path).expect("task record")).expect("task json");
        if task["status"]["notBefore"].is_u64() {
            task["status"]["notBefore"] = json!(0);
            fs::write(&path, task.to_string()).expect("task record");
        }
    }

    pub fn object_path(&self, kind: &str, name: &str) -> PathBuf {
        self.data
            .join("objects")
            .join(kind)
            .join(format!("{name}.json"))
    }
}

impl Drop for Env {
    fn drop(&mut self) {
        // A failing test keeps its directory for the post-mortem.
        if !thread::panicking() {
            let _ = fs::remove_dir_all(&self.root);
        }
    }
}

/// Wait for a child within the bound; past it, kill it and fail. The pipes
/// are drained on their own threads so a chatty child cannot block on a full
/// pipe while we wait for it.
pub fn wait(child: Child) -> Run {
    wait_within(child, TIMEOUT)
}

/// [`wait`] with an explicit bound, for a child that legitimately takes
/// longer than one fleet command (a whole installer run).
pub fn wait_within(mut child: Child, bound: Duration) -> Run {
    let drain = |pipe: Option<Box<dyn Read + Send>>| {
        thread::spawn(move || {
            let mut buf = Vec::new();
            if let Some(mut p) = pipe {
                let _ = p.read_to_end(&mut buf);
            }
            String::from_utf8_lossy(&buf).into_owned()
        })
    };
    let out = drain(
        child
            .stdout
            .take()
            .map(|p| Box::new(p) as Box<dyn Read + Send>),
    );
    let err = drain(
        child
            .stderr
            .take()
            .map(|p| Box::new(p) as Box<dyn Read + Send>),
    );
    let deadline = Instant::now() + bound;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(10)),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                panic!("fleet did not finish within {bound:?}");
            }
            Err(e) => panic!("waiting for fleet: {e}"),
        }
    };
    Run {
        code: status.code(),
        stdout: out.join().expect("stdout"),
        stderr: err.join().expect("stderr"),
    }
}

/// The resources a scenario needs besides its Tasks: none, since a Task
/// carries everything it runs on.
pub fn base_config() -> Value {
    json!([])
}

pub fn task(name: &str, dispatch: bool) -> Value {
    json!({
        "apiVersion": "fleet/v1alpha1",
        "kind": "Task",
        "metadata": {"name": name, "labels": {"topic": "e2e"}},
        "spec": {
            "harness": "claude-code",
            "host": "local",
            "brief": format!("Do the work for {name}."),
            "workspace": {"dir": format!("work/{name}")},
            "dispatch": dispatch,
            "outputs": {"prUrl": {"type": "string", "required": true}, "tests": {"type": "integer"}}
        }
    })
}

pub fn condition<'a>(status: &'a Value, type_: &str) -> &'a Value {
    status["conditions"]
        .as_array()
        .and_then(|cs| cs.iter().find(|c| c["type"] == type_))
        .unwrap_or_else(|| panic!("no condition {type_} in {status:#}"))
}

pub fn assert_condition(status: &Value, type_: &str, want: &str, reason: &str) {
    let c = condition(status, type_);
    assert_eq!(c["status"], want, "{type_} in {status:#}");
    assert_eq!(c["reason"], reason, "{type_} in {status:#}");
}

pub fn exists(path: &Path) -> bool {
    path.exists()
}

pub fn stub_exe() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_fleet-test-stub"))
}

/// Whether a process is still alive, waiting up to `within` for it to go.
pub fn alive_after(pid: u32, within: Duration) -> bool {
    let deadline = Instant::now() + within;
    loop {
        if !alive(pid) {
            return false;
        }
        if Instant::now() >= deadline {
            return true;
        }
        thread::sleep(Duration::from_millis(50));
    }
}

#[cfg(unix)]
fn alive(pid: u32) -> bool {
    // A zombie has exited; only a running or sleeping process counts.
    match fs::read_to_string(format!("/proc/{pid}/stat")) {
        Ok(stat) => !stat
            .rsplit_once(')')
            .is_some_and(|(_, rest)| rest.trim_start().starts_with('Z')),
        Err(_) => Command::new("kill")
            .args(["-0", &pid.to_string()])
            .stderr(Stdio::null())
            .status()
            .is_ok_and(|s| s.success()),
    }
}

#[cfg(windows)]
fn alive(pid: u32) -> bool {
    Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/NH"])
        .output()
        .is_ok_and(|o| String::from_utf8_lossy(&o.stdout).contains(&pid.to_string()))
}

/// What a thurbox scenario applies besides its Tasks: nothing, as a Task
/// names its runtime host itself.
pub fn runtime_config() -> Value {
    base_config()
}

pub fn object(kind: &str, name: &str, spec: Value) -> Value {
    json!({"apiVersion": "fleet/v1alpha1", "kind": kind, "metadata": {"name": name}, "spec": spec})
}
