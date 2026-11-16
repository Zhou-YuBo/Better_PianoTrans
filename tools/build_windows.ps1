param(
    [ValidateSet('Portable', 'Installer')][string]$Kind = 'Portable',
    [Parameter(Mandatory)][string]$RuntimeSource,
    [Parameter(Mandatory)][string]$AssetsRoot,
    [string]$Destination = '',
    [string]$InnoCompiler = '',
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtime = [IO.Path]::GetFullPath($RuntimeSource)
$assets = [IO.Path]::GetFullPath($AssetsRoot)
$outputs = Join-Path $project 'outputs'
if (-not $Destination) {
    $Destination = Join-Path $outputs $(if ($Kind -eq 'Portable') { 'BetterPianoTrans-portable' } else { 'BetterPianoTrans-installer' })
}
$target = [IO.Path]::GetFullPath($Destination)
if (-not $target.StartsWith($outputs.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "Destination must be inside $outputs"
}
if ((Test-Path -LiteralPath $target) -and (Get-ChildItem -LiteralPath $target -Force | Select-Object -First 1)) {
    throw "Destination must be empty: $target"
}

function Require-File([string]$path) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing file: $path" }
}
function Copy-Tree([string]$source, [string]$destination) {
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "Missing directory: $source" }
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    & robocopy $source $destination /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD __pycache__ .pytest_cache /XF *.pyc *.pyo | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copy failed ($LASTEXITCODE): $source" }
}

$python = Join-Path $runtime 'python.exe'
Require-File $python
Require-File (Join-Path $runtime 'pythonw.exe')
Require-File (Join-Path $runtime 'LICENSE.txt')
Require-File (Join-Path $runtime 'Lib\site-packages\torch\__init__.py')
Require-File (Join-Path $runtime 'Lib\site-packages\PySide6\__init__.py')
$identity = (& $python -c 'import sys,struct; print(f"{sys.version_info.major}.{sys.version_info.minor}/{struct.calcsize(chr(80))*8}")').Trim()
if ($LASTEXITCODE -ne 0 -or $identity -ne '3.11/64') { throw "Expected Python 3.11 x64; found $identity" }

$expected = Get-Content -LiteralPath (Join-Path $project 'requirements-windows-cu128.txt') |
    Where-Object { $_ -match '^[A-Za-z0-9_.-]+==' } |
    ForEach-Object { $_.ToLowerInvariant() }
$actual = & $python -m pip freeze --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect runtime packages with pip freeze' }
$actual = @($actual | ForEach-Object { $_.ToLowerInvariant() })
$missing = @($expected | Where-Object { $_ -notin $actual })
if ($missing.Count) { throw "Python dependency mismatch: $($missing -join ', ')" }

$manifest = Get-Content -LiteralPath (Join-Path $project 'release\assets.sha256')
foreach ($line in $manifest) {
    if ($line.StartsWith('#') -or -not $line.Trim()) { continue }
    if ($line -notmatch '^([a-f0-9]{64})  ([a-zA-Z0-9_./=+-]+)$') { throw "Malformed asset manifest line: $line" }
    $relative = $Matches[2].Replace('/', '\')
    $file = [IO.Path]::GetFullPath((Join-Path $assets $relative))
    if (-not $file.StartsWith($assets.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Invalid asset path: $relative" }
    Require-File $file
    $digest = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($digest -ne $Matches[1]) { throw "SHA-256 mismatch: $relative" }
}
foreach ($relative in @('src\pianotrans_gui\qml\Workbench.qml', 'third_party\byte_dance_core\pytorch\inference.py', 'LICENSE')) {
    Require-File (Join-Path $project $relative)
}
if ($Kind -eq 'Installer') {
    if (-not $InnoCompiler) { $InnoCompiler = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source }
    if (-not $InnoCompiler) { throw 'Provide -InnoCompiler pointing to Inno Setup 6 ISCC.exe' }
    Require-File $InnoCompiler
}
if ($CheckOnly) {
    Write-Output 'Preflight passed. No package was written.'
    return
}

New-Item -ItemType Directory -Path $target -Force | Out-Null
$stage = if ($Kind -eq 'Portable') { $target } else { Join-Path $target 'stage' }
New-Item -ItemType Directory -Path $stage -Force | Out-Null
Copy-Tree $runtime (Join-Path $stage 'runtime')
Copy-Tree (Join-Path $project 'src') (Join-Path $stage 'app\src')
foreach ($part in @('pytorch', 'utils')) {
    Copy-Tree (Join-Path $project "third_party\byte_dance_core\$part") (Join-Path $stage "app\third_party\byte_dance_core\$part")
}
foreach ($line in $manifest) {
    if ($line.StartsWith('#') -or -not $line.Trim()) { continue }
    $null = $line -match '^([a-f0-9]{64})  (.+)$'
    $relative = $Matches[2].Replace('/', '\')
    $destination = Join-Path (Join-Path $stage 'app') $relative
    New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $assets $relative) -Destination $destination
}
foreach ($file in Get-ChildItem -LiteralPath (Join-Path $project 'third_party') -Recurse -File) {
    if ($file.Extension -eq '.py') { continue }
    $relative = $file.FullName.Substring((Join-Path $project 'third_party').Length).TrimStart('\')
    $destination = Join-Path $stage (Join-Path 'licenses\third_party' $relative)
    New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $destination
}
Copy-Item -LiteralPath (Join-Path $project 'LICENSE') -Destination $stage
Copy-Item -LiteralPath (Join-Path $project 'release\launcher.cmd') -Destination (Join-Path $stage 'BetterPianoTrans.cmd')
Copy-Item -LiteralPath (Join-Path $project 'release\launcher-diagnostics.cmd') -Destination (Join-Path $stage 'BetterPianoTrans-Diagnostics.cmd')
if ($Kind -eq 'Portable') {
    foreach ($part in @('data', 'cache', 'temp')) { New-Item -ItemType Directory -Path (Join-Path $stage $part) | Out-Null }
    Write-Output "Portable package ready: $stage"
} else {
    $setupOutput = Join-Path $target 'setup'
    New-Item -ItemType Directory -Path $setupOutput | Out-Null
    & $InnoCompiler "/DStageDir=$stage" "/DOutputDir=$setupOutput" (Join-Path $project 'release\BetterPianoTrans.iss')
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed: $LASTEXITCODE" }
    Write-Output "Installer ready: $setupOutput"
}
