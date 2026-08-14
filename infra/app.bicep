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

module identity 'br/public:avm/res/managed-identity/user-assigned-identity:0.6.0' = {
  name: 'identity'
  params: {
    name: identityName
    location: location
    tags: tags
    enableTelemetry: false
  }
}

module workspace 'br/public:avm/res/operational-insights/workspace:0.16.1' = {
  name: 'workspace'
  params: {
    name: workspaceName
    location: location
    skuName: 'PerGB2018'
    dataRetention: 30
    dailyQuotaGb: '0.1'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    tags: tags
    enableTelemetry: false
  }
}

module applicationInsights 'br/public:avm/res/insights/component:0.8.0' = {
  name: 'application-insights'
  params: {
    name: applicationInsightsName
    location: location
    applicationType: 'web'
    kind: 'web'
    workspaceResourceId: workspace.outputs.resourceId
    disableIpMasking: false
    disableLocalAuth: false
    ingestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
    retentionInDays: 30
    tags: tags
    enableTelemetry: false
  }
}

module keyVault 'br/public:avm/res/key-vault/vault:0.14.0' = {
  name: 'key-vault'
  params: {
    name: keyVaultName
    location: location
    sku: 'standard'
    enableVaultForDeployment: false
    enableVaultForTemplateDeployment: false
    enableVaultForDiskEncryption: false
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
    roleAssignments: [
      {
        name: guid(keyVaultName, identity.outputs.resourceId, 'civicops-secrets-user')
        principalId: identity.outputs.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: 'Key Vault Secrets User'
      }
    ]
    secrets: [
      {
        name: 'database-url'
        value: databaseUrl
      }
      {
        name: 'session-secret'
        value: sessionSecret
      }
      {
        name: 'entra-certificate-private-key'
        value: entraCertificatePrivateKey
      }
    ]
    tags: tags
    enableTelemetry: false
  }
}

module containerApp 'br/public:avm/res/app/container-app:0.23.0' = {
  name: 'container-app'
  params: {
    name: appName
    location: location
    environmentResourceId: managedEnvironmentId
    activeRevisionsMode: 'Multiple'
    maxInactiveRevisions: 3
    ingressExternal: true
    ingressAllowInsecure: false
    ingressTargetPort: 8000
    ingressTransport: 'auto'
    traffic: [
      {
        latestRevision: true
        weight: 100
      }
    ]
    managedIdentities: {
      userAssignedResourceIds: [
        identity.outputs.resourceId
      ]
    }
    secrets: [
      {
        name: 'database-url'
        keyVaultUrl: keyVault.outputs.secrets[0].uriWithVersion
        identity: identity.outputs.resourceId
      }
      {
        name: 'session-secret'
        keyVaultUrl: keyVault.outputs.secrets[1].uriWithVersion
        identity: identity.outputs.resourceId
      }
      {
        name: 'entra-private-key'
        keyVaultUrl: keyVault.outputs.secrets[2].uriWithVersion
        identity: identity.outputs.resourceId
      }
      {
        name: 'application-insights'
        value: applicationInsights.outputs.connectionString
      }
    ]
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
          { name: 'OTEL_RESOURCE_ATTRIBUTES', value: 'deployment.environment=production,service.version=0.3.3' }
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
    scaleSettings: {
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
    tags: tags
    enableTelemetry: false
  }
}

output containerAppName string = containerApp.outputs.name
output endpoint string = 'https://${containerApp.outputs.fqdn}'
output keyVaultName string = keyVault.outputs.name
output applicationInsightsName string = applicationInsights.outputs.name
