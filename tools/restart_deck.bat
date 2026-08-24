@echo off
REM Stops the Turing Deck and starts it again. Self-elevates, because the deck
REM runs elevated so the CPU temperature sensor can reach AMD's SDK CLI.
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel%==0 goto run
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b
:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_deck.ps1" > "%~dp0..\restart_result.txt" 2>&1
exit /b
