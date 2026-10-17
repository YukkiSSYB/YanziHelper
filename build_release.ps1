param(
    [string]$PythonExe = ''
)

$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
$toolDirectory = Join-Path $projectDirectory '.build-tools'
$releaseDirectory = Join-Path $projectDirectory 'release'
$buildDirectory = Join-Path $projectDirectory 'build_release'
$stagingDirectory = Join-Path $buildDirectory ('candidate-' + [guid]::NewGuid().ToString('N'))
$specFile = Join-Path $projectDirectory 'YanziHelper.release.spec'

if (-not $PythonExe) {
    $venvPython = Join-Path $projectDirectory '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $PythonExe = $venvPython
    } else {
        $pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($pythonCommand) {
            $PythonExe = $pythonCommand.Source
        }
    }
} elseif (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    $pythonCommand = Get-Command $PythonExe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pythonCommand) {
        $PythonExe = $pythonCommand.Source
    }
}
if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python runtime not found: $PythonExe. Pass -PythonExe with a Python 3.12+ installation including tkinter."
}
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).ProviderPath

$requiredResources = @('ddhid.63340.dll', 'start.wav', 'stop.wav')
$missingResources = @($requiredResources | Where-Object {
    -not (Test-Path -LiteralPath (Join-Path $projectDirectory $_) -PathType Leaf)
})
if ($missingResources.Count -gt 0) {
    throw ('Required release resources are missing: ' + ($missingResources -join ', ') + '. Add the matching resource files described in README.md before building.')
}
$configSource = Join-Path $projectDirectory 'config.ini'
if (-not (Test-Path -LiteralPath $configSource -PathType Leaf)) {
    $configSource = Join-Path $projectDirectory 'config.example.ini'
}
if (-not (Test-Path -LiteralPath $configSource -PathType Leaf)) {
    throw 'Release configuration is missing. Provide config.ini or config.example.ini.'
}

$previousPythonPath = $env:PYTHONPATH
$previousBuildCache = $env:PYINSTALLER_CONFIG_DIR
Push-Location -LiteralPath $projectDirectory
try {
    if (Test-Path -LiteralPath (Join-Path $toolDirectory 'PyInstaller') -PathType Container) {
        $env:PYTHONPATH = $toolDirectory
        if ($previousPythonPath) {
            $env:PYTHONPATH += [IO.Path]::PathSeparator + $previousPythonPath
        }
    }
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $toolDirectory 'cache'
    & $PythonExe -B -c 'import PyInstaller'
    if ($LASTEXITCODE -ne 0) {
        throw 'PyInstaller is unavailable. Install requirements-dev.txt into this Python environment, or install locally with: python -m pip install --target .build-tools -r requirements-dev.txt'
    }
    & $PythonExe -B -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "Regression tests failed with exit code $LASTEXITCODE; release was not changed."
    }
    & $PythonExe -m PyInstaller --noconfirm --clean --distpath $stagingDirectory --workpath $buildDirectory $specFile
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    # Verify the newly built EXE before replacing the user's working release.
    # --self-test patches input, sound and profile writes; no DD driver is loaded.
    $candidateExe = Join-Path $stagingDirectory 'YanziHelper.exe'
    $selfTestReport = Join-Path $stagingDirectory 'selftest-result.json'
    $selfTestProcess = Start-Process -FilePath $candidateExe -ArgumentList @('--self-test', ('"{0}"' -f $selfTestReport)) -PassThru -WindowStyle Hidden
    if (-not $selfTestProcess.WaitForExit(30000)) {
        $selfTestProcess.Kill($true)
        throw 'Packaged self-test timed out; release was not changed.'
    }
    if ($selfTestProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $selfTestReport)) {
        throw "Packaged self-test failed with exit code $($selfTestProcess.ExitCode); release was not changed."
    }
    $report = Get-Content -LiteralPath $selfTestReport -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($report.status -ne 'PASS' -or $report.frozen -ne $true -or $report.real_driver_loaded -ne $false -or $report.global_hook_started -ne $false) {
        throw 'Packaged self-test did not confirm a safe PASS; release was not changed.'
    }
    New-Item -ItemType Directory -Path $releaseDirectory -Force | Out-Null
    $releaseExe = Join-Path $releaseDirectory 'YanziHelper.exe'
    if (Test-Path -LiteralPath $releaseExe) {
        $backupDirectory = Join-Path $projectDirectory ('backups\release-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $backupDirectory | Out-Null
        Copy-Item -LiteralPath $releaseExe -Destination (Join-Path $backupDirectory 'YanziHelper.exe')
    }
    Copy-Item -LiteralPath $candidateExe -Destination $releaseExe -Force
    Copy-Item -LiteralPath $selfTestReport -Destination (Join-Path $releaseDirectory 'selftest-result.json') -Force

    # Keep the project's original configuration, and preserve settings from a
    # previously used release when rebuilding in place.
    $releaseConfig = Join-Path $releaseDirectory 'config.ini'
    if (-not (Test-Path -LiteralPath $releaseConfig)) {
        Copy-Item -LiteralPath $configSource -Destination $releaseConfig
    }
    $guidePath = Join-Path $projectDirectory '使用说明.txt'
    if ((Test-Path -LiteralPath $guidePath) -and (Get-Item -LiteralPath $guidePath).Length -gt 0) {
        Copy-Item -LiteralPath $guidePath -Destination (Join-Path $releaseDirectory '使用说明.txt') -Force
    }
    foreach ($noticeName in @('LICENSE', 'NOTICE.md', 'LICENSES\pydd-MIT.txt')) {
        $noticePath = Join-Path $projectDirectory $noticeName
        if (Test-Path -LiteralPath $noticePath -PathType Leaf) {
            $releaseNoticePath = Join-Path $releaseDirectory $noticeName
            New-Item -ItemType Directory -Path (Split-Path -Parent $releaseNoticePath) -Force | Out-Null
            Copy-Item -LiteralPath $noticePath -Destination $releaseNoticePath -Force
        }
    }
    & $PythonExe -B (Join-Path $projectDirectory 'tools\package_release.py')
    if ($LASTEXITCODE -ne 0) {
        throw "Release ZIP verification failed with exit code $LASTEXITCODE"
    }
    Get-Item -LiteralPath (Join-Path $releaseDirectory 'YanziHelper.exe') | Select-Object FullName, Length, LastWriteTime
} finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:PYINSTALLER_CONFIG_DIR = $previousBuildCache
    Pop-Location
}
