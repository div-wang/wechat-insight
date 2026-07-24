[CmdletBinding()]
param(
    [string]$BuildPython = "",
    [string]$RuntimeVersion = "3.13.12",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$SourceRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$BuildRoot = Join-Path $SourceRoot "build\portable-runtime"
$DownloadRoot = Join-Path $SourceRoot "build\downloads"
$DistRoot = Join-Path $SourceRoot "dist"
$PackageRoot = Join-Path $DistRoot "wechat-insight-portable"
$ReleaseRoot = Join-Path $SourceRoot "release"
$ArchivePath = Join-Path $ReleaseRoot "wechat-insight-portable-windows-x64.zip"
$RuntimeZip = Join-Path $DownloadRoot "python-$RuntimeVersion-embed-amd64.zip"
$RuntimeUrl = "https://www.python.org/ftp/python/$RuntimeVersion/python-$RuntimeVersion-embed-amd64.zip"
$GetPipPath = Join-Path $DownloadRoot "get-pip.py"

if (-not $BuildPython) {
    $VenvPython = Join-Path $SourceRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $VenvPython) {
        $BuildPython = $VenvPython
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $BuildPython = "py"
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $BuildPython = "python"
    } else {
        throw "Python 3.9+ is required on the build computer only."
    }
}

if (-not $SkipTests) {
    & $BuildPython -m unittest discover -s (Join-Path $SourceRoot "tests") -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed. Portable build stopped." }
}

foreach ($Target in @($BuildRoot, $PackageRoot)) {
    $ResolvedTarget = [System.IO.Path]::GetFullPath($Target)
    if (-not $ResolvedTarget.StartsWith($SourceRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a directory outside the workspace: $ResolvedTarget"
    }
    if (Test-Path -LiteralPath $ResolvedTarget) {
        Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
    }
}

New-Item -ItemType Directory -Force `
    -Path $BuildRoot, $DownloadRoot, $PackageRoot, $ReleaseRoot | Out-Null

if (-not (Test-Path -LiteralPath $RuntimeZip)) {
    Write-Host "Downloading official Python embedded runtime: $RuntimeUrl"
    Invoke-WebRequest -Uri $RuntimeUrl -OutFile $RuntimeZip
}
if (-not (Test-Path -LiteralPath $GetPipPath)) {
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPipPath
}

$RuntimeDir = Join-Path $PackageRoot "runtime"
$AppDir = Join-Path $PackageRoot "app"
New-Item -ItemType Directory -Force -Path $RuntimeDir, $AppDir | Out-Null
Expand-Archive -LiteralPath $RuntimeZip -DestinationPath $RuntimeDir -Force

$RuntimeMinor = ($RuntimeVersion.Split(".")[0..1] -join "")
$PthPath = Join-Path $RuntimeDir "python$RuntimeMinor._pth"
if (-not (Test-Path -LiteralPath $PthPath)) {
    throw "Embedded Python path configuration was not found: $PthPath"
}
@(
    "python$RuntimeMinor.zip",
    ".",
    "Lib\site-packages",
    "..\app",
    "import site"
) | Set-Content -LiteralPath $PthPath -Encoding ASCII

$RuntimePython = Join-Path $RuntimeDir "python.exe"
$Signature = Get-AuthenticodeSignature -LiteralPath $RuntimePython
if ($Signature.Status -ne "Valid") {
    throw "Official embedded Python signature is not valid: $($Signature.Status)"
}

& $RuntimePython $GetPipPath --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "Failed to bootstrap pip in portable runtime." }
& $RuntimePython -m pip install `
    -r (Join-Path $SourceRoot "requirements.txt") `
    --no-warn-script-location `
    --no-cache-dir
if ($LASTEXITCODE -ne 0) { throw "Failed to install portable runtime dependencies." }

Copy-Item -LiteralPath (Join-Path $SourceRoot "wechat_insight_cli.py") -Destination $AppDir
Copy-Item -LiteralPath (Join-Path $SourceRoot "scripts") -Destination $AppDir -Recurse
Copy-Item -Path (Join-Path $SourceRoot "portable\*") -Destination $PackageRoot -Recurse

$ArchiveCreated = $false
for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
    try {
        if (Test-Path -LiteralPath $ArchivePath) {
            Remove-Item -LiteralPath $ArchivePath -Force
        }
        & tar.exe -a -c -f $ArchivePath -C $DistRoot "wechat-insight-portable"
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $ArchivePath)) {
            $Entries = @(& tar.exe -tf $ArchivePath)
            if ($LASTEXITCODE -eq 0 -and $Entries.Count -gt 10) {
                $ArchiveCreated = $true
                break
            }
        }
    } catch {
        Write-Warning "Archive attempt $Attempt failed: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 2
}
if (-not $ArchiveCreated) {
    throw "Failed to create a verified portable archive after 5 attempts."
}

$ArchiveHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash
Write-Host "Portable directory: $PackageRoot"
Write-Host "Portable archive: $ArchivePath"
Write-Host "SHA256: $ArchiveHash"
