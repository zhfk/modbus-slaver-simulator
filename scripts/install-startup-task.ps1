param([string]$DataDir = "$env:LOCALAPPDATA\ModbusSimulator", [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (!(Test-Path $Python)) { throw 'Run scripts/build.ps1 first' }
$Action = New-ScheduledTaskAction -Execute $Python -Argument "-m simulator.supervisor --port $Port --data-dir `"$DataDir`"" -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName 'ModbusSimulator' -Action $Action -Trigger $Trigger -Settings $Settings -Description 'Modbus simulator with external health monitor' -Force
Write-Output 'Registered logon task. For unattended boot, configure an appropriate service account and startup trigger in Task Scheduler.'
