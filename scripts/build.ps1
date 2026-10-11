# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
<#
.SYNOPSIS
    Builds EchoSubLens.exe (a one-folder app in dist\EchoSubLens) with PyInstaller.

.DESCRIPTION
    1. Creates .venv (Python 3.10) and installs requirements-build.txt when needed.
    2. Writes the Windows version resource from echosub_lens\__init__.py.
    3. Runs PyInstaller with EchoSubLens.spec.
    The result runs on any 64-bit Windows 10/11 PC without Python installed.

.PARAMETER Clean
    Delete build\ and dist\EchoSubLens first for a from-scratch build.

.PARAMETER SkipInstall
    Don't check or install dependencies.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Clean
#>
param(
    [switch]$Clean,
    [switch]$SkipInstall
)

. (Join-Path $PSScriptRoot "common.ps1")
Set-Location $Root
$started = Get-Date
$version = Get-AppVersion
$buildDir = Join-Path $Root "build"
$distApp = Join-Path $Root "dist\EchoSubLens"

if ($Clean) {
    Write-Step "Cleaning build\ and dist\EchoSubLens"
    foreach ($dir in @($buildDir, $distApp)) {
        if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
    }
}

if (-not $SkipInstall) {
    Initialize-Venv
    Install-Requirements "requirements-build.txt"
}

Write-Step "Writing the version resource for EchoSub Lens $version"
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
$parts = ($version -split "[.\-+]" | Select-Object -First 3) + @("0", "0", "0") | Select-Object -First 4
$tuple = ($parts | ForEach-Object { [int]$_ }) -join ", "
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($tuple), prodvers=($tuple), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Mohammad Al-Safadi'),
      StringStruct('FileDescription', 'EchoSub Lens - translate the text on your screen'),
      StringStruct('FileVersion', '$version'),
      StringStruct('InternalName', 'EchoSubLens'),
      StringStruct('LegalCopyright', 'Copyright (C) 2026 Mohammad Al-Safadi. GPL-3.0.'),
      StringStruct('OriginalFilename', 'EchoSubLens.exe'),
      StringStruct('ProductName', 'EchoSub Lens'),
      StringStruct('ProductVersion', '$version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Path (Join-Path $buildDir "version_info.txt") -Encoding UTF8

Write-Step "Running PyInstaller (takes a few minutes)"
Invoke-Checked $Python @("-m", "PyInstaller", (Join-Path $Root "EchoSubLens.spec"), "--noconfirm",
    "--distpath", (Join-Path $Root "dist"), "--workpath", (Join-Path $buildDir "pyinstaller"))

$exe = Join-Path $distApp "EchoSubLens.exe"
if (-not (Test-Path $exe)) { throw "Build finished but $exe is missing." }
$size = (Get-ChildItem $distApp -Recurse -File | Measure-Object Length -Sum).Sum
Write-Step "Built EchoSub Lens $version"
Write-Host "  App:      $exe"
Write-Host "  Size:     $(Format-Size $size)"
Write-Host "  Duration: $([int]((Get-Date) - $started).TotalMinutes) min $([int]((Get-Date) - $started).Seconds) s"
