# Installs the built Turing Deck app to %LOCALAPPDATA%\TuringDeck, points the
# logon task at it, and starts it. Run elevated (the task needs admin to write,
# and the running deck runs elevated so it needs admin to stop).
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$src  = Join-Path $repo "dist\TuringDeck"
$dest = Join-Path $env:LOCALAPPDATA "TuringDeck"
$name = "Turing Deck"

if (-not (Test-Path (Join-Path $src "TuringDeck.exe"))) { throw "build not found at $src" }

# 1. stop anything currently running, from repo or install dir
$targets = Get-CimInstance Win32_Process |
  Where-Object { $_.Name -in @('pythonw.exe','python.exe','TuringDeck.exe','TuringDisplay.exe') -and
                 $_.CommandLine -and (($_.CommandLine -like "*$repo*") -or ($_.CommandLine -like "*$dest*")) }
foreach ($p in $targets) {
    "stopping pid $($p.ProcessId)  $($p.Name)"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

# 2. copy the build, preserving an existing config if we are upgrading
$keep = @{}
foreach ($f in @("config.yaml","deck.yaml")) {
    $p = Join-Path $dest $f
    if (Test-Path $p) { $keep[$f] = Get-Content $p -Raw }
}
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
Copy-Item $src $dest -Recurse -Force
foreach ($f in $keep.Keys) {
    Set-Content -Path (Join-Path $dest $f) -Value $keep[$f] -Encoding utf8 -NoNewline
    "preserved existing $f"
}

# 3. repoint the logon task at the executable
$exe = Join-Path $dest "TuringDeck.exe"
$action  = New-ScheduledTaskAction -Execute $exe -WorkingDirectory $dest
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT15S"
$principal = New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\$env:USERNAME" `
    -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$settings.DisallowStartIfOnBatteries = $false
$settings.StopIfGoingOnBatteries = $false
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
}
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description "Turing smart screen deck (standalone app)." | Out-Null

# 4. start it
Start-Process -FilePath $exe -WorkingDirectory $dest
Start-Sleep -Seconds 6

"installed to : $dest"
"size         : {0:N0} MB" -f ((Get-ChildItem $dest -Recurse | Measure-Object Length -Sum).Sum / 1MB)
"task action  : $((Get-ScheduledTask -TaskName $name).Actions[0].Execute)"
"running      :"
Get-Process TuringDeck,TuringDisplay -ErrorAction SilentlyContinue |
  ForEach-Object { "  {0,-16} pid {1,-7} {2,6:N1} MB" -f $_.ProcessName, $_.Id, ($_.WorkingSet64/1MB) }
