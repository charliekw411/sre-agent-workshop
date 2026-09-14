metadata description = 'Azure Developer CLI entry point for the complete Azure SRE Agent workshop environment.'

targetScope = 'subscription'

@description('Name of the Azure Developer CLI environment.')
@minLength(1)
param environmentName string

@description('Azure region for all workshop resources.')
param location string

@description('Object ID of the signed-in attendee or service principal.')
param deployerPrincipalId string

@allowed([
  'User'
  'ServicePrincipal'
])
param deployerPrincipalType string = 'User'

@description('Optional email address that receives workshop alert notifications.')
param alertEmail string = ''

var normalizedEnvironmentName = toLower(replace(environmentName, '-', ''))
var suffix = '${take(normalizedEnvironmentName, 5)}${take(uniqueString(subscription().id, environmentName), 7)}'
var resourceGroupName = 'rg-sre-agent-workshop-${environmentName}'
var tags = {
  workload: 'sre-agent-workshop'
  environment: environmentName
  'azd-env-name': environmentName
}

resource workshopResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module foundation '../main.bicep' = {
  scope: workshopResourceGroup
  params: {
    location: location
    suffix: suffix
    deployerPrincipalId: deployerPrincipalId
    deployerPrincipalType: deployerPrincipalType
    tags: tags
  }
}

module applications '../apps.bicep' = {
  scope: workshopResourceGroup
  params: {
    location: location
    environmentName: environmentName
    suffix: suffix
    ordersImage: 'mcr.microsoft.com/dotnet/samples:aspnetapp'
    catalogImage: 'mcr.microsoft.com/dotnet/samples:aspnetapp'
    tags: tags
  }
  dependsOn: [
    foundation
  ]
}

module monitoring '../alerts.bicep' = {
  scope: workshopResourceGroup
  params: {
    location: location
    suffix: suffix
    alertEmail: alertEmail
    tags: tags
  }
  dependsOn: [
    applications
  ]
}

module sreAgent '../sre-agent.bicep' = {
  scope: workshopResourceGroup
  params: {
    location: location
    suffix: suffix
    deployerPrincipalId: deployerPrincipalId
    deployerPrincipalType: deployerPrincipalType
    tags: tags
  }
  dependsOn: [
    foundation
  ]
}

output AZURE_RESOURCE_GROUP string = workshopResourceGroup.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = foundation.outputs.registryLoginServer
output AZURE_CONTAINER_REGISTRY_NAME string = foundation.outputs.registryName
output WORKSHOP_SUFFIX string = suffix
output LOCATION string = location
output RESOURCE_GROUP string = workshopResourceGroup.name
output SUBSCRIPTION_ID string = subscription().subscriptionId
output TENANT_ID string = tenant().tenantId
output ACR_NAME string = foundation.outputs.registryName
output ACR_LOGIN_SERVER string = foundation.outputs.registryLoginServer
output LOG_ANALYTICS_NAME string = foundation.outputs.workspaceName
output LOG_ANALYTICS_ID string = foundation.outputs.workspaceResourceId
output LOG_ANALYTICS_CUSTOMER_ID string = foundation.outputs.workspaceCustomerId
output APP_INSIGHTS_NAME string = foundation.outputs.appInsightsName
output SQL_SERVER_NAME string = foundation.outputs.sqlServerName
output SQL_DATABASE_NAME string = foundation.outputs.sqlDatabaseName
output CONTAINER_ENV_NAME string = foundation.outputs.containerAppsEnvironmentName
output ORDERS_API_FQDN string = applications.outputs.ordersApiFqdn
output SERVICE_ORDERS_API_ENDPOINT_URL string = 'https://${applications.outputs.ordersApiFqdn}'
output BOOTSTRAP_JOB_NAME string = applications.outputs.bootstrapJobName
output KEY_VAULT_NAME string = foundation.outputs.keyVaultName
output KEY_VAULT_URI string = foundation.outputs.keyVaultUri
output FAULT_TOKEN_SECRET_URI string = foundation.outputs.faultTokenSecretUri
output ORDERS_IDENTITY_PRINCIPAL_ID string = foundation.outputs.identityPrincipalId
output ORDERS_IDENTITY_CLIENT_ID string = foundation.outputs.identityClientId
output ORDERS_IDENTITY_RESOURCE_ID string = foundation.outputs.identityResourceId
output SRE_AGENT_NAME string = sreAgent.outputs.agentName
output SRE_AGENT_RESOURCE_ID string = sreAgent.outputs.agentResourceId
output SRE_AGENT_ENDPOINT string = sreAgent.outputs.agentEndpoint
output SRE_AGENT_PRINCIPAL_ID string = sreAgent.outputs.agentPrincipalId
output SRE_AGENT_IDENTITY_RESOURCE_ID string = sreAgent.outputs.operationalIdentityResourceId
output SRE_AGENT_IDENTITY_PRINCIPAL_ID string = sreAgent.outputs.operationalIdentityPrincipalId
