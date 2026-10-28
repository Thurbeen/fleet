//! Release policy and reproducible packaging, shared by CI and fixture tests.
use crate::{Result, exec};
use clap::Subcommand;
use semver::Version;
use sha2::{Digest, Sha256};
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};

pub const TARGETS: [&str; 3] = [
    "x86_64-unknown-linux-musl",
    "aarch64-unknown-linux-musl",
    "x86_64-pc-windows-msvc",
];
#[derive(Debug, Subcommand)]
pub enum Command {
    Title {
        title: String,
        number: String,
    },
    Plan {
        #[arg(long = "ref")]
        reference: Option<String>,
        #[arg(long)]
        version: Option<String>,
        #[arg(long)]
        dry_run: bool,
    },
    Package {
        #[arg(long)]
        target: String,
        #[arg(long)]
        version: String,
        #[arg(long)]
        binary: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    Checksums {
        #[arg(long)]
        version: String,
        directory: PathBuf,
    },
    Smoke {
        /// Run offline initialization in a new, isolated user directory.
        #[arg(long)]
        first_run_dir: Option<PathBuf>,
        #[arg(long)]
        binary: PathBuf,
        #[arg(long)]
        version: String,
    },
}
fn version(s: &str) -> Result<Version> {
    Ok(Version::parse(s.strip_prefix('v').unwrap_or(s))?)
}
fn conventional(s: &str) -> Option<(&str, bool)> {
    let (prefix, summary) = s.split_once(": ")?;
    if summary.trim().is_empty() || s.contains(['\n', '\r']) {
        return None;
    }
    let breaking = prefix.ends_with('!');
    let p = prefix.trim_end_matches('!');
    let kind = if let Some((kind, scope)) = p.split_once('(') {
        let scope = scope.strip_suffix(')')?;
        if scope.is_empty()
            || !scope
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || "._/-".contains(c))
        {
            return None;
        }
        kind
    } else {
        p
    };
    [
        "feat", "fix", "perf", "refactor", "docs", "style", "test", "chore", "build", "ci",
        "revert",
    ]
    .contains(&kind)
    .then_some((kind, breaking))
}
fn git(args: &[&str]) -> Result<String> {
    exec("git", args, &[])
}
fn tags(extra: &[&str]) -> Result<Vec<(Version, String)>> {
    let mut args = vec!["tag", "--list", "v*"];
    args.extend_from_slice(extra);
    Ok(git(&args)?
        .lines()
        .filter_map(|s| version(s).ok().map(|v| (v, s.into())))
        .collect())
}
fn shipped(p: &str) -> bool {
    p.starts_with("crates/")
        || p.starts_with("scripts/install-release.")
        || [
            "Cargo.toml",
            "Cargo.lock",
            "rust-toolchain.toml",
            "LICENSE",
            ".github/workflows/cd.yml",
        ]
        .contains(&p)
}
fn plan(reference: Option<&str>, forced: Option<&str>, dry: bool) -> Result {
    let all = tags(&[])?;
    let last = tags(&["--merged", "HEAD"])?
        .into_iter()
        .max_by(|a, b| a.0.cmp(&b.0));
    let span = last
        .as_ref()
        .map(|(_, s)| format!("{s}..HEAD"))
        .unwrap_or("HEAD".into());
    let mut reason = "no releasable commit".to_string();
    let proposed = if let Some(v) = forced {
        reason = "forced by dispatch".into();
        Some(version(v)?)
    } else {
        // A dry run reads committed history only, so untracked files are
        // irrelevant. On success cog prints the next tag when a bump is due and
        // nothing on stdout when none is. Anything else is an error, never
        // "no release".
        let out = exec(
            "cog",
            &[
                "bump",
                "--auto",
                "--disable-bump-commit",
                "--dry-run",
                "--skip-untracked",
            ],
            &[],
        )?;
        let next = match out.lines().map(str::trim).find(|l| !l.is_empty()) {
            None => None,
            Some(l) if l.starts_with('v') => Some(version(l)?),
            _ => return Err(format!("unexpected cog bump output: {out}").into()),
        };
        let changed = if last.is_some() {
            git(&["diff", "--name-only", &span])?
        } else {
            git(&["ls-tree", "-r", "--name-only", "HEAD"])?
        };
        match next {
            Some(v) if changed.lines().any(shipped) => {
                reason = "releasable shipped changes".into();
                Some(v)
            }
            _ => None,
        }
    };
    if let Some(v) = &proposed
        && all.iter().any(|(t, _)| t == v)
    {
        return Err(format!("v{v} is already a tag; a tag never moves").into());
    }
    let ready = fs::read_to_string(".github/release-readiness.toml")
        .ok()
        .and_then(|s| toml::from_str::<toml::Value>(&s).ok())
        .is_some_and(|v| v.get("ready").and_then(toml::Value::as_bool) == Some(true));
    let release = proposed.is_some();
    let publish = release && ready && reference == Some("refs/heads/main") && !dry;
    let newest = proposed
        .as_ref()
        .is_some_and(|v| v.pre.is_empty() && all.iter().all(|(t, _)| v > t));
    let prerelease = proposed.as_ref().is_some_and(|v| !v.pre.is_empty());
    let v = if let Some(v) = proposed {
        format!("v{v}")
    } else if dry {
        format!(
            "v0.0.0-dryrun.g{}",
            git(&["rev-parse", "--short=7", "HEAD"])?.trim()
        )
    } else {
        String::new()
    };
    if release && !publish {
        reason = if dry {
            "dry run"
        } else if !ready {
            "release readiness held"
        } else {
            "only refs/heads/main publishes"
        }
        .into();
    }
    println!(
        "release={release}\nversion={v}\nsemver={}\nnewest={newest}\nprerelease={prerelease}\npublish={publish}\nbuild={}\nreason={reason}",
        v.trim_start_matches('v'),
        publish || dry
    );
    Ok(())
}
fn archive(version: &str, target: &str) -> String {
    format!(
        "fleet-{version}-{target}.{}",
        if target.contains("windows") {
            "zip"
        } else {
            "tar.gz"
        }
    )
}
fn normalized(v: &str) -> Result<String> {
    Ok(format!("v{}", version(v)?))
}
fn package(target: &str, v: &str, binary: &Path, out: &Path) -> Result {
    if !TARGETS.contains(&target) {
        return Err(format!("unsupported release target: {target}").into());
    }
    let v = normalized(v)?;
    let exe = fs::read(binary)?;
    let license = fs::read("LICENSE")?;
    fs::create_dir_all(out)?;
    let path = out.join(archive(&v, target));
    let file = File::create(&path)?;
    if target.contains("windows") {
        let mut z = zip::ZipWriter::new(file);
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Stored)
            .last_modified_time(zip::DateTime::default());
        for (name, bytes) in [("fleet.exe", &exe), ("LICENSE", &license)] {
            z.start_file(name, options)?;
            z.write_all(bytes)?;
        }
        z.finish()?;
    } else {
        let gz = flate2::GzBuilder::new()
            .mtime(0)
            .write(file, flate2::Compression::default());
        let mut tar = tar::Builder::new(gz);
        for (name, bytes, mode) in [("fleet", &exe, 0o755), ("LICENSE", &license, 0o644)] {
            let mut h = tar::Header::new_gnu();
            h.set_size(bytes.len() as u64);
            h.set_mode(mode);
            h.set_mtime(0);
            h.set_uid(0);
            h.set_gid(0);
            h.set_cksum();
            tar.append_data(&mut h, name, bytes.as_slice())?;
        }
        tar.into_inner()?.finish()?;
    }
    println!("{}", path.display());
    Ok(())
}
fn checksums(v: &str, dir: &Path) -> Result {
    let v = normalized(v)?;
    let mut names = TARGETS.iter().map(|t| archive(&v, t)).collect::<Vec<_>>();
    for name in [
        "install-release.sh",
        "install-release.ps1",
        "vega-provenance.json",
        "vega-starter.toml",
    ] {
        if dir.join(name).is_file() {
            names.push(name.into());
        }
    }
    names.sort();
    let mut sums = String::new();
    for name in names {
        let bytes =
            fs::read(dir.join(&name)).map_err(|e| format!("missing archive {name}: {e}"))?;
        let hash = Sha256::digest(bytes)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>();
        sums.push_str(&format!("{hash}  {name}\n"));
    }
    let path = dir.join(format!("fleet-{v}-checksums.txt"));
    fs::write(&path, sums)?;
    println!("{}", path.display());
    Ok(())
}
pub fn run(c: Command) -> Result {
    match c {
        Command::Title { title, number } => {
            if conventional(&title).is_none()
                || title.trim_end().ends_with(')')
                    && title.rsplit_once(" (#").is_some_and(|(_, n)| {
                        n.trim_end_matches(')').chars().all(|c| c.is_ascii_digit())
                    })
            {
                return Err("invalid conventional title or duplicate (#N) suffix".into());
            }
            println!("title accepted: {title} (#{number})");
            Ok(())
        }
        Command::Plan {
            reference,
            version,
            dry_run,
        } => plan(reference.as_deref(), version.as_deref(), dry_run),
        Command::Package {
            target,
            version,
            binary,
            out,
        } => package(&target, &version, &binary, &out),
        Command::Checksums { version, directory } => checksums(&version, &directory),
        Command::Smoke {
            binary,
            version: v,
            first_run_dir,
        } => {
            let expected = version(&v)?.to_string();
            let path = fs::canonicalize(binary)?;
            let text = exec(
                path.to_str().ok_or("non-UTF8 binary path")?,
                &["--version"],
                &[],
            )?;
            if text.trim() != format!("fleet {expected}") {
                return Err(
                    format!("version mismatch: {text:?}, expected fleet {expected}").into(),
                );
            }
            if let Some(root) = first_run_dir {
                fs::create_dir(&root)?;
                let root = fs::canonicalize(root)?;
                let mut spec =
                    fleet_exec::Spec::new(path, std::time::Duration::from_secs(30)).arg("init");
                spec.clear_env = true;
                spec.cwd = Some(root.clone());
                for (key, p) in [
                    ("HOME", root.clone()),
                    ("USERPROFILE", root.clone()),
                    ("XDG_CONFIG_HOME", root.join("config")),
                    ("APPDATA", root.join("config")),
                    ("XDG_DATA_HOME", root.join("data")),
                    ("LOCALAPPDATA", root.join("data")),
                    ("PATH", root.join("no-tools")),
                ] {
                    spec.env.push((key.into(), p.to_string_lossy().into()));
                }
                for key in ["SYSTEMROOT", "WINDIR"] {
                    if let Ok(value) = std::env::var(key) {
                        spec.env.push((key.into(), value));
                    }
                }
                let outcome = fleet_exec::run(&spec);
                if outcome.success_text().is_none() {
                    return Err(format!("offline bootstrap failed: {}", outcome.failure()).into());
                }
                for file in [
                    "config/fleet/config.toml",
                    "data/fleet/vega/AGENTS.md",
                    "data/fleet/vega/task.json",
                    "data/fleet/vega/LICENSE",
                    "data/fleet/vega/.fleet/starter.toml",
                    "data/fleet/vega/.fleet/provenance.json",
                ] {
                    if !root.join(file).is_file() {
                        return Err(format!("bootstrap missing {file}").into());
                    }
                }
                println!(
                    "offline installed bootstrap: config, starter, license and provenance verified"
                );
            }
            Ok(())
        }
    }
}
