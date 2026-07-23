[CmdletBinding()]
param(
    [string]$InstallDir = $(if ($env:WECHAT_INSIGHT_HOME) { $env:WECHAT_INSIGHT_HOME } else { Join-Path $env:LOCALAPPDATA "wechat-insight" }),
    [string]$Repository = $(if ($env:WECHAT_INSIGHT_REPO_URL) { $env:WECHAT_INSIGHT_REPO_URL } else { "https://github.com/caigee-cmd/wechat-insight.git" })
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host "[wechat-insight] $Message" -ForegroundColor Cyan
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "未找到 Git。请先安装 Git for Windows。"
}

$Python = if (Get-Command py -ErrorAction SilentlyContinue) {
    @("py", "-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    @("python")
} else {
    throw "未找到 Python，需要 Python 3.9 或更高版本。"
}

& $Python[0] @($Python[1..($Python.Count - 1)]) -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python 版本太低，需要 Python 3.9+。" }

$ResolvedInstallDir = [System.IO.Path]::GetFullPath($InstallDir)
if (Test-Path (Join-Path $ResolvedInstallDir ".git")) {
    Write-Step "更新已有 checkout: $ResolvedInstallDir"
    & git -C $ResolvedInstallDir pull --ff-only
} else {
    Write-Step "clone 到: $ResolvedInstallDir"
    $Parent = Split-Path -Parent $ResolvedInstallDir
    New-Item -ItemType Directory -Force -Path $Parent | Out-Null
    & git clone $Repository $ResolvedInstallDir
}
if ($LASTEXITCODE -ne 0) { throw "获取源码失败。" }

$VenvDir = Join-Path $ResolvedInstallDir ".venv"
if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
    Write-Step "创建 venv: $VenvDir"
    & $Python[0] @($Python[1..($Python.Count - 1)]) -m venv $VenvDir
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
Write-Step "安装 Python 依赖"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $ResolvedInstallDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "安装 Python 依赖失败。" }

Write-Host ""
Write-Host "安装完成。下一步："
Write-Host "  cd `"$ResolvedInstallDir`""
Write-Host "  .\wechat-insight.cmd doctor"
Write-Host "  .\wechat-insight.cmd setup"
