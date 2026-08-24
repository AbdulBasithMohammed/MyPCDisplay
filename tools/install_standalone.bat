@echo off
REM Installs Turing Deck as a self-contained app (own Python) in %LOCALAPPDATA%.
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel%==0 goto run
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b
:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_standalone.ps1" > "%~dp0..\standalone_result.txt" 2>&1
exit /b
