"""
The signing step in build.ps1.

A build with no certificate says so and carries on, a build with one signs
exactly the launcher and the AI opponent, and a signing failure fails the
build. The functions are taken out of build.ps1 by PowerShell's own parser.

Three layers:
  * the script text: which files are passed to the signing step, and in what
    order relative to the freeze and the archive;
  * the functions against a stub signtool, which records its command lines;
  * a real signtool signing a copy of a small executable with a throwaway
    self-signed certificate, created in Cert:\\CurrentUser\\My only and
    deleted afterwards. Nothing is imported into a Root or TrustedPublisher
    store. Skipped when no signtool is installed or the timestamp server
    cannot be reached.

No game and no window.
"""
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile

REPO = str(pathlib.Path(__file__).resolve().parents[2])
BUILD_PS1 = os.path.join(REPO, "release", "build.ps1")
BUILD_TEXT = open(BUILD_PS1, encoding="utf-8-sig").read()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
POWERSHELL = shutil.which("powershell") or "powershell"

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        fails.append(label)


def skip(label, why):
    print(f"  [SKIP] {label}: {why}")


def ps_quote(text):
    return "'" + str(text).replace("'", "''") + "'"


def run_ps(command, env=None, timeout=180):
    """Run `command` with build.ps1's functions defined. Returns (rc, output)."""
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile("
        f"{ps_quote(BUILD_PS1)}, [ref]$null, [ref]$null)\n"
        "$ast.FindAll({ param($a) $a -is "
        "[System.Management.Automation.Language.FunctionDefinitionAst] }, $false)"
        " | ForEach-Object { . ([scriptblock]::Create($_.Extent.Text)) }\n"
        + command)
    p = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
         "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=timeout, env=env,
        creationflags=NO_WINDOW)
    return p.returncode, p.stdout + p.stderr


def region(start, end):
    a = BUILD_TEXT.index(start)
    return BUILD_TEXT[a:BUILD_TEXT.index(end, a)]


# What the script passes to the signing step
calls = re.findall(r"Invoke-ReleaseSigning -Config \$Signing -Path (\$\w+)",
                   BUILD_TEXT)
check("the signing step is called for the launcher, the AI, and the opt-in set",
      calls, ["$BuiltExe", "$BuiltAI", "$gameSign"])

built_exe = re.search(r"\$BuiltExe\s*=\s*Join-Path \$Stage '([^']+)'", BUILD_TEXT)
built_ai = re.search(r"\$BuiltAI\s*=\s*Join-Path \$GameDir '([^']+)'", BUILD_TEXT)
check("$BuiltExe is the launcher", built_exe and built_exe.group(1),
      "CosmicSupremacyLauncher.exe")
check("$BuiltAI is the AI opponent", built_ai and built_ai.group(1),
      "CosmicSupremacyAI.exe")

sign_launcher_at = BUILD_TEXT.index("Invoke-ReleaseSigning -Config $Signing -Path $BuiltExe")
sign_ai_at = BUILD_TEXT.index("Invoke-ReleaseSigning -Config $Signing -Path $BuiltAI")
check("the launcher is signed right after it is frozen",
      BUILD_TEXT.index("PyInstaller reported success but $BuiltExe")
      < sign_launcher_at < BUILD_TEXT.index("# ── 6b."), True)
check("the AI is signed right after it is frozen",
      BUILD_TEXT.index("PyInstaller reported success but $BuiltAI")
      < sign_ai_at < BUILD_TEXT.index("# ── 7. Add the game files"), True)
check("everything is signed before the archive step",
      BUILD_TEXT.index("Invoke-ReleaseSigning -Config $Signing -Path $gameSign")
      < BUILD_TEXT.index("# ── 8. Zip"), True)

listed = re.search(r"\$GameExecutables\s*=\s*@\(([^)]*)\)", BUILD_TEXT)
game_exes = re.findall(r"'([^']+)'", listed.group(1)) if listed else None
check("the signed game set is the three clients and nothing else", game_exes,
      ["CosmicSupremacy.exe", "CosmicSupremacy_Resurgence.exe",
       "CosmicSupremacy_Player.exe"])
check("the game executables are signed whenever a signer is configured",
      "if ($Signing -and -not $SkipGameExecutables) {" in region("# ── 7d.", "# ── 8. Zip")
      and "Invoke-ReleaseSigning" in region("if ($Signing -and -not $SkipGameExecutables) {",
                                            "# ── 8. Zip"), True)
