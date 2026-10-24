//! Installed-artifact acceptance: no checkout, Git, shell or network tool.
mod support;

use std::fs;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};
use support::{Env, wait};

fn installed(env: &Env, args: &[&str]) -> Command {
    let bin = env
        .root
        .join(if cfg!(windows) { "fleet.exe" } else { "fleet" });
    if !bin.exists() {
        // A standalone hard link avoids a writable-copy/exec race between
        // parallel test forks; unlinking the build path leaves it runnable.
        fs::hard_link(env!("CARGO_BIN_EXE_fleet"), &bin).unwrap();
    }
    let mut cmd = Command::new(bin);
    cmd.args(args)
        .env_clear()
        .current_dir(&env.root)
        .env("HOME", &env.root)
        .env("XDG_CONFIG_HOME", env.root.join("config"))
        .env("XDG_DATA_HOME", env.root.join("local-data"))
        .env("APPDATA", env.root.join("config"))
        .env("LOCALAPPDATA", env.root.join("local-data"))
        .env("PATH", env.root.join("empty-path"))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if cfg!(windows) {
        for key in ["SYSTEMROOT", "WINDIR"] {
            if let Some(value) = std::env::var_os(key) {
                cmd.env(key, value);
            }
        }
    }
    cmd
}

fn customize_config(path: &std::path::Path) -> Vec<u8> {
    let mut config: toml::Value = toml::from_str(&fs::read_to_string(path).unwrap()).unwrap();
    config["runtime"]["callTimeoutMs"] = toml::Value::Integer(4321);
    let bytes = format!(
        "{}\n# Private local customization\n",
        toml::to_string(&config).unwrap()
    )
    .into_bytes();
    fs::write(path, &bytes).unwrap();
    bytes
}

fn assert_config_instruction(brief: &str, expected: &std::path::Path) {
    use serde::Deserialize;
    let (_, encoded) = brief
        .rsplit_once("Fleet configuration path (JSON string): ")
        .expect("explicit config instruction");
    let mut parser = serde_json::Deserializer::from_str(encoded);
    let emitted = std::path::PathBuf::deserialize(&mut parser).expect("usable JSON config path");
    assert_eq!(
        fs::canonicalize(emitted).unwrap(),
        fs::canonicalize(expected).unwrap(),
        "the brief must identify the effective config file regardless of native path spelling"
    );
}

