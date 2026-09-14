metadata description = 'Read-only Azure SRE Agent scoped to the workshop resource group.'

targetScope = 'resourceGroup'

param location string = resourceGroup().location

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

param tags object = {}

resource operationalIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-sre-${suffix}'
  location: location
  tags: tags
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: 'law-${suffix}'
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: 'appi-${suffix}'
}

var readerRoleId = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'
var monitoringReaderRoleId = '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
var logAnalyticsReaderRoleId = '73c42c96-874c-492b-b04d-ab87d138a893'
var administratorRoleId = 'e79298df-d852-4c6d-84f9-5d13249d1e55'

resource operationalReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, operationalIdentity.id, readerRoleId)
  properties: {
    principalId: operationalIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', readerRoleId)
  }
}

resource operationalMonitoringReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, operationalIdentity.id, monitoringReaderRoleId)
  properties: {
    principalId: operationalIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringReaderRoleId)
  }
}

resource operationalLogReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(workspace.id, operationalIdentity.id, logAnalyticsReaderRoleId)
  scope: workspace
  properties: {
    principalId: operationalIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', logAnalyticsReaderRoleId)
  }
}

// Verified against microsoft/sre-agent/sreagent-templates/bicep/agent-core.bicep.
// This preview API uses Low/High and Review/Automatic, not Reader/ReadOnly.
#disable-next-line BCP081
resource agent 'Microsoft.App/agents@2025-05-01-preview' = {
  name: 'sre-${suffix}'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${operationalIdentity.id}': {}
    }
  }
  properties: {
    knowledgeGraphConfiguration: {
      identity: operationalIdentity.id
      managedResources: [
        resourceGroup().id
      ]
    }
    actionConfiguration: {
      identity: operationalIdentity.id
      accessLevel: 'Low'
      mode: 'Review'
    }
    incidentManagementConfiguration: {
      type: 'AzMonitor'
      connectionName: 'azmonitor'
    }
    logConfiguration: {
      applicationInsightsConfiguration: {
        appId: appInsights.properties.AppId
        connectionString: appInsights.properties.ConnectionString
      }
    }
  }
  dependsOn: [
    operationalReader
    operationalMonitoringReader
    operationalLogReader
  ]
}

// The first-party template also grants the system identity read access for connector queries.
resource systemReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, agent.id, readerRoleId)
  properties: {
    principalId: agent.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', readerRoleId)
  }
}

resource systemMonitoringReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, agent.id, monitoringReaderRoleId)
  properties: {
    principalId: agent.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringReaderRoleId)
  }
}

resource systemLogReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(workspace.id, agent.id, logAnalyticsReaderRoleId)
  scope: workspace
  properties: {
    principalId: agent.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', logAnalyticsReaderRoleId)
  }
}

// Typed connector shape from microsoft/sre-agent agent-extensions.bicep at 3fa8db85.
#disable-next-line BCP081
resource appInsightsConnector 'Microsoft.App/agents/connectors@2025-05-01-preview' = {
  parent: agent
  name: 'app-insights'
  properties: {
    dataConnectorType: 'AppInsights'
    dataSource: appInsights.id
    extendedProperties: {
      armResourceId: appInsights.id
      resource: {
        name: appInsights.name
      }
      appId: appInsights.properties.AppId
    }
    identity: 'system'
  }
  dependsOn: [
    systemReader
    systemMonitoringReader
    systemLogReader
  ]
}

#disable-next-line BCP081
resource logAnalyticsConnector 'Microsoft.App/agents/connectors@2025-05-01-preview' = {
  parent: agent
  name: 'log-analytics'
  properties: {
    dataConnectorType: 'LogAnalytics'
    dataSource: workspace.id
    extendedProperties: {
      armResourceId: workspace.id
      resource: {
        name: workspace.name
      }
    }
    identity: 'system'
  }
  dependsOn: [
    systemReader
    systemMonitoringReader
    systemLogReader
    appInsightsConnector
  ]
}

resource deployerAdministrator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(agent.id, deployerPrincipalId, administratorRoleId)
  scope: agent
  properties: {
    principalId: deployerPrincipalId
    principalType: deployerPrincipalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', administratorRoleId)
  }
}

output agentName string = agent.name
output agentResourceId string = agent.id
output agentEndpoint string = agent.properties.agentEndpoint
output agentPrincipalId string = agent.identity.principalId
output operationalIdentityResourceId string = operationalIdentity.id
output operationalIdentityPrincipalId string = operationalIdentity.properties.principalId
