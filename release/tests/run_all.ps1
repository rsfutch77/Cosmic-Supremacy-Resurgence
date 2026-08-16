#Requires -Version 5.1
<#
.SYNOPSIS
    Pre-release smoke tests. Run before tagging.

.DESCRIPTION
    Three checks, in rising cost. Each is here because it caught a real bug:

      test_save_protocol   Speaks the wire protocol to cs_server directly. No
                           game needed, ~2 seconds. Covers slot allocation from
                           the gameid=-1 sentinel, the savegamelist format, and
                           the blob round-trip.
      test_status_cycle    Drives the real launcher window and the real client
                           for each playable mode: click, confirm it reports the
                           game running, kill it, confirm the status recovers.
      test_external_status Same, for a client started OUTSIDE the launcher,
                           which has no child handle to watch.

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

# A launcher already running owns port 8888, so the tests would silently take
# the "reusing the existing server" path instead of exercising their own.
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

Invoke-Test 'test_save_protocol.py'
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
