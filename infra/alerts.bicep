metadata description = 'VM CPU, PostgreSQL dependency, and Orders API HTTP 5xx alerts.'

targetScope = 'resourceGroup'

param location string = resourceGroup().location

@minLength(3)
@maxLength(12)
param suffix string

param vmName string
param workspaceName string

@description('Optional alert recipient. Without an email, alerts still appear in Azure Monitor.')
param alertEmail string = ''

@description('Average VM CPU utilization threshold over five minutes.')
@minValue(1)
@maxValue(100)
param cpuPercentThreshold int = 80

@description('Number of HTTP 5xx responses in five minutes above which the alert fires.')
@minValue(0)
param httpErrorThreshold int = 10

param tags object = {}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: workspaceName
}

resource vm 'Microsoft.Compute/virtualMachines@2024-03-01' existing = {
  name: vmName
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = if (!empty(alertEmail)) {
  name: 'ag-orders-${suffix}'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'orders'
    enabled: true
    emailReceivers: [
      {
        name: 'workshop-operator'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

resource cpuAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-orders-high-cpu'
  location: 'global'
  tags: tags
  properties: {
    description: 'Orders API VM CPU utilization is sustained above the configured threshold.'
    severity: 2
    enabled: true
    scopes: [
      vm.id
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    targetResourceType: 'Microsoft.Compute/virtualMachines'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'HighCpu'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'Percentage CPU'
          metricNamespace: 'Microsoft.Compute/virtualMachines'
          operator: 'GreaterThan'
          threshold: cpuPercentThreshold
          timeAggregation: 'Average'
        }
      ]
    }
    autoMitigate: true
    actions: empty(alertEmail) ? [] : [
      {
        actionGroupId: actionGroup!.id
      }
    ]
  }
}

resource postgresqlDependencyAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'alert-orders-postgresql-connectivity'
  location: location
  tags: tags
  kind: 'LogAlert'
  properties: {
    displayName: 'Orders PostgreSQL dependency failures'
    description: 'Orders API cannot complete one or more PostgreSQL dependency operations.'
    severity: 1
    enabled: true
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    scopes: [
      workspace.id
    ]
    // The first deployment precedes AMA ingestion and creation of the Perf table.
    skipQueryValidation: true
    criteria: {
      allOf: [
        {
          query: '''
AppDependencies
| where TimeGenerated > ago(5m)
| where AppRoleName == "orders-api"
| where DependencyType == "PostgreSQL" and Success == false
| extend FailureCount = coalesce(ItemCount, 1)
| project TimeGenerated, FailureCount
'''
          metricMeasureColumn: 'FailureCount'
          timeAggregation: 'Total'
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
      actionGroups: empty(alertEmail) ? [] : [
        actionGroup!.id
      ]
    }
    autoMitigate: true
  }
}

resource httpErrorAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'alert-orders-http-5xx'
  location: location
  tags: tags
  kind: 'LogAlert'
  properties: {
    displayName: 'Orders API HTTP 5xx responses'
    description: 'Orders API returned more than the configured number of HTTP 5xx responses in five minutes.'
    severity: 1
    enabled: true
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    scopes: [
      workspace.id
    ]
    skipQueryValidation: true
    criteria: {
      allOf: [
        {
          query: '''
AppRequests
| where TimeGenerated > ago(5m)
| where AppRoleName == "orders-api"
| where toint(ResultCode) between (500 .. 599)
| extend ErrorCount = coalesce(ItemCount, 1)
| project TimeGenerated, ErrorCount
'''
          metricMeasureColumn: 'ErrorCount'
          timeAggregation: 'Total'
          operator: 'GreaterThan'
          threshold: httpErrorThreshold
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: empty(alertEmail) ? [] : [
        actionGroup!.id
      ]
    }
    autoMitigate: true
  }
}

output actionGroupId string = empty(alertEmail) ? '' : actionGroup!.id
output actionGroupName string = empty(alertEmail) ? '' : actionGroup!.name
