# Runs a test suite: fast | gpu | e2e | live | all.
# Extra arguments are passed to pytest, e.g.: scripts\test.ps1 fast -k storage
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('fast', 'gpu', 'e2e', 'live', 'all')]
    [string]$Suite,
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$PytestArgs
)
. "$PSScriptRoot\_common.ps1"
Assert-Venv
Set-TrainerEnv

$markers = @{
    fast = 'not gpu and not e2e and not live'
    gpu  = 'gpu'
    e2e  = 'e2e'
    live = 'live'
}

function Invoke-Suite([string]$Name) {
    Write-Host "=== suite: $Name ===" -ForegroundColor Cyan
    $pyArgs = @('-m', 'pytest', '-m', $markers[$Name]) + @($PytestArgs | Where-Object { $_ })
    Push-Location $RepoRoot
    try {
        # Out-Host keeps pytest output out of the function's return value.
        & $VenvPython @pyArgs | Out-Host
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($code -eq 5) {
        Write-Host "suite ${Name}: no tests collected" -ForegroundColor Yellow
        return 0
    }
    return $code
}

$suites = if ($Suite -eq 'all') { @('fast', 'gpu', 'e2e', 'live') } else { @($Suite) }
$failed = @()
foreach ($s in $suites) {
    $code = Invoke-Suite $s
    if ($code -ne 0) { $failed += "$s (exit $code)" }
}

if ($failed.Count -gt 0) {
    Write-Host "FAILED: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "OK: $($suites -join ', ')" -ForegroundColor Green
exit 0
