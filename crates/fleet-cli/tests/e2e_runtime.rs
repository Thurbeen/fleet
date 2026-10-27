//! The thurbox runtime end to end: the real `fleet` binary, its file store,
//! TaskController, and the thurbox adapter driving a bounded stand-in for
//! `thurbox-cli` (`tests/stubs/stub.rs`). No real session or host is touched.

mod support;

use std::time::Duration;

use serde_json::{Value, json};
use support::{Env, alive_after, assert_condition, runtime_config};

/// A Task whose workspace is an absolute path, as thurbox requires.
fn task(name: &str, dispatch: bool) -> Value {
    let mut t = support::task(name, dispatch);
    let dir = if cfg!(windows) {
        format!("C:\\work\\{name}")
    } else {
        format!("/work/{name}")
    };
    t["spec"]["workspace"]["dir"] = json!(dir);
    t
}

fn setup(name: &str, extra: Vec<Value>) -> Env {
    let env = Env::thurbox(name);
    let mut items = runtime_config();
    items.as_array_mut().unwrap().extend(extra);
    env.apply(items).ok();
    env
}

/// The key the thurbox adapter gives a Task's session: the Task's uid under
/// the configured prefix (`fleet` by default). Short enough for thurbox's
/// 64-character keys whatever the Task's name.
fn thurbox_key(env: &Env, task: &str) -> String {
    let uid = env.get(task)["metadata"]["uid"]
        .as_str()
        .unwrap()
        .to_string();
    format!("fleet.{uid}")
}

fn result_args<'a>(task: &'a str, generation: &'a str) -> Vec<&'a str> {
    vec![
        "result",
        task,
        "--generation",
        generation,
        "--outcome",
        "shipped",
        "--output",
        "prUrl=\"https://example.invalid/pr/1\"",
    ]
}

fn set_session_state(env: &Env, key: &str, state: &str) {
    env.edit_stub(|s| {
        for sess in s["sessions"].as_array_mut().unwrap() {
            if sess["key"] == key {
                sess["state"] = json!(state);
            }
        }
    });
}

fn argv_of(env: &Env, sub: &str) -> Vec<Vec<String>> {
    env.stub_calls()
        .iter()
        .filter(|c| c["argv"][0] == "session" && c["argv"][1] == sub)
        .map(|c| {
            c["argv"]
                .as_array()
                .unwrap()
                .iter()
                .map(|a| a.as_str().unwrap().to_string())
                .collect()
        })
        .collect()
}

fn receipts(status: &Value) -> Vec<Value> {
    status["receipts"]
        .as_object()
        .map(|m| m.values().cloned().collect())
        .unwrap_or_default()
}

fn full_status(env: &Env, name: &str) -> Value {
    env.get(name)["status"].clone()
}

#[test]
fn a_task_lives_its_whole_life_on_thurbox_cli() {
    let env = setup("thurbox-life", vec![task("t1", true)]);
    env.reconcile().ok();

    let st = env.status("t1");
    assert_eq!(st["phase"], "Briefed", "{st:#}");
    let full = full_status(&env, "t1");
    assert_eq!(full["session"]["host"], "local", "{full:#}");

    let key = thurbox_key(&env, "t1");
    let sessions = env.stub_sessions();
    assert_eq!(sessions.len(), 1, "{sessions:#?}");
    assert_eq!(sessions[0]["key"], key.as_str());
    assert_eq!(sessions[0]["agent"], "claude-code");
    assert_eq!(sessions[0]["sent"], json!(["Do the work for t1."]));
    let create = &argv_of(&env, "create")[0];
    for want in ["--key", "--on-existing", "adopt", "--agent", "--json"] {
        assert!(create.iter().any(|a| a == want), "{want} in {create:?}");
    }
    assert!(
        !create.iter().any(|a| a == "--host"),
        "the local host is thurbox's own, not a --host: {create:?}"
    );

    env.fleet(&result_args("t1", "1")).ok();
    env.reconcile().ok();
    assert_eq!(env.status("t1")["phase"], "Concluded");

    // Releasing waits for the session to be at rest (P7), then deletes it.
    env.fleet(&["delete", "task", "t1"]).ok();
    env.reconcile().ok();
    assert_condition(&env.status("t1"), "Released", "False", "SessionBusy");
    assert_eq!(env.stub_sessions().len(), 1);
    set_session_state(&env, &key, "idle");
    env.reconcile().ok();
    assert!(env.stub_sessions().is_empty(), "{:#?}", env.stub_sessions());
    env.fleet(&["get", "task", "t1"]).code(3);
    env.assert_no_temp();
}