struct Running(std::process::Child);
impl Drop for Running {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn until(mut predicate: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(40);
    while !predicate() {
        assert!(
            Instant::now() < deadline,
            "automatic progress did not happen"
        );
        thread::sleep(Duration::from_millis(50));
    }
}

fn fake_bootstrap(name: &str) -> Env {
    let mut env = Env::new(name);
    env.set_var(
        "LOCALAPPDATA",
        &env.root.join("local-data").to_string_lossy(),
    );
    env.set_var("APPDATA", &env.root.join("config").to_string_lossy());
    // An explicit fresh config uses the same discovery and bootstrap machinery.
    fs::remove_file(&env.config).unwrap();
    env.fleet(&["init"]).ok();
    let generated = fs::read_to_string(&env.config).unwrap();
    let mut config: toml::Value = toml::from_str(&generated).unwrap();
    config["runtime"] = toml::Value::Table(toml::map::Map::from_iter([
        ("kind".into(), toml::Value::String("fake".into())),
        (
            "dir".into(),
            toml::Value::String(env.fake.to_string_lossy().into_owned()),
        ),
    ]));
    config["data"]["dir"] = toml::Value::String(env.data.to_string_lossy().into_owned());
    fs::write(&env.config, toml::to_string(&config).unwrap()).unwrap();
    env
}

#[test]
fn up_continues_reconciling_other_tasks_and_reconnects_without_duplicate_sessions() {
    let env = fake_bootstrap("up-progress");
    let first = Running(env.spawn(&["up", "--poll-ms", "100", "--lease-ttl-ms", "5000"]));
    until(|| env.fleet(&["get", "task", "vega"]).code == Some(0));
    until(|| env.status("vega")["phase"] == "Briefed");
    env.host()
        .set_at_rest(&env.session_key("vega"), false)
        .unwrap();
    let original = env.get("vega");
    env.apply(support::task("while-vega-busy", true)).ok();
    until(|| env.status("while-vega-busy")["phase"] == "Briefed");
    assert_eq!(env.calls("spawn"), 2);
    // A competing startup is refused while the resident owns the lease.
    env.fleet(&["up", "--poll-ms", "100"]).code(6);
    drop(first);
    // A crashed manager's lease must expire before recovery; use a short
    // explicit lease in the restart fixture instead of altering live records.
    thread::sleep(Duration::from_millis(5300));
    let _restart = Running(env.spawn(&["up", "--poll-ms", "100", "--lease-ttl-ms", "5000"]));
    env.apply(support::task("after-restart", true)).ok();
    until(|| env.status("after-restart")["phase"] == "Briefed");
    assert_eq!(
        env.get("vega")["metadata"]["uid"],
        original["metadata"]["uid"]
    );
    assert_eq!(env.calls("spawn"), 3, "restart adopts existing VEGA");
}

#[test]
fn installed_first_run_creates_runnable_config_and_editable_workspace_without_git() {
    let env = Env::without_tools("installed-init");
    wait(installed(&env, &["init"]).spawn().unwrap()).ok();
    let config = env.root.join("config/fleet/config.toml");
    let workspace = env.root.join("local-data/fleet/vega");
    assert!(config.is_file(), "native config discovery path");
    assert!(
        workspace.join("AGENTS.md").is_file(),
        "bundled instructions"
    );
    assert!(
        workspace.join("task.json").is_file(),
        "ordinary Task manifest"
    );
    let customized_config = customize_config(&config);
    fs::write(workspace.join("AGENTS.md"), "custom instructions").unwrap();
    fs::write(workspace.join("private.txt"), "private customization").unwrap();
    wait(installed(&env, &["init"]).spawn().unwrap()).ok();
    wait(installed(&env, &["init", "--update"]).spawn().unwrap()).ok();
    assert_eq!(fs::read(&config).unwrap(), customized_config);

    assert_eq!(
        fs::read_to_string(workspace.join("AGENTS.md")).unwrap(),
        "custom instructions"
    );
    assert_eq!(
        fs::read_to_string(workspace.join("private.txt")).unwrap(),
        "private customization"
    );
    wait(
        installed(
            &env,
            &["apply", "-f", workspace.join("task.json").to_str().unwrap()],
        )
        .spawn()
        .unwrap(),
    )
    .ok()
    .stdout_has("Task/vega created");
    wait(installed(&env, &["up"]).spawn().unwrap())
        .code(1)
        .stderr_has("thurbox");
}

#[test]
fn concurrent_init_preserves_identity_config_and_private_files_and_updates_safely() {
    let env = Env::without_tools("concurrent-init");
    let one = installed(&env, &["init"]).spawn().unwrap();
    let two = installed(&env, &["init"]).spawn().unwrap();
    wait(one).ok();
    wait(two).ok();
    let config = env.root.join("config/fleet/config.toml");
    let workspace = env.root.join("local-data/fleet/vega");
    let newest_config = fs::read(&config).unwrap();
    let original_config = customize_config(&config);
    let original_instance = fs::read(workspace.join(".fleet/instance")).unwrap();
    let original_task = fs::read(workspace.join("task.json")).unwrap();
    fs::write(workspace.join("AGENTS.md"), "user edit").unwrap();
    fs::write(workspace.join("private.txt"), "private").unwrap();
    // Simulate an older pristine managed prompt to exercise an upgrade.
    let prompt = workspace.join("prompts/mission-control.md");
    let newest = fs::read(&prompt).unwrap();
    fs::write(&prompt, "older managed instructions").unwrap();
    let state_path = workspace.join(".fleet/baseline.json");
    let mut state: serde_json::Value =
        serde_json::from_slice(&fs::read(&state_path).unwrap()).unwrap();
    use sha2::{Digest, Sha256};
    state["files"]["prompts/mission-control.md"] = serde_json::json!(
        Sha256::digest(b"older managed instructions")
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
    );
    // Config remains user-owned even when it matches its older managed
    // baseline; a new binary must offer a conflict instead of upgrading it.
    state["files"]["config"] = serde_json::json!(
        Sha256::digest(&original_config)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
    );
    fs::write(&state_path, serde_json::to_vec_pretty(&state).unwrap()).unwrap();
    wait(installed(&env, &["init", "--update"]).spawn().unwrap())
        .ok()
        .stdout_has("preserved customization");
    assert_eq!(fs::read(&prompt).unwrap(), newest);
    assert_eq!(
        fs::read_to_string(workspace.join("AGENTS.md")).unwrap(),
        "user edit"
    );
    assert_eq!(
        fs::read_to_string(workspace.join("private.txt")).unwrap(),
        "private"
    );
    assert_eq!(fs::read(&config).unwrap(), original_config);
    let candidate_hash: String = Sha256::digest(&newest_config)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    assert_eq!(
        fs::read(
            workspace
                .join(".fleet/conflicts")
                .join(candidate_hash)
                .join("config")
        )
        .unwrap(),
        newest_config
    );

    assert_eq!(
        fs::read(workspace.join(".fleet/instance")).unwrap(),
        original_instance
    );
    assert_eq!(
        fs::read(workspace.join("task.json")).unwrap(),
        original_task
    );
}

#[test]
fn data_directory_override_and_config_precedence_keep_all_commands_in_one_store() {
    let env = Env::without_tools("config-precedence");
    let path = env.root.join("chosen config.toml");
    let data = env.root.join("chosen-store");
    wait(
        installed(
            &env,
            &[
                "--config",
                path.to_str().unwrap(),
                "--data-dir",
                data.to_str().unwrap(),
                "init",
            ],
        )
        .spawn()
        .unwrap(),
    )
    .ok();
    let mut command = installed(&env, &["list", "task", "--json"]);
    command.env("FLEET_CONFIG", &path);
    wait(command.spawn().unwrap()).ok();
    assert!(
        data.join("store.json").exists(),
        "init's --data-dir must be the directory used by subsequent commands"
    );
    assert!(!env.root.join("config/fleet/config.toml").exists());
    let missing = env.root.join("missing.toml");
    let mut command = installed(
        &env,
        &["--config", missing.to_str().unwrap(), "list", "task"],
    );
    command.env("FLEET_CONFIG", &path);
    wait(command.spawn().unwrap())
        .code(1)
        .stderr_has("cannot read config");
}

#[test]
fn installed_up_runs_real_thurbox_adapter_and_preserves_unknown_session_identity() {
    let env = Env::without_tools("installed-up");
    wait(installed(&env, &["init"]).spawn().unwrap()).ok();
    let path = env.root.join("config").join(".").join("fleet/config.toml");
    let mut config: toml::Value = toml::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
    config["runtime"]["cli"] =
        toml::Value::String(support::stub_exe().to_string_lossy().into_owned());
    config["runtime"]["callTimeoutMs"] = toml::Value::Integer(3000);
    fs::write(&path, toml::to_string(&config).unwrap()).unwrap();
    fs::create_dir_all(env.stub_dir()).unwrap();
    let mut command = installed(&env, &["up", "--poll-ms", "100"]);
    command.env("FLEET_STUB_DIR", env.stub_dir());
    let _resident = Running(command.spawn().unwrap());
    let get = || wait(installed(&env, &["get", "task", "vega"]).spawn().unwrap());
    until(|| get().code == Some(0));
    until(|| get().ok().json()["status"]["phase"] == "Briefed");
    let original = get().ok().json();
    assert_config_instruction(original["spec"]["brief"].as_str().unwrap(), &path);
    assert_eq!(env.stub_calls_of("create"), 1);
    // Unreachable rows are explicit Unknown, never evidence of absence.
    let lock_path = env.stub_dir().join("state.lock");
    let mut lock = None;
    until(|| {
        match fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&lock_path)
        {
            Ok(file) => {
                lock = Some(file);
                true
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => false,
            Err(e) => panic!("stub lock: {e}"),
        }
    });
    env.edit_stub(|s| {
        s["sessions"][0]["state"] = serde_json::json!("unreachable");
    });
    drop(lock);
    fs::remove_file(lock_path).unwrap();
    thread::sleep(Duration::from_millis(600));
    assert_eq!(env.stub_calls_of("create"), 1);
    assert_eq!(
        get().ok().json()["status"]["session"],
        original["status"]["session"]
    );
}

#[test]
fn up_refuses_a_store_override_that_would_send_workers_to_another_store() {
    let env = fake_bootstrap("up-override");
    let other = env.root.join("other-store");
    let error_path = env.root.join("override-error");
    let mut command = env.command(&["--data-dir", other.to_str().unwrap(), "up"]);
    command.stderr(fs::File::create(&error_path).unwrap());
    let mut process = Running(command.spawn().unwrap());
    thread::sleep(Duration::from_millis(600));
    assert_eq!(
        process.0.try_wait().unwrap().and_then(|s| s.code()),
        Some(1),
        "mismatched store must be refused"
    );
    assert!(
        fs::read_to_string(error_path)
            .unwrap()
            .contains("persist data.dir")
    );
    assert!(!other.exists(), "refuse before writing an unrelated store");
}

#[test]
fn manager_renews_after_the_final_probe_before_waiting_under_its_lease() {
    use fleet_api::plan::{EffectOutcome, SpawnSpec};
    use fleet_api::runtime::Runtime;
    use fleet_api::task::SessionRef;
    use fleet_api::{Clock, Observation};
    use fleet_manager::{Manager, RunOptions};
    use fleet_store::FileStore;
    use std::sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    };

