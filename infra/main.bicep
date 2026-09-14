metadata description = 'Foundation resources for the Azure SRE Agent workshop: identity, container registry, observability, Azure SQL Database, and the Container Apps environment.'

targetScope = 'resourceGroup'

@description('Azure region for every resource. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Short lowercase alphanumeric suffix appended to every resource name. Must be globally unique enough for the container registry and SQL logical server names.')
@minLength(3)
@maxLength(12)
param suffix string

@description('Object ID of the attendee or service principal deploying the workshop.')
param deployerPrincipalId string

@allowed([
  'User'
  'ServicePrincipal'
])
param deployerPrincipalType string = 'User'

@description('Reruns the server-side secret existence check on each deployment without rotating an existing token.')
param tokenInitializationRun string = utcNow()

@description('Log Analytics retention in days.')
@minValue(30)
@maxValue(730)
param logRetentionInDays int = 30

@description('Maximum size of the orders database in bytes. The small ceiling is what makes the Module 10 storage exhaustion lab finish in minutes.')
param sqlMaxSizeBytes int = 1073741824

@description('Tags applied to every resource.')
param tags object = {
  workload: 'sre-agent-workshop'
  environment: 'workshop'
}

var databaseName = 'sqldb-orders'
var acrName = 'acr${suffix}'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${suffix}'
  location: location
  tags: tags
}

resource catalogIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-catalog-${suffix}'
  location: location
  tags: tags
}

resource bootstrapIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-bootstrap-${suffix}'
  location: location
  tags: tags
}

resource tokenIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-token-${suffix}'
  location: location
  tags: tags
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    // Image pulls use the workload managed identity instead of admin credentials.
    adminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, acrPullRoleId)
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
  }
}

resource catalogAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, catalogIdentity.id, acrPullRoleId)
  scope: registry
  properties: {
    principalId: catalogIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
  }
}

resource bootstrapAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, bootstrapIdentity.id, acrPullRoleId)
  scope: registry
  properties: {
    principalId: bootstrapIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-${suffix}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
  }
}

var secretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var secretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource ordersSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, identity.id, secretsUserRoleId)
  scope: keyVault
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsUserRoleId)
  }
}

resource attendeeSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, deployerPrincipalId, secretsUserRoleId)
  scope: keyVault
  properties: {
    principalId: deployerPrincipalId
    principalType: deployerPrincipalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsUserRoleId)
  }
}

resource tokenSecretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, tokenIdentity.id, secretsOfficerRoleId)
  scope: keyVault
  properties: {
    principalId: tokenIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsOfficerRoleId)
  }
}

