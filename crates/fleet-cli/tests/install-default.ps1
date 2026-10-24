# Native Windows clean install, followed by resource creation in the default store.
param([string] $Binary, [string] $Installer, [string] $Manifest, [string] $Root)
$ErrorActionPreference = 'Stop'
$reported = & $Binary --version
if ($LASTEXITCODE -ne 0 -or $reported -notmatch '^fleet (.+)$') { throw "fixture binary has no version (exit $LASTEXITCODE): $reported" }
$version = 'v' + $Matches[1]
$releases = Join-Path $Root 'releases'
$release = Join-Path $releases $version
New-Item -ItemType Directory -Force -Path $release | Out-Null
$archive = "fleet-$version-x86_64-pc-windows-msvc.zip"
$path = Join-Path $release $archive
# Windows PowerShell 5.1 loads neither: ZipArchiveMode is in System.IO.Compression.
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::Open($path, [IO.Compression.ZipArchiveMode]::Create)
try { [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $Binary, 'fleet.exe') | Out-Null }
finally { $zip.Dispose() }
$sha = [Security.Cryptography.SHA256]::Create()
$stream = [IO.File]::OpenRead($path)
try { $hash = ([BitConverter]::ToString($sha.ComputeHash($stream)) -replace '-', '').ToLowerInvariant() }
finally { $stream.Dispose(); $sha.Dispose() }
[IO.File]::WriteAllText((Join-Path $release "fleet-$version-checksums.txt"), "$hash  $archive`n", [Text.UTF8Encoding]::new($false))
$env:LOCALAPPDATA = Join-Path $Root 'local'
$env:FLEET_VERSION = $version
$env:FLEET_RELEASE_BASE = $releases
Remove-Item Env:FLEET_INSTALL_DIR -ErrorAction SilentlyContinue
Remove-Item Env:FLEET_CONFIG -ErrorAction SilentlyContinue
& $Installer
if ($LASTEXITCODE -ne 0) { throw 'default installation failed' }
$installed = Join-Path $env:LOCALAPPDATA 'Programs\fleet\bin\fleet.exe'
if (-not (Test-Path -LiteralPath $installed)) { throw 'binary not installed at the documented destination' }
& $installed apply -f $Manifest
if ($LASTEXITCODE -ne 0) { throw 'first default-store apply failed after clean installation' }
& $installed get Task example
if ($LASTEXITCODE -ne 0) { throw 'fresh resource not found' }
$marker = Join-Path $env:LOCALAPPDATA 'fleet\store.json'
if (-not (Test-Path -LiteralPath $marker)) { throw 'default resource store was not created' }
Write-Output 'native clean install and default-store apply: passed'
