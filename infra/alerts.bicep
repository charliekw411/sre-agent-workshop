metadata description = 'Azure Monitor detection layer for the Azure SRE Agent workshop: action group, metric alert rules, and log alert rules covering the three workshop incidents.'

targetScope = 'resourceGroup'

@description('Azure region for the log alert rules. Metric alerts and action groups are global.')
param location string = resourceGroup().location

@description('Short lowercase alphanumeric suffix used by the foundation deployment.')
@minLength(3)
@maxLength(12)
param suffix string

@description('Email address that receives alert notifications.')
param alertEmail string = ''

@description('CPU threshold in nanocores for orders-api. The container is allocated 0.5 vCPU, so 400000000 is 80 percent utilization.')
param cpuThresholdNanoCores int = 400000000

@description('Count of HTTP 5xx responses within the evaluation window that triggers the error alert.')
param httpErrorThreshold int = 10

@description('Azure SQL Database used storage percentage that triggers the storage alert.')
param storagePercentThreshold int = 85

@description('Tags applied to every resource.')
param tags object = {
  workload: 'sre-agent-workshop'
  environment: 'workshop'
}

var databaseName = 'sqldb-orders'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: 'law-${suffix}'
}

resource ordersApi 'Microsoft.App/containerApps@2024-03-01' existing = {
  name: 'orders-api'
}

resource catalogApi 'Microsoft.App/containerApps@2024-03-01' existing = {
  name: 'catalog-api'
}

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' existing = {
  name: 'sql-${suffix}'

  resource ordersDatabase 'databases@2023-08-01-preview' existing = {
    name: databaseName
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-sre-workshop'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'sreWorkshop'
    enabled: true
    emailReceivers: empty(alertEmail) ? [] : [
      {
        name: 'workshop-operator'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

// Module 06: saturation signal. Fires when orders-api burns most of its allocated vCPU.
resource cpuAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-orders-api-high-cpu'
  location: 'global'
  tags: tags
  properties: {
    description: 'orders-api CPU utilization is sustained above 80 percent of its allocation.'
    severity: 2
    enabled: true
    scopes: [
      ordersApi.id
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    targetResourceType: 'Microsoft.App/containerApps'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'HighCpu'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'UsageNanoCores'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: cpuThresholdNanoCores
          timeAggregation: 'Average'
        }
      ]
    }
    autoMitigate: true
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

// Module 08: error signal. Counts HTTP 5xx responses served by the public ingress.
resource httpErrorAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-orders-api-http-5xx'
  location: 'global'
  tags: tags
  properties: {
    description: 'orders-api is returning HTTP 5xx responses above the acceptable rate.'
    severity: 1
    enabled: true
    scopes: [
      ordersApi.id
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    targetResourceType: 'Microsoft.App/containerApps'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'ServerErrors'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'Requests'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: httpErrorThreshold
          timeAggregation: 'Total'
          dimensions: [
            {
              name: 'statusCodeCategory'
              operator: 'Include'
              values: [
                '5xx'
              ]
            }
          ]
        }
      ]
    }
    autoMitigate: true
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

// Supporting signal for Modules 06 and 08: replicas restarting indicate probe failures.
resource restartAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-orders-api-restarts'
  location: 'global'
  tags: tags
  properties: {
    description: 'orders-api replicas are restarting, which usually means probes are failing.'
    severity: 2
    enabled: true
    scopes: [
      ordersApi.id
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    targetResourceType: 'Microsoft.App/containerApps'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'Restarts'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'RestartCount'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 2
          timeAggregation: 'Total'
        }
      ]
    }
    autoMitigate: true
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

// Module 08: confirms whether the dependency itself is unhealthy or only its caller.
resource catalogCpuAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-catalog-api-high-cpu'
  location: 'global'
  tags: tags
  properties: {
    description: 'catalog-api CPU utilization is sustained above 80 percent of its allocation.'
    severity: 3
    enabled: true
    scopes: [
      catalogApi.id
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    targetResourceType: 'Microsoft.App/containerApps'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'HighCpu'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'UsageNanoCores'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 200000000
          timeAggregation: 'Average'
        }
      ]
    }
    autoMitigate: true
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

// Module 10: saturation signal on the data tier.
resource sqlStorageAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-sqldb-storage-critical'
  location: 'global'
  tags: tags
  properties: {
    description: 'The orders database is approaching its maximum size. Write operations fail once it is full.'
    severity: 1
    enabled: true
    scopes: [
      sqlServer::ordersDatabase.id
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    targetResourceType: 'Microsoft.Sql/servers/databases'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'StoragePercent'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'storage_percent'
          metricNamespace: 'Microsoft.Sql/servers/databases'
          operator: 'GreaterThan'
          threshold: storagePercentThreshold
          timeAggregation: 'Maximum'
        }
      ]
    }
    autoMitigate: true
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

// Log-based detection gives the agent an exception signature to correlate against metrics.
resource exceptionSpikeAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-application-exception-spike'
  location: location
  tags: tags
  kind: 'LogAlert'
  properties: {
    displayName: 'Application exception spike'
    description: 'Application Insights recorded an unusual number of exceptions across Contoso Order Services.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    scopes: [
      workspace.id
    ]
    criteria: {
      allOf: [
        {
          query: '''
AppExceptions
| where TimeGenerated > ago(15m)
| summarize ExceptionCount = count() by AppRoleName, ProblemId
| where ExceptionCount > 10
'''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
    autoMitigate: true
  }
}

// Distinguishes a dependency failure from a caller-side failure during Module 09.
resource dependencyFailureAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-dependency-failure-rate'
  location: location
  tags: tags
  kind: 'LogAlert'
  properties: {
    displayName: 'Dependency failure rate'
    description: 'More than 20 percent of outbound dependency calls from orders-api are failing.'
    severity: 1
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    scopes: [
      workspace.id
    ]
    criteria: {
      allOf: [
        {
          query: '''
AppDependencies
| where TimeGenerated > ago(15m)
| where AppRoleName == "orders-api"
| summarize Total = count(), Failed = countif(Success == false) by Target
| where Total > 20
| extend FailureRate = todouble(Failed) / todouble(Total)
| where FailureRate > 0.2
'''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
    autoMitigate: true
  }
}

output actionGroupId string = actionGroup.id
output actionGroupName string = actionGroup.name
output metricAlertNames array = [
  cpuAlert.name
  httpErrorAlert.name
  restartAlert.name
  catalogCpuAlert.name
  sqlStorageAlert.name
]
output logAlertNames array = [
  exceptionSpikeAlert.name
  dependencyFailureAlert.name
]
