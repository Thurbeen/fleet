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
    assert!(
        !create.iter().any(|a| a == "--add-dir" || a == "--add-repo"),
        "a legacy Task stays a single-workspace session: {create:?}"
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
fn a_task_attaches_prepared_additional_workspaces_to_one_session() {
    let mut t = task("many", true);
    let additional = if cfg!(windows) {
        vec!["D:\\work\\api", "D:\\work\\docs with spaces"]
    } else {
        vec!["/work/api", "/work/docs with spaces"]
    };
    t["spec"]["workspace"]["additionalDirs"] = json!(additional);
    let env = setup("thurbox-multi-workspace", vec![t]);

    env.reconcile().ok();

    assert_eq!(env.status("many")["phase"], "Briefed");
    let create = &argv_of(&env, "create")[0];
    let attached: Vec<&str> = create
        .windows(2)
        .filter(|pair| pair[0] == "--add-dir")
        .map(|pair| pair[1].as_str())
        .collect();
    assert_eq!(attached, additional);
    assert!(
        !create.iter().any(|arg| arg == "--add-repo"),
        "prepared workspaces must be attached as-is: {create:?}"
    );
    assert_eq!(env.stub_sessions()[0]["additional_dirs"], json!(additional));

    let mut changed = task("many", true);
    changed["spec"]["workspace"]["additionalDirs"] = json!([additional[0]]);
    env.apply(changed)
        .code(1)
        .stderr_has("Task spec is immutable");

    let key = thurbox_key(&env, "many");
    set_session_state(&env, &key, "idle");
    env.fleet(&["delete", "task", "many"]).ok();
    env.reconcile().ok();
    env.fleet(&["get", "task", "many"]).code(3);
    assert!(env.stub_sessions().is_empty());
}

#[test]
fn a_task_can_ask_thurbox_to_provision_a_multi_repository_worktree() {
    let (primary, attached, repositories) = if cfg!(windows) {
        (
            "C:\\source\\primary repo",
            "D:\\prepared\\docs repo",
            vec!["D:\\source\\api repo", "D:\\source\\client@source"],
        )
    } else {
        (
            "/source/primary repo",
            "/prepared/docs repo",
            vec!["/source/api repo", "/source/client@source"],
        )
    };
    let mut t = task("provision", true);
    t["spec"]["host"] = json!("build-host");
    t["spec"]["workspace"] = json!({
        "dir": primary,
        "additionalDirs": [attached],
        "worktree": {
            "branch": "feature/task-worktrees",
            "baseBranch": "release/base",
            "additionalRepositories": repositories
                .iter()
                .map(|path| json!({"path": path}))
                .collect::<Vec<_>>()
        }
    });
    let env = setup("thurbox-provision-worktrees", vec![]);
    env.edit_stub(|s| s["hosts"] = json!({"build-host": "up"}));
    env.apply(t).ok();

    env.reconcile().ok();

    assert_eq!(env.status("provision")["phase"], "Briefed");
    let create = &argv_of(&env, "create")[0];
    let value_after = |flag: &str| {
        let at = create.iter().position(|arg| arg == flag).unwrap();
        create[at + 1].as_str()
    };
    assert_eq!(value_after("--repo-path"), primary);
    assert_eq!(value_after("--worktree-branch"), "feature/task-worktrees");
    assert_eq!(value_after("--base-branch"), "release/base");
    assert_eq!(value_after("--add-dir"), attached);
    assert_eq!(value_after("--host"), "build-host");
    assert!(!create.iter().any(|arg| arg == "--existing-worktree"));
    let added: Vec<&str> = create
        .windows(2)
        .filter(|pair| pair[0] == "--add-repo")
        .map(|pair| pair[1].as_str())
        .collect();
    assert_eq!(
        added,
        repositories
            .iter()
            .map(|path| format!("{path}@release/base"))
            .collect::<Vec<_>>()
    );

    let session = &full_status(&env, "provision")["session"];
    assert_ne!(session["workspace"]["cwd"], primary);
    assert_eq!(session["workspace"]["baseBranch"], "release/base");
    assert_eq!(session["workspace"]["additionalDirs"], json!([attached]));
    let worktrees = session["workspace"]["worktrees"].as_array().unwrap();
    assert_eq!(worktrees.len(), 3, "{session:#}");
    assert_eq!(worktrees[0]["repository"], primary);
    assert_eq!(worktrees[0]["branch"], "feature/task-worktrees");
    assert_eq!(worktrees[0]["createdByRuntime"], true);
    assert_eq!(worktrees[2]["repository"], repositories[1]);
}

#[test]
fn an_unverifiable_existing_primary_worktree_is_refused() {
    let (primary, attached, repository) = if cfg!(windows) {
        (
            "C:\\prepared\\primary repo",
            "D:\\prepared\\notes",
            "D:\\source\\client@source",
        )
    } else {
        (
            "/prepared/primary repo",
            "/prepared/notes",
            "/source/client@source",
        )
    };
    let mut t = task("mixed", true);
    t["spec"]["workspace"] = json!({
        "dir": primary,
        "additionalDirs": [attached],
        "worktree": {
            "branch": "feature/mixed",
            "primary": "existing",
            "additionalRepositories": [{"path": repository}]
        }
    });
    let env = setup("thurbox-existing-worktree-refused", vec![]);
    env.apply(t).code(1).stderr_has("unknown field `primary`");
    assert!(env.stub_sessions().is_empty());
}

#[test]
fn worktree_intent_validation_refuses_ambiguous_or_conflicting_input() {
    let env = setup("thurbox-worktree-validation", vec![]);
    let source = if cfg!(windows) {
        "C:\\source\\primary"
    } else {
        "/source/primary"
    };
    let cases = [
        (
            "empty-branch",
            json!({"branch": ""}),
            "worktree.branch must not be empty",
        ),
        (
            "ambiguous-base",
            json!({"branch": "feature/x", "baseBranch": "release@candidate"}),
            "baseBranch must not contain '@'",
        ),
        (
            "duplicate-source",
            json!({"branch": "feature/x", "additionalRepositories": [{"path": source}]}),
            "duplicates another workspace or repository path",
        ),
        (
            "per-repository-base",
            json!({"branch": "feature/x", "additionalRepositories": [{"path": "/source/other", "baseBranch": "other"}]}),
            "unknown field `baseBranch`",
        ),
    ];
    for (name, worktree, message) in cases {
        let mut t = task(name, false);
        t["spec"]["workspace"]["dir"] = json!(source);
        t["spec"]["workspace"]["worktree"] = worktree;
        env.apply(t).code(1).stderr_has(message);
    }
}

#[test]
fn a_lost_provisioning_response_is_adopted_with_resolved_membership() {
    let mut t = task("recover", true);
    t["spec"]["workspace"]["worktree"] = json!({
        "branch": "feature/recover",
        "baseBranch": "stable",
        "additionalRepositories": [{"path": if cfg!(windows) { "D:\\source\\other" } else { "/source/other" }}]
    });
    let env = setup("thurbox-provision-recovery", vec![]);
    env.stub_fault("create", "hang-after", 1);
    env.apply(t).ok();

    env.reconcile().ok();
    env.reconcile().ok();

    let status = env.status("recover");
    assert_eq!(status["phase"], "Briefed", "{status:#}");
    assert_condition(&status, "Spawned", "True", "Adopted");
    assert_eq!(env.stub_sessions().len(), 1);
    assert_eq!(argv_of(&env, "create").len(), 1, "never provisions twice");
    assert_eq!(argv_of(&env, "send").len(), 1);
    assert_eq!(
        full_status(&env, "recover")["session"]["workspace"]["baseBranch"],
        "stable"
    );
}

#[test]
fn provisioned_worktree_release_holds_without_cleanliness_evidence() {
    let mut t = task("release-worktree", true);
    t["spec"]["workspace"]["worktree"] = json!({"branch": "feature/release"});
    let env = setup("thurbox-provision-release", vec![t]);
    env.reconcile().ok();
    let key = thurbox_key(&env, "release-worktree");
    set_session_state(&env, &key, "idle");

    env.fleet(&["delete", "task", "release-worktree"]).ok();
    env.reconcile().ok();

    let status = env.status("release-worktree");
    assert_eq!(status["phase"], "Releasing", "{status:#}");
    assert_condition(&status, "Released", "False", "ReleaseRetry");
    assert!(
        status
            .to_string()
            .contains("cannot verify that runtime-owned worktrees are clean"),
        "{status:#}"
    );
    assert_eq!(argv_of(&env, "delete").len(), 0);
    assert_eq!(status["finalizers"], json!(["fleet.dev/release"]));
}

#[test]
fn partial_or_unsupported_runtime_provisioning_stays_unknown_or_stalls() {
    let mut partial = task("partial", true);
    partial["spec"]["workspace"]["worktree"] = json!({"branch": "feature/partial"});
    let env = setup("thurbox-partial-provisioning", vec![]);
    env.edit_stub(|s| s["ignoreWorktrees"] = json!(true));
    env.apply(partial).ok();
    let report = env.reconcile().ok().json();
    assert_eq!(env.status("partial")["phase"], "Spawning");
    assert!(
        report["unknowns"]
            .to_string()
            .contains("conflicting worktree membership"),
        "{report:#}"
    );
    assert_eq!(argv_of(&env, "create").len(), 1);
    assert_eq!(argv_of(&env, "send").len(), 0);

    let mut conflict = task("conflict", true);
    conflict["spec"]["workspace"]["worktree"] =
        json!({"branch": "feature/conflict", "baseBranch": "expected"});
    let env = setup("thurbox-conflicting-provisioning", vec![]);
    env.edit_stub(|s| s["spawnFields"] = json!({"base_branch": "stale"}));
    env.apply(conflict).ok();
    let report = env.reconcile().ok().json();
    assert_eq!(env.status("conflict")["phase"], "Spawning");
    assert!(
        report["unknowns"]
            .to_string()
            .contains("conflicting base branch"),
        "{report:#}"
    );
    assert_eq!(argv_of(&env, "create").len(), 1);
    assert_eq!(argv_of(&env, "send").len(), 0);

    let mut unsupported = task("unsupported", true);
    unsupported["spec"]["workspace"]["worktree"] = json!({"branch": "feature/unsupported"});
    let env = setup("thurbox-unsupported-provisioning", vec![]);
    env.edit_stub(|s| s["unsupportedWorktrees"] = json!(true));
    env.apply(unsupported).ok();
    env.reconcile().ok();
    for _ in 0..2 {
        env.expire_wait("unsupported");
        env.reconcile().ok();
    }
    let status = env.status("unsupported");
    assert_eq!(status["phase"], "Stalled", "{status:#}");
    assert_condition(&status, "Spawned", "False", "SpawnFailed");
    assert!(
        status
            .to_string()
            .contains("unknown argument '--worktree-branch'"),
        "{status:#}"
    );
    assert_eq!(argv_of(&env, "create").len(), 3);
    assert_eq!(argv_of(&env, "send").len(), 0);
}

#[test]
fn additional_workspace_validation_is_strict_without_rewriting_host_paths() {
    let env = setup("thurbox-multi-workspace-validation", vec![]);
    let primary = if cfg!(windows) {
        "C:\\work\\primary"
    } else {
        "/work/primary"
    };
    let duplicate = if cfg!(windows) {
        "D:\\work\\duplicate"
    } else {
        "/work/duplicate"
    };
    for (name, dirs, message) in [
        ("empty", vec![""], "must not be empty"),
        ("blank", vec!["   "], "must not be empty"),
        ("nul", vec!["/work/has\0nul"], "must not contain NUL"),
        (
            "primary-duplicate",
            vec![primary],
            "duplicates another workspace directory",
        ),
        (
            "additional-duplicate",
            vec![duplicate, duplicate],
            "duplicates another workspace directory",
        ),
    ] {
        let mut t = task(name, false);
        t["spec"]["workspace"]["dir"] = json!(primary);
        t["spec"]["workspace"]["additionalDirs"] = json!(dirs);
        env.apply(t).code(1).stderr_has(message);
    }
}

#[test]
fn remote_windows_workspace_spellings_keep_their_argv_boundaries() {
    let mut t = task("windows-paths", true);
    t["spec"]["host"] = json!("windows-host");
    t["spec"]["workspace"] = json!({
        "dir": "C:\\work\\primary repo",
        "additionalDirs": ["D:\\work\\client repo", "\\\\server\\share\\docs repo"]
    });
    let env = setup("thurbox-remote-windows-paths", vec![]);
    env.edit_stub(|s| s["hosts"] = json!({"windows-host": "up"}));
    env.apply(t).ok();

    env.reconcile().ok();

    assert_eq!(env.status("windows-paths")["phase"], "Briefed");
    let create = &argv_of(&env, "create")[0];
    let repo = create.iter().position(|arg| arg == "--repo-path").unwrap();
    assert_eq!(create[repo + 1], "C:\\work\\primary repo");
    let attached: Vec<&str> = create
        .windows(2)
        .filter(|pair| pair[0] == "--add-dir")
        .map(|pair| pair[1].as_str())
        .collect();
    assert_eq!(
        attached,
        ["D:\\work\\client repo", "\\\\server\\share\\docs repo"]
    );
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
    let mut t = task("t1", true);
    t["spec"]["workspace"]["additionalDirs"] = if cfg!(windows) {
        json!(["D:\\work\\second"])
    } else {
        json!(["/work/second"])
    };
    env.apply(t).ok();
    env.reconcile().ok();
    env.reconcile().ok();

    let st = env.status("t1");
    assert_eq!(st["phase"], "Briefed", "{st:#}");
    assert_condition(&st, "Spawned", "True", "Adopted");
    assert_eq!(env.stub_sessions().len(), 1, "never twinned");
    assert_eq!(argv_of(&env, "create").len(), 1, "never respawned blindly");
    assert_eq!(argv_of(&env, "send").len(), 1, "the brief stays idempotent");
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
            "name": key, "state": "idle", "stopped": false, "meta": {}, "sent": [], "env": [],
            "cwd": if cfg!(windows) { "C:\\work\\t1" } else { "/work/t1" },
            "additional_dirs": []
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
fn a_matching_key_with_conflicting_workspace_membership_is_unknown() {
    let mut t = task("many", false);
    t["spec"]["workspace"]["additionalDirs"] = if cfg!(windows) {
        json!(["D:\\work\\declared"])
    } else {
        json!(["/work/declared"])
    };
    let env = setup("thurbox-workspace-conflict", vec![t.clone()]);
    let key = thurbox_key(&env, "many");
    env.edit_stub(|s| {
        s["sessions"] = json!([{
            "id": "00000000-0000-4000-8000-000000000099", "key": key, "host": "local",
            "name": key, "state": "idle", "stopped": false, "meta": {}, "sent": [], "env": [],
            "cwd": if cfg!(windows) { "C:\\work\\many" } else { "/work/many" },
            "additional_dirs": if cfg!(windows) { json!(["D:\\work\\other"]) } else { json!(["/work/other"]) }
        }]);
    });
    t["spec"]["dispatch"] = json!(true);
    env.apply(t).ok();

    let report = env.reconcile().ok().json();

    assert_eq!(env.status("many")["phase"], "Spawning");
    assert!(
        report["unknowns"]
            .to_string()
            .contains("conflicting workspace"),
        "{report:#}"
    );
    assert_eq!(argv_of(&env, "create").len(), 1, "no duplicate startup");
    assert_eq!(argv_of(&env, "send").len(), 0, "no wrong worker is briefed");
}

#[test]
fn a_new_session_is_verified_before_fleet_adopts_or_briefs_it() {
    let mut t = task("many", true);
    t["spec"]["workspace"]["additionalDirs"] = if cfg!(windows) {
        json!(["D:\\work\\declared"])
    } else {
        json!(["/work/declared"])
    };
    let env = setup("thurbox-created-workspace-conflict", vec![]);
    env.edit_stub(|s| s["ignoreAddDirs"] = json!(true));
    env.apply(t).ok();

    let report = env.reconcile().ok().json();

    assert_eq!(env.status("many")["phase"], "Spawning");
    assert!(
        report["unknowns"]
            .to_string()
            .contains("conflicting workspace"),
        "{report:#}"
    );
    assert_eq!(argv_of(&env, "create").len(), 1);
    assert_eq!(argv_of(&env, "send").len(), 0, "no wrong worker is briefed");
}

#[test]
fn a_recorded_session_with_changed_workspace_membership_is_not_briefed() {
    let mut t = task("many", true);
    t["spec"]["workspace"]["additionalDirs"] = if cfg!(windows) {
        json!(["D:\\work\\declared"])
    } else {
        json!(["/work/declared"])
    };
    let env = setup("thurbox-recorded-workspace-conflict", vec![]);
    env.edit_stub(|s| s["spawnState"] = json!("working"));
    env.apply(t).ok();
    env.reconcile().ok();
    assert_eq!(env.status("many")["phase"], "Spawned");

    let key = thurbox_key(&env, "many");
    env.edit_stub(|s| {
        let session = s["sessions"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .find(|session| session["key"] == key)
            .unwrap();
        session["state"] = json!("idle");
        session["additional_dirs"] = if cfg!(windows) {
            json!(["D:\\work\\other"])
        } else {
            json!(["/work/other"])
        };
    });

    let report = env.reconcile().ok().json();

    assert_eq!(env.status("many")["phase"], "Spawned", "{report:#}");
    assert!(
        report["unknowns"]
            .to_string()
            .contains("conflicting workspace"),
        "{report:#}"
    );
    assert_eq!(argv_of(&env, "send").len(), 0, "no wrong worker is briefed");
}

#[test]
fn a_briefed_session_with_changed_workspace_membership_gets_no_inbox_message() {
    let mut t = task("many", true);
    t["spec"]["workspace"]["additionalDirs"] = if cfg!(windows) {
        json!(["D:\\work\\declared"])
    } else {
        json!(["/work/declared"])
    };
    let env = setup("thurbox-briefed-workspace-conflict", vec![t]);
    env.reconcile().ok();
    env.fleet(&["send", "many", "new instructions"]).ok();

    let key = thurbox_key(&env, "many");
    env.edit_stub(|s| {
        let session = s["sessions"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .find(|session| session["key"] == key)
            .unwrap();
        session["additional_dirs"] = if cfg!(windows) {
            json!(["D:\\work\\other"])
        } else {
            json!(["/work/other"])
        };
    });

    let report = env.reconcile().ok().json();

    assert!(
        report["unknowns"]
            .to_string()
            .contains("conflicting workspace"),
        "{report:#}"
    );
    assert_eq!(
        argv_of(&env, "send").len(),
        1,
        "only the original brief was sent"
    );
    assert_eq!(env.status("many")["inbox"].as_array().unwrap().len(), 1);
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

    let mut t = task("relative-extra", true);
    t["spec"]["workspace"]["additionalDirs"] = json!(["work/other"]);
    env.apply(t).ok();
    env.reconcile().ok();
    let st = env.status("relative-extra");
    assert!(
        support::condition(&st, "Spawned")["message"]
            .as_str()
            .unwrap()
            .contains("additionalDirs"),
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

/// A fresh Codex has not emitted a hook yet. The process state alone cannot
/// distinguish its empty initial composer from a dialog or an active turn.
fn initial_codex(env: &Env) {
    env.edit_stub(|s| {
        s["spawnState"] = json!("running");
        s["spawnFields"] = json!({
            "agent": "codex", "state_source": "process", "hook_reported": false,
            "hook_state": null, "hook_state_contradicted": false,
            "hook_corroboration": "agent", "foreground_process": "/bin/codex"
        });
        s["capture"] = json!({
            "ansi": false, "lines": 0, "cursor_row": 3, "cursor_col": 2,
            "foreground_process": "/bin/codex",
            "output": "  >_ OpenAI Codex (v0.164.0)\n  To get started, describe a task or try one of these commands:\n\n› Ask Codex to do anything\n  ? for shortcuts\n"
        });
    });
}

#[test]
fn a_never_reported_codex_initial_composer_receives_its_brief_once() {
    let env = setup("codex-first-start", vec![]);
    initial_codex(&env);
    let mut t = task("first", true);
    t["spec"]["harness"] = json!("codex");
    env.apply(t).ok();
    // The first manager sees startup still in progress, then exits. A new
    // process must recover the same session when the composer becomes ready.
    env.edit_stub(|s| s["capture"]["cursor_row"] = json!(0));
    env.reconcile().ok();
    assert_eq!(env.status("first")["phase"], "Spawned");
    assert_eq!(argv_of(&env, "send").len(), 0);
    let id = env.stub_sessions()[0]["id"].clone();
    env.edit_stub(|s| s["capture"]["cursor_row"] = json!(3));
    env.reconcile().ok();
    assert_eq!(env.status("first")["phase"], "Briefed");
    // Each reconcile is a new CLI process: reconnect keeps identity and receipt.
    env.reconcile().ok();
    assert_eq!(env.stub_sessions()[0]["id"], id);
    assert_eq!(argv_of(&env, "send").len(), 1);
    assert_eq!(argv_of(&env, "create").len(), 1);
    assert!(
        argv_of(&env, "capture")[0]
            .windows(2)
            .any(|a| a == ["--lines", "0"])
    );
    // Keep the initial composer evidence visible: even when ready() accepts
    // it, at_rest() must not use that proof to authorize deletion.
    set_session_state(&env, &thurbox_key(&env, "first"), "running");
    env.fleet(&["delete", "task", "first"]).ok();
    env.reconcile().ok();
    assert_eq!(argv_of(&env, "delete").len(), 0);
}

#[test]
fn uncertain_or_occupied_codex_startup_never_receives_a_brief() {
    let cases = [
        ("working", "spawnState", json!("working")),
        ("unknown", "spawnState", json!("unknown")),
        ("wrong-key", "key", json!("another-task")),
        ("hook-working", "hook_state", json!("working")),
        ("blocked", "spawnState", json!("blocked")),
        ("shell", "agent", json!("shell")),
        ("stopped", "stopped", json!(true)),
        ("reported", "hook_reported", json!(true)),
        ("contradicted", "hook_state_contradicted", json!(true)),
        ("uncorroborated", "hook_corroboration", json!("unknown")),
        ("dead", "foreground_process", json!("/bin/bash")),
        ("cursor-row", "cursor_row", json!(0)),
        ("cursor-col", "cursor_col", json!(5)),
        ("capture-shell", "capture_process", json!("/bin/bash")),
        (
            "dialog",
            "output",
            json!("Trust this folder?\n› Ask Codex to do anything\n"),
        ),
        ("occupied", "output", json!("Codex\n\n› do something\n")),
        ("missing", "cursor_row", Value::Null),
        ("missing-process", "foreground_process", Value::Null),
        ("scrollback", "lines", json!(200)),
        ("ansi", "ansi", json!(true)),
    ];
    for (name, field, value) in cases {
        let env = setup(&format!("codex-guard-{name}"), vec![]);
        initial_codex(&env);
        env.edit_stub(|s| match field {
            "spawnState" => s[field] = value,
            "cursor_row" | "cursor_col" | "output" | "lines" | "ansi" => {
                s["capture"][field] = value
            }
            "capture_process" => s["capture"]["foreground_process"] = value,
            _ => s["spawnFields"][field] = value,
        });
        let mut t = task("guard", true);
        t["spec"]["harness"] = json!("codex");
        env.apply(t).ok();
        env.reconcile().ok();
        assert_eq!(argv_of(&env, "send").len(), 0, "{name}");
    }
    let env = setup("codex-unreadable-capture", vec![]);
    initial_codex(&env);
    env.stub_fault("capture", "fail", 10);
    env.apply(task("guard", true)).ok();
    env.reconcile().ok();
    assert_eq!(argv_of(&env, "send").len(), 0);
}
