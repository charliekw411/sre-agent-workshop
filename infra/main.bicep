metadata description = 'Foundation resources for the Azure SRE Agent workshop: identity, container registry, observability, Azure SQL Database, and the Container Apps environment.'

targetScope = 'resourceGroup'

@description('Azure region for every resource. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Short lowercase alphanumeric suffix appended to every resource name. Must be globally unique enough for the container registry and SQL logical server names.')
@minLength(3)
@maxLength(12)
param suffix string

@description('Administrator login for the Azure SQL logical server.')
@minLength(4)
param sqlAdminLogin string = 'sreworkshopadmin'

@description('Administrator password for the Azure SQL logical server. Supply a value from a secure source; never commit it.')
@secure()
@minLength(16)
param sqlAdminPassword string

@description('Object ID of the Microsoft Entra principal that becomes the SQL server Entra administrator. Defaults to no Entra administrator.')
param sqlEntraAdminObjectId string = ''

@description('Display name of the Microsoft Entra principal that becomes the SQL server Entra administrator.')
param sqlEntraAdminName string = ''

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
    administratorLogin: sqlAdminLogin
    administratorLoginPassword: sqlAdminPassword
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    restrictOutboundNetworkAccess: 'Disabled'
    administrators: empty(sqlEntraAdminObjectId)
      ? null
      : {
          administratorType: 'ActiveDirectory'
          principalType: 'User'
          login: sqlEntraAdminName
          sid: sqlEntraAdminObjectId
          tenantId: subscription().tenantId
          azureADOnlyAuthentication: false
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

output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer

output workspaceName string = workspace.name
output workspaceResourceId string = workspace.id
output workspaceCustomerId string = workspace.properties.customerId

output appInsightsName string = appInsights.name
output appInsightsResourceId string = appInsights.id
@description('Application Insights connection string. Treated as a configuration value, not a credential, but still avoid printing it in shared logs.')
output appInsightsConnectionString string = appInsights.properties.ConnectionString

output sqlServerName string = sqlServer.name
output sqlServerFqdn string = sqlServer.properties.fullyQualifiedDomainName
output sqlDatabaseName string = database.name
output sqlDatabaseResourceId string = database.id

output containerAppsEnvironmentName string = containerAppsEnvironment.name
output containerAppsEnvironmentResourceId string = containerAppsEnvironment.id
