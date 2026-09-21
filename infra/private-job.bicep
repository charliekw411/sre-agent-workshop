metadata description = 'Storage-free managed-identity job for private Key Vault initialization or fault operations.'

targetScope = 'resourceGroup'

param location string = resourceGroup().location
param resourceName string
param environmentId string
param identityResourceId string
param identityClientId string
param keyVaultUri string
param ordersApiUrl string = ''
param tags object = {}

@allowed([
  'initialize'
  'fault'
])
param operation string

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: resourceName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityResourceId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: operation == 'initialize' ? 900 : 300
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
    }
    template: {
      containers: [
        {
          name: 'workshop-private-client'
          image: 'mcr.microsoft.com/azure-cli:2.64.0'
          command: [
            'python3'
          ]
          args: [
            '-c'
            loadTextContent('../scripts/private-job.py')
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'WORKSHOP_OPERATION'
              value: operation
            }
            {
              name: 'KEY_VAULT_URI'
              value: keyVaultUri
            }
            {
              name: 'IDENTITY_CLIENT_ID'
              value: identityClientId
            }
            {
              name: 'ORDERS_API_URL'
              value: ordersApiUrl
            }
            {
              name: 'WORKSHOP_REQUEST_ID'
              value: ''
            }
            {
              name: 'WORKSHOP_REQUEST'
              value: ''
            }
          ]
        }
      ]
    }
  }
}

output jobName string = job.name
