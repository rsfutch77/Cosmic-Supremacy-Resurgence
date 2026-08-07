#Requires -Version 5.1
<#
.SYNOPSIS
    Starts the Cosmic Supremacy stub server using the project virtualenv.

.PARAMETER Port
    Port to listen on. Defaults to 8888 (the port the patched client expects).

.EXAMPLE
    .\run_server.ps1
    .\run_server.ps1 -Port 9000
#>
[CmdletBinding()]
param(
    [int]$Port = 8888
)

$ErrorActionPreference = 'Stop'

$RepoRoot   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ServerDir  = Join-Path $RepoRoot 'server'
$VenvPython = Join-Path $ServerDir '.venv\Scripts\python.exe'
$Entry      = Join-Path $ServerDir 'cs_server.py'

if (-not (Test-Path $VenvPython)) {
    Write-Host "No virtualenv found at server\.venv - run .\setup.ps1 first." -ForegroundColor Red
    exit 1
}

$env:CSPORT = $Port
Write-Host "Starting cs_server.py on port $Port  (Ctrl+C to stop)" -ForegroundColor Cyan
& $VenvPython $Entry
