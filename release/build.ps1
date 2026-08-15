#Requires -Version 5.1
<#
.SYNOPSIS
    Builds the player-facing release: freezes the launcher and assembles the
    folder that gets zipped and attached to a GitHub release.

.DESCRIPTION
    Output lands in dist\ (gitignored) so the 8 MB client binaries are never
    committed twice. The repo keeps sources; this script produces artifacts.

        dist\CosmicSupremacy-Resurgence-v<version>\
            CosmicSupremacyLauncher.exe
            README.txt
            game\CosmicSupremacy.exe, CosmicSupremacy_TestBed.exe
            game\galaxies\*.csgalaxy
        dist\CosmicSupremacy-Resurgence-v<version>.zip

    Which client EXEs and galaxy files get copied is read from manifest.json,
    not hardcoded here — every mode with "show": true contributes its pair. When
    the three EXEs are reconciled into one, this script needs no edit.

    Build dependencies (PyInstaller) go in release\.venv-build, kept separate
    from server\.venv so a build never perturbs the dev environment.

.PARAMETER Version
    Override the version. Defaults to the "version" field in manifest.json.

.PARAMETER Clean
    Delete build\ and this version's dist\ output before building.

.PARAMETER SkipZip
    Assemble the folder but do not produce the .zip.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\release\build.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\release\build.ps1 -Version 0.2.0 -Clean
#>
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$Clean,
    [switch]$SkipZip
)

$ErrorActionPreference = 'Stop'

# ── Paths ─────────────────────────────────────────────────────────────────────
$ReleaseDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot   = Split-Path -Parent $ReleaseDir
$ClientDir  = Join-Path $RepoRoot 'client'
$ServerDir  = Join-Path $RepoRoot 'server'
$BuildDir   = Join-Path $ReleaseDir 'build'
$DistRoot   = Join-Path $RepoRoot 'dist'
$Manifest   = Join-Path $ReleaseDir 'manifest.json'
$VenvDir    = Join-Path $ReleaseDir '.venv-build'
$VenvPy     = Join-Path $VenvDir 'Scripts\python.exe'
$PinFile    = Join-Path $RepoRoot '.python-version'

function Write-Step { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "    OK  $m" -ForegroundColor Green }
function Write-Warn2{ param($m) Write-Host "    !   $m" -ForegroundColor Yellow }

Write-Host "Cosmic Supremacy - Resurgence : release build" -ForegroundColor White

# ── 0. Manifest and version ───────────────────────────────────────────────────
if (-not (Test-Path $Manifest)) { throw "manifest not found: $Manifest" }
$cfg = Get-Content $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $Version) { $Version = $cfg.version }

$StageName = "CosmicSupremacy-Resurgence-v$Version"
$Stage     = Join-Path $DistRoot $StageName
$GameDir   = Join-Path $Stage 'game'
$GalaxyDir = Join-Path $GameDir 'galaxies'

Write-Host "Version: $Version"
Write-Host "Output : $Stage"

$modes = @($cfg.modes | Where-Object { $_.show })
if ($modes.Count -eq 0) { throw 'manifest has no modes with "show": true' }
Write-Host "Modes  : $(($modes | ForEach-Object { $_.id }) -join ', ')"

# ── 1. Clean ──────────────────────────────────────────────────────────────────
if ($Clean) {
    Write-Step "Cleaning"
    foreach ($p in @($BuildDir, $Stage, "$Stage.zip")) {
        if (Test-Path $p) { Remove-Item -Recurse -Force $p; Write-Ok "removed $p" }
    }
}

# ── 2. Verify every source file the manifest asks for ─────────────────────────
# Done before the venv work so a missing binary fails in two seconds rather than
# after a PyInstaller run.
Write-Step "Checking source files"
$missing = @()
foreach ($m in $modes) {
    foreach ($f in @((Join-Path $ClientDir $m.exe), (Join-Path $ClientDir $m.galaxy))) {
        if (-not (Test-Path $f)) { $missing += $f }
    }
}
if ($missing.Count -gt 0) {
    $missing | ForEach-Object { Write-Host "    missing: $_" -ForegroundColor Red }
    throw "manifest references files that are not in client\"
}
Write-Ok "all client files present"

# ── 3. Build virtualenv ───────────────────────────────────────────────────────
Write-Step "Preparing build environment"

$UvBin = Join-Path $env:USERPROFILE '.local\bin'
if ($env:PATH -notlike "*$UvBin*") { $env:PATH = "$UvBin;$env:PATH" }
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) { throw "uv not found on PATH - run .\setup.ps1 first" }

if (-not (Test-Path $VenvPy)) {
    $pyVer = '3.12'
    if (Test-Path $PinFile) { $pyVer = (Get-Content $PinFile -Raw).Trim() }
    & uv venv --python $pyVer $VenvDir
    if ($LASTEXITCODE -ne 0) { throw 'uv venv failed' }
    Write-Ok "created release\.venv-build"
} else {
    Write-Ok "release\.venv-build already exists"
}

& uv pip install --python $VenvPy --quiet pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'pyinstaller install failed' }
$piVer = & $VenvPy -c "import PyInstaller; print(PyInstaller.__version__)"
Write-Ok "PyInstaller $piVer"

