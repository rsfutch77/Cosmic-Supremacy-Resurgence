<#
.SYNOPSIS
    Run the referee worker inside Windows Sandbox. Started by referee.wsb.

.DESCRIPTION
    Copies the read-only stage to a writable folder, points Google's client
    libraries at the key in C:\key when one is there, and runs the worker with
    its log, status file and operator page in C:\out, which the host can read.
    The worker is restarted after it exits, unless the stage was made with -Once.
    The operator page opens in the sandbox's Edge once the worker writes it.
#>
$ErrorActionPreference = 'Stop'

$Work = 'C:\referee'
$Code = Join-Path $Work 'resurgence'
$Out  = 'C:\out'
$Boot = Join-Path $Out 'bootstrap.log'

function Say($msg) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Encoding utf8 $Boot $line
}

try {
    Say "sandbox up, stage commit $(Get-Content C:\stage\commit.txt)"

    # A host on a VPN carries less than 1500-byte packets, so TCP connects and
    # then TLS handshakes stall. 1280 (the IPv6 minimum) fits inside any tunnel.
    Get-NetIPInterface -ConnectionState Connected |
        Where-Object { $_.InterfaceAlias -notlike 'Loopback*' } |
        ForEach-Object {
            Set-NetIPInterface -InterfaceIndex $_.ifIndex -AddressFamily $_.AddressFamily -NlMtuBytes 1280
            Say "mtu 1280 on $($_.InterfaceAlias) $($_.AddressFamily)"
        }
    & robocopy C:\stage\resurgence $Code /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copying the stage failed ($LASTEXITCODE)" }

    # Smart App Control refuses the unsigned interpreter, so it is switched off
    # inside this disposable sandbox. The host's setting is not touched.
    $Ci = 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy'
    Set-ItemProperty -Path $Ci -Name VerifiedAndReputablePolicyState -Value 0 -Type DWord
    $Refresh = (& CiTool.exe --refresh --json 2>&1 | Out-String).Trim()
    Say "smart app control: off for this sandbox, state $((Get-ItemProperty $Ci).VerifiedAndReputablePolicyState), refresh $Refresh"

    $Config = Get-Content C:\stage\config.json -Raw | ConvertFrom-Json
    $Store = $Config.store
    if ($Config.seed) {
        $Store = Join-Path $Work 'galaxy'
        & robocopy (Join-Path C:\stage $Config.seed) $Store /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "copying the seed galaxy failed ($LASTEXITCODE)" }
    }

    $Key = Get-ChildItem C:\key -Filter '*.json' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Key) {
        $env:GOOGLE_APPLICATION_CREDENTIALS = $Key.FullName
        Say "credential: $($Key.Name)"
    } else {
        Say 'credential: none'
    }

    if ($Config.bucket) { $env:FIREBASE_STORAGE_BUCKET = $Config.bucket }

    $Python = Join-Path $Code 'server\.venv\Scripts\python.exe'
    $Entry  = Join-Path $Code 'server\referee_worker.py'
    $WorkerArgs = @('--store', $Store, '--work-dir', $Out)
    if ($Config.once) { $WorkerArgs += '--once' }

    # Windows PowerShell turns each stderr line of a native program into an
    # error record, which 'Stop' would make fatal to the loop.
    $ErrorActionPreference = 'Continue'

    # The operator page opens in the sandbox's Edge once this start has written
    # it, or after PageWait with whatever page is there, which then shows its
    # own STALE banner. The meta refresh keeps it current.
    $Page = Join-Path $Out 'operator.html'
    $PageWait = 180   # seconds to wait for the worker's startup page
    $EdgePolicy = 'HKLM:\SOFTWARE\Policies\Microsoft\Edge'
    New-Item -Path $EdgePolicy -Force | Out-Null
    Set-ItemProperty -Path $EdgePolicy -Name HideFirstRunExperience -Value 1 -Type DWord
    Start-Job -ArgumentList $Page, (Get-Date), $PageWait -ScriptBlock {
        param($Page, $Since, $Wait)
        $deadline = (Get-Date).AddSeconds($Wait)
        while ((Get-Date) -lt $deadline -and
               -not ((Test-Path $Page) -and (Get-Item $Page).LastWriteTime -ge $Since)) {
            Start-Sleep -Seconds 2
        }
        if (Test-Path $Page) { Start-Process msedge.exe -ArgumentList '--no-first-run', $Page }
    } | Out-Null
    Say "operator page: opens in Edge when written, at most ${PageWait}s"

    Say 'preflight'
    & $Python (Join-Path $Code 'server\sandbox\preflight.py') $Store 2>&1 |
        Out-File -Append -Encoding utf8 (Join-Path $Out 'preflight.log')
    Say "preflight exited with $LASTEXITCODE"

    while ($true) {
        Say "worker: $Python $Entry $($WorkerArgs -join ' ')"
        & $Python $Entry @WorkerArgs 2>&1 | Out-File -Append -Encoding utf8 (Join-Path $Out 'worker_console.log')
        Say "worker exited with $LASTEXITCODE"
        if ($Config.once) { break }
        Start-Sleep -Seconds 30
    }
} catch {
    Say "bootstrap failed: $_"
}
