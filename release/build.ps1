#Requires -Version 5.1
<#
.SYNOPSIS
    Builds the player-facing release: freezes the launcher and assembles the
    folder that gets zipped and attached to a GitHub release.

.DESCRIPTION
    Output lands in dist\ (gitignored) so the 8 MB client binaries are never
    committed twice. The repo keeps sources; this script produces artifacts.

    Build dependencies (PyInstaller) go in release\.venv-build, kept separate
    from server\.venv so a build never perturbs the dev environment.

.PARAMETER Version
    Override the version. Defaults to the "version" field in manifest.json.

.PARAMETER Clean
    Delete build\ and this version's dist\ output before building.

.PARAMETER SkipZip
    Assemble the folder but do not produce the .zip.

.PARAMETER SignCertThumbprint
    SHA-1 thumbprint of a code signing certificate in Cert:\CurrentUser\My.
    Defaults to the CS_SIGN_CERT_THUMBPRINT environment variable. With neither
    set the build is unsigned, says so, and carries on. With one set,
    CosmicSupremacyLauncher.exe and CosmicSupremacyAI.exe are signed with
    signtool (SHA-256, RFC 3161 timestamp) right after each is frozen, and the
    build fails if either cannot be signed or the signature does not read back.
    The Artifact Signing parameters below are the other signer.

.PARAMETER SignTimestampUrl
    RFC 3161 timestamp server. Defaults to CS_SIGN_TIMESTAMP_URL, then to
    http://timestamp.digicert.com for a certificate in the store, or
    http://timestamp.acs.microsoft.com for Azure Artifact Signing.

.PARAMETER SignToolPath
    Full path to signtool.exe. Defaults to CS_SIGNTOOL, then PATH, then the
    newest copy in an installed Windows SDK or the NuGet package cache.

.PARAMETER SignArtifactEndpoint
    Azure Artifact Signing: the regional endpoint of the account, for example
    https://eus.codesigning.azure.net. Defaults to CS_SIGN_AS_ENDPOINT. Setting
    this, -SignArtifactAccount and -SignArtifactProfile selects Artifact
    Signing, which is signtool with Azure.CodeSigning.Dlib.dll and a metadata
    file written to release\build. It cannot be combined with
    -SignCertThumbprint. The dlib authenticates as the signed-in Azure
    identity (Azure CLI login or the AZURE_TENANT_ID, AZURE_CLIENT_ID and
    AZURE_CLIENT_SECRET environment variables); no secret is kept here.

.PARAMETER SignArtifactAccount
    Azure Artifact Signing account name. Defaults to CS_SIGN_AS_ACCOUNT.

.PARAMETER SignArtifactProfile
    Azure Artifact Signing certificate profile name. Defaults to
    CS_SIGN_AS_PROFILE.

.PARAMETER SignArtifactDlib
    Full path to Azure.CodeSigning.Dlib.dll, from the same architecture folder
    as signtool. Defaults to CS_SIGN_AS_DLIB, then the newest
    Microsoft.ArtifactSigning.Client package in the NuGet cache.

.PARAMETER SignArtifactAuthExclude
    Credential types the dlib must not try, written into the metadata file as
    ExcludeCredentials. Defaults to CS_SIGN_AS_EXCLUDE_CREDENTIALS
    (comma separated), then to InteractiveBrowserCredential, so an unattended
    build fails rather than opening a browser.

.PARAMETER SignGameExecutables
    Also sign the patched game clients named in $PatchedGameExecutables. Off by
    default. CosmicSupremacy.exe, the original client, is never signed.

.PARAMETER SignAllowUntrusted
    Accept a signature whose chain does not end in a trusted root. For trying
    the signing step with a self-signed certificate; a release is not built
    with it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\release\build.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\release\build.ps1 -Version 0.2.0 -Clean
#>
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$Clean,
    [switch]$SkipZip,
    [string]$SignCertThumbprint = $env:CS_SIGN_CERT_THUMBPRINT,
    [string]$SignTimestampUrl = $env:CS_SIGN_TIMESTAMP_URL,
    [string]$SignToolPath = $env:CS_SIGNTOOL,
    [string]$SignArtifactEndpoint = $env:CS_SIGN_AS_ENDPOINT,
    [string]$SignArtifactAccount = $env:CS_SIGN_AS_ACCOUNT,
    [string]$SignArtifactProfile = $env:CS_SIGN_AS_PROFILE,
    [string]$SignArtifactDlib = $env:CS_SIGN_AS_DLIB,
    [string[]]$SignArtifactAuthExclude = $(if ($env:CS_SIGN_AS_EXCLUDE_CREDENTIALS) {
                                               @($env:CS_SIGN_AS_EXCLUDE_CREDENTIALS -split ',' |
                                                 ForEach-Object { $_.Trim() } | Where-Object { $_ })
                                           } else { @('InteractiveBrowserCredential') }),
    [switch]$SignGameExecutables,
    [switch]$SignAllowUntrusted
)

$ErrorActionPreference = 'Stop'

# ── Paths ─────────────────────────────────────────────────────────────────────
$ReleaseDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot   = Split-Path -Parent $ReleaseDir
$ClientDir  = Join-Path $RepoRoot 'client'
$ServerDir  = Join-Path $RepoRoot 'server'
# The four directories the multiplayer turn machinery lives in. They are flat
# module directories rather than packages, so each has to be on the analysis
# path in its own right.
$ServerTools = Join-Path $ServerDir 'dev_tools'
$ClientTools = Join-Path $ClientDir 'dev_tools'
$AiPlayerDir = Join-Path $ClientTools 'ai_player'
$BuildDir   = Join-Path $ReleaseDir 'build'
$DistRoot   = Join-Path $RepoRoot 'dist'
$Manifest   = Join-Path $ReleaseDir 'manifest.json'
$VenvDir    = Join-Path $ReleaseDir '.venv-build'
$VenvPy     = Join-Path $VenvDir 'Scripts\python.exe'
$PinFile    = Join-Path $RepoRoot '.python-version'

