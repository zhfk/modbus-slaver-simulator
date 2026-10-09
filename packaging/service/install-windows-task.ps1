param([string]$DataDir = "$env:LOCALAPPDATA\ModbusSimulator", [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$ReleaseDir = Split-Path $PSScriptRoot -Parent
$Binary = Join-Path $ReleaseDir 'modbus-simulator.exe'
if (!(Test-Path $Binary)) { throw 'Use this script from the unpacked Windows release' }
$Action = New-ScheduledTaskAction -Execute $Binary -Argument "--port $Port --data-dir `"$DataDir`"" -WorkingDirectory $ReleaseDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName 'ModbusSimulator' -Action $Action -Trigger $Trigger -Settings $Settings -Description 'Self-contained Modbus simulator with external supervision' -Force