    // First exercise the emitted starter through the CLI and real controller.
    let env = fake_bootstrap("up-lease-clock");
    let cfg: toml::Value = toml::from_str(&fs::read_to_string(&env.config).unwrap()).unwrap();
    let manifest =
        std::path::Path::new(cfg["starter"]["workspace"].as_str().unwrap()).join("task.json");
    env.apply(serde_json::from_slice(&fs::read(manifest).unwrap()).unwrap())
        .ok();
    env.reconcile().ok();
    let session: SessionRef =
        serde_json::from_value(env.get("vega")["status"]["session"].clone()).unwrap();

    #[derive(Debug)]
    struct TestClock(AtomicU64);
    impl Clock for TestClock {
        fn now(&self) -> u64 {
            self.0.load(Ordering::SeqCst)
        }
    }
    struct SlowLookup {
        clock: Arc<TestClock>,
        session: SessionRef,
    }
    impl Runtime for SlowLookup {
        fn operation_bound(&self) -> u64 {
            1500
        }
        fn host(&self, _: &str) -> Observation<bool> {
            Observation::Known(true)
        }
        fn find(&self, host: &str, key: &str) -> Observation<Option<SessionRef>> {
            assert_eq!(host, self.session.host);
            assert_eq!(key, self.session.key);
            self.clock.0.fetch_add(1500, Ordering::SeqCst);
            Observation::Known(Some(self.session.clone()))
        }
        fn ready(&self, _: &SessionRef) -> Observation<bool> {
            panic!("already briefed")
        }
        fn brief_delivered(&self, _: &SessionRef, _: &str) -> Observation<bool> {
            panic!("already briefed")
        }
        fn at_rest(&self, _: &SessionRef) -> Observation<bool> {
            panic!("not deleting")
        }
        fn spawn(&self, _: &SpawnSpec) -> Observation<EffectOutcome> {
            panic!("must retain session")
        }
        fn brief(&self, _: &SessionRef, _: &str, _: &str) -> Observation<EffectOutcome> {
            panic!("must not rebrief")
        }
        fn release(&self, _: &SessionRef) -> Observation<EffectOutcome> {
            panic!("must retain session")
        }
    }
    let clock = Arc::new(TestClock(AtomicU64::new(100_000)));
    let store = FileStore::open_with_clock(&env.data, clock.clone()).unwrap();
    let runtime = SlowLookup {
        clock: clock.clone(),
        session,
    };
    let manager = Manager {
        store: &store,
        runtime: &runtime,
        clock: clock.as_ref(),
        holder: "lease-clock-test".into(),
    };
    let mut sweeps = 0;
    // Probe 1500ms and wait900ms each fit the2000ms lease, but together
    // do not. Clock values avoid measuring the machine's scheduling/fsync.
    manager
        .serve(
            RunOptions {
                max_passes: 1,
                lease_ttl: 2000,
            },
            |_| {
                sweeps += 1;
                if sweeps == 1 {
                    clock.0.fetch_add(900, Ordering::SeqCst);
                    true
                } else {
                    false
                }
            },
        )
        .expect("renew after the slow operation, before the poll wait");
    assert_eq!(sweeps, 2);
}

