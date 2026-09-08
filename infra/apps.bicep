metadata description = 'Contoso Order Services container apps for the Azure SRE Agent workshop. Deploy after the foundation template and after the container images have been built.'

targetScope = 'resourceGroup'

@description('Azure region for every resource. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Short lowercase alphanumeric suffix used by the foundation deployment.')
@minLength(3)
@maxLength(12)
param suffix string

@description('Fully qualified image reference for orders-api, for example acrsre42.azurecr.io/orders-api:v1.')
param ordersImage string

@description('Fully qualified image reference for catalog-api, for example acrsre42.azurecr.io/catalog-api:v1.')
param catalogImage string

@description('Administrator login for the Azure SQL logical server.')
param sqlAdminLogin string = 'sreworkshopadmin'

@description('Administrator password for the Azure SQL logical server.')
@secure()
param sqlAdminPassword string

@description('Shared secret required in the X-Fault-Token header by every fault-injection endpoint.')
@secure()
param faultToken string

@description('Tags applied to every resource.')
param tags object = {
  workload: 'sre-agent-workshop'
  environment: 'workshop'
}

var acrName = 'acr${suffix}'
var databaseName = 'sqldb-orders'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-${suffix}'
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: acrName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: 'appi-${suffix}'
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: 'cae-${suffix}'
}

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' existing = {
  name: 'sql-${suffix}'
}

var sqlConnectionString = 'Server=tcp:${sqlServer.properties.fullyQualifiedDomainName},1433;Initial Catalog=${databaseName};Persist Security Info=False;User ID=${sqlAdminLogin};Password=${sqlAdminPassword};MultipleActiveResultSets=False;Encrypt=True;TrustServerCertificate=False;Connection Timeout=30;'

resource catalogApi 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'catalog-api'
  location: location
  tags: union(tags, { service: 'catalog-api' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        // Internal only. The blast radius lesson in Module 08 depends on this service
        // being unreachable from outside the environment.
        external: false
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
      secrets: [
        {
          name: 'appinsights-connection-string'
          value: appInsights.properties.ConnectionString
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'catalog-api'
          image: catalogImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
            {
              name: 'ASPNETCORE_URLS'
              value: 'http://+:8080'
            }
            {
              name: 'SERVICE_NAME'
              value: 'catalog-api'
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health/live'
                port: 8080
              }
              initialDelaySeconds: 10
              periodSeconds: 15
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health/ready'
                port: 8080
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

resource ordersApi 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'orders-api'
  location: location
  tags: union(tags, { service: 'orders-api' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
      secrets: [
        {
          name: 'appinsights-connection-string'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'sql-connection-string'
          value: sqlConnectionString
        }
        {
          name: 'fault-token'
          value: faultToken
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'orders-api'
          image: ordersImage
          resources: {
            // Pinned small on purpose. A generous CPU allocation makes the Module 06
            // saturation incident take far longer to become visible.
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
            {
              name: 'ConnectionStrings__OrdersDb'
              secretRef: 'sql-connection-string'
            }
            {
              name: 'Fault__Token'
              secretRef: 'fault-token'
            }
            {
              name: 'Fault__Enabled'
              value: 'true'
            }
            {
              name: 'Catalog__BaseUrl'
              value: 'http://catalog-api'
            }
            {
              name: 'ASPNETCORE_URLS'
              value: 'http://+:8080'
            }
            {
              name: 'SERVICE_NAME'
              value: 'orders-api'
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                // Deliberately a process liveness check rather than a dependency check.
                // Module 08 asks you to explain why that choice hides the real failure.
                path: '/health/live'
                port: 8080
              }
              initialDelaySeconds: 15
              periodSeconds: 15
              failureThreshold: 5
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health/ready'
                port: 8080
              }
              initialDelaySeconds: 10
              periodSeconds: 10
              failureThreshold: 5
            }
          ]
        }
      ]
      scale: {
        // Fixed at one replica so CPU saturation stays observable instead of being
        // absorbed by autoscaling. Module 07 discusses what changes when it is not.
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
  dependsOn: [
    catalogApi
  ]
}

output ordersApiName string = ordersApi.name
output ordersApiFqdn string = ordersApi.properties.configuration.ingress.fqdn
output ordersApiResourceId string = ordersApi.id
output catalogApiName string = catalogApi.name
output catalogApiResourceId string = catalogApi.id
