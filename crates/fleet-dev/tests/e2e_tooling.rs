//! Acceptance tests exercise the actual tooling binary against private files.
use fleet_exec::{Outcome, Spec, run};
use serde_json::json;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::Duration;
static SEQ: AtomicU32 = AtomicU32::new(0);
struct Env(PathBuf);
impl Env {
    fn new() -> Self {
        let p = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
            "tooling-{}-{}",
            std::process::id(),
            SEQ.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&p).unwrap();
        Self(p)
    }
    fn write(&self, p: &str, text: &str) {
        let p = self.0.join(p);
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, text).unwrap();
    }
    fn call(&self, args: &[&str], env: &[(&str, &str)]) -> (bool, String) {
        let mut s = Spec::new(env!("CARGO_BIN_EXE_fleet-dev"), Duration::from_secs(120))
            .args(args.iter().copied());
        s.cwd = Some(self.0.clone());
        s.env = env
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        match run(&s) {
            Outcome::Exited {
                code,
                stdout,
                stderr,
            } => (
                code == Some(0),
                format!(
                    "{}{}",
                    String::from_utf8_lossy(&stdout),
                    String::from_utf8_lossy(&stderr)
                ),
            ),
            o => panic!("{o:?}"),
        }
    }
    fn git(&self, args: &[&str]) {
        let mut s = Spec::new("git", Duration::from_secs(10)).args(args.iter().copied());
        s.cwd = Some(self.0.clone());
        s.env = vec![
            (
                "GIT_CONFIG_GLOBAL".into(),
                self.0.join("no-config").to_string_lossy().into(),
            ),
            ("GIT_CONFIG_NOSYSTEM".into(), "1".into()),
        ];
        let o = run(&s);
        assert!(o.success_text().is_some(), "{o:?}");
    }
    fn repo(&self) {
        self.git(&["init", "-b", "main"]);
        self.git(&["config", "user.name", "Fixture"]);
        self.git(&["config", "user.email", "fixture@example.invalid"]);
        self.git(&["config", "commit.gpgsign", "false"]);
        self.write("crates/demo.rs", "pub fn shipped() {}\n");
        self.commit("feat: first release");
        self.write(
            ".github/release-readiness.toml",
            "ready = false\nreason = 'integration pending'\n",
        );
    }
    fn commit(&self, title: &str) {
        self.git(&["add", "."]);
        self.git(&["commit", "-m", title]);
    }
}
impl Drop for Env {
    fn drop(&mut self) {
        if !std::thread::panicking() {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
}

const CI: &str = "on: {pull_request: {branches: [main]}}\njobs:\n  rust:\n    strategy: {matrix: {os: [ubuntu-latest, windows-latest, ubuntu-26.04-arm]}}\n    runs-on: ${{ matrix.os }}\n    timeout-minutes: 30\n    steps: [{run: 'cargo xtask check'}]\n  all-checks:\n    if: always()\n    needs: [rust]\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n    steps: [{run: 'cargo xtask verdict'}]\n";

#[test]
fn workflow_and_verdict_fail_closed_on_mutated_jobs() {
    let e = Env::new();
    e.write(".github/workflows/ci.yml", CI);
    assert!(e.call(&["workflow"], &[]).0);
    assert!(
        e.call(
            &["verdict"],
            &[("NEEDS", r#"{"rust":{"result":"success"}}"#)]
        )
        .0
    );
    for result in ["skipped", "cancelled", "failure", ""] {
        let n = json!({"rust":{"result":result}}).to_string();
        let (ok, out) = e.call(&["verdict"], &[("NEEDS", &n)]);
        assert!(!ok && out.contains("rust"), "{out}");
    }
    let (ok, out) = e.call(&["verdict"], &[("NEEDS", "{}")]);
    assert!(!ok && out.contains("missing"), "{out}");
    for (from, to, why) in [
        ("if: always()", "if: success()", "always"),
        ("needs: [rust]", "needs: []", "needs"),
        ("timeout-minutes: 30", "name: No timeout", "timeout"),
        (
            "cargo xtask check",
            "cargo xtask check rust-fmt",
            "rust-test",
        ),
        ("windows-latest", "macos-latest", "windows"),
    ] {
        e.write(".github/workflows/ci.yml", &CI.replace(from, to));
        let (ok, out) = e.call(&["workflow"], &[]);
        assert!(!ok && out.contains(why), "{why}: {out}");
    }
}

#[test]
fn every_workflow_rejects_injection_and_unpinned_actions() {
    let e = Env::new();
    e.write(".github/workflows/ci.yml", CI);
    for expr in [
        "github.event.pull_request.title",
        "inputs['version']",
        "github['event']['pull_request']['title']",
        "github [ 'event' ] [ 'comment' ] [ 'body' ]",
        "iNpUtS.version",
        "github.event.pull_request.body",
        "github.head_ref",
        "github.event.head_commit.message",
        "inputs.version",
        "github.event.comment.body",
        "format('{0}', github.event.pull_request.title)",
    ] {
        e.write(
            ".github/workflows/extra.yml",
            &format!("jobs:\n  extra:\n    steps:\n      - run: echo ${{{{ {expr} }}}}\n"),
        );
        let (ok, out) = e.call(&["workflow"], &[]);
        assert!(!ok && out.contains("interpolation"), "{out}");
    }
    e.write(
        ".github/workflows/extra.yml",
        "jobs:\n  extra:\n    steps: [{uses: 'actions/checkout@main'}]\n",
    );
    let (ok, out) = e.call(&["workflow"], &[]);
    assert!(!ok && out.contains("SHA"), "{out}");
}

#[test]
fn release_plan_gates_readiness_ref_tags_and_relevance() {
    let e = Env::new();
    e.repo();
    let (ok, out) = e.call(&["release", "plan", "--ref", "refs/heads/main"], &[]);
    assert!(
        ok && out.contains("publish=false") && out.contains("version=v0.1.0"),
        "{out}"
    );
    e.write(".github/release-readiness.toml", "ready = true\n");
    let (ok, out) = e.call(
        &[
            "release",
            "plan",
            "--ref",
            "refs/heads/feature",
            "--version",
            "1.0.0",
        ],
        &[],
    );
    assert!(ok && out.contains("publish=false"), "{out}");
    let (ok, out) = e.call(
        &[
            "release",
            "plan",
            "--ref",
            "refs/heads/main",
            "--version",
            "1.0.0",
            "--dry-run",
        ],
        &[],
    );
    assert!(
        ok && out.contains("build=true") && out.contains("publish=false"),
        "{out}"
    );
    e.git(&["tag", "v0.1.0"]);
    let (ok, out) = e.call(
        &[
            "release",
            "plan",
            "--ref",
            "refs/heads/main",
            "--version",
            "0.1.0",
        ],
        &[],
    );
    assert!(!ok && out.contains("already"), "{out}");
    e.write("README.md", "docs only");
    e.commit("fix: prose");
    let (ok, out) = e.call(&["release", "plan", "--ref", "refs/heads/main"], &[]);
    assert!(ok && out.contains("release=false"), "{out}");
    for v in ["01.2.3", "1.2", "1.2.3-", "1.2.3/../../escape"] {
        assert!(!e.call(&["release", "plan", "--version", v], &[]).0);
    }
}

#[test]
fn archives_are_reproducible_and_missing_targets_fail_checksums() {
    let e = Env::new();
    e.write("LICENSE", "fixture licence\n");
    e.write("binary", "binary fixture\n");
    let targets = [
        "x86_64-unknown-linux-musl",
        "aarch64-unknown-linux-musl",
        "x86_64-pc-windows-msvc",
    ];
    for t in targets {
        let args = [
            "release",
            "package",
            "--version",
            "v1.2.3",
            "--target",
            t,
            "--binary",
            "binary",
            "--out",
            "dist",
        ];
        let (ok, out) = e.call(&args, &[]);
        assert!(ok, "{out}");
        let suffix = if t.contains("windows") {
            "zip"
        } else {
            "tar.gz"
        };
        let p = e.0.join(format!("dist/fleet-v1.2.3-{t}.{suffix}"));
        let a = fs::read(&p).unwrap();
        assert!(e.call(&args, &[]).0);
        assert_eq!(a, fs::read(&p).unwrap());
        use std::io::Read;
        if t.contains("windows") {
            let mut archive = zip::ZipArchive::new(std::io::Cursor::new(&a)).unwrap();
            assert_eq!(archive.len(), 2);
            for (name, want) in [
                ("fleet.exe", "binary fixture\n"),
                ("LICENSE", "fixture licence\n"),
            ] {
                let mut bytes = String::new();
                archive
                    .by_name(name)
                    .unwrap()
                    .read_to_string(&mut bytes)
                    .unwrap();
                assert_eq!(bytes, want);
            }
        } else {
            let gz = flate2::read::GzDecoder::new(a.as_slice());
            let mut archive = tar::Archive::new(gz);
            let entries = archive
                .entries()
                .unwrap()
                .map(|entry| {
                    let mut entry = entry.unwrap();
                    let name = entry.path().unwrap().to_string_lossy().into_owned();
                    let mode = entry.header().mode().unwrap();
                    let mut bytes = String::new();
                    entry.read_to_string(&mut bytes).unwrap();
                    (name, mode, bytes)
                })
                .collect::<Vec<_>>();
            assert_eq!(
                entries,
                vec![
                    ("fleet".into(), 0o755, "binary fixture\n".into()),
                    ("LICENSE".into(), 0o644, "fixture licence\n".into())
                ]
            );
        }
        if t != targets[2] {
            assert!(
                !e.call(
                    &["release", "checksums", "--version", "v1.2.3", "dist"],
                    &[]
                )
                .0
            );
        }
    }
    let (ok, out) = e.call(
        &["release", "checksums", "--version", "v1.2.3", "dist"],
        &[],
    );
    assert!(ok, "{out}");
    let sums = fs::read_to_string(e.0.join("dist/fleet-v1.2.3-checksums.txt")).unwrap();
    assert_eq!(sums.lines().count(), 3);
}

#[test]
fn title_and_suite_validation_are_executable() {
    let e = Env::new();
    for t in ["feat: accept", "fix(cli)!: break"] {
        assert!(e.call(&["release", "title", t, "12"], &[]).0);
    }
    for t in ["oops", "feat: double (#12)", "feat: ", "unknown: hello"] {
        assert!(!e.call(&["release", "title", t, "12"], &[]).0);
    }
    let listing = json!({"rust-suites": {"a":{"binary-name":"architecture","kind":"test","testcases":{"holds":{"ignored":false,"filter-match":{"status":"matches"}}}},"e":{"binary-name":"e2e_demo","kind":"test","testcases":{"runs":{"ignored":false,"filter-match":{"status":"matches"}}}}}});
    e.write("listing.json", &listing.to_string());
    assert!(e.call(&["suites", "listing.json"], &[]).0);
    for field in ["ignored", "filtered"] {
        let mut bad = listing.clone();
        if field == "ignored" {
            bad["rust-suites"]["e"]["testcases"]["runs"]["ignored"] = json!(true);
        } else {
            bad["rust-suites"]["e"]["testcases"]["runs"]["filter-match"]["status"] =
                json!("mismatch");
        }
        e.write("listing.json", &bad.to_string());
        let (ok, out) = e.call(&["suites", "listing.json"], &[]);
        assert!(!ok && out.contains("e2e_"), "{out}");
    }
}

fn installer(e: &Env, args: &[&str]) -> (bool, String) {
    installer_with(e, args, Some("v1.2.3"))
}
fn installer_with(e: &Env, args: &[&str], version: Option<&str>) -> (bool, String) {
    let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let (program, script) = if cfg!(windows) {
        ("powershell", "scripts/install-release.ps1")
    } else {
        ("sh", "scripts/install-release.sh")
    };
    let mut s = Spec::new(program, Duration::from_secs(30));
    if cfg!(windows) {
        s = s.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]);
    }
    s = s
        .arg(repo.join(script).into_os_string())
        .args(args.iter().map(|a| {
            if cfg!(windows) && *a == "--uninstall" {
                "-Uninstall"
            } else {
                *a
            }
        }));
    s.cwd = Some(e.0.clone());
    s.env = vec![
        (
            "FLEET_RELEASE_BASE".into(),
            e.0.join("releases").to_string_lossy().into(),
        ),
        (
            "FLEET_INSTALL_DIR".into(),
            e.0.join("bin").to_string_lossy().into(),
        ),
        ("TMPDIR".into(), e.0.to_string_lossy().into()),
        ("TEMP".into(), e.0.to_string_lossy().into()),
    ];
    if let Some(v) = version {
        s.env.push(("FLEET_VERSION".into(), v.into()));
    }
    match run(&s) {
        Outcome::Exited {
            code,
            stdout,
            stderr,
        } => (
            code == Some(0),
            format!(
                "{}{}",
                String::from_utf8_lossy(&stdout),
                String::from_utf8_lossy(&stderr)
            ),
        ),
        o => panic!("{o:?}"),
    }
}
fn install_fixture(e: &Env) {
    e.write("LICENSE", "fixture\n");
    for t in [
        "x86_64-unknown-linux-musl",
        "aarch64-unknown-linux-musl",
        "x86_64-pc-windows-msvc",
    ] {
        let (ok, out) = e.call(
            &[
                "release",
                "package",
                "--version",
                "v1.2.3",
                "--target",
                t,
                "--binary",
                env!("CARGO_BIN_EXE_fleet-install-fixture"),
                "--out",
                "releases/v1.2.3",
            ],
            &[],
        );
        assert!(ok, "{out}");
    }
    let (ok, out) = e.call(
        &[
            "release",
            "checksums",
            "--version",
            "v1.2.3",
            "releases/v1.2.3",
        ],
        &[],
    );
    assert!(ok, "{out}");
}
#[test]
fn offline_install_update_tamper_refusal_and_uninstall() {
    let e = Env::new();
    install_fixture(&e);
    let (ok, out) = installer(&e, &[]);
    assert!(ok, "{out}");
    let binary =
        e.0.join("bin")
            .join(if cfg!(windows) { "fleet.exe" } else { "fleet" });
    let first = fs::read(&binary).unwrap();
    let (ok, out) = installer(&e, &[]);
    assert!(ok, "{out}");
    assert_eq!(first, fs::read(&binary).unwrap());
    e.write(
        "releases/v1.2.3/fleet-v1.2.3-checksums.txt",
        "tampered checksums\n",
    );
    assert!(!installer(&e, &[]).0);
    assert_eq!(first, fs::read(&binary).unwrap());
    let (ok, out) = installer(&e, &["--uninstall"]);
    assert!(ok && !binary.exists(), "{out}");
}
#[test]
fn concurrent_install_and_wrong_version_preserve_existing_binary() {
    let e = Env::new();
    install_fixture(&e);
    let (ok, out) = installer(&e, &[]);
    assert!(ok, "{out}");
    let binary =
        e.0.join("bin")
            .join(if cfg!(windows) { "fleet.exe" } else { "fleet" });
    let before = fs::read(&binary).unwrap();
    fs::create_dir(e.0.join("bin/.fleet-install.lock")).unwrap();
    let (ok, out) = installer(&e, &[]);
    assert!(!ok && out.contains("lock"), "{out}");
    assert_eq!(before, fs::read(&binary).unwrap());
    fs::remove_dir(e.0.join("bin/.fleet-install.lock")).unwrap();
    // A correctly checksummed archive carrying the wrong version is still refused.
    fs::rename(e.0.join("releases/v1.2.3"), e.0.join("releases/v2.0.0")).unwrap();
    for t in [
        "x86_64-unknown-linux-musl",
        "aarch64-unknown-linux-musl",
        "x86_64-pc-windows-msvc",
    ] {
        assert!(
            e.call(
                &[
                    "release",
                    "package",
                    "--version",
                    "v2.0.0",
                    "--target",
                    t,
                    "--binary",
                    env!("CARGO_BIN_EXE_fleet-install-fixture"),
                    "--out",
                    "releases/v2.0.0"
                ],
                &[]
            )
            .0
        );
    }
    assert!(
        e.call(
            &[
                "release",
                "checksums",
                "--version",
                "v2.0.0",
                "releases/v2.0.0"
            ],
            &[]
        )
        .0
    );
    fs::rename(e.0.join("releases/v2.0.0"), e.0.join("releases/v1.2.3")).unwrap();
    // Installer below is explicitly asked for a different release via direct fixture call.
    let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let mut s = if cfg!(windows) {
        Spec::new("powershell", Duration::from_secs(30))
            .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(repo.join("scripts/install-release.ps1").into_os_string())
    } else {
        Spec::new("sh", Duration::from_secs(30))
            .arg(repo.join("scripts/install-release.sh").into_os_string())
    };
    fs::rename(e.0.join("releases/v1.2.3"), e.0.join("releases/v2.0.0")).unwrap();
    s.env = vec![
        ("FLEET_VERSION".into(), "v2.0.0".into()),
        (
            "FLEET_RELEASE_BASE".into(),
            e.0.join("releases").to_string_lossy().into(),
        ),
        (
            "FLEET_INSTALL_DIR".into(),
            e.0.join("bin").to_string_lossy().into(),
        ),
    ];
    assert!(run(&s).success_text().is_none());
    assert_eq!(before, fs::read(binary).unwrap());
}

#[test]
fn native_gate_executes_fixture_tests_doctests_lint_docs_and_format() {
    let e = Env::new();
    e.write(
        "Cargo.toml",
        "[package]\nname = 'gate-fixture'\nversion = '0.0.0'\nedition = '2024'\n[workspace]\n",
    );
    let lib = "/// One more.\n/// ```\n/// assert_eq!(gate_fixture::inc(1), 2);\n/// ```\npub fn inc(x: u32) -> u32 {\n    x + 1\n}\n";
    e.write("src/lib.rs", lib);
    e.write(
        "tests/architecture.rs",
        "#[test]\nfn holds() {\n    assert_eq!(gate_fixture::inc(1), 2);\n}\n",
    );
    let e2e = "#[test]\nfn runs() {\n    assert_eq!(gate_fixture::inc(2), 3);\n}\n";
    e.write("tests/e2e_demo.rs", e2e);
    e.write(".config/nextest.toml", "[profile.ci]\nfail-fast = false\n");
    let mut s =
        Spec::new("cargo", Duration::from_secs(30)).args(["generate-lockfile", "--offline"]);
    s.cwd = Some(e.0.clone());
    assert!(run(&s).success_text().is_some());
    let target = e.0.join("target").to_string_lossy().into_owned();
    let env = [
        ("CARGO_TARGET_DIR", target.as_str()),
        ("CARGO_NET_OFFLINE", "true"),
    ];
    for area in ["rust-fmt", "rust-test", "rust-lint", "rust-doc"] {
        let (ok, out) = e.call(&["check", area], &env);
        assert!(ok, "{out}");
    }
    e.write(
        "tests/e2e_demo.rs",
        &e2e.replace("#[test]", "#[test]\n#[ignore]"),
    );
    let (ok, out) = e.call(&["check", "rust-test"], &env);
    assert!(!ok && out.contains("e2e_"), "{out}");
    e.write("tests/e2e_demo.rs", e2e);
    e.write(
        ".config/nextest.toml",
        "[profile.ci]\ndefault-filter = 'not binary(e2e_demo)'\n",
    );
    let (ok, out) = e.call(&["check", "rust-test"], &env);
    assert!(!ok && out.contains("e2e_"), "{out}");
    e.write(".config/nextest.toml", "[profile.ci]\nfail-fast = false\n");
    fs::remove_file(e.0.join("tests/architecture.rs")).unwrap();
    let (ok, out) = e.call(&["check", "rust-test"], &env);
    assert!(!ok && out.contains("architecture"), "{out}");
    e.write(
        "tests/architecture.rs",
        "#[test]\nfn holds() { assert!(false); }\n",
    );
    let (ok, out) = e.call(&["check", "rust-test"], &env);
    assert!(!ok && out.contains("cargo nextest run"), "{out}");
    e.write("tests/architecture.rs", "#[test]\nfn holds() {}\n");
    e.write("src/lib.rs", &lib.replace("inc(1), 2", "inc(1), 3"));
    let (ok, out) = e.call(&["check", "rust-test"], &env);
    assert!(!ok && out.contains("cargo test --doc"), "{out}");
    e.write("src/lib.rs", &lib.replace("One more.", "See [`Missing`]."));
    let (ok, out) = e.call(&["check", "rust-doc"], &env);
    assert!(!ok && out.contains("cargo doc"), "{out}");
    e.write("src/lib.rs", &lib.replace("x + 1", "x+1"));
    assert!(!e.call(&["check", "rust-fmt"], &env).0);
    assert!(e.call(&["check", "--fix", "rust-fmt"], &env).0);
    e.write(
        "src/lib.rs",
        &lib.replace("    x + 1", "    let unused = 1;\n    x + 1"),
    );
    let (ok, out) = e.call(&["check", "rust-lint"], &env);
    assert!(!ok && out.contains("cargo clippy"), "{out}");
    fs::remove_file(e.0.join("Cargo.toml")).unwrap();
    let (ok, out) = e.call(&["check", "rust-test"], &env);
    assert!(!ok && out.contains("Cargo.toml"), "{out}");
}

const CD: &str = "defaults: {run: {shell: bash}}\njobs:\n  build:\n    runs-on: ubuntu-26.04-arm\n    steps:\n      - run: cargo xtask check rust-test\n      - run: cargo xtask release package\n  publish:\n    environment: release\n    steps: []\n";
const TITLE: &str = "on: {pull_request: {types: [opened, edited, synchronize, reopened]}}\njobs:\n  title:\n    steps: [{run: 'cargo xtask release title \"$PR_TITLE\" \"$PR_NUMBER\"'}]\n";
#[test]
fn release_and_title_workflows_keep_native_platform_and_trust_promises() {
    let e = Env::new();
    e.write(".github/workflows/ci.yml", CI);
    e.write(".github/workflows/cd.yml", CD);
    e.write(".github/workflows/pr-title.yml", TITLE);
    let (ok, out) = e.call(&["workflow"], &[]);
    assert!(ok, "{out}");
    for (from, to, why) in [
        ("shell: bash", "shell: sh", "pipefail"),
        (
            "environment: release",
            "environment: production",
            "environment",
        ),
        ("cargo xtask check rust-test", "cargo build", "rust-test"),
        ("ubuntu-26.04-arm", "macos-latest", "untested runner"),
    ] {
        e.write(".github/workflows/cd.yml", &CD.replace(from, to));
        let (ok, out) = e.call(&["workflow"], &[]);
        assert!(!ok && out.contains(why), "{why}: {out}");
    }
    e.write(".github/workflows/cd.yml", CD);
    e.write(
        ".github/workflows/pr-title.yml",
        &TITLE.replace("opened, edited,", "opened,"),
    );
    let (ok, out) = e.call(&["workflow"], &[]);
    assert!(!ok && out.contains("edited"), "{out}");
    e.write(".github/workflows/pr-title.yml", TITLE);
    e.write(
        ".github/workflows/ci.yml",
        &CI.replace("cargo xtask check'", "echo cargo xtask check'"),
    );
    assert!(!e.call(&["workflow"], &[]).0);
    e.write(
        ".github/workflows/ci.yml",
        &CI.replace(
            "steps: [{run: 'cargo xtask check'}]",
            "steps: [{if: success(), run: 'cargo xtask check'}]",
        ),
    );
    assert!(!e.call(&["workflow"], &[]).0);
    // An additional runner doing only Rust still owes every Rust check.
    let rust = "rust-fmt rust-lint rust-test rust-doc rust-deny";
    let extra = format!(
        "  arm:\n    runs-on: ubuntu-26.04-arm\n    timeout-minutes: 20\n    steps: [{{run: 'cargo xtask check {rust}'}}]\n"
    );
    let complete = CI
        .replace("windows-latest, ubuntu-26.04-arm", "windows-latest")
        .replace("needs: [rust]", "needs: [rust, arm]")
        + &extra;
    e.write(".github/workflows/ci.yml", &complete);
    assert!(e.call(&["workflow"], &[]).0);
    e.write(
        ".github/workflows/ci.yml",
        &complete.replace(rust, "rust-fmt rust-lint rust-test rust-doc"),
    );
    let (ok, out) = e.call(&["workflow"], &[]);
    assert!(!ok && out.contains("rust-deny"), "{out}");
}

const SIGNED_CD: &str = r#"defaults: {run: {shell: bash}}
jobs:
  build:
    runs-on: windows-latest
    steps:
      - run: cargo xtask check rust-test
      - id: signpath
        env:
          SIGNPATH_API_TOKEN: ${{ secrets.SIGNPATH_API_TOKEN }}
          SIGNPATH_ORGANIZATION_ID: ${{ vars.SIGNPATH_ORGANIZATION_ID }}
          SIGNPATH_PROJECT_SLUG: ${{ vars.SIGNPATH_PROJECT_SLUG }}
          SIGNPATH_SIGNING_POLICY_SLUG: ${{ vars.SIGNPATH_SIGNING_POLICY_SLUG }}
        run: echo enabled=true >> "$GITHUB_OUTPUT"
      - if: steps.signpath.outputs.enabled == 'true'
        uses: SignPath/github-action-submit-signing-request@c92b958760219087e01f8d67a1669ed57afe2627
        with:
          api-token: ${{ secrets.SIGNPATH_API_TOKEN }}
          organization-id: ${{ vars.SIGNPATH_ORGANIZATION_ID }}
          project-slug: ${{ vars.SIGNPATH_PROJECT_SLUG }}
          signing-policy-slug: ${{ vars.SIGNPATH_SIGNING_POLICY_SLUG }}
      - run: cargo xtask release package
      - if: steps.signpath.outputs.enabled == 'true'
        run: powershell -NoProfile -Command Get-AuthenticodeSignature fleet.exe
  publish:
    environment: release
    steps: []
"#;
#[test]
fn windows_release_signs_before_packaging_behind_its_configuration_gate() {
    let e = Env::new();
    e.write(".github/workflows/ci.yml", CI);
    e.write(".github/workflows/cd.yml", SIGNED_CD);
    let (ok, out) = e.call(&["workflow"], &[]);
    assert!(ok, "{out}");
    let sign = "      - if: steps.signpath.outputs.enabled == 'true'\n        uses: SignPath";
    let package = "      - run: cargo xtask release package\n";
    let start = SIGNED_CD.find(sign).unwrap();
    let end = SIGNED_CD.find(package).unwrap();
    let late = SIGNED_CD
        .replace(package, "")
        .replace(sign, &format!("{package}{sign}"));
    for (cd, why) in [
        (
            SIGNED_CD.replace(&SIGNED_CD[start..end], ""),
            "no SignPath signing step",
        ),
        (
            SIGNED_CD.replace(
                "      - if: steps.signpath.outputs.enabled == 'true'\n        uses:",
                "      - uses:",
            ),
            "gated on steps.signpath",
        ),
        (
            SIGNED_CD.replace(
                "          SIGNPATH_PROJECT_SLUG: ${{ vars.SIGNPATH_PROJECT_SLUG }}\n",
                "",
            ),
            "SIGNPATH_PROJECT_SLUG",
        ),
        (
            SIGNED_CD.replace(
                "api-token: ${{ secrets.SIGNPATH_API_TOKEN }}",
                "api-token: committed-token",
            ),
            "api-token",
        ),
        (
            SIGNED_CD.replace(
                "organization-id: ${{ vars.SIGNPATH_ORGANIZATION_ID }}",
                "organization-id: 00000000-0000-0000-0000-000000000000",
            ),
            "organization-id",
        ),
        (late, "sign before packaging"),
        (
            SIGNED_CD.replace("Get-AuthenticodeSignature", "Get-FileHash"),
            "Get-AuthenticodeSignature",
        ),
        (
            SIGNED_CD.replace(
                "      - if: steps.signpath.outputs.enabled == 'true'\n        run: powershell",
                "      - run: powershell",
            ),
            "Get-AuthenticodeSignature",
        ),
    ] {
        e.write(".github/workflows/cd.yml", &cd);
        let (ok, out) = e.call(&["workflow"], &[]);
        assert!(!ok && out.contains(why), "{why}: {out}");
    }
}

#[test]
fn release_plan_semver_ordering_prereleases_and_backdated_tags() {
    let e = Env::new();
    e.repo();
    e.write(".github/release-readiness.toml", "ready = true\n");
    for (v, prerelease, newest) in [
        ("1.0.0-alpha.2", true, false),
        ("1.0.0-alpha.10", true, false),
        ("1.0.0", false, true),
    ] {
        let (ok, out) = e.call(
            &[
                "release",
                "plan",
                "--ref",
                "refs/heads/main",
                "--version",
                v,
            ],
            &[],
        );
        assert!(
            ok && out.contains(&format!("prerelease={prerelease}"))
                && out.contains(&format!("newest={newest}"))
                && out.contains("publish=true"),
            "{out}"
        );
    }
    e.git(&["tag", "v9.0.0"]);
    let (ok, out) = e.call(
        &[
            "release",
            "plan",
            "--ref",
            "refs/heads/main",
            "--version",
            "1.0.0",
        ],
        &[],
    );
    assert!(ok && out.contains("newest=false"), "{out}");
    e.write(".github/release-readiness.toml", "invalid TOML");
    let (ok, out) = e.call(
        &[
            "release",
            "plan",
            "--ref",
            "refs/heads/main",
            "--version",
            "10.0.0",
        ],
        &[],
    );
    assert!(ok && out.contains("publish=false"), "{out}");
}

#[test]
fn the_gate_refuses_missing_tools() {
    let e = Env::new();
    e.write("Cargo.toml", "[workspace]\nmembers=[]\n");
    let path = e.0.join("empty-path");
    fs::create_dir(&path).unwrap();
    let path = path.to_string_lossy();
    let (ok, out) = e.call(&["check", "rust-test"], &[("PATH", path.as_ref())]);
    assert!(!ok && out.contains("could not start"), "{out}");
}

#[test]
fn docs_gate_rejects_broken_links_malformed_svg_and_inconsistent_structure() {
    let e = Env::new();
    e.repo();
    e.write("Cargo.toml", "[workspace]\nmembers=[]\n");
    e.write("README.md", "# Fixture\n\n![Diagram](diagram.svg)\n");
    e.write(
        "diagram.svg",
        "<svg xmlns=\"http://www.w3.org/2000/svg\"><style>@media (prefers-color-scheme: light) { :root { color: #000; } } @media (prefers-color-scheme: dark) { :root { color: #fff; } }</style><path/></svg>",
    );
    e.git(&["add", "."]);
    let (ok, out) = e.call(&["check", "docs"], &[]);
    assert!(ok, "{out}");
    e.write("diagram.svg", "<svg><broken></svg>");
    let (ok, out) = e.call(&["check", "docs"], &[]);
    assert!(!ok && out.contains("SVG"), "{out}");
    fs::remove_file(e.0.join("diagram.svg")).unwrap();
    let (ok, out) = e.call(&["check", "docs"], &[]);
    assert!(!ok && out.contains("missing link"), "{out}");

    e.write(
        "diagram.svg",
        "<svg xmlns=\"http://www.w3.org/2000/svg\"><path/></svg>",
    );
    let (ok, out) = e.call(&["check", "docs"], &[]);
    assert!(!ok && out.contains("light and dark modes"), "{out}");

    e.write("docs/task-controller/README.md", "# Task controller\n");
    e.git(&["add", "."]);
    let (ok, out) = e.call(&["check", "docs"], &[]);
    assert!(!ok && out.contains("docs/controllers/task"), "{out}");

    fs::remove_dir_all(e.0.join("docs/task-controller")).unwrap();
    e.write(
        "diagram.svg",
        "<svg xmlns=\"http://www.w3.org/2000/svg\"><style>@media (prefers-color-scheme: light) { :root { color: #000; } } @media (prefers-color-scheme: dark) { :root { color: #fff; } }</style><path/></svg>",
    );
    e.write("docs/controllers/README.md", "# Controllers\n");
    e.write("docs/controllers/task/README.md", "# Task controller\n");
    e.write(
        "AGENTS.md",
        "Read the [Task controller](docs/controllers/task/README.md).\n",
    );
    e.git(&["add", "-A"]);
    let (ok, out) = e.call(&["check", "docs"], &[]);
    assert!(!ok && out.contains("controller index"), "{out}");

    e.write("docs/controllers/plugin/README.md", "# Plugin controller\n");
    e.write(
        "docs/architecture.svg",
        "<svg xmlns=\"http://www.w3.org/2000/svg\"><style>@media (prefers-color-scheme: light) { :root { color: #000; } } @media (prefers-color-scheme: dark) { :root { color: #fff; } }</style><text>three controllers</text></svg>",
    );
    e.write(
        "AGENTS.md",
        "Read the [controller index](docs/controllers/README.md).\n",
    );
    e.git(&["add", "-A"]);
    let (ok, out) = e.call(&["check", "docs"], &[]);
    assert!(!ok && out.contains("PluginController"), "{out}");
    e.write("docs/architecture.svg", "<svg xmlns=\"http://www.w3.org/2000/svg\"><style>@media (prefers-color-scheme: light) { :root { color: #000; } } @media (prefers-color-scheme: dark) { :root { color: #fff; } }</style><text>TaskController</text><text>PluginController host</text></svg>");
    let (ok, out) = e.call(&["check", "docs"], &[]);
    assert!(ok, "{out}");

    e.write(
        "docs/controllers/gateway/README.md",
        "# Gateway controller\n\nDesign, not implemented.\n",
    );
    e.git(&["add", "-A"]);
    let (ok, out) = e.call(&["check", "docs"], &[]);
    assert!(!ok && out.contains("GatewayController"), "{out}");
    e.write("docs/architecture.svg", "<svg xmlns=\"http://www.w3.org/2000/svg\"><style>@media (prefers-color-scheme: light) { :root { color: #000; } } @media (prefers-color-scheme: dark) { :root { color: #fff; } }</style><text>TaskController</text><text>GatewayController</text><text>PluginController host</text></svg>");
    let (ok, out) = e.call(&["check", "docs"], &[]);
    assert!(!ok && out.contains("GatewayController"), "{out}");
    e.write("docs/architecture.svg", "<svg xmlns=\"http://www.w3.org/2000/svg\"><style>@media (prefers-color-scheme: light) { :root { color: #000; } } @media (prefers-color-scheme: dark) { :root { color: #fff; } }</style><text>TaskController</text><text>planned GatewayController</text><text>PluginController host</text></svg>");
    let (ok, out) = e.call(&["check", "docs"], &[]);
    assert!(ok, "{out}");

    e.write("docs/architecture.svg", "<svg xmlns=\"http://www.w3.org/2000/svg\"><style>@media (prefers-color-scheme: light) { :root { color: #000; } } @media (prefers-color-scheme: dark) { :root { color: #fff; } }</style><text>TaskController</text><text>planned GatewayController</text><text>planned PluginController host</text></svg>");
    let (ok, out) = e.call(&["check", "docs"], &[]);
    assert!(!ok && out.contains("PluginController"), "{out}");
    e.write(
        "docs/controllers/gateway/README.md",
        "# Gateway controller\n\nShipped.\n",
    );
    e.write("docs/architecture.svg", "<svg xmlns=\"http://www.w3.org/2000/svg\"><style>@media (prefers-color-scheme: light) { :root { color: #000; } } @media (prefers-color-scheme: dark) { :root { color: #fff; } }</style><text>TaskController</text><text>planned GatewayController</text><text>PluginController host</text></svg>");
    let (ok, out) = e.call(&["check", "docs"], &[]);
    assert!(!ok && out.contains("GatewayController"), "{out}");
}
#[cfg(unix)]
#[test]
fn workflow_symlink_outside_checkout_is_refused() {
    let e = Env::new();
    let outside = Env::new();
    outside.write("ci.yml", CI);
    fs::create_dir_all(e.0.join(".github/workflows")).unwrap();
    std::os::unix::fs::symlink(
        outside.0.join("ci.yml"),
        e.0.join(".github/workflows/ci.yml"),
    )
    .unwrap();
    let (ok, out) = e.call(&["workflow"], &[]);
    assert!(!ok && out.contains("outside checkout"), "{out}");
}

#[test]
fn swallowed_ci_and_release_test_failures_cannot_count_as_validation() {
    let e = Env::new();
    e.write(".github/workflows/cd.yml", CD);
    for replacement in ["cargo xtask check || true", "cargo xtask check; true"] {
        e.write(
            ".github/workflows/ci.yml",
            &CI.replace("cargo xtask check'", &format!("{replacement}'")),
        );
        let (ok, out) = e.call(&["workflow"], &[]);
        assert!(!ok, "swallowed CI failure accepted: {out}");
    }
    for replacement in [
        "continue-on-error: true\n    timeout-minutes: 30",
        "timeout-minutes: 30\n    continue-on-error: ${{ true }}",
    ] {
        e.write(
            ".github/workflows/ci.yml",
            &CI.replace("timeout-minutes: 30", replacement),
        );
        let (ok, out) = e.call(&["workflow"], &[]);
        assert!(!ok, "ignored CI failure accepted: {out}");
    }
    e.write(".github/workflows/ci.yml", CI);
    for replacement in [
        "if: false\n        run: cargo xtask check rust-test",
        "continue-on-error: true\n        run: cargo xtask check rust-test",
        "run: cargo xtask check rust-test || true",
    ] {
        e.write(
            ".github/workflows/cd.yml",
            &CD.replace("run: cargo xtask check rust-test", replacement),
        );
        let (ok, out) = e.call(&["workflow"], &[]);
        assert!(!ok, "skipped or swallowed release test accepted: {out}");
    }
}

#[test]
fn absent_or_invalid_readiness_holds_without_panicking() {
    let e = Env::new();
    e.repo();
    for text in [
        "",
        "reason = 'waiting'\n",
        "ready = 'true'\n",
        "ready = false\n",
        "invalid TOML",
    ] {
        e.write(".github/release-readiness.toml", text);
        let (ok, out) = e.call(
            &["release", "plan", "--ref", "refs/heads/main", "--dry-run"],
            &[],
        );
        assert!(
            ok && out.contains("publish=false") && out.contains("build=true"),
            "{out}"
        );
    }
    fs::remove_file(e.0.join(".github/release-readiness.toml")).unwrap();
    let (ok, out) = e.call(
        &["release", "plan", "--ref", "refs/heads/main", "--dry-run"],
        &[],
    );
    assert!(ok && out.contains("publish=false"), "{out}");
}

#[test]
fn windows_default_path_policy_is_disjoint_and_has_no_effects() {
    let e = Env::new();
    let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let program = if cfg!(windows) { "powershell" } else { "pwsh" };
    let spec = Spec::new(program, Duration::from_secs(30))
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
        ])
        .arg(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/install-paths.ps1")
                .into_os_string(),
        )
        .arg("-Installer")
        .arg(repo.join("scripts/install-release.ps1").into_os_string())
        .arg("-Root")
        .arg(e.0.clone().into_os_string());
    let outcome = run(&spec);
    if let Outcome::NotStarted(why) = &outcome {
        let requirement = if cfg!(windows) {
            "Windows PowerShell (powershell.exe) is required for the Windows installer path-policy test"
        } else {
            "PowerShell 7 (pwsh) is required for the Windows installer path-policy test"
        };
        panic!(
            "{requirement}. Install it and add {program} to PATH; see CONTRIBUTING.md. Could not start: {why}"
        );
    }
    if let Outcome::Exited {
        code,
        stdout,
        stderr,
    } = &outcome
    {
        assert_eq!(
            *code,
            Some(0),
            "{}{}",
            String::from_utf8_lossy(stdout),
            String::from_utf8_lossy(stderr)
        );
    } else {
        panic!("{}", outcome.failure());
    }
}