#[test]
fn a_host_that_cannot_be_read_is_unknown_for_its_own_tasks_only() {
    let mut on_remote = task("far", true);
    on_remote["spec"]["host"] = json!("remote");
    let env = setup("thurbox-unknown-host", vec![]);
    env.edit_stub(|s| s["hosts"] = json!({"remote": "up"}));
    env.apply(on_remote).ok();
    env.reconcile().ok();
    assert_eq!(env.status("far")["phase"], "Briefed");
    let create = &argv_of(&env, "create")[0];
    let at = create.iter().position(|a| a == "--host").expect("--host");
    assert_eq!(create[at + 1], "remote");

    // The remote host stops answering. Its Task is Unknown: no WorkerGone
    // clock, no respawn. A local Task carries on in the same run.
    env.edit_stub(|s| s["hosts"]["remote"] = json!("unknown"));
    env.apply(task("near", true)).ok();
    let report = env.reconcile().ok().json();
    assert_eq!(env.status("near")["phase"], "Briefed");
    let far = env.status("far");
    assert_eq!(far["phase"], "Briefed", "{far:#}");
    assert!(
        far["conditions"]
            .as_array()
            .unwrap()
            .iter()
            .all(|c| c["type"] != "WorkerGone"),
        "{far:#}"
    );
    assert!(
        report["unknowns"].to_string().contains("remote"),
        "{report:#}"
    );
    assert_eq!(argv_of(&env, "create").len(), 2, "nothing respawned");

    // A host thurbox says it cannot reach is False: nothing is spawned there.
    env.edit_stub(|s| s["hosts"]["remote"] = json!("down"));
    let mut blocked = task("blocked", true);
    blocked["spec"]["host"] = json!("remote");
    env.apply(blocked).ok();
    env.reconcile().ok();
    assert_condition(
        &env.status("blocked"),
        "HostReachable",
        "False",
        "Unreachable",
    );
    assert_eq!(argv_of(&env, "create").len(), 2);
}

#[test]
fn a_spawn_that_times_out_is_adopted_by_its_key_and_its_process_tree_is_killed() {
    let env = setup("thurbox-spawn-timeout", vec![]);
    // The session is created, then the answer never comes.
    env.stub_fault("create", "hang-after", 1);
    env.apply(task("t1", true)).ok();
    env.reconcile().ok();

    let st = env.status("t1");
    assert_eq!(st["phase"], "Briefed", "{st:#}");
    assert_condition(&st, "Spawned", "True", "Adopted");
    assert_eq!(env.stub_sessions().len(), 1, "never twinned");
    assert_eq!(argv_of(&env, "create").len(), 1, "never respawned blindly");
    let pids = env.grandchildren();
    assert!(!pids.is_empty(), "the stub hung with a grandchild");
    for pid in pids {
        assert!(
            !alive_after(pid, Duration::from_secs(10)),
            "process {pid} outlived the timeout: the whole tree must be killed"
        );
    }
}

#[test]
fn an_orphan_session_holding_the_key_is_adopted_not_twinned() {
    let env = setup("thurbox-orphan", vec![task("t1", false)]);
    let key = thurbox_key(&env, "t1");
    // A manager that died after its create left this session behind.
    env.edit_stub(|s| {
        s["sessions"] = json!([{
            "id": "00000000-0000-4000-8000-000000000099", "key": key, "host": "local",
            "name": key, "state": "idle", "stopped": false, "meta": {}, "sent": [], "env": []
        }]);
    });
    env.apply(task("t1", true)).ok();
    env.reconcile().ok();
    assert_eq!(env.stub_sessions().len(), 1);
    assert_eq!(env.status("t1")["phase"], "Briefed");
    let adopted = receipts(&full_status(&env, "t1"))
        .iter()
        .any(|r| r["outcome"]["known"]["adopted"] == true);
    assert!(adopted, "{:#}", full_status(&env, "t1"));
}

