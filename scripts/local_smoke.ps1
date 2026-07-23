[CmdletBinding()]
param(
    [ValidateSet("doctor", "setup", "list", "quick")]
    [string]$Command = "quick",
    [int]$Days = 7
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $PSScriptRoot
$Cli = Join-Path $RootDir "wechat-insight.cmd"
$ConfigPath = if ($env:WECHAT_INSIGHT_CONFIG_PATH) { $env:WECHAT_INSIGHT_CONFIG_PATH } else { Join-Path $HOME ".config\wechat-insight.json" }

if (-not (Test-Path $Cli)) { throw "未找到 CLI: $Cli" }

function Invoke-Cli([string[]]$Arguments) {
    & $Cli @Arguments
    if ($LASTEXITCODE -ne 0) { throw "CLI 执行失败: $($Arguments -join ' ')" }
}

if ($Command -eq "doctor") { Invoke-Cli @("doctor"); exit 0 }
if ($Command -eq "setup") { Invoke-Cli @("setup"); exit 0 }
if ($Command -eq "list") { Invoke-Cli @("list"); exit 0 }

Invoke-Cli @("doctor")
Invoke-Cli @("list")
Invoke-Cli @("export", "--days", $Days.ToString())

$DataDir = Join-Path $HOME ".wechat-insight\data"
if (Test-Path $ConfigPath) {
    $Config = Get-Content -Raw -Encoding UTF8 $ConfigPath | ConvertFrom-Json
    if ($Config.data_dir) { $DataDir = [Environment]::ExpandEnvironmentVariables($Config.data_dir) }
}
$Latest = Get-ChildItem -Path $DataDir -Filter "messages_*.jsonl" -File |
    Sort-Object LastWriteTime, Name -Descending |
    Select-Object -First 1
if (-not $Latest) { throw "未找到最新导出文件，请先检查 export 是否成功。" }

Invoke-Cli @("report-data", "--input", $Latest.FullName)
Invoke-Cli @("html", "--input", $Latest.FullName)
Write-Host "Smoke 验收完成"
Write-Host "最近导出: $($Latest.FullName)"
