# Install the daily sqlite backup job on the server.
#
#   .\deploy\install_backup.ps1
#
# Uploads deploy\backup_db.py and deploy\install_backup_remote.sh, then writes
# /etc/cron.d/sleep-schedule-backup (04:17 UTC daily, keep 14 copies in
# /var/backups/sleep-schedule) and runs it once so the result is visible.
# Connection settings come from deploy/local.settings.ps1 like the other
# deploy scripts.  This file is pure ASCII on purpose (Windows PowerShell 5.1
# reads a BOM-less .ps1 as ANSI).
param(
    [string]$Server,
    [string]$User,
    [string]$Password,
    [int]$Port,
    [string]$Root
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
if (-not $SudoPass) { $SudoPass = $Password }
if (-not $Port) { $Port = $DeployPort }
if (-not $Port) { $Port = 22 }
if (-not $Root) {
    $derived = Split-Path -Parent $PSScriptRoot
    if (Test-Path -LiteralPath (Join-Path $derived "run.py")) { $Root = $derived }
    else { $Root = (Get-Location).Path }
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot "backup_db.py"))) {
    throw "deploy\backup_db.py not found"
}

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
$sudoB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($SudoPass))

& ssh @common $target "rm -rf /tmp/sleep-backup && mkdir -p /tmp/sleep-backup"
if ($LASTEXITCODE -ne 0) { throw "ssh pre-clean failed" }
& scp @scpCommon (Join-Path $PSScriptRoot "backup_db.py") "${target}:/tmp/sleep-backup/"
if ($LASTEXITCODE -ne 0) { throw "scp backup_db.py failed" }
& scp @scpCommon (Join-Path $PSScriptRoot "install_backup_remote.sh") "${target}:/tmp/sleep-backup/"
if ($LASTEXITCODE -ne 0) { throw "scp remote script failed" }

$remote = "export SUDO_PASS=`"`$(echo `"$sudoB64`" | base64 -d)`" && bash /tmp/sleep-backup/install_backup_remote.sh"
& ssh @common $target $remote
if ($LASTEXITCODE -ne 0) { throw "remote install failed" }
Write-Host "backup job installed"
