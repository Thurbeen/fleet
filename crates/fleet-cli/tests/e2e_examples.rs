//! The shipped fresh-config example is executable without Python or external tools.
mod support;
use support::Env;

#[test]
fn shipped_example_applies_and_reconciles_in_a_fresh_isolated_store() {
    let mut e = Env::without_tools("shipped-example");
    e.fake = e.root.join("fake-host");
    std::fs::write(&e.config, include_str!("../../../examples/fleet.toml"))
        .expect("example config");
    let mut resources: serde_json::Value =
        serde_json::from_str(include_str!("../../../examples/resources.json"))
            .expect("example manifest");
    e.apply(resources.clone()).ok();
    e.fleet(&["list", "Task", "--json"])
        .ok()
        .stdout_has("example");
    e.reconcile().ok();
    assert_eq!(e.calls("spawn"), 0, "suspended example started work");
    resources[0]["spec"]["dispatch"] = true.into();
    e.apply(resources).ok();
    e.reconcile().ok();
    assert_eq!(e.calls("spawn"), 1);
    let generation = e.get("example")["metadata"]["generation"]
        .as_u64()
        .expect("generation")
        .to_string();
    e.fleet(&[
        "result",
        "example",
        "--generation",
        &generation,
        "--outcome",
        "shipped",
        "--summary",
        "isolated example complete",
    ])
    .ok();
    e.reconcile().ok();
    e.host()
        .set_at_rest(&e.session_key("example"), true)
        .expect("worker at rest");
    e.fleet(&["delete", "Task", "example"]).ok();
    e.reconcile().ok();
    e.fleet(&["get", "Task", "example"]).code(3);
    e.assert_no_temp();
}

#[cfg(windows)]
#[test]
fn windows_clean_install_then_default_store_apply() {
    let e = Env::new("windows-default-install");
    std::fs::create_dir_all(e.root.join("roaming")).unwrap();
    let repo = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let mut command = std::process::Command::new("powershell");
    command
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
        ])
        .arg(std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/install-default.ps1"))
        .arg("-Binary")
        .arg(env!("CARGO_BIN_EXE_fleet"))
        .arg("-Installer")
        .arg(repo.join("scripts/install-release.ps1"))
        .arg("-Manifest")
        .arg(repo.join("examples/resources.json"))
        .arg("-Root")
        .arg(&e.root)
        .env_clear()
        .env("HOME", &e.root)
        // Windows PowerShell 5.1 starts very slowly without a profile and
        // app-data location; both stay inside the test root.
        .env("USERPROFILE", &e.root)
        .env("APPDATA", e.root.join("roaming"))
        .env("TMP", e.root.join("tmp"))
        .env("TEMP", e.root.join("tmp"));
    // Without PATHEXT, PowerShell does not run an .exe as a native command: it
    // hands it to the shell, with no output and no exit code.
    for name in [
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "COMSPEC",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "COMMONPROGRAMFILES",
        "WINDIR",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_ARCHITEW6432",
    ] {
        if let Some(value) = std::env::var_os(name) {
            command.env(name, value);
        }
    }
    command
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    // A whole PowerShell install and two fleet commands, well inside nextest's
    // five-minute limit for a slow test.
    support::wait_within(
        command.spawn().expect("PowerShell"),
        std::time::Duration::from_secs(240),
    )
    .ok()
    .stdout_has("native clean install and default-store apply: passed");
}