function Write-Step { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "    OK  $m" -ForegroundColor Green }
function Write-Warn2{ param($m) Write-Host "    !   $m" -ForegroundColor Yellow }

# Throws if a folder holds compiled Python outside an executable.
function Assert-NoLooseBytecode {
    param([string]$Dir)
    $loose = @(Get-ChildItem $Dir -Recurse -Force |
               Where-Object { $_.Name -eq '__pycache__' -or $_.Extension -in '.pyc', '.pyo' })
    if ($loose.Count -gt 0) {
        $loose | ForEach-Object { Write-Host "    loose: $($_.FullName)" -ForegroundColor Red }
        throw "$Dir carries Python bytecode outside the executables"
    }
}

# The processes that have a file open, by name and pid, from the file system's
# own list (FileProcessIdsUsingFileInformation). Opening the file for this asks
# only for its attributes and shares everything, so it cannot itself be the
# thing holding the file. Used to name the holder when the archive step is
# refused, which is otherwise only a guess.
function Get-FileHolders {
    param([string]$Path)
    if (-not ('CsBuild.Holders' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace CsBuild {
public static class Holders {
    [StructLayout(LayoutKind.Sequential)] struct IoStatus { public IntPtr Status; public IntPtr Info; }
    [DllImport("ntdll.dll")]
    static extern int NtQueryInformationFile(SafeFileHandle h, out IoStatus io, IntPtr buf, int len, int cls);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr sa, uint disp, uint flags, IntPtr tmpl);
    public static long[] Pids(string path) {
        using (var h = CreateFileW(path, 0x80, 7, IntPtr.Zero, 3, 0, IntPtr.Zero)) {
            if (h.IsInvalid) return new long[0];
            const int len = 64 * 1024;
            IntPtr buf = Marshal.AllocHGlobal(len);
            try {
                IoStatus io;
                if (NtQueryInformationFile(h, out io, buf, len, 47) != 0) return new long[0];
                int n = Marshal.ReadInt32(buf);
                var pids = new long[n];
                for (int i = 0; i < n; i++) pids[i] = Marshal.ReadIntPtr(buf, IntPtr.Size * (i + 1)).ToInt64();
                return pids;
            } finally { Marshal.FreeHGlobal(buf); }
        }
    }
}
}
'@
    }
    @([CsBuild.Holders]::Pids($Path) | ForEach-Object {
        $p = Get-Process -Id $_ -ErrorAction SilentlyContinue
        if ($p) { "$($p.ProcessName) ($_)" } else { "pid $_" }
    })
}

# Waits until no process has any of the files open, or the timeout passes.
# Returns whether they were released, how long it took, and every holder seen.
function Wait-FilesReleased {
    param([string[]]$Paths, [double]$TimeoutSeconds = 60)
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $seen = [ordered]@{}
    while ($true) {
        $busy = $false
        foreach ($path in $Paths) {
            foreach ($h in @(Get-FileHolders $path)) {
                $busy = $true
                $seen["$(Split-Path -Leaf $path) in $h"] = $true
            }
        }
        $done = (-not $busy) -or ($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds)
        if ($done) {
            return [pscustomobject]@{
                Released = -not $busy
                Seconds  = [math]::Round($clock.Elapsed.TotalSeconds, 1)
                Holders  = @($seen.Keys)
            }
        }
        Start-Sleep -Milliseconds 250
    }
}

# ── Code signing ──────────────────────────────────────────────────────────────
# Authenticode signing of the executables this project builds. Everything here
# is inert until signing is configured, and a configured signer that cannot
# sign fails the build rather than producing a quietly unsigned release.
#
# Two kinds of signer are configured, and setting both is an error:
#   CertStore        a certificate named by thumbprint in the current user's
#                    personal store (Cert:\CurrentUser\My). A hardware token
#                    appears there while it is plugged in.
#   ArtifactSigning  Azure Artifact Signing, reached through signtool with
#                    Microsoft's Azure.CodeSigning.Dlib.dll and a metadata JSON
#                    file naming the endpoint, account and certificate profile.
#                    No key or secret is held here: the dlib authenticates as
#                    the signed-in Azure identity.
# Get-SignToolArguments is the one place that turns a configuration into a
# signtool command line, so a further kind is one more case there.

# The game executables carry a signature only when -SignGameExecutables is
# given, and only these two. CosmicSupremacy.exe is the original client and is
# never signed with this project's certificate.
$PatchedGameExecutables = @('CosmicSupremacy_Resurgence.exe', 'CosmicSupremacy_Player.exe')

# Microsoft's documented requirements for signtool with the Artifact Signing
# dlib: Windows SDK signtool 10.0.2261.755 or later, and not the 20348 SDK.
function Get-SigningDefaults {
    [pscustomobject]@{
        MinSignTool        = [version]'10.0.2261.755'
        DlibName           = 'Azure.CodeSigning.Dlib.dll'
        ArtifactTimestamp  = 'http://timestamp.acs.microsoft.com'
        CertStoreTimestamp = 'http://timestamp.digicert.com'
    }
}

