metadata description = 'Idempotently inject or reset the bounded PostgreSQL TCP 5432 connectivity incident.'

targetScope = 'resourceGroup'

param networkSecurityGroupName string
param injectPostgresqlFault bool

resource networkSecurityGroup 'Microsoft.Network/networkSecurityGroups@2024-05-01' existing = {
  name: networkSecurityGroupName
}

resource faultRule 'Microsoft.Network/networkSecurityGroups/securityRules@2024-05-01' = {
  parent: networkSecurityGroup
  name: 'PostgreSqlFaultInjection'
  properties: {
    description: 'Workshop toggle for VM-to-PostgreSQL connectivity; azd up reconciles this rule to Allow.'
    priority: 100
    direction: 'Outbound'
    access: injectPostgresqlFault ? 'Deny' : 'Allow'
    protocol: 'Tcp'
    sourceAddressPrefix: '10.240.0.0/27'
    sourcePortRange: '*'
    destinationAddressPrefix: '10.240.0.32/27'
    destinationPortRange: '5432'
  }
}

output access string = faultRule.properties.access
