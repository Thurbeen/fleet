//! A bounded stand-in for `thurbox-cli`, so the end-to-end tests drive the
//! real binary against something that behaves like it without any real
//! session or host: `session …` and `doctor …`, the slice the runtime adapter
//! calls, answering in its JSON shapes.
//!
//! Its whole state is one JSON file in `$FLEET_STUB_DIR`, guarded by a lock
//! file so concurrent fleet processes see one host. Every call is appended to
//! `calls.jsonl` with its argv and the NAMES of its environment, so a test can
//! prove what reached a command line. Faults make a call fail, hang (leaving a
//! grandchild behind, to prove the caller kills the whole tree), or hang after
//! its effect landed (a lost answer).

use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Command, ExitCode, Stdio};
use std::thread::sleep;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

fn dir() -> PathBuf {
    PathBuf::from(std::env::var_os("FLEET_STUB_DIR").expect("FLEET_STUB_DIR is set"))
}

struct Lock(PathBuf);

impl Lock {
    fn take() -> Lock {
        let path = dir().join("state.lock");
        let deadline = Instant::now() + Duration::from_secs(30);
        loop {
            match OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(_) => return Lock(path),
                Err(_) if Instant::now() < deadline => sleep(Duration::from_millis(5)),
                Err(e) => panic!("stub lock: {e}"),
            }
        }
    }
}

