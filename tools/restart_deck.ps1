# Stops the Turing Deck (controller + display child) and starts it again.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# Only kill processes whose command line actually points at this repo, so
# unrelated Python programs are left alone.
$procs = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
         Where-Object { $_.CommandLine -and ($_.CommandLine -like "*$root*") -and
                        (($_.CommandLine -like "*deck.py*") -or ($_.CommandLine -like "*main.py*")) }
foreach ($p in $procs) {
    "stopping pid $($p.ProcessId): $($p.CommandLine)"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
Start-Process -FilePath (Join-Path $root "venv\Scripts\pythonw.exe") `
              -ArgumentList "`"$(Join-Path $root 'deck.py')`"" -WorkingDirectory $root
Start-Sleep -Seconds 4
"running now:"
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
  Where-Object { $_.CommandLine -like "*$root*" } |
  ForEach-Object { "  pid $($_.ProcessId)" }
