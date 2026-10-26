//! A bounded stand-in for `thurbox-cli`, `gh` and `glab`, so the end-to-end
//! tests drive the real binary against something that behaves like them
//! without any real session, host or forge. As `thurbox-cli` it answers
//! `session …` and `doctor …`, the slice the runtime adapter calls, in its
//! JSON shapes. Copied to a file named `gh` or `glab` it answers `api …` in
//! that forge's shapes, GraphQL for GitHub and REST for GitLab, rendered from
//! one forge-neutral model under `changes` in its state.
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
    if args.first().map(String::as_str) == Some("env-output") {
        if let Some(marker) = args.get(2) {
            std::fs::write(marker, "called").expect("marker");
        }
        let bytes = std::fs::read(&args[1]).expect("source output");
        std::io::Write::write_all(&mut std::io::stdout(), &bytes).expect("output");
        return ExitCode::SUCCESS;
    }
    if args.first().map(String::as_str) == Some("plugin") {
        return plugin(&args);
    }
    log_call(&args);
    let role = std::env::current_exe()
        .ok()
        .and_then(|p| p.file_stem().map(|s| s.to_string_lossy().into_owned()))
        .unwrap_or_default();
    match (role.as_str(), args.first().map(String::as_str)) {
        ("gh", Some("api")) => forge::gh(&args),
        ("glab", Some("api")) => forge::glab(&args),
        (_, Some("session") | Some("doctor")) => thurbox(&args),
        _ => fail("stub: unknown role"),
    }
}

/// The forge roles: one model, two renderings. A change lives under
/// `changes["<project>#<id>"]` as
/// `{state, head, title, checks: [{name, state, url}], reviewDecision,
/// mergeable, approvedBy, reviews: [{id, author, bot, state, body, head, at,
/// editedAt}], comments: [{id, author, bot, body, at, editedAt}],
/// threads: [{id, resolved, path, line, comments: [...]}]}`; `pageSize`
/// bounds a GitHub page, `login` answers whoami, `noRepo` makes the project
/// itself absent. A mark-ready (the GitHub mutation, or GitLab's title
/// update) moves `state` to `open` and records the intent under
/// `readyIntents`. Faults are armed under the call `gh` or `glab`: `fail`,
/// `hang`, `auth`, `rate-limit` (with `retryAfter` seconds) and `malformed`.
mod forge {
    use std::process::ExitCode;

    use serde_json::{Value, json};

    use super::{Lock, fail, hang, load, save, take_fault};

    const HEAD: &str = "HTTP/2.0 200 OK\r\nX-Ratelimit-Remaining: 4999\r\n\r\n";

    fn iso(ms: &Value) -> Value {
        let Some(ms) = ms.as_u64() else {
            return Value::Null;
        };
        let (secs, millis) = (ms / 1000, ms % 1000);
        let days = i64::try_from(secs / 86_400).unwrap_or(0);
        let rem = secs % 86_400;
        // Civil-from-days, Howard Hinnant's algorithm.
        let z = days + 719_468;
        let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
        let doe = z - era * 146_097;
        let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
        let y = yoe + era * 400;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let d = doy - (153 * mp + 2) / 5 + 1;
        let m = if mp < 10 { mp + 3 } else { mp - 9 };
        let y = if m <= 2 { y + 1 } else { y };
        json!(format!(
            "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}.{millis:03}Z",
            rem / 3600,
            (rem % 3600) / 60,
            rem % 60
        ))
    }

    fn var(args: &[String], name: &str) -> Option<String> {
        args.iter()
            .filter(|a| a.contains('='))
            .filter_map(|a| a.split_once('='))
            .find(|(k, _)| *k == name)
            .map(|(_, v)| v.to_string())
    }

    fn flag(args: &[String], name: &str) -> bool {
        var(args, name).as_deref() == Some("true")
    }

    fn answer(code: ExitCode, head: &str, body: &str) -> ExitCode {
        print!("{head}{body}");
        code
    }