#[test]
fn up_reports_known_spawn_failures_without_requiring_a_separate_status_query() {
    let env = fake_bootstrap("up-spawn-diagnostic");
    env.fault(fleet_runtime::Fault::new("spawn", "fail"));
    let error_path = env.root.join("stderr");
    let mut command = env.command(&["up", "--poll-ms", "100"]);
    command.stderr(fs::File::create(&error_path).unwrap());
    let _resident = Running(command.spawn().unwrap());
    until(|| env.fleet(&["get", "task", "vega"]).code == Some(0));
    until(|| {
        env.status("vega")["conditions"]
            .as_array()
            .is_some_and(|cs| cs.iter().any(|c| c["reason"] == "SpawnRetry"))
    });
    until(|| {
        fs::read_to_string(&error_path)
            .unwrap()
            .contains("SpawnRetry")
    });
}

#[test]
fn healthy_briefed_vega_does_not_report_a_startup_failure() {
    let env = fake_bootstrap("up-healthy-diagnostic");
    let path = env.root.join("stderr");
    let mut command = env.command(&["up", "--poll-ms", "100"]);
    command.stderr(fs::File::create(&path).unwrap());
    let _resident = Running(command.spawn().unwrap());
    until(|| env.fleet(&["get", "task", "vega"]).code == Some(0));
    until(|| env.status("vega")["phase"] == "Briefed");
    let before = env.calls("find");
    until(|| env.calls("find") >= before + 3);
    assert!(
        fs::read_to_string(path).unwrap().is_empty(),
        "a healthy running Task is not a startup failure"
    );
}