check("-SkipGameExecutables is a switch that leaves them unsigned",
      "[switch]$SkipGameExecutables" in BUILD_TEXT
      and "$SignGameExecutables" not in BUILD_TEXT, True)
signing_code = region("# ── Code signing", "Write-Host \"Cosmic Supremacy - Resurgence")
signing_code = "\n".join(l for l in signing_code.splitlines()
                         if not l.lstrip().startswith("#"))
bare_names = re.findall(r"CosmicSupremacy(?:_\w+)?\.exe", signing_code)
check("the signing functions name no game executable but those three",
      sorted(set(bare_names)), sorted(game_exes or []))

rc, out = run_ps("$c = Resolve-SigningConfig -Thumbprint '' "
                 "-TimestampUrl 'http://t.example'; "
                 "\"config-is-null=$($null -eq $c)\"")
check("no certificate: the configuration step succeeds", rc, 0)
check("and returns nothing to sign with", "config-is-null=True" in out, True)
said = [l for l in out.splitlines() if "unsigned" in l.lower()]
check("and says the build is unsigned, once", len(said), 1)
check("and prints nothing else", len([l for l in out.splitlines() if l.strip()]), 2)

scratch = tempfile.mkdtemp(prefix="signing_")
fake = os.path.join(scratch, "fake.exe")
with open(fake, "wb") as fh:
    fh.write(b"MZ" + b"\0" * 64)
before = open(fake, "rb").read()
rc, out = run_ps(f"Invoke-ReleaseSigning -Config $null -Path @({ps_quote(fake)})")
check("the signing step with no configuration does nothing", (rc, out.strip()),
      (0, ""))
check("and leaves the file as it was", open(fake, "rb").read() == before, True)

rc, out = run_ps("Resolve-SigningConfig -Thumbprint '' "
                 "-TimestampUrl 'http://t.example' -GameExecutables")
check("asking for the game executables with no certificate fails",
      rc != 0 and "game executables needs a signer" in out, True)

# A configuration that cannot work fails at the start
rc, out = run_ps("Resolve-SigningConfig -Thumbprint 'ABCD' "
                 "-TimestampUrl 'http://t.example'")
check("a short thumbprint fails", rc != 0 and "40 hex" in out, True)

rc, out = run_ps(f"Resolve-SigningConfig -Thumbprint {ps_quote('A' * 40)} "
                 "-TimestampUrl 'ftp://t.example'")
check("a timestamp server that is not http fails",
      rc != 0 and "timestamp server" in out, True)

rc, out = run_ps(f"Resolve-SigningConfig -Thumbprint {ps_quote('A' * 40)} "
                 "-TimestampUrl 'http://t.example'")
check("a thumbprint with no certificate behind it fails",
      rc != 0 and "no certificate" in out, True)

rc, out = run_ps(f"Find-SignTool {ps_quote(os.path.join(scratch, 'missing.exe'))}")
check("a signtool path that does not exist fails", rc != 0 and "not found" in out,
      True)

home = os.path.join(scratch, "home")
pkg = os.path.join(home, ".nuget", "packages", "microsoft.windows.sdk.buildtools",
                   "10.0.1", "bin")
for ver, arch in (("10.0.9.0", "x64"), ("10.0.19.0", "x64"), ("10.0.19.0", "x86"),
                  ("10.0.19.0", "arm64")):
    d = os.path.join(pkg, ver, arch)
    os.makedirs(d)
    open(os.path.join(d, "signtool.exe"), "wb").close()
bare_env = dict(os.environ)
bare_env["USERPROFILE"] = home
bare_env["ProgramFiles(x86)"] = os.path.join(scratch, "no_program_files")
bare_env["PATH"] = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                "System32")
rc, out = run_ps("Find-SignTool", env=bare_env)
want = os.path.join(pkg, "10.0.19.0", "x64", "signtool.exe")
check("signtool is found in the NuGet cache, newest version, this architecture",
      out.strip().lower(), want.lower())
shutil.rmtree(os.path.join(home, ".nuget"))
rc, out = run_ps("\"found=[$(Find-SignTool)]\"", env=bare_env)
check("and Find-SignTool returns nothing when there is none", out.strip(),
      "found=[]")

