# Shared helpers for the project scripts. Dot-source it: . "$PSScriptRoot\_common.ps1"
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:VenvDir = Join-Path $RepoRoot '.venv'
$script:VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$script:ToolsDir = Join-Path $RepoRoot '.tools'
$script:UvExe = Join-Path $ToolsDir 'uv\Scripts\uv.exe'

function Get-TrainerDataDir {
    if ($env:TRAINER_DATA_DIR) { return $env:TRAINER_DATA_DIR }
    return (Join-Path $env:LOCALAPPDATA 'EnglishTrainer')
}

function Get-TrainerModelsDir {
    if ($env:TRAINER_MODELS_DIR) { return $env:TRAINER_MODELS_DIR }
    return (Join-Path (Get-TrainerDataDir) 'models')
}

function Set-TrainerEnv {
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:HF_HOME = Get-TrainerModelsDir
    $env:HF_HUB_DISABLE_TELEMETRY = '1'
    $env:UV_PROJECT_ENVIRONMENT = $VenvDir
    $env:UV_PYTHON_DOWNLOADS = 'never'
    $env:UV_NO_PROGRESS = '1'
}

function Invoke-Checked {
    # Runs a native command and throws if it exits with a non-zero code.
    param(
        [Parameter(Mandatory)][string]$Exe,
        [Parameter(ValueFromRemainingArguments)][string[]]$Arguments
    )
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Exe $($Arguments -join ' ')"
    }
}

function Assert-Venv {
    if (-not (Test-Path $VenvPython)) {
        throw "Python environment not found at $VenvDir. Run scripts\setup.ps1 first."
    }
}
