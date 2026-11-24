# Build the Yiuki Sleep desktop bundle (onedir) and verify it with --smoke.
#
#   pwsh -File desktop/build/build.ps1
#   pwsh -File desktop/build/build.ps1 -SkipTests
#
# Output: dist\desktop\YiukiSleep\YiukiSleep.exe plus dist\desktop\SHA256SUMS.txt.
# Inno Setup runs only when desktop\build\installer.iss and ISCC.exe both exist.

param(
    [switch]$SkipTests,
    [string]$Python = "",
    [string]$Iscc = ""
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

if ($Python -eq "") {
    foreach ($candidate in @("desktop\.venv\Scripts\python.exe", "backend\.venv\Scripts\python.exe")) {
        if (Test-Path $candidate) { $Python = $candidate; break }
    }
}
if ($Python -eq "" -or -not (Test-Path $Python)) { throw "python not found; create desktop\.venv first" }

$match = Select-String -Path "desktop\__init__.py" -Pattern '__version__ = "([^"]+)"'
if (-not $match) { throw "cannot read __version__ from desktop\__init__.py" }
$Version = $match.Matches[0].Groups[1].Value
Write-Host "building YiukiSleep $Version with $Python"

if (-not $SkipTests) {
    & $Python -m pytest tests\test_advice_engine.py tests\test_desktop_mode.py tests\test_desktop_shell.py -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
}

Remove-Item -Recurse -Force "dist\desktop", "build\pyinstaller" -ErrorAction SilentlyContinue
& $Python -m PyInstaller --noconfirm --clean "desktop\build\app.spec" --distpath "dist\desktop" --workpath "build\pyinstaller"
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

$Exe = "dist\desktop\YiukiSleep\YiukiSleep.exe"
if (-not (Test-Path $Exe)) { throw "missing $Exe" }
# The exe is windowed (no console), so it writes the self-check report to a file.
$Report = "dist\desktop\smoke-report.json"
& $Exe --smoke --report $Report | Out-Host
if ($LASTEXITCODE -ne 0) { throw "bundled --smoke failed" }
$Smoke = Get-Content -Path $Report -Raw -Encoding utf8 | ConvertFrom-Json
if (-not $Smoke.ok) { throw "bundled --smoke reported failure" }
Remove-Item -Force $Report -ErrorAction SilentlyContinue
Write-Host "bundled --smoke ok"

if ($Iscc -eq "") {
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )) {
        if (Test-Path $candidate) { $Iscc = $candidate; break }
    }
}
if ((Test-Path "desktop\build\installer.iss") -and $Iscc -ne "" -and (Test-Path $Iscc)) {
    & $Iscc "/DMyVersion=$Version" "desktop\build\installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "inno setup failed" }
} else {
    Write-Host "installer skipped (installer.iss or ISCC.exe not available)"
}

$base = (Resolve-Path "dist\desktop").Path.TrimEnd("\")
$hashes = Get-ChildItem -Path "dist\desktop" -Recurse -File | Where-Object { $_.Extension -in ".exe", ".zip" }
$lines = foreach ($file in $hashes) {
    $hash = (Get-FileHash -Path $file.FullName -Algorithm SHA256).Hash.ToLower()
    # Windows PowerShell 5.1 has no [System.IO.Path]::GetRelativePath.
    "$hash  $($file.FullName.Substring($base.Length + 1))"
}
$lines | Set-Content -Path "dist\desktop\SHA256SUMS.txt" -Encoding ascii

# Update manifest consumed by the app (docs/DESKTOP.md section 9). Publish the
# generated file to https://yiuki.me/desktop/latest.json from the site repo.
$installerFile = Get-ChildItem -Path "dist\desktop" -Filter "YiukiSleep-setup-*.exe" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($installerFile) { $artifact = $installerFile } else { $artifact = Get-Item "dist\desktop\YiukiSleep\YiukiSleep.exe" }
$manifest = [ordered]@{
    version       = $Version
    min_supported = "0.1.0"
    notes         = "Yiuki Sleep $Version"
    url           = "https://yiuki.me/desktop/$($artifact.Name)"
    sha256        = (Get-FileHash -Path $artifact.FullName -Algorithm SHA256).Hash.ToLower()
    size          = $artifact.Length
}
$manifest | ConvertTo-Json | Set-Content -Path "dist\desktop\latest.json" -Encoding utf8
Write-Host "artifacts in dist\desktop (latest.json written)"