#[test]
fn a_brief_whose_answer_was_lost_is_read_back_and_never_sent_twice() {
    // Recovered: the session recorded the delivery, the answer was lost.
    let env = setup("thurbox-brief-recovered", vec![]);
    // meta-set #1 marks the brief pending, #2 marks it delivered.
    env.stub_fault_after("meta-set", "hang-after", 1, 1);
    env.apply(task("t1", true)).ok();
    env.reconcile().ok();
    let st = env.status("t1");
    assert_eq!(st["phase"], "Briefed", "{st:#}");
    assert_condition(&st, "Briefed", "True", "ReceiptRecovered");
    assert_eq!(argv_of(&env, "send").len(), 1);

    // Unconfirmed: the send itself never answered. Fleet cannot tell, so it
    // asks for a person rather than typing the brief again.
    let env = setup("thurbox-brief-unconfirmed", vec![]);
    env.stub_fault("send", "hang-after", 1);
    env.apply(task("t1", true)).ok();
    env.reconcile().ok();
    for _ in 0..3 {
        env.expire_wait("t1");
        env.reconcile().ok();
    }
    let st = env.status("t1");
    assert_eq!(st["phase"], "Spawned", "{st:#}");
    assert_condition(&st, "Briefed", "Unknown", "BriefUnconfirmed");
    assert_condition(&st, "NeedsAttention", "True", "BriefUnconfirmed");
    assert_eq!(argv_of(&env, "send").len(), 1, "never a second brief");

    // A send that fails may have typed part of the brief before failing:
    // thurbox-cli cannot say nothing arrived, so neither does fleet.
    let env = setup("thurbox-brief-failed-send", vec![]);
    env.stub_fault("send", "fail", 1);
    env.apply(task("t1", true)).ok();
    env.reconcile().ok();
    env.expire_wait("t1");
    env.reconcile().ok();
    let st = env.status("t1");
    assert_eq!(st["phase"], "Spawned", "{st:#}");
    assert_condition(&st, "Briefed", "Unknown", "BriefUnconfirmed");
    assert_eq!(argv_of(&env, "send").len(), 1, "never typed twice");
}

#[test]
fn a_release_whose_answer_is_unknown_is_never_assumed_done() {
    let env = setup("thurbox-release", vec![task("t1", true), task("t2", true)]);
    env.reconcile().ok();
    for t in ["t1", "t2"] {
        let k = thurbox_key(&env, t);
        set_session_state(&env, &k, "idle");
    }

    // t1: the delete happened but its answer was lost; the next attempt finds
    // the session gone and records the release without deleting twice.
    env.stub_fault("delete", "hang-after", 1);
    env.fleet(&["delete", "task", "t1"]).ok();
    env.reconcile().ok();
    assert_eq!(env.status("t1")["finalizers"], json!(["fleet.dev/release"]));
    env.expire_wait("t1");
    env.reconcile().ok();
    env.fleet(&["get", "task", "t1"]).code(3);
    assert_eq!(argv_of(&env, "delete").len(), 1);

    // t2: the delete never answers. After three attempts, each after a
    // wait, a person is asked; the finalizer holds the record meanwhile.
    env.stub_fault("delete", "hang", 3);
    env.fleet(&["delete", "task", "t2"]).ok();
    env.reconcile().ok();
    for _ in 0..2 {
        env.expire_wait("t2");
        env.reconcile().ok();
    }
    let st = env.status("t2");
    assert_condition(&st, "Released", "False", "ReleaseFailed");
    assert_condition(&st, "NeedsAttention", "True", "ReleaseFailed");
    assert_eq!(st["finalizers"], json!(["fleet.dev/release"]));
    assert_eq!(argv_of(&env, "delete").len(), 4);
}

#[test]
fn release_never_deletes_a_different_session_that_reuses_the_key() {
    let env = setup("thurbox-release-identity", vec![task("t1", true)]);
    env.reconcile().ok();
    let key = thurbox_key(&env, "t1");
    let original_id = env.stub_sessions()[0]["id"].clone();
    let replacement_id = "00000000-0000-4000-8000-000000000999";
    env.edit_stub(|s| {
        let mut replacement = s["sessions"][0].clone();
        replacement["id"] = json!(replacement_id);
        replacement["state"] = json!("working");
        // The stored session is idle, but no longer holds this Task's key.
        s["sessions"][0]["key"] = json!("retired-key");
        s["sessions"][0]["state"] = json!("idle");
        s["sessions"].as_array_mut().unwrap().push(replacement);
    });
    env.fleet(&["delete", "task", "t1"]).ok();
    env.reconcile().ok();
    assert!(
        env.stub_sessions()
            .iter()
            .any(|s| s["id"] == replacement_id && s["state"] == "working"),
        "a different session's working state was never checked: {:#?}",
        env.stub_sessions()
    );
    assert_eq!(argv_of(&env, "delete").len(), 0);
    assert_eq!(env.get("t1")["status"]["session"]["id"], original_id);
    assert_eq!(
        env.get("t1")["metadata"]["finalizers"],
        json!(["fleet.dev/release"])
    );
    assert_eq!(
        env.stub_sessions()
            .iter()
            .filter(|s| s["key"] == key)
            .count(),
        1
    );
}

