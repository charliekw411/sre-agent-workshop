#requires -Version 7.4

<#
.SYNOPSIS
Prepare workshop users, bulk-issue TAPs, assign scoped access, or clean up a cohort.
.EXAMPLE
.\scripts\manage-participants.ps1 prepare -TenantId $tenantId -Cohort workshop -Domain $domain -Count 1
.EXAMPLE
.\scripts\manage-participants.ps1 issue-taps -TenantId $tenantId -Cohort workshop
.EXAMPLE
.\scripts\manage-participants.ps1 assign-access -TenantId $tenantId -Cohort workshop -SubscriptionId $subscriptionId -EnvironmentMap .\environments.csv -NoAccessValidated
.EXAMPLE
.\scripts\manage-participants.ps1 cleanup -TenantId $tenantId -Cohort workshop -Alias participant-01 -ConfirmCleanup
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('prepare', 'issue-taps', 'assign-access', 'cleanup')]
    [string]$Action = 'prepare',
    [guid]$TenantId = [guid]::Empty,
    [ValidatePattern('^[a-z0-9][a-z0-9-]{0,23}$')]
    [string]$Cohort = 'workshop',
    [string]$Domain,
    [ValidateRange(1, 1000)]
    [int]$Count = 1,
    [string]$RosterPath,
    [string]$EnvironmentMap,
    [guid]$SubscriptionId = [guid]::Empty,
    [ValidateRange(10, 60)]
    [int]$LifetimeMinutes = 30,
    [string[]]$Alias,
    [string]$StateDirectory,
    [string]$RosterOutput,
    [switch]$ReplaceTaps,
    [switch]$RetryUnconfirmedGroupCreation,
    [switch]$NoAccessValidated,
    [switch]$ConfirmCleanup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-WorkshopError {
    param([string]$Message, [int]$Status = 0, [string]$Code = '')
    $error = [InvalidOperationException]::new($Message)
    $error.Data['WorkshopSafe'] = $true
    $error.Data['Status'] = $Status
    $error.Data['Code'] = $Code
    return $error
}

function Assert-Workshop {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw (New-WorkshopError $Message) }
}

function Get-WorkshopErrorMessage {
    param([System.Management.Automation.ErrorRecord]$Record)
    if ($Record.Exception.Data['WorkshopSafe']) { return $Record.Exception.Message }
    return "Unexpected $($Record.Exception.GetType().Name); potentially sensitive error details withheld."
}

function Test-WorkshopGuid {
    param($Value)
    $parsed = [guid]::Empty
    return [guid]::TryParse([string]$Value, [ref]$parsed) -and $parsed -ne [guid]::Empty
}

function Test-WorkshopEmptyList {
    param($Value)
    return $null -eq $Value -or ($Value -is [Collections.IEnumerable] -and
        $Value -isnot [string] -and $Value -isnot [System.Collections.IDictionary] -and @($Value).Count -eq 0)
}

function Get-WorkshopStableId {
    param([string]$Seed)
    $bytes = [System.Security.Cryptography.SHA256]::HashData(
        [Text.Encoding]::UTF8.GetBytes($Seed.ToLowerInvariant()))
    return [guid]::new([byte[]]$bytes[0..15]).ToString()
}

function Set-WorkshopPrivatePermissions {
    param([string]$Path, [switch]$Directory)
    if ($IsWindows) {
        $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $acl = Get-Acl -LiteralPath $Path
        $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
        if ($acl.AreAccessRulesProtected -and $acl.GetOwner([Security.Principal.SecurityIdentifier]) -eq $owner -and
            $rules.Count -eq 1 -and $rules[0].IdentityReference -eq $owner -and
            $rules[0].AccessControlType -eq 'Allow' -and $rules[0].FileSystemRights -eq 'FullControl') {
            return
        }
        if ($acl.GetOwner([Security.Principal.SecurityIdentifier]) -ne $owner) { $acl.SetOwner($owner) }
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($rule in @($acl.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier]))) {
            [void]$acl.RemoveAccessRuleSpecific($rule)
        }
        $inheritance = if ($Directory) {
            [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
        } else {
            [Security.AccessControl.InheritanceFlags]::None
        }
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $owner, 'FullControl', $inheritance, 'None', 'Allow'))
        Set-Acl -LiteralPath $Path -AclObject $acl
    } else {
        $mode = [IO.UnixFileMode]'UserRead, UserWrite'
        if ($Directory) { $mode = $mode -bor [IO.UnixFileMode]::UserExecute }
        [IO.File]::SetUnixFileMode($Path, $mode)
    }
}

function Initialize-WorkshopDirectory {
    param([string]$Path)
    $fullPath = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $relative = [IO.Path]::GetRelativePath($root, $fullPath)
    Assert-Workshop ($relative -eq '..' -or $relative.StartsWith("..$([IO.Path]::DirectorySeparatorChar)")) `
        'StateDirectory must be outside the repository; it contains private operational state and encrypted credentials.'
    Assert-Workshop ($fullPath -ne [IO.Path]::GetPathRoot($fullPath)) 'StateDirectory cannot be a filesystem root.'
    $ancestor = $fullPath
    while ($ancestor) {
        if (Test-Path -LiteralPath $ancestor) {
            $item = Get-Item -LiteralPath $ancestor -Force
            Assert-Workshop (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) `
                'StateDirectory cannot traverse a symbolic link or junction.'
        }
        $ancestor = [IO.Path]::GetDirectoryName($ancestor)
    }
    if (Test-Path -LiteralPath $fullPath) {
        Assert-Workshop (Test-Path -LiteralPath $fullPath -PathType Container) 'StateDirectory must be a directory.'
        if (-not (Test-Path -LiteralPath (Join-Path $fullPath 'state.json'))) {
            $otherFiles = @(Get-ChildItem -LiteralPath $fullPath -Force | Where-Object Name -ne '.lock')
            Assert-Workshop ($otherFiles.Count -eq 0) `
                'StateDirectory is not empty and has no ownership state. Recover the original state or choose an empty directory.'
        }
    }
    [void][IO.Directory]::CreateDirectory($fullPath)
    Set-WorkshopPrivatePermissions $fullPath -Directory
    foreach ($item in Get-ChildItem -LiteralPath $fullPath -Force) {
        Assert-Workshop (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) `
            'Private state cannot contain symbolic links or junctions.'
    }
    return $fullPath
}

function Write-WorkshopJson {
    param([string]$Path, $Value)
    $temporary = "$Path.new"
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 30))
    Set-WorkshopPrivatePermissions $temporary
    [IO.File]::Move($temporary, $Path, $true)
}

function Get-WorkshopState {
    param([string]$Directory, [string]$Tenant, [string]$Name, [switch]$Create)
    $path = Join-Path $Directory 'state.json'
    if (Test-Path -LiteralPath $path) {
        $state = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json -AsHashtable
        Assert-Workshop ($state.version -eq 1 -and $state.tenantId -eq $Tenant -and $state.cohort -eq $Name) `
            'Private state belongs to a different tenant/cohort or unsupported version.'
        Assert-Workshop ($state.ownerTag -match "^sre-agent-workshop:$Name`:[0-9a-f-]{36}$") `
            'Private state has an invalid ownership marker.'
        Assert-Workshop ($state.participants -is [System.Collections.IDictionary]) 'Private participant state is invalid.'
        return $state
    }
    Assert-Workshop $Create 'Run prepare first, using the same tenant, cohort, and StateDirectory.'
    return @{
        version = 1; tenantId = $Tenant; cohort = $Name
        ownerTag = "sre-agent-workshop:$Name`:$([guid]::NewGuid())"
        domain = ''; groupId = ''; groupCreationUnconfirmed = $false; participants = @{}
    }
}

