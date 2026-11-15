# Build locally from a tested release and the four original files you obtained.
# Vendor hashes identify the supported DD HID 63340 kit; the app is bound to its local test report.
[CmdletBinding(DefaultParameterSetName='Build')]
param(
    [Parameter(Mandatory, ParameterSetName='Build')][string]$DriverDirectory,
    [Parameter(ParameterSetName='Build')][string]$ReleaseDirectory,
    [Parameter(Mandatory, ParameterSetName='Test')][switch]$TestOnly,
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$compiler = @('Framework64', 'Framework') | ForEach-Object {
    Join-Path $env:WINDIR "Microsoft.NET\$_\v4.0.30319\csc.exe"
} | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $compiler) { throw 'Windows .NET Framework C# compiler not found.' }

$hashes = [ordered]@{App=''; Installer=''; Driver=''; Definition=''; Catalog=''}
$inputsForTest = @()
if (-not $TestOnly) {
    if (-not $ReleaseDirectory) { $ReleaseDirectory = Join-Path $project 'release' }
    $release = (Resolve-Path -LiteralPath $ReleaseDirectory).ProviderPath
    $driver = (Resolve-Path -LiteralPath $DriverDirectory).ProviderPath
    $report = Get-Content -LiteralPath (Join-Path $release 'selftest-result.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($report.status -cne 'PASS' -or $report.frozen -isnot [bool] -or -not $report.frozen -or
        $report.real_driver_loaded -isnot [bool] -or $report.real_driver_loaded -or
        $report.global_hook_started -isnot [bool] -or $report.global_hook_started) {
        throw 'The release has not passed its packaged self-test.'
    }
    $hashes.App = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $release 'YanziHelper.exe')).Hash
    if ($report.executable_sha256 -ne $hashes.App) { throw 'The executable does not match the self-test report.' }
    $names = [ordered]@{Installer='ddc.exe'; Driver='ddhid63340.sys'; Definition='ddhid63340.inf'; Catalog='ddhid63340.cat'}
    # Fingerprints of the original, unmodified DD HID 63340 x64 distribution.
    # Never enroll arbitrary files from DriverDirectory as valid new hashes.
    $vendor = @{
        Installer='3c535b334f0897b8a0870bcb476c30ea79afd09cfc18f8e00190bdc7c6c46785'
        Driver='fbe510402b3822c63e94752051b7d5895b67875f22ec48593de19764a649f8b1'
        Definition='17fe3814f57e98dd2af97f56b63502e474ea5e41cda1a510ffe435ee6ad7a104'
        Catalog='6135c664711127a62e0988f6844521e345d78ace9d3747a392400ce99be96983'
    }
    $entries = @(Get-ChildItem -LiteralPath $driver -Force)
    if ($entries.Count -ne $names.Count) { throw 'DriverDirectory must contain only the four matching vendor files.' }
    foreach ($entry in $entries) {
        if ($entry.PSIsContainer -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $entry.Name -notin $names.Values) { throw "Unexpected driver folder entry: $($entry.Name)" }
    }
    foreach ($key in $names.Keys) {
        $digest = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $driver $names[$key])).Hash
        if ($digest -ne $vendor[$key]) { throw "Unsupported DD HID 63340 payload: $($names[$key])" }
        $hashes[$key] = $vendor[$key]
    }
    $inputsForTest = @($release, $driver)
}

$id = [Guid]::NewGuid().ToString('N')
if (-not $OutputDirectory) {
    if ($TestOnly) { $OutputDirectory = [IO.Path]::GetTempPath() }
    else { $OutputDirectory = Join-Path $project "build_release\driver-kit-$id" }
}
$output = [IO.Directory]::CreateDirectory($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)).FullName
$work = [IO.Directory]::CreateDirectory((Join-Path $output ".launcher-build-$id")).FullName
$buildHashes = Join-Path $work 'BuildHashes.cs'
$tester = Join-Path $work 'TestLauncher.exe'
$launcher = Join-Path $work 'launcher.exe'
try {
    $constants = foreach ($key in $hashes.Keys) { 'internal const string {0} = "{1}";' -f $key, $hashes[$key] }
    [IO.File]::WriteAllText($buildHashes, "namespace YanziDriverKit { internal static class BuildHashes { $($constants -join ' ') } }")
    $compile = @('/nologo','/utf8output','/codepage:65001','/optimize+','/platform:anycpu','/r:System.Windows.Forms.dll')
    $source = Join-Path $PSScriptRoot 'Launcher.cs'
    & $compiler @compile '/target:exe' '/main:TestLauncher' "/out:$tester" $source (Join-Path $PSScriptRoot 'TestLauncher.cs') $buildHashes
    if ($LASTEXITCODE -ne 0) { throw 'Launcher test compilation failed.' }
    & $tester @inputsForTest
    if ($LASTEXITCODE -ne 0) { throw 'Launcher verification failed.' }
    if (-not $TestOnly) {
        $manifest = Join-Path $PSScriptRoot 'launcher.manifest'
        & $compiler @compile '/target:winexe' "/win32manifest:$manifest" "/out:$launcher" $source $buildHashes
        if ($LASTEXITCODE -ne 0) { throw 'Launcher compilation failed.' }
        # Unicode filename kept independent of the source encoding in Windows PowerShell 5.1.
        $name = -join [char[]](0x542F,0x52A8,0x52A9,0x624B)
        $destination = Join-Path $output ($name + '.exe')
        [IO.File]::Move($launcher, $destination)  # Never overwrite an existing deliverable.
        Get-Item -LiteralPath $destination | Select-Object FullName, Length
    }
} finally {
    foreach ($path in @($buildHashes,$tester,$launcher)) {
        if ([IO.File]::Exists($path)) { [IO.File]::Delete($path) }
    }
    [IO.Directory]::Delete($work)
}