resource faultTokenGenerator 'Microsoft.Resources/deploymentScripts@2023-08-01' = {
  name: 'generate-fault-token'
  location: location
  tags: tags
  kind: 'AzureCLI'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${tokenIdentity.id}': {}
    }
  }
  properties: {
    azCliVersion: '2.64.0'
    forceUpdateTag: tokenInitializationRun
    timeout: 'PT20M'
    cleanupPreference: 'Always'
    retentionInterval: 'P1D'
    environmentVariables: [
      {
        name: 'KEY_VAULT_URI'
        value: keyVault.properties.vaultUri
      }
      {
        name: 'KEY_VAULT_RESOURCE'
        value: 'https://${substring(environment().suffixes.keyvaultDns, 1)}'
      }
    ]
    // The generated secret stays in Python memory and an HTTPS body, never CLI arguments or logs.
    scriptContent: '''
set +x
set -eu
export AZURE_CORE_OUTPUT=none
export AZURE_CORE_ONLY_SHOW_ERRORS=true
export AZURE_LOGGING_ENABLE_LOG_FILE=false
export AZURE_CORE_COLLECT_TELEMETRY=false
python3 - <<'PY'
import json
import os
import secrets
import subprocess
import time
import urllib.request

vault = os.environ["KEY_VAULT_URI"].rstrip("/")
for attempt in range(90):
    try:
        credential = subprocess.run(
            ["az", "account", "get-access-token", "--resource", os.environ["KEY_VAULT_RESOURCE"],
             "--query", "accessToken", "--output", "tsv", "--only-show-errors"],
            capture_output=True, text=True, check=True, timeout=60,
        ).stdout.strip()
        headers = {"Authorization": "Bearer " + credential, "Content-Type": "application/json"}
        url = vault + "/secrets?api-version=7.4"
        while url:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
                page = json.load(response)
            if any(item["id"].split("/secrets/", 1)[-1].split("/")[0] == "fault-token"
                   for item in page.get("value", [])):
                raise SystemExit(0)
            url = page.get("nextLink")
        body = json.dumps({"value": secrets.token_hex(32)}).encode()
        request = urllib.request.Request(
            vault + "/secrets/fault-token?api-version=7.4",
            data=body, headers=headers, method="PUT",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
        raise SystemExit(0)
    except Exception:
        # Retry propagation/transient failures; never print response bodies or exception details.
        time.sleep(10)
raise SystemExit("Unable to initialize the workshop token in Key Vault.")
PY
'''
  }
  dependsOn: [
    tokenSecretsOfficer
  ]
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'law-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: logRetentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${suffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    IngestionMode: 'LogAnalytics'
    DisableIpMasking: false
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: 'sql-${suffix}'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    restrictOutboundNetworkAccess: 'Disabled'
    administrators: {
      administratorType: 'ActiveDirectory'
      principalType: 'Application'
      login: bootstrapIdentity.name
      sid: bootstrapIdentity.properties.principalId
      tenantId: subscription().tenantId
      azureADOnlyAuthentication: true
    }
  }
}

// Allows Container Apps outbound traffic, which presents as an Azure service address.
resource allowAzureServices 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sqlServer
  name: 'AllowAllWindowsAzureIps'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// A 1 GB ceiling on an S0 database fills fast enough for a lab and costs about a dollar a week.
resource database 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: databaseName
  location: location
  tags: tags
  sku: {
    name: 'S0'
    tier: 'Standard'
    capacity: 10
  }
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
    maxSizeBytes: sqlMaxSizeBytes
    zoneRedundant: false
    readScale: 'Disabled'
    requestedBackupStorageRedundancy: 'Local'
  }
}

resource databaseDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-to-law'
  scope: database
  properties: {
    workspaceId: workspace.id
    logs: [
      {
        category: 'SQLInsights'
        enabled: true
      }
      {
        category: 'Errors'
        enabled: true
      }
      {
        category: 'Blocks'
        enabled: true
      }
      {
        category: 'Timeouts'
        enabled: true
      }
      {
        category: 'QueryStoreRuntimeStatistics'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'Basic'
        enabled: true
      }
    ]
  }
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${suffix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
    zoneRedundant: false
  }
}

resource environmentDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-to-law'
  scope: containerAppsEnvironment
  properties: {
    workspaceId: workspace.id
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
  }
}

output identityName string = identity.name
output identityResourceId string = identity.id
output identityClientId string = identity.properties.clientId
output identityPrincipalId string = identity.properties.principalId
output bootstrapIdentityResourceId string = bootstrapIdentity.id
output bootstrapIdentityClientId string = bootstrapIdentity.properties.clientId
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output faultTokenSecretUri string = '${keyVault.properties.vaultUri}secrets/fault-token'

output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer

output workspaceName string = workspace.name
output workspaceResourceId string = workspace.id
output workspaceCustomerId string = workspace.properties.customerId

output appInsightsName string = appInsights.name
output appInsightsResourceId string = appInsights.id

output sqlServerName string = sqlServer.name
output sqlServerFqdn string = sqlServer.properties.fullyQualifiedDomainName
output sqlDatabaseName string = database.name
output sqlDatabaseResourceId string = database.id

output containerAppsEnvironmentName string = containerAppsEnvironment.name
output containerAppsEnvironmentResourceId string = containerAppsEnvironment.id
