<#
.SYNOPSIS
    Starts the referee on the standby VM. Run by its logon task in the auto-logon session.

.DESCRIPTION
    Names this host for standby_check.py, starts the status push that copies
    C:\out to the bucket, and runs the same bootstrap.ps1 the sandbox runs.
#>
$env:CS_REFEREE_HOST = 'standby-vm'
$Meta = 'http://metadata.google.internal/computeMetadata/v1/instance/attributes'
$H = @{ 'Metadata-Flavor' = 'Google' }
$Bucket = Invoke-RestMethod -Headers $H "$Meta/standby-bucket"
$Prefix = Invoke-RestMethod -Headers $H "$Meta/standby-prefix"
$Python = 'C:\stage\resurgence\server\.venv\Scripts\python.exe'
$Tool = 'C:\stage\resurgence\server\standby\standby_vm.py'
Start-Process -WindowStyle Hidden $Python -ArgumentList @($Tool, 'push', '--bucket', $Bucket, '--prefix', $Prefix, '--out', 'C:\out')
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\stage\resurgence\server\sandbox\bootstrap.ps1
