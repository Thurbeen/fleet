# Run a downloaded Windows installer without putting `irm ... | iex` on a
# child PowerShell command line. The caller owns the URL; this file leaves no
# downloaded script behind, including when the download or installer fails.
param([Parameter(Mandatory = $true)][string]$Uri)

$download = Join-Path ([IO.Path]::GetTempPath()) ("fleet-installer-$([guid]::NewGuid().ToString('N')).ps1")
$code = 1
try {
    Invoke-RestMethod -Uri $Uri -OutFile $download -ErrorAction Stop
    $shell = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    & $shell -NoProfile -ExecutionPolicy Bypass -File $download
    $code = $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine("fleet install: downloading the installer failed: $_")
} finally {
    Remove-Item -LiteralPath $download -ErrorAction SilentlyContinue
}
exit $code