function Save-WorkshopState {
    param($State, [string]$Directory)
    Write-WorkshopJson (Join-Path $Directory 'state.json') $State
}

function Save-WorkshopRoster {
    param($State, [string]$Directory, [string]$Output)
    $rows = @(foreach ($key in @($State.participants.Keys | Sort-Object)) {
        $participant = $State.participants[$key]
        [pscustomobject]@{
            Alias = $key; Username = $participant.username
            Environment = $participant.environment; ResourceGroup = $participant.resourceGroup
            Status = $participant.status
        }
    })
    if (-not $rows.Count) { return }
    $path = Join-Path $Directory 'roster.csv'
    $rows | Export-Csv -LiteralPath "$path.new" -NoTypeInformation -Encoding utf8
    Set-WorkshopPrivatePermissions "$path.new"
    [IO.File]::Move("$path.new", $path, $true)
    if ($Output) {
        Assert-Workshop ([IO.Path]::GetExtension($Output) -eq '.csv') 'RosterOutput must be a CSV file, not a private state or credential file.'
        $rows | Export-Csv -LiteralPath $Output -NoTypeInformation -Encoding utf8
    }
}

function Connect-WorkshopGraph {
    param([string]$Tenant, [string]$Operation)
    Assert-Workshop ([bool](Get-Module -ListAvailable Microsoft.Graph.Authentication)) `
        'Install Microsoft.Graph.Authentication first: Install-Module Microsoft.Graph.Authentication -Scope CurrentUser'
    Import-Module Microsoft.Graph.Authentication -ErrorAction Stop
    $scopes = @('User.Read.All', 'GroupMember.Read.All', 'RoleManagement.Read.Directory')
    switch ($Operation) {
        'prepare' { $scopes += 'User.ReadWrite.All', 'Group.ReadWrite.All', 'Domain.Read.All' }
        'issue-taps' {
            $scopes += 'Policy.Read.AuthenticationMethod', 'UserAuthenticationMethod.ReadWrite.All'
        }
        'cleanup' { $scopes += 'User.ReadWrite.All', 'Group.ReadWrite.All', 'UserAuthenticationMethod.ReadWrite.All' }
    }
    Write-Host '[authentication] Requesting device-code sign-in. Complete the code prompt below with your organizer account.'
    try {
        Connect-MgGraph -TenantId $Tenant -Scopes $scopes -UseDeviceCode -ContextScope Process -NoWelcome -Verbose:$false -Debug:$false `
            -ErrorAction Stop | ForEach-Object { Write-Host $_ }
    } catch {
        $details = $_.Exception.ToString()
        $reason = [regex]::Match($details, '\bAADSTS\d+\b').Value
        if (-not $reason) {
            $reason = if ($details -match '(?i)(timed out|timeout|cancell?ed)') { 'timed out or cancelled' } else { 'device-code sign-in failed' }
        }
        throw (New-WorkshopError "Microsoft Graph sign-in failed ($reason). Rerun the command and complete the code prompt. If it fails again, check tenant sign-in policy and admin consent. Raw authentication details withheld.")
    }
    $context = Get-MgContext
    Assert-Workshop ($context.TenantId -eq $Tenant -and $context.AuthType -eq 'Delegated') `
        'Microsoft Graph must be connected to the requested tenant using delegated organizer authentication.'
    # A lost TAP POST response must not be retried by the SDK.
    [void](Set-MgRequestContext -MaxRetry 0 -ClientTimeout 60)
}

function Invoke-WorkshopGraph {
    param([string]$Method = 'GET', [string]$Path, $Body, [switch]$AllowNotFound)
    $url = if ($Path.StartsWith('/')) { "https://graph.microsoft.com/v1.0$Path" } else { $Path }
    $uri = [uri]$url
    Assert-Workshop ($uri.Scheme -eq 'https' -and $uri.Host -eq 'graph.microsoft.com' -and $uri.Port -eq 443 -and
        -not $uri.UserInfo -and -not $uri.Fragment -and $uri.AbsolutePath.StartsWith('/v1.0/')) 'Refusing an unexpected Graph endpoint.'
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        try {
            $parameters = @{
                Method = $Method; Uri = $url; OutputType = 'Hashtable'
                ErrorAction = 'Stop'; Verbose = $false; Debug = $false
            }
            if ($null -ne $Body) {
                $parameters.Body = $Body | ConvertTo-Json -Depth 15 -Compress
                $parameters.ContentType = 'application/json'
            }
            return Invoke-MgGraphRequest @parameters
        } catch {
            $status = 0
            $retryAfter = 0
            $responseProperty = $_.Exception.PSObject.Properties['Response']
            if ($responseProperty -and $responseProperty.Value) {
                $response = $responseProperty.Value
                if ($response.PSObject.Properties['StatusCode']) { $status = [int]$response.StatusCode }
                if ($response.PSObject.Properties['Headers'] -and $response.Headers.RetryAfter) {
                    if ($response.Headers.RetryAfter.Delta) {
                        $retryAfter = [int][Math]::Ceiling($response.Headers.RetryAfter.Delta.TotalSeconds)
                    }
                }
            } elseif ($_.Exception.PSObject.Properties['ResponseStatusCode']) {
                $status = [int]$_.Exception.ResponseStatusCode
            }
            if ($AllowNotFound -and $status -eq 404) { return $null }
            if ($Method -eq 'GET' -and $status -in @(408, 429, 500, 502, 503, 504) -and $attempt -lt 4) {
                Start-Sleep -Seconds ([Math]::Min(60, [Math]::Max($retryAfter, [Math]::Pow(2, $attempt + 1))))
                continue
            }
            throw (New-WorkshopError "Graph $Method failed (HTTP $status). Credential-related error details withheld." $status)
        }
    }
}

function Get-WorkshopGraphCollection {
    param([string]$Path)
    $items = [Collections.Generic.List[object]]::new()
    $seen = [Collections.Generic.HashSet[string]]::new()
    while ($Path) {
        Assert-Workshop ($seen.Add($Path)) 'Graph returned a pagination cycle.'
        $response = Invoke-WorkshopGraph -Path $Path
        Assert-Workshop ($response -is [System.Collections.IDictionary] -and
            $response['value'] -is [Collections.IEnumerable] -and $response['value'] -isnot [string] -and
            $response['value'] -isnot [System.Collections.IDictionary]) `
            'Graph returned an invalid collection.'
        foreach ($item in $response.value) { $items.Add($item) }
        $Path = [string]$response['@odata.nextLink']
    }
    return $items.ToArray()
}

function Get-WorkshopUser {
    param([string]$Identity)
    $key = [uri]::EscapeDataString($Identity)
    return Invoke-WorkshopGraph -Path "/users/$key`?`$select=id,displayName,userPrincipalName,userType,accountEnabled,onPremisesSyncEnabled,creationType,employeeType" -AllowNotFound
}

function Assert-WorkshopUser {
    param($User, $State, [string]$Username, [switch]$AllowDisabled)
    Assert-Workshop ($null -ne $User -and (Test-WorkshopGuid $User.id)) 'The participant account is missing or invalid.'
    Assert-Workshop ($User.userPrincipalName -eq $Username -and $User.employeeType -eq $State.ownerTag) `
        'An existing account does not match this cohort ownership state; it will not be adopted or changed.'
    Assert-Workshop ($User.userType -eq 'Member' -and $User['onPremisesSyncEnabled'] -ne $true -and
        $User['creationType'] -ne 'Invitation') 'Participant accounts must be local internal Members, not guests or synchronized identities.'
    Assert-Workshop ($AllowDisabled -or $User.accountEnabled -eq $true) 'The participant account is disabled; resolve its status before preparing access.'
    $roles = @(Get-WorkshopGraphCollection "/users/$($User.id)/transitiveMemberOf" |
        Where-Object { $_['@odata.type'] -eq '#microsoft.graph.directoryRole' })
    $eligible = @(Get-WorkshopGraphCollection "/roleManagement/directory/roleEligibilityScheduleInstances?`$filter=principalId eq '$($User.id)'&`$select=id")
    Assert-Workshop ($roles.Count -eq 0 -and $eligible.Count -eq 0) `
        'The participant has an active or eligible Entra directory role; no changes will be made.'
}

