# Hardware and system requirements, checked by setup.ps1 before anything is downloaded.
#
# Get-TrFacts reads this computer; Test-TrRequirements only looks at the facts it is given, so
# tests feed it fake facts (tests/test_requirements_ps1.py). The driver and architecture
# thresholds come from the PyTorch CUDA 12.8 build (driver R570 or newer, compute capability 7.5
# or newer), the same stack as the ES/PT version of the app.
# Messages (Russian) are in requirements.messages.json.
#
# Runs on Windows PowerShell 5.1 (what install.cmd uses) and PowerShell 7. Keep it ASCII.
#
#   . scripts\requirements.ps1
#   if (-not (Invoke-TrRequirements -RepoRoot $RepoRoot)) { exit 1 }

$script:TrMinDriver = [version]'572.61'
$script:TrMinComputeCap = [version]'7.5'
$script:TrMinVramMb = 5800        # a 6 GB card reports about 6140 MiB
# All three models loaded take about 3.6 GB (docs/reports/data/bench_stage0.json); the app has no
# reduced mode and was tested on an 8 GB card, so a smaller card gets a warning.
$script:TrTestedVramMb = 7600     # an 8 GB card reports about 8150 MiB
$script:TrMinRamMb = 15000        # 16 GB installed shows about 15.7 GB to Windows
$script:TrMinWindowsBuild = 19045 # Windows 10 22H2
# Disk space for a first installation (measured for the same packages and models, rounded up):
# Python environment 4.9 GB and package cache 4.9 GB in the app folder; models 3.1 GB and 2 GB for
# recordings, logs and the database in the user's local application data.
$script:TrNeedAppMb = 10000
$script:TrNeedDataMb = 5500

function Get-TrLanguage {
    # The installer speaks Russian, like the app.
    return 'ru'
}

# "GB" in Russian, from character codes (this file stays ASCII).
$script:TrGb = -join [char[]](0x0413, 0x0411)

function Format-TrSize([double]$Mb) {
    $ru = [System.Globalization.CultureInfo]::GetCultureInfo('ru-RU')
    return (($Mb / 1024).ToString('N1', $ru) + ' ' + $script:TrGb)
}

function Format-TrGb([int]$Gb) {
    return ("$Gb " + $script:TrGb)
}

function Get-TrNvidiaSmi {
    # First NVIDIA GPU as nvidia-smi reports it (the app uses the same one), or $null.
    $exe = $null
    $cmd = Get-Command 'nvidia-smi.exe' -ErrorAction SilentlyContinue
    if ($cmd) { $exe = $cmd.Source }
    if (-not $exe) {
        $candidate = Join-Path $env:SystemRoot 'System32\nvidia-smi.exe'
        if (Test-Path $candidate) { $exe = $candidate }
    }
    if (-not $exe) { return $null }
    try {
        $out = & $exe '--query-gpu=name,driver_version,compute_cap,memory.total' '--format=csv,noheader,nounits' 2>$null
    } catch {
        return $null
    }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    foreach ($line in @($out)) {
        $parts = @($line -split ',' | ForEach-Object { $_.Trim() })
        if ($parts.Count -ne 4) { continue }
        $vram = 0
        if (-not [int]::TryParse(($parts[3] -split '\.')[0], [ref]$vram)) { continue }
        return [pscustomobject]@{
            Name        = $parts[0]
            Driver      = $parts[1]
            ComputeCap  = $parts[2]
            VramTotalMb = $vram
        }
    }
    return $null
}

