param(
    [string]$Destination = '',
    [string]$Python = 'py'
)

$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$work = Join-Path $project 'work'
if (-not $Destination) { $Destination = Join-Path $work 'runtime' }
$target = [IO.Path]::GetFullPath($Destination)
if (-not $target.StartsWith($work.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "Destination must be inside $work"
}
if ((Test-Path -LiteralPath $target) -and (Get-ChildItem -LiteralPath $target -Force | Select-Object -First 1)) {
    throw "Destination must be empty: $target"
}

function Copy-Tree([string]$source, [string]$destination, [string[]]$exclude = @()) {
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "Missing directory: $source" }
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    $argsList = @($source, $destination, '/E', '/R:1', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/XD', '__pycache__')
    if ($exclude.Count) { $argsList += $exclude }
    $argsList += @('/XF', '*.pyc', '*.pyo')
    & robocopy @argsList | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copy failed ($LASTEXITCODE): $source" }
}

New-Item -ItemType Directory -Path $work -Force | Out-Null
$venv = Join-Path $work 'build-venv'
if (Test-Path -LiteralPath $venv) { throw "Build environment already exists: $venv" }
$env:TEMP = Join-Path $project '.codex-tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $project '.codex-cache\pip'
New-Item -ItemType Directory -Path $env:TEMP, $env:PIP_CACHE_DIR -Force | Out-Null
if ($Python -eq 'py') { & py -3.11 -m venv $venv } else { & $Python -m venv $venv }
if ($LASTEXITCODE -ne 0) { throw 'Could not create Python 3.11 environment' }
$venvPython = Join-Path $venv 'Scripts\python.exe'
$identity = (& $venvPython -c 'import sys,struct; print(f"{sys.version_info.major}.{sys.version_info.minor}/{struct.calcsize(chr(80))*8}")').Trim()
if ($identity -ne '3.11/64') { throw "Expected Python 3.11 x64; found $identity" }
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $project 'requirements-windows-cu128.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
$base = (& $venvPython -c 'import sys; print(sys.base_prefix)').Trim()
New-Item -ItemType Directory -Path $target -Force | Out-Null
foreach ($name in @('python.exe', 'pythonw.exe', 'python3.dll', 'python311.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt')) {
    $source = Join-Path $base $name
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing Python runtime file: $source" }
    Copy-Item -LiteralPath $source -Destination $target
}
Copy-Tree (Join-Path $base 'Lib') (Join-Path $target 'Lib') @('site-packages', 'test', 'tests', 'idlelib', 'ensurepip', 'lib2to3')
Copy-Tree (Join-Path $base 'DLLs') (Join-Path $target 'DLLs')
Copy-Tree (Join-Path $base 'tcl') (Join-Path $target 'tcl')
Copy-Tree (Join-Path $venv 'Lib\site-packages') (Join-Path $target 'Lib\site-packages')
Write-Output "Windows runtime ready: $target"
