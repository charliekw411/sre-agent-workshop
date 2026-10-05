metadata description = 'Private Entra-only PostgreSQL Flexible Server for the Orders workshop.'

targetScope = 'resourceGroup'

param location string = resourceGroup().location

@minLength(3)
@maxLength(12)
param suffix string

param delegatedSubnetResourceId string
param virtualNetworkResourceId string
param workspaceResourceId string
param tags object = {}

var serverName = take('psql-orders-${suffix}', 63)
var databaseName = 'orders'
var privateDnsZoneName = 'private.postgres.database.azure.com'

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: privateDnsZoneName
  location: 'global'
  tags: tags
}

resource privateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: privateDnsZone
  name: 'orders-${suffix}'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetworkResourceId
    }
  }
}

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  tags: union(tags, {
    service: 'orders-database'
  })
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled'
      tenantId: tenant().tenantId
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      delegatedSubnetResourceId: delegatedSubnetResourceId
      privateDnsZoneArmResourceId: privateDnsZone.id
      publicNetworkAccess: 'Disabled'
    }
    storage: {
      autoGrow: 'Enabled'
      storageSizeGB: 32
    }
  }
  dependsOn: [
    privateDnsLink
  ]
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: server
  name: databaseName
  properties: {}
}

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'postgresql-observability'
  scope: server
  properties: {
    workspaceId: workspaceResourceId
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

output serverName string = server.name
output serverResourceId string = server.id
output serverFqdn string = server.properties.fullyQualifiedDomainName
output databaseName string = databaseName
output privateDnsZoneName string = privateDnsZone.name
output privateDnsZoneResourceId string = privateDnsZone.id