impl Drop for Lock {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

fn load() -> Value {
    match fs::read(dir().join("state.json")) {
        Ok(b) => serde_json::from_slice(&b).expect("stub state"),
        Err(_) => json!({}),
    }
}

fn save(state: &Value) {
    let tmp = dir().join("state.json.tmp");
    fs::write(&tmp, serde_json::to_vec_pretty(state).expect("json")).expect("write state");
    fs::rename(&tmp, dir().join("state.json")).expect("rename state");
}

fn log_call(args: &[String]) {
    let mut names: Vec<String> = std::env::vars_os()
        .map(|(k, _)| k.to_string_lossy().into_owned())
        .collect();
    names.sort();
    let line = json!({"argv": args, "envNames": names});
    let mut f = OpenOptions::new()
        .append(true)
        .create(true)
        .open(dir().join("calls.jsonl"))
        .expect("calls log");
    writeln!(f, "{line}").expect("log");
}

/// The fault armed for `call`, consumed once.
fn take_fault(state: &mut Value, call: &str) -> Option<String> {
    let faults = state.get_mut("faults")?.as_array_mut()?;
    let i = faults.iter().position(|f| f["call"] == call)?;
    // `after: N` lets the first N matching calls through.
    if let Some(after) = faults[i]["after"].as_u64().filter(|n| *n > 0) {
        faults[i]["after"] = json!(after - 1);
        return None;
    }
    let mode = faults[i]["mode"].as_str().unwrap_or("fail").to_string();
    let times = faults[i]["times"].as_u64().unwrap_or(1);
    if times <= 1 {
        faults.remove(i);
    } else {
        faults[i]["times"] = json!(times - 1);
    }
    Some(mode)
}

/// Hang like a stuck program: leave a grandchild that would outlive a caller
/// killing only its direct child, record its pid, and never answer.
fn hang() -> ! {
    let me = std::env::current_exe().expect("self");
    let child = Command::new(me)
        .arg("sleep")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("grandchild");
    let mut f = OpenOptions::new()
        .append(true)
        .create(true)
        .open(dir().join("grandchildren"))
        .expect("pids");
    writeln!(f, "{}", child.id()).expect("pid");
    sleep(Duration::from_secs(600));
    std::process::exit(9)
}

fn opt(args: &[String], name: &str) -> Option<String> {
    args.iter()
        .position(|a| a == name)
        .and_then(|i| args.get(i + 1).cloned())
}

fn opts(args: &[String], name: &str) -> Vec<String> {
    args.iter()
        .enumerate()
        .filter(|(_, a)| *a == name)
        .filter_map(|(i, _)| args.get(i + 1).cloned())
        .collect()
}

fn host_state(state: &Value, host: &str) -> String {
    state["hosts"][host]
        .as_str()
        .map(str::to_string)
        .unwrap_or_else(|| if host == "local" { "up" } else { "absent" }.to_string())
}

fn sessions(state: &mut Value) -> &mut Vec<Value> {
    if !state["sessions"].is_array() {
        state["sessions"] = json!([]);
    }
    state["sessions"].as_array_mut().expect("sessions")
}

fn event(state: &mut Value, kind: &str, s: &Value) {
    let seq = state["seq"].as_u64().unwrap_or(0) + 1;
    state["seq"] = json!(seq);
    // The shape `thurbox-cli watch --json` prints: no host and no key.
    let e = json!({
        "seq": seq, "event": kind, "session": s["id"], "name": s["name"],
        "state": s["state"], "at": seq
    });
    if !state["events"].is_array() {
        state["events"] = json!([]);
    }
    state["events"].as_array_mut().expect("events").push(e);
}

/// One session row as `session list --json` prints it. A session on a host
/// that cannot be reached reads `unreachable`, as thurbox's do.
fn row(state: &Value, s: &Value) -> Value {
    let mut r = s.clone();
    if let Some(o) = r.as_object_mut() {
        o.remove("meta");
        o.remove("sent");
        o.remove("env");
    }
    let host = s["host"].as_str().unwrap_or("local");
    if host_state(state, host) != "up" {
        r["state"] = json!("unreachable");
    }
    r
}

fn find_index(state: &Value, id: &str) -> Option<usize> {
    state["sessions"]
        .as_array()?
        .iter()
        .position(|s| s["id"] == id)
}

fn fail(msg: &str) -> ExitCode {
    eprintln!("{msg}");
    ExitCode::from(1)
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("sleep") {
        sleep(Duration::from_secs(600));
        return ExitCode::SUCCESS;
    }
    log_call(&args);
    match args.first().map(String::as_str) {
        Some("session") | Some("doctor") => thurbox(&args),
        _ => fail("stub: unknown role"),
    }
}

fn thurbox(args: &[String]) -> ExitCode {
    let call = match (
        args.first().map(String::as_str),
        args.get(1).map(String::as_str),
    ) {
        (Some("session"), Some("meta")) => {
            format!("meta-{}", args.get(2).cloned().unwrap_or_default())
        }
        (Some("session"), Some(sub)) => sub.to_string(),
        (Some(top), _) => top.to_string(),
        _ => return fail("stub: no command"),
    };
    // `meta set` without a value reads it from stdin, before the lock.
    let mut stdin_value = None;
    if call == "meta-set" && args.len() < 6 {
        let mut v = String::new();
        let _ = std::io::stdin().read_to_string(&mut v);
        stdin_value = Some(v);
    }
    let lock = Lock::take();
    let mut state = load();
    let fault = take_fault(&mut state, &call);
    match fault.as_deref() {
        Some("fail") => {
            save(&state);
            return fail(&format!("stub: {call} refused"));
        }
        Some("hang") => {
            save(&state);
            drop(lock);
            hang();
        }
        Some("slow") => {
            save(&state);
            drop(lock);
            sleep(Duration::from_millis(1500));
            let _lock = Lock::take();
            let mut state = load();
            let (code, out) = answer(&call, args, &mut state, stdin_value);
            save(&state);
            if let Some(out) = out {
                println!("{out}");
            }
            return code;
        }
        _ => {}
    }
    let (code, out) = answer(&call, args, &mut state, stdin_value);
    save(&state);
    drop(lock);
    if fault.as_deref() == Some("hang-after") {
        hang();
    }
    if let Some(out) = out {
        println!("{out}");
    }
    code
}

fn answer(
    call: &str,
    args: &[String],
    state: &mut Value,
    stdin_value: Option<String>,
) -> (ExitCode, Option<String>) {
    let ok = |v: Value| (ExitCode::SUCCESS, Some(v.to_string()));
    let err = |m: String| {
        eprintln!("{m}");
        (ExitCode::from(1), None)
    };
    match call {
        "doctor" => {
            let mut checks = vec![json!({"key": "multiplexer", "level": "ok", "detail": "stub"})];
            if let Some(hosts) = state["hosts"].as_object() {
                for (h, s) in hosts {
                    let level = if s == "up" || s == "unknown" {
                        "ok"
                    } else {
                        "fail"
                    };
                    checks.push(
                        json!({"key": format!("host:{h}"), "level": level, "detail": "stub"}),
                    );
                }
            }
            ok(json!({"verdict": "ok", "multiplexer": "stub", "path": [], "checks": checks}))
        }
        "create" => {
            let host = opt(args, "--host").unwrap_or_else(|| "local".to_string());
            let key = opt(args, "--key").unwrap_or_default();
            let repo = opt(args, "--repo-path").unwrap_or_default();
            let b = repo.as_bytes();
            let absolute = repo.starts_with('/')
                || (b.len() > 2
                    && b[0].is_ascii_alphabetic()
                    && b[1] == b':'
                    && (b[2] == b'\\' || b[2] == b'/'));
            if !absolute {
                return err(format!(
                    "--repo-path must be an absolute path, got {repo:?}"
                ));
            }
            match host_state(state, &host).as_str() {
                "up" => {}
                "absent" => return err(format!("host {host} is not in hosts.toml")),
                other => return err(format!("host {host} cannot be reached ({other})")),
            }
            let existing = state["sessions"]
                .as_array()
                .and_then(|ss| {
                    ss.iter()
                        .find(|s| s["key"] == key.as_str() && s["host"] == host.as_str())
                })
                .cloned();
            if let Some(s) = existing {
                if opt(args, "--on-existing").as_deref() == Some("adopt") {
                    let mut r = row(state, &s);
                    r["created"] = json!(false);
                    return ok(r);
                }
                return err(format!("key {key} is taken on {host}"));
            }
            let n = state["next"].as_u64().unwrap_or(0) + 1;
            state["next"] = json!(n);
            let ready = state["spawnState"].as_str().unwrap_or("idle").to_string();
            let env_names: Vec<String> = opts(args, "--env")
                .iter()
                .map(|kv| kv.split('=').next().unwrap_or_default().to_string())
                .collect();
            let mut s = json!({
                "id": format!("00000000-0000-4000-8000-{n:012}"),
                "name": opt(args, "--name"), "key": key, "host": host,
                "agent": opt(args, "--agent"), "cwd": opt(args, "--repo-path"),
                "state": ready, "stopped": false, "meta": {}, "sent": [], "env": env_names
            });
            if let Some(fields) = state["spawnFields"].as_object() {
                s.as_object_mut().unwrap().extend(fields.clone());
            }
            sessions(state).push(s.clone());
            event(state, "created", &s);
            let mut r = row(state, &s);
            r["created"] = json!(true);
            ok(r)
        }
        "list" => {
            let prefix = opt(args, "--key-prefix").unwrap_or_default();
            let exact = opt(args, "--key");
            let rows: Vec<Value> = state["sessions"]
                .as_array()
                .cloned()
                .unwrap_or_default()
                .iter()
                .filter(|s| {
                    let k = s["key"].as_str().unwrap_or("");
                    k.starts_with(&prefix) && exact.as_deref().is_none_or(|e| k == e)
                })
                .map(|s| row(state, s))
                .collect();
            ok(json!(rows))
        }
        "get" => {
            let id = args.get(2).cloned().unwrap_or_default();
            match find_index(state, &id) {
                Some(i) => {
                    let s = state["sessions"][i].clone();
                    ok(row(state, &s))
                }
                None => err(format!("no session {id}")),
            }
        }
        "capture" => {
            let id = args.get(2).cloned().unwrap_or_default();
            let mut capture = state["capture"].clone();
            capture["session_id"] = json!(id);
            ok(capture)
        }
        "send" => {
            let id = args.get(2).cloned().unwrap_or_default();
            // As thurbox-cli parses it: a text that starts with `-` is a
            // flag unless `--` comes first.
            let text = match args.get(3).map(String::as_str) {
                Some("--") => args.get(4).cloned().unwrap_or_default(),
                Some(t) if t.starts_with('-') => {
                    return (
                        ExitCode::from(2),
                        Some(format!("error: unexpected argument '{t}' found")),
                    );
                }
                _ => args.get(3).cloned().unwrap_or_default(),
            };
            let Some(i) = find_index(state, &id) else {
                return err(format!("no session {id}"));
            };
            let s = &mut state["sessions"][i];
            s["sent"].as_array_mut().expect("sent").push(json!(text));
            s["state"] = json!("working");
            let s = s.clone();
            event(state, "state", &s);
            ok(json!({"submitted": true}))
        }
        "meta-get" | "meta-set" => {
            let id = args.get(3).cloned().unwrap_or_default();
            let key = args.get(4).cloned().unwrap_or_default();
            let Some(i) = find_index(state, &id) else {
                return err(format!("no session {id}"));
            };
            if call == "meta-get" {
                let v = state["sessions"][i]["meta"][&key]
                    .as_str()
                    .unwrap_or("")
                    .to_string();
                (ExitCode::SUCCESS, Some(v))
            } else {
                let v = args.get(5).cloned().or(stdin_value).unwrap_or_default();
                state["sessions"][i]["meta"][&key] = json!(v);
                (ExitCode::SUCCESS, None)
            }
        }
        "delete" | "stop" => {
            let id = args.get(2).cloned().unwrap_or_default();
            let Some(i) = find_index(state, &id) else {
                return err(format!("no session {id}"));
            };
            if call == "delete" {
                let s = sessions(state).remove(i);
                event(state, "gone", &s);
            } else {
                state["sessions"][i]["stopped"] = json!(true);
                state["sessions"][i]["state"] = json!("stopped");
                let s = state["sessions"][i].clone();
                event(state, "state", &s);
            }
            ok(json!({"ok": true}))
        }
        "watch" => {
            let since: u64 = opt(args, "--since")
                .and_then(|s| s.parse().ok())
                .unwrap_or(0);
            let mut lines: Vec<Value> = state["events"]
                .as_array()
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .filter(|e| e["seq"].as_u64().unwrap_or(0) > since)
                .collect();
            // Stream faults are armed as data rather than a call fault, so
            // the stream still answers.
            match take_fault(state, "watch-stream").as_deref() {
                Some("gap") if !lines.is_empty() => {
                    lines.remove(0);
                }
                Some("dup") if !lines.is_empty() => {
                    let first = lines[0].clone();
                    lines.insert(0, first);
                }
                _ => {}
            }
            let out: Vec<String> = lines.iter().map(Value::to_string).collect();
            (
                ExitCode::SUCCESS,
                Some(out.join("\n")).filter(|s| !s.is_empty()),
            )
        }
        other => err(format!("stub: {other} is not modelled")),
    }
}