#[test]
fn a_different_session_reusing_the_key_does_not_hide_a_vanished_worker() {
    let env = setup("thurbox-worker-identity", vec![task("t1", true)]);
    env.reconcile().ok();
    let original_id = env.stub_sessions()[0]["id"].clone();
    env.edit_stub(|s| {
        // The real worker vanished; another session now holds its key.
        s["sessions"][0]["id"] = json!("00000000-0000-4000-8000-000000000999");
    });
    env.reconcile().ok();
    let st = env.status("t1");
    assert_condition(&st, "WorkerGone", "True", "SessionGone");
    assert_condition(&st, "NeedsAttention", "True", "WorkerGone");
    assert_eq!(env.get("t1")["status"]["session"]["id"], original_id);
    assert_eq!(argv_of(&env, "create").len(), 1, "never respawned");
    assert_eq!(argv_of(&env, "send").len(), 1, "never rebriefed");
}

#[test]
fn a_relative_workspace_is_refused_before_any_session_is_made() {
    let mut t = support::task("rel", true);
    t["spec"]["workspace"]["dir"] = json!("work/rel");
    let env = setup("thurbox-relative", vec![t]);
    env.reconcile().ok();
    let st = env.status("rel");
    let spawned = support::condition(&st, "Spawned");
    assert!(
        spawned["message"].as_str().unwrap().contains("absolute"),
        "{st:#}"
    );
    assert_eq!(argv_of(&env, "create").len(), 0, "thurbox is never asked");
}

#[test]
fn a_new_store_never_adopts_a_session_an_old_store_left_behind() {
    let env = setup("thurbox-two-stores", vec![task("t1", true)]);
    env.reconcile().ok();
    assert_eq!(env.stub_sessions().len(), 1);
    // A fresh data directory on the same hosts: same names, same order.
    let cfg = std::fs::read_to_string(&env.config).unwrap();
    std::fs::write(
        &env.config,
        cfg.replace("dir = \"data\"", "dir = \"data2\""),
    )
    .unwrap();
    let mut items = runtime_config();
    items.as_array_mut().unwrap().push(task("t1", true));
    env.apply(items).ok();
    env.reconcile().ok();
    assert_eq!(
        env.stub_sessions().len(),
        2,
        "the second store's t1 has its own session: {:#?}",
        env.stub_sessions()
    );
}

#[test]
fn slow_probes_never_outlive_the_lease() {
    let env = setup("thurbox-slow", vec![task("t1", true)]);
    // Each call fits the lease; the probes of one Task together do not.
    let cfg = std::fs::read_to_string(&env.config).unwrap();
    std::fs::write(
        &env.config,
        cfg.replace("callTimeoutMs = 3000", "callTimeoutMs = 2000"),
    )
    .unwrap();
    env.stub_fault("list", "slow", 2);
    env.stub_fault("doctor", "slow", 2);
    // A regression guard, not a proof of the per-probe renewal: the lease
    // must outlast a brief's four calls with their grace, and no Task's
    // probes add up to more than that.
    env.fleet(&["reconcile", "--json", "--lease-ttl-ms", "30000"])
        .ok();
    assert_eq!(env.status("t1")["phase"], "Briefed");
}

#[test]
fn the_lease_outlasts_one_whole_runtime_operation() {
    // A brief is four thurbox-cli calls: a lease that cannot hold four of
    // them is refused before anything runs.
    let env = setup("thurbox-op-bound", vec![task("t1", true)]);
    let cfg = std::fs::read_to_string(&env.config).unwrap();
    std::fs::write(
        &env.config,
        cfg.replace("callTimeoutMs = 3000", "callTimeoutMs = 2000"),
    )
    .unwrap();
    env.fleet(&["reconcile", "--lease-ttl-ms", "5000"])
        .code(1)
        .stderr_has("lease");
    assert_eq!(argv_of(&env, "create").len(), 0);
    // The default lease is sized to hold it.
    env.reconcile().ok();
    assert_eq!(env.status("t1")["phase"], "Briefed");
}