# Azure Artifact Signing, against a stub signtool and a stand-in dlib. Nothing
# reaches the service. Command line: signtool sign /fd SHA256 /tr <url>
# /td SHA256 /dlib <Azure.CodeSigning.Dlib.dll> /dmdf <metadata.json> <file>,
# with Endpoint, CodeSigningAccountName and CertificateProfileName in the JSON.
import json                                                     # noqa: E402

AS = os.path.join(scratch, "as")
as_tool_dir = os.path.join(AS, "tool", "x64")
as_dlib_dir = os.path.join(AS, "dlib", "bin", "x64")
as_dlib_x86 = os.path.join(AS, "dlib", "bin", "x86")
for d in (as_tool_dir, as_dlib_dir, as_dlib_x86):
    os.makedirs(d)
as_stub = os.path.join(as_tool_dir, "signtool.cmd")
as_log = os.path.join(AS, "stub.log")
with open(as_stub, "w") as fh:
    fh.write("@echo off\r\n"
             f"echo %*>>\"{as_log}\"\r\n"
             "exit /b %STUB_EXIT%\r\n")
as_dlib = os.path.join(as_dlib_dir, "Azure.CodeSigning.Dlib.dll")
for p in (as_dlib, os.path.join(as_dlib_x86, "Azure.CodeSigning.Dlib.dll")):
    open(p, "wb").close()
as_endpoint = "https://eus.codesigning.azure.net"
as_args = (f"-ArtifactEndpoint {ps_quote(as_endpoint)} -ArtifactAccount MyAcct "
           f"-ArtifactProfile MyProfile -ArtifactDlib {ps_quote(as_dlib)} "
           f"-SignToolPath {ps_quote(as_stub)} "
           f"-MetadataPath {ps_quote(os.path.join(AS, 'metadata.json'))}")
as_files = [os.path.join(AS, "CosmicSupremacyLauncher.exe"),
            os.path.join(AS, "CosmicSupremacyAI.exe")]
for f in as_files:
    with open(f, "wb") as fh:
        fh.write(b"MZ" + b"\0" * 64)

rc, out = run_ps(f"$c = Resolve-SigningConfig {as_args}; "
                 "\"kind=$($c.Kind) tl=$($c.TimestampUrl) dlib=$($c.Dlib)\"")
check("Artifact Signing settings resolve", rc, 0)
check("to the ArtifactSigning kind", "kind=ArtifactSigning" in out, True)
check("with Microsoft's recommended timestamp server",
      "tl=http://timestamp.acs.microsoft.com" in out, True)
check("and the dlib that was named", f"dlib={as_dlib}" in out, True)

rc, out = run_ps(
    f"Resolve-SigningConfig -Thumbprint {ps_quote('A' * 40)} {as_args}")
check("a thumbprint together with Artifact Signing settings is refused",
      rc != 0 and "configure one signer" in out, True)

rc, out = run_ps(f"Resolve-SigningConfig -ArtifactEndpoint {ps_quote(as_endpoint)}")
check("Artifact Signing with only an endpoint names what is missing",
      rc != 0 and "account name" in out and "certificate profile" in out, True)

for label, swap, want in (
        ("an endpoint that is not a codesigning.azure.net host",
         ("-ArtifactEndpoint 'https://eus.codesigning.azure.net'",
          "-ArtifactEndpoint 'https://example.com'"), "endpoint must look like"),
        ("an http endpoint",
         ("-ArtifactEndpoint 'https://eus.codesigning.azure.net'",
          "-ArtifactEndpoint 'http://eus.codesigning.azure.net'"),
         "endpoint must look like"),
        ("an account name of two characters",
         ("-ArtifactAccount MyAcct", "-ArtifactAccount ab"), "account name"),
        ("an account name with consecutive hyphens",
         ("-ArtifactAccount MyAcct", "-ArtifactAccount My--Acct"), "account name"),
        ("a profile name of four characters",
         ("-ArtifactProfile MyProfile", "-ArtifactProfile Prof"),
         "certificate profile name"),
        ("a profile name ending in a hyphen",
         ("-ArtifactProfile MyProfile", "-ArtifactProfile MyProfile-"),
         "certificate profile name")):
    rc, out = run_ps("Resolve-SigningConfig " + as_args.replace(*swap))
    check(f"{label} is refused", rc != 0 and want in out, True)