#[cfg(unix)]
#[test]
fn missing_pwsh_fails_with_an_actionable_gate_prerequisite() {
    let e = Env::new();
    let empty = e.0.join("empty-path");
    fs::create_dir(&empty).unwrap();
    let mut spec = Spec::new(
        std::env::current_exe().unwrap().into_os_string(),
        Duration::from_secs(30),
    )
    .args([
        "--exact",
        "windows_default_path_policy_is_disjoint_and_has_no_effects",
        "--nocapture",
    ]);
    spec.cwd = Some(e.0.clone());
    spec.env
        .push(("PATH".into(), empty.to_string_lossy().into_owned()));
    let Outcome::Exited {
        code,
        stdout,
        stderr,
    } = run(&spec)
    else {
        panic!("path-policy test child did not exit");
    };
    assert_ne!(code, Some(0), "missing PowerShell must fail, never skip");
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&stdout),
        String::from_utf8_lossy(&stderr)
    );
    assert!(
        text.contains("PowerShell 7 (pwsh) is required for"),
        "{text}"
    );
    assert!(
        text.contains("PATH") && text.contains("CONTRIBUTING.md"),
        "{text}"
    );
}

#[test]
fn the_gate_never_rebuilds_the_binary_it_runs_from() {
    // `cargo xtask check` runs fleet-dev while its own `cargo test` rebuilds
    // the workspace, fleet-dev included. Windows refuses to replace a running
    // executable, so the alias builds fleet-dev in a target dir of its own.
    let config = include_str!("../../../.cargo/config.toml");
    let alias = config
        .lines()
        .find(|l| l.trim_start().starts_with("xtask"))
        .expect("an xtask alias");
    assert!(alias.contains("--target-dir target/xtask"), "{alias}");
}

