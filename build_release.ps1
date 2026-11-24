param(
    [string]$PythonExe = '',
    [switch]$CandidateOnly
)

$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
$toolDirectory = Join-Path $projectDirectory '.build-tools'
$releaseDirectory = Join-Path $projectDirectory 'release'
$buildDirectory = Join-Path $projectDirectory 'build_release'
$buildId = [guid]::NewGuid().ToString('N')
$stagingDirectory = Join-Path $buildDirectory ('candidate-' + $buildId)
$workDirectory = Join-Path $buildDirectory ('work-' + $buildId)
$specFile = Join-Path $projectDirectory 'YanziHelper.release.spec'
$packageTool = Join-Path $projectDirectory 'tools\package_release.py'

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

$requiredResources = @('ddhid.63340.dll', 'start.wav', 'stop.wav', 'assets/yunshang.png', 'assets/feitian.png', 'assets/brand.png')
$missingResources = @($requiredResources | Where-Object {
    -not (Test-Path -LiteralPath (Join-Path $projectDirectory $_) -PathType Leaf)
})
if ($missingResources.Count -gt 0) {
    throw ('Required release resources are missing: ' + ($missingResources -join ', ') + '. Add the matching resource files described in docs/BUILD.md before building.')
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
    & $PythonExe -B $packageTool --validate-config $configSource
    if ($LASTEXITCODE -ne 0) {
        throw 'The build configuration is invalid; release was not changed.'
    }
    $releaseConfig = Join-Path $releaseDirectory 'config.ini'
    if (-not $CandidateOnly -and (Test-Path -LiteralPath $releaseConfig)) {
        & $PythonExe -B $packageTool --validate-config $releaseConfig
        if ($LASTEXITCODE -ne 0) {
            throw 'The preserved release configuration is invalid; release was not changed.'
        }
    }
    if (Test-Path -LiteralPath (Join-Path $toolDirectory 'PyInstaller') -PathType Container) {
        $env:PYTHONPATH = $toolDirectory
        if ($previousPythonPath) {
            $env:PYTHONPATH += [IO.Path]::PathSeparator + $previousPythonPath
        }
    }
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $workDirectory 'cache'
    & $PythonExe -B -c 'import PyInstaller'
    if ($LASTEXITCODE -ne 0) {
        throw 'PyInstaller is unavailable. Install requirements-dev.txt into this Python environment, or install locally with: python -m pip install --target .build-tools -r requirements-dev.txt'
    }
    & $PythonExe -B -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "Regression tests failed with exit code $LASTEXITCODE; release was not changed."
    }
    & $PythonExe -m PyInstaller --noconfirm --clean --distpath $stagingDirectory --workpath $workDirectory $specFile
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
    if ($report.status -ne 'PASS' -or $report.frozen -ne $true -or $report.input_backend -ne 'PyDD' -or $report.real_input_sent -ne $false -or $report.real_driver_loaded -ne $false -or $report.global_hook_started -ne $false) {
        throw 'Packaged self-test did not confirm a safe PASS; release was not changed.'
    }
    if ($CandidateOnly) {
        & $PythonExe -B $packageTool --verify-candidate $stagingDirectory --config $configSource
        if ($LASTEXITCODE -ne 0) {
            throw 'Candidate verification failed.'
        }
        Get-Item -LiteralPath $candidateExe | Select-Object FullName, Length, LastWriteTime
        return
    }
    # Python verifies the tested EXE/configuration hashes, preserves the user's
    # release profile, assembles and checks ZIP, then commits both artifacts.
    # Completed moves are rolled back if publication fails; no EXE is copied
    # over a working release in place.
    & $PythonExe -B $packageTool --candidate $stagingDirectory --config $configSource
    if ($LASTEXITCODE -ne 0) {
        throw "Release publication failed with exit code $LASTEXITCODE; see the error above for rollback status."
    }
    Get-Item -LiteralPath (Join-Path $releaseDirectory 'YanziHelper.exe') | Select-Object FullName, Length, LastWriteTime
} finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:PYINSTALLER_CONFIG_DIR = $previousBuildCache
    Pop-Location
}
