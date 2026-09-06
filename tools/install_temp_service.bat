@echo off
REM Registers the elevated CPU-temperature reader. Run this once.
REM It raises a UAC prompt; the deck itself stays unelevated.
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel%==0 goto run
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b
:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_temp_service.ps1" > "%~dp0..\temp_service_result.txt" 2>&1
type "%~dp0..\temp_service_result.txt"
pause
