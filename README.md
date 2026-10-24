# Fleet

## Stable: Python v1.0.0

**Use the pinned v1.0.0 release for stable Fleet.** `main` is the development
branch for v2.0.0 beta, which implements the Task resource lifecycle. V2 is not
a stable release and has different CLI, configuration and data formats. There is
no automatic v1 migration.

For a new stable installation, install git and
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

**Unpatched clients must install this freeze before v2 lands.** They can
otherwise pull v2 through their old automatic sync or installer. Documentation
alone cannot protect old code, and a manual `git pull` bypasses the guard.
Pin the tag explicitly using the commands above; do not run development
installers over a stable checkout.

To inspect v2 deliberately, use a separate clone of `main` and follow its
README. [PR #308](https://github.com/Thurbeen/fleet/pull/308) is the unmerged
Task/controller beta candidate; v1 queues and configuration are not v2
inputs. Keep beta configuration and data separate from v1.

## Fleet v2 development

Fleet v2 runs one agent session per Task. A Task names a harness, a runtime
host, a workspace and a brief. Its controller spawns the session, sends the
brief once, records the worker's answer, and deletes the session when the Task
is deleted. Fleet v2 is a Rust binary with a file-backed resource store, one
manager lease, and explicit ownership of each status field.

Read the [overall architecture](ARCHITECTURE.md) for the component boundaries
and execution flow. The [Task controller guide](docs/task-controller/README.md)
defines its [resource](docs/task-controller/resource.md),
[state machine](docs/task-controller/state-machine.md) and
[interface](docs/task-controller/interface.md);
[the contracts](crates/CONTRACTS.md) record the integration guarantees.
This is the v2 development line: releases stay held until acceptance, and
Fleet v1 remains the default for existing users.
The root `FLEET_MAJOR` file says `2`; a v1 checkout containing the freeze
guard reads it before updating and refuses to move onto v2.

## Build and try it

Install the toolchain in [rust-toolchain.toml](rust-toolchain.toml) through
rustup, then build the binary from the committed dependency lock:

```sh
cargo build --release --locked -p fleet-cli --bin fleet
```

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

After a production release is accepted, download the matching archive and
checksum file from [Fleet releases](https://github.com/Thurbeen/fleet/releases).
Supported release targets are Linux x86_64 and aarch64 (musl), and Windows
x86_64 (MSVC with a static CRT). macOS is not a release target.

Fleet v1 stays the default for existing users; v2 is a beta you opt into.
The checked-in installers never pick a version for you: they refuse to run
unless `FLEET_VERSION` names a v2 release tag, and they never resolve
"latest". Inspect a downloaded script, then execute it:

```sh
FLEET_VERSION=v2.0.0-beta.1 sh scripts/install-release.sh
sh scripts/install-release.sh --uninstall
```

On Windows, use Windows PowerShell 5.1 or PowerShell 7:

```powershell
$env:FLEET_VERSION = 'v2.0.0-beta.1'
powershell -NoProfile -File scripts/install-release.ps1
powershell -NoProfile -File scripts/install-release.ps1 -Uninstall
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
`--data-dir` wins over `[data] dir`. A nonempty directory without this build's
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

The release readiness hold is independent of these operator actions. Changing
it requires combined tests and review. Nothing in CI or installation performs
a live cutover.

## Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md) for the native gate, release dry runs,
and architecture tests. [AGENTS.md](AGENTS.md) holds agent working rules.
