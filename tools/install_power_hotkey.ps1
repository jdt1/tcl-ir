[CmdletBinding()]
param([switch]$Enable)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$monitor = Join-Path $repoRoot 'src\power_hotkey.py'
$pythonw = $null
foreach ($environmentName in @('.venv', 'venv')) {
    $candidate = Join-Path $repoRoot "$environmentName\Scripts\pythonw.exe"
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $pythonw = $candidate
        break
    }
}
if ($null -eq $pythonw) {
    throw "Virtual-environment Python was not found under .venv or venv in $repoRoot"
}
if (-not (Test-Path -LiteralPath $monitor -PathType Leaf)) {
    throw "Power hotkey listener was not found at $monitor"
}
$arguments = '"' + $monitor + '"'
if (-not $Enable) { $arguments += ' --dry-run' }
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'TCL IR Power Hotkey.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $repoRoot
$shortcut.WindowStyle = 7
$shortcut.Save()

Get-CimInstance Win32_Process -Filter "name='pythonw.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains('"' + $monitor + '"') } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop }
$process = Start-Process -FilePath $pythonw -ArgumentList $arguments -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2
$process.Refresh()
if ($process.HasExited) { throw 'Power hotkey listener exited. Check power-hotkey.log.' }
$mode = if ($Enable) { 'ENABLED' } else { 'DRY RUN' }
Write-Host "TCL IR Power Hotkey installed in your startup folder and started in $mode mode."
Write-Host "Log: $env:LOCALAPPDATA\tcl-ir\power-hotkey.log"