rc, out = run_ps("Resolve-SigningConfig " + as_args.replace(
    ps_quote(as_dlib), ps_quote(os.path.join(AS, "gone", "Azure.CodeSigning.Dlib.dll"))))
check("a dlib path that does not exist is refused",
      rc != 0 and "dlib not found" in out, True)

wrong = os.path.join(AS, "dlib", "bin", "x64", "other.dll")
open(wrong, "wb").close()
rc, out = run_ps("Resolve-SigningConfig " + as_args.replace(
    ps_quote(as_dlib), ps_quote(wrong)))
check("a dlib with the wrong file name is refused",
      rc != 0 and "must be named Azure.CodeSigning.Dlib.dll" in out, True)

rc, out = run_ps("Resolve-SigningConfig " + as_args.replace(
    ps_quote(as_dlib), ps_quote(os.path.join(as_dlib_x86, "Azure.CodeSigning.Dlib.dll"))))
check("a dlib of a different architecture from signtool is refused",
      rc != 0 and "must match" in out, True)

rc, out = run_ps("Resolve-SigningConfig " + as_args + " -ArtifactAuthExclude 'bad name'")
check("an excluded credential that is not a credential name is refused",
      rc != 0 and "not a credential name" in out, True)

check("the build's own default keeps an unattended run from opening a browser",
      "@('InteractiveBrowserCredential')" in BUILD_TEXT, True)
check("the build passes the Artifact Signing settings to the resolver",
      all(s in BUILD_TEXT for s in ("-ArtifactEndpoint $SignArtifactEndpoint",
                                    "-ArtifactAccount $SignArtifactAccount",
                                    "-ArtifactProfile $SignArtifactProfile",
                                    "-ArtifactDlib $SignArtifactDlib")), True)
check("from environment variables of the same style as the thumbprint",
      all(s in BUILD_TEXT for s in ("$env:CS_SIGN_AS_ENDPOINT",
                                    "$env:CS_SIGN_AS_ACCOUNT",
                                    "$env:CS_SIGN_AS_PROFILE",
                                    "$env:CS_SIGN_AS_DLIB")), True)

# Versions: Microsoft's page gives 10.0.2261.755 as the minimum and says the
# 20348 SDK is not supported with the dlib.
rc, out = run_ps(
    "foreach ($v in '10.0.28000.2705', '10.0.22621.755', '10.0.2261.755', "
    "'10.0.2261.754', '10.0.20348.1') { "
    "\"$v=[$(Test-SignToolVersion ([version]$v))]\" }")
got = dict(re.findall(r"(\S+)=\[(.*)\]", out))
check("current SDK signtool versions are accepted",
      (got.get("10.0.28000.2705"), got.get("10.0.22621.755"),
       got.get("10.0.2261.755")), ("", "", ""))
check("one below the stated minimum is refused",
      "older than" in got.get("10.0.2261.754", ""), True)
check("and the 20348 SDK is refused", "20348" in got.get("10.0.20348.1", ""), True)

# The dlib is found in the NuGet cache by architecture and newest version.
pkg_home = os.path.join(AS, "home")
for ver in ("1.0.115", "1.0.128"):
    for arch in ("x64", "x86"):
        d = os.path.join(pkg_home, ".nuget", "packages",
                         "microsoft.artifactsigning.client", ver, "bin", arch)
        os.makedirs(d)
        open(os.path.join(d, "Azure.CodeSigning.Dlib.dll"), "wb").close()
cache_env = dict(os.environ, USERPROFILE=pkg_home)
rc, out = run_ps("Find-ArtifactSigningDlib '' 'x86'", env=cache_env)
want = os.path.join(pkg_home, ".nuget", "packages",
                    "microsoft.artifactsigning.client", "1.0.128", "bin", "x86",
                    "Azure.CodeSigning.Dlib.dll")
check("the dlib is found in the NuGet cache, newest version, matching "
      "architecture", out.strip().lower(), want.lower())

