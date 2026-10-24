# Execute only the installer's pure path function; never its installation body.
param([string] $Installer, [string] $Root)
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Installer, [ref] $tokens, [ref] $errors)
if ($errors.Count) { throw 'installer does not parse' }
$function = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-FleetInstallDir' }, $true)
if (-not $function) { throw 'missing pure install path policy' }
. ([scriptblock]::Create($function.Extent.Text))
$store = [IO.Path]::Combine($Root, 'fleet')
$install = Get-FleetInstallDir '' $Root
$separator = [IO.Path]::DirectorySeparatorChar
if ($install -eq $store -or $install.StartsWith($store + $separator) -or $store.StartsWith($install + $separator)) {
    throw 'default install and store directories nest or collide'
}
$override = [IO.Path]::Combine($Root, 'custom')
if ((Get-FleetInstallDir $override $Root) -ne $override) { throw 'explicit destination lost' }
if (Test-Path -LiteralPath $install) { throw 'pure path resolution created a directory' }
Write-Output 'install path policy: disjoint, explicit override preserved, no filesystem effects'
