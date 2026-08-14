param location string = resourceGroup().location
param containerAppName string
@secure()
param alertEmail string
param tags object = {
  application: 'civicops-ml'
  environment: 'production'
  managedBy: 'bicep'
}

resource containerApp 'Microsoft.App/containerApps@2025-01-01' existing = {
  name: containerAppName
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-civicops-ml-prod'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'CivicOps'
    enabled: true
    emailReceivers: [
      {
        name: 'CivicOps owner'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

resource httpServerErrorAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'civicops-http-5xx'
  location: 'global'
  tags: tags
  properties: {
    description: 'CivicOps returned one or more HTTP 5xx responses within five minutes.'
    severity: 1
    enabled: true
    scopes: [
      containerApp.id
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    autoMitigate: true
    targetResourceType: 'Microsoft.App/containerApps'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'HttpServerErrors'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'Requests'
          metricNamespace: 'Microsoft.App/containerApps'
          dimensions: [
            {
              name: 'statusCodeCategory'
              operator: 'Include'
              values: [
                '5xx'
              ]
            }
          ]
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
          skipMetricValidation: false
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

resource containerRestartAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'civicops-container-restarts'
  location: 'global'
  tags: tags
  properties: {
    description: 'A running CivicOps replica restarted unexpectedly.'
    severity: 2
    enabled: true
    scopes: [
      containerApp.id
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    autoMitigate: true
    targetResourceType: 'Microsoft.App/containerApps'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'ContainerRestarts'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'RestartCount'
          metricNamespace: 'Microsoft.App/containerApps'
          dimensions: []
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Maximum'
          skipMetricValidation: false
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

resource failedOperationAlert 'Microsoft.Insights/activityLogAlerts@2020-10-01' = {
  name: 'civicops-failed-operations'
  location: 'global'
  tags: tags
  properties: {
    description: 'An Azure administrative operation failed in the CivicOps production resource group.'
    enabled: true
    scopes: [
      resourceGroup().id
    ]
    condition: {
      allOf: [
        {
          field: 'category'
          equals: 'Administrative'
        }
        {
          field: 'status'
          equals: 'Failed'
        }
      ]
    }
    actions: {
      actionGroups: [
        {
          actionGroupId: actionGroup.id
        }
      ]
    }
  }
}

output actionGroupName string = actionGroup.name