# Then in the Client Tools install, which holds one x64 copy.
tools_home = os.path.join(AS, "localappdata")
tools_dir = os.path.join(tools_home, "Microsoft", "MicrosoftArtifactSigningClientTools")
os.makedirs(tools_dir)
tools_dlib = os.path.join(tools_dir, "Azure.CodeSigning.Dlib.dll")
open(tools_dlib, "wb").close()
empty_home = os.path.join(AS, "empty_home")
os.makedirs(empty_home)
tools_env = dict(os.environ, USERPROFILE=empty_home, LOCALAPPDATA=tools_home)
rc, out = run_ps("Find-ArtifactSigningDlib '' 'x64'", env=tools_env)
check("with no NuGet copy, the Client Tools dlib is found for x64",
      out.strip().lower(), tools_dlib.lower())
rc, out = run_ps("Find-ArtifactSigningDlib '' 'x86'", env=tools_env)
check("and is not offered for x86", out.strip(), "")
both_env = dict(os.environ, USERPROFILE=pkg_home, LOCALAPPDATA=tools_home)
rc, out = run_ps("Find-ArtifactSigningDlib '' 'x64'", env=both_env)
check("the NuGet cache still comes first when both are present",
      out.strip().lower(), want.replace("\\x86\\", "\\x64\\").lower())
none_env = dict(os.environ, USERPROFILE=empty_home,
                LOCALAPPDATA=os.path.join(AS, "empty_localappdata"))
rc, out = run_ps("Find-ArtifactSigningDlib '' 'x64'", env=none_env)
check("with neither, nothing is found", out.strip(), "")

# The command line and the metadata file, from the real function.
env = dict(os.environ, STUB_EXIT="0")
open(as_log, "w").close()
rc, out = run_ps(
    f"$c = Resolve-SigningConfig {as_args} "
    "-ArtifactAuthExclude InteractiveBrowserCredential; "
    f"function Assert-SignedFile {{ }} "
    f"Invoke-ReleaseSigning -Config $c -Path @({ps_quote(as_files[0])}, "
    f"{ps_quote(as_files[1])})", env=env)
lines = [l for l in open(as_log).read().splitlines() if l.strip()]
check("Artifact Signing signs two files with two signtool runs", (rc, len(lines)),
      (0, 2))
check("naming exactly the launcher and the AI, in that order",
      [os.path.basename(re.findall(r"(\S+\.exe)\s*$", l)[0]) for l in lines],
      ["CosmicSupremacyLauncher.exe", "CosmicSupremacyAI.exe"])
meta_path = os.path.join(AS, "metadata.json")
for l in lines:
    check("each call is sign, SHA-256 digests, Microsoft's timestamp server, "
          "the dlib and the metadata file",
          all(s in l for s in ("sign ", "/fd SHA256", "/td SHA256",
                               "/tr http://timestamp.acs.microsoft.com",
                               f"/dlib {as_dlib}", f"/dmdf {meta_path}")), True)
    check("and no certificate selector", "/sha1" in l or "/a " in l, False)
meta = json.load(open(meta_path, encoding="utf-8"))
check("the metadata file carries the keys the dlib reads", meta, {
    "Endpoint": as_endpoint, "CodeSigningAccountName": "MyAcct",
    "CertificateProfileName": "MyProfile",
    "ExcludeCredentials": ["InteractiveBrowserCredential"]})
check("and starts without a byte order mark",
      open(meta_path, "rb").read()[:1], b"{")

rc, out = run_ps(
    f"$c = Resolve-SigningConfig {as_args} -ArtifactAuthExclude @(); "
    f"function Assert-SignedFile {{ }} Invoke-ReleaseSigning -Config $c "
    f"-Path @({ps_quote(as_files[0])})", env=env)
check("with nothing to exclude the metadata has no ExcludeCredentials",
      "ExcludeCredentials" in json.load(open(meta_path, encoding="utf-8")), False)

open(as_log, "w").close()
rc, out = run_ps(
    f"$c = Resolve-SigningConfig {as_args}; function Assert-SignedFile {{ }} "
    f"Invoke-ReleaseSigning -Config $c -Path @({ps_quote(as_files[0])}) "
    "-Attempts 2 -RetryDelaySeconds 0", env=dict(os.environ, STUB_EXIT="1"))
check("a failing signtool fails the Artifact Signing build after retrying",
      rc != 0 and "signtool failed for CosmicSupremacyLauncher.exe" in out
      and len(open(as_log).read().splitlines()) == 2, True)

