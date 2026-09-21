$ErrorActionPreference = 'Stop'
& "$PSScriptRoot/invoke-workshop.ps1" postprovision
exit $LASTEXITCODE
