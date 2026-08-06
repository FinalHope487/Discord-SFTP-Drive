#Requires -Version 5.1
<#
.SYNOPSIS
    Build the client, bring the stack up, and wait until the drive can actually
    be opened.

.DESCRIPTION
    `docker compose up -d` returns as soon as the containers are *running*,
    which is several seconds before the server is usable: it still has to
    authenticate against Discord, reach MongoDB, build its indexes and start
    listening. Opening the address in that window gives a connection refused,
    which reads like a broken deployment rather than an early click.

    So this waits for /api/health, and the wait is meaningful: the web server
    is started last (src/main.py), after the Discord credentials have been
    validated and the SFTP listener is up. Nothing answers on that path until
    startup has genuinely finished.

    It deliberately does NOT re-check .env, the Discord token, or the password
    secret. docker-compose.yml already fails on those with one readable line
    each, and a second opinion here would be a second parser guessing at the
    first one's output.

.PARAMETER SkipBuild
    Leave client/app/dist as it is. Use when only the Python side changed.

.PARAMETER Open
    Open the address in the default browser once it answers.

.PARAMETER TimeoutSeconds
    How long to wait for the server before giving up and showing the log.

.EXAMPLE
    .\scripts\start.ps1
.EXAMPLE
    .\scripts\start.ps1 -Open
#>
[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$Open,
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "  $text" -ForegroundColor Cyan }
function Note($text) { Write-Host "  $text" -ForegroundColor DarkGray }
function Die($text) {
    Write-Host ""
    Write-Host "  $text" -ForegroundColor Red
    exit 1
}

# Both docker and npm report progress on stderr, and Windows PowerShell turns
# stderr from a native command into a terminating error whenever that stream is
# being captured -- so the script would "fail" with a non-zero exit code while
# printing that everything is ready. It only looks fine in an interactive
# terminal, where nothing is capturing. The exit code is the thing worth
# trusting, so each call checks $LASTEXITCODE itself and the preference is
# lowered for the duration of the call.
function Invoke-Native {
    param([scriptblock]$Command, [string]$Failure)
    $prior = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command } finally { $ErrorActionPreference = $prior }
    if ($LASTEXITCODE -ne 0 -and $Failure) { Die $Failure }
}

# An address you can actually browse to. 0.0.0.0 and [::] mean "every
# interface", which is a bind address, not a destination.
function Reachable($published) {
    if (-not $published) { return $null }
    return ($published.Trim() -replace '^0\.0\.0\.0:', '127.0.0.1:' -replace '^\[::\]:', '127.0.0.1:')
}

if (-not (Test-Path ".env")) {
    Die ".env is missing. Copy .env.example to .env and fill it in first."
}

# ------------------------------------------------------------------ client
#
# dist/ is mounted into the container read-only rather than baked into the
# image, so building it here costs a browser refresh instead of an image
# rebuild -- which would drop every live session and with it every unwrapped
# master key. Building unconditionally (it takes under a second) is what makes
# "run this, then open the address" true after a frontend edit; a staleness
# check that gets it wrong would serve the old interface silently.
if ($SkipBuild) {
    Note "Skipping the client build (-SkipBuild)."
} else {
    if (-not (Test-Path "client/app/node_modules")) {
        Step "Installing client dependencies (first run only)..."
        Invoke-Native { npm --prefix client/app install } "npm install failed."
    }
    Step "Building the client..."
    Invoke-Native { npm --prefix client/app run build } `
        "The client build failed; the interface would be stale."
}

# ------------------------------------------------------------------- stack
Step "Starting the containers..."
Invoke-Native { docker compose up -d --build } `
    "docker compose refused to start. Its message above is the real one."

# Ask Docker where port 8080 actually landed instead of reading WEB_BIND and
# WEB_PORT back out of .env: compose owns that resolution, and re-deriving it
# here would be a guess that goes wrong exactly when someone has changed it.
Invoke-Native { $script:published = docker compose port sftp-discord-server 8080 | Select-Object -First 1 }
$published = Reachable $script:published
if (-not $published) { Die "Port 8080 is not published. Is WEB_ENABLED set to 0?" }
$url = "http://$published"

# ------------------------------------------------------------------- ready
Step "Waiting for the server to finish starting..."
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$ready = $false
while ((Get-Date) -lt $deadline) {
    # `restart: on-failure:5` means a configuration the server rejects ends as
    # an exited container, not a slow one. Without this the reward for a typo
    # in .env would be the full timeout and then a log.
    Invoke-Native {
        $script:state = docker compose ps --format "{{.Service}} {{.State}}" |
            Where-Object { $_ -like "sftp-discord-server *" }
    }
    if ($script:state -and $script:state -notlike "* running*") {
        Write-Host ""
        Invoke-Native { docker compose logs --tail 30 sftp-discord-server }
        Die "The server stopped while starting. The last lines of its log are above."
    }

    try {
        $response = Invoke-WebRequest -Uri "$url/api/health" -UseBasicParsing -TimeoutSec 3
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch {
        # Connection refused for as long as it is still starting up. Expected.
    }
    Start-Sleep -Milliseconds 700
}

if (-not $ready) {
    Write-Host ""
    Invoke-Native { docker compose logs --tail 30 sftp-discord-server }
    Die "No answer on $url/api/health after ${TimeoutSeconds}s. The log is above."
}

Invoke-Native { $script:sftpPort = docker compose port sftp-discord-server 2222 | Select-Object -First 1 }
$sftp = Reachable $script:sftpPort

Write-Host ""
Write-Host "  Ready. Open:  $url" -ForegroundColor Green
if ($sftp) { Note "SFTP is on $sftp with the same username and password." }
Note "Sign in with SFTP_USER and the password in secrets/sftp_password."
Note "Stop it again with: docker compose down"
Write-Host ""

if ($Open) { Start-Process $url }

# Explicit, so the exit code says whether the drive is up. Falling off the end
# leaves whatever the last native command happened to set, and every step here
# ends in one of those -- the script would report the exit code of `docker
# compose port` while claiming to be a readiness check.
exit 0
