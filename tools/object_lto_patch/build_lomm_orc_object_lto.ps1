param(
    [Parameter(Mandatory=$true)]
    [string]$MM9Root,
    [string]$SourceObjectLto,
    [string]$OutDir = "$PSScriptRoot\..\..\output\lomm_orc_object_lto_candidate"
)

$ErrorActionPreference = "Stop"

if (!$SourceObjectLto) {
    $SourceObjectLto = Join-Path (Join-Path ([System.IO.Path]::GetFullPath($MM9Root)) "data") "object.lto"
}

if (!(Test-Path -LiteralPath $SourceObjectLto)) {
    throw "Source object.lto was not found: $SourceObjectLto"
}

$msvcRoot = Get-ChildItem -Path "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC" -Directory |
    Sort-Object Name -Descending |
    Select-Object -First 1
if (!$msvcRoot) {
    throw "Visual Studio 2022 BuildTools MSVC directory was not found"
}

$sdkInclude = Get-ChildItem -Path "${env:ProgramFiles(x86)}\Windows Kits\10\Include" -Directory |
    Sort-Object Name -Descending |
    Select-Object -First 1
$sdkLib = Get-ChildItem -Path "${env:ProgramFiles(x86)}\Windows Kits\10\Lib" -Directory |
    Sort-Object Name -Descending |
    Select-Object -First 1
if (!$sdkInclude -or !$sdkLib) {
    throw "Windows 10 SDK include/lib directories were not found"
}

$cl = Join-Path $msvcRoot.FullName "bin\Hostx64\x86\cl.exe"
if (!(Test-Path -LiteralPath $cl)) {
    throw "x86 cl.exe was not found at $cl"
}

$env:PATH = @(
    (Join-Path $msvcRoot.FullName "bin\Hostx64\x86"),
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\$($sdkInclude.Name)\x86",
    $env:PATH
) -join ";"
$env:INCLUDE = @(
    (Join-Path $msvcRoot.FullName "include"),
    (Join-Path $sdkInclude.FullName "ucrt"),
    (Join-Path $sdkInclude.FullName "shared"),
    (Join-Path $sdkInclude.FullName "um"),
    (Join-Path $sdkInclude.FullName "winrt"),
    (Join-Path $sdkInclude.FullName "cppwinrt")
) -join ";"
$env:LIB = @(
    (Join-Path $msvcRoot.FullName "lib\x86"),
    (Join-Path $sdkLib.FullName "ucrt\x86"),
    (Join-Path $sdkLib.FullName "um\x86")
) -join ";"

$resolvedOut = [System.IO.Path]::GetFullPath($OutDir)
$dataDir = Join-Path $resolvedOut "data"
$buildDir = Join-Path $resolvedOut "build"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

$source = Join-Path $PSScriptRoot "object_lto_lomm_orc_wrapper.cpp"
$def = Join-Path $PSScriptRoot "object_lto_lomm_orc_wrapper.def"
$obj = Join-Path $buildDir "object_lto_lomm_orc_wrapper.obj"
$implib = Join-Path $buildDir "object.lib"
$dll = Join-Path $dataDir "object.lto"
$base = Join-Path $dataDir "object_lto_base.lto"

Copy-Item -LiteralPath $SourceObjectLto -Destination $base -Force

Remove-Item -LiteralPath (Join-Path $dataDir "object.lib") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $dataDir "object.exp") -Force -ErrorAction SilentlyContinue

& $cl /nologo /EHsc /std:c++17 /O2 /W4 /DUNICODE /D_UNICODE /LD $source /Fe:$dll /Fo:$obj /link /DEF:$def /IMPLIB:$implib
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$manifest = [ordered]@{
    version = 1
    kind = "object_lto_candidate"
    created_at = Get-Date -Format "yyyyMMdd_HHmmss"
    source_object_lto = [System.IO.Path]::GetFullPath($SourceObjectLto)
    output_dir = $resolvedOut
    files = @(
        [ordered]@{
            source_file = $SourceObjectLto
            output_file = $base
            target_relative = "data\object_lto_base.lto"
            kind = "original-base-module"
        },
        [ordered]@{
            source_file = $source
            output_file = $dll
            target_relative = "data\object.lto"
            kind = "wrapper-object-lto"
        }
    )
    object_lto_patch = [ordered]@{
        strategy = "wrapper-appended-classdef-row-bound-constructor"
        candidate_class = "LoMMOrcMage"
        parent_class = "LizardOrcMage"
        target_row = "121"
        declared_properties = @()
        notes = @(
            "Wrapper loads data\object_lto_base.lto and forwards the original exports.",
            "ObjectDLLSetup appends LoMMOrcMage as a visible/runtime-loadable child of LizardOrcMage.",
            "Construction calls MM9's shared actor row constructor with actor ID 121, then assigns the LizardOrcMage vtable.",
            "Destruction/plugin callbacks and object size are inherited from LizardOrcMage.",
            "Actor ID 121 is the stock Dwarven Soldier actor-table row. DwarvenSoldier has zero shipped DAT instances, so this is a sacrificial-slot experiment.",
            "This is experimental until in-game row selection is verified with stock row 191."
        )
    }
}

$manifestPath = Join-Path $resolvedOut "manifest.json"
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Host "Built candidate object.lto batch: $resolvedOut"
Write-Host "wrapper: $dll"
Write-Host "base: $base"
Write-Host "manifest: $manifestPath"
