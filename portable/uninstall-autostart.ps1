[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$TaskName = "WeChat Insight Collector"

function Test-Administrator {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
    return $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
    Start-Process powershell.exe `
        -Verb RunAs `
        -WindowStyle Hidden `
        -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden",
            "-File", "`"$PSCommandPath`""
        )
    exit 0
}

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task: $TaskName"
} else {
    Write-Host "Scheduled task does not exist."
}

Write-Host "Local configuration, keys, messages, and logs were preserved."
