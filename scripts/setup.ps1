# Prepares everything the app needs: Python environment, dependencies, models.
# Safe to run again: every step checks what is already in place.
param(
    [switch]$SkipModels
)
. "$PSScriptRoot\_common.ps1"
Set-TrainerEnv

$UvVersion = '0.13.7'

function Write-Step([string]$Text) { Write-Host "--- $Text" -ForegroundColor Cyan }

# 1. Python 3.12 through the py launcher.
Write-Step 'Python 3.12'
$basePython = $null
try { $basePython = (& py -3.12 -c 'import sys; print(sys.executable)' 2>$null) } catch { $basePython = $null }
if (-not $basePython -or $LASTEXITCODE -ne 0) {
    throw 'Python 3.12 not found. Install it from python.org (per-user install, no admin rights needed) and run setup again.'
}
$basePython = $basePython.Trim()
Write-Host "using $basePython"

# 2. uv in a private tools environment inside the project (no global installs).
Write-Step "uv $UvVersion"
$uvVenv = Join-Path $ToolsDir 'uv'
if (-not (Test-Path (Join-Path $uvVenv 'Scripts\python.exe'))) {
    Invoke-Checked $basePython -m venv $uvVenv
}
$haveUv = $null
if (Test-Path $UvExe) { $haveUv = ((& $UvExe --version) -split ' ')[1] }
if ($haveUv -ne $UvVersion) {
    Invoke-Checked (Join-Path $uvVenv 'Scripts\python.exe') -m pip install --disable-pip-version-check --quiet "uv==$UvVersion"
}
Invoke-Checked $UvExe --version

# 3. Project environment from the lock file.
Write-Step 'dependencies (uv sync --locked)'
Push-Location $RepoRoot
try {
    if (Test-Path (Join-Path $RepoRoot 'uv.lock')) {
        Invoke-Checked $UvExe sync --locked --python $basePython
    } else {
        Write-Host 'uv.lock not found: resolving dependencies for the first time'
        Invoke-Checked $UvExe lock --python $basePython
        Invoke-Checked $UvExe sync --locked --python $basePython
    }
} finally {
    Pop-Location
}

# 4. Models into the application data directory.
if ($SkipModels) {
    Write-Step 'models: skipped (-SkipModels)'
} elseif (Test-Path (Join-Path $RepoRoot 'trainer\setup_models.py')) {
    Write-Step "models -> $(Get-TrainerModelsDir)"
    Push-Location $RepoRoot
    try { Invoke-Checked $VenvPython -m trainer.setup_models } finally { Pop-Location }
}

Write-Host 'setup: OK' -ForegroundColor Green
exit 0
