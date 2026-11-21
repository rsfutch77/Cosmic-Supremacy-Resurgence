<#
.SYNOPSIS
    The standby VM's startup script. Runs as SYSTEM at every boot.

.DESCRIPTION
    First boot: downloads the bundle `standby_vm.py prepare` uploaded, using the
    VM's attached service account, lays it out the way referee.wsb maps the
    sandbox (C:\stage read by bootstrap.ps1, C:\key empty, C:\out for logs),
    makes an auto-logon user and a logon task, and reboots. The game client needs
    a desktop session, so the referee starts at logon, not here.

    Later boots do nothing: auto-logon starts the logon task again.
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'    # Invoke-WebRequest crawls with a progress bar
$Done = 'C:\standby_setup.done'
function Say($m) { Write-Host "STANDBY $(Get-Date -Format s) $m" }

if (Test-Path $Done) { Say 'set up already; auto-logon starts the referee'; exit 0 }

$Meta = 'http://metadata.google.internal/computeMetadata/v1'
$H = @{ 'Metadata-Flavor' = 'Google' }
$Bucket = Invoke-RestMethod -Headers $H "$Meta/instance/attributes/standby-bucket"
$Prefix = Invoke-RestMethod -Headers $H "$Meta/instance/attributes/standby-prefix"
$Token = (Invoke-RestMethod -Headers $H "$Meta/instance/service-accounts/default/token").access_token
$Object = [uri]::EscapeDataString("$Prefix/bundle.zip")
Say "downloading gs://$Bucket/$Prefix/bundle.zip"
Invoke-WebRequest -UseBasicParsing -Headers @{ Authorization = "Bearer $Token" } `
    "https://storage.googleapis.com/storage/v1/b/$Bucket/o/$Object`?alt=media" -OutFile C:\bundle.zip
Say "downloaded $((Get-Item C:\bundle.zip).Length) bytes"
Expand-Archive C:\bundle.zip C:\bundle -Force
Move-Item C:\bundle\stage C:\stage
New-Item -ItemType Directory -Force C:\key, C:\out | Out-Null
# The staged venv names this interpreter home, as on the machine that staged it.
$PyHome = (Get-Content C:\bundle\python_home.txt -Raw).Trim()
New-Item -ItemType Directory -Force (Split-Path $PyHome) | Out-Null
Move-Item C:\bundle\python $PyHome
Remove-Item -Recurse -Force C:\bundle, C:\bundle.zip
Say "stage commit $(Get-Content C:\stage\commit.txt)"

$User = 'referee'
$Pw = (-join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })) + '!a9'
net user $User $Pw /add /y | Out-Null
net localgroup Administrators $User /add | Out-Null
$Wl = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty $Wl AutoAdminLogon '1'
Set-ItemProperty $Wl DefaultUserName $User
Set-ItemProperty $Wl DefaultPassword $Pw
Set-ItemProperty $Wl DefaultDomainName $env:COMPUTERNAME

$Logon = 'C:\stage\resurgence\server\standby\vm_logon.ps1'
$Act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File $Logon"
$Trg = New-ScheduledTaskTrigger -AtLogOn -User "$env:COMPUTERNAME\$User"
$Pri = New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\$User" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName 'CosmicSupremacy Referee Standby' -Action $Act -Trigger $Trg -Principal $Pri | Out-Null
Set-Content $Done (Get-Date -Format s)
Say 'set up; rebooting into the auto-logon session'
Restart-Computer -Force
