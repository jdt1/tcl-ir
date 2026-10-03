[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$monitor = Join-Path $repoRoot 'src\power_hotkey.py'
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'TCL IR Power Hotkey.lnk'
if (Test-Path -LiteralPath $shortcutPath) {
    Remove-Item -LiteralPath $shortcutPath -Force
}
Get-CimInstance Win32_Process -Filter "name='pythonw.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains('"' + $monitor + '"') } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop }
Write-Host 'TCL IR Power Hotkey stopped and removed from startup. Existing logs were retained.'