#[test]
fn v2_installs_only_when_a_version_is_named() {
    // Fleet v1 stays frozen; v2 is installed by explicitly naming
    // its version. The installer never resolves "latest", which could be v1.
    let e = Env::new();
    install_fixture(&e);
    let (ok, out) = installer_with(&e, &[], None);
    assert!(!ok, "{out}");
    assert!(out.contains("FLEET_VERSION"), "{out}");
    assert!(!out.contains("latest"), "{out}");
    let binary =
        e.0.join("bin")
            .join(if cfg!(windows) { "fleet.exe" } else { "fleet" });
    assert!(!binary.exists());
    // Removing an install needs no version.
    let (ok, out) = installer_with(&e, &["--uninstall"], None);
    assert!(ok, "{out}");
}

#[test]
fn the_tree_declares_fleet_major_2() {
    // Python v1 clones read this file from a fetched ref before they update,
    // and refuse anything that is not their own major version. This tree is
    // v2: it must say so exactly, and never inherit v1's `1`.
    let marker = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../FLEET_MAJOR");
    let text = fs::read_to_string(&marker).expect("a root FLEET_MAJOR file");
    assert_eq!(text, "2\n", "FLEET_MAJOR must be exactly \"2\\n\"");
}

#[test]
fn stable_tag_release_requires_main_ancestry_version_and_readiness() {
    let e = Env::new();
    e.repo();
    e.write("Cargo.toml", "[workspace.package]\nversion = '2.0.0'\n");
    e.write(".github/release-readiness.toml", "ready = true\n");
    e.commit("feat: stable release");
    e.git(&["update-ref", "refs/remotes/origin/main", "HEAD"]);
    e.git(&["tag", "v2.0.0"]);
    let args = ["release", "plan", "--ref", "refs/tags/v2.0.0"];
    let (ok, out) = e.call(&args, &[]);
    assert!(
        ok && out.contains("publish=true") && out.contains("version=v2.0.0"),
        "{out}"
    );
    assert!(
        out.contains("newest=true"),
        "first stable tag is latest: {out}"
    );
    let (ok, out) = e.call(
        &["release", "plan", "--ref", "refs/tags/v2.0.0", "--dry-run"],
        &[],
    );
    assert!(
        ok && out.contains("publish=false") && out.contains("build=true"),
        "{out}"
    );
    e.write(".github/release-readiness.toml", "ready = false\n");
    let (ok, out) = e.call(&args, &[]);
    assert!(ok && out.contains("publish=false"), "{out}");
    e.write(".github/release-readiness.toml", "ready = true\n");
    e.write("Cargo.toml", "[workspace.package]\nversion = '2.0.1'\n");
    let (ok, out) = e.call(&args, &[]);
    assert!(!ok && out.contains("manifest version"), "{out}");
    e.write("Cargo.toml", "[workspace.package]\nversion = '2.0.0'\n");
    e.write("crates/demo.rs", "pub fn unmerged() {}\n");
    e.commit("fix: unmerged candidate");
    let (ok, out) = e.call(&args, &[]);
    assert!(!ok && out.contains("tag does not name HEAD"), "{out}");
    e.git(&["tag", "v2.0.1"]);
    let (ok, out) = e.call(&["release", "plan", "--ref", "refs/tags/v2.0.1"], &[]);
    assert!(!ok && out.contains("main"), "unmerged tag: {out}");
}

