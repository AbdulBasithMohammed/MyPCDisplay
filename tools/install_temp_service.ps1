# Registers "Turing CPU Temp" to run elevated at logon.
#
# This is the only elevated piece of the deck. It exists because AMD's Ryzen
# Master CLI - the sole source of CPU die temperature on this machine - refuses
# to run without admin, while everything else in the app runs fine unelevated
# and is better off staying that way.
#
# Remove it any time with:
#   Unregister-ScheduledTask -TaskName "Turing CPU Temp" -Confirm:$false
$ErrorActionPreference = "Stop"
$root    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pythonw = Join-Path $root "venv\Scripts\pythonw.exe"
$script  = Join-Path $root "tools\amd_temp_service.py"
$name    = "Turing CPU Temp"

if (-not (Test-Path $pythonw)) { throw "pythonw not found at $pythonw" }
if (-not (Test-Path $script))  { throw "amd_temp_service.py not found at $script" }

# pythonw.exe rather than the .bat, so no console window flashes at logon.
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $root

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$principal = New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\$env:USERNAME" `
    -LogonType Interactive -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$settings.DisallowStartIfOnBatteries = $false
$settings.StopIfGoingOnBatteries = $false

if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
}
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description "Reads CPU temperature via the AMD Ryzen Master SDK and publishes cache/cpu_temp.json for the Turing deck." | Out-Null

Start-ScheduledTask -TaskName $name

$t = Get-ScheduledTask -TaskName $name
"registered : $($t.TaskName)"
"runlevel   : $($t.Principal.RunLevel)"
"action     : $($t.Actions[0].Execute) $($t.Actions[0].Arguments)"

# Prove it actually produced a reading, rather than reporting success on
# registration alone. The first CLI call takes ~1.2s; allow for a slow start.
$state = Join-Path $root "cache\cpu_temp.json"
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Path $state) {
        $age = (Get-Date) - (Get-Item $state).LastWriteTime
        if ($age.TotalSeconds -lt 30) { break }
    }
}
if (Test-Path $state) {
    "reading    : $(Get-Content $state -Raw)"
} else {
    "reading    : NONE - cache\cpu_temp.json was never written. Check Task Scheduler history."
}