function Get-TrDriveFreeMb([string]$Path) {
    $root = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Path))
    $drive = New-Object System.IO.DriveInfo($root)
    return [pscustomobject]@{ Drive = $root.TrimEnd('\'); FreeMb = [int64]($drive.AvailableFreeSpace / 1MB) }
}

function Get-TrFacts {
    # Everything Test-TrRequirements needs, read from this computer.
    param(
        [Parameter(Mandatory)][string]$AppDir,
        [Parameter(Mandatory)][string]$DataDir
    )
    $os = Get-CimInstance Win32_OperatingSystem
    $adapters = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name } | Where-Object { $_ })

    # Space already taken by an earlier installation is not needed again.
    $needApp = $script:TrNeedAppMb
    if (Test-Path (Join-Path $AppDir '.venv\Scripts\python.exe')) { $needApp = 500 }
    $needData = $script:TrNeedDataMb
    if (Test-Path (Join-Path $DataDir 'models')) { $needData = 2048 }

    $disks = @()
    $app = Get-TrDriveFreeMb $AppDir
    $data = Get-TrDriveFreeMb (Split-Path $DataDir -Parent)
    if ($app.Drive -eq $data.Drive) {
        $disks += [pscustomobject]@{ Drive = $app.Drive; FreeMb = $app.FreeMb; NeedMb = $needApp + $needData }
    } else {
        $disks += [pscustomobject]@{ Drive = $app.Drive; FreeMb = $app.FreeMb; NeedMb = $needApp }
        $disks += [pscustomobject]@{ Drive = $data.Drive; FreeMb = $data.FreeMb; NeedMb = $needData }
    }

    return [pscustomobject]@{
        WindowsBuild = [int]$os.BuildNumber
        RamTotalMb   = [int64]([int64]$os.TotalVisibleMemorySize / 1024)
        Adapters     = $adapters
        Nvidia       = Get-TrNvidiaSmi
        Disks        = $disks
    }
}

function New-TrCheck([string]$Id, [bool]$Ok, [bool]$Blocking, [string]$Value, [string]$Required, [string]$Name, [string]$Message) {
    if (-not $Message) { $Message = $Id }
    return [pscustomobject]@{
        Id = $Id; Ok = $Ok; Blocking = $Blocking; Value = $Value; Required = $Required
        Name = $Name; Message = $Message
    }
}

function ConvertTo-TrVersion([string]$Text) {
    $v = $null
    if ([version]::TryParse(($Text -replace '[^0-9.]', ''), [ref]$v)) { return $v }
    return $null
}

function Test-TrRequirements {
    # One check per requirement; Blocking checks stop the installation.
    param([Parameter(Mandatory)]$Facts)
    $checks = @()
    $nv = $Facts.Nvidia
    $nvidiaAdapters = @($Facts.Adapters | Where-Object { $_ -match 'NVIDIA' })

    if ($null -eq $nv) {
        if ($nvidiaAdapters.Count -gt 0) {
            # An NVIDIA card is there, but nvidia-smi (installed with the driver) is not.
            $checks += New-TrCheck 'gpu' $true $true $nvidiaAdapters[0] 'NVIDIA GPU' $nvidiaAdapters[0]
            $checks += New-TrCheck 'driver' $false $true 'none' $script:TrMinDriver.ToString() $nvidiaAdapters[0] 'driver_missing'
        } else {
            $found = 'none'
            if (@($Facts.Adapters).Count -gt 0) { $found = (@($Facts.Adapters) -join ', ') }
            $checks += New-TrCheck 'gpu' $false $true $found 'NVIDIA GPU' ''
        }
    } else {
        $checks += New-TrCheck 'gpu' $true $true $nv.Name 'NVIDIA GPU' $nv.Name
        $driver = ConvertTo-TrVersion $nv.Driver
        $driverOk = ($null -ne $driver) -and ($driver -ge $script:TrMinDriver)
        $checks += New-TrCheck 'driver' $driverOk $true $nv.Driver $script:TrMinDriver.ToString() $nv.Name
        $cc = ConvertTo-TrVersion $nv.ComputeCap
        $ccOk = ($null -ne $cc) -and ($cc -ge $script:TrMinComputeCap)
        $checks += New-TrCheck 'architecture' $ccOk $true $nv.ComputeCap $script:TrMinComputeCap.ToString() $nv.Name
        $vramOk = [int64]$nv.VramTotalMb -ge $script:TrMinVramMb
        $checks += New-TrCheck 'vram' $vramOk $true (Format-TrSize $nv.VramTotalMb) (Format-TrGb 6) $nv.Name
        if ($vramOk -and [int64]$nv.VramTotalMb -lt $script:TrTestedVramMb) {
            $checks += New-TrCheck 'vram_untested' $false $false (Format-TrSize $nv.VramTotalMb) (Format-TrGb 8) $nv.Name
        }
    }

    foreach ($disk in @($Facts.Disks)) {
        $ok = [int64]$disk.FreeMb -ge [int64]$disk.NeedMb
        $checks += New-TrCheck 'disk' $ok $true (Format-TrSize $disk.FreeMb) (Format-TrSize $disk.NeedMb) $disk.Drive
    }
    $ramOk = [int64]$Facts.RamTotalMb -ge $script:TrMinRamMb
    $checks += New-TrCheck 'ram' $ramOk $false (Format-TrSize $Facts.RamTotalMb) (Format-TrGb 16) ''
    if ([int]$Facts.WindowsBuild -gt 0) {
        $winOk = [int]$Facts.WindowsBuild -ge $script:TrMinWindowsBuild
        $checks += New-TrCheck 'windows' $winOk $false ([string]$Facts.WindowsBuild) "$($script:TrMinWindowsBuild) (Windows 10 22H2)" ''
    }
    return $checks
}

