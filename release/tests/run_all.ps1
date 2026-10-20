#Requires -Version 5.1
<#
.SYNOPSIS
    Pre-release smoke tests. Run before tagging.

.DESCRIPTION
    The checks, in rising cost:

      test_identity        The player's name: validation, storage, migration
                           of an older multiplayer.json. No game, no window.
      test_packaging       The multiplayer path survives freezing. No game,
                           no window.
      test_signing         The signing step in build.ps1. Signs a copy of a
                           small executable with a throwaway self-signed
                           certificate when signtool is installed. No game.
      test_ai_state_reset  The opponent's fog-of-war map and pass marker are
                           cleared on a new game and kept on a load. No game.
      test_save_protocol   The wire protocol to cs_server: slot allocation,
                           savegamelist format, blob round-trip. No game.
      test_save_message_path
                           The message save path names no injection
                           primitive and is off by default. No game.
      test_minimised_poll  The Galaxies page skips its timed listing while
                           minimised. No game, no network.
      test_tutorial_patch  The Tutorial and main-window-size patches the
                           build applies to the clients. No game.
      test_continue        Single Player Play continues the newest save,
                           New Game starts over, and a finished Tutorial is
                           marked. No game.
      test_turn_clock_hold Single Player turns happen only on Next Turn:
                           the engine's own clock is held and re-held
                           after each boundary. No game.
      test_status_cycle   Drives the real launcher and client for each
                           playable mode: launch, kill, status recovers.
      test_external_status Same, for a client started outside the launcher.

    The last two start and kill the actual game, so windows will open and close
    while they run. Do not use the machine during them.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\release\tests\run_all.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$TestsDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot = Split-Path -Parent (Split-Path -Parent $TestsDir)
$Py       = Join-Path $RepoRoot 'server\.venv\Scripts\python.exe'

if (-not (Test-Path $Py)) { throw "no venv at $Py - run .\setup.ps1 first" }

# A running launcher owns port 8888, and the tests would silently reuse its
# server.
$live = Get-Process CosmicSupremacyLauncher -ErrorAction SilentlyContinue
if ($live) { throw 'close the launcher before running the tests' }

$failed = @()

function Invoke-Test {
    param([string]$Script, [string[]]$TestArgs = @())
    $label = [IO.Path]::GetFileNameWithoutExtension($Script)
    if ($TestArgs.Count) { $label += " $($TestArgs -join ' ')" }
    Write-Host "`n==> $label" -ForegroundColor Cyan
    & $Py (Join-Path $TestsDir $Script) @TestArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    FAILED" -ForegroundColor Red
        $script:failed += $label
    }
    Get-Process CosmicSupremacy* -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
}

Invoke-Test 'test_identity.py'
Invoke-Test 'test_packaging.py'
Invoke-Test 'test_signing.py'
Invoke-Test 'test_build_and_log.py'
Invoke-Test 'test_ai_state_reset.py'
Invoke-Test 'test_save_protocol.py'
Invoke-Test 'test_save_message_path.py'
Invoke-Test 'test_minimised_poll.py'
Invoke-Test 'test_tutorial_patch.py'
Invoke-Test 'test_continue.py'
Invoke-Test 'test_turn_clock_hold.py'
foreach ($mode in @('tutorial', 'demo', 'testbed')) {
    Invoke-Test 'test_status_cycle.py' @($mode)
}
Invoke-Test 'test_external_status.py'

Write-Host ''
if ($failed.Count) {
    Write-Host "FAILED: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host 'All pre-release tests passed.' -ForegroundColor Green