#[test]
fn installed_binary_has_path_guidance() {
    let e = Env::new();
    install_fixture(&e);
    let (ok, out) = installer(&e, &[]);
    assert!(ok && out.contains("PATH"), "{out}");
}

#[test]
fn release_smoke_requires_offline_bootstrap_from_the_installed_binary() {
    let e = Env::new();
    let (ok, out) = e.call(
        &[
            "release",
            "smoke",
            "--binary",
            env!("CARGO_BIN_EXE_fleet-install-fixture"),
            "--version",
            "v1.2.3",
            "--first-run-dir",
            "clean-user",
        ],
        &[],
    );
    assert!(!ok && out.contains("bootstrap missing"), "{out}");
}

#[test]
fn release_checksums_include_published_installers_and_starter_metadata() {
    let e = Env::new();
    install_fixture(&e);
    for name in [
        "install-release.sh",
        "install-release.ps1",
        "vega-provenance.json",
        "vega-starter.toml",
    ] {
        e.write(&format!("releases/v1.2.3/{name}"), "release metadata");
    }
    let (ok, out) = e.call(
        &[
            "release",
            "checksums",
            "--version",
            "v1.2.3",
            "releases/v1.2.3",
        ],
        &[],
    );
    assert!(ok, "{out}");
    let sums = fs::read_to_string(e.0.join("releases/v1.2.3/fleet-v1.2.3-checksums.txt")).unwrap();
    for name in [
        "install-release.sh",
        "install-release.ps1",
        "vega-provenance.json",
        "vega-starter.toml",
    ] {
        assert!(sums.contains(&format!("  {name}\n")), "{sums}");
    }
}

