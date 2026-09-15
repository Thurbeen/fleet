# Install fleet on Windows: uv, git and the checkout, then `fleet install` for everything else.
#
#     powershell -c "irm https://raw.githubusercontent.com/Thurbeen/fleet/main/install.ps1 | iex"
#
# Or read it before running it:
#
#     irm https://raw.githubusercontent.com/Thurbeen/fleet/main/install.ps1 -OutFile install.ps1
#     notepad install.ps1
#     powershell -ExecutionPolicy Bypass -File install.ps1
#
# The same four steps as install.sh, whose header argues each of them:
#
#   1. uv         astral's own installer, which needs no admin
#   2. git        winget, after saying so and asking once
#   3. checkout   clone it, or fast-forward the one already there - sticky
#                 where the Mission Control lead already opens one, never
#                 overwriting a directory that is not a fleet clone, another
#                 branch, a divergence, or uncommitted changes it is behind on
#   4. hand-off   uv run --project <checkout> fleet install
#
# `fleet install` (scripts/lib/install.py) owns everything else.
#
# `irm | iex` EVALUATES THIS TEXT in the operator's own session. So there is no
# param() block, settings come from the environment, and nothing here calls
# `exit`, which would close the operator's window: the body is one function,
# and only a run as a file turns its result into an exit code. Windows
# PowerShell 5.1 is the floor.
#
# Settings, from the environment:
#   FLEET_DIR     where the checkout goes (default: %USERPROFILE%\fleet)
#   FLEET_REPO    what to clone (default: https://github.com/Thurbeen/fleet.git)
#   FLEET_BRANCH  the branch to track (default: main)
#   FLEET_YES     1 answers every question yes
#   UV_INSTALL_DIR, UV_NO_MODIFY_PATH   passed through to astral's uv installer
#   FLEET_TEST_UV_INSTALLER   TESTS ONLY: a local .ps1 run instead of
#                 downloading astral's installer; nothing reads it unless set
#
# As a file it also takes --dir DIR and --yes.
# Exit (as a file): 0 installed, 1 refused or a step failed, 2 usage.

function Test-FleetCommand([string]$Name) {
    [bool](Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue)
}

function Write-FleetRefusal([string]$Message) {
    [Console]::Error.WriteLine("")
    [Console]::Error.WriteLine("fleet install: $Message")
}

# Append the user's and the machine's registry Path, where an installer writes
# a new directory that a running process never re-reads.
function Update-FleetPath {
    $seen = @{}
    $entries = New-Object System.Collections.Generic.List[string]
    $all = @($env:Path) + @([Environment]::GetEnvironmentVariable('Path', 'Machine')) +
        @([Environment]::GetEnvironmentVariable('Path', 'User'))
    foreach ($value in $all) {
        if (-not $value) { continue }
        foreach ($entry in ([Environment]::ExpandEnvironmentVariables($value) -split ';')) {
            if ($entry -and -not $seen.ContainsKey($entry.ToLowerInvariant())) {
                $seen[$entry.ToLowerInvariant()] = $true
                $entries.Add($entry)
            }
        }
    }
    $env:Path = $entries -join ';'
}

# The installer's own order of places, put on this session's PATH where uv is in one.
function Add-FleetUvPath {
    $dirs = @($env:UV_INSTALL_DIR, $env:XDG_BIN_HOME)
    if ($env:XDG_DATA_HOME) { $dirs += (Join-Path $env:XDG_DATA_HOME '..\bin') }
    $dirs += (Join-Path $HOME '.local\bin')
    $dirs += (Join-Path $env:USERPROFILE '.local\bin')
    $found = @($dirs | Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path $_ 'uv.exe')) })
    if ($found) { $env:Path = (($found + @($env:Path)) -join ';') }
}