function Get-SigningArch {
    if ([Environment]::Is64BitOperatingSystem) { 'x64' } else { 'x86' }
}

# signtool.exe: the explicit path, then PATH, then the newest copy for this
# machine's architecture in a Windows SDK or in the Microsoft.Windows.SDK.BuildTools
# NuGet package cache. Returns $null when none is found.
function Find-SignTool {
    param([string]$Override)
    if ($Override) {
        if (-not (Test-Path -LiteralPath $Override -PathType Leaf)) {
            throw "signtool not found at $Override"
        }
        return (Resolve-Path -LiteralPath $Override).Path
    }
    $onPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    $arch = Get-SigningArch
    $roots = @()
    if (${env:ProgramFiles(x86)}) { $roots += Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin' }
    if ($env:USERPROFILE) {
        $roots += Join-Path $env:USERPROFILE '.nuget\packages\microsoft.windows.sdk.buildtools'
    }
    $found = @()
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $found += @(Get-ChildItem -LiteralPath $root -Filter signtool.exe -Recurse -Depth 4 -ErrorAction SilentlyContinue |
                    Where-Object { $_.DirectoryName -like "*\$arch" })
    }
    if ($found.Count -eq 0) { return $null }
    $newest = $found | Sort-Object {
        if ($_.FullName -match '\\(\d+\.\d+\.\d+\.\d+)\\') { [version]$Matches[1] } else { [version]'0.0.0.0' }
    } -Descending | Select-Object -First 1
    return $newest.FullName
}

# Azure.CodeSigning.Dlib.dll: the explicit path, then the newest copy for the
# given architecture in the NuGet cache of the Microsoft.ArtifactSigning.Client
# package (bin\<arch>\ inside the package). Returns $null when none is found.
function Find-ArtifactSigningDlib {
    param([string]$Override, [string]$Arch)
    $d = Get-SigningDefaults
    if ($Override) { return $Override }
    if (-not $env:USERPROFILE) { return $null }
    $root = Join-Path $env:USERPROFILE '.nuget\packages\microsoft.artifactsigning.client'
    if (-not (Test-Path -LiteralPath $root)) { return $null }
    $found = @(Get-ChildItem -LiteralPath $root -Filter $d.DlibName -Recurse -Depth 3 -ErrorAction SilentlyContinue |
               Where-Object { $_.DirectoryName -like "*\bin\$Arch" })
    if ($found.Count -eq 0) { return $null }
    $newest = $found | Sort-Object {
        if ($_.FullName -match 'microsoft\.artifactsigning\.client\\([\d.]+)\\') { [version]$Matches[1] } else { [version]'0.0' }
    } -Descending | Select-Object -First 1
    return $newest.FullName
}

# The reason a signtool version cannot be used with the Artifact Signing dlib,
# or $null when it can.
function Test-SignToolVersion {
    param([version]$Version)
    $d = Get-SigningDefaults
    if ($Version.Build -eq 20348) {
        return "signtool $Version is from the 20348 Windows SDK, which the Artifact Signing dlib does not support"
    }
    if ($Version -lt $d.MinSignTool) {
        return "signtool $Version is older than the $($d.MinSignTool) the Artifact Signing dlib needs"
    }
    return $null
}

