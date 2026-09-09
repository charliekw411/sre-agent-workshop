$ErrorActionPreference = 'Stop'

New-Item -ItemType Directory -Path .workshop -Force | Out-Null
$values = & azd env get-values | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
$values | ForEach-Object { "export $_" } | Set-Content .workshop/workshop.env