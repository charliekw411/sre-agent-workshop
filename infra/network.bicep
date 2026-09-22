metadata description = 'Private SQL and Key Vault connectivity for the workshop Container Apps environment.'

targetScope = 'resourceGroup'

param location string = resourceGroup().location
param suffix string
param sqlServerId string
param keyVaultId string
param tags object = {}

resource network 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-${suffix}'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.240.0.0/16'
      ]
    }
    subnets: [
      {
        name: 'container-apps'
        properties: {
          addressPrefix: '10.240.0.0/23'
          delegations: [
            {
              name: 'container-apps'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: '10.240.2.0/27'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource appsSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: network
  name: 'container-apps'
}

resource endpointsSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: network
  name: 'private-endpoints'
}

var privateServices = [
  {
    name: 'sql'
    resourceId: sqlServerId
    groupId: 'sqlServer'
    zone: 'privatelink${environment().suffixes.sqlServerHostname}'
  }
  {
    name: 'vault'
    resourceId: keyVaultId
    groupId: 'vault'
    zone: 'privatelink.vaultcore.azure.net'
  }
]

resource zones 'Microsoft.Network/privateDnsZones@2020-06-01' = [for service in privateServices: {
  name: service.zone
  location: 'global'
  tags: tags
}]

resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = [for (service, index) in privateServices: {
  parent: zones[index]
  name: 'workshop'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: network.id
    }
  }
}]

resource endpoints 'Microsoft.Network/privateEndpoints@2024-05-01' = [for service in privateServices: {
  name: 'pe-${service.name}-${suffix}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: endpointsSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: service.name
        properties: {
          privateLinkServiceId: service.resourceId
          groupIds: [
            service.groupId
          ]
        }
      }
    ]
  }
}]

resource zoneGroups 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = [for (service, index) in privateServices: {
  parent: endpoints[index]
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: service.name
        properties: {
          privateDnsZoneId: zones[index].id
        }
      }
    ]
  }
}]

output virtualNetworkName string = network.name
output infrastructureSubnetId string = appsSubnet.id
output sqlPrivateEndpointName string = endpoints[0].name
output keyVaultPrivateEndpointName string = endpoints[1].name
