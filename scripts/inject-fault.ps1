$ErrorActionPreference = 'Stop'
& "$PSScriptRoot/invoke-workshop.ps1" fault @args
exit $LASTEXITCODE
