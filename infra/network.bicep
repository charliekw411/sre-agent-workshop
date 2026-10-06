metadata description = 'Public Orders VM subnet and private delegated PostgreSQL subnet with a controlled TCP 5432 fault rule.'

targetScope = 'resourceGroup'

param location string = resourceGroup().location

@minLength(3)
@maxLength(12)
param suffix string

param tags object = {}

resource networkSecurityGroup 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-orders-${suffix}'
  location: location
  tags: union(tags, {
    component: 'postgresql-connectivity'
  })
  properties: {
    securityRules: [
      {
        name: 'AllowOrdersHttp'
        properties: {
          description: 'Public workshop Orders API; administration uses Azure Run Command, not SSH.'
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '8080'
        }
      }
      {
        name: 'DenyOtherInbound'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
      {
        name: 'PostgreSqlFaultInjection'
        properties: {
          description: 'Workshop toggle for VM-to-PostgreSQL connectivity; azd up reconciles this rule to Allow.'
          priority: 100
          direction: 'Outbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '10.240.0.0/27'
          sourcePortRange: '*'
          destinationAddressPrefix: '10.240.0.32/27'
          destinationPortRange: '5432'
        }
      }
    ]
  }
}

resource network 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-${suffix}'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.240.0.0/24'
      ]
    }
    subnets: [
      {
        name: 'orders'
        properties: {
          addressPrefix: '10.240.0.0/27'
          defaultOutboundAccess: false
          networkSecurityGroup: {
            id: networkSecurityGroup.id
          }
        }
      }
      {
        name: 'postgresql'
        properties: {
          addressPrefix: '10.240.0.32/27'
          delegations: [
            {
              name: 'Microsoft.DBforPostgreSQL.flexibleServers'
              properties: {
                serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers'
              }
            }
          ]
        }
      }
    ]
  }
}

resource ordersSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: network
  name: 'orders'
}

resource postgresqlSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: network
  name: 'postgresql'
}

resource publicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'pip-orders-${suffix}'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Regional'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
    dnsSettings: {
      domainNameLabel: 'orders-${suffix}'
    }
  }
}

resource networkInterface 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: 'nic-orders-${suffix}'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [
      {
        name: 'primary'
        properties: {
          primary: true
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: ordersSubnet.id
          }
          publicIPAddress: {
            id: publicIp.id
          }
        }
      }
    ]
  }
}

output virtualNetworkName string = network.name
output virtualNetworkResourceId string = network.id
output ordersSubnetResourceId string = ordersSubnet.id
output postgresqlSubnetResourceId string = postgresqlSubnet.id
output networkSecurityGroupName string = networkSecurityGroup.name
output networkSecurityGroupResourceId string = networkSecurityGroup.id
output postgresqlFaultRuleName string = 'PostgreSqlFaultInjection'
output postgresqlFaultRuleResourceId string = '${networkSecurityGroup.id}/securityRules/PostgreSqlFaultInjection'
output networkInterfaceResourceId string = networkInterface.id
output publicIpAddress string = publicIp.properties.ipAddress
output publicIpResourceId string = publicIp.id
output publicFqdn string = publicIp.properties.dnsSettings.fqdn
