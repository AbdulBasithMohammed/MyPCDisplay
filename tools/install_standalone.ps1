# Installs Turing Deck as a self-contained app in %LOCALAPPDATA%\TuringDeck.
#
# It carries its own copy of Python, so the app keeps working if you upgrade,
# move or uninstall the Python in your user profile. A copied python.exe keeps
# its Authenticode signature, so Smart App Control allows it - unlike a
# self-built PyInstaller exe, which is unsigned and gets blocked.
#
# Run elevated: the logon task needs admin, and the running deck is elevated.
$ErrorActionPreference = "Stop"
$repo   = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$dest   = Join-Path $env:LOCALAPPDATA "TuringDeck"
$pysrc  = Split-Path -Parent (Get-Command "$repo\venv\Scripts\python.exe").Source | Split-Path -Parent
$pybase = (Get-Content "$repo\venv\pyvenv.cfg" | Where-Object { $_ -like "home =*" }) -replace '^home\s*=\s*',''
$name   = "Turing Deck"

# Elevate if we are not already. The logon task runs the deck as admin, so its
# python.exe holds locks (libcrypto-3.dll and friends) that a normal user
# cannot break - and an unelevated query cannot even read those processes'
# command lines, so the stop step below silently matches nothing and the
# install then dies half-way through deleting the old folder.
$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Output "not elevated - relaunching as administrator (approve the UAC prompt)..."
    $psi = Start-Process -FilePath "powershell.exe" -Verb RunAs -PassThru -Wait `
        -ArgumentList @("-ExecutionPolicy","Bypass","-NoProfile","-File","`"$($MyInvocation.MyCommand.Path)`"")
    exit $psi.ExitCode
}

if (-not (Test-Path $pybase)) { throw "base Python not found at $pybase" }

function Say($m) { Write-Output $m }

# 1. stop anything running from the repo or a previous install
$targets = Get-CimInstance Win32_Process |
  Where-Object { $_.Name -in @('pythonw.exe','python.exe') -and $_.CommandLine -and
                 (($_.CommandLine -like "*$repo*") -or ($_.CommandLine -like "*$dest*")) }
foreach ($p in $targets) { Say "stopping pid $($p.ProcessId)"; Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

# Confirm they are really gone. Deleting the install folder out from under a
# live process fails halfway and leaves a broken install, so stop here instead.
$still = Get-CimInstance Win32_Process |
  Where-Object { $_.Name -in @('pythonw.exe','python.exe') -and $_.CommandLine -and
                 (($_.CommandLine -like "*$repo*") -or ($_.CommandLine -like "*$dest*")) }
if ($still) {
    throw ("still running after stop: " + (($still | ForEach-Object { $_.ProcessId }) -join ", ") +
           " - close Turing Deck from its tray icon and run this again")
}

# 2. Preserve only files that hold *user or runtime* state:
#      config.yaml       - the app writes the current theme and flip state here
#      services.yaml     - the user's location and private calendar URL
#      geocode_cache.json- saves a lookup, harmless to keep
#    deck.yaml is deliberately NOT preserved: it defines which screens exist, so
#    keeping the old copy would stop new screens ever reaching the install.
$keep = @{}
foreach ($f in @("config.yaml","services.yaml","geocode_cache.json","discord_token.json")) {
    $p = Join-Path $dest $f
    if (Test-Path $p) { $keep[$f] = Get-Content $p -Raw }
}
$cacheTmp = Join-Path $env:TEMP "TuringDeck-cache-carry"
$cacheSrc = Join-Path $dest "cache"
if (Test-Path $cacheTmp) { Remove-Item $cacheTmp -Recurse -Force }
if (Test-Path $cacheSrc) { Move-Item $cacheSrc $cacheTmp -Force }

if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory -Path $dest -Force | Out-Null

if (Test-Path $cacheTmp) {
    Move-Item $cacheTmp (Join-Path $dest "cache") -Force
    Say "preserved download cache"
}

# 3. private Python interpreter + stdlib
Say "copying Python runtime..."
robocopy $pybase (Join-Path $dest "python") /E /NFL /NDL /NJH /NJS /NP /XD "Scripts" | Out-Null

# 4. the venv's packages on top of it (PyInstaller is build-only)
Say "copying packages..."
robocopy "$repo\venv\Lib\site-packages" (Join-Path $dest "python\Lib\site-packages") /E /NFL /NDL /NJH /NJS /NP `
    /XD "PyInstaller" "pyinstaller" "__pycache__" | Out-Null

# 5. application code
Say "copying app..."
foreach ($f in @("deck.py","main.py","config.yaml","deck.yaml","services.yaml","league_builds.yaml")) {
    $sp = Join-Path $repo $f
    if (Test-Path $sp) { Copy-Item $sp $dest -Force }
}
robocopy "$repo\library" (Join-Path $dest "library") /E /NFL /NDL /NJH /NJS /NP /XD "__pycache__" | Out-Null
robocopy "$repo\external" (Join-Path $dest "external") /E /NFL /NDL /NJH /NJS /NP | Out-Null

# 6. only the resources our screens reference (full res/ is over 1 GB)
foreach ($t in @("DeckWhiteBlue","DeckDetail","DeckAgenda","DeckVoice","DeckLeague")) {
    robocopy "$repo\res\themes\$t" (Join-Path $dest "res\themes\$t") /E /NFL /NDL /NJH /NJS /NP | Out-Null
}
New-Item -ItemType Directory -Path (Join-Path $dest "res\themes") -Force | Out-Null
Copy-Item "$repo\res\themes\default.yaml" (Join-Path $dest "res\themes") -Force
foreach ($f in @("jetbrains-mono","roboto","malgun")) {
    robocopy "$repo\res\fonts\$f" (Join-Path $dest "res\fonts\$f") /E /NFL /NDL /NJH /NJS /NP | Out-Null
}
robocopy "$repo\res\icons" (Join-Path $dest "res\icons") /E /NFL /NDL /NJH /NJS /NP | Out-Null

foreach ($f in $keep.Keys) {
    Set-Content -Path (Join-Path $dest $f) -Value $keep[$f] -Encoding utf8 -NoNewline
    Say "preserved existing $f"
}

# 7. point the logon task at the private interpreter
$pyw = Join-Path $dest "python\pythonw.exe"
if (-not (Test-Path $pyw)) { throw "pythonw.exe missing from the copied runtime" }
$action  = New-ScheduledTaskAction -Execute $pyw -Argument "`"$(Join-Path $dest 'deck.py')`"" -WorkingDirectory $dest
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT15S"
$principal = New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$settings.DisallowStartIfOnBatteries = $false
$settings.StopIfGoingOnBatteries = $false
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
}
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description "Turing smart screen deck (self-contained)." | Out-Null

# 8. launch and report
Start-Process -FilePath $pyw -ArgumentList "`"$(Join-Path $dest 'deck.py')`"" -WorkingDirectory $dest
Start-Sleep -Seconds 8

Say ""
Say "installed to  : $dest"
Say ("size          : {0:N0} MB" -f ((Get-ChildItem $dest -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))
Say ("interpreter   : {0}" -f $pyw)
Say ("signature     : {0}" -f (Get-AuthenticodeSignature $pyw).Status)
Say ("task action   : {0}" -f (Get-ScheduledTask -TaskName $name).Actions[0].Execute)
Say "running       :"
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
  Where-Object { $_.CommandLine -like "*$dest*" } |
  ForEach-Object { Say ("  pid {0}  {1}" -f $_.ProcessId, $_.CommandLine) }
