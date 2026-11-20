@echo off
rem Personal AI English Teacher RUS installer: double-click. No administrator rights and no Python needed.
rem Runs scripts\setup.ps1 with Windows PowerShell; safe to run again (it continues where it stopped).
setlocal
cd /d "%~dp0"
rem Started from a PowerShell 7 terminal, Windows PowerShell would inherit its module path.
set "PSModulePath="
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
set "code=%errorlevel%"
if not "%code%"=="0" (
    echo.
    pause
)
exit /b %code%