#[test]
fn a_spec_that_would_reach_thurbox_cli_as_a_flag_is_refused_when_written() {
    let env = setup("thurbox-written-bounds", vec![]);
    for (field, value) in [("harness", "--help"), ("host", "-x")] {
        let mut t = task("dash", false);
        t["spec"][field] = json!(value);
        env.apply(t).code(1).stderr_has(field);
    }
    env.fleet(&["get", "task", "dash"]).code(3);
}

#[test]
fn a_brief_that_starts_with_a_dash_is_sent_as_text() {
    let mut t = task("dash", true);
    t["spec"]["brief"] = json!("- fix the failing test");
    let env = setup("thurbox-dash-brief", vec![t]);
    env.reconcile().ok();
    let st = env.status("dash");
    assert_eq!(st["phase"], "Briefed", "{st:#}");
    assert_eq!(
        env.stub_sessions()[0]["sent"],
        json!(["- fix the failing test"])
    );
}

/// A fresh session before any hook, as thurbox classifies it: a running
/// process. Fleet reads no screen and knows no agent: only a thurbox state of
/// idle or done lets a brief go out, and the condition names the state.
#[test]
fn a_brief_waits_for_a_thurbox_state_that_says_ready() {
    let cases = [
        (
            "running",
            json!({"state_source": "process", "hook_reported": false}),
        ),
        ("unknown", json!({})),
        ("working", json!({})),
        ("blocked", json!({})),
        ("idle", json!({"stopped": true})),
    ];
    for (state, fields) in cases {
        let env = setup(&format!("unclassified-{state}"), vec![]);
        env.edit_stub(|s| {
            s["spawnState"] = json!(state);
            s["spawnFields"] = fields.clone();
        });
        let mut t = task("first", true);
        t["spec"]["harness"] = json!("codex");
        env.apply(t).ok();
        env.reconcile().ok();
        assert_eq!(argv_of(&env, "send").len(), 0, "{state} {fields}");
        assert_eq!(env.status("first")["phase"], "Spawned", "{state}");
    }

    let env = setup("unclassified-then-idle", vec![]);
    env.edit_stub(|s| s["spawnState"] = json!("running"));
    let mut t = task("first", true);
    t["spec"]["harness"] = json!("codex");
    env.apply(t).ok();
    env.reconcile().ok();
    let st = full_status(&env, "first");
    assert_condition(&st, "Briefed", "Unknown", "ReadyUnknown");
    let msg = support::condition(&st, "Briefed")["message"].to_string();
    assert!(msg.contains("running") && !msg.contains("odex"), "{msg}");
    // Each reconcile is a new CLI process: reconnect keeps identity and receipt.
    let id = env.stub_sessions()[0]["id"].clone();
    set_session_state(&env, &thurbox_key(&env, "first"), "idle");
    env.reconcile().ok();
    env.reconcile().ok();
    assert_eq!(env.status("first")["phase"], "Briefed");
    assert_eq!(env.stub_sessions()[0]["id"], id);
    assert_eq!(argv_of(&env, "send").len(), 1);
    assert_eq!(argv_of(&env, "create").len(), 1);
    assert_eq!(argv_of(&env, "capture").len(), 0, "no screen is read");
    // Neither subagents still at work nor a running process is proof of
    // rest: deletion waits.
    set_session_state(&env, &thurbox_key(&env, "first"), "background");
    env.fleet(&["delete", "task", "first"]).ok();
    env.reconcile().ok();
    let st = full_status(&env, "first");
    assert_condition(&st, "Released", "False", "SessionBusy");
    set_session_state(&env, &thurbox_key(&env, "first"), "running");
    env.reconcile().ok();
    assert_eq!(argv_of(&env, "delete").len(), 0);
}

/// A draft change on the fake forge with one note per `(id, body)`.
fn change_with(notes: &[(&str, &str)]) -> fleet_api::forge::ChangeSnapshot {
    use fleet_api::forge::*;
    let notes: Vec<Note> = notes
        .iter()
        .enumerate()
        .map(|(i, (id, body))| Note {
            id: (*id).into(),
            kind: NoteKind::Comment,
            author: "reviewer".into(),
            bot: false,
            thread: None,
            resolved: None,
            path: None,
            line: None,
            at: 10 * (i as u64 + 1),
            edited_at: None,
            url: format!("https://forge.test/acme/widgets/pull/7#note-{id}"),
            body: (*body).into(),
        })
        .collect();
    ChangeSnapshot {
        state: ChangeState::Draft,
        head: HeadSeen {
            id: "aaa111".into(),
            checks: Checks::Pending,
            failing: Vec::new(),
        },
        review_decision: ReviewDecision::None,
        mergeable: Mergeable {
            value: Some(false),
            reason: Some("draft".into()),
        },
        threads: Threads { unresolved: 0 },
        reviews: Vec::new(),
        notes,
    }
}