function Assert-TimestampUrl {
    param([string]$Url)
    $uri = $null
    if (-not [Uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -notin 'http', 'https') {
        throw "signing timestamp server must be an http or https URL, got '$Url'"
    }
}

function Resolve-CertStoreSigning {
    param([string]$Thumbprint, [string]$TimestampUrl, [string]$SignToolPath, [bool]$AllowUntrusted)
    $d = Get-SigningDefaults
    # A thumbprint copied out of the certificate dialog carries invisible
    # formatting characters, so everything that is not a hex digit is dropped.
    $thumb = ($Thumbprint -replace '[^0-9A-Fa-f]', '').ToUpperInvariant()
    if ($thumb.Length -ne 40) {
        throw "signing thumbprint must be 40 hex digits (SHA-1), got $($thumb.Length)"
    }
    if (-not $TimestampUrl) { $TimestampUrl = $d.CertStoreTimestamp }
    Assert-TimestampUrl $TimestampUrl

    $cert = Get-Item -LiteralPath "Cert:\CurrentUser\My\$thumb" -ErrorAction SilentlyContinue
    if (-not $cert) { throw "no certificate with thumbprint $thumb in Cert:\CurrentUser\My" }
    if (-not $cert.HasPrivateKey) {
        throw "certificate $thumb has no private key available (is the hardware token plugged in?)"
    }
    if ($cert.NotAfter -le (Get-Date)) { throw "certificate $thumb expired $($cert.NotAfter)" }
    $codeSigning = @($cert.EnhancedKeyUsageList | Where-Object { $_.ObjectId -eq '1.3.6.1.5.5.7.3.3' })
    if ($codeSigning.Count -eq 0) { throw "certificate $thumb is not a code signing certificate" }

    $tool = Find-SignTool $SignToolPath
    if (-not $tool) {
        throw ('signtool.exe not found. Install the Windows SDK "Signing Tools for Desktop Apps", ' +
               'or pass -SignToolPath, or set CS_SIGNTOOL')
    }
    Write-Ok "signing as $($cert.Subject) ($thumb)"
    Write-Ok "signtool $tool"
    return [pscustomobject]@{
        Kind           = 'CertStore'
        Thumbprint     = $thumb
        Subject        = $cert.Subject
        SignTool       = $tool
        TimestampUrl   = $TimestampUrl
        AllowUntrusted = $AllowUntrusted
    }
}

function Resolve-ArtifactSigning {
    param(
        [string]$Endpoint, [string]$Account, [string]$CertProfile, [string]$Dlib,
        [string[]]$AuthExclude, [string]$MetadataPath,
        [string]$TimestampUrl, [string]$SignToolPath, [bool]$AllowUntrusted
    )
    $d = Get-SigningDefaults
    $missing = @()
    if (-not $Endpoint)    { $missing += 'endpoint (-SignArtifactEndpoint or CS_SIGN_AS_ENDPOINT)' }
    if (-not $Account)     { $missing += 'account name (-SignArtifactAccount or CS_SIGN_AS_ACCOUNT)' }
    if (-not $CertProfile) { $missing += 'certificate profile (-SignArtifactProfile or CS_SIGN_AS_PROFILE)' }
    if ($missing.Count -gt 0) { throw "Artifact Signing is half configured, missing: $($missing -join '; ')" }

    # The shapes Microsoft documents: the regional endpoint host, an account
    # name of 3 to 24 characters and a profile name of 5 to 100, both letters,
    # digits and single hyphens, starting with a letter and ending with a
    # letter or digit.
    if ($Endpoint -notmatch '^https://[A-Za-z0-9]+\.codesigning\.azure\.net/?$') {
        throw "Artifact Signing endpoint must look like https://eus.codesigning.azure.net, got '$Endpoint'"
    }
    if ($Account.Length -lt 3 -or $Account.Length -gt 24 -or
        $Account -notmatch '^[A-Za-z][A-Za-z0-9-]*[A-Za-z0-9]$' -or $Account -match '--') {
        throw "Artifact Signing account name is 3 to 24 letters, digits and single hyphens, got '$Account'"
    }
    if ($CertProfile.Length -lt 5 -or $CertProfile.Length -gt 100 -or
        $CertProfile -notmatch '^[A-Za-z][A-Za-z0-9-]*[A-Za-z0-9]$' -or $CertProfile -match '--') {
        throw "Artifact Signing certificate profile name is 5 to 100 letters, digits and single hyphens, got '$CertProfile'"
    }
    foreach ($c in $AuthExclude) {
        if ($c -notmatch '^[A-Za-z]+Credential$') { throw "not a credential name to exclude: '$c'" }
    }
    if (-not $TimestampUrl) { $TimestampUrl = $d.ArtifactTimestamp }
    Assert-TimestampUrl $TimestampUrl

    $tool = Find-SignTool $SignToolPath
    if (-not $tool) {
        throw ('signtool.exe not found. Artifact Signing needs the Windows SDK signtool ' +
               '(Microsoft.Windows.SDK.BuildTools from NuGet works); pass -SignToolPath or set CS_SIGNTOOL')
    }
    $toolArch = Split-Path -Leaf (Split-Path -Parent $tool)
    $pv = $null
    $versionText = (Get-Item -LiteralPath $tool).VersionInfo.ProductVersion
    if ([version]::TryParse([string]$versionText, [ref]$pv)) {
        $problem = Test-SignToolVersion $pv
        if ($problem) { throw $problem }
    } else {
        Write-Warn2 "could not read the version of $tool; Artifact Signing needs $($d.MinSignTool) or later"
    }

    $dlibPath = Find-ArtifactSigningDlib $Dlib $toolArch
    if (-not $dlibPath) {
        throw ("$($d.DlibName) not found for $toolArch. Install the Microsoft.ArtifactSigning.Client " +
               'NuGet package, or pass -SignArtifactDlib or set CS_SIGN_AS_DLIB')
    }
    if (-not (Test-Path -LiteralPath $dlibPath -PathType Leaf)) { throw "Artifact Signing dlib not found at $dlibPath" }
    $dlibPath = (Resolve-Path -LiteralPath $dlibPath).Path
    if ((Split-Path -Leaf $dlibPath) -ne $d.DlibName) {
        throw "the Artifact Signing dlib must be named $($d.DlibName), got $(Split-Path -Leaf $dlibPath)"
    }
    $dlibArch = Split-Path -Leaf (Split-Path -Parent $dlibPath)
    $arches = 'x86', 'x64', 'arm64'
    if ($toolArch -in $arches -and $dlibArch -in $arches -and $toolArch -ne $dlibArch) {
        throw "signtool is $toolArch but the dlib is $dlibArch; they must match"
    }

    $runtimes = @()
    if (Get-Command dotnet -ErrorAction SilentlyContinue) {
        $runtimes = @(& dotnet --list-runtimes 2>$null | Where-Object { $_ -match '^Microsoft\.NETCore\.App 8\.' })
    }
    if ($runtimes.Count -eq 0) {
        Write-Warn2 '.NET 8 runtime not found by dotnet; signtool with the Artifact Signing dlib fails silently without it'
    }

    if (-not $MetadataPath) {
        $MetadataPath = Join-Path ([IO.Path]::GetTempPath()) "cs-artifact-signing-$PID.json"
    }
    $metadata = [ordered]@{
        Endpoint               = $Endpoint
        CodeSigningAccountName = $Account
        CertificateProfileName = $CertProfile
    }
    if (@($AuthExclude).Count -gt 0) { $metadata['ExcludeCredentials'] = @($AuthExclude) }

    Write-Ok "signing with Azure Artifact Signing, account $Account, profile $CertProfile"
    Write-Ok "signtool $tool"
    Write-Ok "dlib $dlibPath"
    return [pscustomobject]@{
        Kind           = 'ArtifactSigning'
        Thumbprint     = $null
        Subject        = "Artifact Signing $Account/$CertProfile"
        SignTool       = $tool
        TimestampUrl   = $TimestampUrl
        AllowUntrusted = $AllowUntrusted
        Dlib           = $dlibPath
        MetadataPath   = $MetadataPath
        Metadata       = $metadata
    }
}

# Turns the signing parameters into a configuration object, or $null when none
# is set. Everything that can be wrong with the configuration is checked here,
# at the start of the build, so a mistyped thumbprint or a missing dlib costs
# seconds and not a PyInstaller run.
function Resolve-SigningConfig {
    param(
        [string]$Thumbprint,
        [string]$TimestampUrl,
        [string]$SignToolPath,
        [string]$ArtifactEndpoint,
        [string]$ArtifactAccount,
        [string]$ArtifactProfile,
        [string]$ArtifactDlib,
        [string[]]$ArtifactAuthExclude = @(),
        [string]$MetadataPath,
        [switch]$AllowUntrusted,
        [switch]$GameExecutables
    )
    $artifact = @($ArtifactEndpoint, $ArtifactAccount, $ArtifactProfile) | Where-Object { $_ }
    if (-not $Thumbprint -and @($artifact).Count -eq 0) {
        if ($GameExecutables) {
            throw '-SignGameExecutables needs a signer: set -SignCertThumbprint (CS_SIGN_CERT_THUMBPRINT) or the Artifact Signing settings'
        }
        Write-Warn2 'no signing certificate configured - this build is unsigned'
        return $null
    }
    if ($Thumbprint -and @($artifact).Count -gt 0) {
        throw 'both a certificate thumbprint and Artifact Signing settings are set; configure one signer'
    }
    if ($Thumbprint) {
        return Resolve-CertStoreSigning $Thumbprint $TimestampUrl $SignToolPath ([bool]$AllowUntrusted)
    }
    return Resolve-ArtifactSigning -Endpoint $ArtifactEndpoint -Account $ArtifactAccount `
        -CertProfile $ArtifactProfile -Dlib $ArtifactDlib -AuthExclude $ArtifactAuthExclude `
        -MetadataPath $MetadataPath -TimestampUrl $TimestampUrl -SignToolPath $SignToolPath `
        -AllowUntrusted ([bool]$AllowUntrusted)
}

# The signtool command line for one file. The only function that knows how the
# signer is reached.
function Get-SignToolArguments {
    param($Config, [string]$File)
    switch ($Config.Kind) {
        'CertStore' {
            return @('sign', '/fd', 'SHA256', '/tr', $Config.TimestampUrl, '/td', 'SHA256',
                     '/sha1', $Config.Thumbprint, $File)
        }
        'ArtifactSigning' {
            return @('sign', '/fd', 'SHA256', '/tr', $Config.TimestampUrl, '/td', 'SHA256',
                     '/dlib', $Config.Dlib, '/dmdf', $Config.MetadataPath, $File)
        }
        default { throw "unknown signing kind '$($Config.Kind)'" }
    }
}

# The metadata file the dlib reads. It names the account and profile and holds
# no secret, so it is written fresh for each signing run.
function Write-SigningMetadata {
    param($Config)
    $dir = Split-Path -Parent $Config.MetadataPath
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
    $json = $Config.Metadata | ConvertTo-Json
    [IO.File]::WriteAllText($Config.MetadataPath, $json, (New-Object Text.UTF8Encoding($false)))
}

# Reads the signature back. A file that signtool reported success on but that
# does not carry our signature, or a signature with no timestamp, is a failure.
# An untrusted chain is accepted only when the configuration says so, which is
# the case for a self-signed certificate used to exercise the step.
function Assert-SignedFile {
    param($Config, [string]$File)
    $sig = Get-AuthenticodeSignature -LiteralPath $File
    $name = Split-Path -Leaf $File
    if (-not $sig.SignerCertificate) { throw "$name carries no signature ($($sig.Status))" }
    if ($Config.Thumbprint -and $sig.SignerCertificate.Thumbprint -ne $Config.Thumbprint) {
        throw "$name is signed by $($sig.SignerCertificate.Thumbprint), not $($Config.Thumbprint)"
    }
    if (-not $sig.TimeStamperCertificate) { throw "$name carries no timestamp" }
    $accepted = @('Valid')
    if ($Config.AllowUntrusted) { $accepted += @('NotTrusted', 'UnknownError') }
    if ($sig.Status.ToString() -notin $accepted) {
        throw "$name signature status is $($sig.Status): $($sig.StatusMessage)"
    }
    return $sig
}

# Signs and verifies each file. Does nothing when no signer is configured.
# The timestamp server and the signing service are network services that fail
# now and then, so a failed signtool run is repeated before the build gives up.
function Invoke-ReleaseSigning {
    param($Config, [string[]]$Path, [int]$Attempts = 3, [int]$RetryDelaySeconds = 5)
    if (-not $Config) { return }
    if ($Config.Kind -eq 'ArtifactSigning') { Write-SigningMetadata $Config }
    foreach ($file in $Path) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "cannot sign, missing: $file" }
        $signArgs = Get-SignToolArguments $Config $file
        $name = Split-Path -Leaf $file
        for ($try = 1; ; $try++) {
            # Native stderr becomes a terminating error under 'Stop' in 5.1.
            $saved = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            $out = @(& $Config.SignTool @signArgs 2>&1 | ForEach-Object { "$_" })
            $code = $LASTEXITCODE
            $ErrorActionPreference = $saved
            if ($code -eq 0) { break }
            $tail = ($out | Select-Object -Last 4) -join ' | '
            if ($try -ge $Attempts) { throw "signtool failed for $name (exit $code): $tail" }
            Write-Warn2 "signtool attempt $try failed for $name (exit $code): $tail; retrying"
            Start-Sleep -Seconds $RetryDelaySeconds
        }
        $sig = Assert-SignedFile $Config $file
        Write-Ok "signed $name ($($sig.Status), timestamped)"
    }
}

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

# Placeholder cards ("enabled": false, e.g. the coming-soon Multiplayer button)
# are shown in the launcher but ship no files. They must be filtered out before
# any path is built from $_.exe, or Join-Path is handed $null and throws.
$playable = @($modes | Where-Object { $_.exe -and $_.galaxy -and ($_.enabled -ne $false) })
if ($playable.Count -eq 0) { throw 'manifest has no playable modes to ship' }
$placeholders = @($modes | Where-Object { $playable -notcontains $_ })
if ($placeholders.Count -gt 0) {
    Write-Host "Shipping: $(($playable | ForEach-Object { $_.id }) -join ', ')"
    Write-Host "Coming soon (no files): $(($placeholders | ForEach-Object { $_.id }) -join ', ')"
}

# ── 0b. Signing configuration ─────────────────────────────────────────────────
# Resolved before anything is built so a certificate that is not there, or a
# missing signtool, fails now.
Write-Step "Signing configuration"
$Signing = Resolve-SigningConfig -Thumbprint $SignCertThumbprint `
    -TimestampUrl $SignTimestampUrl -SignToolPath $SignToolPath `
    -ArtifactEndpoint $SignArtifactEndpoint -ArtifactAccount $SignArtifactAccount `
    -ArtifactProfile $SignArtifactProfile -ArtifactDlib $SignArtifactDlib `
    -ArtifactAuthExclude $SignArtifactAuthExclude `
    -MetadataPath (Join-Path $BuildDir 'artifact-signing-metadata.json') `
    -AllowUntrusted:$SignAllowUntrusted -GameExecutables:$SignGameExecutables

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
foreach ($m in $playable) {
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

Write-Step "Preparing $StageName"

$live = Get-Process CosmicSupremacyLauncher -ErrorAction SilentlyContinue
if ($live) {
    throw ("a launcher is still running (pid " + (($live | ForEach-Object { $_.Id }) -join ', ') +
           ") and holds the files this build must overwrite - close it and re-run")
}
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

# The build name is decided here and nowhere else, so it has to be carried into
# the exe rather than left for the launcher to guess. stamp_build writes it to
# build.json and launcher.build_info() reads it back through bundled(). Without
# this a build made with -Version 0.1.1 ships a launcher calling itself
# whatever manifest.json says, and the version gate compares the wrong number.
& $VenvPy (Join-Path $ReleaseDir 'stamp_build.py') $Version $BuildDir | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'stamp_build failed' }
$BuildStamp = Join-Path $BuildDir 'build.json'
if (-not (Test-Path $BuildStamp)) { throw "stamp_build reported success but $BuildStamp is missing" }
Write-Ok "stamped build $Version"

$piArgs = @(
    '--noconfirm', '--onefile', '--windowed',
    '--name', 'CosmicSupremacyLauncher',
    # manifest.json rides inside the exe: it is the build's own definition of
    # what the modes are, not something a player is meant to retune.
    '--add-data', "$Manifest;.",
    # build.json rides beside it, written a few lines above by stamp_build.
    '--add-data', "$BuildStamp;.",
    # beta_notice.txt is the text a player reads before joining. It is a
    # data file, so the import graph never sees it and a frozen build
    # would raise FileNotFoundError where the notice should be.
    '--add-data', "$ServerDir\beta_notice.txt;.",
    # cs_server is imported inside a function after its environment is set, so
    # name it explicitly rather than relying on the import graph walker.
    '--paths', $ServerDir,
    '--hidden-import', 'cs_server',
    # gamectl backs the Next Turn / Save / Load buttons and is imported inside
    # the handlers, not at module scope, so name it rather than rely on the
    # import graph walker finding it.
    '--hidden-import', 'gamectl',
    # The multiplayer turn machinery. multiplayer_modules() imports player_turn
    # and turn_store once a galaxy is configured, and from there every module
    # is reached from inside a function: save_parser and set_blob_player stamp
    # the turn blob, game_cycle starts and closes the client, gamestate and
    # ejbo_viewer read the running client to confirm it came up as the right
    # civ, advance_turns holds the turn clock, trigger_save takes the capture
    # back, canonical and order_diff decide whether a capture carries orders.
    # inject_civ and inject_design are pulled in by save_parser's neighbours.
    # merge_orders is the referee's, named here so a checkout and a release
    # agree about what a store-aware build contains.
    '--paths', $ServerTools,
    '--paths', $ClientTools,
    '--paths', $AiPlayerDir,
    '--hidden-import', 'player_turn',
    '--hidden-import', 'turn_store',
    # The Games tab lists galaxies through these two, and both are
    # imported inside functions, so the graph walker does not see them.
    # Without them a frozen build reports that it cannot list galaxies.
    '--hidden-import', 'galaxy_directory',
    '--hidden-import', 'firebase_store',
    # beta_notice carries the text a player must see before joining.
    '--hidden-import', 'beta_notice',
    '--hidden-import', 'save_parser',
    '--hidden-import', 'set_blob_player',
    '--hidden-import', 'inject_civ',
    '--hidden-import', 'inject_design',
    '--hidden-import', 'canonical',
    '--hidden-import', 'order_diff',
    '--hidden-import', 'merge_orders',
    '--hidden-import', 'game_cycle',
    '--hidden-import', 'gamestate',
    '--hidden-import', 'ejbo_viewer',
    '--hidden-import', 'advance_turns',
    '--hidden-import', 'trigger_save',
    # This install's Firebase identity, imported by player_token() once a
    # galaxy that wants one is opened. Missing from the bundle it would be an
    # ImportError swallowed into "no token", so a release would talk to the
    # beta's function anonymously and only the function would notice.
    '--hidden-import', 'fb_auth',
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
Invoke-ReleaseSigning -Config $Signing -Path $BuiltExe

# ── 6b. Freeze the AI opponent ────────────────────────────────────────────────
# Only if a mode asks for one. A release with no "ai" key in any mode should not
# carry a second executable it never starts.
$wantsAI = @($modes | Where-Object { $_.ai }).Count -gt 0
if ($wantsAI) {
    Write-Step "Freezing AI opponent (PyInstaller)"
    $AiDir    = Join-Path $ClientDir 'dev_tools\ai_player'
    $ToolsDir = Join-Path $ClientDir 'dev_tools'
    $AiEntry  = Join-Path $AiDir 'ai.py'
    if (-not (Test-Path $AiEntry)) { throw "AI entry point not found: $AiEntry" }
    
    $aiArgs = @(
        '--noconfirm', '--onefile',
        '--name', 'CosmicSupremacyAI',
        '--paths', $AiDir,
        '--paths', $ToolsDir,
        '--hidden-import', 'ejbo_viewer',
        # Into game\, not the folder root. The player should open the release
        # and see exactly one thing to click; the opponent is machinery the
        # launcher starts, and a second executable beside the launcher is an
        # invitation to run the wrong one. find_ai() already looks here.
        '--distpath', $GameDir,
        '--workpath', (Join-Path $BuildDir 'work-ai'),
        '--specpath', $BuildDir
    )
    if ($IconOut) { $aiArgs += @('--icon', $IconOut) }
    $aiArgs += $AiEntry

    & $VenvPy -m PyInstaller @aiArgs
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed for the AI' }

    $BuiltAI = Join-Path $GameDir 'CosmicSupremacyAI.exe'
    if (-not (Test-Path $BuiltAI)) { throw "PyInstaller reported success but $BuiltAI is missing" }
    Write-Ok "game\CosmicSupremacyAI.exe  ($([math]::Round((Get-Item $BuiltAI).Length / 1MB, 1)) MB)"
    Invoke-ReleaseSigning -Config $Signing -Path $BuiltAI

    # Fail the build rather than ship a Single Player mode with nobody in it.
    foreach ($m in @($modes | Where-Object { $_.ai })) {
        if (-not $m.ai.civ) { throw "mode '$($m.id)' has an ai block with no civ name" }
        Write-Ok "$($m.id): opponent plays '$($m.ai.civ)'"
    }
} else {
    Write-Warn2 'no mode declares an "ai" opponent - not building one'
}

# ── 7. Add the game files ─────────────────────────────────────────────────────
Write-Step "Adding game files"
Copy-Item (Join-Path $ReleaseDir 'PLAYER_README.txt') (Join-Path $Stage 'README.txt')

# The zip is where the original binaries actually get distributed, so the file
# explaining whose they are travels with them rather than only living in the repo.
$License = Join-Path $RepoRoot 'LICENSE'
if (Test-Path $License) {
    Copy-Item $License (Join-Path $Stage 'LICENSE.txt')
    Write-Ok 'LICENSE.txt'
} else {
    Write-Warn2 'no LICENSE at the repo root - shipping without one'
}

# A client double-clicked in game\ shows a setup message that walks the player
# through the original site's sign-up flow. patch_launch_messages.py writes each
# client into the stage with that text pointing at the launcher instead, so the
# copy is the patch and the tracked binary is only read.
$PatchMessages = Join-Path $ClientDir 'dev_tools\patch_launch_messages.py'
function Copy-Client {
    param($src, $dst)
    & $VenvPy $PatchMessages --source $src --out $dst | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "patch_launch_messages failed for $(Split-Path -Leaf $src)" }
}

# Several modes share one EXE, so copy the distinct set rather than per mode.
$copied = @{}
foreach ($m in $playable) {
    if (-not $copied.ContainsKey($m.exe)) {
        Copy-Client (Join-Path $ClientDir $m.exe) (Join-Path $GameDir $m.exe)
        $copied[$m.exe] = $true
        Write-Ok "game\$($m.exe)"
    }
    if (-not $copied.ContainsKey($m.galaxy)) {
        Copy-Item (Join-Path $ClientDir $m.galaxy) (Join-Path $GalaxyDir $m.galaxy)
        $copied[$m.galaxy] = $true
        Write-Ok "game\galaxies\$($m.galaxy)"
    }
}

# ── 7b. The multiplayer client ────────────────────────────────────────────────
# A session mode names no EXE, because the turn loop writes the .dat for each
# turn and picks the build itself, so the copy above never reaches it. It still
# needs a client on disk, and not the single player one: a player's client must
# never compute a turn the referee has not, or the state it hands back mixes
# their orders with a simulation nobody authorised.
#
# Player is TestBed plus one byte that silences the coat-of-arms prompt. It is
# gitignored, being a patched copy of a tracked binary, so a checkout that has
# not run patch_hide_setup_prompts.py --build ships TestBed instead. That is the
# same fallback game_cycle.resolve_exe makes, one prompt worse.
$mpModes = @($modes | Where-Object { $_.session -eq 'multiplayer' })
if ($mpModes.Count -gt 0) {
    Write-Step "Adding the multiplayer client"
    $mpExe = $null
    foreach ($name in @('CosmicSupremacy_Player.exe', 'CosmicSupremacy_TestBed.exe')) {
        $cand = Join-Path $ClientDir $name
        if (Test-Path $cand) { $mpExe = $cand; break }
    }
    if ($mpExe) {
        Copy-Client $mpExe (Join-Path $GameDir (Split-Path -Leaf $mpExe))
        Write-Ok "game\$(Split-Path -Leaf $mpExe)"
        if ((Split-Path -Leaf $mpExe) -ne 'CosmicSupremacy_Player.exe') {
            Write-Warn2 ('shipping TestBed: players will see the coat-of-arms ' +
                         'prompt every turn. Build the player client with ' +
                         'patch_hide_setup_prompts.py --build and re-run')
        }
    } else {
        # Not fatal: the rest of the release is still worth shipping. The
        # launcher checks for this client before it starts a turn and says so.
        Write-Warn2 ('no multiplayer client in client\ - the Multiplayer card ' +
                     'will refuse to start')
    }
}

# ── 7c. No loose bytecode ─────────────────────────────────────────────────────
# PyInstaller carries every module, trigger_save included, inside the launcher's
# PYZ archive, so a release has no .pyc files and no __pycache__ folders. A
# loose trigger_save .pyc is what Defender quarantines as
# Exploit:Python/Leivion.C in a checkout, so one appearing here fails the build
# rather than reaching a player's scanner.
Write-Step "Checking for loose bytecode"
Assert-NoLooseBytecode $Stage
Write-Ok "no .pyc and no __pycache__ in $StageName"

# ── 7d. The patched game executables, when asked ──────────────────────────────
# Off unless -SignGameExecutables is given. Whether the patched clients carry
# the project's signature is a rights question as well as a build one, so it is
# a switch and not a default.
if ($SignGameExecutables) {
    Write-Step "Signing the patched game executables"
    $gameSign = @($PatchedGameExecutables | ForEach-Object { Join-Path $GameDir $_ } |
                  Where-Object { Test-Path -LiteralPath $_ })
    if ($gameSign.Count -eq 0) { throw 'no patched game executable in the release folder to sign' }
    Invoke-ReleaseSigning -Config $Signing -Path $gameSign
}

# ── 8. Zip ────────────────────────────────────────────────────────────────────
if (-not $SkipZip) {
    Write-Step "Creating archive"
    $Zip = "$Stage.zip"
    if (Test-Path $Zip) { Remove-Item -Force $Zip }
    # The executables copied a moment ago are held open while the antivirus
    # scans them, and Compress-Archive fails PermissionDenied on one it cannot
    # open. A build on 29 September named the holder as MsMpEng, Defender's
    # engine. So the archive waits for the scan to finish rather than failing
    # into it, and the retry below stays for a holder that outlasts the wait.
    $staged = @(Get-ChildItem $Stage -Recurse -File -Filter *.exe | ForEach-Object { $_.FullName })
    $wait = Wait-FilesReleased $staged 60
    if ($wait.Holders.Count -gt 0) {
        $state = if ($wait.Released) { 'released' } else { 'still held' }
        Write-Ok "waited $($wait.Seconds)s for $($wait.Holders -join '; '), $state"
    }
    for ($try = 1; ; $try++) {
        try {
            if (Test-Path $Zip) { Remove-Item -Force $Zip }
            Compress-Archive -Path $Stage -DestinationPath $Zip -CompressionLevel Optimal -ErrorAction Stop
            break
        } catch {
            Write-Warn2 "archive attempt $try failed ($($_.Exception.Message))"
            foreach ($exe in @(Get-ChildItem $Stage -Recurse -File -Filter *.exe)) {
                $held = @(Get-FileHolders $exe.FullName)
                if ($held.Count -gt 0) {
                    Write-Warn2 "    $($exe.Name) is open in: $($held -join ', ')"
                }
            }
            if ($try -ge 6) { throw }
            Write-Warn2 "retrying in 10s"
            Start-Sleep -Seconds 10
        }
    }
    Write-Ok "$Zip  ($([math]::Round((Get-Item $Zip).Length / 1MB, 1)) MB)"

    # Published in the release notes so a player can verify the download, and so
    # "is this the build I tested?" has an answer later.
    $sha = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower()
    Set-Content -Path "$Zip.sha256" -Encoding ascii -Value "$sha  $(Split-Path -Leaf $Zip)"
    Write-Ok "SHA256 $sha"
}

# ── Done ──────────────────────────────────────────────────────────────────────
$total = (Get-ChildItem $Stage -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host @"

------------------------------------------------------------------
Release built: $StageName  ($([math]::Round($total / 1MB, 1)) MB unpacked)

Test it the way a player would - from the staged folder, not the repo:
    $Stage\CosmicSupremacyLauncher.exe

Then publish the zip as the v$Version release asset. That release is the only
copy of the download: the site's download page links straight at it.
    gh release create v$Version "$Stage.zip" ``
        --title "Cosmic Supremacy: Resurgence v$Version" ``
        --notes-file "$ReleaseDir\RELEASE_NOTES_v$Version.md"
------------------------------------------------------------------
"@ -ForegroundColor White
