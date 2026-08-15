#Requires -Version 5.1
<#
.SYNOPSIS
    Sets up the Python development environment for Cosmic Supremacy - Resurgence.

.DESCRIPTION
    1. Installs 'uv' (Astral's Python version + package manager) if not present.
    2. Installs the pinned CPython version from .python-version (managed by uv,
       so multiple versions can coexist without touching the system Python).
    3. Creates server\.venv and installs server\requirements.txt into it.
    4. Verifies that cs_server.py compiles and its imports resolve.

    Safe to re-run. Nothing is installed system-wide except uv itself, which
    lives in %USERPROFILE%\.local\bin.

.PARAMETER PythonVersion
    Override the Python version. Defaults to the contents of .python-version.

.PARAMETER Recreate
    Delete and rebuild the virtual environment from scratch.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup.ps1 -PythonVersion 3.13 -Recreate
#>
[CmdletBinding()]
param(
    [string]$PythonVersion,
    [switch]$Recreate
)

$ErrorActionPreference = 'Stop'

# ── Paths ─────────────────────────────────────────────────────────────────────
$RepoRoot   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ServerDir  = Join-Path $RepoRoot 'server'
$VenvDir    = Join-Path $ServerDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$Reqs       = Join-Path $ServerDir 'requirements.txt'
$PinFile    = Join-Path $RepoRoot '.python-version'

function Write-Step { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "    OK  $m" -ForegroundColor Green }
function Write-Warn2{ param($m) Write-Host "    !   $m" -ForegroundColor Yellow }

Write-Host "Cosmic Supremacy - Resurgence : environment setup" -ForegroundColor White
Write-Host "Repo: $RepoRoot"

# ── 0. Resolve the target Python version ──────────────────────────────────────
if (-not $PythonVersion) {
    if (Test-Path $PinFile) {
        $PythonVersion = (Get-Content $PinFile -Raw).Trim()
    } else {
        $PythonVersion = '3.12'
    }
}
Write-Host "Target Python: $PythonVersion"

# ── 1. Ensure uv is available ─────────────────────────────────────────────────
Write-Step "Checking for uv"

# The uv installer drops the binary here and edits the *user* PATH, which does
# not affect an already-running shell. Add it to this session's PATH up front.
$UvBin = Join-Path $env:USERPROFILE '.local\bin'
if ($env:PATH -notlike "*$UvBin*") { $env:PATH = "$UvBin;$env:PATH" }

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Warn2 "uv not found - installing from https://astral.sh/uv/install.ps1"
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Write-Host @"

Automatic install failed: $($_.Exception.Message)

Install uv manually with either of these, then re-run this script:

    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    winget install --id=astral-sh.uv -e

"@ -ForegroundColor Red
        exit 1
    }
    if ($env:PATH -notlike "*$UvBin*") { $env:PATH = "$UvBin;$env:PATH" }
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) {
        Write-Host "uv installed but not on PATH. Open a new terminal and re-run this script." -ForegroundColor Red
        exit 1
    }
}
Write-Ok "uv $(& uv --version)"

# ── 2. Install the pinned interpreter ─────────────────────────────────────────
Write-Step "Installing CPython $PythonVersion (uv-managed)"
& uv python install $PythonVersion
if ($LASTEXITCODE -ne 0) { throw "uv python install $PythonVersion failed" }
Write-Ok "CPython $PythonVersion available"
Write-Host "    (list all versions any time with: uv python list)"

# ── 3. Virtual environment ────────────────────────────────────────────────────
Write-Step "Preparing virtual environment at server\.venv"
if ($Recreate -and (Test-Path $VenvDir)) {
    Write-Warn2 "-Recreate specified, removing existing .venv"
    Remove-Item -Recurse -Force $VenvDir
}
if (-not (Test-Path $VenvPython)) {
    & uv venv --python $PythonVersion $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "uv venv failed" }
    Write-Ok "created"
} else {
    Write-Ok "already exists (use -Recreate to rebuild)"
}

# ── 4. Dependencies ───────────────────────────────────────────────────────────
Write-Step "Installing dependencies from server\requirements.txt"
if (Test-Path $Reqs) {
    & uv pip install --python $VenvPython -r $Reqs
    if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }
    Write-Ok "dependencies installed"
} else {
    Write-Warn2 "requirements.txt not found - skipping"
}

# ── 5. Verify ─────────────────────────────────────────────────────────────────
Write-Step "Verifying"
$ver = & $VenvPython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
Write-Ok "venv interpreter reports $ver"

& $VenvPython -c "import fastapi, uvicorn, bcrypt, jwt, aiosqlite; print('    OK  third-party imports resolve')"
if ($LASTEXITCODE -ne 0) { throw "import check failed" }

& $VenvPython -m py_compile (Join-Path $ServerDir 'cs_server.py')
if ($LASTEXITCODE -ne 0) { throw "cs_server.py failed to compile" }
Write-Ok "cs_server.py compiles"

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host @"

------------------------------------------------------------------
Setup complete.

Start the server:
    .\run_server.ps1

Or manually:
    server\.venv\Scripts\python.exe server\cs_server.py

The server listens on port 8888 (override with `$env:CSPORT).
Then drag a .csgalaxy file onto the patched client EXE.

Managing Python versions with uv:
    uv python list              show installed + available versions
    uv python install 3.13      add another version
    echo 3.13 > .python-version repin this repo, then .\setup.ps1 -Recreate
------------------------------------------------------------------
"@ -ForegroundColor White
