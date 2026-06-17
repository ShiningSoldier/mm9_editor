param(
    [string]$OutDir = "$PSScriptRoot\bin"
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$source = Join-Path $PSScriptRoot "object_lto_dump.cpp"
$output = Join-Path $OutDir "object_lto_dump.exe"
$obj = Join-Path $OutDir "object_lto_dump.obj"

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

& $cl /nologo /EHsc /std:c++17 /O2 /W4 /DUNICODE /D_UNICODE $source /Fe:$output /Fo:$obj
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Built $output"
