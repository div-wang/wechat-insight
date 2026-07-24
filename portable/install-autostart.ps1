[CmdletBinding()]
param(
    [string]$ReportUrl = "",
    [string]$ReportToken = "",
    [int]$IntervalSeconds = 60,
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$TaskName = "WeChat Insight Collector"
$PackageRoot = $PSScriptRoot
$PythonwExe = Join-Path $PackageRoot "runtime\pythonw.exe"
$AgentScript = Join-Path $PackageRoot "app\scripts\windows_agent.py"
$StateDir = Join-Path $env:LOCALAPPDATA "wechat-insight"
$ConfigPath = Join-Path $StateDir "wechat-insight.json"

function Test-Administrator {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
    return $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
    $Arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden",
        "-File", "`"$PSCommandPath`"",
        "-IntervalSeconds", $IntervalSeconds
    )
    if ($ReportUrl) { $Arguments += @("-ReportUrl", "`"$ReportUrl`"") }
    if ($ReportToken) { $Arguments += @("-ReportToken", "`"$ReportToken`"") }
    if ($NoStart) { $Arguments += "-NoStart" }
    Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList $Arguments
    exit 0
}

if (-not (Test-Path -LiteralPath $PythonwExe)) {
    throw "Portable Python runtime was not found: $PythonwExe"
}
if (-not (Test-Path -LiteralPath $AgentScript)) {
    throw "Background agent was not found: $AgentScript"
}

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
$Config = @{}
if (Test-Path -LiteralPath $ConfigPath) {
    $Existing = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $Existing.psobject.Properties | ForEach-Object { $Config[$_.Name] = $_.Value }
}
$Config["poll_interval_seconds"] = [Math]::Max(15, $IntervalSeconds)
$Config["poll_lookback_days"] = if ($Config.ContainsKey("poll_lookback_days")) { $Config["poll_lookback_days"] } else { 2 }
$Config["lan_report_batch_size"] = if ($Config.ContainsKey("lan_report_batch_size")) { $Config["lan_report_batch_size"] } else { 200 }
$Config["lan_report_timeout_seconds"] = if ($Config.ContainsKey("lan_report_timeout_seconds")) { $Config["lan_report_timeout_seconds"] } else { 10 }
if ($ReportUrl) { $Config["lan_report_url"] = $ReportUrl }
if ($ReportToken) { $Config["lan_report_token"] = $ReportToken }
$ConfigJson = $Config | ConvertTo-Json -Depth 10
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ConfigPath, $ConfigJson, $Utf8NoBom)

$CurrentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Action = New-ScheduledTaskAction `
    -Execute $PythonwExe `
    -Argument "`"$AgentScript`"" `
    -WorkingDirectory $PackageRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Export WeChat messages every minute and report to a configured LAN endpoint" `
    -Force | Out-Null

if (-not $NoStart) {
    Start-ScheduledTask -TaskName $TaskName
}

Write-Host "Installed: $TaskName"
Write-Host "Run as: $CurrentUser (highest privileges)"
Write-Host "Configuration: $ConfigPath"
Write-Host "Log: $(Join-Path $StateDir 'logs\agent.log')"
if (-not $Config["lan_report_url"]) {
    Write-Host "LAN reporting is disabled. Configure lan_report_url and restart the task."
}
