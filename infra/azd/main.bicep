metadata description = 'Azure Developer CLI entry point for a fresh single-VM Azure SRE Agent workshop environment.'

targetScope = 'subscription'

@description('Name of the Azure Developer CLI environment.')
@minLength(1)
@maxLength(64)
param envName string

@description('Azure region for all workshop resources.')
param location string = 'australiaeast'

@description('Object ID of the signed-in attendee or service principal.')
@minLength(1)
param deployerPrincipalId string

@allowed([
  'User'
  'ServicePrincipal'
])
param deployerPrincipalType string = 'User'

@description('SSH public key generated and retained in the azd environment by preup. No SSH port is exposed.')
@secure()
@minLength(1)
param vmSshPublicKey string

@description('VM size override. The default supplies two non-burstable x64 vCPUs.')
param vmSize string = 'Standard_D2as_v5'

@description('Separate SQLite data disk capacity in GiB; keep unchanged on redeployment unless growing the disk.')
@minValue(4)
@maxValue(1023)
param dataDiskSizeGiB int = 8

@description('Optional email address for workshop alert notifications.')
param alertEmail string = ''

var suffix = take(uniqueString(subscription().id, envName), 12)
var resourceGroupName = 'rg-sre-agent-workshop-${envName}'
var tags = {
  workload: 'sre-agent-workshop'
  environment: envName
  'azd-env-name': envName
  'workshop-architecture': 'single-vm'
}

resource workshopResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module workshop '../main.bicep' = {
  name: 'workshop'
  scope: workshopResourceGroup
  params: {
    location: location
    suffix: suffix
    deployerPrincipalId: deployerPrincipalId
    deployerPrincipalType: deployerPrincipalType
    vmSshPublicKey: vmSshPublicKey
    vmSize: vmSize
    dataDiskSizeGiB: dataDiskSizeGiB
    alertEmail: alertEmail
    tags: tags
  }
}

output AZURE_RESOURCE_GROUP string = workshopResourceGroup.name
output RESOURCE_GROUP string = workshopResourceGroup.name
output WORKSHOP_SUFFIX string = suffix
output LOCATION string = location
output SUBSCRIPTION_ID string = subscription().subscriptionId
output TENANT_ID string = tenant().tenantId
output VM_NAME string = workshop.outputs.vmName
output VM_RESOURCE_ID string = workshop.outputs.vmResourceId
output VM_PRINCIPAL_ID string = workshop.outputs.vmPrincipalId
output VM_ADMIN_USERNAME string = workshop.outputs.vmAdminUsername
output DATA_DISK_NAME string = workshop.outputs.dataDiskName
output DATA_DISK_RESOURCE_ID string = workshop.outputs.dataDiskResourceId
output ORDERS_API_FQDN string = workshop.outputs.ordersApiFqdn
output SERVICE_ORDERS_API_ENDPOINT_URL string = workshop.outputs.ordersApiEndpoint
output LOG_ANALYTICS_NAME string = workshop.outputs.workspaceName
output LOG_ANALYTICS_ID string = workshop.outputs.workspaceResourceId
output LOG_ANALYTICS_CUSTOMER_ID string = workshop.outputs.workspaceCustomerId
output APP_INSIGHTS_NAME string = workshop.outputs.appInsightsName
output APP_INSIGHTS_RESOURCE_ID string = workshop.outputs.appInsightsResourceId
output VIRTUAL_NETWORK_NAME string = workshop.outputs.virtualNetworkName
output VIRTUAL_NETWORK_RESOURCE_ID string = workshop.outputs.virtualNetworkResourceId
output PUBLIC_IP_ADDRESS string = workshop.outputs.publicIpAddress
output PUBLIC_IP_RESOURCE_ID string = workshop.outputs.publicIpResourceId
output DATA_COLLECTION_RULE_NAME string = workshop.outputs.dataCollectionRuleName
output DATA_COLLECTION_RULE_ID string = workshop.outputs.dataCollectionRuleId
output ACTION_GROUP_NAME string = workshop.outputs.actionGroupName
output ACTION_GROUP_ID string = workshop.outputs.actionGroupId
output SRE_AGENT_NAME string = workshop.outputs.sreAgentName
output SRE_AGENT_RESOURCE_ID string = workshop.outputs.sreAgentResourceId
output SRE_AGENT_ENDPOINT string = workshop.outputs.sreAgentEndpoint
output SRE_AGENT_PRINCIPAL_ID string = workshop.outputs.sreAgentPrincipalId
output SRE_AGENT_IDENTITY_RESOURCE_ID string = workshop.outputs.sreAgentIdentityResourceId
output SRE_AGENT_IDENTITY_PRINCIPAL_ID string = workshop.outputs.sreAgentIdentityPrincipalId
