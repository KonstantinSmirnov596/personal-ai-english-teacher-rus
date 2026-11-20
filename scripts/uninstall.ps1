# Removes the trainer (Personal AI English Teacher RUS) from this computer. Started by uninstall.cmd.
#
#   - asks first; closes a running trainer
#   - removes the shortcuts that point to this app, .venv, .tools (uv, Python, package cache),
#     and the downloaded window assets (web\fonts, web\vendor)
#   - asks separately before deleting the data folder (%LOCALAPPDATA%\EnglishTrainer) and the API
#     keys in the Windows Credential Manager; without a yes they stay
#   - the app folder itself (source files) is left for the user to delete
#
# Options for unattended runs: -Yes (remove the app), -DeleteData (also data and keys).
param(
    [switch]$Yes,
    [switch]$DeleteData
)
. "$PSScriptRoot\_common.ps1"
. "$PSScriptRoot\requirements.ps1"
. "$PSScriptRoot\installer.ps1"
Set-TrainerEnv
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Lang = Get-TrLanguage
$Msg = (Get-TrMessages).$Lang
function Say([string]$Key, [hashtable]$Values = @{}) { return (Format-TrText ([string]$Msg.$Key) $Values) }

$dataDir = Get-TrainerDataDir
$remove = $Yes -or (Read-TrYesNo -Question (Say 'uninstall_question') -YesLetters ([string]$Msg.yes_letters) -Default $false)
if (-not $remove) {
    Write-Host (Say 'uninstall_cancelled')
    exit 0
}
$deleteData = $DeleteData -or ((-not $Yes) -and (Read-TrYesNo -Question (Say 'uninstall_data_question' @{ path = $dataDir }) -YesLetters ([string]$Msg.yes_letters) -Default $false))

# A running trainer keeps files open. The venv's pythonw.exe is a launcher that starts the
# interpreter from .tools\python, so match any Python process from this app folder.
$appPrefix = $RepoRoot.TrimEnd([char]92) + [char]92
$running = @(Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($appPrefix, [StringComparison]::OrdinalIgnoreCase) })
if ($running.Count -gt 0) {
    Write-Host (Say 'uninstall_running')
    foreach ($p in $running) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

# Keys first: deleting them needs the app's Python environment.
if ($deleteData -and (Test-Path $VenvPython)) {
    Push-Location $RepoRoot
    try { & $VenvPython -m trainer.uninstall_keys } finally { Pop-Location }
}

# Shortcuts that point to this app.
. "$PSScriptRoot\shortcut.ps1"
foreach ($link in @((Get-TrShortcutPaths) + (Join-Path $RepoRoot "$(Get-TrShortcutName).lnk"))) {
    if (Test-Path $link) {
        $target = Get-TrShortcutTarget $link
        if ($target -and $target.StartsWith($VenvDir, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -Force $link
        }
    }
}

foreach ($dir in @($VenvDir, $ToolsDir, (Join-Path $RepoRoot 'web\fonts'), (Join-Path $RepoRoot 'web\vendor'))) {
    if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
}

if ($deleteData) {
    if (Test-Path $dataDir) { Remove-Item -Recurse -Force $dataDir }
} else {
    Write-Host (Say 'uninstall_kept' @{ path = $dataDir })
}
Write-Host (Say 'uninstall_done') -ForegroundColor Green
exit 0
