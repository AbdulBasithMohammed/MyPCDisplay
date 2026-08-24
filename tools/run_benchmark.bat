@echo off
REM Stops the deck, benchmarks the panel, then restarts the deck.
cd /d "%~dp0.."
net session >nul 2>&1
if %errorlevel%==0 goto run
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b
:run
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*TuringDeck*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
timeout /t 3 /nobreak >nul
"%~dp0..\venv\Scripts\python.exe" "%~dp0benchmark_panel.py" > "%~dp0..\benchmark_raw.txt" 2>&1
timeout /t 2 /nobreak >nul
start "" "%LOCALAPPDATA%\TuringDeck\python\pythonw.exe" "%LOCALAPPDATA%\TuringDeck\deck.py"
exit /b
