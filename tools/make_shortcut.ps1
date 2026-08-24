# Creates a Desktop shortcut that starts the Turing Deck.
# It runs the registered scheduled task rather than the script directly, so the
# deck starts elevated WITHOUT a UAC prompt (the task is registered at highest
# run level). Falls back to start-deck.bat if the task is missing.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$icon = Join-Path $root "res\icons\monitor-icon-17865\64.png"
$lnk  = Join-Path ([Environment]::GetFolderPath("Desktop")) "Turing Deck.lnk"

$task = Get-ScheduledTask -TaskName "Turing Deck" -ErrorAction SilentlyContinue
$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut($lnk)
if ($task) {
    $s.TargetPath = "$env:SystemRoot\System32\schtasks.exe"
    $s.Arguments  = '/run /tn "Turing Deck"'
    $s.Description = "Start the Turing Deck panel controller (no UAC prompt)"
} else {
    $s.TargetPath = Join-Path $root "start-deck.bat"
    $s.Description = "Start the Turing Deck panel controller"
}
$s.WorkingDirectory = $root
$s.WindowStyle = 7          # minimised, so no window is thrown in your face
# .lnk needs an .ico; convert the bundled png once.
$ico = Join-Path $root "res\icons\deck.ico"
if (-not (Test-Path $ico) -and (Test-Path $icon)) {
    Add-Type -AssemblyName System.Drawing
    $bmp = [System.Drawing.Bitmap]::FromFile($icon)
    $h = $bmp.GetHicon()
    $i = [System.Drawing.Icon]::FromHandle($h)
    $fs = [System.IO.File]::Create($ico)
    $i.Save($fs); $fs.Close(); $bmp.Dispose()
}
if (Test-Path $ico) { $s.IconLocation = $ico }
$s.Save()
"shortcut : $lnk"
"target   : $($s.TargetPath) $($s.Arguments)"
"via task : $([bool]$task)"