/// Readiness is thurbox's to report, whatever agent runs in the session. A
/// row thurbox classifies only as a running process (no hook has said idle)
/// is never sent to: the declared change's feedback waits under an
/// attention that names the thurbox state, and goes out once thurbox
/// reports the session idle. The harness name reaches thurbox verbatim.
#[test]
fn feedback_waits_for_a_thurbox_state_that_says_ready_whatever_the_agent() {
    const URL: &str = "https://forge.test/acme/widgets/pull/7";
    let mut env = Env::thurbox_forge("thurbox-unclassified-ready");
    env.set_var("USER", "tester");
    let forge = env.fake_forge();
    forge
        .update(|s| s.login = Some("fleet-bot".into()))
        .unwrap();
    forge
        .set_change("acme/widgets", "7", change_with(&[("n1", "first look")]))
        .unwrap();
    env.apply(runtime_config()).ok();
    let mut t = task("t1", true);
    t["spec"]["harness"] = json!("claude-custom");
    env.apply(t).ok();
    env.reconcile().ok();
    assert_eq!(env.status("t1")["phase"], "Briefed");
    let create = &argv_of(&env, "create")[0];
    assert!(
        create.windows(2).any(|a| a == ["--agent", "claude-custom"]),
        "the harness name is thurbox's agent name, unchanged: {create:?}"
    );

    // The session as thurbox shows it once its hooks fall silent.
    let key = thurbox_key(&env, "t1");
    env.edit_stub(|s| {
        for sess in s["sessions"].as_array_mut().unwrap() {
            if sess["key"] == key.as_str() {
                let fields = json!({
                    "state": "running", "state_source": "process",
                    "hook_reported": false, "hook_state": null,
                    "hook_corroboration": "foreign-agent", "foreground_process": "claude"
                });
                sess.as_object_mut()
                    .unwrap()
                    .extend(fields.as_object().unwrap().clone());
            }
        }
    });
    env.fleet(&["declare", "t1", "--change-request", URL]).ok();
    let cr = support::derived_name("fake", support::FORGE_HOST, "acme/widgets", "7");
    forge
        .set_change(
            "acme/widgets",
            "7",
            change_with(&[("n1", "first look"), ("n2", "please add a test")]),
        )
        .unwrap();
    for _ in 0..3 {
        env.expire_wait_of("ChangeRequest", &cr);
        let run = env.reconcile();
        let said = format!("{}{}", run.stdout, run.stderr);
        assert!(!said.contains("Codex"), "{said}");
    }
    assert_eq!(env.stub_sessions()[0]["sent"].as_array().unwrap().len(), 1);
    let st = full_status(&env, "t1");
    assert!(!st["inbox"].as_array().unwrap().is_empty(), "{st:#}");
    assert_condition(&st, "NeedsAttention", "True", "ReadyUnknown");
    let msg = support::condition(&st, "NeedsAttention")["message"]
        .as_str()
        .unwrap()
        .to_string();
    assert!(
        msg.contains("\"running\""),
        "names the thurbox state: {msg}"
    );
    for agent in ["odex", "laude", "agent"] {
        assert!(!msg.contains(agent), "says nothing about agents: {msg}");
    }

    // Background (subagents still running) is thurbox's word for a session
    // still busy: nothing is sent, and nothing pages the operator for it.
    set_session_state(&env, &key, "background");
    env.reconcile().ok();
    assert_eq!(env.stub_sessions()[0]["sent"].as_array().unwrap().len(), 1);
    let st = full_status(&env, "t1");
    assert_condition(&st, "NeedsAttention", "False", "Resolved");

    // Thurbox reports it idle: the join and the feedback go out, one per
    // pass, each to a session observed idle on that pass.
    for _ in 0..4 {
        set_session_state(&env, &key, "idle");
        env.reconcile().ok();
    }
    let sent = env.stub_sessions()[0]["sent"].clone();
    let sent: Vec<&str> = sent
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap())
        .collect();
    assert!(
        sent.iter().any(|t| t.contains("> please add a test")),
        "{sent:#?}"
    );
    let st = full_status(&env, "t1");
    assert_condition(&st, "NeedsAttention", "False", "Resolved");
}
