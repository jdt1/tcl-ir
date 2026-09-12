param(
    [Parameter(Mandatory = $true)]
    [string]$WdiSimplePath
)

$ErrorActionPreference = 'Stop'

$expectedInstallerHash = 'E5C3C51396E5AEACC5B63D2991623894EB5A6A8981B7AEF680E1D0D8AF8A4387'
$devicePattern = 'USB\VID_045C&PID_0195*'
$usbFlagsPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\usbflags\045C01950200'
$stateDirectory = Join-Path $env:LOCALAPPDATA 'tcl-ir'
$driverDirectory = Join-Path $stateDirectory 'driver'
$logPath = Join-Path $stateDirectory 'winusb-install.log'
$resultPath = Join-Path $stateDirectory 'setup-result.json'

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'This setup must run elevated.'
}

if (-not (Test-Path -LiteralPath $WdiSimplePath -PathType Leaf)) {
    throw "wdi-simple was not found at $WdiSimplePath"
}

$actualHash = (Get-FileHash -LiteralPath $WdiSimplePath -Algorithm SHA256).Hash
if ($actualHash -ne $expectedInstallerHash) {
    throw "Unexpected wdi-simple SHA-256: $actualHash"
}

$devices = @(Get-PnpDevice -PresentOnly | Where-Object InstanceId -like $devicePattern)
if ($devices.Count -ne 1) {
    throw "Expected exactly one connected USB 045C:0195 device; found $($devices.Count)."
}

New-Item -ItemType Directory -Force -Path $stateDirectory, $driverDirectory | Out-Null
New-Item -Path $usbFlagsPath -Force | Out-Null
New-ItemProperty -Path $usbFlagsPath -Name SkipBOSDescriptorQuery -PropertyType DWord -Value 1 -Force | Out-Null

$installerArguments = @(
    '--name', 'Ocrustar USB IR Blaster',
    '--manufacturer', 'ElkSmart',
    '--vid', '0x045c',
    '--pid', '0x0195',
    '--type', '0',
    '--inf', 'ocrustar_winusb.inf',
    '--dest', $driverDirectory,
    '--timeout', '120000',
    '--silent',
    '--log', '1'
)

& $WdiSimplePath @installerArguments 2>&1 | Tee-Object -FilePath $logPath
if ($LASTEXITCODE -ne 0) {
    throw "WinUSB installer failed with exit code $LASTEXITCODE. See $logPath"
}

Start-Sleep -Seconds 2
$device = Get-PnpDevice -PresentOnly | Where-Object InstanceId -like $devicePattern | Select-Object -First 1
$properties = if ($device) { @(Get-PnpDeviceProperty -InstanceId $device.InstanceId) } else { @() }
$result = [pscustomobject]@{
    Status = $device.Status
    Class = $device.Class
    FriendlyName = $device.FriendlyName
    InstanceId = $device.InstanceId
    Problem = $device.Problem
    Service = ($properties | Where-Object KeyName -eq 'DEVPKEY_Device_Service').Data
    DriverInf = ($properties | Where-Object KeyName -eq 'DEVPKEY_Device_DriverInfPath').Data
    SkipBOSDescriptorQuery = (Get-ItemProperty -Path $usbFlagsPath).SkipBOSDescriptorQuery
}
$result | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding utf8
$result | Format-List