rc, out = run_ps(
    f"$c = Resolve-SigningConfig {as_args}; Invoke-ReleaseSigning -Config $c "
    f"-Path @({ps_quote(as_files[0])})", env=env)
check("and so does one that reports success on an unsigned file",
      rc != 0 and "carries no signature" in out, True)

secrets = ("secret", "password", "token")
check("nothing secret is written by the step",
      any(s in json.dumps(meta).lower() for s in secrets), False)
shutil.rmtree(AS, ignore_errors=True)

# The functions against a stub signtool. The stub tests and the real signing
# test share one throwaway certificate, which the configuration step needs.
SUBJECT = f"CN=CS signing test {os.getpid()}"
plain_subject = f"CN=CS signing test plain {os.getpid()}"
thumbs = []


def make_cert(subject, code_signing=True):
    kind = "-Type CodeSigningCert" if code_signing else ""
    rc, out = run_ps(
        f"$c = New-SelfSignedCertificate -Subject {ps_quote(subject)} {kind} "
        "-CertStoreLocation Cert:\\CurrentUser\\My "
        "-NotAfter (Get-Date).AddDays(2); $c.Thumbprint")
    thumb = out.strip().splitlines()[-1].strip() if rc == 0 else ""
    if re.fullmatch(r"[0-9A-F]{40}", thumb):
        thumbs.append(thumb)
        return thumb
    print(out)
    return None


def remove_certs():
    for t in thumbs:
        run_ps(f"Remove-Item -LiteralPath Cert:\\CurrentUser\\My\\{t} -Force "
               "-ErrorAction SilentlyContinue")


