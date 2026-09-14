$ErrorActionPreference = 'Stop'
$python = $null
$candidates = if ($IsWindows) { @('python', 'python3') } else { @('python3', 'python') }
foreach ($candidate in $candidates) {
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command) {
        & $command.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $python = $command
            break
        }
    }
}
if (-not $python) {
    throw 'Install Python 3.10 or later before running azd up.'
}
& $python.Source "$PSScriptRoot/workshop.py" @args
exit $LASTEXITCODE