    /// Arm and apply the role's fault; `None` means proceed normally.
    fn faulted(
        state: &mut Value,
        lock: Lock,
        role: &str,
        args: &[String],
    ) -> Result<Lock, ExitCode> {
        let fault = take_fault(state, role);
        let Some(mode) = fault.as_deref() else {
            return Ok(lock);
        };
        let mode = mode.to_string();
        save(state);
        drop(lock);
        let retry = state["retryAfter"].as_u64().unwrap_or(30);
        let _ = args;
        Err(match mode.as_str() {
            "hang" => hang(),
            "auth" => {
                eprintln!("To get started with {role}, please run: {role} auth login");
                ExitCode::from(4)
            }
            "rate-limit" => {
                let status = if role == "gh" {
                    "403 Forbidden"
                } else {
                    "429 Too Many Requests"
                };
                answer(
                    ExitCode::from(1),
                    &format!(
                        "HTTP/2.0 {status}\r\nRetry-After: {retry}\r\nX-Ratelimit-Remaining: 0\r\n\r\n"
                    ),
                    "{\"message\":\"API rate limit exceeded for this user.\"}",
                )
            }
            "malformed" => answer(ExitCode::SUCCESS, HEAD, "<html>not json"),
            _ => fail(&format!("stub: {role} refused")),
        })
    }

