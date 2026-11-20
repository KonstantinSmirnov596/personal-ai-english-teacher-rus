@echo off
rem Personal AI English Teacher RUS uninstaller: removes the Python environment, downloaded tools and shortcuts.
rem Your data and API keys are deleted only after you confirm.
setlocal
cd /d "%~dp0"
rem Started from a PowerShell 7 terminal, Windows PowerShell would inherit its module path.
set "PSModulePath="
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\uninstall.ps1" %*
set "code=%errorlevel%"
echo.
pause
exit /b %code%
