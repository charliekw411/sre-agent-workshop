metadata description = 'Contoso Order Services container apps and the manual, identity-based database bootstrap job.'

targetScope = 'resourceGroup'

@description('Azure region for every resource. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Azure Developer CLI environment name used for resource discovery.')
param environmentName string = 'workshop'

@description('Short lowercase alphanumeric suffix used by the foundation deployment.')
@minLength(3)
@maxLength(12)
param suffix string

@description('Fully qualified image reference for orders-api.')
param ordersImage string

@description('Fully qualified image reference for catalog-api.')
param catalogImage string

@description('Tags applied to every resource.')
param tags object = {
  workload: 'sre-agent-workshop'
  environment: 'workshop'
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-${suffix}'
}

resource catalogIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-catalog-${suffix}'
}

resource bootstrapIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-bootstrap-${suffix}'
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: 'kv-${suffix}'
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: 'acr${suffix}'
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

var databaseName = 'sqldb-orders'
var sqlConnectionString = 'Server=tcp:${sqlServer.properties.fullyQualifiedDomainName},1433;Initial Catalog=${databaseName};Authentication=Active Directory Managed Identity;User Id=${identity.properties.clientId};Encrypt=True;TrustServerCertificate=False;Connection Timeout=30;'
var bootstrapConnectionString = 'Server=tcp:${sqlServer.properties.fullyQualifiedDomainName},1433;Initial Catalog=${databaseName};Authentication=Active Directory Managed Identity;User Id=${bootstrapIdentity.properties.clientId};Encrypt=True;TrustServerCertificate=False;Connection Timeout=30;'

resource catalogApi 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'catalog-api'
  location: location
  tags: union(tags, {
    service: 'catalog-api'
    'azd-env-name': environmentName
    'azd-service-name': 'catalog-api'
  })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${catalogIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        // The dependency must not be reachable outside this environment.
        external: false
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: catalogIdentity.id
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
          // Both the initial ASP.NET sample and the deployed API expose process health at /.
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/'
                port: 8080
              }
              initialDelaySeconds: 10
              periodSeconds: 15
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/'
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
  tags: union(tags, {
    service: 'orders-api'
    'azd-env-name': environmentName
    'azd-service-name': 'orders-api'
  })
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
          name: 'fault-token'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/fault-token'
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'orders-api'
          image: ordersImage
          resources: {
            // Keep CPU small so the saturation lab completes promptly.
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
              value: sqlConnectionString
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
              value: 'https://${catalogApi.properties.configuration.ingress.fqdn}'
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
                // Deliberately process liveness, not a dependency check.
                path: '/'
                port: 8080
              }
              initialDelaySeconds: 15
              periodSeconds: 15
              failureThreshold: 5
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/'
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
        // Fixed replicas keep CPU saturation observable instead of autoscaling it away.
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

resource bootstrapJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'orders-db-bootstrap'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${bootstrapIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerAppsEnvironment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: bootstrapIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'orders-db-bootstrap'
          image: ordersImage
          args: [
            '--bootstrap'
          ]
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'ConnectionStrings__OrdersDb'
              value: bootstrapConnectionString
            }
            {
              name: 'ORDERS_IDENTITY_PRINCIPAL_ID'
              value: identity.properties.principalId
            }
          ]
        }
      ]
    }
  }
}

output bootstrapJobName string = bootstrapJob.name
output ordersApiName string = ordersApi.name
output ordersApiFqdn string = ordersApi.properties.configuration.ingress.fqdn
output ordersApiResourceId string = ordersApi.id
output catalogApiName string = catalogApi.name
output catalogApiResourceId string = catalogApi.id
