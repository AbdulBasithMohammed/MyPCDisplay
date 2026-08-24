@echo off
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel%==0 goto run
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b
:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_app.ps1" > "%~dp0..\install_result.txt" 2>&1
exit /b