#[test]
fn reconnect_uses_stored_immutable_inputs_after_the_editable_template_changes() {
    let env = fake_bootstrap("up-retained-inputs");
    let config: toml::Value = toml::from_str(&fs::read_to_string(&env.config).unwrap()).unwrap();
    let path =
        std::path::Path::new(config["starter"]["workspace"].as_str().unwrap()).join("task.json");
    let template: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    env.apply(template).ok();
    env.reconcile().ok();
    assert_eq!(env.status("vega")["phase"], "Briefed");
    let original = env.get("vega");
    let mut changed: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    changed[0]["spec"]["host"] = serde_json::json!("unused-host");
    fs::write(&path, serde_json::to_vec(&changed).unwrap()).unwrap();
    env.host().set_host("unused-host", "down").unwrap();
    let mut resident = Running(env.spawn(&["up", "--poll-ms", "100"]));
    thread::sleep(Duration::from_millis(600));
    assert!(
        resident.0.try_wait().unwrap().is_none(),
        "reconnect must probe the retained host, not a changed template"
    );
    env.apply(support::task("still-progressing", true)).ok();
    until(|| env.status("still-progressing")["phase"] == "Briefed");
    assert_eq!(env.get("vega")["spec"], original["spec"]);
}

#[cfg(target_os = "linux")]
#[test]
fn native_config_and_data_can_live_on_different_filesystems() {
    use std::os::unix::fs::MetadataExt;
    let env = Env::without_tools("different-filesystems");
    // A tiny native tmpfs fixture, never a build or shared deployment.
    let other = std::path::PathBuf::from("/dev/shm")
        .join(format!("fleet-bootstrap-{}", std::process::id()));
    struct Cleanup(std::path::PathBuf);
    impl Drop for Cleanup {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    let _cleanup = Cleanup(other.clone());
    fs::create_dir_all(&other).unwrap();
    assert_ne!(
        fs::metadata(&env.root).unwrap().dev(),
        fs::metadata(&other).unwrap().dev(),
        "cross-volume acceptance requires two filesystems"
    );
    let mut command = installed(&env, &["init"]);
    command.env("XDG_DATA_HOME", &other);
    wait(command.spawn().unwrap()).ok();
    assert!(env.root.join("config/fleet/config.toml").is_file());
    assert!(other.join("fleet/vega/task.json").is_file());
}

#[cfg(target_os = "linux")]
#[test]
fn graceful_shutdown_releases_the_lease_and_reconnects_without_waiting_for_expiry() {
    let env = fake_bootstrap("up-graceful");
    let mut first = Running(env.spawn(&["up", "--poll-ms", "100"]));
    until(|| env.fleet(&["get", "task", "vega"]).code == Some(0));
    until(|| env.status("vega")["phase"] == "Briefed");
    let session = env.get("vega")["status"]["session"].clone();
    assert!(
        Command::new("/bin/kill")
            .args(["-TERM", &first.0.id().to_string()])
            .status()
            .unwrap()
            .success()
    );
    let mut status = None;
    until(|| {
        status = first.0.try_wait().unwrap();
        status.is_some()
    });
    assert_eq!(status.unwrap().code(), Some(0));
    let _second = Running(env.spawn(&["up", "--poll-ms", "100"]));
    env.apply(support::task("after-graceful-stop", true)).ok();
    until(|| env.status("after-graceful-stop")["phase"] == "Briefed");
    assert_eq!(env.get("vega")["status"]["session"], session);
    assert_eq!(env.calls("spawn"), 2);
}

#[test]
fn separate_configurations_get_separate_starter_instances_and_boot_instructions() {
    let env = Env::without_tools("separate-instances");
    let one = env.root.join("one.toml");
    let two = env.root.join("two.toml");
    wait(
        installed(&env, &["--config", one.to_str().unwrap(), "init"])
            .spawn()
            .unwrap(),
    )
    .ok();
    wait(
        installed(&env, &["--config", two.to_str().unwrap(), "init"])
            .spawn()
            .unwrap(),
    )
    .ok();
    let a: toml::Value = toml::from_str(&fs::read_to_string(&one).unwrap()).unwrap();
    let b: toml::Value = toml::from_str(&fs::read_to_string(&two).unwrap()).unwrap();
    assert_ne!(
        a["runtime"]["keyPrefix"], b["runtime"]["keyPrefix"],
        "separate configs must not share session namespace"
    );
    assert_ne!(a["starter"]["workspace"], b["starter"]["workspace"]);
    assert_ne!(a["data"]["dir"], b["data"]["dir"]);
    for (config, path) in [(a, one), (b, two)] {
        let task: serde_json::Value = serde_json::from_slice(
            &fs::read(
                std::path::Path::new(config["starter"]["workspace"].as_str().unwrap())
                    .join("task.json"),
            )
            .unwrap(),
        )
        .unwrap();
        assert_config_instruction(task[0]["spec"]["brief"].as_str().unwrap(), &path);
    }
}

#[test]
fn explicit_config_and_store_work_without_native_home_variables() {
    let env = Env::without_tools("explicit-no-home");
    let config = env.root.join("explicit.toml");
    let store = env.root.join("explicit-store");
    let mut command = installed(
        &env,
        &[
            "--config",
            config.to_str().unwrap(),
            "--data-dir",
            store.to_str().unwrap(),
            "init",
        ],
    );
    for key in [
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "APPDATA",
        "LOCALAPPDATA",
    ] {
        command.env_remove(key);
    }
    wait(command.spawn().unwrap()).ok();
    wait(
        installed(
            &env,
            &[
                "--config",
                config.to_str().unwrap(),
                "list",
                "task",
                "--json",
            ],
        )
        .spawn()
        .unwrap(),
    )
    .ok();
    assert!(store.join("store.json").is_file());
}
