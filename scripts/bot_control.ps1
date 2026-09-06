<#
.SYNOPSIS
    Start / stop / check the STS2 Silent bot.

.DESCRIPTION
    Single control point for running the bot without needing a Claude session.

    `start` launches Slay the Spire 2 through Steam if it isn't already up,
    waits for the mod's API to respond, and only then starts the bot -- the
    bot can do nothing until that API answers.

    `start` also always stops any existing bot first: running two bots against
    the same game makes them fight over every screen and issue conflicting
    actions (this happened for real and looked like the bot ignoring code
    changes).

.PARAMETER Action
    start | stop | status | restart

.PARAMETER MaxActions
    Action cap for a run (default 100000, i.e. effectively "until stopped").

.PARAMETER NoGame
    Don't launch the game; assume it's already running.
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "stop", "status", "restart")]
    [string]$Action,

    [int]$MaxActions = 100000,

    # Skip launching Slay the Spire 2 (assume it's already up).
    [switch]$NoGame,

    # Relic exploration: take the least-sampled relic at each relic choice
    # instead of the best-scoring one, to widen the relic sample. These runs
    # are tagged `neow_explore` and must NOT be used to judge a code change --
    # deliberately taking weaker relics lowers the average floor.
    [switch]$Explore
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot

$SteamAppId = "2868840"
$GameProcessName = "SlayTheSpire2"
$ApiUrl = "http://localhost:15526/api/v1/singleplayer"
$GameWaitSeconds = 180

function Get-BotProcesses {
    # Match on the *command line*, not the executable name. Filtering on
    # "python.exe" alone would miss a bot launched via pythonw.exe or a
    # virtualenv shim -- STOP BOT would then report success while leaving a
    # bot alive and still driving the game.
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*bot.main*" -and $_.Name -notlike "*bash*" -and $_.Name -notlike "*powershell*" }
}

function Get-PythonPath {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "Could not find python.exe. Install Python 3.12 or add it to PATH."
}

function Test-ApiReachable {
    try {
        $null = Invoke-RestMethod -Uri $ApiUrl -TimeoutSec 3
        return $true
    } catch {
        return $false
    }
}

function Start-Game {
    <#
        Launches the game through Steam (not the exe directly, so Steam's own
        DRM/overlay/cloud-save handling stays intact) and waits for the mod's
        API to come up. The bot cannot do anything until that responds, so
        starting it earlier just burns retries.
    #>
    if (Get-Process -Name $GameProcessName -ErrorAction SilentlyContinue) {
        if (Test-ApiReachable) {
            Write-Host "Game already running and API is up." -ForegroundColor Green
            return $true
        }
        Write-Host "Game is running but the API is not responding yet..." -ForegroundColor Yellow
    } else {
        Write-Host "Launching Slay the Spire 2 via Steam..." -ForegroundColor Cyan
        Start-Process "steam://rungameid/$SteamAppId"
    }

    Write-Host "Waiting for the game's mod API (up to $GameWaitSeconds s)..." -ForegroundColor Cyan
    $deadline = (Get-Date).AddSeconds($GameWaitSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-ApiReachable) {
            Write-Host "Game API is up." -ForegroundColor Green
            return $true
        }
        Start-Sleep -Seconds 3
    }

    Write-Host "Timed out waiting for the game API." -ForegroundColor Red
    Write-Host "Check the game launched, and that mods are enabled in its settings." -ForegroundColor Red
    return $false
}

function Stop-Bot {
    $procs = @(Get-BotProcesses)
    if ($procs.Count -eq 0) {
        Write-Host "Bot is not running." -ForegroundColor Yellow
        return
    }
    foreach ($p in $procs) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    $left = @(Get-BotProcesses).Count
    if ($left -eq 0) {
        Write-Host "Stopped $($procs.Count) bot process(es)." -ForegroundColor Green
    } else {
        Write-Host "WARNING: $left bot process(es) still running." -ForegroundColor Red
    }
}

function Start-Bot {
    # Never leave a second bot running against the same game.
    $existing = @(Get-BotProcesses)
    if ($existing.Count -gt 0) {
        Write-Host "Stopping $($existing.Count) existing bot process(es) first..." -ForegroundColor Yellow
        Stop-Bot
    }

    if (-not $NoGame) {
        if (-not (Start-Game)) {
            Write-Host "Not starting the bot -- the game is not ready." -ForegroundColor Red
            return
        }
    }

    $python = Get-PythonPath
    $logDir = Join-Path $ProjectDir "logs"
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
    $stdout = Join-Path $logDir "bot_stdout.log"
    $stderr = Join-Path $logDir "bot_stderr.log"

    if ($Explore) {
        $env:STS2_EXPLORE_RELICS = "1"
        Write-Host "EXPLORE MODE: sampling under-tested relics; these runs are not performance runs." -ForegroundColor Magenta
    } else {
        $env:STS2_EXPLORE_RELICS = "0"
    }

    Start-Process -FilePath $python `
        -ArgumentList @("-m", "bot.main", "--max-actions", "$MaxActions") `
        -WorkingDirectory $ProjectDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr | Out-Null

    Start-Sleep -Seconds 3
    $running = @(Get-BotProcesses)
    if ($running.Count -eq 1) {
        Write-Host "Bot started (PID $($running[0].ProcessId))." -ForegroundColor Green
        Write-Host "Output: $stdout"
    } elseif ($running.Count -eq 0) {
        Write-Host "Bot failed to start. Check $stderr" -ForegroundColor Red
        if (Test-Path $stderr) { Get-Content $stderr -Tail 15 }
    } else {
        Write-Host "WARNING: $($running.Count) bot processes running." -ForegroundColor Red
    }
}

function Get-BotStatus {
    $procs = @(Get-BotProcesses)
    if ($procs.Count -eq 0) {
        Write-Host "Bot: STOPPED" -ForegroundColor Yellow
    } else {
        Write-Host "Bot: RUNNING ($($procs.Count) process(es))" -ForegroundColor Green
        foreach ($p in $procs) {
            Write-Host "  PID $($p.ProcessId)  started $($p.CreationDate)"
        }
        if ($procs.Count -gt 1) {
            Write-Host "  WARNING: more than one bot is running -- they will fight over the game." -ForegroundColor Red
            Write-Host "  Run stop_bot then start_bot." -ForegroundColor Red
        }
    }

    # Is the game's mod API reachable?
    try {
        $null = Invoke-RestMethod -Uri "http://localhost:15526/api/v1/singleplayer" -TimeoutSec 3
        Write-Host "Game API: reachable" -ForegroundColor Green
    } catch {
        Write-Host "Game API: NOT reachable (is Slay the Spire 2 running with mods enabled?)" -ForegroundColor Yellow
    }
}

switch ($Action) {
    "start"   { Start-Bot }
    "stop"    { Stop-Bot }
    "status"  { Get-BotStatus }
    "restart" { Stop-Bot; Start-Bot }
}
