# Publish the desktop release artefacts (installer + latest.json) to the site.
#
#   .\deploy\publish_desktop.ps1
#
# Copies dist\desktop\YiukiSleep-setup-*.exe and dist\desktop\latest.json to
# /var/www/sleep-schedule/desktop/ on the server, so the desktop app's update
# check (https://yiuki.me/desktop/latest.json) and the download link work.
# Connection settings come from deploy/local.settings.ps1, exactly like
# deploy/apply_fix.ps1.  This file is pure ASCII: Windows PowerShell 5.1 reads a
# BOM-less .ps1 as ANSI, so non-ASCII text would be mangled.
param(
    [string]$Server,
    [string]$User,
    [string]$Password,
    [int]$Port,
    [string]$Root,
    # Skip the upload and only query the public URLs.
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"

$localSettings = Join-Path $PSScriptRoot 'local.settings.ps1'
if (Test-Path -LiteralPath $localSettings) {
    . $localSettings
}
else {
    if ($env:SSP_DEPLOY_HOST) { $DeployHost = $env:SSP_DEPLOY_HOST }
    if ($env:SSP_DEPLOY_USER) { $DeployUser = $env:SSP_DEPLOY_USER }
    if ($env:SSP_DEPLOY_PASSWORD) { $DeployPassword = $env:SSP_DEPLOY_PASSWORD }
    if ($env:SSP_SUDO_PASS) { $SudoPass = $env:SSP_SUDO_PASS }
    if ($env:SSP_DEPLOY_PORT) { $DeployPort = [int]$env:SSP_DEPLOY_PORT }
}
if (-not $Server) { $Server = $DeployHost }
if (-not $User) { $User = $DeployUser }
if (-not $Password) { $Password = $DeployPassword }
if (-not $Port) { $Port = $DeployPort }
if (-not $Port) { $Port = 22 }
if (-not $Root) {
    $derived = Split-Path -Parent $PSScriptRoot
    if (Test-Path -LiteralPath (Join-Path $derived "run.py")) { $Root = $derived }
    else { $Root = (Get-Location).Path }
}
$root = $Root.TrimEnd("\")
$dist = Join-Path $root "dist\desktop"

function Test-Published([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 20
        return [int]$response.StatusCode
    }
    catch {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return 0
    }
}

if (-not $VerifyOnly) {
    if (-not (Test-Path -LiteralPath (Join-Path $dist "latest.json"))) {
        throw "dist\desktop\latest.json not found - run desktop\build\build.ps1 first"
    }
    $setup = Get-ChildItem -LiteralPath $dist -Filter "YiukiSleep-setup-*.exe" |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $setup) { throw "no YiukiSleep-setup-*.exe in dist\desktop - run the build first" }

    $stage = Join-Path $env:TEMP "sleep-desktop-stage"
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
    New-Item -ItemType Directory -Path $stage | Out-Null
    Copy-Item -LiteralPath $setup.FullName -Destination $stage
    Copy-Item -LiteralPath (Join-Path $dist "latest.json") -Destination $stage

    $askpass = Join-Path $env:TEMP "dsh_askpass.bat"
    Set-Content -LiteralPath $askpass -Value "@echo off`r`necho $Password" -Encoding ASCII
    $env:SSH_ASKPASS = $askpass
    $env:SSH_ASKPASS_REQUIRE = "force"
    $env:DISPLAY = "localhost:0"

    $commonOptions = @(
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "UserKnownHostsFile=$env:USERPROFILE\.ssh\dsh_known_hosts",
        "-o", "PubkeyAuthentication=no",
        "-o", "NumberOfPasswordPrompts=1",
        "-o", "ConnectTimeout=25",
        "-o", "LogLevel=ERROR"
    )
    $common = $commonOptions + @("-p", "$Port")
    $scpCommon = $commonOptions + @("-P", "$Port")
    $target = "$User@$Server"
    $sudoB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Password))

    & ssh @common $target "rm -rf /tmp/sleep-desktop && mkdir -p /tmp/sleep-desktop"
    if ($LASTEXITCODE -ne 0) { throw "ssh pre-clean failed" }
    & scp @scpCommon (Join-Path $stage "*") "${target}:/tmp/sleep-desktop/"
    if ($LASTEXITCODE -ne 0) { throw "scp artefacts failed" }
    & scp @scpCommon (Join-Path $PSScriptRoot "publish_desktop_remote.sh") "${target}:/tmp/sleep-desktop/"
    if ($LASTEXITCODE -ne 0) { throw "scp remote script failed" }

    # The quotes are backtick-escaped so PowerShell hands them to ssh untouched;
    # without that, native argument passing strips them and bash sees a broken
    # command substitution.
    $remote = "export SUDO_PASS=`"`$(echo `"$sudoB64`" | base64 -d)`" && STAGE=/tmp/sleep-desktop bash /tmp/sleep-desktop/publish_desktop_remote.sh && rm -rf /tmp/sleep-desktop"
    & ssh @common $target $remote
    if ($LASTEXITCODE -ne 0) { throw "remote install failed" }
}

$manifest = "https://yiuki.me/desktop/latest.json"
$code = Test-Published $manifest
Write-Host ("latest.json -> HTTP " + $code)
if ($code -ne 200) { throw "latest.json is not published" }
# Read the raw bytes and decode UTF-8 ourselves: Windows PowerShell 5.1 decodes
# .Content with the ANSI code page, which turns the file into mojibake and makes
# ConvertFrom-Json fail with "Invalid JSON primitive".
$response = Invoke-WebRequest -Uri $manifest -UseBasicParsing -TimeoutSec 20
$body = [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
if ($body.Length -gt 0 -and $body[0] -eq [char]0xFEFF) { $body = $body.Substring(1) }
Write-Host ("manifest: " + $body)
$parsed = $body | ConvertFrom-Json
$setupName = [string]$parsed.url
$setupCode = Test-Published $setupName
Write-Host ($setupName + " -> HTTP " + $setupCode)
if ($setupCode -ne 200) { throw "installer is not downloadable" }
Write-Host "desktop release published"
