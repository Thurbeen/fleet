# Fleet

Fleet v2.0.0 runs one agent session per Task, with a bundled VEGA starter.
Install a standalone binary; Rust, Cargo and Git are not needed.

Linux x86_64 or aarch64:

```sh
curl -fsSL https://github.com/Thurbeen/fleet/releases/download/v2.0.0/install-release.sh | FLEET_VERSION=v2.0.0 sh
```

Windows x86_64 (PowerShell 5.1 or 7):

```powershell
$env:FLEET_VERSION='v2.0.0'; & ([scriptblock]::Create((Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/Thurbeen/fleet/v2.0.0/scripts/install-release.ps1).Content))
```

Installers verify the archive checksum and exact binary version, then install
only the executable. Follow their PATH guidance. Run `fleet init` separately
for offline setup, then `fleet up` when Thurbox and the harness are ready.
[Release assets](https://github.com/Thurbeen/fleet/releases/tag/v2.0.0) include
checksums, pinned installers, VEGA manifest/provenance and GitHub build
attestations. macOS has no binary release.

## Frozen Python v1.0.0

**Existing Python users stay on pinned v1.0.0.** V2 has different CLI,
configuration and data formats. There is no automatic v1 migration; installing
v2 is a separate, deliberate choice. Preserve the v1 checkout and data.

For a frozen v1 installation, install git and
[uv](https://docs.astral.sh/uv/getting-started/installation/), then run these
commands on Linux or Windows PowerShell:

```text
git clone --branch v1.0.0 --single-branch https://github.com/Thurbeen/fleet.git fleet-v1
uv run --project fleet-v1 fleet install
```

The tag checkout is detached deliberately: it stays at the frozen Python
release rather than following `main`. Keep this directory; the extension
records its path.

**Existing users following `main` must pin v1 before updating.** In your
existing Python Fleet checkout, preserve your operator state and resolve any
tracked edits before switching; do not force the checkout:

```text
git fetch origin tag v1.0.0
git switch --detach v1.0.0
uv run fleet install
```

These commands keep the checkout in place and do not migrate its queue or
configuration. The detached checkout prevents ordinary sync from following
`main`. This freeze also includes a v1 major-version boundary: SessionStart
and reconciler sync, and both v1 installers, inspect the fetched candidate
before checkout or fast-forward. They refuse a missing or non-v1
`FLEET_MAJOR` marker, or missing Python entry points, and leave the existing
checkout and data unchanged. A fresh v1 installer also checks before writing
candidate files. To reinstall a pinned checkout, use `uv run fleet install`
there rather than the branch-tracking bootstrap.

**Unpatched clients must install this freeze before updating.** They can
otherwise pull v2 through their old automatic sync or installer. Documentation
alone cannot protect old code, and a manual `git pull` bypasses the guard.
Pin the tag explicitly using the commands above; do not run development
installers over a stable checkout.

Keep v2 configuration and data separate from v1. The v1.0.0 tag and its
major-version update guard stay frozen; binary installation does not change
a running Python checkout.

## Fleet v2

Fleet v2 runs one agent session per Task. A Task names a harness, a runtime
host, a workspace and a brief. Its controller spawns the session, sends the
brief once, records the worker's answer, and deletes the session when the Task
is deleted. Fleet v2 is a Rust binary with a file-backed resource store, one
manager lease, and explicit ownership of each status field.

Read the [overall architecture](docs/ARCHITECTURE.md) for the component boundaries
and execution flow. The [Task controller guide](docs/task-controller/README.md)
defines its [resource](docs/task-controller/resource.md),
[state machine](docs/task-controller/state-machine.md) and
[interface](docs/task-controller/interface.md);
[the contracts](crates/CONTRACTS.md) record the integration guarantees.
V2 is a standalone binary; existing Python deployments stay on frozen v1.
The root `FLEET_MAJOR` file says `2`; a v1 checkout containing the freeze
guard reads it before updating and refuses to move onto v2.

## Build and try it

Install the toolchain in [rust-toolchain.toml](rust-toolchain.toml) through
rustup, then build the binary from the committed dependency lock:

```sh
cargo build --release --locked -p fleet-cli --bin fleet
```

The binary embeds a pinned [VEGA starter](https://github.com/Thurbeen/vega).
Copy it onto PATH; initial setup needs neither Git nor a Fleet checkout:

```sh
fleet init
fleet up
```

`init` works offline. To run real sessions, install Thurbox (`thurbox-cli`
on PATH), configure its local multiplexer, and install and authenticate the
starter's `codex` harness first. Fleet does not install prerequisites or change
login state. `up` checks host availability and reports unavailable or unknown
prerequisites. Harness login/readiness failures remain visible in Task status;
inspect `fleet status task vega --json` and Thurbox's session before retrying.

`up` runs in the foreground and continuously reconciles every due Task, even
while VEGA is busy or idle. Keep it running in a terminal or your own service
supervisor. Ctrl-C (also SIGTERM on Unix) finishes the bounded sweep, releases
the manager lease and retains sessions. Run `fleet up` again to reconnect to
the same ordinary `Task/vega`; a simultaneous manager exits with code 6.
After a crash, wait for the recorded lease to expire before restarting. Unknown
session observations never trigger a blind replacement. Stalled, completed or
deleting VEGA Tasks require inspection and deliberate recovery, not another
automatic Task identity.

### Configuration and customization

Config selection is `--config PATH`, then `FLEET_CONFIG`, then exactly one
native per-user path: `$XDG_CONFIG_HOME/fleet/config.toml` (fallback
`~/.config/fleet/config.toml`) on Linux, `%APPDATA%\fleet\config.toml` on
Windows. Fleet never searches the current directory or its parents. Explicit
paths must exist for commands other than `init`; `init --config PATH` creates
a separate configuration and a default data/workspace root at
`<native-data>/fleet/instances/<config-path-hash>/`. Relative paths inside TOML
resolve beside that file. With no native data-home variables, explicit
`--config` uses a `fleet-data` directory beside the config as its data root.
An explicit store must be separate from the config and workspace.

Default init writes a workspace at `$XDG_DATA_HOME/fleet/vega` (fallback
`~/.local/share/fleet/vega`), or `%LOCALAPPDATA%\fleet\vega` on Windows, and a
fresh resource store under the same Fleet directory at `store-v1`. The config
records absolute paths and a generated per-instance session key prefix.
`[starter] workspace` selects the workspace. Its `task.json` is a runnable JSON
array containing one Task. Before first `up`, edit its harness or brief
and customize `AGENTS.md`, prompts and skills. To set `spec.model`, first
configure the matching `[runtime.modelEnv]` harness-to-variable mapping; the
starter does not assume a Codex model environment variable. After creation,
reconnect keeps
the stored immutable Task inputs. Updated instructions can be read from the
workspace; changing Task inputs requires deliberate deletion/recreation after
session release. VEGA's rendered brief names the exact config file for all
Task operations and child-worker handoffs.

Repeated `init` preserves existing files and config. After updating the binary,
run `fleet init --update` to offer its bundled content. Unedited managed
workspace files advance; edits, deletions and private files survive. Config is
user-owned after creation and is never replaced by an update. Review proposed
conflicts under `vega/.fleet/conflicts/<content-hash>/`; merge them manually.
The `.fleet` directory holds instance identity, baseline hashes, manifest and
source provenance; preserve it with the workspace. Malformed metadata and
managed symlinks are refused. Initializers serialize with OS locks; a crash
releases the locks and a repeat init completes missing files conservatively.

The bundled content is MIT licensed at exact source commit
`27148af18577f954b5c3b17667f2510c5940198e`. The build verifies every allowlisted
asset against [the provenance hashes](crates/fleet-cli/bundle/vega/provenance.json)
and embeds the assets, source manifest and license in the executable. Runtime
init/update never fetches a branch or downloads content. Polling defaults to
one bounded sweep per second; `up --poll-ms` accepts 100 through 10000 ms.
Each unfinished Briefed Task costs one bounded session lookup per sweep;
unchanged Task status is not rewritten. A manual `fleet reconcile` remains
available for bounded diagnostics while `up` is stopped.

Use the deliberately isolated fake runtime example to explore resource flows
without starting an agent or contacting a forge:

```sh
fleet --config examples/fleet.toml apply -f examples/resources.json
fleet --config examples/fleet.toml list Task --json
fleet --config examples/fleet.toml get Task example
fleet --config examples/fleet.toml status Task example --json
fleet version --json
```

The example Task is suspended (`dispatch: false`). A Task whose spec sets
`dispatch: true` starts when `fleet reconcile` observes it. With the fake
runtime this records a simulated session and runs no external tool. For real
sessions configure `[runtime] kind = "thurbox"` and set the Task's
`spec.host` to a host thurbox knows (`local` by default). Connection details
stay in thurbox.

```sh
fleet --config examples/fleet.toml reconcile
fleet --config examples/fleet.toml delete Task example
fleet --config examples/fleet.toml reconcile
```

`apply -f -` reads JSON from stdin. A file can hold one object, an array of
objects, or an object with `items`. Config uses versioned TOML; resource
manifests use `apiVersion: fleet/v1alpha1`. Paths in the config are relative
to that config file. Use `fleet --help` and each command's `--help` for the
flags this build actually serves.

A worker writes its answer with `fleet result TASK --generation N --outcome
shipped --summary TEXT`; the generation must match the delivered brief, and
required outputs must match the Task's declared schema. A result is a claim,
not proof of an artifact landing.

## Install, update and uninstall

Use the one-liners above, or download and inspect the pinned installer,
matching archive and checksum file from [Fleet releases](https://github.com/Thurbeen/fleet/releases).
Supported release targets are Linux x86_64 and aarch64 (musl), and Windows
x86_64 (MSVC with a static CRT). macOS is not a release target.

The installers require an explicit `FLEET_VERSION` and never resolve
"latest". To inspect before executing, download the scripts from the versioned
release assets into a new directory, then run:

```sh
FLEET_VERSION=v2.0.0 sh install-release.sh
sh install-release.sh --uninstall
```

On Windows, use Windows PowerShell 5.1 or PowerShell 7:

```powershell
$env:FLEET_VERSION = 'v2.0.0'
powershell -NoProfile -File install-release.ps1
powershell -NoProfile -File install-release.ps1 -Uninstall
```

Repeat installation with a newer `FLEET_VERSION` to update. Set
`FLEET_INSTALL_DIR` to choose an isolated binary directory; the defaults are
`~/.local/bin` and
`%LOCALAPPDATA%\Programs\fleet\bin`. Add that directory to PATH yourself. Installers
verify the archive checksum and candidate version before replacing the
binary, and refuse a second installer holding the destination lock. A failed
update preserves the old binary. A stale `.fleet-install.lock` requires
checking that its installer is gone before manually removing that lock.

`FLEET_RELEASE_BASE` may name an HTTPS release download base or an offline
directory laid out as `<base>/<tag>/<archive-and-checksums>`. Installers never
start services, install an extension, configure credentials, or alter data.
Uninstall removes the binary only. Stop managers deliberately before updating
or uninstalling an operational deployment; changing an executable does not
transfer its manager lease.

## Data and deliberate cutover

The default resource store is fresh: `$XDG_DATA_HOME/fleet` or
`~/.local/share/fleet` on Linux, `%LOCALAPPDATA%\fleet` on Windows. An explicit
`--data-dir` wins over `[data] dir` for resource commands. For `up`, persist
that directory in `[data] dir` first: a differing runtime override is refused
so the VEGA brief and workers use the same store. A nonempty directory without
this build's
store marker, an unsupported schema, or corrupt data is refused. Keep private
config and resources outside a public checkout. Fleet v2 passes no
environment to a session besides the model variable; credentials stay with
the agent and its host, never in manifests or briefs.

To switch an existing deployment deliberately:

1. Preserve its checkout, configuration, records, services and active sessions.
   Complete or explicitly retain every child before stopping its manager.
2. Back up its private state. Disable its automatic service and hook starts at
   the chosen no-child boundary; verify its old manager has stopped.
3. Install the accepted Rust binary into a separate directory. Choose a fresh
   data directory and apply freshly authored resources. There is no automatic
   parser, importer, live-data reset, or migration of old queue records.
4. Run `fleet version --json`, inspect resources and their schema versions,
   then start the new manager explicitly. Never point two implementations at
   the same data or run both as owners of the same work.
5. If acceptance fails, stop the new manager before restoring the preserved
   deployment. Preserve its new store for diagnosis. Installer rollback alone
   does not downgrade a store schema.

Release acceptance is independent of these operator actions. Changing
it requires combined tests and review. Nothing in CI or installation performs
a live cutover.

## Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md) for the native gate, release dry runs,
and architecture tests. [AGENTS.md](AGENTS.md) holds agent working rules.

## Reusable execution defaults

Tasks can reference a passive `TaskConfig` for harness, host, model and
environment defaults. `EnvProvider` resources supply stored string maps and
explicit manager-local JSON files or bounded commands. Explicit Task values
win; launch values are frozen privately before the first effect and reused
across retries and restarts. See [execution configuration](docs/task-controller/execution-config.md)
for runnable manifests, precedence, security boundaries and snapshot recovery.
