[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$taskName = 'TCL IR Wake Monitor'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "$taskName is not installed."
    exit 0
}

Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Host "$taskName stopped and removed. Existing logs were retained."
