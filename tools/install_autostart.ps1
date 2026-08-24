# Registers "Turing Deck" to start hidden and elevated at logon.
# Remove it any time with:  Unregister-ScheduledTask -TaskName "Turing Deck" -Confirm:$false
$ErrorActionPreference = "Stop"
$root    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pythonw = Join-Path $root "venv\Scripts\pythonw.exe"
$script  = Join-Path $root "deck.py"
$name    = "Turing Deck"

if (-not (Test-Path $pythonw)) { throw "pythonw not found at $pythonw" }
if (-not (Test-Path $script))  { throw "deck.py not found at $script" }

# pythonw.exe is targeted directly rather than the .bat, so no console window
# can flash at logon.
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $root

# Delay so USB has enumerated and COM3 exists before the panel is opened.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT15S"

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
    -Description "Turing smart screen deck: tray controller, hotkeys and display." | Out-Null

$t = Get-ScheduledTask -TaskName $name
"registered : $($t.TaskName)"
"state      : $($t.State)"
"runlevel   : $($t.Principal.RunLevel)"
"action     : $($t.Actions[0].Execute) $($t.Actions[0].Arguments)"
"workingdir : $($t.Actions[0].WorkingDirectory)"