#[test]
fn native_release_fixture_installs_with_binary_mode_checksum_tools() {
    let e = Env::new();
    install_fixture(&e);
    let fixture = e.0.join("installer-fixture/v1.2.3");
    fs::create_dir_all(&fixture).unwrap();
    for item in fs::read_dir(e.0.join("releases/v1.2.3")).unwrap() {
        let item = item.unwrap();
        if item
            .file_name()
            .to_string_lossy()
            .ends_with("checksums.txt")
        {
            continue;
        }
        fs::copy(item.path(), fixture.join(item.file_name())).unwrap();
    }
    let recipe = include_str!("../../../.github/workflows/cd.yml")
        .lines()
        .find(|l| l.contains("sha256sum") && l.contains("checksums.txt"))
        .expect("native release fixture checksum recipe")
        .trim();
    // On Windows PATH may resolve bash.exe to WSL, not Git Bash. Locate the
    // shell shipped beside the declared Git prerequisite, as Actions does.
    #[cfg(windows)]
    let shell = {
        let output = run(&Spec::new("git", Duration::from_secs(10)).arg("--exec-path"))
            .success_text()
            .expect("Git prerequisite");
        Path::new(output.trim())
            .ancestors()
            .map(|p| p.join("bin/bash.exe"))
            .find(|p| p.is_file())
            .expect("Git for Windows Bash is required for native release fixture acceptance")
    };
    #[cfg(not(windows))]
    let shell = PathBuf::from("bash");
    let mut spec =
        Spec::new(shell, Duration::from_secs(30)).args(["-eo", "pipefail", "-c", recipe]);
    spec.cwd = Some(e.0.clone());
    spec.env.push(("VERSION".into(), "v1.2.3".into()));
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        // Model Windows' binary default without claiming native Windows evidence.
        let tool =
            run(&Spec::new("sh", Duration::from_secs(10)).args(["-c", "command -v sha256sum"]))
                .success_text()
                .expect("sha256sum prerequisite");
        e.write(
            "checksum-tools/sha256sum",
            &format!("#!/bin/sh\nexec '{}' --binary \"$@\"\n", tool.trim()),
        );
        fs::set_permissions(
            e.0.join("checksum-tools/sha256sum"),
            fs::Permissions::from_mode(0o755),
        )
        .unwrap();
        let mut paths = vec![e.0.join("checksum-tools")];
        paths.extend(std::env::split_paths(&std::env::var_os("PATH").unwrap()));
        spec.env.push((
            "PATH".into(),
            std::env::join_paths(paths)
                .unwrap()
                .to_string_lossy()
                .into(),
        ));
    }
    let result = run(&spec);
    assert!(result.success_text().is_some(), "{result:?}");
    fs::copy(
        fixture.join("fleet-v1.2.3-checksums.txt"),
        e.0.join("releases/v1.2.3/fleet-v1.2.3-checksums.txt"),
    )
    .unwrap();
    let (ok, out) = installer(&e, &[]);
    assert!(ok, "native release checksum fixture: {out}");
}
