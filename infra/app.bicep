param location string
param managedEnvironmentId string
@secure()
param publicBaseUrl string
param containerImage string
param entraTenantId string
param entraClientId string
param entraCertificateThumbprint string
@secure()
param entraCertificatePrivateKey string
@secure()
param databaseUrl string
@secure()
param sessionSecret string
param tags object

var token = uniqueString(subscription().id, resourceGroup().id)
var appName = 'ca-civicops-ml-prod'
var identityName = 'id-civicops-ml-prod'
var keyVaultName = 'kvcivicops${token}'
var workspaceName = 'log-civicops-ml-prod'
var applicationInsightsName = 'appi-civicops-ml-prod'
var keyVaultSecretsUserRole = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: identityName
  location: location
  tags: tags
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    workspaceCapping: {
      dailyQuotaGb: json('0.1')
    }
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: applicationInsightsName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    DisableIpMasking: false
    IngestionMode: 'LogAnalytics'
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
  }
}

resource databaseSecret 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: keyVault
  name: 'database-url'
  properties: {
    value: databaseUrl
  }
}

resource sessionSecretResource 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: keyVault
  name: 'session-secret'
  properties: {
    value: sessionSecret
  }
}

resource certificateSecret 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = {
  parent: keyVault
  name: 'entra-certificate-private-key'
  properties: {
    value: entraCertificatePrivateKey
  }
}

resource keyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, identity.id, 'civicops-secrets-user')
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRole
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource containerApp 'Microsoft.App/containerApps@2025-01-01' = {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: managedEnvironmentId
    configuration: {
      activeRevisionsMode: 'Multiple'
      ingress: {
        external: true
        allowInsecure: false
        targetPort: 8000
        transport: 'auto'
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: databaseSecret.properties.secretUriWithVersion
          identity: identity.id
        }
        {
          name: 'session-secret'
          keyVaultUrl: sessionSecretResource.properties.secretUriWithVersion
          identity: identity.id
        }
        {
          name: 'entra-private-key'
          keyVaultUrl: certificateSecret.properties.secretUriWithVersion
          identity: identity.id
        }
        {
          name: 'application-insights'
          value: applicationInsights.properties.ConnectionString
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'civicops-ml'
          image: containerImage
          env: [
            { name: 'CIVICOPS_ENVIRONMENT', value: 'production' }
            { name: 'CIVICOPS_PUBLIC_BASE_URL', value: publicBaseUrl }
            { name: 'CIVICOPS_AUTO_MIGRATE', value: 'false' }
            { name: 'CIVICOPS_ENTRA_TENANT_ID', value: entraTenantId }
            { name: 'CIVICOPS_ENTRA_CLIENT_ID', value: entraClientId }
            { name: 'CIVICOPS_ENTRA_CERTIFICATE_THUMBPRINT', value: entraCertificateThumbprint }
            { name: 'CIVICOPS_ENTRA_CERTIFICATE_PRIVATE_KEY', secretRef: 'entra-private-key' }
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'CIVICOPS_SESSION_SECRET', secretRef: 'session-secret' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'application-insights' }
            { name: 'OTEL_SERVICE_NAME', value: 'civicops-ml' }
            { name: 'OTEL_RESOURCE_ATTRIBUTES', value: 'deployment.environment=production,service.version=0.3.0' }
            { name: 'CIVICOPS_TRACES_PER_SECOND', value: '0.5' }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/livez', port: 8000 }
              periodSeconds: 5
              failureThreshold: 24
            }
            {
              type: 'Liveness'
              httpGet: { path: '/livez', port: 8000 }
              initialDelaySeconds: 10
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: { path: '/healthz', port: 8000 }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 6
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
  dependsOn: [
    keyVaultAccess
  ]
}

output containerAppName string = containerApp.name
output endpoint string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output keyVaultName string = keyVault.name
output applicationInsightsName string = applicationInsights.name
