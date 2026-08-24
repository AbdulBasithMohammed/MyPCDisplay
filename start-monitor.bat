@echo off
REM Turing smart screen system monitor - DEBUG launcher (visible console).
REM For everyday use run start-deck.bat instead: tray icon, hotkeys, no window.
REM
REM Runs elevated. Sensor data comes from psutil (CPU load) and NVML (NVIDIA
REM GPU), neither of which needs admin - but CPU temperature is read via AMD's
REM Ryzen Master SDK CLI, which refuses to run without it. LibreHardwareMonitor
REM cannot be used at all here: it needs WinRing0, which Memory Integrity blocks.
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel%==0 goto run
echo Requesting administrator rights (needed for CPU temperature)...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b
:run
echo Running as administrator. Close this window or press Ctrl+C to stop.
"%~dp0venv\Scripts\python.exe" "%~dp0main.py"
pause
