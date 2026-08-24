@echo off
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel%==0 goto run
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b
:run
"%~dp0venv\Scripts\python.exe" "%~dp0diag_lhm.py"
