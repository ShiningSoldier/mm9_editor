param(
    [string]$ObjectLto = "C:\Program Files (x86)\GOG Galaxy\Games\Might and Magic 9\data\object.lto",
    [string]$Exe = "$PSScriptRoot\bin\object_lto_dump.exe"
)

$ErrorActionPreference = "Stop"

if (!(Test-Path -LiteralPath $Exe)) {
    throw "Dumper executable not found at $Exe. Run build.ps1 first."
}
if (!(Test-Path -LiteralPath $ObjectLto)) {
    throw "object.lto not found at $ObjectLto"
}

$jsonText = & $Exe $ObjectLto
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$dump = $jsonText | ConvertFrom-Json
if ($dump.schema -ne "mm9_editor.object_lto_dump.v1") {
    throw "Unexpected schema: $($dump.schema)"
}

$requiredClasses = @(
    "Honk",
    "Honk2",
    "ElderHonk",
    "ElderHonkFemale",
    "HonkSeer",
    "LizardOrc",
    "LizardOrcWarrior",
    "LizardOrcMage"
)

$classNames = @{}
foreach ($class in $dump.classes) {
    $classNames[$class.name] = $true
}

foreach ($name in $requiredClasses) {
    if (!$classNames.ContainsKey($name)) {
        throw "Missing expected class: $name"
    }
}

Write-Host "Validated $($dump.class_count) classes from $ObjectLto"
