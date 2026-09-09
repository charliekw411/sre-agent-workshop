$ErrorActionPreference = 'Stop'

if (-not (& azd env get-value SQL_ADMIN_PASSWORD 2>$null)) {
    $passwordBytes = [byte[]]::new(16)
    [Security.Cryptography.RandomNumberGenerator]::Fill($passwordBytes)
    $sqlAdminPassword = "$([Convert]::ToHexString($passwordBytes).ToLowerInvariant())Aa1!"
    & azd env set SQL_ADMIN_PASSWORD $sqlAdminPassword
}

if (-not (& azd env get-value FAULT_TOKEN 2>$null)) {
    $tokenBytes = [byte[]]::new(24)
    [Security.Cryptography.RandomNumberGenerator]::Fill($tokenBytes)
    $faultToken = [Convert]::ToHexString($tokenBytes).ToLowerInvariant()
    & azd env set FAULT_TOKEN $faultToken
}

if (-not (& azd env get-value ALERT_EMAIL 2>$null)) {
    $alertEmail = & az ad signed-in-user show --query mail --output tsv
    if ([string]::IsNullOrWhiteSpace($alertEmail) -or $alertEmail -eq 'null') {
        $alertEmail = & az ad signed-in-user show --query userPrincipalName --output tsv
    }
    & azd env set ALERT_EMAIL $alertEmail
}