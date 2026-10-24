# Install the fleet binary from a GitHub release on Windows, or update it:
# running this again with a newer version replaces the binary in place.
#
#   FLEET_VERSION       the release tag, such as v2.0.0 (required: upgrades are explicit)
#   FLEET_INSTALL_DIR   where fleet.exe goes (default: %LOCALAPPDATA%\Programs\fleet\bin)
#   FLEET_RELEASE_BASE  where releases are fetched from: an https URL laid out as
#                       GitHub serves them (<base>/<tag>/<asset>), or a local
#                       directory with the same layout, for an offline install
#
# The archive and candidate version are verified before an atomic replacement.
# A failed replacement preserves the installed binary. x86_64 only,
# the one Windows target the release builds. Runs on Windows PowerShell 5.1
# and on PowerShell 7.
param([switch] $Uninstall)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Fail([string] $message) {
    [Console]::Error.WriteLine("install-release: $message")
    exit 1
}

$repo = 'Thurbeen/fleet'
$base = if ($env:FLEET_RELEASE_BASE) { $env:FLEET_RELEASE_BASE } else { "https://github.com/$repo/releases/download" }
# Pure path policy: explicit environment values in, destination path out.
function Get-FleetInstallDir([string] $Override, [string] $LocalAppData) {
    if ($Override) { return $Override }
    if (-not $LocalAppData) { throw 'LOCALAPPDATA is required when FLEET_INSTALL_DIR is unset' }
    return [IO.Path]::Combine($LocalAppData, 'Programs', 'fleet', 'bin')
}
$dir = Get-FleetInstallDir $env:FLEET_INSTALL_DIR $env:LOCALAPPDATA

# A 32-bit PowerShell on 64-bit Windows reports x86 here and the real
# architecture in PROCESSOR_ARCHITEW6432.
$arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
if ($arch -ne 'AMD64') {
    Fail "unsupported platform: Windows $arch. The release builds x86_64-pc-windows-msvc, x86_64-unknown-linux-musl and aarch64-unknown-linux-musl"
}
$target = 'x86_64-pc-windows-msvc'

New-Item -ItemType Directory -Force -Path $dir | Out-Null
$lock = Join-Path $dir '.fleet-install.lock'
try { New-Item -ItemType Directory -Path $lock -ErrorAction Stop | Out-Null }
catch { Fail "install lock exists at $lock; another installer may be running" }
$tmp = $null
$staged = $null
try {
    $dest = Join-Path $dir 'fleet.exe'
    if ($args.Count -ne 0) { Fail 'usage: install-release.ps1 [-Uninstall]' }
    if ($Uninstall) {
        if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Force }
        Write-Output "uninstalled fleet from $dir"
        exit 0
    }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$version = $env:FLEET_VERSION
# Pin the version explicitly; never redirect a v1 user to v2 automatically.
if (-not $version) {
    Fail 'Name the release explicitly, for example $env:FLEET_VERSION = ''v2.0.0''; see the README for frozen v1 installs.'
}

if ($version -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$') { Fail "invalid release version: $version" }
$archive = "fleet-$version-$target.zip"
$sums = "fleet-$version-checksums.txt"
$tmp = Join-Path $dir ('.fleet-download-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null

function Fetch([string] $name) {
    $dest = Join-Path $tmp $name
    try {
        if ($base -match '^https://') {
            Invoke-WebRequest -UseBasicParsing -Uri "$base/$version/$name" -OutFile $dest
        } elseif ($base -match '^[a-z]+://') {
            Fail "FLEET_RELEASE_BASE must be an https URL or a directory: $base"
        } else {
            Copy-Item -LiteralPath (Join-Path (Join-Path $base $version) $name) -Destination $dest
        }
    } catch {
        Fail "could not fetch $name for $version from ${base}: $_"
    }
    return $dest
}

    $zip = Fetch $archive
    $sumsFile = Fetch $sums

    $expected = $null
    foreach ($line in Get-Content -LiteralPath $sumsFile) {
        $fields = $line -split '  ', 2
        if ($fields.Count -eq 2 -and $fields[1] -eq $archive) { $expected = $fields[0] }
    }
    if (-not $expected) { Fail "$sums lists no checksum for $archive" }
    # .NET, not Get-FileHash or Expand-Archive: both are script-module
    # commands, and Windows PowerShell started from pwsh 7 inherits a module
    # path where they do not load.
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($zip)
    try {
        $actual = ([System.BitConverter]::ToString($sha.ComputeHash($stream)) -replace '-', '').ToLowerInvariant()
    } finally {
        $stream.Dispose()
        $sha.Dispose()
    }
    if ($actual -ne $expected) { Fail "checksum mismatch for ${archive}: expected $expected, got $actual" }

    $unpacked = Join-Path $tmp 'x'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $unpacked)
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $staged = Join-Path $dir ('.fleet-new-' + [guid]::NewGuid() + '.exe')
    Copy-Item -LiteralPath (Join-Path $unpacked 'fleet.exe') -Destination $staged
    $reported = & $staged --version
    if ($LASTEXITCODE -ne 0 -or $reported -cne "fleet $($version.Substring(1))") { Fail 'candidate version mismatch' }
    # ReplaceFile is one filesystem operation. If replacement is refused,
    # the existing executable remains present; there is no remove/rename gap.
    if (Test-Path -LiteralPath $dest) {
        # PowerShell passes $null to a .NET string parameter as "", which
        # Replace rejects as a backup path; NullString is a real null.
        [IO.File]::Replace($staged, $dest, [NullString]::Value)
    } else {
        [IO.File]::Move($staged, $dest)
    }
} finally {
    if ($tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
    if ($staged) { Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
}

Write-Output "installed fleet $version ($target) to $dest"
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($userPath -split ';') -contains $dir)) {
    Write-Output "$dir is not on your PATH; add it to run fleet by name."
}
