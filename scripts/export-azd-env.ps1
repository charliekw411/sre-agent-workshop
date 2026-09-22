$ErrorActionPreference = 'Stop'

& "$PSScriptRoot/invoke-workshop.ps1" export
exit $LASTEXITCODE