try:
    thumb = make_cert(SUBJECT)
    plain = make_cert(plain_subject, code_signing=False)
    check("a throwaway code signing certificate was created", bool(thumb), True)

    if thumb:
        stub = os.path.join(scratch, "stub_signtool.cmd")
        log = os.path.join(scratch, "stub.log")
        with open(stub, "w") as fh:
            fh.write("@echo off\r\n"
                     f"echo %*>>\"{log}\"\r\n"
                     "exit /b %STUB_EXIT%\r\n")
        a = os.path.join(scratch, "CosmicSupremacyLauncher.exe")
        b = os.path.join(scratch, "CosmicSupremacyAI.exe")
        for f in (a, b):
            with open(f, "wb") as fh:
                fh.write(b"MZ" + b"\0" * 64)
        url = "http://timestamp.example/rfc3161"
        config = (f"$cfg = Resolve-SigningConfig -Thumbprint {ps_quote(thumb)} "
                  f"-TimestampUrl {ps_quote(url)} -SignToolPath {ps_quote(stub)}; ")

        rc, out = run_ps(config + "\"kind=$($cfg.Kind) tool=$($cfg.SignTool)\"")
        check("a configured certificate resolves", rc, 0)
        check("and records the signtool it will run",
              f"tool={stub}" in out and "kind=CertStore" in out, True)

        rc, out = run_ps(
            f"Resolve-SigningConfig -Thumbprint {ps_quote(plain)} "
            f"-TimestampUrl {ps_quote(url)} -SignToolPath {ps_quote(stub)}")
        check("a certificate that is not for code signing is refused",
              rc != 0 and "not a code signing certificate" in out, True)

        spaced = " ".join(thumb[i:i + 4] for i in range(0, 40, 4)).lower()
        rc, out = run_ps(
            f"$c = Resolve-SigningConfig -Thumbprint {ps_quote(spaced)} "
            f"-TimestampUrl {ps_quote(url)} -SignToolPath {ps_quote(stub)}; "
            "$c.Thumbprint")
        check("a thumbprint typed in lower case with spaces is accepted",
              out.strip().splitlines()[-1], thumb)

        env = dict(os.environ, STUB_EXIT="0")
        open(log, "w").close()
        rc, out = run_ps(
            config + "function Assert-SignedFile { } "
            f"Invoke-ReleaseSigning -Config $cfg -Path @({ps_quote(a)}, {ps_quote(b)})",
            env=env)
        lines = [l for l in open(log).read().splitlines() if l.strip()]
        check("signing two files runs signtool twice", (rc, len(lines)), (0, 2))
        named = [re.findall(r"(\S+\.exe)\s*$", l) for l in lines]
        check("naming exactly the launcher and the AI, in that order",
              [os.path.basename(n[0]) for n in named if n],
              ["CosmicSupremacyLauncher.exe", "CosmicSupremacyAI.exe"])
        for l in lines:
            check("each call asks for SHA-256 file and timestamp digests, "
                  "an RFC 3161 server and the certificate by thumbprint",
                  all(s in l for s in ("sign ", "/fd SHA256", "/td SHA256",
                                       f"/tr {url}", f"/sha1 {thumb}")), True)

        open(log, "w").close()
        env = dict(os.environ, STUB_EXIT="1")
        rc, out = run_ps(
            config + "function Assert-SignedFile { } "
            "Invoke-ReleaseSigning -Config $cfg "
            f"-Path @({ps_quote(a)}, {ps_quote(b)}) -Attempts 2 -RetryDelaySeconds 0",
            env=env)
        lines = [l for l in open(log).read().splitlines() if l.strip()]
        check("a signtool that fails fails the build", rc != 0 and
              "signtool failed for CosmicSupremacyLauncher.exe" in out, True)
        check("after retrying, and before touching the second file",
              [os.path.basename(re.findall(r"(\S+\.exe)\s*$", l)[0])
               for l in lines],
              ["CosmicSupremacyLauncher.exe"] * 2)

        env = dict(os.environ, STUB_EXIT="0")
        rc, out = run_ps(
            config + f"Invoke-ReleaseSigning -Config $cfg -Path @({ps_quote(a)})",
            env=env)
        check("a signtool that reports success on a file it did not sign "
              "fails the build", rc != 0 and "carries no signature" in out, True)

        # A real signtool, a throwaway certificate, a copy of a small exe
        tool_rc, tool_out = run_ps(
            "$t = Find-SignTool $env:CS_SIGNTOOL; if ($t) { $t }")
        tool = tool_out.strip().splitlines()[-1] if tool_out.strip() else ""
        host = "timestamp.digicert.com"
        try:
            socket.create_connection((host, 80), timeout=5).close()
            online = True
        except OSError:
            online = False
        default_url = re.search(r"CertStoreTimestamp\s*=\s*'(http[^']+)'", BUILD_TEXT)
        check("the default timestamp server is the one this test reaches",
              default_url and host in default_url.group(1), True)
        if not (tool and os.path.isfile(tool)):
            skip("real signing", "no signtool.exe found")
        elif not online:
            skip("real signing", f"{host} is not reachable")
        else:
            # The interpreter carries no signature of its own.
            sample = os.path.join(scratch, "sample.exe")
            shutil.copyfile(sys.executable, sample)
            real = (f"$cfg = Resolve-SigningConfig -Thumbprint {ps_quote(thumb)} "
                    f"-TimestampUrl http://{host} -SignToolPath {ps_quote(tool)} "
                    "-AllowUntrusted; ")
            rc, out = run_ps(
                real + f"Invoke-ReleaseSigning -Config $cfg -Path @({ps_quote(sample)})")
            check("a real signtool signs a copy of a small executable", rc, 0)
            rc, out = run_ps(
                f"$s = Get-AuthenticodeSignature -LiteralPath {ps_quote(sample)}; "
                "\"signer=$($s.SignerCertificate.Thumbprint) "
                "stamped=$($null -ne $s.TimeStamperCertificate) "
                "status=$($s.Status)\"")
            check("the signature reads back as ours", f"signer={thumb}" in out, True)
            check("with a timestamp", "stamped=True" in out, True)
            check("and untrusted, as a self-signed certificate is",
                  "status=Valid" not in out and "status=" in out, True)
            rc, out = run_ps(
                real.replace(" -AllowUntrusted", "")
                + f"Invoke-ReleaseSigning -Config $cfg -Path @({ps_quote(sample)})")
            check("verification fails an untrusted signature unless it is allowed",
                  rc != 0 and "signature status is" in out, True)
finally:
    remove_certs()
    shutil.rmtree(scratch, ignore_errors=True)

rc, out = run_ps(
    f"@(Get-ChildItem Cert:\\CurrentUser\\My | Where-Object "
    f"{{ $_.Subject -like 'CN=CS signing test*{os.getpid()}' }}).Count")
check("the throwaway certificates are gone", out.strip().splitlines()[-1], "0")

print()
if fails:
    print("FAILED: " + ", ".join(fails))
    sys.exit(1)
print("signing checks passed")