function Get-TrMessages([string]$Root = $PSScriptRoot) {
    $path = Join-Path $Root 'requirements.messages.json'
    return (Get-Content -Raw -Encoding UTF8 -Path $path | ConvertFrom-Json)
}

function Format-TrMessage {
    param([Parameter(Mandatory)]$Check, [Parameter(Mandatory)]$Messages, [string]$Language = 'ru')
    $table = $Messages.$Language
    if ($null -eq $table) { $table = $Messages.ru }
    $text = [string]$table.($Check.Message)
    $text = $text.Replace('{value}', [string]$Check.Value).Replace('{required}', [string]$Check.Required)
    return $text.Replace('{name}', [string]$Check.Name)
}

function Invoke-TrRequirements {
    # Prints the result in the user's language; returns $true when nothing blocks.
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [string]$DataDir = (Join-Path $env:LOCALAPPDATA 'EnglishTrainer'),
        [string]$Language = (Get-TrLanguage),
        $Facts = $null
    )
    if ($null -eq $Facts) { $Facts = Get-TrFacts -AppDir $RepoRoot -DataDir $DataDir }
    $messages = Get-TrMessages
    $table = $messages.$Language
    $checks = @(Test-TrRequirements -Facts $Facts)
    $blocking = @($checks | Where-Object { -not $_.Ok -and $_.Blocking })
    $warnings = @($checks | Where-Object { -not $_.Ok -and -not $_.Blocking })

    Write-Host "--- $($table.title)" -ForegroundColor Cyan
    foreach ($c in $checks) {
        $mark = 'ok  '
        if (-not $c.Ok) { $mark = 'FAIL'; if (-not $c.Blocking) { $mark = 'warn' } }
        Write-Host ("  [{0}] {1}: {2}" -f $mark, $c.Id, $c.Value)
    }
    if ($warnings.Count -gt 0 -and $blocking.Count -eq 0) {
        Write-Host $table.warned -ForegroundColor Yellow
    }
    foreach ($c in @($blocking + $warnings)) {
        $color = 'Yellow'
        if ($c.Blocking) { $color = 'Red' }
        Write-Host ('  - ' + (Format-TrMessage -Check $c -Messages $messages -Language $Language)) -ForegroundColor $color
    }
    if (($blocking.Count + $warnings.Count) -gt 0) {
        Write-Host $table.doc.Replace('{doc}', (Join-Path $RepoRoot 'README.md'))
    }
    if ($blocking.Count -gt 0) {
        Write-Host $table.blocked -ForegroundColor Red
        return $false
    }
    if ($warnings.Count -eq 0) { Write-Host $table.ok -ForegroundColor Green }
    return $true
}
