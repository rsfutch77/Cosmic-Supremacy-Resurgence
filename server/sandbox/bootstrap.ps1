<#
.SYNOPSIS
    Run the referee worker inside Windows Sandbox. Started by referee.wsb.

.DESCRIPTION
    Copies the read-only stage to a writable folder, points Google's client
    libraries at the key in C:\key when one is there, and runs the worker with
    its log, status file and operator page in C:\out, which the host can read.
    The worker is restarted after it exits, unless the stage was made with -Once.
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

    # The sandbox reaches the internet through the host, and a host on a VPN
    # carries less than the 1500-byte packets the sandbox sends (WireGuard is
    # 1380 or lower). TCP connects and then every TLS handshake stalls, because
    # the server's certificate chain is the first full-sized packet. 1280 is
    # the IPv6 minimum and fits inside any tunnel.
    Get-NetIPInterface -ConnectionState Connected |
        Where-Object { $_.InterfaceAlias -notlike 'Loopback*' } |
        ForEach-Object {
            Set-NetIPInterface -InterfaceIndex $_.ifIndex -AddressFamily $_.AddressFamily -NlMtuBytes 1280
            Say "mtu 1280 on $($_.InterfaceAlias) $($_.AddressFamily)"
        }
    & robocopy C:\stage\resurgence $Code /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copying the stage failed ($LASTEXITCODE)" }

    # Windows Sandbox boots with Smart App Control on, which refuses the
    # unsigned interpreter the stage runs and offers no per-program exception.
    # This sandbox runs nothing but the referee and is discarded when it
    # closes, and its isolation is the virtual machine, so the policy is
    # switched off for this boot. The host's setting is not touched.
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
