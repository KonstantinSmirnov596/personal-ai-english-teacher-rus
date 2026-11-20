# Installs or updates the trainer (Personal AI English Teacher RUS). Started by install.cmd; no
# system Python and no administrator rights needed. Safe to run again: every step checks what is
# in place.
#
#   1. requirements and folders, before anything is downloaded
#   2. uv from its GitHub release (SHA-256 pinned in installer.ps1)
#   3. Python 3.12 through uv, into .tools\python (uv checks its SHA-256)
#   4. packages from uv.lock into .venv (uv checks every hash in the lock file)
#   5. models by pinned commit (trainer/model_registry.py); an interrupted download continues
#   6. fonts and the voice detector by sha512
#   7. shortcuts, then the app starts
#
# Options:
#   -NoHardwareCheck  developer machines without an NVIDIA GPU (CI): only the Python environment
#                     is needed there; the app checks the hardware at every start anyway
#   -SkipModels       do not download models
#   -Shortcuts ask|yes|no  Desktop and Start menu shortcuts (ask: a question in the console)
#   -NoLaunch         do not start the app at the end
#   -Dev              also install the developer tools (pytest, ruff, Playwright) for the tests
param(
    [switch]$SkipModels,
    [switch]$NoHardwareCheck,
    [ValidateSet('ask', 'yes', 'no')][string]$Shortcuts = 'ask',
    [switch]$NoLaunch,
    [switch]$Dev
)
. "$PSScriptRoot\_common.ps1"
. "$PSScriptRoot\requirements.ps1"
. "$PSScriptRoot\installer.ps1"
Set-TrainerEnv
# Russian messages stay intact when the output is redirected to a log.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Lang = Get-TrLanguage
$Msg = (Get-TrMessages).$Lang
function Say([string]$Key, [hashtable]$Values = @{}) { return (Format-TrText ([string]$Msg.$Key) $Values) }
function Write-Step([string]$Text) { Write-Host "--- $Text" -ForegroundColor Cyan }

function Stop-Setup([string]$Reason) {
    Write-Host ''
    Write-Host (Say 'failed' @{ reason = $Reason }) -ForegroundColor Red
    Write-Host (Say 'failed_hint')
    exit 1
}

try {
    # 1. Requirements and folders, before any download.
    if ($NoHardwareCheck) {
        Write-Host 'requirements check skipped (-NoHardwareCheck)' -ForegroundColor Yellow
    } elseif (-not (Invoke-TrRequirements -RepoRoot $RepoRoot -DataDir (Get-TrainerDataDir))) {
        exit 1
    }
    Write-Step (Say 'step_path')
    $pathProblems = @(Test-TrInstallPath -AppDir $RepoRoot -DataDir (Get-TrainerDataDir))
    if ($pathProblems.Count -gt 0) {
        foreach ($p in $pathProblems) { Write-Host ('  - ' + (Say $p.Id @{ path = $p.Path })) -ForegroundColor Red }
        exit 1
    }

    # 2. uv.
    Write-Step (Say 'step_uv' @{ version = $script:TrUvVersion })
    $uv = Install-TrUv -ToolsDir $ToolsDir
    Invoke-Checked $uv --version

    # 3. Python, private to the app: no PATH entries, no registry entries.
    Write-Step (Say 'step_python' @{ version = $script:TrPythonVersion })
    $env:UV_PYTHON_DOWNLOADS = 'manual'
    Invoke-Checked $uv python install $script:TrPythonVersion --install-dir $PythonDir --no-bin --no-registry
    $python = (& $uv python find $script:TrPythonVersion --managed-python | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or -not $python) { throw 'Python was installed but uv cannot find it' }
    $env:UV_PYTHON_DOWNLOADS = 'never'
    Write-Host "  $($python.Trim())"

    # 4. Packages from the lock file.
    Write-Step (Say 'step_packages')
    Push-Location $RepoRoot
    try {
        # Users get the app's packages only; developers add the test tools with -Dev.
        if ($Dev) {
            Invoke-Checked $uv sync --locked --python $python.Trim()
        } else {
            Invoke-Checked $uv sync --locked --no-dev --python $python.Trim()
        }
    } finally {
        Pop-Location
    }

    # 5. Models.
    if ($SkipModels) {
        Write-Host 'models skipped (-SkipModels)' -ForegroundColor Yellow
    } else {
        Write-Step (Say 'step_models')
        Push-Location $RepoRoot
        try {
            & $VenvPython -m trainer.setup_models
            if ($LASTEXITCODE -ne 0) { throw "model download failed (exit code $LASTEXITCODE)" }
        } finally { Pop-Location }
    }

    # 6. Window assets.
    Write-Step (Say 'step_assets')
    Push-Location $RepoRoot
    try {
        Invoke-Checked $VenvPython -m trainer.setup_fonts
        Invoke-Checked $VenvPython -m trainer.setup_vendor
    } finally {
        Pop-Location
    }

    # 7. Shortcuts: always in the app folder; Desktop and Start menu on request.
    Write-Step (Say 'step_shortcuts')
    $wantLinks = $false
    if ($Shortcuts -eq 'yes') { $wantLinks = $true }
    elseif ($Shortcuts -eq 'ask') { $wantLinks = Read-TrYesNo -Question (Say 'shortcuts_question') -YesLetters ([string]$Msg.yes_letters) }
    if ($wantLinks) {
        & (Join-Path $PSScriptRoot 'make_shortcut.ps1') -Desktop -StartMenu
    } else {
        & (Join-Path $PSScriptRoot 'make_shortcut.ps1')
    }
    if ($LASTEXITCODE -ne 0) { throw 'shortcut creation failed' }
} catch {
    $text = $_.Exception.Message
    if ($text -like 'checksum:*') { Stop-Setup (Say 'failed_checksum' @{ what = $text.Substring(9) }) }
    Stop-Setup $text
}

Write-Host (Say 'done') -ForegroundColor Green
if (-not $NoLaunch) {
    Write-Host (Say 'launching')
    $pythonw = Join-Path $VenvDir 'Scripts\pythonw.exe'
    Start-Process -FilePath $pythonw -ArgumentList '-m', 'trainer.launcher' -WorkingDirectory $RepoRoot
}
exit 0