function Get-WorkshopInputRoster {
    param([string]$Path, [int]$Size, [string]$Name)
    $rows = @(if ($Path) {
        Import-Csv -LiteralPath $Path
    } else {
        for ($index = 1; $index -le $Size; $index++) {
            [pscustomobject]@{ Alias = 'participant-{0:d2}' -f $index; DisplayName = "SRE Workshop $Name Participant $index" }
        }
    })
    Assert-Workshop ($rows.Count -gt 0 -and $rows.Count -le 1000) 'A participant roster must contain 1 to 1000 rows.'
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($row in $rows) {
        $columns = @($row.PSObject.Properties.Name)
        Assert-Workshop ('Alias' -in $columns -and 'DisplayName' -in $columns -and
            @($columns | Where-Object { $_ -notin @('Alias', 'DisplayName', 'Environment', 'ResourceGroup') }).Count -eq 0) `
            'Participant CSV columns must be Alias,DisplayName, with optional Environment,ResourceGroup; do not include credentials or identifiers.'
        Assert-Workshop ($row.Alias -cmatch '^[a-z0-9][a-z0-9-]{0,23}$' -and $seen.Add($row.Alias)) `
            'Participant aliases must be unique lowercase names of up to 24 letters, digits, or hyphens.'
        Assert-Workshop ($row.DisplayName.Trim().Length -in 1..256 -and $row.DisplayName -notmatch '[\x00-\x1f\x7f]') `
            'Participant display names must contain 1 to 256 characters without control characters.'
    }
    return $rows
}

function Ensure-WorkshopGroup {
    param($State, [string]$Directory, [switch]$RetryUnconfirmed)
    $name = "SRE Workshop Participants ($($State.cohort))"
    $select = 'id,displayName,description,securityEnabled,mailEnabled,groupTypes,isAssignableToRole'
    $filter = [uri]::EscapeDataString("displayName eq '$name'")
    $path = "/groups?`$filter=$filter&`$select=$select"
    if ($State.groupId) {
        $group = Invoke-WorkshopGraph -Path "/groups/$($State.groupId)?`$select=$select" -AllowNotFound
        Assert-Workshop ([bool]$group) 'The recorded cohort group is missing. Recover the original group/state; no replacement group was created.'
    } else {
        $groups = @(Get-WorkshopGraphCollection $path)
        Assert-Workshop ($groups.Count -le 1) 'Multiple groups have the reserved cohort name; resolve the collision before proceeding.'
        if (-not $groups.Count) {
            Assert-Workshop (-not $State.groupCreationUnconfirmed -or $RetryUnconfirmed) `
                'An earlier group creation remains unconfirmed. Wait for Entra propagation and rerun; use -RetryUnconfirmedGroupCreation only after confirming no cohort group exists.'
            $State.groupCreationUnconfirmed = $true
            Save-WorkshopState $State $Directory
            try {
                $group = Invoke-WorkshopGraph -Method POST -Path '/groups' -Body @{
                    displayName = $name; description = $State.ownerTag
                    mailNickname = "sre-$($State.cohort)"; mailEnabled = $false; securityEnabled = $true; groupTypes = @()
                }
            } catch {
                if ($_.Exception.Data['Status'] -notin @(0, 408, 429, 500, 502, 503, 504)) {
                    $State.groupCreationUnconfirmed = $false
                    Save-WorkshopState $State $Directory
                    throw
                }
                for ($attempt = 0; $attempt -lt 6; $attempt++) {
                    $groups = @(Get-WorkshopGraphCollection $path)
                    if ($groups.Count) { break }
                    Start-Sleep -Seconds ([Math]::Min(16, [Math]::Pow(2, $attempt + 1)))
                }
                Assert-Workshop ($groups.Count -eq 1) `
                    'Group creation outcome is uncertain. Rerun prepare to reconcile; no duplicate create was attempted.'
                $group = $groups[0]
            }
        } else { $group = $groups[0] }
        if ((Test-WorkshopGuid $group.id) -and $group.description -eq $State.ownerTag) {
            $State.groupId = $group.id
            $State.groupCreationUnconfirmed = $false
            Save-WorkshopState $State $Directory
        }
    }
    Assert-Workshop ((Test-WorkshopGuid $group.id) -and $group.displayName -eq $name -and $group.description -eq $State.ownerTag -and
        $group.securityEnabled -eq $true -and $group.mailEnabled -eq $false -and
        -not $group['isAssignableToRole'] -and (Test-WorkshopEmptyList $group['groupTypes'])) `
        'The reserved group exists but is not this cohort-owned, static, non-role-assignable security group.'
    Assert-Workshop (-not $State.groupId -or $State.groupId -eq $group.id) 'The cohort group was replaced; restore the original ownership state.'
    $State.groupId = $group.id
    Save-WorkshopState $State $Directory
}

function Get-WorkshopMembers {
    param($State)
    Assert-Workshop (Test-WorkshopGuid $State.groupId) 'The cohort group has not been prepared.'
    $group = Invoke-WorkshopGraph -Path "/groups/$($State.groupId)?`$select=id,description,isAssignableToRole,groupTypes"
    Assert-Workshop ($group.description -eq $State.ownerTag -and -not $group['isAssignableToRole'] -and
        (Test-WorkshopEmptyList $group['groupTypes'])) 'The group ownership or role-assignment settings changed.'
    return Get-WorkshopGraphCollection "/groups/$($State.groupId)/members?`$select=id"
}

