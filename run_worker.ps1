#Requires -Version 5.1
<#
.SYNOPSIS
    Runs the referee over one galaxy, unattended, using the project virtualenv.

.DESCRIPTION
    The command the scheduled task runs, and the command to run by hand to see
    what the scheduled task would do. It starts server\referee_worker.py, which
    closes an overdue turn at startup, then closes each turn as its deadline
    passes, keeps a cs_server on 8888 for the capture to arrive on, asks Windows
    not to sleep, and refuses to be a second worker on a galaxy that has one.

    run_server.ps1 is the precedent for how this finds Python. Nothing here
    needs an elevated shell.

.PARAMETER Store
    The galaxy: a directory, the base URL of a turn_server.py, or a
    firebase://project/galaxy spec.

.PARAMETER DataDir
    What cs_server.py is given as CS_DATA_DIR. Captures arrive in its saves
    subdirectory, and the referee collects them from there. Defaults to the
    checkout's server directory, which is where cs_server writes when it is
    started by hand.

.PARAMETER SaveDir
    Where captures are collected from, if that is not <DataDir>\saves.

.PARAMETER NoCsServer
    Do not start a stub server and do not refuse one that is already there. For
    an operator who is running their own. The referee still checks the port
    before it spends a turn, so this gives up the early refusal, not the turn.

.PARAMETER AdoptServer
    Use a stub server that is already on the port. Only meaningful with a
    -DataDir or -SaveDir naming where that server writes, since nothing in the
    protocol can ask it.

.PARAMETER Once
    Close at most one turn and exit.

.PARAMETER Status
    Print what the worker for this galaxy last wrote about itself, and exit.
    Reads a file; it is not proof that a worker is running.

.PARAMETER WriteTaskXml
    Write a filled-in copy of referee_worker_task.xml to this path and print the
    schtasks command that would register it. It registers nothing.

.PARAMETER TaskUser
    The account the task runs as, DOMAIN\user. Defaults to the current user.
    With -WriteTaskXml only.

.EXAMPLE
    .\run_worker.ps1 -Store C:\galaxies\sandbox
    .\run_worker.ps1 -Store C:\galaxies\sandbox -Once
    .\run_worker.ps1 -Store C:\galaxies\sandbox -Status
    .\run_worker.ps1 -Store C:\galaxies\sandbox -WriteTaskXml .\referee_task.xml
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Store,
    [string]$DataDir,
    [string]$SaveDir,
    [switch]$NoCsServer,
    [switch]$AdoptServer,
    [switch]$Once,
    [switch]$Status,
    [string]$WriteTaskXml,
    [string]$TaskUser
)

$ErrorActionPreference = 'Stop'

$RepoRoot   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ServerDir  = Join-Path $RepoRoot 'server'
$VenvPython = Join-Path $ServerDir '.venv\Scripts\python.exe'
$Entry      = Join-Path $ServerDir 'referee_worker.py'
$Template   = Join-Path $RepoRoot 'referee_worker_task.xml'
$ThisScript = $MyInvocation.MyCommand.Definition

if (-not (Test-Path $VenvPython)) {
    Write-Host "No virtualenv found at server\.venv - run .\setup.ps1 first." -ForegroundColor Red
    exit 1
}

# The arguments are built once and used for both jobs: running the worker now,
# and writing the task that will run it later. A task whose command line has
# drifted from the one the operator tested is a task nobody can reason about.
$WorkerArgs = @('--store', $Store)
if ($DataDir)     { $WorkerArgs += @('--data-dir', $DataDir) }
if ($SaveDir)     { $WorkerArgs += @('--save-dir', $SaveDir) }
if ($NoCsServer)  { $WorkerArgs += '--no-cs-server' }
if ($AdoptServer) { $WorkerArgs += '--adopt-server' }
if ($Once)        { $WorkerArgs += '--once' }

if ($WriteTaskXml) {
    if (-not (Test-Path $Template)) {
        Write-Host "No task template at $Template" -ForegroundColor Red
        exit 1
    }
    if (-not $TaskUser) { $TaskUser = "$env:USERDOMAIN\$env:USERNAME" }

    # The task runs this script rather than python directly, so that what the
    # machine does at 3am is the same command an operator can type.
    $Pass = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle',
              'Minimized', '-File', ('"{0}"' -f $ThisScript), '-Store',
              ('"{0}"' -f $Store))
    if ($DataDir)     { $Pass += @('-DataDir', ('"{0}"' -f $DataDir)) }
    if ($SaveDir)     { $Pass += @('-SaveDir', ('"{0}"' -f $SaveDir)) }
    if ($NoCsServer)  { $Pass += '-NoCsServer' }
    if ($AdoptServer) { $Pass += '-AdoptServer' }

    # Escaped as XML text rather than pasted in. A store path holding an
    # ampersand would otherwise produce a file Task Scheduler refuses, and the
    # message it gives says nothing about which character did it.
    function Esc([string]$s) {
        return [System.Security.SecurityElement]::Escape($s)
    }

    $xml = Get-Content -Raw -Encoding UTF8 $Template
    $xml = $xml.Replace('{{USERID}}', (Esc $TaskUser))
    $xml = $xml.Replace('{{ARGUMENTS}}', (Esc ($Pass -join ' ')))
    $xml = $xml.Replace('{{WORKINGDIRECTORY}}', (Esc $RepoRoot))

    $out = [System.IO.Path]::GetFullPath(
        [System.IO.Path]::Combine((Get-Location).Path, $WriteTaskXml))

    # Written as UTF-16, which is what Task Scheduler's own export produces and
    # what it will accept. The template is UTF-8 so that it can be read and
    # reviewed as text in the repository; handing those bytes to schtasks fails
    # with "unable to switch the encoding", which names neither the file nor
    # the cause. Measured through the Schedule.Service COM object, which
    # refuses the same file for the same reason.
    $xml = $xml.Replace('<?xml version="1.0" encoding="UTF-8"?>',
                        '<?xml version="1.0" encoding="UTF-16"?>')
    [System.IO.File]::WriteAllText($out, $xml,
        (New-Object System.Text.UnicodeEncoding($false, $true)))

    Write-Host "Wrote $out" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Nothing has been registered. To register it, run:" -ForegroundColor Cyan
    Write-Host "    schtasks /Create /TN `"CosmicSupremacy Referee`" /XML `"$out`""
    Write-Host ""
    Write-Host "Then, to start it now without waiting for a logon:" -ForegroundColor Cyan
    Write-Host "    schtasks /Run /TN `"CosmicSupremacy Referee`""
    Write-Host ""
    Write-Host "The task starts at logon, not at boot: computing a turn launches" -ForegroundColor Yellow
    Write-Host "the game client, which needs a desktop, and a task that runs" -ForegroundColor Yellow
    Write-Host "whether or not a user is logged on has none. For a reboot to" -ForegroundColor Yellow
    Write-Host "reach the galaxy with nobody there, set this account to log on" -ForegroundColor Yellow
    Write-Host "automatically: control userpasswords2." -ForegroundColor Yellow
    exit 0
}

if ($Status) {
    & $VenvPython $Entry '--store' $Store '--status'
    exit $LASTEXITCODE
}

Write-Host "Referee worker on $Store  (Ctrl+C to stop)" -ForegroundColor Cyan
& $VenvPython $Entry @WorkerArgs
exit $LASTEXITCODE
