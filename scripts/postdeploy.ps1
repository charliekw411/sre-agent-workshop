$ErrorActionPreference = 'Stop'
& "$PSScriptRoot/invoke-workshop.ps1" postdeploy
exit $LASTEXITCODE
