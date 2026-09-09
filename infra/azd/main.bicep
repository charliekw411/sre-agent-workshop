metadata description = 'Azure Developer CLI entry point for the complete Azure SRE Agent workshop environment.'

targetScope = 'subscription'

@description('Name of the Azure Developer CLI environment.')
@minLength(1)
param environmentName string

@description('Azure region for all workshop resources.')
param location string

@description('Administrator password for the workshop Azure SQL logical server.')
@secure()
@minLength(16)
param sqlAdminPassword string

@description('Shared secret required by the workshop fault-injection endpoints.')
@secure()
param faultToken string

@description('Email address that receives workshop alert notifications.')
param alertEmail string

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
    sqlAdminPassword: sqlAdminPassword
    tags: tags
  }
}

module applications '../apps.bicep' = {
  scope: workshopResourceGroup
  params: {
    location: location
    environmentName: environmentName
    suffix: suffix
    ordersImage: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
    catalogImage: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
    sqlAdminPassword: sqlAdminPassword
    faultToken: faultToken
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

output AZURE_RESOURCE_GROUP string = workshopResourceGroup.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = foundation.outputs.registryLoginServer
output AZURE_CONTAINER_REGISTRY_NAME string = foundation.outputs.registryName
output WORKSHOP_SUFFIX string = suffix
output LOCATION string = location
output RESOURCE_GROUP string = workshopResourceGroup.name
output SUBSCRIPTION_ID string = subscription().subscriptionId
output TENANT_ID string = tenant().tenantId
output SQL_ADMIN_LOGIN string = 'sreworkshopadmin'
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