function Install-FleetUv {
    if (Test-FleetCommand 'uv') { return $true }
    # Installed by an earlier run into a directory this session's PATH lacks.
    Add-FleetUvPath
    if (Test-FleetCommand 'uv') { return $true }
    Write-Host "uv is not installed; installing it with astral's installer (no admin needed)."
    # A child PowerShell, so the installer's own `exit` cannot end this session.
    # Its output goes to the screen and never into this function's return
    # value: what a child prints would otherwise make a failed install read as
    # success and walk on into the clone.
    $shell = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    if ($env:FLEET_TEST_UV_INSTALLER) {
        & $shell -NoProfile -ExecutionPolicy Bypass -File $env:FLEET_TEST_UV_INSTALLER | Out-Host
    } else {
        & $shell -NoProfile -ExecutionPolicy Bypass -Command 'irm https://astral.sh/uv/install.ps1 | iex' | Out-Host
    }
    if ($LASTEXITCODE -ne 0) {
        Write-FleetRefusal "the uv installer failed; its error is above."
        return $false
    }
    Add-FleetUvPath
    if (-not (Test-FleetCommand 'uv')) {
        Write-FleetRefusal "uv was installed but this window cannot find it. Open a new PowerShell and run this again."
        return $false
    }
    return $true
}

function Install-FleetGit([bool]$Yes) {
    if (Test-FleetCommand 'git') { return $true }
    $line = 'winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements'
    if (-not (Test-FleetCommand 'winget')) {
        Write-FleetRefusal "git is not installed, and cloning fleet needs it. winget is not here either: install Git from https://git-scm.com/download/win, then run this again."
        return $false
    }
    Write-Host "git is not installed, and cloning fleet needs it. This installs it:"
    Write-Host "  $line"
    if (-not $Yes) {
        if ([Console]::IsInputRedirected) {
            Write-FleetRefusal "there is no terminal to ask. Run this yourself, or run the installer again with FLEET_YES=1:`n  $line"
            return $false
        }
        $answer = Read-Host 'Install git now? [y/N]'
        if ($answer -notmatch '^[yY]') {
            Write-FleetRefusal "git was not installed. Install it, then run this again."
            return $false
        }
    }
    & winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-FleetRefusal "installing git failed; its error is above."
        return $false
    }
    Update-FleetPath
    if (-not (Test-FleetCommand 'git')) {
        Write-FleetRefusal "git was installed but this window cannot find it yet. Open a new PowerShell and run this again."
        return $false
    }
    return $true
}

# The checkout a running Mission Control lead opens, or $null.
function Get-FleetLeadCheckout {
    if (-not (Test-FleetCommand 'thurbox-cli')) { return $null }
    try {
        $json = (& thurbox-cli session list --json 2>$null) -join "`n"
        $sessions = $json | ConvertFrom-Json -ErrorAction Stop
    } catch {
        return $null
    }
    foreach ($session in $sessions) {
        if ("$($session.name)" -like '* Mission Control' -and $session.cwd -and
            (Test-Path -LiteralPath (Join-Path $session.cwd 'extension.toml.in'))) {
            return [string]$session.cwd
        }
    }
    return $null
}