    fn change<'a>(state: &'a Value, project: &str, id: &str) -> Option<&'a Value> {
        state["changes"].get(format!("{project}#{id}"))
    }

    fn author(item: &Value) -> Value {
        json!({"login": item["author"], "__typename": if item["bot"] == true { "Bot" } else { "User" }})
    }

    /// One page of a connection: cursors are indexes.
    fn page(items: &[Value], after: Option<String>, size: usize) -> (Vec<Value>, Value) {
        let start = after
            .and_then(|c| c.parse::<usize>().ok())
            .map_or(0, |i| i + 1);
        let end = (start + size).min(items.len());
        let nodes = items[start..end].to_vec();
        let info = json!({"hasNextPage": end < items.len(), "endCursor": if end > start { json!((end - 1).to_string()) } else { Value::Null }});
        (nodes, info)
    }

    pub fn gh(args: &[String]) -> ExitCode {
        let lock = Lock::take();
        let mut state = load();
        let _lock = match faulted(&mut state, lock, "gh", args) {
            Ok(l) => l,
            Err(code) => return code,
        };
        let query = var(args, "query").unwrap_or_default();
        if query.contains("viewer") {
            let login = state["login"].as_str().unwrap_or("stub-login");
            return answer(
                ExitCode::SUCCESS,
                HEAD,
                &json!({"data": {"viewer": {"login": login}}}).to_string(),
            );
        }
        let (owner, name, number) = (
            var(args, "owner").unwrap_or_default(),
            var(args, "name").unwrap_or_default(),
            var(args, "number").unwrap_or_default(),
        );
        if state["noRepo"] == true {
            return answer(ExitCode::from(1), HEAD, &json!({"data": {"repository": null}, "errors": [{"type": "NOT_FOUND", "path": ["repository"], "message": "Could not resolve to a Repository"}]}).to_string());
        }
        if query.contains("markPullRequestReadyForReview") {
            // The mutation names the pull request by the id the fresh read
            // handed out: `PR_<project>#<number>`.
            let id = var(args, "id").unwrap_or_default();
            let Some(key) = id.strip_prefix("PR_") else {
                return answer(ExitCode::SUCCESS, HEAD, &json!({"data": null, "errors": [{"type": "NOT_FOUND", "message": "Could not resolve to a node"}]}).to_string());
            };
            let key = key.to_string();
            let intent = var(args, "intent").unwrap_or_default();
            let Some(c) = state["changes"].get_mut(&key) else {
                return answer(ExitCode::SUCCESS, HEAD, &json!({"data": null, "errors": [{"type": "NOT_FOUND", "message": "Could not resolve to a node"}]}).to_string());
            };
            if c["state"] != "draft" {
                return answer(ExitCode::SUCCESS, HEAD, &json!({"data": {"markPullRequestReadyForReview": null}, "errors": [{"message": "Pull request is not a draft"}]}).to_string());
            }
            c["state"] = json!("open");
            if !c["readyIntents"].is_array() {
                c["readyIntents"] = json!([]);
            }
            c["readyIntents"]
                .as_array_mut()
                .unwrap()
                .push(json!(intent));
            save(&state);
            return answer(ExitCode::SUCCESS, HEAD, &json!({"data": {"markPullRequestReadyForReview": {"clientMutationId": intent, "pullRequest": {"isDraft": false}}}}).to_string());
        }
        let Some(c) = change(&state, &format!("{owner}/{name}"), &number) else {
            return answer(ExitCode::from(1), HEAD, &json!({"data": {"repository": {"pullRequest": null}}, "errors": [{"type": "NOT_FOUND", "path": ["repository", "pullRequest"], "message": "Could not resolve to a PullRequest"}]}).to_string());
        };
        let size = usize::try_from(state["pageSize"].as_u64().unwrap_or(100)).unwrap_or(100);
        let head = c["head"].as_str().unwrap_or("").to_string();
        if var(args, "withCore").is_none() {
            // The fresh read before a mark-ready: id, draft, state and head.
            let state_word = match c["state"].as_str().unwrap_or("open") {
                "closed" => "CLOSED",
                "merged" => "MERGED",
                _ => "OPEN",
            };
            return answer(ExitCode::SUCCESS, HEAD, &json!({"data": {"repository": {"pullRequest": {
                "id": format!("PR_{owner}/{name}#{number}"), "isDraft": c["state"] == "draft", "state": state_word, "headRefOid": head
            }}}}).to_string());
        }
        let mut pr = json!({});
        if flag(args, "withCore") {
            let state_word = match c["state"].as_str().unwrap_or("open") {
                "closed" => "CLOSED",
                "merged" => "MERGED",
                _ => "OPEN",
            };
            let (mergeable, merge_state) = match c["mergeable"].as_str().unwrap_or("mergeable") {
                "conflicting" => ("CONFLICTING", "DIRTY".to_string()),
                "unknown" => ("UNKNOWN", "UNKNOWN".to_string()),
                "mergeable" => ("MERGEABLE", "CLEAN".to_string()),
                other => ("MERGEABLE", other.to_ascii_uppercase()),
            };
            let contexts: Vec<Value> = c["checks"].as_array().cloned().unwrap_or_default().iter().map(|k| {
                let st = k["state"].as_str().unwrap_or("success");
                if k["kind"] == "status" {
                    json!({"__typename": "StatusContext", "context": k["name"], "state": st.to_ascii_uppercase(), "targetUrl": k["url"]})
                } else {
                    let (status, conclusion) = match st {
                        "success" => ("COMPLETED", json!("SUCCESS")),
                        "failure" => ("COMPLETED", json!("FAILURE")),
                        "skipped" => ("COMPLETED", json!("SKIPPED")),
                        _ => ("IN_PROGRESS", Value::Null),
                    };
                    json!({"__typename": "CheckRun", "name": k["name"], "status": status, "conclusion": conclusion, "detailsUrl": k["url"]})
                }
            }).collect();
            pr = json!({
                "isDraft": c["state"] == "draft",
                "state": state_word,
                "reviewDecision": match c["reviewDecision"].as_str() { Some("approved") => json!("APPROVED"), Some("changes-requested") => json!("CHANGES_REQUESTED"), _ => Value::Null },
                "mergeable": mergeable,
                "mergeStateStatus": merge_state,
                "headRefOid": head,
                "commits": {"nodes": [{"commit": {"oid": head, "statusCheckRollup": if contexts.is_empty() { Value::Null } else { json!({"contexts": {"pageInfo": {"hasNextPage": false}, "nodes": contexts}}) }}}]}
            });
        }
        if flag(args, "withReviews") {
            let items: Vec<Value> = c["reviews"].as_array().cloned().unwrap_or_default().iter().map(|r| json!({
                "id": r["id"], "state": r["state"].as_str().unwrap_or("commented").replace('-', "_").to_ascii_uppercase(),
                "body": r["body"], "url": format!("https://github.test/{owner}/{name}/pull/{number}#pullrequestreview-{}", r["id"]),
                "createdAt": iso(&r["at"]), "lastEditedAt": iso(&r["editedAt"]), "author": author(r), "commit": {"oid": r["head"]}
            })).collect();
            let (nodes, info) = page(&items, var(args, "reviewsAfter"), size);
            pr["reviews"] = json!({"pageInfo": info, "nodes": nodes});
        }
        if flag(args, "withComments") {
            let items: Vec<Value> = c["comments"].as_array().cloned().unwrap_or_default().iter().map(|k| json!({
                "id": k["id"], "body": k["body"], "url": format!("https://github.test/{owner}/{name}/pull/{number}#issuecomment-{}", k["id"]),
                "createdAt": iso(&k["at"]), "lastEditedAt": iso(&k["editedAt"]), "author": author(k)
            })).collect();
            let (nodes, info) = page(&items, var(args, "commentsAfter"), size);
            pr["comments"] = json!({"pageInfo": info, "nodes": nodes});
        }
        if flag(args, "withThreads") {
            let items: Vec<Value> = c["threads"].as_array().cloned().unwrap_or_default().iter().map(|t| {
                let comments: Vec<Value> = t["comments"].as_array().cloned().unwrap_or_default().iter().map(|k| json!({
                    "id": k["id"], "body": k["body"], "url": format!("https://github.test/{owner}/{name}/pull/{number}#discussion_r{}", k["id"]),
                    "path": t["path"], "line": t["line"], "originalLine": t["line"],
                    "createdAt": iso(&k["at"]), "lastEditedAt": iso(&k["editedAt"]), "author": author(k)
                })).collect();
                json!({"id": t["id"], "isResolved": t["resolved"] == true, "comments": {"pageInfo": {"hasNextPage": false}, "nodes": comments}})
            }).collect();
            let (nodes, info) = page(&items, var(args, "threadsAfter"), size);
            pr["reviewThreads"] = json!({"pageInfo": info, "nodes": nodes});
        }
        answer(
            ExitCode::SUCCESS,
            HEAD,
            &json!({"data": {"repository": {"pullRequest": pr}}}).to_string(),
        )
    }

    fn not_found() -> ExitCode {
        answer(
            ExitCode::from(1),
            "HTTP/2.0 404 Not Found\r\n\r\n",
            "{\"message\":\"404 Not found\"}",
        )
    }

    fn gl_user(item: &Value) -> Value {
        json!({"username": item["author"], "bot": item["bot"] == true})
    }

    pub fn glab(args: &[String]) -> ExitCode {
        let lock = Lock::take();
        let mut state = load();
        let _lock = match faulted(&mut state, lock, "glab", args) {
            Ok(l) => l,
            Err(code) => return code,
        };
        let endpoint = args.last().cloned().unwrap_or_default();
        let (path, query) = endpoint.split_once('?').unwrap_or((&endpoint, ""));
        let param = |k: &str| {
            query
                .split('&')
                .filter_map(|kv| kv.split_once('='))
                .find(|(a, _)| *a == k)
                .map(|(_, v)| v.to_string())
        };
        if path == "user" {
            let login = state["login"].as_str().unwrap_or("stub-login");
            return answer(
                ExitCode::SUCCESS,
                HEAD,
                &json!({"username": login}).to_string(),
            );
        }
        let Some(rest) = path.strip_prefix("projects/") else {
            return fail("stub: glab endpoint not modelled");
        };
        let mut parts = rest.splitn(2, '/');
        let project = parts.next().unwrap_or("").replace("%2F", "/");
        let tail = parts.next().unwrap_or("");
        if state["noRepo"] == true {
            return not_found();
        }
        if tail.is_empty() {
            return answer(
                ExitCode::SUCCESS,
                HEAD,
                &json!({"id": 1, "path_with_namespace": project}).to_string(),
            );
        }
        let Some(rest) = tail.strip_prefix("merge_requests/") else {
            return fail("stub: glab endpoint not modelled");
        };
        let mut parts = rest.splitn(2, '/');
        let iid = parts.next().unwrap_or("");
        let sub = parts.next().unwrap_or("");
        let Some(c) = change(&state, &project, iid) else {
            return not_found();
        };
        let key = format!("{project}#{iid}");
        let head = c["head"].as_str().unwrap_or("").to_string();
        let web_url = format!("https://gitlab.test/{project}/-/merge_requests/{iid}");
        let title = |c: &Value| {
            c["title"].as_str().map_or_else(
                || {
                    if c["state"] == "draft" {
                        "Draft: stub change".to_string()
                    } else {
                        "stub change".to_string()
                    }
                },
                str::to_string,
            )
        };
        let put = args.iter().any(|a| a == "PUT");
        if put && sub.is_empty() {
            // GitLab takes a merge request out of draft through its title.
            let new_title = var(args, "title").unwrap_or_default();
            let c = state["changes"].get_mut(&key).expect("change");
            c["title"] = json!(new_title);
            let still_draft = new_title.to_ascii_lowercase().starts_with("draft");
            if !still_draft && c["state"] == "draft" {
                c["state"] = json!("open");
                if !c["readyIntents"].is_array() {
                    c["readyIntents"] = json!([]);
                }
                c["readyIntents"]
                    .as_array_mut()
                    .unwrap()
                    .push(json!("title update"));
            }
            let body = json!({"iid": iid.parse::<u64>().unwrap_or(0), "draft": still_draft, "title": new_title, "sha": head});
            save(&state);
            return answer(ExitCode::SUCCESS, HEAD, &body.to_string());
        }
        let body = match sub {
            "" => {
                let checks = c["checks"].as_array().cloned().unwrap_or_default();
                let pipeline = if checks.is_empty() {
                    Value::Null
                } else {
                    let status = if checks.iter().any(|k| k["state"] == "failure") {
                        "failed"
                    } else if checks.iter().any(|k| k["state"] == "pending") {
                        "running"
                    } else {
                        "success"
                    };
                    json!({"status": status, "web_url": format!("{web_url}/pipelines/1")})
                };
                json!({
                    "iid": iid.parse::<u64>().unwrap_or(0),
                    "state": match c["state"].as_str().unwrap_or("open") { "closed" => "closed", "merged" => "merged", _ => "opened" },
                    "draft": c["state"] == "draft",
                    "title": title(c),
                    "sha": head,
                    "detailed_merge_status": c["mergeable"].as_str().unwrap_or("mergeable"),
                    "head_pipeline": pipeline,
                    "web_url": web_url
                })
            }
            "approvals" => {
                let by: Vec<Value> = c["approvedBy"]
                    .as_array()
                    .cloned()
                    .unwrap_or_default()
                    .iter()
                    .map(|u| json!({"user": {"username": u}}))
                    .collect();
                json!({"approved": !by.is_empty(), "approved_by": by})
            }
            "discussions" => {
                let mut all: Vec<Value> = Vec::new();
                for k in c["comments"].as_array().cloned().unwrap_or_default() {
                    all.push(json!({"id": format!("d{}", k["id"].as_str().unwrap_or("")), "individual_note": true, "notes": [
                        {"id": k["id"], "type": Value::Null, "body": k["body"], "author": gl_user(&k), "system": false,
                         "created_at": iso(&k["at"]), "updated_at": iso(if k["editedAt"].is_null() { &k["at"] } else { &k["editedAt"] }), "resolvable": false}
                    ]}));
                }
                for t in c["threads"].as_array().cloned().unwrap_or_default() {
                    let notes: Vec<Value> = t["comments"].as_array().cloned().unwrap_or_default().iter().map(|k| json!({
                        "id": k["id"], "type": "DiffNote", "body": k["body"], "author": gl_user(k), "system": false,
                        "created_at": iso(&k["at"]), "updated_at": iso(if k["editedAt"].is_null() { &k["at"] } else { &k["editedAt"] }),
                        "resolvable": true, "resolved": t["resolved"] == true,
                        "position": {"new_path": t["path"], "new_line": t["line"]}
                    })).collect();
                    all.push(json!({"id": t["id"], "individual_note": false, "notes": notes}));
                }
                let per: usize = param("per_page").and_then(|v| v.parse().ok()).unwrap_or(20);
                let page_no: usize = param("page")
                    .and_then(|v| v.parse().ok())
                    .unwrap_or(1)
                    .max(1);
                let start = (page_no - 1) * per;
                let end = (start + per).min(all.len());
                Value::Array(if start < all.len() {
                    all[start..end].to_vec()
                } else {
                    Vec::new()
                })
            }
            _ => return fail("stub: glab endpoint not modelled"),
        };
        answer(ExitCode::SUCCESS, HEAD, &body.to_string())
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
            let mut env_names: Vec<String> = opts(args, "--env")
                .iter()
                .map(|kv| kv.split('=').next().unwrap_or_default().to_string())
                .collect();
            if args.iter().any(|a| a == "--env-stdin") {
                let mut input = String::new();
                std::io::Read::read_to_string(&mut std::io::stdin(), &mut input)
                    .expect("environment stdin");
                let map: std::collections::BTreeMap<String, String> =
                    serde_json::from_str(&input).expect("environment map");
                env_names.extend(map.into_keys());
            }

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

/// Protocol fixture: reads only the explicit input directory, never operator state.
fn plugin(args: &[String]) -> ExitCode {
    let root = PathBuf::from(&args[1]);
    let call = args.last().unwrap();
    let scenario: Value =
        serde_json::from_slice(&fs::read(root.join("plugin.json")).unwrap()).unwrap();
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input).unwrap();
    for line in input.lines() {
        let req: Value = serde_json::from_str(line).unwrap();
        let mut log = OpenOptions::new()
            .create(true)
            .append(true)
            .open(root.join("plugin-calls.jsonl"))
            .unwrap();
        writeln!(log, "{}", json!({"call":call,"input":req,"envNames":std::env::vars().map(|(k,_)|k).collect::<Vec<_>>()})).unwrap();
        if scenario["failCall"] == *call {
            if scenario["failure"] == "flood" {
                std::io::stdout()
                    .write_all(&vec![b'x'; 9 * 1024 * 1024])
                    .unwrap();
            }
            sleep(Duration::from_secs(600));
        }
        let payload = match call.as_str() {
            "manifest" => json!({"manifest":scenario["manifest"]}),
            "probes" => {
                json!({"probes": if req["observations"].as_object().unwrap().is_empty() {scenario.get("probes").cloned().unwrap_or(json!([]))} else {json!([])}})
            }
            "reconcile" if scenario["reflect"] == true => {
                json!({"plan":{"status":{"observedGeneration":req["object"]["metadata"]["generation"],"observations":req["observations"]}}})
            }
            "reconcile" => json!({"plan": scenario["plan"]}),
            "observe" if scenario["observeUnknown"] == true => {
                json!({"unknown":{"why":"Throttled","retryAfterMs":1000}})
            }
            "observe" => {
                json!({"known": {"seen": true,"providerWon":std::env::var("PLUGIN_SECRET").ok().as_deref()==Some("provider-value")}})
            }
            "perform" => json!({"outcome": {"done": {"posted": true}}}),
            _ => panic!("unexpected call"),
        };
        let mut answer = payload;
        answer["id"] = req["id"].clone();
        answer["ok"] = json!(true);
        println!("{answer}");
    }
    ExitCode::SUCCESS
}
