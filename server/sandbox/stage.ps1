<#
.SYNOPSIS
    Stage the referee for Windows Sandbox and write the .wsb that runs it.

.DESCRIPTION
    The referee loads player-supplied blobs into a closed-source client. Inside
    Windows Sandbox, a blob that crashes or subverts that client reaches a
    disposable VM that holds only what is mapped into it:

      <Root>\stage   read-only   the code the referee runs, copied from this checkout
      <Root>\key     read-only   a service account key, and nothing else
      <Root>\out     read-write  the worker log, status file and operator page
      Python home    read-only   the interpreter server\.venv was built on

    The sandbox runs a snapshot of the checkout, not the checkout itself, so a
    code change reaches the referee only when this script is run again. Nothing
    on the host executes anything from <Root>\out.

    With no key in <Root>\key the worker uses no Google credential, which serves
    a folder store and fails a firebase:// one.

.PARAMETER Store
    The galaxy, as the worker's --store takes it. A folder path is read inside the
    sandbox, so a folder galaxy is given with -Seed.

.PARAMETER Seed
    A folder galaxy on the host, copied into the stage and from there to a
    writable folder inside the sandbox at every start. -Store is then ignored.
    For testing the sandbox without a credential.

.PARAMETER KeyFile
    A service account key to copy into <Root>\key. Omit to keep the key already
    there.

.PARAMETER Bucket
    The galaxy's Storage bucket. The worker otherwise finds it by asking the
    project which default bucket name it has, which a service account limited
    to objects in one bucket is not allowed to ask.

.PARAMETER Once
    The worker makes one pass and the sandbox stays open for inspection.

.EXAMPLE
    .\server\sandbox\stage.ps1 -Store firebase://cs-resurgence/h3check -Bucket cs-resurgence.firebasestorage.app -KeyFile C:\keys\referee.json
    .\server\sandbox\stage.ps1 -Seed .\server\galaxy_demo -Once
#>
param(
    [string]$Store,
    [string]$Seed,
    [string]$KeyFile,
    [string]$Bucket,
    [string]$Root = 'C:\CSReferee',
    [switch]$Once
)

$ErrorActionPreference = 'Stop'

$Repo   = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Stage  = Join-Path $Root 'stage'
$Code   = Join-Path $Stage 'resurgence'
$KeyDir = Join-Path $Root 'key'
$Out    = Join-Path $Root 'out'

if (-not $Store -and -not $Seed) {
    Write-Host '-Store or -Seed is required.' -ForegroundColor Red
    exit 2
}

$Cfg = Get-Content (Join-Path $Repo 'server\.venv\pyvenv.cfg')
$PyHome = (($Cfg | Where-Object { $_ -match '^home\s*=' }) -split '=', 2)[1].Trim()
if (-not (Test-Path $PyHome)) {
    Write-Host "The venv's Python home $PyHome does not exist." -ForegroundColor Red
    exit 2
}

foreach ($d in $Stage, $Code, $KeyDir, $Out) {
    New-Item -ItemType Directory -Force $d | Out-Null
}

# robocopy exit codes below 8 are success.
function Copy-Tree($From, $To, $ExtraArgs) {
    & robocopy $From $To /MIR /NFL /NDL /NJH /NJS /NP @ExtraArgs | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy $From -> $To failed ($LASTEXITCODE)" }
}

# What the worker imports and launches, and none of the checkout's game state:
# captures, tick inputs, logs and crash dumps stay on the host.
$Skip = @('/XD', '__pycache__', 'saves', 'worker_work', 'referee_work', 'join_work',
          'galaxy_demo', 'uidemo', 'snapshots', 'checkpoints', 'state', 'tests',
          '/XF', '*.log', '*.dmp', '*.dat', '*.b64', '*.sqlite3', '*.bak')
Copy-Tree (Join-Path $Repo 'server') (Join-Path $Code 'server') $Skip
Copy-Tree (Join-Path $Repo 'client\dev_tools') (Join-Path $Code 'client\dev_tools') $Skip
Get-ChildItem (Join-Path $Repo 'client') -Filter '*.exe' |
    Copy-Item -Destination (New-Item -ItemType Directory -Force (Join-Path $Code 'client'))

$SeedName = ''
if ($Seed) {
    $SeedName = 'seed_galaxy'
    Copy-Tree (Resolve-Path $Seed).Path (Join-Path $Stage $SeedName) @()
} elseif (Test-Path (Join-Path $Stage 'seed_galaxy')) {
    Remove-Item -Recurse -Force (Join-Path $Stage 'seed_galaxy')
}

if ($KeyFile) {
    Get-ChildItem $KeyDir -Filter '*.json' | Remove-Item -Force
    Copy-Item $KeyFile (Join-Path $KeyDir 'referee.json')
}

$Config = [ordered]@{ store = $Store; seed = $SeedName; bucket = $Bucket; once = [bool]$Once }
$Config | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $Stage 'config.json')

$Commit = (& git -C $Repo rev-parse --short HEAD 2>$null)
Set-Content -Encoding utf8 (Join-Path $Stage 'commit.txt') $Commit

function Map($HostDir, $SandboxDir, $ReadOnly) {
    @"
    <MappedFolder>
      <HostFolder>$HostDir</HostFolder>
      <SandboxFolder>$SandboxDir</SandboxFolder>
      <ReadOnly>$(if ($ReadOnly) { 'true' } else { 'false' })</ReadOnly>
    </MappedFolder>
"@
}

$Wsb = @"
<Configuration>
  <VGpu>Enable</VGpu>
  <Networking>Enable</Networking>
  <MemoryInMB>4096</MemoryInMB>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <PrinterRedirection>Disable</PrinterRedirection>
  <AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput>
  <MappedFolders>
$(Map $Stage 'C:\stage' $true)
$(Map $KeyDir 'C:\key' $true)
$(Map $Out 'C:\out' $false)
$(Map $PyHome $PyHome $true)
  </MappedFolders>
  <LogonCommand>
    <Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\stage\resurgence\server\sandbox\bootstrap.ps1</Command>
  </LogonCommand>
</Configuration>
"@
$WsbPath = Join-Path $Root 'referee.wsb'
Set-Content -Encoding utf8 $WsbPath $Wsb

Write-Host "Staged $Commit into $Stage" -ForegroundColor Cyan
Write-Host "  store: $(if ($Seed) { "seed $Seed" } else { $Store })"
Write-Host "  key:   $(if (Get-ChildItem $KeyDir -Filter '*.json') { 'present' } else { 'none' })"
Write-Host "  out:   $Out"
Write-Host "Start it with: WindowsSandbox.exe $WsbPath"
