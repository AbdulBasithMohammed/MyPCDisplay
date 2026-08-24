@echo off
REM Turing Deck - background controller (tray icon + hotkeys, no console).
REM Elevates because the CPU temperature sensor shells out to AMD's Ryzen
REM Master SDK CLI, which refuses to run without administrator rights.
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel%==0 goto run
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%~f0' -Verb RunAs -WindowStyle Hidden"
exit /b
:run
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0deck.py"
exit /b
