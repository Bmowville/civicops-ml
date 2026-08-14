targetScope = 'subscription'

@description('Azure region for the CivicOps resources.')
param location string = 'eastus2'

@description('Resource group created for CivicOps.')
param resourceGroupName string = 'rg-civicops-ml-prod'

@description('Existing Container Apps managed environment resource ID.')
param managedEnvironmentId string

@description('Public HTTPS origin used for the Entra redirect URI.')
@secure()
param publicBaseUrl string

@description('Immutable public GHCR image reference pinned by digest.')
param containerImage string

@description('Microsoft Entra tenant ID for the existing CivicOps app.')
param entraTenantId string

@description('Microsoft Entra client ID for the existing CivicOps app.')
param entraClientId string

@description('SHA-1 thumbprint of the production Entra certificate.')
param entraCertificateThumbprint string

@description('PEM-encoded private key for the production Entra certificate.')
@secure()
param entraCertificatePrivateKey string

@description('Least-privilege pooled Neon PostgreSQL URL.')
@secure()
param databaseUrl string

@description('Random application session-signing secret of at least 32 characters.')
@secure()
param sessionSecret string

@description('Private email destination for production operational alerts.')
@secure()
param alertEmail string

param tags object = {
  application: 'civicops-ml'
  environment: 'production'
  managedBy: 'bicep'
}

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-11-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module application './app.bicep' = {
  name: 'civicops-ml-production'
  scope: resourceGroup
  params: {
    location: location
    managedEnvironmentId: managedEnvironmentId
    publicBaseUrl: publicBaseUrl
    containerImage: containerImage
    entraTenantId: entraTenantId
    entraClientId: entraClientId
    entraCertificateThumbprint: entraCertificateThumbprint
    entraCertificatePrivateKey: entraCertificatePrivateKey
    databaseUrl: databaseUrl
    sessionSecret: sessionSecret
    tags: tags
  }
}

module monitoring './monitoring.bicep' = {
  name: 'civicops-ml-monitoring'
  scope: resourceGroup
  params: {
    location: location
    containerAppName: application.outputs.containerAppName
    alertEmail: alertEmail
    tags: tags
  }
}

output resourceGroupName string = resourceGroup.name
output containerAppName string = application.outputs.containerAppName
output endpoint string = application.outputs.endpoint
output keyVaultName string = application.outputs.keyVaultName
output applicationInsightsName string = application.outputs.applicationInsightsName
output actionGroupName string = monitoring.outputs.actionGroupName
