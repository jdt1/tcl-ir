[CmdletBinding()]
param(
    [switch]$Enable,
    [ValidateRange(0, 86400)]
    [int]$MinOffSeconds = 660,
    [ValidateRange(0, 300)]
    [int]$RecentInputSeconds = 5,
    [ValidateRange(0, 86400)]
    [int]$CooldownSeconds = 60
)

$ErrorActionPreference = 'Stop'
$taskName = 'TCL IR Wake Monitor'
$repoRoot = Split-Path -Parent $PSScriptRoot
$monitor = Join-Path $repoRoot 'src\wake_monitor.py'

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
    throw "Wake monitor was not found at $monitor"
}

$arguments = @(
    ('"{0}"' -f $monitor)
    '--min-off-seconds', $MinOffSeconds
    '--recent-input-seconds', $RecentInputSeconds
    '--cooldown-seconds', $CooldownSeconds
)
if (-not $Enable) {
    $arguments += '--dry-run'
}

$action = New-ScheduledTaskAction `
    -Execute $pythonw `
    -Argument ($arguments -join ' ') `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings

Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

$mode = if ($Enable) { 'ENABLED' } else { 'DRY RUN' }
Write-Host "$taskName installed and started in $mode mode."
Write-Host "Log: $env:LOCALAPPDATA\tcl-ir\wake-monitor.log"