function Sync-FleetCheckout([string]$Dir, [string]$Repo, [string]$Branch) {
    $empty = (Test-Path -LiteralPath $Dir -PathType Container) -and
        -not (Get-ChildItem -LiteralPath $Dir -Force | Select-Object -First 1)
    if (-not (Test-Path -LiteralPath $Dir) -or $empty) {
        $parent = Split-Path -Parent $Dir
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
        & git clone --quiet --branch $Branch $Repo $Dir | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-FleetRefusal "git clone of $Repo failed; its error is above."
            return $false
        }
        Write-Host "Cloned $Repo ($Branch)."
        return $true
    }

    # --show-cdup prints nothing at a repository's top level: no path comparison
    # that a short 8.3 name or a symlinked directory would get wrong.
    $cdup = & git -C $Dir rev-parse --show-cdup 2>$null
    $isRepo = ($LASTEXITCODE -eq 0) -and -not $cdup
    if (-not (Test-Path -LiteralPath (Join-Path $Dir 'extension.toml.in')) -or
        -not (Test-Path -LiteralPath (Join-Path $Dir 'scripts\lib\queue.py')) -or -not $isRepo) {
        Write-FleetRefusal "$Dir exists and is not a fleet clone, so it was left alone.`nSet FLEET_DIR to an empty or new directory and run this again."
        return $false
    }

    $current = & git -C $Dir symbolic-ref --quiet --short HEAD 2>$null
    if ("$current" -ne $Branch) {
        $name = if ($current) { $current } else { 'a detached HEAD' }
        Write-FleetRefusal "$Dir is on '$name', not '$Branch'; not switching it.`nCheck out $Branch there yourself, then run this again."
        return $false
    }

    & git -C $Dir fetch --quiet origin $Branch | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Could not fetch origin; carrying on with the checkout as it is."
        return $true
    }
    $behind = [int](& git -C $Dir rev-list --count "HEAD..origin/$Branch")
    $ahead = [int](& git -C $Dir rev-list --count "origin/$Branch..HEAD")
    $dirty = (& git -C $Dir status --porcelain --untracked-files=no) -join "`n"

    if ($behind -gt 0 -and $ahead -gt 0) {
        Write-FleetRefusal "$Dir has diverged from origin/$Branch ($ahead ahead, $behind behind); not rebasing or resetting it.`nReconcile it by hand, then run this again."
        return $false
    }
    if ($behind -gt 0 -and $dirty) {
        Write-FleetRefusal "$Dir is $behind commit(s) behind origin/$Branch and has uncommitted changes to tracked files; not fast-forwarding over them.`nCommit or stash them, then run this again:`n$dirty"
        return $false
    }
    if ($behind -gt 0) {
        & git -C $Dir merge --ff-only --quiet "origin/$Branch" | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-FleetRefusal "the fast-forward of $Dir failed; its error is above."
            return $false
        }
        $short = & git -C $Dir rev-parse --short HEAD
        Write-Host "Fast-forwarded $behind commit(s) to $short."
    } elseif ($ahead -gt 0) {
        Write-Host "The checkout is $ahead commit(s) ahead of origin/$Branch; left as it is."
    } else {
        Write-Host "The checkout is already current with origin/$Branch."
    }
    return $true
}

# Sets $script:FleetInstallExit rather than returning it: a native command's
# output inside a function is part of what the function returns, and capturing
# it would hide the whole install from the operator.
function Install-Fleet {
    $ErrorActionPreference = 'Continue'
    $script:FleetInstallExit = 1
    $yes = ($env:FLEET_YES -eq '1')
    $dirArg = $null
    for ($i = 0; $i -lt $args.Count; $i++) {
        switch -regex ($args[$i]) {
            '^(--yes|-y)$' { $yes = $true }
            '^--dir$' {
                if ($i + 1 -ge $args.Count) { Write-FleetRefusal '--dir takes a directory'; $script:FleetInstallExit = 2; return }
                $i++; $dirArg = $args[$i]
            }
            '^--dir=' { $dirArg = $args[$i].Substring(6) }
            default { Write-FleetRefusal "unknown argument: $($args[$i])"; $script:FleetInstallExit = 2; return }
        }
    }

    $repo = if ($env:FLEET_REPO) { $env:FLEET_REPO } else { 'https://github.com/Thurbeen/fleet.git' }
    $branch = if ($env:FLEET_BRANCH) { $env:FLEET_BRANCH } else { 'main' }

    if (-not (Install-FleetUv)) { return }
    if (-not (Install-FleetGit $yes)) { return }

    if ($dirArg) {
        $dir = $dirArg; $why = '--dir'
    } elseif ($env:FLEET_DIR) {
        $dir = $env:FLEET_DIR; $why = 'FLEET_DIR'
    } else {
        $dir = Get-FleetLeadCheckout
        $why = 'the checkout your Mission Control session already opens'
        if (-not $dir) {
            $dir = Join-Path $env:USERPROFILE 'fleet'
            $why = 'the default; set FLEET_DIR to choose another'
        }
    }
    if (-not [IO.Path]::IsPathRooted($dir)) { $dir = Join-Path (Get-Location).Path $dir }
    $dir = [IO.Path]::GetFullPath($dir)

    Write-Host "fleet: installing into $dir"
    Write-Host "       ($why)"
    Write-Host ''
    if (-not (Sync-FleetCheckout $dir $repo $branch)) { return }
    Write-Host ''

    $fleetArgs = @('run', '--project', $dir, 'fleet', 'install')
    if ($yes) { $fleetArgs += '--yes' }
    & uv @fleetArgs
    $script:FleetInstallExit = $LASTEXITCODE
}

Install-Fleet @args
if ($PSCommandPath) { exit $script:FleetInstallExit }