function Ensure-WorkshopMembership {
    param($State, [string]$UserId)
    $members = @(Get-WorkshopMembers $State)
    if ($UserId -notin @($members | ForEach-Object { $_.id })) {
        try {
            [void](Invoke-WorkshopGraph -Method POST -Path "/groups/$($State.groupId)/members/`$ref" `
                -Body @{ '@odata.id' = "https://graph.microsoft.com/v1.0/directoryObjects/$UserId" })
        } catch {
            $failure = $_
            Start-Sleep -Seconds 2
            $members = @(Get-WorkshopMembers $State)
            if ($UserId -notin @($members | ForEach-Object { $_.id })) { throw $failure }
        }
    }
    for ($attempt = 0; $attempt -lt 6; $attempt++) {
        $members = @(Get-WorkshopMembers $State)
        if ($UserId -in @($members | ForEach-Object { $_.id })) { return }
        Start-Sleep -Seconds ([Math]::Min(16, [Math]::Pow(2, $attempt + 1)))
    }
    throw (New-WorkshopError 'Group membership did not propagate. Rerun prepare before issuing TAPs.')
}

function Prepare-WorkshopParticipant {
    param($State, [string]$Directory, $Row)
    $key = $Row.Alias
    $username = "sre-$($State.cohort)-$key@$($State.domain)"
    if (-not $State.participants.Contains($key)) {
        $State.participants[$key] = @{
            username = $username; userId = ''; environment = ''; resourceGroup = ''
            subscription = ''; scopes = @{}; assignments = @(); status = 'pending'; cleanupStarted = $false
        }
        Save-WorkshopState $State $Directory
    }
    $participant = $State.participants[$key]
    Assert-Workshop (-not $participant.cleanupStarted) 'Cleanup is incomplete for this participant; finish cleanup before preparing it again.'
    $user = Get-WorkshopUser $username
    if (-not $user) {
        $password = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)) + 'aA1!'
        try {
            $user = Invoke-WorkshopGraph -Method POST -Path '/users' -Body @{
                displayName = $Row.DisplayName.Trim(); userPrincipalName = $username
                mailNickname = "sre-$($State.cohort)-$key"; userType = 'Member'; accountEnabled = $true
                employeeType = $State.ownerTag
                passwordProfile = @{ password = $password; forceChangePasswordNextSignIn = $false }
            }
        } catch {
            if ($_.Exception.Data['Status'] -notin @(0, 408, 429, 500, 502, 503, 504)) { throw }
            Start-Sleep -Seconds 2
            $user = Get-WorkshopUser $username
            Assert-Workshop ([bool]$user) 'User creation outcome is uncertain. Rerun prepare to reconcile; no duplicate create was attempted.'
        } finally { $password = $null }
    }
    for ($attempt = 0; $attempt -lt 6; $attempt++) {
        $current = Get-WorkshopUser $username
        if ($current) { $user = $current; break }
        Start-Sleep -Seconds ([Math]::Min(16, [Math]::Pow(2, $attempt + 1)))
    }
    Assert-WorkshopUser $user $State $username
    Assert-Workshop (-not $participant.userId -or $participant.userId -eq $user.id) `
        'A different identity now uses this username; it will not replace the recorded participant.'
    $participant.userId = $user.id
    Save-WorkshopState $State $Directory
    Ensure-WorkshopMembership $State $user.id
    $participant.status = 'prepared'
    Save-WorkshopState $State $Directory
}

function Assert-WorkshopTapPolicy {
    param($Policy, [string]$GroupId, [int]$Minutes)
    Assert-Workshop ($Policy.state -eq 'enabled' -and $Policy.isUsableOnce -eq $true) `
        'Enable TAP and require one-time use in Entra Authentication methods > Policies > Temporary Access Pass.'
    $targets = @($Policy.includeTargets)
    Assert-Workshop ($targets.Count -eq 1 -and $targets[0].id -eq $GroupId -and
        $targets[0].targetType -eq 'group' -and (Test-WorkshopEmptyList $Policy['excludeTargets'])) `
        'The TAP policy must target only this workshop group, with no exclusions; the script does not change tenant policy.'
    Assert-Workshop ($Policy.minimumLifetimeInMinutes -is [long] -or $Policy.minimumLifetimeInMinutes -is [int]) 'TAP minimum lifetime is invalid.'
    Assert-Workshop ($Policy.maximumLifetimeInMinutes -is [long] -or $Policy.maximumLifetimeInMinutes -is [int]) 'TAP maximum lifetime is invalid.'
    Assert-Workshop ($Minutes -ge $Policy.minimumLifetimeInMinutes -and $Minutes -le $Policy.maximumLifetimeInMinutes) `
        'LifetimeMinutes is outside the tenant TAP policy bounds. Configure a short onboarding window in Entra first.'
}

function Get-WorkshopTaps {
    param([string]$UserId)
    return Get-WorkshopGraphCollection "/users/$UserId/authentication/temporaryAccessPassMethods?`$select=id,startDateTime,lifetimeInMinutes,isUsableOnce,isUsable,methodUsabilityReason"
}

function Write-WorkshopTapHandoff {
    param([string]$Directory, [string]$Key, [string]$Username, $Tap)
    Assert-Workshop $IsWindows 'Bulk TAP handoff requires Windows DPAPI; no plaintext fallback is supported.'
    Assert-Workshop ($Key -cmatch '^[a-z0-9][a-z0-9-]{0,23}$') 'The encrypted handoff alias is invalid.'
    $folder = Join-Path $Directory 'taps'
    [void][IO.Directory]::CreateDirectory($folder)
    Set-WorkshopPrivatePermissions $folder -Directory
    $path = Join-Path $folder "$Key.clixml"
    $credential = [pscredential]::new($Username, (ConvertTo-SecureString $Tap.temporaryAccessPass -AsPlainText -Force))
    @{
        methodId = $Tap.id; expiresUtc = ([datetimeoffset]$Tap.startDateTime).AddMinutes($Tap.lifetimeInMinutes).ToString('o')
        credential = $credential
    } | Export-Clixml -LiteralPath "$path.new" -Depth 5
    Set-WorkshopPrivatePermissions "$path.new"
    [IO.File]::Move("$path.new", $path, $true)
}

function Remove-WorkshopHandoff {
    param([string]$Directory, [string]$Key)
    Assert-Workshop ($Key -cmatch '^[a-z0-9][a-z0-9-]{0,23}$') 'The encrypted handoff alias is invalid.'
    foreach ($name in @("$Key.clixml", "$Key.clixml.new")) {
        $path = Join-Path (Join-Path $Directory 'taps') $name
        if (Test-Path -LiteralPath $path) {
            Assert-Workshop (((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) `
                'An encrypted handoff path was replaced by a symbolic link.'
            [IO.File]::Delete($path)
        }
    }
}

function Issue-WorkshopTap {
    param($State, [string]$Directory, [string]$Key, [int]$Minutes, [switch]$Replace)
    $participant = $State.participants[$Key]
    Assert-Workshop (-not $participant.cleanupStarted) 'This participant is being cleaned up; TAP issuance is blocked.'
    Assert-WorkshopUser (Get-WorkshopUser $participant.userId) $State $participant.username
    $existing = @(Get-WorkshopTaps $participant.userId)
    Assert-Workshop ($existing.Count -le 1) 'The participant has multiple TAP methods; resolve the unexpected state in Entra.'
    if ($existing.Count) {
        $old = $existing[0]
        $expires = ([datetimeoffset]$old.startDateTime).AddMinutes($old.lifetimeInMinutes)
        if (-not $Replace -and $expires -gt [datetimeoffset]::UtcNow -and
            $old.methodUsabilityReason -notin @('OneTimeUsed', 'Expired')) {
            $path = Join-Path (Join-Path $Directory 'taps') "$Key.clixml"
            Assert-Workshop (Test-Path -LiteralPath $path) `
                'A TAP already exists but its secure handoff is missing. Use -ReplaceTaps explicitly to revoke it and issue a new one.'
            $handoff = Import-Clixml -LiteralPath $path
            Assert-Workshop ($old.isUsableOnce -eq $true -and $old.methodUsabilityReason -in @('EnabledByPolicy', 'NotYetValid') -and
                ($old.isUsable -eq $true -or $old.methodUsabilityReason -eq 'NotYetValid') -and
                $handoff.methodId -eq $old.id -and $handoff.credential -is [pscredential] -and
                $handoff.credential.UserName -eq $participant.username) `
                'The existing TAP or encrypted handoff is not usable for this cohort. Use -ReplaceTaps explicitly after reviewing it.'
            return 'tap-retained'
        }
        Assert-Workshop (Test-WorkshopGuid $old.id) 'Graph returned an invalid TAP method ID.'
        [void](Invoke-WorkshopGraph -Method DELETE -Path "/users/$($participant.userId)/authentication/temporaryAccessPassMethods/$($old.id)" -AllowNotFound)
        Remove-WorkshopHandoff $Directory $Key
    }
    $participant.status = 'tap-pending'
    Save-WorkshopState $State $Directory
    $tap = Invoke-WorkshopGraph -Method POST -Path "/users/$($participant.userId)/authentication/temporaryAccessPassMethods" `
        -Body @{ lifetimeInMinutes = $Minutes; isUsableOnce = $true }
    try {
        Assert-Workshop ((Test-WorkshopGuid $tap.id) -and $tap.isUsableOnce -eq $true -and
            $tap.lifetimeInMinutes -eq $Minutes -and $tap.temporaryAccessPass -is [string] -and
            $tap.temporaryAccessPass.Length -in 8..48 -and $tap.temporaryAccessPass -notmatch '[\x00-\x1f\x7f]') `
            'Graph returned an unexpected TAP response; secure handoff was not accepted.'
        Write-WorkshopTapHandoff $Directory $Key $participant.username $tap
        $participant.status = 'tap-issued'
        Save-WorkshopState $State $Directory
    } catch {
        $failure = $_
        if (Test-WorkshopGuid $tap.id) {
            try {
                [void](Invoke-WorkshopGraph -Method DELETE -Path "/users/$($participant.userId)/authentication/temporaryAccessPassMethods/$($tap.id)" -AllowNotFound)
                Remove-WorkshopHandoff $Directory $Key
            } catch {
                throw (New-WorkshopError 'Secure TAP handoff failed and revocation could not be confirmed. Revoke the pass in Entra before retrying.')
            }
        }
        throw (New-WorkshopError "Secure TAP handoff failed; the new pass was revoked. $(Get-WorkshopErrorMessage $failure)")
    } finally {
        if ($tap -is [System.Collections.IDictionary]) { $tap['temporaryAccessPass'] = $null }
    }
    return 'tap-issued'
}

function Invoke-WorkshopAzure {
    param([string[]]$Arguments, [switch]$Read, [switch]$AllowNotFound)
    $command = Get-Command az -ErrorAction SilentlyContinue
    Assert-Workshop ([bool]$command) 'Install Azure CLI and run az login before assigning or removing Azure access.'
    $saved = @{}
    foreach ($name in @('AZURE_CORE_ENABLE_LOG_FILE', 'AZURE_CORE_COLLECT_TELEMETRY', 'AZURE_CORE_ONLY_SHOW_ERRORS')) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name)
    }
    $env:AZURE_CORE_ENABLE_LOG_FILE = 'false'
    $env:AZURE_CORE_COLLECT_TELEMETRY = 'false'
    $env:AZURE_CORE_ONLY_SHOW_ERRORS = 'true'
    $PSNativeCommandUseErrorActionPreference = $false
    try {
        for ($attempt = 0; $attempt -lt 5; $attempt++) {
            $text = (& $command.Source @Arguments --only-show-errors --output json 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -eq 0) {
                if (-not $text) { return $null }
                try { return ConvertFrom-Json -InputObject $text -AsHashtable } catch {
                    throw (New-WorkshopError 'Azure CLI returned invalid JSON; no successful result was accepted.')
                }
            }
            $code = 'Unknown'
            foreach ($candidate in @('PrincipalNotFound', 'RoleDefinitionDoesNotExist', 'RoleAssignmentExists',
                'TooManyRequests', 'ResourceNotFound', 'RoleAssignmentNotFound', 'AuthorizationFailed', 'Forbidden',
                'ServiceUnavailable', 'InvalidScope', 'BadRequest', 'RoleAssignmentUpdateNotPermitted')) {
                if ($text.Contains($candidate)) { $code = $candidate; break }
            }
            if ($code -eq 'Unknown' -and $text -match 'ConnectionError|connection reset|timed? out|timeout') { $code = 'ConnectionFailure' }
            if ($AllowNotFound -and $code -in @('ResourceNotFound', 'RoleAssignmentNotFound')) { return $null }
            if ($Read -and $code -in @('TooManyRequests', 'ServiceUnavailable') -and $attempt -lt 4) {
                Start-Sleep -Seconds ([Math]::Pow(2, $attempt + 1))
                continue
            }
            throw (New-WorkshopError "Azure CLI failed ($code). Raw service output withheld." 0 $code)
        }
    } finally {
        foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name]) }
    }
}

function Get-WorkshopEnvironmentMap {
    param([string]$Path, [string[]]$Keys)
    Assert-Workshop ([bool]$Path) 'assign-access requires -EnvironmentMap with Alias,Environment,ResourceGroup columns.'
    $rows = @(Import-Csv -LiteralPath $Path)
    Assert-Workshop ($rows.Count -gt 0) 'The environment mapping is empty.'
    $aliases = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $environments = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $groups = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($row in $rows) {
        $columns = @($row.PSObject.Properties.Name)
        Assert-Workshop ('Alias' -in $columns -and 'Environment' -in $columns -and 'ResourceGroup' -in $columns -and
            @($columns | Where-Object { $_ -notin @('Alias', 'DisplayName', 'Environment', 'ResourceGroup') }).Count -eq 0) `
            'Environment CSV must contain only Alias,Environment,ResourceGroup and optional DisplayName; no private identifiers or credentials.'
        Assert-Workshop ($row.Alias -cmatch '^[a-z0-9][a-z0-9-]{0,23}$' -and $aliases.Add($row.Alias) -and
            $row.Environment -match '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$' -and $environments.Add($row.Environment) -and
            $row.ResourceGroup -eq "rg-sre-agent-workshop-$($row.Environment)" -and $groups.Add($row.ResourceGroup)) `
            'Environment mappings must be one-to-one, with unique aliases/environments and matching workshop resource-group names.'
    }
    foreach ($key in $Keys) {
        Assert-Workshop ($key -in $aliases) 'Every selected participant must match exactly one environment; nothing will be assigned.'
    }
    return $rows
}

function Resolve-WorkshopEnvironment {
    param($Row, [string]$Subscription)
    $scope = "/subscriptions/$Subscription/resourceGroups/$($Row.ResourceGroup)"
    $group = Invoke-WorkshopAzure @('group', 'show', '--name', $Row.ResourceGroup, '--subscription', $Subscription) -Read
    Assert-Workshop ($group.id -eq $scope -and $group.tags['workshop-architecture'] -eq 'single-vm-postgresql-v1' -and
        $group.tags['azd-env-name'] -eq $Row.Environment) 'The mapped resource group has the wrong identity, architecture, or environment tag.'
    $inventory = @(Invoke-WorkshopAzure @('resource', 'list', '--resource-group', $Row.ResourceGroup, '--subscription', $Subscription) -Read)
    $types = @{
        vm = 'Microsoft.Compute/virtualMachines'; workspace = 'Microsoft.OperationalInsights/workspaces'
        nsg = 'Microsoft.Network/networkSecurityGroups'; postgresql = 'Microsoft.DBforPostgreSQL/flexibleServers'
        agent = 'Microsoft.App/agents'
    }
    $selected = @{}
    foreach ($key in $types.Keys) {
        $matches = @($inventory | Where-Object { $_.type -eq $types[$key] })
        Assert-Workshop ($matches.Count -eq 1) "The environment must contain exactly one workshop $key resource."
        $resource = $matches[0]
        Assert-Workshop ($resource.id -eq "$scope/providers/$($types[$key])/$($resource.name)") `
            'A resource inventory entry belongs to a different environment.'
        $selected[$key] = $resource
    }
    Assert-Workshop ($selected.vm.name -match '^vm-orders-([a-z0-9]{3,12})$') 'The environment VM name is not a workshop VM.'
    $suffix = $Matches[1]
    Assert-Workshop ($selected.workspace.name -eq "law-$suffix" -and $selected.nsg.name -eq "nsg-orders-$suffix" -and
        $selected.postgresql.name -eq "psql-orders-$suffix" -and $selected.agent.name -eq "sre-$suffix") `
        'Environment resources do not share the expected workshop suffix.'
    $rule = Invoke-WorkshopAzure @('network', 'nsg', 'rule', 'show', '--resource-group', $Row.ResourceGroup,
        '--nsg-name', $selected.nsg.name, '--name', 'PostgreSqlFaultInjection', '--subscription', $Subscription) -Read
    $ruleId = "$($selected.nsg.id)/securityRules/PostgreSqlFaultInjection"
    Assert-Workshop ($rule.id -eq $ruleId -and $rule.priority -eq 100 -and $rule.direction -eq 'Outbound' -and
        $rule.protocol -eq 'Tcp' -and $rule.sourceAddressPrefix -eq '10.240.0.0/27' -and $rule.sourcePortRange -eq '*' -and
        $rule.destinationAddressPrefix -eq '10.240.0.32/27' -and $rule.destinationPortRange -eq '5432' -and
        $rule.access -in @('Allow', 'Deny') -and (Test-WorkshopEmptyList $rule['sourceAddressPrefixes']) -and
        (Test-WorkshopEmptyList $rule['destinationAddressPrefixes']) -and (Test-WorkshopEmptyList $rule['sourcePortRanges']) -and
        (Test-WorkshopEmptyList $rule['destinationPortRanges'])) 'The exact PostgreSQL fault rule does not match the fixed workshop boundary.'
    return @{
        resourceGroup = $scope; vm = $selected.vm.id; workspace = $selected.workspace.id
        faultRule = $ruleId; agent = $selected.agent.id
    }
}

function Ensure-WorkshopRole {
    param([string]$Subscription, [string]$Directory, [string]$Name, [string[]]$Actions)
    $scope = "/subscriptions/$Subscription"
    $roles = @(Invoke-WorkshopAzure @('role', 'definition', 'list', '--name', $Name, '--subscription', $Subscription) -Read)
    Assert-Workshop ($roles.Count -le 1) 'Multiple role definitions have the workshop role name.'
    if (-not $roles.Count) {
        $definition = @{
            Name = $Name; Id = Get-WorkshopStableId "sre-agent-workshop:$Subscription`:$Name"
            IsCustom = $true; Description = 'Managed by sre-agent-workshop; assign only at the documented resource scope.'
            Actions = $Actions; NotActions = @(); DataActions = @(); NotDataActions = @(); AssignableScopes = @($scope)
        }
        $path = Join-Path $Directory 'custom-role.json'
        Write-WorkshopJson $path $definition
        try {
            [void](Invoke-WorkshopAzure @('role', 'definition', 'create', '--role-definition', "@$path", '--subscription', $Subscription))
        } catch {
            if ($_.Exception.Data['Code'] -notin @('TooManyRequests', 'ServiceUnavailable', 'ConnectionFailure')) { throw }
            Write-Host '[roles] Role creation did not return a confirmed result; reconciling by name before any retry.'
        } finally {
            [IO.File]::Delete($path)
        }
        for ($attempt = 0; $attempt -lt 6; $attempt++) {
            $roles = @(Invoke-WorkshopAzure @('role', 'definition', 'list', '--name', $Name, '--subscription', $Subscription) -Read)
            if ($roles.Count) { break }
            Start-Sleep -Seconds ([Math]::Min(16, [Math]::Pow(2, $attempt + 1)))
        }
        Assert-Workshop ($roles.Count -eq 1) 'Custom role creation was not confirmed. Rerun assign-access to reconcile before creating anything else.'
    }
    $role = $roles[0]
    $permissions = @($role.permissions)
    Assert-Workshop ($role.roleType -eq 'CustomRole' -and $permissions.Count -eq 1 -and
        @($role.assignableScopes).Count -eq 1 -and $role.assignableScopes[0] -eq $scope) 'An existing workshop role has unexpected type or assignable scopes.'
    $permission = $permissions[0]
    Assert-Workshop (@(Compare-Object @($permission.actions | Sort-Object) @($Actions | Sort-Object)).Count -eq 0 -and
        (Test-WorkshopEmptyList $permission['notActions']) -and (Test-WorkshopEmptyList $permission['dataActions']) -and
        (Test-WorkshopEmptyList $permission['notDataActions']) -and (Test-WorkshopGuid $role.name)) `
        'An existing workshop role does not have the exact narrow permissions; it will not be widened or replaced.'
    return $role.name
}

function Get-WorkshopAssignment {
    param([string]$Id, [string]$Subscription)
    return Invoke-WorkshopAzure @('rest', '--method', 'get', '--url',
        "https://management.azure.com$Id`?api-version=2022-04-01", '--subscription', $Subscription) -Read -AllowNotFound
}

function Assert-WorkshopAssignment {
    param($Assignment, $Expected, [string]$UserId, [string]$OwnerTag)
    $properties = $Assignment.properties
    Assert-Workshop ($Assignment.id -eq $Expected.id -and $properties.principalId -eq $UserId -and
        $properties.roleDefinitionId -eq "/subscriptions/$($Expected.subscription)/providers/Microsoft.Authorization/roleDefinitions/$($Expected.roleId)" -and
        $properties.scope -eq $Expected.scope -and $properties.description -eq $OwnerTag -and
        -not $properties['condition']) 'An existing assignment does not match cohort ownership, principal, role, or exact scope.'
}

function Ensure-WorkshopAssignment {
    param($State, [string]$Directory, [string]$Key, [string]$Subscription, [string]$RoleId, [string]$Scope)
    $participant = $State.participants[$Key]
    $name = Get-WorkshopStableId "$($State.ownerTag):$($participant.userId):$Scope`:$RoleId"
    $expected = @{
        id = "$Scope/providers/Microsoft.Authorization/roleAssignments/$name"
        scope = $Scope; roleId = $RoleId; subscription = $Subscription
    }
    if ($expected.id -notin @($participant.assignments | ForEach-Object { $_.id })) {
        $participant.assignments += $expected
        Save-WorkshopState $State $Directory
    }
    for ($attempt = 0; $attempt -lt 6; $attempt++) {
        $assignment = Get-WorkshopAssignment $expected.id $Subscription
        if ($assignment) {
            Assert-WorkshopAssignment $assignment $expected $participant.userId $State.ownerTag
            return
        }
        try {
            [void](Invoke-WorkshopAzure @('role', 'assignment', 'create', '--name', $name, '--assignee-object-id', $participant.userId,
                '--assignee-principal-type', 'User', '--role', $RoleId, '--scope', $Scope,
                '--description', $State.ownerTag, '--subscription', $Subscription))
        } catch {
            if ($_.Exception.Data['Code'] -eq 'RoleAssignmentExists') {
                $equivalent = @(Invoke-WorkshopAzure @('role', 'assignment', 'list', '--scope', $Scope, '--all',
                    '--fill-principal-name', 'false', '--fill-role-definition-name', 'false', '--subscription', $Subscription) -Read |
                    Where-Object {
                        $_.principalId -eq $participant.userId -and $_.scope -eq $Scope -and
                        $_.roleDefinitionId -eq "/subscriptions/$Subscription/providers/Microsoft.Authorization/roleDefinitions/$RoleId"
                    })
                Assert-Workshop ($equivalent.Count -eq 1) 'An equivalent role assignment exists but could not be matched uniquely; no duplicate grant was created.'
                $actualId = $equivalent[0].id
                Assert-Workshop ($actualId -match "^$([regex]::Escape($Scope))/providers/Microsoft.Authorization/roleAssignments/[0-9a-f-]{36}$") `
                    'The equivalent role assignment has an unexpected resource ID.'
                $replacement = @{ id = $actualId; scope = $Scope; roleId = $RoleId; subscription = $Subscription }
                Assert-WorkshopAssignment (Get-WorkshopAssignment $actualId $Subscription) $replacement $participant.userId $State.ownerTag
                $participant.assignments = @($participant.assignments | Where-Object { $_.id -ne $expected.id }) + @($replacement)
                Save-WorkshopState $State $Directory
                return
            }
            if ($_.Exception.Data['Code'] -notin @('PrincipalNotFound', 'RoleDefinitionDoesNotExist', 'TooManyRequests', 'ServiceUnavailable', 'ConnectionFailure')) { throw }
        }
        Start-Sleep -Seconds ([Math]::Min(16, [Math]::Pow(2, $attempt + 1)))
    }
    throw (New-WorkshopError 'The exact role assignment was not confirmed after bounded propagation retries. No broader scope or contributor role was used.')
}

function Remove-WorkshopParticipant {
    param($State, [string]$Directory, [string]$Key)
    $participant = $State.participants[$Key]
    $user = Get-WorkshopUser $participant.username
    if ($user) {
        Assert-WorkshopUser $user $State $participant.username -AllowDisabled
        Assert-Workshop ($user.id -eq $participant.userId) 'The username now belongs to a different identity; cleanup is blocked.'
    }
    $participant.cleanupStarted = $true
    Save-WorkshopState $State $Directory
    if ($user) {
        [void](Invoke-WorkshopGraph -Method PATCH -Path "/users/$($user.id)" -Body @{ accountEnabled = $false })
        $revocation = Invoke-WorkshopGraph -Method POST -Path "/users/$($user.id)/revokeSignInSessions"
        Assert-Workshop ($revocation.value -eq $true) 'Sign-in session revocation was not confirmed; cleanup is incomplete.'
        foreach ($tap in @(Get-WorkshopTaps $user.id)) {
            Assert-Workshop (Test-WorkshopGuid $tap.id) 'An unexpected TAP method cannot be removed safely.'
            [void](Invoke-WorkshopGraph -Method DELETE -Path "/users/$($user.id)/authentication/temporaryAccessPassMethods/$($tap.id)" -AllowNotFound)
        }
    }
    foreach ($expected in @($participant.assignments)) {
        Assert-Workshop ((Test-WorkshopGuid $expected.subscription) -and (Test-WorkshopGuid $expected.roleId) -and
            $expected.subscription -eq $participant.subscription -and $expected.scope -in @($participant.scopes.Values) -and
            $expected.id -match "^$([regex]::Escape($expected.scope))/providers/Microsoft.Authorization/roleAssignments/[0-9a-f-]{36}$") `
            'Recorded cleanup scope is invalid; no assignment will be deleted.'
        $assignment = Get-WorkshopAssignment $expected.id $expected.subscription
        if ($assignment) {
            Assert-WorkshopAssignment $assignment $expected $participant.userId $State.ownerTag
            [void](Invoke-WorkshopAzure @('role', 'assignment', 'delete', '--ids', $expected.id, '--subscription', $expected.subscription))
            Assert-Workshop (-not (Get-WorkshopAssignment $expected.id $expected.subscription)) 'Role assignment deletion has not propagated; rerun cleanup.'
        }
    }
    if ($user) {
        [void](Invoke-WorkshopGraph -Method DELETE -Path "/groups/$($State.groupId)/members/$($user.id)/`$ref" -AllowNotFound)
        [void](Invoke-WorkshopGraph -Method DELETE -Path "/users/$($user.id)" -AllowNotFound)
        Assert-Workshop (-not (Get-WorkshopUser $participant.username)) 'User deletion has not propagated; rerun cleanup.'
    }
    Remove-WorkshopHandoff $Directory $Key
    $participant.assignments = @()
    $participant.userId = ''
    $participant.cleanupStarted = $false
    $participant.status = 'cleaned'
    Save-WorkshopState $State $Directory
}

function Invoke-WorkshopParticipants {
    [CmdletBinding()]
    param(
        [string]$Action = 'prepare', [guid]$TenantId = [guid]::Empty, [string]$Cohort = 'workshop', [string]$Domain,
        [int]$Count = 1, [string]$RosterPath, [string]$EnvironmentMap, [guid]$SubscriptionId = [guid]::Empty,
        [int]$LifetimeMinutes = 30, [string[]]$Alias, [string]$StateDirectory, [string]$RosterOutput,
        [switch]$ReplaceTaps, [switch]$RetryUnconfirmedGroupCreation, [switch]$NoAccessValidated, [switch]$ConfirmCleanup
    )
    Assert-Workshop ($TenantId -ne [guid]::Empty) '-TenantId must identify the workshop tenant.'
    Assert-Workshop ($Cohort -cmatch '^[a-z0-9][a-z0-9-]{0,23}$') 'Cohort must be a lowercase name of up to 24 letters, digits, or hyphens.'
    Assert-Workshop ($Action -in @('prepare', 'issue-taps', 'assign-access', 'cleanup')) 'Choose prepare, issue-taps, assign-access, or cleanup.'
    Assert-Workshop (-not ($RosterPath -and $PSBoundParameters.ContainsKey('Count'))) 'Use either -Count or -RosterPath, not both.'
    Assert-Workshop (-not $ReplaceTaps -or $Action -eq 'issue-taps') '-ReplaceTaps applies only to issue-taps.'
    Assert-Workshop (-not $RetryUnconfirmedGroupCreation -or $Action -eq 'prepare') '-RetryUnconfirmedGroupCreation applies only to prepare after an organizer verifies the missing group in Entra.'
    Assert-Workshop (-not $Alias -or $Action -ne 'prepare') 'Use a roster or count for prepare; -Alias selects already-prepared participants.'
    if ($Action -eq 'issue-taps') {
        Assert-Workshop $IsWindows 'Bulk TAP handoff requires Windows DPAPI. The script will not create credentials with a plaintext fallback.'
        Assert-Workshop ($LifetimeMinutes -in 10..60) 'TAP lifetime must be 10 to 60 minutes.'
    }
    if ($Action -eq 'assign-access') {
        Assert-Workshop $NoAccessValidated 'First validate TAP sign-in and no workshop access with the pilot account, then pass -NoAccessValidated.'
        Assert-Workshop ($SubscriptionId -ne [guid]::Empty) 'assign-access requires -SubscriptionId.'
    }
    if ($Action -eq 'cleanup') { Assert-Workshop $ConfirmCleanup 'Cleanup removes cohort-owned users and access. Pass -ConfirmCleanup to request it explicitly.' }
    if ($RosterOutput) { Assert-Workshop ([IO.Path]::GetExtension($RosterOutput) -eq '.csv') 'RosterOutput must be a CSV file.' }
    $rows = @()
    if ($Action -eq 'prepare') {
        Assert-Workshop ($Domain -match '^[a-z0-9][a-z0-9-]*\.onmicrosoft\.com$') 'prepare requires the tenant managed -Domain, for example <tenant>.onmicrosoft.com.'
        $Domain = $Domain.ToLowerInvariant()
        Assert-Workshop ($Count -in 1..1000) 'Count must be 1 to 1000.'
        $rows = @(Get-WorkshopInputRoster $RosterPath $Count $Cohort)
    }
    if (-not $StateDirectory) {
        $StateDirectory = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) `
            (Join-Path 'SreAgentWorkshop' (Join-Path $TenantId.ToString() $Cohort))
    }
    $directory = Initialize-WorkshopDirectory $StateDirectory
    $lock = $null
    try {
        try { $lock = [IO.File]::Open((Join-Path $directory '.lock'), 'OpenOrCreate', 'ReadWrite', 'None') } catch {
            throw (New-WorkshopError 'This cohort state is in use. Wait for the other organizer command to finish.')
        }
        Set-WorkshopPrivatePermissions (Join-Path $directory '.lock')
        $state = Get-WorkshopState $directory $TenantId.ToString() $Cohort -Create:($Action -eq 'prepare')
        if ($Action -eq 'prepare') {
            Assert-Workshop (-not $state.domain -or $state.domain -eq $Domain) 'A cohort cannot change its username domain.'
            $state.domain = $Domain
        }
        Save-WorkshopState $state $directory
        Connect-WorkshopGraph $TenantId.ToString() $Action
        if ($Action -eq 'prepare') {
            $domainInfo = Invoke-WorkshopGraph -Path "/domains/$Domain`?`$select=id,isVerified,authenticationType"
            Assert-Workshop ($domainInfo.id -eq $Domain -and $domainInfo.isVerified -eq $true -and $domainInfo.authenticationType -eq 'Managed') `
                'The requested username domain is not a verified managed domain of this tenant.'
            Ensure-WorkshopGroup $state $directory -RetryUnconfirmed:$RetryUnconfirmedGroupCreation
            $keys = @($rows | ForEach-Object { $_.Alias })
        } else {
            $keys = @(if ($Alias) { $Alias } else {
                $state.participants.Keys | Where-Object { $Action -eq 'cleanup' -or $state.participants[$_].status -ne 'cleaned' } | Sort-Object
            })
            Assert-Workshop ($keys.Count -gt 0 -and @($keys | Select-Object -Unique).Count -eq $keys.Count) 'Select at least one unique prepared participant.'
            foreach ($key in $keys) {
                Assert-Workshop ($key -cmatch '^[a-z0-9][a-z0-9-]{0,23}$' -and $state.participants.Contains($key)) 'A selected alias is not in this prepared cohort.'
            }
            $members = @(Get-WorkshopMembers $state)
            if ($Action -ne 'cleanup') {
                foreach ($member in $members) {
                    Assert-Workshop ($member['@odata.type'] -eq '#microsoft.graph.user' -and
                        $member.id -in @($state.participants.Values | ForEach-Object { $_.userId })) `
                        'The group contains an untracked user or non-user member. Resolve its membership before issuing TAPs or access.'
                }
                foreach ($key in $keys) {
                    Assert-Workshop ($state.participants[$key].userId -in @($members | ForEach-Object { $_.id })) 'A selected participant is missing from the workshop group.'
                }
            }
        }
        if ($Action -eq 'issue-taps') {
            $policy = Invoke-WorkshopGraph -Path '/policies/authenticationMethodsPolicy/authenticationMethodConfigurations/temporaryAccessPass'
            Assert-WorkshopTapPolicy $policy $state.groupId $LifetimeMinutes
        }
        $resolved = @{}
        if ($Action -eq 'assign-access') {
            $account = Invoke-WorkshopAzure @('account', 'show', '--subscription', $SubscriptionId.ToString()) -Read
            Assert-Workshop ($account.tenantId -eq $TenantId.ToString() -and $account.id -eq $SubscriptionId.ToString() -and
                $account.state -eq 'Enabled') 'Azure CLI is not connected to the requested enabled subscription in the workshop tenant.'
            $map = @(Get-WorkshopEnvironmentMap $EnvironmentMap $keys)
            foreach ($key in $keys) {
                $participant = $state.participants[$key]
                Assert-Workshop (-not $participant.cleanupStarted) 'Finish participant cleanup before assigning access.'
                Assert-WorkshopUser (Get-WorkshopUser $participant.userId) $state $participant.username
                $row = @($map | Where-Object Alias -eq $key)[0]
                Assert-Workshop (-not $participant.resourceGroup -or ($participant.resourceGroup -eq $row.ResourceGroup -and $participant.environment -eq $row.Environment)) `
                    'An existing participant cannot be moved to another environment; clean up its old assignments first.'
                Assert-Workshop (-not $participant.subscription -or $participant.subscription -eq $SubscriptionId.ToString()) `
                    'An existing participant cannot be moved to another subscription.'
                $resolved[$key] = Resolve-WorkshopEnvironment $row $SubscriptionId.ToString()
                foreach ($scopeName in $participant.scopes.Keys) {
                    Assert-Workshop ($participant.scopes[$scopeName] -eq $resolved[$key][$scopeName]) `
                        'Environment resource scopes changed; clean up the previous access before replacing them.'
                }
            }
            $vmRole = Ensure-WorkshopRole $SubscriptionId.ToString() $directory 'SRE Workshop VM Run Command' `
                @('Microsoft.Compute/virtualMachines/read', 'Microsoft.Compute/virtualMachines/runCommand/action')
            $faultRole = Ensure-WorkshopRole $SubscriptionId.ToString() $directory 'SRE Workshop PostgreSQL Fault Rule' `
                @('Microsoft.Network/networkSecurityGroups/securityRules/read', 'Microsoft.Network/networkSecurityGroups/securityRules/write')
        }
        $failed = 0
        foreach ($key in $keys) {
            try {
                switch ($Action) {
                    'prepare' {
                        Prepare-WorkshopParticipant $state $directory @($rows | Where-Object Alias -eq $key)[0]
                        $status = 'prepared'
                    }
                    'issue-taps' { $status = Issue-WorkshopTap $state $directory $key $LifetimeMinutes -Replace:$ReplaceTaps }
                    'assign-access' {
                        $participant = $state.participants[$key]
                        $row = @($map | Where-Object Alias -eq $key)[0]
                        $participant.environment = $row.Environment
                        $participant.resourceGroup = $row.ResourceGroup
                        $participant.subscription = $SubscriptionId.ToString()
                        $participant.scopes = $resolved[$key]
                        Save-WorkshopState $state $directory
                        $scopes = $resolved[$key]
                        foreach ($grant in @(
                            @{ role = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'; scope = $scopes.resourceGroup },
                            @{ role = '73c42c96-874c-492b-b04d-ab87d138a893'; scope = $scopes.workspace },
                            @{ role = $vmRole; scope = $scopes.vm },
                            @{ role = $faultRole; scope = $scopes.faultRule },
                            @{ role = 'e79298df-d852-4c6d-84f9-5d13249d1e55'; scope = $scopes.agent }
                        )) { Ensure-WorkshopAssignment $state $directory $key $SubscriptionId.ToString() $grant.role $grant.scope }
                        $participant.status = 'access-assigned'
                        $status = 'access-assigned'
                    }
                    'cleanup' { Remove-WorkshopParticipant $state $directory $key; $status = 'cleaned' }
                }
                Save-WorkshopState $state $directory
                Write-Host "[$Action] ${key}: $status"
            } catch {
                $failed++
                if ($state.participants.Contains($key)) { $state.participants[$key].status = "$Action-failed" }
                Save-WorkshopState $state $directory
                Write-Host "[$Action] ${key}: FAILED. $(Get-WorkshopErrorMessage $_)"
            }
        }
        Save-WorkshopRoster $state $directory $RosterOutput
        Write-Host "[$Action] $($keys.Count - $failed) succeeded; $failed failed."
        if ($Action -eq 'prepare') {
            Write-Host "Workshop group: SRE Workshop Participants ($Cohort). No Azure RBAC was created. Enable TAP for this group before issue-taps."
        }
        Assert-Workshop ($failed -eq 0) 'Some participants failed. Resolve the reported causes and rerun the same action; successful identities and grants are preserved.'
    } finally {
        if ($lock) { $lock.Dispose() }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-WorkshopParticipants @PSBoundParameters } catch {
        Write-Error -Message (Get-WorkshopErrorMessage $_) -ErrorAction Continue
        exit 1
    }
}