# ── 4. Icon ───────────────────────────────────────────────────────────────────
# Optional: a build without an icon is fine, a build that dies over one is not.
Write-Step "Extracting application icon"
$IconOut = Join-Path $BuildDir 'cosmic.ico'
$IconSrc = Join-Path $ClientDir 'CosmicSupremacy.exe'
& $VenvPy (Join-Path $ReleaseDir 'extract_icon.py') $IconSrc $IconOut
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $IconOut)) {
    Write-Warn2 'icon extraction failed - building with the default icon'
    $IconOut = $null
} else {
    Write-Ok "icon written to $IconOut"
}

# ── 5. Create the release folder ──────────────────────────────────────────────
# Before freezing, not after, so PyInstaller can emit the executable straight
# into its final home. Building it somewhere else first and copying leaves a
# second, runnable launcher in the build tree with no game\ folder beside it —
# which looks exactly like the real thing, fails with "Game files not found",
# and is the first thing anyone double-clicks.
Write-Step "Preparing $StageName"

# A launcher still running holds its own exe and data\ open, so the staging
# wipe below fails on a file lock. Say why rather than surfacing a bare
# access-denied several lines later.
$live = Get-Process CosmicSupremacyLauncher -ErrorAction SilentlyContinue
if ($live) {
    throw ("a launcher is still running (pid " + (($live | ForEach-Object { $_.Id }) -join ', ') +
           ") and holds the files this build must overwrite - close it and re-run")
}

# Earlier versions of this script built to build\exe and copied from there. Any
# leftover copy is a fully runnable launcher with no game\ folder beside it,
# which fails with "Game files not found" - remove the trap.
$LegacyExeDir = Join-Path $BuildDir 'exe'
if (Test-Path $LegacyExeDir) {
    Remove-Item -Recurse -Force $LegacyExeDir
    Write-Ok "removed stale $LegacyExeDir"
}

if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force $GalaxyDir | Out-Null
Write-Ok "created $Stage"

# ── 6. Freeze the launcher ────────────────────────────────────────────────────
Write-Step "Freezing launcher (PyInstaller)"
$piArgs = @(
    '--noconfirm', '--onefile', '--windowed',
    '--name', 'CosmicSupremacyLauncher',
    # manifest.json rides inside the exe: it is the build's own definition of
    # what the modes are, not something a player is meant to retune.
    '--add-data', "$Manifest;.",
    # cs_server is imported inside a function after its environment is set, so
    # name it explicitly rather than relying on the import graph walker.
    '--paths', $ServerDir,
    '--hidden-import', 'cs_server',
    '--distpath', $Stage,
    '--workpath', (Join-Path $BuildDir 'work'),
    '--specpath', $BuildDir
)
if ($IconOut) {
    # --icon sets the icon Explorer draws on the EXE. The taskbar button and the
    # window title bar come from the *window* icon instead, which tkinter
    # defaults to its own feather - so the .ico also ships as data for the
    # launcher to hand to iconbitmap() at runtime.
    $piArgs += @('--icon', $IconOut, '--add-data', "$IconOut;.")
}
$piArgs += (Join-Path $ReleaseDir 'launcher.py')

& $VenvPy -m PyInstaller @piArgs
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

$BuiltExe = Join-Path $Stage 'CosmicSupremacyLauncher.exe'
if (-not (Test-Path $BuiltExe)) { throw "PyInstaller reported success but $BuiltExe is missing" }
Write-Ok "built $([math]::Round((Get-Item $BuiltExe).Length / 1MB, 1)) MB"

# ── 7. Add the game files ─────────────────────────────────────────────────────
Write-Step "Adding game files"
Copy-Item (Join-Path $ReleaseDir 'PLAYER_README.txt') (Join-Path $Stage 'README.txt')

# Several modes share one EXE, so copy the distinct set rather than per mode.
$copied = @{}
foreach ($m in $modes) {
    if (-not $copied.ContainsKey($m.exe)) {
        Copy-Item (Join-Path $ClientDir $m.exe) (Join-Path $GameDir $m.exe)
        $copied[$m.exe] = $true
        Write-Ok "game\$($m.exe)"
    }
    if (-not $copied.ContainsKey($m.galaxy)) {
        Copy-Item (Join-Path $ClientDir $m.galaxy) (Join-Path $GalaxyDir $m.galaxy)
        $copied[$m.galaxy] = $true
        Write-Ok "game\galaxies\$($m.galaxy)"
    }
}

# ── 8. Zip ────────────────────────────────────────────────────────────────────
if (-not $SkipZip) {
    Write-Step "Creating archive"
    $Zip = "$Stage.zip"
    if (Test-Path $Zip) { Remove-Item -Force $Zip }
    Compress-Archive -Path $Stage -DestinationPath $Zip -CompressionLevel Optimal
    Write-Ok "$Zip  ($([math]::Round((Get-Item $Zip).Length / 1MB, 1)) MB)"
}

# ── Done ──────────────────────────────────────────────────────────────────────
$total = (Get-ChildItem $Stage -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host @"

------------------------------------------------------------------
Release built: $StageName  ($([math]::Round($total / 1MB, 1)) MB unpacked)

Test it the way a player would - from the staged folder, not the repo:
    $Stage\CosmicSupremacyLauncher.exe

Then attach the .zip to a GitHub release and tag it v$Version.
------------------------------------------------------------------
"@ -ForegroundColor White
